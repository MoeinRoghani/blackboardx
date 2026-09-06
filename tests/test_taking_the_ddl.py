"""The DDL is available to run yourself, and running it twice is safe.

A library that owns tables in someone else's database cannot assume it may
create them. An application whose schema belongs to a migration tool, or whose
role has no CREATE, takes the statements and runs them itself.
"""

from __future__ import annotations

import os
import threading

import pytest

DSN = os.environ.get("BLACKBOARD_TEST_POSTGRES_DSN")


class TestTakingTheStatements:
    def test_postgres_hands_over_the_statements_it_would_run(self) -> None:
        from blackboard import PostgresStore

        sql = PostgresStore.schema_sql()
        assert "CREATE TABLE IF NOT EXISTS blackboard_contributions" in sql
        assert "CREATE TABLE IF NOT EXISTS blackboard_outbox" in sql

    def test_sqlite_hands_over_its_own(self) -> None:
        from blackboard import SqliteStore

        sql = SqliteStore.schema_sql()
        assert "CREATE TABLE IF NOT EXISTS contributions" in sql
        assert "CREATE TABLE IF NOT EXISTS agent_progress" in sql

    def test_asking_for_the_statements_needs_no_connection(self) -> None:
        """So it can be piped into a migration tool from anywhere."""
        from blackboard import PostgresStore, SqliteStore

        assert PostgresStore.schema_sql()
        assert SqliteStore.schema_sql()

    def test_it_is_eight_tables_and_three_indexes(self) -> None:
        """`docs/concepts/storage.md` counts them, and a reader plans a
        migration from that count. A table added here updates that page.
        """
        from blackboard import PostgresStore

        sql = PostgresStore.schema_sql()
        assert sql.count("CREATE TABLE IF NOT EXISTS") == 8
        assert sql.count("INDEX IF NOT EXISTS") == 3

    def test_every_statement_is_conditional(self) -> None:
        """Running them against a database that has them changes nothing."""
        from blackboard import PostgresStore

        for statement in PostgresStore.schema_sql().split(";"):
            body = statement.strip()
            if not body or body.startswith("--"):
                continue
            assert "IF NOT EXISTS" in body, body[:60]


@pytest.mark.skipif(not DSN, reason="BLACKBOARD_TEST_POSTGRES_DSN names no server")
class TestReplicasStartingTogether:
    def test_eight_calls_at_once_all_succeed(self) -> None:
        """The property wanted: replicas starting together all get a schema.

        This does not reproduce the race it guards against. `CREATE TABLE IF
        NOT EXISTS` is not atomic in Postgres, so two sessions can both find a
        table missing and one then fail, but the window is small enough that
        eight threads against a local server do not reliably hit it. The lock
        rests on that being documented rather than on this failing without it.
        """
        from blackboard import PostgresStore

        assert DSN is not None
        with PostgresStore.from_dsn(DSN) as store:
            store.create_schema()
            with store._pool.connection() as connection:
                for table in ("blackboard_outbox", "blackboard_agent_progress"):
                    connection.execute(f"DROP TABLE IF EXISTS {table}")

            barrier = threading.Barrier(8)
            failures: list[BaseException] = []
            lock = threading.Lock()

            def create() -> None:
                barrier.wait()
                try:
                    store.create_schema()
                except BaseException as raised:
                    with lock:
                        failures.append(raised)

            threads = [threading.Thread(target=create) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            assert failures == [], f"{len(failures)} of 8 raised: {failures[:1]}"

    def test_the_statements_handed_over_are_the_ones_it_runs(self) -> None:
        """A user running them by hand gets the schema the library expects."""
        from blackboard import Level, PostgresStore

        assert DSN is not None
        with PostgresStore.from_dsn(DSN) as store:
            with store._pool.connection() as connection:
                connection.execute(PostgresStore.schema_sql())
            board = f"ddl-{os.getpid()}"
            store.delete(board)
            store.declare(board, Level("findings"))
            store.append(board, "findings", "a finding", notify=frozenset({"t"}))
            assert [c.content for c in store.read_level(board, "findings")] == [
                "a finding"
            ]
            assert store.unsent()
            store.delete(board)

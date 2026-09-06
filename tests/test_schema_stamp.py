"""A store records what wrote a record and refuses one it cannot read."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from blackboard import SCHEMA_VERSION, Level, SchemaVersionError, SqliteStore
from blackboard._schema import SCHEMA_COMPAT_VERSION, stamp_to_write


class TestTheDecision:
    def test_a_record_with_no_stamp_is_adopted(self) -> None:
        assert stamp_to_write(None, where="a file") == SCHEMA_VERSION

    def test_a_record_at_this_schema_is_left_alone(self) -> None:
        assert stamp_to_write(SCHEMA_VERSION, where="a file") is None

    def test_a_record_behind_this_schema_is_brought_forward(self) -> None:
        assert stamp_to_write(SCHEMA_VERSION - 1, where="a file") == SCHEMA_VERSION

    def test_a_record_ahead_of_this_schema_is_refused(self) -> None:
        with pytest.raises(SchemaVersionError) as refused:
            stamp_to_write(SCHEMA_VERSION + 1, where="incidents.sqlite3")
        said = str(refused.value)
        assert "incidents.sqlite3" in said
        assert str(SCHEMA_VERSION + 1) in said
        assert str(SCHEMA_VERSION) in said
        assert "Upgrade blackboardx" in said


class TestSqlite:
    def test_a_new_file_carries_the_stamp(self, tmp_path: Path) -> None:
        path = str(tmp_path / "new.sqlite3")
        SqliteStore(path).close()
        assert _sqlite_stamp(path) == SCHEMA_VERSION

    def test_a_file_written_ahead_is_refused_when_it_opens(
        self, tmp_path: Path
    ) -> None:
        path = str(tmp_path / "ahead.sqlite3")
        SqliteStore(path).close()
        _set_sqlite_stamp(path, SCHEMA_VERSION + 1)
        with pytest.raises(SchemaVersionError, match=str(SCHEMA_VERSION + 1)):
            SqliteStore(path)

    def test_a_file_written_before_stamps_existed_is_adopted(
        self, tmp_path: Path
    ) -> None:
        path = str(tmp_path / "unstamped.sqlite3")
        SqliteStore(path).close()
        connection = sqlite3.connect(path)
        connection.execute("DELETE FROM schema_stamp")
        connection.commit()
        connection.close()
        store = SqliteStore(path)
        try:
            assert _sqlite_stamp(path) == SCHEMA_VERSION
        finally:
            store.close()

    def test_a_refused_file_is_not_written_to(self, tmp_path: Path) -> None:
        path = str(tmp_path / "ahead.sqlite3")
        SqliteStore(path).close()
        _set_sqlite_stamp(path, SCHEMA_VERSION + 1)
        with pytest.raises(SchemaVersionError):
            SqliteStore(path)
        assert _sqlite_stamp(path) == SCHEMA_VERSION + 1


def _sqlite_stamp(path: str) -> int | None:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute("SELECT version FROM schema_stamp").fetchone()
    finally:
        connection.close()
    return None if row is None else int(row[0])


def _set_sqlite_stamp(path: str, version: int) -> None:
    """Writes a stamp that says a build at this version, at the oldest.

    The compatibility number is set alongside, because a database that is
    merely newer is readable and only one that says otherwise is not.
    """
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO schema_stamp (id, version, compat_version) VALUES (1, ?, ?) "
        "ON CONFLICT (id) DO UPDATE SET version = excluded.version, "
        "compat_version = excluded.compat_version",
        (version, version),
    )
    connection.commit()
    connection.close()


POSTGRES = os.environ.get("BLACKBOARD_TEST_POSTGRES_DSN")
MONGODB = os.environ.get("BLACKBOARD_TEST_MONGODB_URI")


@pytest.mark.skipif(not POSTGRES, reason="BLACKBOARD_TEST_POSTGRES_DSN is not set")
class TestPostgres:
    @pytest.fixture
    def store(self) -> Iterator[Any]:
        from blackboard import PostgresStore

        assert POSTGRES is not None
        with PostgresStore.from_dsn(POSTGRES) as store:
            store.create_schema()
            yield store

    def test_the_stamp_is_written_when_the_schema_is(self, store: Any) -> None:
        assert _postgres_stamp(store) == SCHEMA_VERSION

    def test_a_database_written_ahead_is_refused_before_it_is_touched(
        self, store: Any
    ) -> None:
        from blackboard import PostgresStore

        _set_postgres_stamp(store, SCHEMA_VERSION + 1)
        try:
            assert POSTGRES is not None
            # No create_schema. The check still runs, because an application
            # pointed at an existing database never calls it.
            with (
                PostgresStore.from_dsn(POSTGRES) as fresh,
                pytest.raises(SchemaVersionError, match=str(SCHEMA_VERSION + 1)),
            ):
                fresh.declare("board-x", Level("platform"))
        finally:
            _set_postgres_stamp(store, SCHEMA_VERSION)

    def test_the_check_runs_once_rather_than_on_every_call(self, store: Any) -> None:
        from blackboard import PostgresStore

        assert POSTGRES is not None
        with PostgresStore.from_dsn(POSTGRES) as fresh:
            fresh.declare("board-once", Level("platform"))
            _set_postgres_stamp(store, SCHEMA_VERSION + 1)
            try:
                # This store already checked, so it is not refused mid-run.
                fresh.append("board-once", "platform", {"n": 1})
            finally:
                _set_postgres_stamp(store, SCHEMA_VERSION)
            fresh.delete("board-once")


def _postgres_stamp(store: Any) -> int | None:
    with store._pool.connection() as connection:
        row = connection.execute(
            "SELECT version FROM blackboard_schema WHERE id = 1"
        ).fetchone()
    return None if row is None else int(row[0])


def _set_postgres_stamp(store: Any, version: int) -> None:
    """As `_set_sqlite_stamp`, and for the same reason."""
    with store._pool.connection() as connection:
        connection.execute(
            "INSERT INTO blackboard_schema (id, version, compat_version) "
            "VALUES (1, %s, %s) ON CONFLICT (id) DO UPDATE SET "
            "version = excluded.version, compat_version = excluded.compat_version",
            (version, version),
        )


@pytest.mark.skipif(not MONGODB, reason="BLACKBOARD_TEST_MONGODB_URI is not set")
class TestMongodb:
    @pytest.fixture
    def store(self) -> Iterator[Any]:
        from blackboard import MongoStore

        assert MONGODB is not None
        with MongoStore.from_uri(MONGODB, database="blackboard_stamp_test") as store:
            store.create_indexes()
            yield store
            store._database.client.drop_database("blackboard_stamp_test")

    def test_the_stamp_is_written_when_the_indexes_are(self, store: Any) -> None:
        found = store._database["blackboard_schema"].find_one({"_id": "schema"})
        assert found["version"] == SCHEMA_VERSION

    def test_a_database_written_ahead_is_refused_before_it_is_touched(
        self, store: Any
    ) -> None:
        from blackboard import MongoStore

        store._database["blackboard_schema"].update_one(
            {"_id": "schema"},
            {
                "$set": {
                    "version": SCHEMA_VERSION + 1,
                    "compat_version": SCHEMA_VERSION + 1,
                }
            },
            upsert=True,
        )
        assert MONGODB is not None
        with (
            MongoStore.from_uri(MONGODB, database="blackboard_stamp_test") as fresh,
            pytest.raises(SchemaVersionError, match=str(SCHEMA_VERSION + 1)),
        ):
            fresh.declare("board-x", Level("platform"))


class TestACompatibilityNumberBesideTheVersion:
    """One number says what a build writes; the other how far back reads it.

    A single monotonic integer cannot tell a database that is merely newer
    from one this build cannot use, so refusing on it turns every schema
    change into a full stop. The second number is the writer declaring which
    builds it left able to read what it wrote.
    """

    def test_a_newer_database_is_used_when_it_says_this_build_may(self) -> None:
        assert (
            stamp_to_write(
                SCHEMA_VERSION + 1, where="the test database", compat=SCHEMA_VERSION
            )
            is None
        )

    def test_a_newer_database_is_refused_when_it_says_this_build_may_not(
        self,
    ) -> None:
        with pytest.raises(SchemaVersionError) as raised:
            stamp_to_write(
                SCHEMA_VERSION + 1,
                where="the test database",
                compat=SCHEMA_VERSION + 1,
            )
        assert "or newer" in str(raised.value)

    def test_the_refusal_names_both_numbers(self) -> None:
        with pytest.raises(SchemaVersionError) as raised:
            stamp_to_write(9, where="the test database", compat=9)
        said = str(raised.value)
        assert "9" in said
        assert str(SCHEMA_VERSION) in said

    def test_a_record_with_no_compatibility_number_is_refused_when_newer(
        self,
    ) -> None:
        """Written before this library had one, so it makes no promise."""
        with pytest.raises(SchemaVersionError):
            stamp_to_write(SCHEMA_VERSION + 1, where="the test database")

    def test_an_older_database_is_stamped_forward_as_before(self) -> None:
        assert (
            stamp_to_write(SCHEMA_VERSION - 1, where="the test database", compat=1)
            == SCHEMA_VERSION
        )

    def test_the_compatibility_number_is_never_above_the_version(self) -> None:
        assert SCHEMA_COMPAT_VERSION <= SCHEMA_VERSION

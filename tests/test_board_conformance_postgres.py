"""The Postgres adapter is held to the same conformance suite, against a server.

The suite runs where ``BLACKBOARD_TEST_POSTGRES_DSN`` names one, and skips
where it does not, so a checkout with no server still runs green. CI sets it
against a service container, which is where the adapter is actually held to
this.
"""

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from conformance import BoardConformance, SharedStoreConformance

from blackboard import BoardStore, PostgresStore

DSN = os.environ.get("BLACKBOARD_TEST_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    DSN is None, reason="BLACKBOARD_TEST_POSTGRES_DSN names no server"
)


@pytest.fixture(scope="module")
def pool() -> Iterator[object]:
    from psycopg_pool import ConnectionPool

    assert DSN is not None
    with ConnectionPool(DSN, min_size=1, max_size=8) as opened:
        PostgresStore(opened).create_schema()
        yield opened


class TestPostgresStore(BoardConformance):
    @pytest.fixture
    def store(self, pool: object) -> BoardStore:
        return PostgresStore(pool)  # type: ignore[arg-type]


class TestPostgresHoldsManyBoards(SharedStoreConformance):
    @pytest.fixture
    def store(self, pool: object) -> BoardStore:
        return PostgresStore(pool)  # type: ignore[arg-type]


def test_create_schema_runs_against_a_database_that_already_has_it(
    pool: object,
) -> None:
    PostgresStore(pool).create_schema()  # type: ignore[arg-type]


def test_a_run_survives_the_process_that_made_it(pool: object) -> None:
    from blackboard import Level, Premise

    board = str(uuid4())
    first = PostgresStore(pool)  # type: ignore[arg-type]
    first.declare(board, Level("platform"))
    first.declare(board, Premise("window"))
    first.set(board, "window", ["t1", "t2"], expected_version=0)
    first.append(board, "platform", {"findings": ["oom"]})

    # A second store over the same database is what another pod holds.
    second = PostgresStore(pool)  # type: ignore[arg-type]
    assert second.read_premise(board, "window").value == ["t1", "t2"]
    assert [c.content for c in second.read_level(board, "platform")] == [
        {"findings": ["oom"]}
    ]
    assert second.append(board, "platform", "later").sequence == 3


def test_a_conflict_takes_no_sequence_number(pool: object) -> None:
    from blackboard import Conflict, Level, Premise

    board = str(uuid4())
    store = PostgresStore(pool)  # type: ignore[arg-type]
    store.declare(board, Level("platform"))
    store.declare(board, Premise("window"))
    store.set(board, "window", "w1", expected_version=0)
    assert store.set(board, "window", "w2", expected_version=0) == Conflict(
        current_version=1
    )
    # The number the conflicting write took went back, so the next write
    # takes 2 rather than 3 and the record has no hole in it.
    assert store.append(board, "platform", "next").sequence == 2

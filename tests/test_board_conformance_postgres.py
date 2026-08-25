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

from blackboard import BoardStore, PostgresBoard

DSN = os.environ.get("BLACKBOARD_TEST_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    DSN is None, reason="BLACKBOARD_TEST_POSTGRES_DSN names no server"
)


@pytest.fixture(scope="module")
def pool() -> Iterator[object]:
    from psycopg_pool import ConnectionPool

    assert DSN is not None
    with ConnectionPool(DSN, min_size=1, max_size=8) as opened:
        PostgresBoard(opened).create_schema()
        yield opened


class TestPostgresBoard(BoardConformance):
    @pytest.fixture
    def board(self, pool: object) -> BoardStore:
        return PostgresBoard(pool, board_id=str(uuid4()))  # type: ignore[arg-type]


class TestPostgresHoldsManyBoards(SharedStoreConformance):
    @pytest.fixture
    def two_boards(self, pool: object) -> tuple[BoardStore, BoardStore]:
        return (
            PostgresBoard(pool, board_id=str(uuid4())),  # type: ignore[arg-type]
            PostgresBoard(pool, board_id=str(uuid4())),  # type: ignore[arg-type]
        )

    @pytest.fixture
    def same_board_twice(self, pool: object) -> tuple[BoardStore, BoardStore]:
        board_id = str(uuid4())
        return (
            PostgresBoard(pool, board_id=board_id),  # type: ignore[arg-type]
            PostgresBoard(pool, board_id=board_id),  # type: ignore[arg-type]
        )


def test_create_schema_runs_against_a_database_that_already_has_it(
    pool: object,
) -> None:
    PostgresBoard(pool).create_schema()  # type: ignore[arg-type]


def test_a_run_survives_the_process_that_made_it(pool: object) -> None:
    board_id = str(uuid4())
    from blackboard import Level, Premise

    first = PostgresBoard(pool, board_id=board_id)  # type: ignore[arg-type]
    first.declare(Level("platform"))
    first.declare(Premise("window"))
    first.set("window", ["t1", "t2"], expected_version=0)
    first.append("platform", {"findings": ["oom"]})

    # A second adapter over the same database is what another pod holds.
    second = PostgresBoard(pool, board_id=board_id)  # type: ignore[arg-type]
    assert second.read_premise("window").value == ["t1", "t2"]
    assert [c.content for c in second.read_level("platform")] == [{"findings": ["oom"]}]
    assert second.append("platform", "later") == 3


def test_a_conflict_takes_no_sequence_number(pool: object) -> None:
    from blackboard import Conflict, Level, Premise

    board = PostgresBoard(pool, board_id=str(uuid4()))  # type: ignore[arg-type]
    board.declare(Level("platform"))
    board.declare(Premise("window"))
    board.set("window", "w1", expected_version=0)
    assert board.set("window", "w2", expected_version=0) == Conflict(current_version=1)
    # The number the conflicting write took went back, so the next write
    # takes 2 rather than 3 and the record has no hole in it.
    assert board.append("platform", "next") == 2

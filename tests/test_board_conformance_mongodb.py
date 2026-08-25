"""The MongoDB adapter is held to the same conformance suite, against a server.

The suite runs where ``BLACKBOARD_TEST_MONGODB_URI`` names a replica set,
and skips where it does not, so a checkout with none still runs green. CI
sets it against a single-node replica set, which is where the adapter is
actually held to this.
"""

import os
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from conformance import BoardConformance, SharedStoreConformance

from blackboard import BoardStore, Conflict, Level, MongoBoard, Premise

URI = os.environ.get("BLACKBOARD_TEST_MONGODB_URI")

pytestmark = pytest.mark.skipif(
    URI is None, reason="BLACKBOARD_TEST_MONGODB_URI names no replica set"
)


@pytest.fixture(scope="module")
def database() -> Iterator[Any]:
    from pymongo import MongoClient

    assert URI is not None
    client: MongoClient[Any] = MongoClient(URI)
    try:
        opened = client["blackboard_test"]
        MongoBoard(opened).create_indexes()
        yield opened
    finally:
        client.close()


class TestMongoBoard(BoardConformance):
    @pytest.fixture
    def board(self, database: Any) -> BoardStore:
        return MongoBoard(database, board_id=str(uuid4()))


class TestMongoHoldsManyBoards(SharedStoreConformance):
    @pytest.fixture
    def two_boards(self, database: Any) -> tuple[BoardStore, BoardStore]:
        return (
            MongoBoard(database, board_id=str(uuid4())),
            MongoBoard(database, board_id=str(uuid4())),
        )

    @pytest.fixture
    def same_board_twice(self, database: Any) -> tuple[BoardStore, BoardStore]:
        board_id = str(uuid4())
        return (
            MongoBoard(database, board_id=board_id),
            MongoBoard(database, board_id=board_id),
        )


def test_create_indexes_runs_against_a_database_that_already_has_them(
    database: Any,
) -> None:
    MongoBoard(database).create_indexes()


def test_a_run_survives_the_process_that_made_it(database: Any) -> None:
    board_id = str(uuid4())
    first = MongoBoard(database, board_id=board_id)
    first.declare(Level("platform"))
    first.declare(Premise("window"))
    first.set("window", ["t1", "t2"], expected_version=0)
    first.append("platform", {"findings": ["oom"]})

    # A second adapter over the same database is what another pod holds.
    second = MongoBoard(database, board_id=board_id)
    assert second.read_premise("window").value == ["t1", "t2"]
    assert [c.content for c in second.read_level("platform")] == [{"findings": ["oom"]}]
    assert second.append("platform", "later") == 3


def test_a_conflict_takes_no_sequence_number(database: Any) -> None:
    board = MongoBoard(database, board_id=str(uuid4()))
    board.declare(Level("platform"))
    board.declare(Premise("window"))
    board.set("window", "w1", expected_version=0)
    assert board.set("window", "w2", expected_version=0) == Conflict(current_version=1)
    # The number the conflicting write took went back, so the next write
    # takes 2 rather than 3 and the record has no hole in it.
    assert board.append("platform", "next") == 2


def test_the_record_is_stored_as_a_document_the_database_can_query(
    database: Any,
) -> None:
    board_id = str(uuid4())
    board = MongoBoard(database, board_id=board_id)
    board.declare(Level("platform"))
    board.append("platform", {"finding": "oom", "host": "node-7"})
    found = database["blackboard_contributions"].find_one(
        {"board_id": board_id, "content.finding": "oom"}
    )
    assert found is not None
    assert found["content"]["host"] == "node-7"

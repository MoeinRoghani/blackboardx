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

from blackboard import BoardStore, Conflict, Level, MongoStore, Premise
from blackboard.conformance import BoardConformance, SharedStoreConformance

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
        MongoStore(opened).create_indexes()
        yield opened
    finally:
        client.close()


class TestMongoStore(BoardConformance):
    @pytest.fixture
    def store(self, database: Any) -> BoardStore:
        return MongoStore(database)


class TestMongoHoldsManyBoards(SharedStoreConformance):
    @pytest.fixture
    def store(self, database: Any) -> BoardStore:
        return MongoStore(database)


def test_create_indexes_runs_against_a_database_that_already_has_them(
    database: Any,
) -> None:
    MongoStore(database).create_indexes()


def test_a_run_survives_the_process_that_made_it(database: Any) -> None:
    board = str(uuid4())
    first = MongoStore(database)
    first.declare(board, Level("platform"))
    first.declare(board, Premise("window"))
    first.set(board, "window", ["t1", "t2"], expected_version=0)
    first.append(board, "platform", {"findings": ["oom"]})

    # A second store over the same database is what another pod holds.
    second = MongoStore(database)
    assert second.read_premise(board, "window").value == ["t1", "t2"]
    assert [c.content for c in second.read_level(board, "platform")] == [
        {"findings": ["oom"]}
    ]
    assert second.append(board, "platform", "later").sequence == 3


def test_a_conflict_takes_no_sequence_number(database: Any) -> None:
    board = str(uuid4())
    store = MongoStore(database)
    store.declare(board, Level("platform"))
    store.declare(board, Premise("window"))
    store.set(board, "window", "w1", expected_version=0)
    assert store.set(board, "window", "w2", expected_version=0) == Conflict(
        current_version=1
    )
    # The number the conflicting write took went back, so the next write
    # takes 2 rather than 3 and the record has no hole in it.
    assert store.append(board, "platform", "next").sequence == 2


def test_the_record_is_stored_as_a_document_the_database_can_query(
    database: Any,
) -> None:
    board_id = str(uuid4())
    store = MongoStore(database)
    store.declare(board_id, Level("platform"))
    store.append(board_id, "platform", {"finding": "oom", "host": "node-7"})
    found = database["blackboard_contributions"].find_one(
        {"board_id": board_id, "content.finding": "oom"}
    )
    assert found is not None
    assert found["content"]["host"] == "node-7"

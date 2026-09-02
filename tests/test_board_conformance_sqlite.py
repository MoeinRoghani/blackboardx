"""The SQLite store is held to the same conformance suite, in memory and on disk."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from conformance import BoardConformance, SharedStoreConformance

from blackboard import BoardStore, Level, Premise, SqliteStore


class TestSqliteInProcess(BoardConformance):
    @pytest.fixture
    def store(self) -> Iterator[BoardStore]:
        opened = SqliteStore()
        yield opened
        opened.close()


class TestSqliteOnDisk(BoardConformance):
    @pytest.fixture
    def store(self, tmp_path: Path) -> Iterator[BoardStore]:
        opened = SqliteStore(str(tmp_path / "board.sqlite3"))
        yield opened
        opened.close()


class TestSqliteHoldsManyBoards(SharedStoreConformance):
    @pytest.fixture
    def store(self, tmp_path: Path) -> Iterator[BoardStore]:
        opened = SqliteStore(str(tmp_path / "board.sqlite3"))
        yield opened
        opened.close()


def test_the_record_survives_the_process_that_made_it(tmp_path: Path) -> None:
    path = str(tmp_path / "board.sqlite3")
    board = "incident-1"

    first = SqliteStore(path)
    first.declare(board, Level("platform"))
    first.declare(board, Premise("window"))
    first.set(board, "window", ["t1", "t2"], expected_version=0)
    first.append(board, "platform", {"findings": ["oom"]})
    first.close()

    reopened = SqliteStore(path)
    assert reopened.read_premise(board, "window").value == ["t1", "t2"]
    assert [c.content for c in reopened.read_level(board, "platform")] == [
        {"findings": ["oom"]}
    ]
    # The counter continues rather than restarting, so a later write cannot
    # take a sequence number an earlier one already holds.
    assert reopened.append(board, "platform", "later") == 3
    reopened.close()

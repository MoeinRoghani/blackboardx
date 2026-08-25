"""The SQLite board is held to the same conformance suite, in memory and on disk."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from conformance import BoardConformance

from blackboard import BoardStore, Level, Register, SqliteBoard


class TestSqliteInProcess(BoardConformance):
    @pytest.fixture
    def board(self) -> Iterator[BoardStore]:
        store = SqliteBoard()
        yield store
        store.close()


class TestSqliteOnDisk(BoardConformance):
    @pytest.fixture
    def board(self, tmp_path: Path) -> Iterator[BoardStore]:
        store = SqliteBoard(str(tmp_path / "board.sqlite3"))
        yield store
        store.close()


def test_the_record_survives_the_process_that_made_it(tmp_path: Path) -> None:
    path = str(tmp_path / "board.sqlite3")

    first = SqliteBoard(path)
    first.declare(Level("platform"))
    first.declare(Register("window"))
    first.set("window", ["t1", "t2"], expected_version=0)
    first.append("platform", {"findings": ["oom"]})
    first.close()

    reopened = SqliteBoard(path)
    assert reopened.read_register("window").value == ["t1", "t2"]
    assert [c.content for c in reopened.read_level("platform")] == [
        {"findings": ["oom"]}
    ]
    # The counter continues rather than restarting, so a later write cannot
    # take a sequence number an earlier one already holds.
    assert reopened.append("platform", "later") == 3
    reopened.close()

"""The SQLite store is held to the same conformance suite, in memory and on disk."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from blackboard import BoardStore, Level, Premise, SqliteStore, Written
from blackboard.conformance import BoardConformance, SharedStoreConformance


class TestSqliteInProcess(BoardConformance):
    @pytest.fixture
    def store(self) -> Iterator[BoardStore]:
        opened = SqliteStore(":memory:")
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
    assert reopened.append(board, "platform", "later").sequence == 3
    reopened.close()


_BEFORE_KEYS = """
CREATE TABLE regions (
    board_id TEXT NOT NULL, name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('level', 'premise')),
    PRIMARY KEY (board_id, name));
CREATE TABLE contributions (
    board_id TEXT NOT NULL, sequence INTEGER NOT NULL,
    region TEXT NOT NULL, content TEXT NOT NULL,
    PRIMARY KEY (board_id, sequence));
CREATE TABLE premises (
    board_id TEXT NOT NULL, name TEXT NOT NULL, value TEXT,
    version INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (board_id, name));
INSERT INTO regions VALUES ('b1', 'platform', 'level');
INSERT INTO contributions VALUES ('b1', 1, 'platform', '{"old": true}');
"""


def test_a_file_written_before_keys_existed_is_opened_and_written(
    tmp_path: Path,
) -> None:
    """The columns are added where they are absent rather than assumed."""
    path = str(tmp_path / "before.sqlite3")
    older = sqlite3.connect(path)
    older.executescript(_BEFORE_KEYS)
    older.commit()
    older.close()

    store = SqliteStore(path)
    try:
        assert [c.content for c in store.read_level("b1", "platform")] == [
            {"old": True}
        ]
        first = store.append("b1", "platform", {"new": True}, "k1")
        assert first == Written(sequence=2)
        assert store.append("b1", "platform", {"new": True}, "k1") == Written(
            sequence=2, repeated=True
        )
        assert len(store.read_level("b1", "platform")) == 2
    finally:
        store.close()

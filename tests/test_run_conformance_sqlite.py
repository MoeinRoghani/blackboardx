"""SQLite, held to the run conformance suite."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from blackboard import Level, SqliteStore
from blackboard.conformance import Bound, RunConformance


class TestSqliteRun(RunConformance):
    @pytest.fixture
    def ready(self) -> Iterator[Bound]:
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteStore(str(Path(directory) / "board.sqlite3"))
            bound = Bound(store, "test-board")
            bound.declare(Level("application"))
            try:
                yield bound
            finally:
                store.close()

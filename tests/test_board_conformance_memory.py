"""The in-memory store is held to the conformance suite like any other."""

import pytest

from blackboard import BoardStore, InMemoryStore
from blackboard.conformance import BoardConformance, SharedStoreConformance


class TestInMemoryStore(BoardConformance):
    @pytest.fixture
    def store(self) -> BoardStore:
        return InMemoryStore()


class TestInMemoryStoreHoldsManyBoards(SharedStoreConformance):
    @pytest.fixture
    def store(self) -> BoardStore:
        return InMemoryStore()

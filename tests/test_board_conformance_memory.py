"""The in-memory store is held to the conformance suite like any other."""

import pytest
from conformance import BoardConformance, SharedStoreConformance

from blackboard import BoardStore, InMemoryStore


class TestInMemoryStore(BoardConformance):
    @pytest.fixture
    def store(self) -> BoardStore:
        return InMemoryStore()


class TestInMemoryStoreHoldsManyBoards(SharedStoreConformance):
    @pytest.fixture
    def store(self) -> BoardStore:
        return InMemoryStore()

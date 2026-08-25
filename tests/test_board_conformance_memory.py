"""The in-memory board is held to the conformance suite like any other."""

import pytest
from conformance import BoardConformance

from blackboard import BoardStore, InMemoryBoard


class TestInMemoryBoard(BoardConformance):
    @pytest.fixture
    def board(self) -> BoardStore:
        return InMemoryBoard()

"""The in-memory store, held to the agent conformance suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from blackboard import InMemoryStore, Level
from blackboard.conformance import AgentConformance, Bound


class TestInMemoryAgents(AgentConformance):
    @pytest.fixture
    def ready(self) -> Iterator[Bound]:
        bound = Bound(InMemoryStore(), "test-board")
        bound.declare(Level("application"))
        yield bound

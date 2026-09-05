"""The in-memory store, held to the run conformance suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from blackboard import InMemoryStore, Level
from blackboard.conformance import Bound, RunConformance


class TestInMemoryRun(RunConformance):
    @pytest.fixture
    def ready(self) -> Iterator[Bound]:
        bound = Bound(InMemoryStore(), "test-board")
        bound.declare(Level("application"))
        yield bound

"""`InMemoryRunStore` against the suite every run store is held to."""

from __future__ import annotations

import pytest

from blackboard import InMemoryRunStore
from blackboard.conformance import RunConformance, SharedRunStoreConformance


class TestInMemoryRunStore(RunConformance):
    @pytest.fixture
    def run_store(self) -> InMemoryRunStore:
        return InMemoryRunStore()


class TestInMemoryRunStoreHoldsManyRuns(SharedRunStoreConformance):
    @pytest.fixture
    def run_store(self) -> InMemoryRunStore:
        return InMemoryRunStore()

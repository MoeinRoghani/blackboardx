"""PostgreSQL, held to the run conformance suite."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from blackboard import Level
from blackboard.conformance import Bound, RunConformance

DSN = os.environ.get("BLACKBOARD_TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(
    not DSN, reason="BLACKBOARD_TEST_POSTGRES_DSN names no server"
)


class TestPostgresRun(RunConformance):
    @pytest.fixture
    def ready(self) -> Iterator[Bound]:
        from blackboard import PostgresStore

        assert DSN is not None
        with PostgresStore.from_dsn(DSN) as store:
            store.create_schema()
            board_id = f"run-conformance-{os.getpid()}-{id(self)}"
            store.delete(board_id)
            bound = Bound(store, board_id)
            bound.declare(Level("application"))
            try:
                yield bound
            finally:
                store.delete(board_id)

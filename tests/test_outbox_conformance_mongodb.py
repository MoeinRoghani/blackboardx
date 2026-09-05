"""MongoDB, held to the outbox conformance suite."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from blackboard import Level
from blackboard.conformance import Bound, OutboxConformance

URI = os.environ.get("BLACKBOARD_TEST_MONGODB_URI")
pytestmark = pytest.mark.skipif(
    not URI, reason="BLACKBOARD_TEST_MONGODB_URI names no server"
)


class TestMongoOutbox(OutboxConformance):
    @pytest.fixture
    def ready(self) -> Iterator[Bound]:
        from blackboard import MongoStore

        assert URI is not None
        with MongoStore.from_uri(URI, "blackboard_run_conformance") as store:
            board_id = f"outbox-conformance-{os.getpid()}-{id(self)}"
            store.delete(board_id)
            bound = Bound(store, board_id)
            bound.declare(Level("application"))
            try:
                yield bound
            finally:
                store.delete(board_id)

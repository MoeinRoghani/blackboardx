"""`PostgresRunStore` against the suite every run store is held to.

Needs a Postgres this test may create tables in, named by
`BLACKBOARD_TEST_POSTGRES_DSN`. Without one the module says so and skips.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from blackboard.conformance import RunConformance, SharedRunStoreConformance

DSN = os.environ.get("BLACKBOARD_TEST_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    DSN is None, reason="BLACKBOARD_TEST_POSTGRES_DSN names no server"
)


@pytest.fixture
def pool() -> Iterator[object]:
    from psycopg_pool import ConnectionPool

    assert DSN is not None
    opened = ConnectionPool(DSN, min_size=1, max_size=4, open=False)
    opened.open(wait=True, timeout=15.0)
    try:
        yield opened
    finally:
        opened.close()


def _store(pool: object) -> object:
    from blackboard import PostgresRunStore

    store = PostgresRunStore(pool)  # type: ignore[arg-type]
    store.create_schema()
    return store


class TestPostgresRunStore(RunConformance):
    # Declared on the class rather than the module, because a fixture on the
    # class the suite defines is what a module-level one would sit behind.
    @pytest.fixture
    def run_store(self, pool: object) -> object:
        return _store(pool)


class TestPostgresRunStoreHoldsManyRuns(SharedRunStoreConformance):
    @pytest.fixture
    def run_store(self, pool: object) -> object:
        return _store(pool)

"""A collection read takes a maximum count, and a store can name its regions."""

import pytest
from conformance import Bound

from blackboard import InMemoryStore, Level, Premise, SqliteStore


@pytest.fixture(params=["memory", "sqlite"])
def board(request: pytest.FixtureRequest) -> Bound:
    store = InMemoryStore() if request.param == "memory" else SqliteStore()
    board = Bound(store, "test-board")
    board.declare(Level("platform"))
    board.declare(Premise("window"))
    return board


class TestALimitOnEveryCollectionRead:
    def test_a_level_read_returns_at_most_the_limit(self, board: Bound) -> None:
        for i in range(10):
            board.append("platform", i)
        assert len(board.read_level("platform", limit=3)) == 3

    def test_no_limit_returns_everything(self, board: Bound) -> None:
        for i in range(10):
            board.append("platform", i)
        assert len(board.read_level("platform")) == 10

    def test_the_sequence_number_is_the_continuation(self, board: Bound) -> None:
        for i in range(10):
            board.append("platform", i)
        seen: list[object] = []
        cursor = 0
        while True:
            page = board.read_level("platform", from_sequence=cursor, limit=4)
            if not page:
                break
            seen.extend(c.content for c in page)
            cursor = page[-1].sequence + 1
        assert seen == list(range(10))

    def test_a_whole_board_read_takes_a_limit_too(self, board: Bound) -> None:
        for i in range(6):
            board.append("platform", i)
        assert len(board.read_board(limit=2)) == 2

    def test_a_limit_of_zero_returns_nothing(self, board: Bound) -> None:
        board.append("platform", "one")
        assert board.read_level("platform", limit=0) == []


class TestNamingTheRegions:
    def test_a_store_names_the_regions_of_one_board(self, board: Bound) -> None:
        assert sorted(r.name for r in board.read_regions()) == ["platform", "window"]

    def test_each_region_says_which_kind_it_is(self, board: Bound) -> None:
        by_name = {r.name: r for r in board.read_regions()}
        assert isinstance(by_name["platform"], Level)
        assert isinstance(by_name["window"], Premise)

    def test_a_board_with_no_regions_names_none(self, board: Bound) -> None:
        assert board.store.read_regions("a-board-nobody-declared") == []

    def test_a_store_records_a_name_and_a_kind_and_nothing_else(self) -> None:
        # The batch window tells the control component when to notify. It is
        # no part of the record, so no store returns it.
        from datetime import timedelta

        store = InMemoryStore()
        store.declare("b", Premise("window", batch_window=timedelta(seconds=5)))
        (region,) = store.read_regions("b")
        assert region == Premise("window")

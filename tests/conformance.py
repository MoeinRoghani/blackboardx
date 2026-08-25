"""The behaviour every board implementation owes, whatever it stores in.

An adapter is correct when it passes this. Import ``BoardConformance`` in a
test module, give it a ``board`` fixture, and the whole suite runs against
that implementation.
"""

import threading

import pytest

from blackboard import (
    BoardChange,
    BoardStore,
    Conflict,
    Contribution,
    DuplicateRegionError,
    Level,
    RegionKindError,
    Register,
    RegisterState,
    UndeclaredRegionError,
    UnsetRegisterError,
    Written,
)


class BoardConformance:
    """Subclass this and supply a ``board`` fixture returning a fresh board.

    The board arrives with no regions declared. Each test declares what it
    needs, so an implementation is never asked to guess a starting shape.
    """

    @pytest.fixture
    def board(self) -> BoardStore:
        raise NotImplementedError("supply a board fixture")

    @pytest.fixture
    def ready(self, board: BoardStore) -> BoardStore:
        board.declare(Level("application"))
        board.declare(Level("platform"))
        board.declare(Register("window"))
        return board

    # Declaring

    def test_a_region_is_declared_before_it_is_used(self, board: BoardStore) -> None:
        with pytest.raises(UndeclaredRegionError):
            board.append("application", "a")

    def test_a_name_declared_twice_is_refused(self, ready: BoardStore) -> None:
        with pytest.raises(DuplicateRegionError):
            ready.declare(Level("application"))
        with pytest.raises(DuplicateRegionError):
            ready.declare(Register("application"))

    def test_a_refused_declaration_leaves_the_region_intact(
        self, ready: BoardStore
    ) -> None:
        ready.append("application", "a")
        with pytest.raises(DuplicateRegionError):
            ready.declare(Level("application"))
        assert ready.read_level("application") == [
            Contribution(sequence=1, content="a")
        ]

    def test_a_region_declared_later_starts_empty(self, ready: BoardStore) -> None:
        ready.append("application", "a")
        ready.declare(Level("change"))
        assert ready.read_level("change") == []

    def test_a_register_declared_later_holds_no_value(self, ready: BoardStore) -> None:
        ready.declare(Register("trigger"))
        with pytest.raises(UnsetRegisterError):
            ready.read_register("trigger")

    # The total order

    def test_one_counter_orders_every_region(self, ready: BoardStore) -> None:
        first = ready.append("application", "a")
        second = ready.append("platform", "b")
        third = ready.set("window", "w", expected_version=0)
        assert isinstance(third, Written)
        assert (first, second, third.sequence) == (1, 2, 3)

    def test_a_region_declared_later_continues_the_count(
        self, ready: BoardStore
    ) -> None:
        ready.append("application", "a")
        ready.declare(Level("change"))
        assert ready.append("change", "c") == 2

    # Levels

    def test_a_level_keeps_arrival_order(self, ready: BoardStore) -> None:
        ready.append("application", "a")
        ready.append("platform", "p")
        ready.append("application", "b")
        assert ready.read_level("application") == [
            Contribution(sequence=1, content="a"),
            Contribution(sequence=3, content="b"),
        ]

    def test_a_level_read_from_a_bound_is_inclusive(self, ready: BoardStore) -> None:
        ready.append("application", "a")
        ready.append("application", "b")
        assert ready.read_level("application", from_sequence=2) == [
            Contribution(sequence=2, content="b")
        ]

    def test_appending_to_a_register_is_refused(self, ready: BoardStore) -> None:
        with pytest.raises(RegionKindError):
            ready.append("window", "a")

    # Registers

    def test_the_first_write_expects_version_zero(self, ready: BoardStore) -> None:
        result = ready.set("window", "w", expected_version=0)
        assert result == Written(sequence=1, version=1)

    def test_a_current_version_replaces_the_value(self, ready: BoardStore) -> None:
        ready.set("window", "w1", expected_version=0)
        assert ready.set("window", "w2", expected_version=1) == Written(
            sequence=2, version=2
        )
        assert ready.read_register("window") == RegisterState(value="w2", version=2)

    def test_a_stale_version_returns_the_current_one(self, ready: BoardStore) -> None:
        ready.set("window", "w1", expected_version=0)
        ready.set("window", "w2", expected_version=1)
        assert ready.set("window", "late", expected_version=1) == Conflict(
            current_version=2
        )

    def test_a_conflict_changes_nothing(self, ready: BoardStore) -> None:
        ready.set("window", "w1", expected_version=0)
        ready.set("window", "late", expected_version=0)
        assert ready.read_register("window") == RegisterState(value="w1", version=1)

    def test_a_conflict_takes_no_sequence_number(self, ready: BoardStore) -> None:
        ready.set("window", "w1", expected_version=0)
        ready.set("window", "late", expected_version=0)
        assert ready.append("application", "a") == 2

    def test_a_conflict_is_absent_from_the_record(self, ready: BoardStore) -> None:
        ready.set("window", "w1", expected_version=0)
        ready.set("window", "late", expected_version=0)
        assert ready.read_board() == [
            BoardChange(sequence=1, region="window", content="w1")
        ]

    def test_setting_a_level_is_refused(self, ready: BoardStore) -> None:
        with pytest.raises(RegionKindError):
            ready.set("application", "a", expected_version=0)

    def test_a_register_may_hold_none(self, ready: BoardStore) -> None:
        ready.set("window", None, expected_version=0)
        assert ready.read_register("window") == RegisterState(value=None, version=1)

    # Reading the whole board

    def test_the_record_is_every_write_in_order(self, ready: BoardStore) -> None:
        ready.append("application", "a")
        ready.set("window", "w", expected_version=0)
        ready.append("platform", "p")
        assert ready.read_board() == [
            BoardChange(sequence=1, region="application", content="a"),
            BoardChange(sequence=2, region="window", content="w"),
            BoardChange(sequence=3, region="platform", content="p"),
        ]

    def test_the_record_read_from_a_bound_is_inclusive(self, ready: BoardStore) -> None:
        ready.append("application", "a")
        ready.append("platform", "p")
        assert ready.read_board(from_sequence=2) == [
            BoardChange(sequence=2, region="platform", content="p")
        ]

    def test_reads_are_snapshots(self, ready: BoardStore) -> None:
        ready.append("application", "a")
        ready.read_level("application").clear()
        ready.read_board().clear()
        assert len(ready.read_level("application")) == 1
        assert len(ready.read_board()) == 1

    def test_reading_an_undeclared_region_is_refused(self, ready: BoardStore) -> None:
        with pytest.raises(UndeclaredRegionError):
            ready.read_level("missing")
        with pytest.raises(UndeclaredRegionError):
            ready.read_register("missing")

    # Content

    def test_content_survives_a_round_trip(self, ready: BoardStore) -> None:
        content = {"findings": ["oom"], "counts": [1, 2, 3], "nested": {"a": None}}
        ready.append("application", content)
        (contribution,) = ready.read_level("application")
        assert contribution.content == content

    # Concurrency

    def test_concurrent_appends_take_distinct_sequences(
        self, ready: BoardStore
    ) -> None:
        results: list[int] = []
        guard = threading.Lock()
        barrier = threading.Barrier(4)

        def run() -> None:
            barrier.wait()
            local = [ready.append("application", "x") for _ in range(25)]
            with guard:
                results.extend(local)

        threads = [threading.Thread(target=run) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(results) == list(range(1, 101))

    def test_concurrent_register_writers_lose_no_update(
        self, ready: BoardStore
    ) -> None:
        ready.set("window", [], expected_version=0)
        barrier = threading.Barrier(4)

        def add(item: int) -> None:
            barrier.wait()
            for _ in range(500):
                state = ready.read_register("window")
                assert isinstance(state.value, list)
                result = ready.set(
                    "window", [*state.value, item], expected_version=state.version
                )
                if isinstance(result, Written):
                    return
            raise AssertionError("the writer never won a version")

        threads = [threading.Thread(target=add, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        state = ready.read_register("window")
        assert isinstance(state.value, list)
        assert sorted(state.value) == list(range(4))
        assert state.version == 5

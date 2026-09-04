"""The suite every store implementation is held to.

`BoardStore` is a protocol, so an application may keep its record in a database that
this library ships no adapter for. What that store has to guarantee
is not obvious from the signatures: one counter across every region rather
than one per region, a conflicting premise write taking no sequence number so
a conflict leaves no gap, a key writing once, a bounded read continuing from
a sequence rather than an offset. Each of those has a case here because an
implementation gets each of them wrong.

`RunStore` is a protocol for the same reason, and what it has to guarantee is
less obvious still: two numbers rather than one when two processes issue a
notification at the same moment, an acknowledgment closing every range it
covers rather than the one it names, and one winner when two callers close a
run. `RunConformance` has a case for each.

Subclass `BoardConformance` and `SharedStoreConformance`, supply a `store`
fixture returning a fresh store, and run pytest:

    from blackboard.conformance import BoardConformance, SharedStoreConformance
    from myapp.storage import CassandraStore


    class TestCassandraStore(BoardConformance):
        @pytest.fixture
        def store(self):
            return CassandraStore(session)


    class TestCassandraHoldsManyBoards(SharedStoreConformance):
        @pytest.fixture
        def store(self):
            return CassandraStore(session)

The four stores the library ships are held to this module rather than to a copy of it.

Running it needs pytest, which the library itself does not need:
``pip install blackboardx[conformance]``.
"""

from __future__ import annotations

try:
    import pytest
except ModuleNotFoundError as absent:  # pragma: no cover
    raise ModuleNotFoundError(
        "the conformance suite needs pytest: pip install blackboardx[conformance]"
    ) from absent


import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from blackboard import (
    BoardChange,
    BoardStore,
    Conflict,
    Contribution,
    Deleted,
    DuplicateRegionError,
    IdempotencyKeyError,
    Level,
    Premise,
    PremiseState,
    RegionKindError,
    UndeclaredRegionError,
    UnsetPremiseError,
    Written,
)
from blackboard._run import RunStore, UnknownRunError


class Bound:
    """A store bound to one board, so a test names that board once.

    The store names a board on every call. These tests are about what a store
    does to one board, so binding it keeps every case readable.
    """

    def __init__(self, store: BoardStore, board_id: str) -> None:
        self.store = store
        self.board_id = board_id

    def declare(self, region: Level | Premise) -> None:
        self.store.declare(self.board_id, region)

    def append(self, level: str, content: object, key: str | None = None) -> int:
        return self.appended(level, content, key).sequence

    def appended(self, level: str, content: object, key: str | None = None) -> Written:
        return self.store.append(self.board_id, level, content, key)

    def set(
        self,
        premise: str,
        value: object,
        expected_version: int,
        key: str | None = None,
    ) -> Written | Conflict:
        return self.store.set(self.board_id, premise, value, expected_version, key)

    def read_level(
        self, level: str, from_sequence: int = 0, limit: int | None = None
    ) -> list[Contribution]:
        return self.store.read_level(self.board_id, level, from_sequence, limit)

    def read_premise(self, premise: str) -> PremiseState:
        return self.store.read_premise(self.board_id, premise)

    def read_board(
        self, from_sequence: int = 0, limit: int | None = None
    ) -> list[BoardChange]:
        return self.store.read_board(self.board_id, from_sequence, limit)

    def read_regions(self) -> list[Level | Premise]:
        return self.store.read_regions(self.board_id)

    def delete(self) -> Deleted:
        return self.store.delete(self.board_id)


class BoardConformance:
    """Subclass this and supply a ``store`` fixture returning a fresh store.

    The board arrives with no regions declared. Each test declares what it
    needs, so an implementation is never asked to guess a starting shape.
    """

    @pytest.fixture
    def store(self) -> BoardStore:
        raise NotImplementedError("supply a store fixture")

    @pytest.fixture
    def board(self, store: BoardStore) -> Bound:
        return Bound(store, str(uuid4()))

    @pytest.fixture
    def ready(self, board: Bound) -> Bound:
        board.declare(Level("application"))
        board.declare(Level("platform"))
        board.declare(Premise("window"))
        return board

    # Declaring

    def test_a_region_is_declared_before_it_is_used(self, board: Bound) -> None:
        with pytest.raises(UndeclaredRegionError):
            board.append("application", "a")

    def test_a_name_declared_twice_is_refused(self, ready: Bound) -> None:
        with pytest.raises(DuplicateRegionError):
            ready.declare(Level("application"))
        with pytest.raises(DuplicateRegionError):
            ready.declare(Premise("application"))

    def test_a_refused_declaration_leaves_the_region_intact(self, ready: Bound) -> None:
        ready.append("application", "a")
        with pytest.raises(DuplicateRegionError):
            ready.declare(Level("application"))
        assert ready.read_level("application") == [
            Contribution(sequence=1, content="a")
        ]

    def test_a_region_declared_later_starts_empty(self, ready: Bound) -> None:
        ready.append("application", "a")
        ready.declare(Level("change"))
        assert ready.read_level("change") == []

    def test_a_premise_declared_later_holds_no_value(self, ready: Bound) -> None:
        ready.declare(Premise("trigger"))
        with pytest.raises(UnsetPremiseError):
            ready.read_premise("trigger")

    # The total order

    def test_one_counter_orders_every_region(self, ready: Bound) -> None:
        first = ready.append("application", "a")
        second = ready.append("platform", "b")
        third = ready.set("window", "w", expected_version=0)
        assert isinstance(third, Written)
        assert (first, second, third.sequence) == (1, 2, 3)

    def test_a_region_declared_later_continues_the_count(self, ready: Bound) -> None:
        ready.append("application", "a")
        ready.declare(Level("change"))
        assert ready.append("change", "c") == 2

    # Levels

    def test_a_level_keeps_arrival_order(self, ready: Bound) -> None:
        ready.append("application", "a")
        ready.append("platform", "p")
        ready.append("application", "b")
        assert ready.read_level("application") == [
            Contribution(sequence=1, content="a"),
            Contribution(sequence=3, content="b"),
        ]

    def test_a_level_read_from_a_bound_is_inclusive(self, ready: Bound) -> None:
        ready.append("application", "a")
        ready.append("application", "b")
        assert ready.read_level("application", from_sequence=2) == [
            Contribution(sequence=2, content="b")
        ]

    def test_appending_to_a_premise_is_refused(self, ready: Bound) -> None:
        with pytest.raises(RegionKindError):
            ready.append("window", "a")

    # Registers

    def test_the_first_write_expects_version_zero(self, ready: Bound) -> None:
        result = ready.set("window", "w", expected_version=0)
        assert result == Written(sequence=1, version=1)

    def test_a_current_version_replaces_the_value(self, ready: Bound) -> None:
        ready.set("window", "w1", expected_version=0)
        assert ready.set("window", "w2", expected_version=1) == Written(
            sequence=2, version=2
        )
        assert ready.read_premise("window") == PremiseState(value="w2", version=2)

    def test_a_stale_version_returns_the_current_one(self, ready: Bound) -> None:
        ready.set("window", "w1", expected_version=0)
        ready.set("window", "w2", expected_version=1)
        assert ready.set("window", "late", expected_version=1) == Conflict(
            current_version=2
        )

    def test_a_conflict_changes_nothing(self, ready: Bound) -> None:
        ready.set("window", "w1", expected_version=0)
        ready.set("window", "late", expected_version=0)
        assert ready.read_premise("window") == PremiseState(value="w1", version=1)

    def test_a_conflict_takes_no_sequence_number(self, ready: Bound) -> None:
        ready.set("window", "w1", expected_version=0)
        ready.set("window", "late", expected_version=0)
        assert ready.append("application", "a") == 2

    def test_a_conflict_is_absent_from_the_record(self, ready: Bound) -> None:
        ready.set("window", "w1", expected_version=0)
        ready.set("window", "late", expected_version=0)
        assert ready.read_board() == [
            BoardChange(sequence=1, region="window", content="w1")
        ]

    def test_setting_a_level_is_refused(self, ready: Bound) -> None:
        with pytest.raises(RegionKindError):
            ready.set("application", "a", expected_version=0)

    def test_a_premise_may_hold_none(self, ready: Bound) -> None:
        ready.set("window", None, expected_version=0)
        assert ready.read_premise("window") == PremiseState(value=None, version=1)

    # Reading the whole board

    def test_the_record_is_every_write_in_order(self, ready: Bound) -> None:
        ready.append("application", "a")
        ready.set("window", "w", expected_version=0)
        ready.append("platform", "p")
        assert ready.read_board() == [
            BoardChange(sequence=1, region="application", content="a"),
            BoardChange(sequence=2, region="window", content="w"),
            BoardChange(sequence=3, region="platform", content="p"),
        ]

    def test_the_record_read_from_a_bound_is_inclusive(self, ready: Bound) -> None:
        ready.append("application", "a")
        ready.append("platform", "p")
        assert ready.read_board(from_sequence=2) == [
            BoardChange(sequence=2, region="platform", content="p")
        ]

    def test_reads_are_snapshots(self, ready: Bound) -> None:
        ready.append("application", "a")
        ready.read_level("application").clear()
        ready.read_board().clear()
        assert len(ready.read_level("application")) == 1
        assert len(ready.read_board()) == 1

    def test_reading_an_undeclared_region_is_refused(self, ready: Bound) -> None:
        with pytest.raises(UndeclaredRegionError):
            ready.read_level("missing")
        with pytest.raises(UndeclaredRegionError):
            ready.read_premise("missing")

    # Content

    def test_content_survives_a_round_trip(self, ready: Bound) -> None:
        content = {"findings": ["oom"], "counts": [1, 2, 3], "nested": {"a": None}}
        ready.append("application", content)
        (contribution,) = ready.read_level("application")
        assert contribution.content == content

    def test_content_that_json_cannot_carry_is_refused(self, ready: Bound) -> None:
        with pytest.raises(TypeError):
            ready.append("application", {"a set"})
        with pytest.raises(TypeError):
            ready.set("window", {"a set"}, expected_version=0)

    def test_refused_content_takes_no_sequence_number(self, ready: Bound) -> None:
        with pytest.raises(TypeError):
            ready.append("application", {"a set"})
        assert ready.append("application", "carried") == 1

    def test_a_tuple_comes_back_as_a_list(self, ready: Bound) -> None:
        ready.append("application", ("a", "b"))
        ready.set("window", ("c", "d"), expected_version=0)
        (contribution,) = ready.read_level("application")
        assert contribution.content == ["a", "b"]
        assert ready.read_premise("window").value == ["c", "d"]

    def test_content_comes_back_detached_from_what_the_caller_wrote(
        self, ready: Bound
    ) -> None:
        content = {"findings": ["oom"]}
        ready.append("application", content)
        content["findings"].append("added after the write")
        (contribution,) = ready.read_level("application")
        assert contribution.content == {"findings": ["oom"]}

    # Concurrency

    def test_concurrent_appends_take_distinct_sequences(self, ready: Bound) -> None:
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

    def test_concurrent_premise_writers_lose_no_update(self, ready: Bound) -> None:
        ready.set("window", [], expected_version=0)
        barrier = threading.Barrier(4)

        def add(item: int) -> None:
            barrier.wait()
            for _ in range(500):
                state = ready.read_premise("window")
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
        state = ready.read_premise("window")
        assert isinstance(state.value, list)
        assert sorted(state.value) == list(range(4))
        assert state.version == 5

    # Bounded reads

    def test_a_level_read_returns_at_most_the_limit(self, ready: Bound) -> None:
        for i in range(10):
            ready.append("application", i)
        assert len(ready.read_level("application", limit=3)) == 3

    def test_a_level_read_with_no_limit_returns_everything(self, ready: Bound) -> None:
        for i in range(10):
            ready.append("application", i)
        assert len(ready.read_level("application")) == 10

    def test_a_limit_of_zero_returns_nothing(self, ready: Bound) -> None:
        ready.append("application", "one")
        assert ready.read_level("application", limit=0) == []

    def test_the_sequence_number_continues_a_bounded_read(self, ready: Bound) -> None:
        for i in range(10):
            ready.append("application", i)
        seen: list[object] = []
        cursor = 0
        while True:
            page = ready.read_level("application", from_sequence=cursor, limit=4)
            if not page:
                break
            seen.extend(c.content for c in page)
            cursor = page[-1].sequence + 1
        assert seen == list(range(10))

    def test_a_whole_board_read_takes_a_limit(self, ready: Bound) -> None:
        for i in range(6):
            ready.append("application", i)
        assert len(ready.read_board(limit=2)) == 2

    # Naming the regions

    def test_a_store_names_the_regions_of_one_board(self, ready: Bound) -> None:
        assert sorted(r.name for r in ready.read_regions()) == [
            "application",
            "platform",
            "window",
        ]

    def test_each_region_says_which_kind_it_is(self, ready: Bound) -> None:
        by_name = {r.name: r for r in ready.read_regions()}
        assert isinstance(by_name["platform"], Level)
        assert isinstance(by_name["window"], Premise)

    def test_a_board_with_no_regions_names_none(self, board: Bound) -> None:
        assert board.read_regions() == []

    # A key names one write

    def test_a_key_writes_once_however_often_it_is_sent(self, ready: Bound) -> None:
        first = ready.appended("platform", {"n": 1}, key="k1")
        again = ready.appended("platform", {"n": 1}, key="k1")
        assert again.sequence == first.sequence
        assert len(ready.read_level("platform")) == 1

    def test_a_repeat_says_it_is_one(self, ready: Bound) -> None:
        assert ready.appended("platform", {"n": 1}, key="k1").repeated is False
        assert ready.appended("platform", {"n": 1}, key="k1").repeated is True

    def test_a_repeat_takes_no_sequence_number(self, ready: Bound) -> None:
        ready.appended("platform", {"n": 1}, key="k1")
        ready.appended("platform", {"n": 1}, key="k1")
        assert ready.append("platform", {"n": 2}) == 2

    def test_a_repeat_leaves_what_the_first_write_stored(self, ready: Bound) -> None:
        ready.appended("platform", {"n": 1}, key="k1")
        ready.appended("platform", {"n": 999}, key="k1")
        assert [c.content for c in ready.read_level("platform")] == [{"n": 1}]

    def test_two_keys_are_two_writes(self, ready: Bound) -> None:
        ready.appended("platform", {"n": 1}, key="k1")
        ready.appended("platform", {"n": 1}, key="k2")
        assert len(ready.read_level("platform")) == 2

    def test_no_key_deduplicates_nothing(self, ready: Bound) -> None:
        ready.append("platform", {"n": 1})
        ready.append("platform", {"n": 1})
        assert len(ready.read_level("platform")) == 2

    def test_a_key_used_for_another_region_is_refused(self, ready: Bound) -> None:
        ready.appended("platform", {"n": 1}, key="k1")
        with pytest.raises(IdempotencyKeyError):
            ready.appended("application", {"n": 1}, key="k1")

    def test_a_refused_key_writes_nothing(self, ready: Bound) -> None:
        ready.appended("platform", {"n": 1}, key="k1")
        with pytest.raises(IdempotencyKeyError):
            ready.appended("application", {"n": 1}, key="k1")
        assert ready.read_level("application") == []

    def test_a_refused_key_takes_no_sequence_number(self, ready: Bound) -> None:
        ready.appended("platform", {"n": 1}, key="k1")
        with pytest.raises(IdempotencyKeyError):
            ready.appended("application", {"n": 1}, key="k1")
        assert ready.append("platform", {"n": 2}) == 2

    def test_a_premise_is_set_once_under_one_key(self, ready: Bound) -> None:
        first = ready.set("window", "a", 0, key="k1")
        assert isinstance(first, Written)
        again = ready.set("window", "b", 0, key="k1")
        assert again == Written(
            sequence=first.sequence, version=first.version, repeated=True
        )
        assert ready.read_premise("window").value == "a"

    def test_a_repeated_set_reaches_the_record_once(self, ready: Bound) -> None:
        ready.set("window", "a", 0, key="k1")
        ready.set("window", "a", 0, key="k1")
        assert [c.region for c in ready.read_board()] == ["window"]

    def test_a_conflicting_set_leaves_its_key_unused(self, ready: Bound) -> None:
        ready.set("window", "a", 0)
        assert isinstance(ready.set("window", "b", 0, key="k1"), Conflict)
        current = ready.read_premise("window").version
        assert isinstance(ready.set("window", "b", current, key="k1"), Written)
        assert ready.read_premise("window").value == "b"

    # Removing a board

    def test_deleting_says_what_it_removed(self, ready: Bound) -> None:
        ready.append("platform", {"n": 1})
        ready.set("window", "w", 0)
        assert ready.delete() == Deleted(
            board_id=ready.board_id, regions_removed=3, writes_removed=2
        )

    def test_deleting_removes_the_regions(self, ready: Bound) -> None:
        ready.delete()
        assert ready.read_regions() == []

    def test_deleting_removes_the_contributions(self, ready: Bound) -> None:
        ready.append("platform", {"n": 1})
        ready.delete()
        ready.declare(Level("platform"))
        assert ready.read_level("platform") == []

    def test_deleting_removes_the_premise_values(self, ready: Bound) -> None:
        ready.set("window", "w", 0)
        ready.delete()
        ready.declare(Premise("window"))
        # Declared afresh, so it holds nothing, as a premise never written does.
        with pytest.raises(UnsetPremiseError):
            ready.read_premise("window")

    def test_a_deleted_region_is_undeclared_again(self, ready: Bound) -> None:
        ready.delete()
        with pytest.raises(UndeclaredRegionError):
            ready.read_level("platform")

    def test_a_board_declared_again_counts_from_one(self, ready: Bound) -> None:
        ready.append("platform", {"n": 1})
        ready.append("platform", {"n": 2})
        ready.delete()
        ready.declare(Level("platform"))
        assert ready.append("platform", {"n": 1}) == 1

    def test_deleting_frees_the_keys_it_wrote(self, ready: Bound) -> None:
        ready.appended("platform", {"n": 1}, key="k1")
        ready.delete()
        ready.declare(Level("platform"))
        assert ready.appended("platform", {"n": 2}, key="k1").repeated is False

    def test_deleting_a_board_nobody_used_names_nothing(
        self, store: BoardStore
    ) -> None:
        board_id = str(uuid4())
        assert store.delete(board_id) == Deleted(
            board_id=board_id, regions_removed=0, writes_removed=0
        )

    def test_deleting_twice_is_safe(self, ready: Bound) -> None:
        ready.append("platform", {"n": 1})
        ready.delete()
        assert ready.delete() == Deleted(
            board_id=ready.board_id, regions_removed=0, writes_removed=0
        )


class SharedStoreConformance:
    """Subclass this and supply a ``store`` fixture. One store, many boards.

    What one board holds is invisible to the others, sequence numbers
    included, so a database serving many concurrent runs is the ordinary case
    rather than a workaround.
    """

    @pytest.fixture
    def store(self) -> BoardStore:
        raise NotImplementedError("supply a store fixture")

    @pytest.fixture
    def two_boards(self, store: BoardStore) -> tuple[Bound, Bound]:
        return Bound(store, str(uuid4())), Bound(store, str(uuid4()))

    @pytest.fixture
    def same_board_twice(self, store: BoardStore) -> tuple[Bound, Bound]:
        board_id = str(uuid4())
        return Bound(store, board_id), Bound(store, board_id)

    def test_a_region_declared_on_one_is_undeclared_on_the_other(
        self, two_boards: tuple[Bound, Bound]
    ) -> None:
        first, second = two_boards
        first.declare(Level("application"))
        with pytest.raises(UndeclaredRegionError):
            second.read_level("application")
        second.declare(Level("application"))

    def test_one_board_does_not_read_the_other_s_contributions(
        self, two_boards: tuple[Bound, Bound]
    ) -> None:
        first, second = two_boards
        first.declare(Level("application"))
        second.declare(Level("application"))
        first.append("application", "from the first")
        second.append("application", "from the second")
        assert [c.content for c in first.read_level("application")] == [
            "from the first"
        ]
        assert [c.content for c in second.read_level("application")] == [
            "from the second"
        ]

    def test_each_board_counts_its_own_sequence(
        self, two_boards: tuple[Bound, Bound]
    ) -> None:
        first, second = two_boards
        first.declare(Level("application"))
        second.declare(Level("application"))
        assert first.append("application", "a") == 1
        assert first.append("application", "b") == 2
        assert second.append("application", "a") == 1

    def test_premises_of_the_same_name_hold_separate_values(
        self, two_boards: tuple[Bound, Bound]
    ) -> None:
        first, second = two_boards
        first.declare(Premise("window"))
        second.declare(Premise("window"))
        first.set("window", "first", expected_version=0)
        assert isinstance(second.set("window", "second", expected_version=0), Written)
        assert first.read_premise("window").value == "first"
        assert second.read_premise("window").value == "second"

    def test_a_second_handle_on_one_board_reads_what_the_first_wrote(
        self, same_board_twice: tuple[Bound, Bound]
    ) -> None:
        first, second = same_board_twice
        first.declare(Level("application"))
        first.append("application", "from the first")
        assert [c.content for c in second.read_level("application")] == [
            "from the first"
        ]

    def test_a_name_one_handle_declared_is_refused_to_the_other(
        self, same_board_twice: tuple[Bound, Bound]
    ) -> None:
        first, second = same_board_twice
        first.declare(Level("application"))
        # Two handles on one board are two processes. This escapes an
        # in-process lock, and it escapes a read that this handle already
        # made, so the refusal rests on the store and has to be the same
        # refusal.
        with pytest.raises(DuplicateRegionError):
            second.declare(Level("application"))
        with pytest.raises(DuplicateRegionError):
            second.declare(Premise("application"))

    def test_the_whole_board_read_names_only_this_board_s_writes(
        self, two_boards: tuple[Bound, Bound]
    ) -> None:
        first, second = two_boards
        first.declare(Level("application"))
        second.declare(Level("application"))
        first.append("application", "a")
        second.append("application", "b")
        assert first.read_board() == [
            BoardChange(sequence=1, region="application", content="a")
        ]

    def test_a_key_writes_once_across_two_views_of_one_board(
        self, same_board_twice: tuple[Bound, Bound]
    ) -> None:
        first, second = same_board_twice
        first.declare(Level("platform"))
        first.appended("platform", {"n": 1}, key="k1")
        assert second.appended("platform", {"n": 1}, key="k1").repeated is True
        assert len(first.read_level("platform")) == 1

    def test_a_key_on_one_board_does_not_silence_another(
        self, two_boards: tuple[Bound, Bound]
    ) -> None:
        for board in two_boards:
            board.declare(Level("platform"))
            board.appended("platform", {"n": 1}, key="k1")
        assert all(len(b.read_level("platform")) == 1 for b in two_boards)

    def test_deleting_one_board_leaves_the_other_whole(
        self, two_boards: tuple[Bound, Bound]
    ) -> None:
        kept, removed = two_boards
        for board in two_boards:
            board.declare(Level("platform"))
            board.declare(Premise("window"))
            board.append("platform", {"n": 1})
            board.set("window", "w", 0)
        removed.delete()
        assert [c.content for c in kept.read_level("platform")] == [{"n": 1}]
        assert kept.read_premise("window").value == "w"
        assert len(kept.read_regions()) == 2

    def test_a_deleted_board_does_not_take_the_other_boards_numbers(
        self, two_boards: tuple[Bound, Bound]
    ) -> None:
        kept, removed = two_boards
        for board in two_boards:
            board.declare(Level("platform"))
            board.append("platform", {"n": 1})
        removed.delete()
        assert kept.append("platform", {"n": 2}) == 2


_OPENED = datetime(2026, 1, 1, tzinfo=UTC)


class RunConformance:
    """Subclass this and supply a ``run_store`` fixture returning a fresh store.

    Every case here is about one run. What a store has to answer for many runs
    at once is in ``SharedRunStoreConformance``.
    """

    @pytest.fixture
    def run_store(self) -> RunStore:
        raise NotImplementedError("supply a run_store fixture")

    @pytest.fixture
    def board_id(self, run_store: RunStore) -> str:
        opened = str(uuid4())
        run_store.open_run(
            opened,
            wall_deadline=_OPENED + timedelta(hours=1),
            idle_deadline=_OPENED + timedelta(minutes=3),
        )
        return opened

    # ------------------------------------------------------------- the run

    def test_a_board_with_no_run_open_is_refused_rather_than_created(
        self, run_store: RunStore
    ) -> None:
        # Opening a run is what creates one, so a mistyped identifier is not
        # answered with a run of its own.
        with pytest.raises(UnknownRunError):
            run_store.registered(str(uuid4()))

    def test_opening_a_run_twice_keeps_the_first_deadlines(
        self, run_store: RunStore, board_id: str
    ) -> None:
        # A second process attaching to a run does not extend the wall clock
        # the first one started.
        run_store.open_run(
            board_id,
            wall_deadline=_OPENED + timedelta(days=1),
            idle_deadline=_OPENED + timedelta(days=1),
        )
        wall, idle = run_store.deadlines(board_id)
        assert wall == _OPENED + timedelta(hours=1)
        assert idle == _OPENED + timedelta(minutes=3)

    # -------------------------------------------------------------- agents

    def test_a_registered_agent_starts_at_the_beginning(
        self, run_store: RunStore, board_id: str
    ) -> None:
        registered = run_store.register(board_id, "ocp", {"signals"}, None)
        assert registered.name == "ocp"
        assert registered.subscribes_to == frozenset({"signals"})
        assert registered.writes_to is None
        assert registered.cursor == 0

    def test_naming_neither_is_kept_apart_from_naming_none(
        self, run_store: RunStore, board_id: str
    ) -> None:
        # None is every premise and no level; an empty set is nothing at all,
        # and a store that returned one for the other would subscribe an agent
        # to what it declined.
        run_store.register(board_id, "silent", frozenset(), frozenset())
        run_store.register(board_id, "default", None, None)
        by_name = {a.name: a for a in run_store.registered(board_id)}
        assert by_name["silent"].subscribes_to == frozenset()
        assert by_name["default"].subscribes_to is None

    def test_a_returning_agent_keeps_its_cursor_and_replaces_its_declaration(
        self, run_store: RunStore, board_id: str
    ) -> None:
        run_store.register(board_id, "ocp", None, None)
        issued = run_store.issue(board_id, "ocp", to_sequence=7)
        run_store.acknowledge(board_id, "ocp", issued)

        returning = run_store.register(board_id, "ocp", {"findings"}, {"findings"})

        assert returning.cursor == 7, "an agent that came back had not forgotten"
        assert returning.subscribes_to == frozenset({"findings"})
        assert [a.name for a in run_store.registered(board_id)] == ["ocp"]

    def test_one_registration_is_read_back_on_its_own(
        self, run_store: RunStore, board_id: str
    ) -> None:
        # What a dispatch reads to find the cursor, which the agent may have
        # moved by acknowledging to another process.
        run_store.register(board_id, "ocp", {"signals"}, None)
        standing = run_store.registration(board_id, "ocp")
        assert standing is not None
        assert standing.subscribes_to == frozenset({"signals"})
        assert run_store.registration(board_id, "never-registered") is None

    def test_the_cursor_read_back_is_the_one_acknowledging_moved(
        self, run_store: RunStore, board_id: str
    ) -> None:
        run_store.register(board_id, "ocp", None, None)
        issued = run_store.issue(board_id, "ocp", to_sequence=11)
        run_store.acknowledge(board_id, "ocp", issued)
        standing = run_store.registration(board_id, "ocp")
        assert standing is not None
        assert standing.cursor == 11

    # ------------------------------------------------------- notifications

    def test_every_notification_takes_its_own_number(
        self, run_store: RunStore, board_id: str
    ) -> None:
        run_store.register(board_id, "ocp", None, None)
        numbers = [run_store.issue(board_id, "ocp", to_sequence=n) for n in (1, 2, 3)]
        assert len(set(numbers)) == 3, "two notifications took one number"

    def test_numbers_are_taken_from_one_counter_per_board(
        self, run_store: RunStore, board_id: str
    ) -> None:
        # Not one per agent: an agent deduplicating on the identifier of a
        # board would otherwise be given a number another agent already had.
        run_store.register(board_id, "ocp", None, None)
        run_store.register(board_id, "git", None, None)
        first = run_store.issue(board_id, "ocp", to_sequence=1)
        second = run_store.issue(board_id, "git", to_sequence=1)
        assert first != second

    def test_an_issued_notification_is_outstanding_until_answered(
        self, run_store: RunStore, board_id: str
    ) -> None:
        run_store.register(board_id, "ocp", None, None)
        issued = run_store.issue(board_id, "ocp", to_sequence=4)
        assert [h.notification_id for h in run_store.outstanding(board_id)] == [issued]

        run_store.acknowledge(board_id, "ocp", issued)
        assert run_store.outstanding(board_id) == []

    def test_acknowledging_advances_the_cursor_to_the_range_it_covered(
        self, run_store: RunStore, board_id: str
    ) -> None:
        run_store.register(board_id, "ocp", None, None)
        issued = run_store.issue(board_id, "ocp", to_sequence=9)
        answered = run_store.acknowledge(board_id, "ocp", issued)
        assert answered is not None
        assert answered.cursor == 9

    def test_acknowledging_one_range_closes_every_range_it_covers(
        self, run_store: RunStore, board_id: str
    ) -> None:
        # The cursor is cumulative, so answering the wider range answered the
        # narrower ones. An agent that answered the newest of several
        # overlapping notifications is otherwise named unfinished for work it
        # did, and a notification whose delivery raised holds the run open.
        run_store.register(board_id, "ocp", None, None)
        narrow = run_store.issue(board_id, "ocp", to_sequence=2)
        wide = run_store.issue(board_id, "ocp", to_sequence=5)

        answered = run_store.acknowledge(board_id, "ocp", wide)

        assert answered is not None
        assert answered.covered == 2
        assert run_store.outstanding(board_id) == []
        assert narrow != wide

    def test_acknowledging_a_wider_range_leaves_a_later_one_outstanding(
        self, run_store: RunStore, board_id: str
    ) -> None:
        run_store.register(board_id, "ocp", None, None)
        earlier = run_store.issue(board_id, "ocp", to_sequence=2)
        later = run_store.issue(board_id, "ocp", to_sequence=8)
        run_store.acknowledge(board_id, "ocp", earlier)
        assert [h.notification_id for h in run_store.outstanding(board_id)] == [later]

    def test_one_agent_s_acknowledgment_leaves_another_s_alone(
        self, run_store: RunStore, board_id: str
    ) -> None:
        run_store.register(board_id, "ocp", None, None)
        run_store.register(board_id, "git", None, None)
        theirs = run_store.issue(board_id, "git", to_sequence=5)
        mine = run_store.issue(board_id, "ocp", to_sequence=5)
        run_store.acknowledge(board_id, "ocp", mine)
        assert [h.agent for h in run_store.outstanding(board_id)] == ["git"]
        assert theirs != mine

    def test_acknowledging_twice_answers_that_nothing_changed(
        self, run_store: RunStore, board_id: str
    ) -> None:
        run_store.register(board_id, "ocp", None, None)
        issued = run_store.issue(board_id, "ocp", to_sequence=3)
        assert run_store.acknowledge(board_id, "ocp", issued) is not None
        assert run_store.acknowledge(board_id, "ocp", issued) is None

    def test_acknowledging_what_was_never_issued_is_refused(
        self, run_store: RunStore, board_id: str
    ) -> None:
        run_store.register(board_id, "ocp", None, None)
        with pytest.raises(UnknownRunError):
            run_store.acknowledge(board_id, "ocp", 4098)

    def test_acknowledging_another_agent_s_notification_is_refused(
        self, run_store: RunStore, board_id: str
    ) -> None:
        run_store.register(board_id, "ocp", None, None)
        run_store.register(board_id, "git", None, None)
        theirs = run_store.issue(board_id, "git", to_sequence=1)
        with pytest.raises(UnknownRunError):
            run_store.acknowledge(board_id, "ocp", theirs)

    def test_forgetting_an_agent_clears_what_it_was_holding(
        self, run_store: RunStore, board_id: str
    ) -> None:
        # A returning agent is told again in one notification covering
        # everything since its cursor, so nothing it was owed is lost.
        run_store.register(board_id, "ocp", None, None)
        run_store.issue(board_id, "ocp", to_sequence=2)
        run_store.forget(board_id, "ocp")
        assert run_store.outstanding(board_id) == []

    def test_forgetting_one_agent_leaves_another_holding(
        self, run_store: RunStore, board_id: str
    ) -> None:
        run_store.register(board_id, "ocp", None, None)
        run_store.register(board_id, "git", None, None)
        run_store.issue(board_id, "ocp", to_sequence=2)
        run_store.issue(board_id, "git", to_sequence=2)
        run_store.forget(board_id, "ocp")
        assert [h.agent for h in run_store.outstanding(board_id)] == ["git"]

    # ------------------------------------------------------- what was sent

    def test_nothing_has_been_dispatched_for_a_new_run(
        self, run_store: RunStore, board_id: str
    ) -> None:
        assert run_store.dispatched_through(board_id) == 0

    def test_the_dispatched_sequence_only_moves_forward(
        self, run_store: RunStore, board_id: str
    ) -> None:
        # What covers the gap after a process stopped compares this to the
        # board's own head, so a lower number arriving late must not undo it.
        run_store.note_dispatched(board_id, 5)
        run_store.note_dispatched(board_id, 2)
        assert run_store.dispatched_through(board_id) == 5

    # ----------------------------------------------------------- deadlines

    def test_pushing_the_idle_deadline_moves_it(
        self, run_store: RunStore, board_id: str
    ) -> None:
        run_store.push_idle(board_id, _OPENED + timedelta(minutes=9))
        _, idle = run_store.deadlines(board_id)
        assert idle == _OPENED + timedelta(minutes=9)

    def test_a_run_past_its_idle_deadline_is_expired(
        self, run_store: RunStore, board_id: str
    ) -> None:
        # Named rather than counted, because a store holds every run an
        # application has opened and a database keeps them between tests.
        assert board_id not in run_store.expired(_OPENED)
        assert board_id in run_store.expired(_OPENED + timedelta(minutes=4))

    def test_a_run_past_its_wall_clock_is_expired_though_it_is_busy(
        self, run_store: RunStore, board_id: str
    ) -> None:
        run_store.push_idle(board_id, _OPENED + timedelta(days=1))
        assert board_id in run_store.expired(_OPENED + timedelta(hours=2))

    def test_a_closed_run_is_never_expired(
        self, run_store: RunStore, board_id: str
    ) -> None:
        run_store.close(board_id, "Settled", ())
        assert board_id not in run_store.expired(_OPENED + timedelta(days=1))

    # ------------------------------------------------------------- closing

    def test_a_run_is_open_until_it_is_closed(
        self, run_store: RunStore, board_id: str
    ) -> None:
        assert run_store.closed_as(board_id) is None
        assert run_store.close(board_id, "Settled", ["ocp"]) is True
        closure = run_store.closed_as(board_id)
        assert closure is not None
        assert closure.outcome == "Settled"
        assert closure.unfinished == frozenset({"ocp"})

    def test_only_one_caller_closes_a_run(
        self, run_store: RunStore, board_id: str
    ) -> None:
        # A local timer and a sweep in another process reach this at the same
        # moment, and a run that ended twice with two outcomes is a run
        # nothing can report.
        assert run_store.close(board_id, "Settled", ()) is True
        assert run_store.close(board_id, "WallClockExpired", ()) is False
        closure = run_store.closed_as(board_id)
        assert closure is not None
        assert closure.outcome == "Settled"

    def test_an_abort_keeps_the_reason_it_was_given(
        self, run_store: RunStore, board_id: str
    ) -> None:
        run_store.close(board_id, "Aborted", (), "the service shut down")
        closure = run_store.closed_as(board_id)
        assert closure is not None
        assert closure.reason == "the service shut down"

    def test_the_idle_deadline_does_not_move_on_a_closed_run(
        self, run_store: RunStore, board_id: str
    ) -> None:
        run_store.close(board_id, "Settled", ())
        run_store.push_idle(board_id, _OPENED + timedelta(days=1))
        assert board_id not in run_store.expired(_OPENED + timedelta(days=2))

    # -------------------------------------------------------------- delete

    def test_deleting_a_run_removes_it(
        self, run_store: RunStore, board_id: str
    ) -> None:
        run_store.register(board_id, "ocp", None, None)
        run_store.delete(board_id)
        with pytest.raises(UnknownRunError):
            run_store.registered(board_id)

    def test_deleting_a_run_nothing_opened_is_safe(self, run_store: RunStore) -> None:
        run_store.delete(str(uuid4()))


class SharedRunStoreConformance:
    """Subclass this and supply a ``run_store`` fixture. One store, many runs.

    What one run holds is invisible to the others, notification numbers
    included, so a database serving every run an application holds is the
    ordinary case rather than a workaround.
    """

    @pytest.fixture
    def run_store(self) -> RunStore:
        raise NotImplementedError("supply a run_store fixture")

    @pytest.fixture
    def two_runs(self, run_store: RunStore) -> tuple[str, str]:
        opened = []
        for _ in range(2):
            board_id = str(uuid4())
            run_store.open_run(
                board_id,
                wall_deadline=_OPENED + timedelta(hours=1),
                idle_deadline=_OPENED + timedelta(minutes=3),
            )
            opened.append(board_id)
        return opened[0], opened[1]

    def test_an_agent_registered_on_one_is_absent_from_the_other(
        self, run_store: RunStore, two_runs: tuple[str, str]
    ) -> None:
        first, second = two_runs
        run_store.register(first, "ocp", None, None)
        assert [a.name for a in run_store.registered(first)] == ["ocp"]
        assert run_store.registered(second) == []

    def test_each_run_numbers_its_own_notifications(
        self, run_store: RunStore, two_runs: tuple[str, str]
    ) -> None:
        first, second = two_runs
        run_store.register(first, "ocp", None, None)
        run_store.register(second, "ocp", None, None)
        assert run_store.issue(first, "ocp", to_sequence=1) == run_store.issue(
            second, "ocp", to_sequence=1
        )

    def test_one_agent_on_two_runs_keeps_two_cursors(
        self, run_store: RunStore, two_runs: tuple[str, str]
    ) -> None:
        first, second = two_runs
        run_store.register(first, "ocp", None, None)
        run_store.register(second, "ocp", None, None)
        issued = run_store.issue(first, "ocp", to_sequence=6)
        run_store.acknowledge(first, "ocp", issued)
        assert run_store.registered(first)[0].cursor == 6
        assert run_store.registered(second)[0].cursor == 0

    def test_closing_one_run_leaves_the_other_open(
        self, run_store: RunStore, two_runs: tuple[str, str]
    ) -> None:
        first, second = two_runs
        run_store.close(first, "Settled", ())
        assert run_store.closed_as(first) is not None
        assert run_store.closed_as(second) is None

    def test_deleting_one_run_leaves_the_other(
        self, run_store: RunStore, two_runs: tuple[str, str]
    ) -> None:
        first, second = two_runs
        run_store.register(second, "ocp", None, None)
        run_store.delete(first)
        assert [a.name for a in run_store.registered(second)] == ["ocp"]

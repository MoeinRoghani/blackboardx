"""The suite every store implementation is held to.

`BoardStore` is a protocol, so an application may keep its record in a database that
this library ships no adapter for. What that store has to guarantee
is not obvious from the signatures: one counter across every region rather
than one per region, a conflicting premise write taking no sequence number so
a conflict leaves no gap, a key writing once, a bounded read continuing from
a sequence rather than an offset. Each of those has a case here because an
implementation gets each of them wrong.

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
from uuid import uuid4

from blackboard import (
    AgentProgress,
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
    RunRecord,
    UndeclaredRegionError,
    Unsent,
    UnsetPremiseError,
    Written,
)


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

    def appended(
        self,
        level: str,
        content: object,
        key: str | None = None,
        writer: str | None = None,
    ) -> Written:
        return self.store.append(self.board_id, level, content, key, writer=writer)

    def set(
        self,
        premise: str,
        value: object,
        expected_version: int,
        key: str | None = None,
        writer: str | None = None,
    ) -> Written | Conflict:
        return self.store.set(
            self.board_id, premise, value, expected_version, key, writer=writer
        )

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

    def open_run(self, wall_clock: float = 3600.0, idle: float = 1800.0) -> None:
        self.store.open_run(self.board_id, wall_clock=wall_clock, idle=idle)

    def read_run(self) -> RunRecord | None:
        return self.store.read_run(self.board_id)

    def touch_run(self, idle: float = 1800.0) -> None:
        self.store.touch_run(self.board_id, idle=idle)

    def close_run(self, closed_as: str = "settled", reason: str | None = None) -> bool:
        return self.store.close_run(
            self.board_id, closed_as=closed_as, reason=reason, unfinished=frozenset()
        )

    def unsent(self, limit: int = 100) -> list[Unsent]:
        return self.store.unsent(limit)

    def mark_sent(self, agent: str, through: int) -> None:
        self.store.mark_sent(self.board_id, agent, through=through)

    def read_agents(self) -> list[AgentProgress]:
        return self.store.read_agents(self.board_id)

    def mark_notified(self, agent: str, through: int) -> None:
        self.store.mark_notified(self.board_id, agent, through=through)

    def acknowledge(self, agent: str, through: int) -> AgentProgress | None:
        return self.store.acknowledge(self.board_id, agent, through=through)

    def progress(self, agent: str) -> AgentProgress | None:
        """The one entry for this agent, or nothing where it has none."""
        return next((p for p in self.read_agents() if p.agent == agent), None)

    def read_regions(self) -> list[Level | Premise]:
        return self.store.read_regions(self.board_id)

    def delete(self) -> Deleted:
        return self.store.delete(self.board_id)


def entries(read: list[Contribution]) -> list[tuple[int, object]]:
    """Projects a level read onto the fields these cases are about."""
    return [(c.sequence, c.content) for c in read]


def changes(read: list[BoardChange]) -> list[tuple[int, str, object]]:
    """Projects a whole-board read onto the fields these cases are about."""
    return [(c.sequence, c.region, c.content) for c in read]


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
        assert entries(ready.read_level("application")) == [(1, "a")]

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
        assert entries(ready.read_level("application")) == [(1, "a"), (3, "b")]

    def test_a_level_read_from_a_bound_is_inclusive(self, ready: Bound) -> None:
        ready.append("application", "a")
        ready.append("application", "b")
        assert entries(ready.read_level("application", from_sequence=2)) == [(2, "b")]

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
        state = ready.read_premise("window")
        assert (state.value, state.version) == ("w2", 2)

    def test_a_stale_version_returns_the_current_one(self, ready: Bound) -> None:
        ready.set("window", "w1", expected_version=0)
        ready.set("window", "w2", expected_version=1)
        assert ready.set("window", "late", expected_version=1) == Conflict(
            current_version=2
        )

    def test_a_conflict_changes_nothing(self, ready: Bound) -> None:
        ready.set("window", "w1", expected_version=0)
        ready.set("window", "late", expected_version=0)
        state = ready.read_premise("window")
        assert (state.value, state.version) == ("w1", 1)

    def test_a_conflict_takes_no_sequence_number(self, ready: Bound) -> None:
        ready.set("window", "w1", expected_version=0)
        ready.set("window", "late", expected_version=0)
        assert ready.append("application", "a") == 2

    def test_a_conflict_is_absent_from_the_record(self, ready: Bound) -> None:
        ready.set("window", "w1", expected_version=0)
        ready.set("window", "late", expected_version=0)
        assert changes(ready.read_board()) == [(1, "window", "w1")]

    def test_setting_a_level_is_refused(self, ready: Bound) -> None:
        with pytest.raises(RegionKindError):
            ready.set("application", "a", expected_version=0)

    def test_a_premise_may_hold_none(self, ready: Bound) -> None:
        ready.set("window", None, expected_version=0)
        state = ready.read_premise("window")
        assert (state.value, state.version) == (None, 1)

    # Reading the whole board

    def test_the_record_is_every_write_in_order(self, ready: Bound) -> None:
        ready.append("application", "a")
        ready.set("window", "w", expected_version=0)
        ready.append("platform", "p")
        assert changes(ready.read_board()) == [
            (1, "application", "a"),
            (2, "window", "w"),
            (3, "platform", "p"),
        ]

    def test_the_record_read_from_a_bound_is_inclusive(self, ready: Bound) -> None:
        ready.append("application", "a")
        ready.append("platform", "p")
        assert changes(ready.read_board(from_sequence=2)) == [(2, "platform", "p")]

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

    def test_a_contribution_names_its_writer(self, ready: Bound) -> None:
        ready.appended("application", "found it", writer="triage")
        (contribution,) = ready.read_level("application")
        assert contribution.writer == "triage"

    def test_a_write_without_a_writer_records_none(self, ready: Bound) -> None:
        ready.append("application", "anonymous")
        (contribution,) = ready.read_level("application")
        assert contribution.writer is None

    def test_the_store_stamps_the_instant_of_a_write(self, ready: Bound) -> None:
        """The store's clock, not the caller's: no instant crosses the call."""
        ready.append("application", "first")
        ready.append("application", "second")
        first, second = ready.read_level("application")
        assert first.written_at is not None
        assert first.written_at.tzinfo is not None
        assert second.written_at is not None
        assert first.written_at <= second.written_at

    def test_a_premise_names_its_writer_and_instant(self, ready: Bound) -> None:
        ready.set("window", "20:00", expected_version=0, writer="operator")
        state = ready.read_premise("window")
        assert state.writer == "operator"
        assert state.written_at is not None
        assert state.written_at.tzinfo is not None

    def test_a_change_names_its_writer_and_instant(self, ready: Bound) -> None:
        ready.appended("application", "a", writer="triage")
        ready.set("window", "w", expected_version=0, writer="operator")
        by_region = {change.region: change for change in ready.read_board()}
        assert by_region["application"].writer == "triage"
        assert by_region["window"].writer == "operator"
        assert all(c.written_at is not None for c in by_region.values())

    def test_a_repeated_key_answers_with_the_first_write_and_writer(
        self, ready: Bound
    ) -> None:
        """The second sender's name does not overwrite the first's."""
        ready.appended("application", "found it", key="k1", writer="triage")
        ready.appended("application", "found it", key="k1", writer="impostor")
        (contribution,) = ready.read_level("application")
        assert contribution.writer == "triage"


class RunConformance:
    """A run's deadlines and outcome, held by the store rather than a process.

    A run closes because nothing happened, and nothing happening means no
    process is being asked anything about that board. So the two deadlines
    are instants the store holds and any caller may read, and closing is a
    write only one caller can win.
    """

    def test_a_board_with_no_run_reads_as_none(self, ready: Bound) -> None:
        assert ready.read_run() is None

    def test_opening_a_run_sets_both_deadlines_ahead_of_the_store_s_clock(
        self, ready: Bound
    ) -> None:
        ready.open_run(wall_clock=3600.0, idle=1800.0)
        run = ready.read_run()
        assert run is not None
        assert run.now < run.idle_deadline < run.wall_deadline
        assert run.closed_as is None
        assert run.expired is None

    def test_the_clock_that_answers_is_the_store_s(self, ready: Bound) -> None:
        """A caller compares two instants that came from one clock."""
        ready.open_run()
        first = ready.read_run()
        second = ready.read_run()
        assert first is not None and second is not None
        assert first.now <= second.now
        assert first.idle_deadline == second.idle_deadline

    def test_touching_a_run_pushes_the_idle_deadline_and_leaves_the_wall_clock(
        self, ready: Bound
    ) -> None:
        ready.open_run(wall_clock=3600.0, idle=1.0)
        before = ready.read_run()
        assert before is not None
        ready.touch_run(idle=1800.0)
        after = ready.read_run()
        assert after is not None
        assert after.idle_deadline > before.idle_deadline
        assert after.wall_deadline == before.wall_deadline

    def test_an_idle_deadline_in_the_past_reads_as_settled(self, ready: Bound) -> None:
        ready.open_run(wall_clock=3600.0, idle=-1.0)
        run = ready.read_run()
        assert run is not None
        assert run.expired == "settled"

    def test_a_wall_clock_in_the_past_outranks_the_idle_deadline(
        self, ready: Bound
    ) -> None:
        ready.open_run(wall_clock=-1.0, idle=-1.0)
        run = ready.read_run()
        assert run is not None
        assert run.expired == "wall_clock_expired"

    def test_the_first_caller_to_close_wins_and_the_rest_are_told(
        self, ready: Bound
    ) -> None:
        """This is what makes closing once-only without any lock."""
        ready.open_run()
        assert ready.close_run("settled") is True
        assert ready.close_run("settled") is False
        assert ready.close_run("aborted", reason="too late") is False

    def test_a_closed_run_keeps_the_outcome_the_winner_wrote(
        self, ready: Bound
    ) -> None:
        ready.open_run()
        ready.store.close_run(
            ready.board_id,
            closed_as="aborted",
            reason="the operator stopped it",
            unfinished=frozenset({"triage", "netops"}),
        )
        run = ready.read_run()
        assert run is not None
        assert run.closed_as == "aborted"
        assert run.reason == "the operator stopped it"
        assert run.unfinished == frozenset({"triage", "netops"})

    def test_a_closed_run_is_never_expired(self, ready: Bound) -> None:
        """Expiry asks what to do next, and a closed run needs nothing done."""
        ready.open_run(wall_clock=-1.0, idle=-1.0)
        ready.close_run("wall_clock_expired")
        run = ready.read_run()
        assert run is not None
        assert run.expired is None

    def test_touching_a_closed_run_does_not_reopen_it(self, ready: Bound) -> None:
        ready.open_run()
        ready.close_run("settled")
        ready.touch_run()
        run = ready.read_run()
        assert run is not None
        assert run.closed_as == "settled"

    def test_a_run_past_a_deadline_is_found_by_the_sweep(self, ready: Bound) -> None:
        ready.open_run(wall_clock=3600.0, idle=-1.0)
        assert ready.board_id in ready.store.runs_past_deadline()

    def test_a_run_inside_its_deadlines_is_not_found(self, ready: Bound) -> None:
        ready.open_run()
        assert ready.board_id not in ready.store.runs_past_deadline()

    def test_a_closed_run_is_not_found_again(self, ready: Bound) -> None:
        ready.open_run(wall_clock=-1.0, idle=-1.0)
        ready.close_run("wall_clock_expired")
        assert ready.board_id not in ready.store.runs_past_deadline()

    def test_the_sweep_returns_no_more_than_it_was_asked_for(
        self, ready: Bound
    ) -> None:
        ready.open_run(wall_clock=3600.0, idle=-1.0)
        assert len(ready.store.runs_past_deadline(limit=1)) <= 1


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
        assert changes(first.read_board()) == [(1, "application", "a")]

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


class AgentConformance:
    """How far each agent has been told and has answered, held by the store.

    A process that takes a write is not always the process an agent
    registered with, so how far that agent has been told cannot live in
    either one. Both numbers are sequence numbers on the board, and both
    only rise, so two processes writing them converge without a lock.
    """

    def test_a_board_that_notified_nobody_reads_as_empty(self, ready: Bound) -> None:
        assert ready.read_agents() == []

    def test_notifying_an_unknown_agent_creates_its_entry(self, ready: Bound) -> None:
        ready.mark_notified("triage", 4)
        assert ready.progress("triage") == AgentProgress(
            agent="triage", notified_through=4, acknowledged_through=0
        )

    def test_a_notification_further_on_raises_the_watermark(self, ready: Bound) -> None:
        ready.mark_notified("triage", 4)
        ready.mark_notified("triage", 9)
        progress = ready.progress("triage")
        assert progress is not None
        assert progress.notified_through == 9

    def test_a_notification_further_back_changes_nothing(self, ready: Bound) -> None:
        """Two processes notifying one agent leave the higher, in any order."""
        ready.mark_notified("triage", 9)
        ready.mark_notified("triage", 4)
        progress = ready.progress("triage")
        assert progress is not None
        assert progress.notified_through == 9

    def test_each_agent_carries_its_own_pair(self, ready: Bound) -> None:
        ready.mark_notified("triage", 4)
        ready.mark_notified("capacity", 7)
        assert {p.agent: p.notified_through for p in ready.read_agents()} == {
            "triage": 4,
            "capacity": 7,
        }

    def test_an_agent_owes_an_answer_once_it_has_been_told(self, ready: Bound) -> None:
        ready.mark_notified("triage", 4)
        progress = ready.progress("triage")
        assert progress is not None
        assert progress.outstanding

    def test_acknowledging_returns_the_entry_as_it_stood_before(
        self, ready: Bound
    ) -> None:
        ready.mark_notified("triage", 4)
        assert ready.acknowledge("triage", 4) == AgentProgress(
            agent="triage", notified_through=4, acknowledged_through=0
        )

    def test_an_acknowledged_agent_owes_nothing(self, ready: Bound) -> None:
        ready.mark_notified("triage", 4)
        ready.acknowledge("triage", 4)
        progress = ready.progress("triage")
        assert progress is not None
        assert not progress.outstanding

    def test_acknowledging_part_of_what_was_told_leaves_the_rest_owed(
        self, ready: Bound
    ) -> None:
        ready.mark_notified("triage", 9)
        ready.acknowledge("triage", 4)
        progress = ready.progress("triage")
        assert progress is not None
        assert progress.acknowledged_through == 4
        assert progress.outstanding

    def test_only_the_first_of_two_equal_acknowledgments_sees_it_unanswered(
        self, ready: Bound
    ) -> None:
        """That is what tells a first acknowledgment from a repeat."""
        ready.mark_notified("triage", 4)
        first = ready.acknowledge("triage", 4)
        second = ready.acknowledge("triage", 4)
        assert first is not None and second is not None
        assert first.acknowledged_through < 4
        assert second.acknowledged_through == 4

    def test_an_acknowledgment_further_back_does_not_lower_the_watermark(
        self, ready: Bound
    ) -> None:
        ready.mark_notified("triage", 9)
        ready.acknowledge("triage", 9)
        ready.acknowledge("triage", 4)
        progress = ready.progress("triage")
        assert progress is not None
        assert progress.acknowledged_through == 9

    def test_acknowledging_beyond_what_was_told_is_refused(self, ready: Bound) -> None:
        """The store never handed that range out, so it is not progress."""
        ready.mark_notified("triage", 4)
        assert ready.acknowledge("triage", 9) is None
        progress = ready.progress("triage")
        assert progress is not None
        assert progress.acknowledged_through == 0

    def test_acknowledging_for_an_agent_with_no_entry_is_refused(
        self, ready: Bound
    ) -> None:
        assert ready.acknowledge("stranger", 1) is None

    def test_an_acknowledgment_reaches_only_the_agent_it_names(
        self, ready: Bound
    ) -> None:
        ready.mark_notified("triage", 4)
        ready.mark_notified("capacity", 4)
        ready.acknowledge("triage", 4)
        assert {p.agent: p.acknowledged_through for p in ready.read_agents()} == {
            "triage": 4,
            "capacity": 0,
        }

    def test_concurrent_notifications_leave_the_highest(self, ready: Bound) -> None:
        """Eight processes notifying one agent, and none undoes another."""
        barrier = threading.Barrier(8)

        def notify(through: int) -> None:
            barrier.wait()
            ready.mark_notified("triage", through)

        threads = [threading.Thread(target=notify, args=(i,)) for i in range(1, 9)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        progress = ready.progress("triage")
        assert progress is not None
        assert progress.notified_through == 8

    def test_exactly_one_of_eight_equal_acknowledgments_sees_it_unanswered(
        self, ready: Bound
    ) -> None:
        """A repeated acknowledgment is told from a first one under load."""
        ready.mark_notified("triage", 5)
        barrier = threading.Barrier(8)
        firsts: list[bool] = []
        lock = threading.Lock()

        def acknowledge() -> None:
            barrier.wait()
            prior = ready.acknowledge("triage", 5)
            with lock:
                firsts.append(prior is not None and prior.acknowledged_through < 5)

        threads = [threading.Thread(target=acknowledge) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sum(firsts) == 1

    def test_an_agent_on_one_board_is_absent_from_another(self, ready: Bound) -> None:
        elsewhere = Bound(ready.store, f"{ready.board_id}-elsewhere")
        ready.mark_notified("triage", 4)
        assert elsewhere.read_agents() == []

    def test_deleting_a_board_removes_what_its_agents_had_answered(
        self, ready: Bound
    ) -> None:
        ready.mark_notified("triage", 4)
        ready.delete()
        assert ready.read_agents() == []


class OutboxConformance:
    """A write and the intent to notify commit together, or not at all.

    The intent to notify used to live in the memory of the process that took
    the write, so a process that committed and stopped lost it. It is a row
    now, written with the contribution, and whoever holds the agent sends it
    and marks it sent.
    """

    def test_a_board_that_notified_nobody_has_nothing_unsent(
        self, ready: Bound
    ) -> None:
        assert ready.unsent() == []

    def test_a_write_naming_no_agent_records_nothing(self, ready: Bound) -> None:
        ready.append("application", "a finding")
        assert ready.unsent() == []

    def test_a_write_records_one_row_for_each_agent_it_names(
        self, ready: Bound
    ) -> None:
        ready.store.append(
            ready.board_id,
            "application",
            "a finding",
            notify=frozenset({"triage", "capacity"}),
        )
        assert sorted((row.agent, row.through) for row in ready.unsent()) == [
            ("capacity", 1),
            ("triage", 1),
        ]

    def test_the_row_carries_the_sequence_the_write_took(self, ready: Bound) -> None:
        ready.append("application", "first")
        written = ready.store.append(
            ready.board_id, "application", "second", notify=frozenset({"triage"})
        )
        (row,) = ready.unsent()
        assert row.through == written.sequence == 2

    def test_a_premise_write_records_the_intent_too(self, ready: Bound) -> None:
        ready.declare(Premise("severity"))
        ready.store.set(
            ready.board_id, "severity", "high", 0, notify=frozenset({"triage"})
        )
        assert [(row.agent, row.through) for row in ready.unsent()] == [("triage", 1)]

    def test_a_conflicting_premise_write_records_nothing(self, ready: Bound) -> None:
        """It stored nothing, so there is nothing to tell anyone about."""
        ready.declare(Premise("severity"))
        ready.set("severity", "high", 0)
        result = ready.store.set(
            ready.board_id, "severity", "later", 0, notify=frozenset({"triage"})
        )
        assert isinstance(result, Conflict)
        assert ready.unsent() == []

    def test_a_repeated_key_records_nothing_the_second_time(self, ready: Bound) -> None:
        """Nothing reached the board, so nobody is newly owed a notification."""
        ready.store.append(
            ready.board_id, "application", "a", key := "k1", notify=frozenset({"t"})
        )
        ready.store.append(
            ready.board_id, "application", "a", key, notify=frozenset({"t"})
        )
        assert len(ready.unsent()) == 1

    def test_marking_a_row_sent_removes_it(self, ready: Bound) -> None:
        ready.store.append(
            ready.board_id, "application", "a", notify=frozenset({"triage"})
        )
        ready.mark_sent("triage", 1)
        assert ready.unsent() == []

    def test_marking_one_agent_leaves_the_other_owed(self, ready: Bound) -> None:
        ready.store.append(
            ready.board_id,
            "application",
            "a",
            notify=frozenset({"triage", "capacity"}),
        )
        ready.mark_sent("triage", 1)
        assert [row.agent for row in ready.unsent()] == ["capacity"]

    def test_marking_an_agent_with_no_rows_changes_nothing(self, ready: Bound) -> None:
        ready.store.append(
            ready.board_id, "application", "a", notify=frozenset({"triage"})
        )
        ready.mark_sent("stranger", 1)
        assert len(ready.unsent()) == 1

    def test_marking_covers_every_row_at_or_below_it(self, ready: Bound) -> None:
        """A notification covers a range, so sending it answers the range."""
        for content in ("first", "second", "third"):
            ready.store.append(
                ready.board_id, "application", content, notify=frozenset({"triage"})
            )
        ready.mark_sent("triage", 2)
        assert [row.through for row in ready.unsent()] == [3]

    def test_marking_below_the_oldest_row_leaves_them_all(self, ready: Bound) -> None:
        ready.append("application", "untold")
        ready.store.append(
            ready.board_id, "application", "a", notify=frozenset({"triage"})
        )
        ready.mark_sent("triage", 1)
        assert [row.through for row in ready.unsent()] == [2]

    def test_marking_the_same_row_twice_is_harmless(self, ready: Bound) -> None:
        ready.store.append(
            ready.board_id, "application", "a", notify=frozenset({"triage"})
        )
        ready.mark_sent("triage", 1)
        ready.mark_sent("triage", 1)
        assert ready.unsent() == []

    def test_one_agent_owed_two_writes_carries_two_rows(self, ready: Bound) -> None:
        for content in ("first", "second"):
            ready.store.append(
                ready.board_id, "application", content, notify=frozenset({"triage"})
            )
        assert [row.through for row in ready.unsent()] == [1, 2]

    def test_the_oldest_work_comes_back_first(self, ready: Bound) -> None:
        for content in ("first", "second", "third"):
            ready.store.append(
                ready.board_id, "application", content, notify=frozenset({"triage"})
            )
        assert [row.through for row in ready.unsent()] == [1, 2, 3]

    def test_the_limit_bounds_what_comes_back(self, ready: Bound) -> None:
        for content in ("first", "second", "third"):
            ready.store.append(
                ready.board_id, "application", content, notify=frozenset({"triage"})
            )
        assert [row.through for row in ready.unsent(limit=2)] == [1, 2]

    def test_a_row_names_the_board_it_belongs_to(self, ready: Bound) -> None:
        ready.store.append(
            ready.board_id, "application", "a", notify=frozenset({"triage"})
        )
        (row,) = ready.unsent()
        assert row.board_id == ready.board_id

    def test_deleting_a_board_removes_what_it_had_unsent(self, ready: Bound) -> None:
        ready.store.append(
            ready.board_id, "application", "a", notify=frozenset({"triage"})
        )
        ready.delete()
        assert ready.unsent() == []

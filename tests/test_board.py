"""The board stores contributions in declared regions under one total order."""

import sys
import threading
from collections.abc import Iterator
from datetime import timedelta

import pytest

from blackboard import (
    BoardChange,
    Conflict,
    Contribution,
    DuplicateRegionError,
    InMemoryStore,
    Level,
    Premise,
    PremiseState,
    RegionKindError,
    RunLimits,
    UndeclaredRegionError,
    UnsetPremiseError,
    Written,
    create_model,
)
from blackboard.conformance import Bound


def make_board() -> Bound:
    """One board inside a fresh store, with the regions these tests use."""
    board = Bound(InMemoryStore(), "test-board")
    for region in (Level("application"), Level("platform"), Premise("window")):
        board.declare(region)
    return board


def _board_with(*regions: Level | Premise) -> Bound:
    board = Bound(InMemoryStore(), "test-board")
    for region in regions:
        board.declare(region)
    return board


@pytest.fixture
def frequent_thread_switches() -> Iterator[None]:
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(interval)


def entries(read: list[Contribution]) -> list[tuple[int, object]]:
    """A level read, projected onto the fields these cases assert about."""
    return [(c.sequence, c.content) for c in read]


def changes(read: list[BoardChange]) -> list[tuple[int, str, object]]:
    """A whole-board read, projected the same way."""
    return [(c.sequence, c.region, c.content) for c in read]


def state(read: PremiseState) -> tuple[object, int]:
    """A premise read, projected onto its value and version."""
    return (read.value, read.version)


class TestAppend:
    def test_append_returns_the_next_sequence_number(self) -> None:
        board = make_board()
        assert board.append("application", "a") == 1
        assert board.append("application", "b") == 2

    def test_one_counter_orders_writes_across_all_regions(self) -> None:
        board = make_board()
        first = board.append("application", "a")
        second = board.append("platform", "b")
        result = board.set("window", "w", expected_version=0)
        assert isinstance(result, Written)
        assert (first, second, result.sequence) == (1, 2, 3)

    def test_append_to_an_undeclared_region_is_refused(self) -> None:
        board = make_board()
        with pytest.raises(UndeclaredRegionError):
            board.append("missing", "a")

    def test_append_creates_no_region_implicitly(self) -> None:
        board = make_board()
        with pytest.raises(UndeclaredRegionError):
            board.append("missing", "a")
        with pytest.raises(UndeclaredRegionError):
            board.read_level("missing")

    def test_append_to_a_premise_is_refused(self) -> None:
        board = make_board()
        with pytest.raises(RegionKindError):
            board.append("window", "a")

    def test_concurrent_appends_all_succeed_with_distinct_sequences(
        self, frequent_thread_switches: None
    ) -> None:
        board = make_board()
        results: list[int] = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(8)

        def run() -> None:
            barrier.wait()
            local = [board.append("application", "x") for _ in range(100)]
            with results_lock:
                results.extend(local)

        threads = [threading.Thread(target=run) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(results) == list(range(1, 801))


class TestSet:
    def test_first_write_expects_version_zero_and_yields_version_one(self) -> None:
        board = make_board()
        result = board.set("window", "w", expected_version=0)
        assert isinstance(result, Written)
        assert result.version == 1

    def test_a_current_version_write_replaces_the_value(self) -> None:
        board = make_board()
        board.set("window", "w1", expected_version=0)
        result = board.set("window", "w2", expected_version=1)
        assert result == Written(sequence=2, version=2)
        assert state(board.read_premise("window")) == ("w2", 2)

    def test_a_stale_version_returns_conflict_with_the_current_version(self) -> None:
        board = make_board()
        board.set("window", "w1", expected_version=0)
        board.set("window", "w2", expected_version=1)
        result = board.set("window", "late", expected_version=1)
        assert result == Conflict(current_version=2)

    def test_a_conflicting_write_leaves_the_premise_unchanged(self) -> None:
        board = make_board()
        board.set("window", "w1", expected_version=0)
        board.set("window", "late", expected_version=0)
        assert state(board.read_premise("window")) == ("w1", 1)

    def test_a_conflicting_write_takes_no_sequence_number(self) -> None:
        board = make_board()
        board.set("window", "w1", expected_version=0)
        board.set("window", "late", expected_version=0)
        assert board.append("application", "a") == 2

    def test_a_conflicting_write_is_absent_from_the_board_record(self) -> None:
        board = make_board()
        board.set("window", "w1", expected_version=0)
        board.set("window", "late", expected_version=0)
        assert changes(board.read_board()) == [(1, "window", "w1")]

    def test_set_on_a_level_is_refused(self) -> None:
        board = make_board()
        with pytest.raises(RegionKindError):
            board.set("application", "a", expected_version=0)

    def test_set_on_an_undeclared_region_is_refused(self) -> None:
        board = make_board()
        with pytest.raises(UndeclaredRegionError):
            board.set("missing", "a", expected_version=0)

    def test_concurrent_premise_writers_lose_no_update(
        self, frequent_thread_switches: None
    ) -> None:
        board = _board_with(Premise("namespace"))
        board.set("namespace", [], expected_version=0)
        barrier = threading.Barrier(8)

        def add(item: int) -> None:
            barrier.wait()
            for _ in range(1000):
                state = board.read_premise("namespace")
                assert isinstance(state.value, list)
                result = board.set(
                    "namespace", [*state.value, item], expected_version=state.version
                )
                if isinstance(result, Written):
                    return
            raise AssertionError("the writer never won a version")

        threads = [threading.Thread(target=add, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        state = board.read_premise("namespace")
        assert isinstance(state.value, list)
        assert sorted(state.value) == list(range(8))
        assert state.version == 9


class TestReads:
    def test_read_level_returns_contributions_in_arrival_order(self) -> None:
        board = make_board()
        board.append("application", "a")
        board.append("platform", "p")
        board.append("application", "b")
        assert entries(board.read_level("application")) == [(1, "a"), (3, "b")]

    def test_read_level_from_sequence_is_inclusive(self) -> None:
        board = make_board()
        board.append("application", "a")
        board.append("application", "b")
        assert entries(board.read_level("application", from_sequence=2)) == [(2, "b")]

    def test_content_comes_back_equal_and_detached_from_the_caller(self) -> None:
        board = make_board()
        content = {"finding": ["f1"]}
        board.append("application", content)
        content["finding"].append("f2")
        (contribution,) = board.read_level("application")
        assert contribution.content == {"finding": ["f1"]}

    def test_read_premise_before_any_write_raises_unset_premise_error(self) -> None:
        board = make_board()
        with pytest.raises(UnsetPremiseError):
            board.read_premise("window")

    def test_read_board_returns_every_change_in_sequence_order(self) -> None:
        board = make_board()
        board.append("application", "a")
        board.set("window", "w", expected_version=0)
        board.append("platform", "p")
        assert changes(board.read_board()) == [
            (1, "application", "a"),
            (2, "window", "w"),
            (3, "platform", "p"),
        ]

    def test_read_board_from_sequence_is_inclusive(self) -> None:
        board = make_board()
        board.append("application", "a")
        board.append("platform", "p")
        assert changes(board.read_board(from_sequence=2)) == [(2, "platform", "p")]

    def test_read_results_are_snapshots_of_internal_state(self) -> None:
        board = make_board()
        board.append("application", "a")
        board.read_level("application").clear()
        board.read_board().clear()
        assert entries(board.read_level("application")) == [(1, "a")]
        assert changes(board.read_board()) == [(1, "application", "a")]


class TestDeclarations:
    def test_a_region_may_be_declared_after_creation(self) -> None:
        board = make_board()
        board.declare(Level("change"))
        assert board.append("change", "c") == 1

    def test_a_level_declared_later_starts_empty(self) -> None:
        board = make_board()
        board.append("application", "a")
        board.declare(Level("change"))
        assert board.read_level("change") == []

    def test_a_level_declared_later_takes_the_next_sequence_numbers(self) -> None:
        board = make_board()
        board.append("application", "a")
        board.declare(Level("change"))
        assert board.append("change", "c") == 2

    def test_a_premise_declared_later_has_no_value_until_written(self) -> None:
        board = make_board()
        board.append("application", "a")
        board.declare(Premise("trigger"))
        with pytest.raises(UnsetPremiseError):
            board.read_premise("trigger")
        assert board.set("trigger", "alert", expected_version=0) == Written(
            sequence=2, version=1
        )

    def test_redeclaring_a_name_with_the_other_kind_is_refused(self) -> None:
        board = make_board()
        with pytest.raises(DuplicateRegionError):
            board.declare(Premise("application"))

    def test_redeclaring_a_name_with_the_same_kind_is_refused(self) -> None:
        board = make_board()
        board.append("application", "a")
        with pytest.raises(DuplicateRegionError):
            board.declare(Level("application"))
        assert entries(board.read_level("application")) == [(1, "a")]
        board.set("window", "w", expected_version=0)
        with pytest.raises(DuplicateRegionError):
            board.declare(Premise("window"))
        assert state(board.read_premise("window")) == ("w", 1)

    def test_a_declaration_of_neither_kind_is_refused(self) -> None:
        board = make_board()
        with pytest.raises(TypeError):
            board.declare("change")  # type: ignore[arg-type]  # the refusal of an untyped caller's object is the behavior under test


class TestTheRecordAnswersWhoAndWhen:
    """A contribution names its writer and the instant the store stamped."""

    def test_the_control_component_records_the_writer_it_was_given(self) -> None:
        model = create_model(
            board_id="b",
            store=InMemoryStore(),
            regions=[Level("findings"), Premise("severity")],
            premises={"severity": "unknown"},
            limits=RunLimits(
                wall_clock=timedelta(minutes=5), idle=timedelta(minutes=5)
            ),
        )
        model.control.write("findings", "oom", writer="triage")
        model.control.set_premise("severity", "high", 1, writer="operator")

        (finding,) = model.reader.read_level("findings")
        assert finding.writer == "triage"
        assert finding.written_at is not None
        assert model.reader.read_premise("severity").writer == "operator"

    def test_an_opening_premise_value_names_no_writer(self) -> None:
        """The application supplied it, and no agent wrote it."""
        model = create_model(
            board_id="b",
            store=InMemoryStore(),
            regions=[Premise("severity")],
            premises={"severity": "unknown"},
            limits=RunLimits(
                wall_clock=timedelta(minutes=5), idle=timedelta(minutes=5)
            ),
        )
        assert model.reader.read_premise("severity").writer is None

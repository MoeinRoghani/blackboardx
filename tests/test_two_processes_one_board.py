"""Two control components over one board, the way two replicas hold one.

Each `Control` here stands for a process. They share a `BoardStore` and a
`RunStore` and hold nothing else in common, which is what a deployment behind
one address across two replicas has.

The board store already worked this way, and says so: its sequence and its
premise version guard hold across processes. These are about the run.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blackboard import (
    Aborted,
    Agent,
    Control,
    InMemoryRunStore,
    InMemoryStore,
    Level,
    ManualClock,
    Notification,
    Premise,
    Rejected,
    RunLimits,
    Settled,
    UnknownNotificationError,
    attach_model,
    create_model,
    sweep,
)

LIMITS = RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(minutes=3))
REGIONS: list[Level | Premise] = [Level("findings"), Premise("appcode")]
# Named rather than left to the default, which is every premise and no level.
# These tests are about what a write to a level does across two processes.
SUBSCRIBED = frozenset({"findings", "appcode"})


class Woken:
    """An agent that records what it was told, in one process."""

    def __init__(self) -> None:
        self.received: list[Notification] = []

    def __call__(self, notification: Notification) -> None:
        self.received.append(notification)

    @property
    def identifiers(self) -> list[int]:
        return [int(n.notification_id) for n in self.received]


@pytest.fixture
def shared() -> tuple[InMemoryStore, InMemoryRunStore]:
    return InMemoryStore(), InMemoryRunStore()


def open_first(
    shared: tuple[InMemoryStore, InMemoryRunStore],
    notify: Woken,
    clock: ManualClock | None = None,
) -> Control:
    store, runs = shared
    return create_model(
        board_id="incident-1",
        store=store,
        run_store=runs,
        regions=REGIONS,
        premises={"appcode": "CHECKOUT"},
        agents=[Agent(name="ocp", notify=notify, subscribes_to=SUBSCRIBED)],
        limits=LIMITS,
        clock=clock,
    ).control


def open_second(
    shared: tuple[InMemoryStore, InMemoryRunStore],
    notify: Woken,
    clock: ManualClock | None = None,
) -> Control:
    store, runs = shared
    return attach_model(
        board_id="incident-1",
        store=store,
        run_store=runs,
        regions=REGIONS,
        agents=[Agent(name="ocp", notify=notify, subscribes_to=SUBSCRIBED)],
        limits=LIMITS,
        clock=clock,
    ).control


class TestTheSecondProcessJoinsTheRunRatherThanStartingOne:
    def test_a_notification_takes_a_number_the_first_did_not_use(
        self, shared: tuple[InMemoryStore, InMemoryRunStore]
    ) -> None:
        """Identifiers count notifications on the board, not in the process.

        Two processes numbering from one each would hand an agent the same
        identifier for two different ranges, and an acknowledgment would then
        close the wrong one.
        """
        here, there = Woken(), Woken()
        first = open_first(shared, here)
        second = open_second(shared, there)

        first.write("findings", {"from": "the first"}, writer="git")
        second.write("findings", {"from": "the second"}, writer="dynatrace")

        issued = here.identifiers + there.identifiers
        assert len(set(issued)) == len(issued), (
            f"two notifications shared a number: {issued}"
        )

    def test_an_agent_acknowledges_to_either_process(
        self, shared: tuple[InMemoryStore, InMemoryRunStore]
    ) -> None:
        """The agent has one address and reaches whichever replica answers.

        An acknowledgment sent to the process that did not issue the
        notification was refused as unknown, and the run then named an agent
        unfinished for work it had finished.
        """
        here, there = Woken(), Woken()
        first = open_first(shared, here)
        second = open_second(shared, there)

        first.write("findings", {"cause": "a bad deploy"}, writer="git")
        assert here.received, "the first process did not wake the agent"
        answered = here.received[-1].notification_id

        second.ack(answered, agent="ocp")

        assert first.outcome() is None
        assert second.outcome() is None
        # Nothing is owed, in either process, because both read one store.
        second.abort("done")
        outcome = second.outcome()
        assert outcome is not None
        assert outcome.unfinished == frozenset()

    def test_a_notification_the_other_process_issued_is_unfinished_here(
        self, shared: tuple[InMemoryStore, InMemoryRunStore]
    ) -> None:
        here, there = Woken(), Woken()
        first = open_first(shared, here)
        second = open_second(shared, there)

        first.write("findings", {"cause": "a bad deploy"}, writer="git")
        second.abort("done")

        outcome = second.outcome()
        assert outcome is not None
        assert outcome.unfinished == frozenset({"ocp"})

    def test_an_acknowledgment_naming_nothing_is_refused_by_either(
        self, shared: tuple[InMemoryStore, InMemoryRunStore]
    ) -> None:
        open_first(shared, Woken())
        second = open_second(shared, Woken())
        with pytest.raises(UnknownNotificationError):
            second.ack(4098, agent="ocp")

    def test_the_agent_keeps_one_cursor_across_both(
        self, shared: tuple[InMemoryStore, InMemoryRunStore]
    ) -> None:
        """A cursor in each process would re-read what the agent had read."""
        here, there = Woken(), Woken()
        first = open_first(shared, here)
        second = open_second(shared, there)

        first.write("findings", {"one": 1}, writer="git")
        first.ack(here.received[-1].notification_id, agent="ocp")
        second.write("findings", {"two": 2}, writer="git")

        assert there.received, "the second process did not wake the agent"
        covered = there.received[-1]
        assert covered.from_sequence > 1, (
            "the second process re-issued a range the agent had acknowledged"
        )


class TestARunEndsOnce:
    def test_the_second_process_reports_the_outcome_the_first_recorded(
        self, shared: tuple[InMemoryStore, InMemoryRunStore]
    ) -> None:
        first = open_first(shared, Woken())
        second = open_second(shared, Woken())

        first.abort("the first process closed it")

        # The second learns by asking, because nothing told it.
        refused = second.write("findings", {"late": True}, writer="git")
        assert isinstance(refused, Rejected)
        outcome = second.outcome()
        assert isinstance(outcome, Aborted)
        assert outcome.reason == "the first process closed it"

    def test_a_write_is_refused_after_the_other_process_closed_the_run(
        self, shared: tuple[InMemoryStore, InMemoryRunStore]
    ) -> None:
        """A board must not gain a contribution after its run ended."""
        store, _ = shared
        first = open_first(shared, Woken())
        second = open_second(shared, Woken())
        first.abort("done")

        second.write("findings", {"late": True}, writer="git")

        assert [c.content for c in store.read_level("incident-1", "findings")] == []


class TestASweepClosesWhatNoProcessHolds:
    def test_a_run_past_its_idle_deadline_is_settled_by_the_sweep(
        self, shared: tuple[InMemoryStore, InMemoryRunStore]
    ) -> None:
        """The process holding it stopped, so nothing is going to add to it."""
        _, runs = shared
        open_first(shared, Woken())
        later = datetime.now(UTC) + timedelta(hours=2)

        assert sweep(runs, now=later) == ["incident-1"]

        closure = runs.closed_as("incident-1")
        assert closure is not None
        assert closure.outcome == "Settled"

    def test_a_sweep_closes_a_run_once(
        self, shared: tuple[InMemoryStore, InMemoryRunStore]
    ) -> None:
        _, runs = shared
        open_first(shared, Woken())
        later = datetime.now(UTC) + timedelta(hours=2)
        assert sweep(runs, now=later) == ["incident-1"]
        assert sweep(runs, now=later) == []

    def test_a_sweep_names_the_agents_that_never_answered(
        self, shared: tuple[InMemoryStore, InMemoryRunStore]
    ) -> None:
        _, runs = shared
        first = open_first(shared, Woken())
        first.write("findings", {"cause": "a bad deploy"}, writer="git")

        sweep(runs, now=datetime.now(UTC) + timedelta(hours=2))

        closure = runs.closed_as("incident-1")
        assert closure is not None
        assert closure.unfinished == frozenset({"ocp"})

    def test_a_process_holding_the_run_closes_it_before_a_sweep_can(
        self, shared: tuple[InMemoryStore, InMemoryRunStore]
    ) -> None:
        # The timer is prompt and applies the application's termination
        # predicate; the sweep is for a run no process holds.
        _, runs = shared
        clock = ManualClock(datetime(2026, 1, 1, tzinfo=UTC))
        first = open_first(shared, Woken(), clock)
        clock.advance(timedelta(minutes=4))

        assert isinstance(first.outcome(), Settled)
        assert sweep(runs, now=datetime(2026, 1, 2, tzinfo=UTC)) == []


class TestAnIdleTimerAsksTheStoreBeforeClosing:
    def test_a_process_seeing_no_traffic_does_not_close_a_busy_run(
        self, shared: tuple[InMemoryStore, InMemoryRunStore]
    ) -> None:
        """The other process is working; this one has heard nothing.

        A write pushes the idle deadline in the store, and the process that
        did not take it has a timer armed from whenever it last saw
        something. Left to fire, that timer closes a run another process is
        working, and the store cannot tell the difference because the close
        is conditional on the run being open and it is.
        """
        here, there = Woken(), Woken()
        quiet = ManualClock(datetime(2026, 1, 1, tzinfo=UTC))
        busy = ManualClock(datetime(2026, 1, 1, tzinfo=UTC))
        first = open_first(shared, here, busy)
        second = open_second(shared, there, quiet)

        # The busy process takes a write two minutes in, which pushes the
        # deadline the store holds out to five minutes.
        busy.advance(timedelta(minutes=2))
        first.write("findings", {"cause": "a bad deploy"}, writer="git")

        # The quiet process reaches its own three-minute deadline first.
        quiet.advance(timedelta(minutes=3, seconds=1))

        assert second.outcome() is None, "a quiet process closed a busy run"
        assert first.outcome() is None

    def test_the_quiet_process_closes_the_run_once_the_store_agrees(
        self, shared: tuple[InMemoryStore, InMemoryRunStore]
    ) -> None:
        quiet = ManualClock(datetime(2026, 1, 1, tzinfo=UTC))
        busy = ManualClock(datetime(2026, 1, 1, tzinfo=UTC))
        first = open_first(shared, Woken(), busy)
        second = open_second(shared, Woken(), quiet)
        busy.advance(timedelta(minutes=2))
        first.write("findings", {"cause": "a bad deploy"}, writer="git")

        quiet.advance(timedelta(minutes=3, seconds=1))
        assert second.outcome() is None
        # Past the deadline the write pushed the store's to.
        quiet.advance(timedelta(minutes=2, seconds=1))

        assert isinstance(second.outcome(), Settled)

"""A run closes on silence, on the wall clock, or because a caller closed it."""

from datetime import UTC, datetime, timedelta

import pytest

from blackboard import (
    Aborted,
    Agent,
    BoardReader,
    InMemoryStore,
    Level,
    ManualClock,
    Notification,
    Premise,
    Rejected,
    RejectionCause,
    RunClosed,
    RunClosedError,
    RunLimits,
    Settled,
    TerminationDecision,
    WallClockExpired,
)
from blackboard._control import Control

START = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
IDLE = timedelta(minutes=10)


def limits(
    wall_clock: timedelta = timedelta(hours=1), idle: timedelta = IDLE
) -> RunLimits:
    return RunLimits(wall_clock=wall_clock, idle=idle)


def keep_open(reader: BoardReader) -> TerminationDecision:
    return TerminationDecision.CONTINUE


class Recorder:
    def __init__(self) -> None:
        self.received: list[Notification] = []

    def __call__(self, notification: Notification) -> None:
        self.received.append(notification)


def declaration(name: str, notify: object) -> Agent:
    return Agent(
        name=name,
        notify=notify,  # type: ignore[arg-type]  # the callers pass a recorder
    )


def make_control(clock: ManualClock, **kwargs: object) -> Control:
    return Control(
        regions=[Level("application"), Premise("window")],
        limits=limits(),
        clock=clock,
        board_id="test-board",
        store=InMemoryStore(),
        **kwargs,  # type: ignore[arg-type]  # forwarded keyword arguments
    )


class TestLimits:
    def test_both_limits_are_positive(self) -> None:
        with pytest.raises(ValueError, match="wall clock"):
            limits(wall_clock=timedelta(0))
        with pytest.raises(ValueError, match="idle"):
            limits(idle=timedelta(0))


class TestSilence:
    def test_a_quiet_instant_does_not_close_the_run(self) -> None:
        clock = ManualClock(start=START)
        control = make_control(clock)
        control.write("application", "a finding", writer="ocp")
        assert control.outcome() is None

    def test_sustained_silence_closes_the_run(self) -> None:
        clock = ManualClock(start=START)
        control = make_control(clock)
        control.write("application", "a finding", writer="ocp")
        clock.advance(IDLE)
        assert control.outcome() == Settled()

    def test_every_event_pushes_the_deadline_out(self) -> None:
        clock = ManualClock(start=START)
        control = make_control(clock)
        control.write("application", "one", writer="ocp")
        clock.advance(IDLE - timedelta(minutes=1))
        control.write("application", "two", writer="ocp")
        clock.advance(IDLE - timedelta(minutes=1))
        assert control.outcome() is None
        clock.advance(timedelta(minutes=1))
        assert control.outcome() == Settled()

    def test_an_agent_holding_a_notification_is_named_unfinished(self) -> None:
        clock = ManualClock(start=START)
        recorder = Recorder()
        control = make_control(clock)
        control.set_premise("window", "w", expected_version=0, writer="operator")
        control.register_agent(declaration("ocp", recorder))
        # The agent never acknowledges. Nothing but the idle limit is
        # measuring anything, so that alone closes the run.
        clock.advance(IDLE)
        assert control.outcome() == Settled(unfinished=frozenset({"ocp"}))

    def test_an_agent_that_acknowledged_is_not_named(self) -> None:
        clock = ManualClock(start=START)
        holder: list[Control] = []

        def ack_at_once(notification: Notification) -> None:
            holder[0].ack(notification.notification_id, agent="ocp")

        control = make_control(clock)
        holder.append(control)
        control.set_premise("window", "w", expected_version=0, writer="operator")
        control.register_agent(declaration("ocp", ack_at_once))
        clock.advance(IDLE)
        assert control.outcome() == Settled()


class TestTerminationPredicate:
    def test_continue_holds_the_run_open_past_the_idle_limit(self) -> None:
        clock = ManualClock(start=START)
        control = make_control(clock, termination_predicate=keep_open)
        control.write("application", "one", writer="ocp")
        clock.advance(IDLE * 3)
        assert control.outcome() is None

    def test_complete_lets_the_run_close(self) -> None:
        clock = ManualClock(start=START)

        def close_when_nonempty(reader: BoardReader) -> TerminationDecision:
            if reader.read_level("application"):
                return TerminationDecision.COMPLETE
            return TerminationDecision.CONTINUE

        control = make_control(clock, termination_predicate=close_when_nonempty)
        clock.advance(IDLE)
        assert control.outcome() is None
        control.write("application", "one", writer="ocp")
        clock.advance(IDLE)
        assert control.outcome() == Settled()


class TestWallClock:
    def test_the_wall_clock_closes_a_run_held_open(self) -> None:
        clock = ManualClock(start=START)
        control = make_control(clock, termination_predicate=keep_open)
        control.write("application", "one", writer="ocp")
        clock.advance(timedelta(hours=1))
        assert control.outcome() == WallClockExpired()

    def test_it_names_the_agents_that_did_not_finish(self) -> None:
        clock = ManualClock(start=START)
        recorder = Recorder()
        control = make_control(clock, termination_predicate=keep_open)
        control.set_premise("window", "w", expected_version=0, writer="operator")
        control.register_agent(declaration("ocp", recorder))
        clock.advance(timedelta(hours=1))
        assert control.outcome() == WallClockExpired(unfinished=frozenset({"ocp"}))


class TestAbortAndAfterClose:
    def test_abort_closes_the_run_and_keeps_its_reason(self) -> None:
        clock = ManualClock(start=START)
        control = make_control(clock)
        control.abort("the operator stopped the run")
        assert control.outcome() == Aborted(reason="the operator stopped the run")
        control.abort("a second reason")
        assert control.outcome() == Aborted(reason="the operator stopped the run")

    def test_a_closed_run_refuses_writes_and_registration_but_allows_reads(
        self,
    ) -> None:
        clock = ManualClock(start=START)
        control = make_control(clock)
        control.set_premise("window", "w", expected_version=0, writer="operator")
        control.abort("stopped")
        assert control.write("application", "late", writer="ocp") == Rejected(
            cause=RejectionCause.RUN_CLOSED, reason="the run has closed"
        )
        with pytest.raises(RunClosedError):
            control.declare(Level("change"))
        with pytest.raises(RunClosedError):
            control.register_agent(declaration("late", Recorder()))
        assert control.reader.read_premise("window").value == "w"
        assert any(isinstance(e, RunClosed) for e in control.read_audit())

    def test_the_idle_timer_does_not_fire_after_close(self) -> None:
        clock = ManualClock(start=START)
        control = make_control(clock)
        control.write("application", "one", writer="ocp")
        control.abort("stopped")
        clock.advance(IDLE * 2)
        assert control.outcome() == Aborted(reason="stopped")

    def test_wait_closed_returns_the_outcome(self) -> None:
        clock = ManualClock(start=START)
        control = make_control(clock)
        assert control.wait_closed(timeout=timedelta(0)) is None
        control.abort("stopped")
        assert control.wait_closed() == Aborted(reason="stopped")

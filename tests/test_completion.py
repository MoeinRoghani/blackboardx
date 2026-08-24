"""The run closes in exactly one of four states, and the audit records which."""

from datetime import UTC, datetime, timedelta

import pytest

from blackboard import (
    Aborted,
    Accept,
    Agent,
    BoardReader,
    BudgetExhausted,
    BudgetKind,
    BudgetReached,
    Complete,
    FinishedWithFailures,
    Level,
    ManualClock,
    Notification,
    NotificationDispatched,
    Register,
    Reject,
    Rejected,
    RejectionCause,
    RunBudgets,
    RunClosed,
    RunClosedError,
    TerminationDecision,
    Written,
)
from blackboard._control import Control

START = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
DEADLINE = timedelta(minutes=5)


def budgets(
    wall_clock: timedelta = timedelta(hours=1),
    total_writes: int = 1000,
    total_notifications: int = 1000,
) -> RunBudgets:
    return RunBudgets(
        wall_clock=wall_clock,
        total_writes=total_writes,
        total_notifications=total_notifications,
    )


def keep_open(reader: BoardReader) -> TerminationDecision:
    return TerminationDecision.CONTINUE


def begin(control: Control, name: str = "worker") -> None:
    """Registers an agent that acknowledges its opening wake immediately.

    A run into which no agent ever registered has not begun, so completion
    is only meaningful once one has.
    """

    def ack_at_once(notification: Notification) -> None:
        control.ack(name, notification.notification_id)

    control.register_agent(
        Agent(
            name=name,
            acknowledgment_deadline=DEADLINE,
            wake_cap=1000,
            notify=ack_at_once,
        )
    )


class Recorder:
    def __init__(self) -> None:
        self.received: list[Notification] = []

    def __call__(self, notification: Notification) -> None:
        self.received.append(notification)


class TestQuiescence:
    def test_an_unacknowledged_notification_holds_the_run_open(self) -> None:
        clock = ManualClock(start=START)
        recorder = Recorder()
        control = Control(regions=[Register("window")], budgets=budgets(), clock=clock)
        control.register_agent(
            Agent(
                name="ocp",
                acknowledgment_deadline=DEADLINE,
                wake_cap=10,
                notify=recorder,
            )
        )
        control.set_register("operator", "window", "w", expected_version=0)
        assert control.outcome() is None
        clock.advance(DEADLINE)
        assert control.outcome() == FinishedWithFailures(
            presumed_failed=frozenset({"ocp"}), capped=frozenset()
        )

    def test_a_post_close_write_is_rejected_with_run_closed(self) -> None:
        clock = ManualClock(start=START)
        control = Control(
            regions=[Level("application"), Register("window")],
            budgets=budgets(),
            clock=clock,
        )
        control.abort("the operator stopped the run")
        result = control.write("ocp", "application", "late")
        assert result == Rejected(
            cause=RejectionCause.RUN_CLOSED, reason="the run has closed"
        )

    def test_the_run_closes_complete_when_every_agent_acknowledged(self) -> None:
        clock = ManualClock(start=START)
        holder: list[Control] = []
        received: list[Notification] = []

        def ack_inline(notification: Notification) -> None:
            received.append(notification)
            holder[0].ack("ocp", notification.notification_id)

        control = Control(regions=[Register("window")], budgets=budgets(), clock=clock)
        holder.append(control)
        control.register_agent(
            Agent(
                name="ocp",
                acknowledgment_deadline=DEADLINE,
                wake_cap=10,
                notify=ack_inline,
            )
        )
        control.set_register("operator", "window", "w", expected_version=0)
        assert control.outcome() == Complete()
        closing = control.read_audit()[-1]
        assert isinstance(closing, RunClosed)
        assert closing.outcome == Complete()

    def test_a_capped_agent_closes_the_run_finished_with_failures(self) -> None:
        clock = ManualClock(start=START)
        holder: list[Control] = []

        def ack_inline(notification: Notification) -> None:
            holder[0].ack("ocp", notification.notification_id)

        control = Control(regions=[Register("window")], budgets=budgets(), clock=clock)
        holder.append(control)
        control.register_agent(
            Agent(
                name="ocp",
                acknowledgment_deadline=DEADLINE,
                wake_cap=1,
                notify=ack_inline,
            )
        )
        control.set_register("operator", "window", "w", expected_version=0)
        assert control.outcome() == FinishedWithFailures(
            presumed_failed=frozenset(), capped=frozenset({"ocp"})
        )


class TestTerminationPredicate:
    def test_continue_holds_the_run_open(self) -> None:
        clock = ManualClock(start=START)
        control = Control(
            regions=[Level("application")],
            termination_predicate=keep_open,
            budgets=budgets(),
            clock=clock,
        )
        control.write("ocp", "application", "one")
        assert control.outcome() is None

    def test_complete_closes_the_run(self) -> None:
        clock = ManualClock(start=START)

        def close_when_nonempty(reader: BoardReader) -> TerminationDecision:
            if reader.read_level("application"):
                return TerminationDecision.COMPLETE
            return TerminationDecision.CONTINUE

        control = Control(
            regions=[Level("application")],
            termination_predicate=close_when_nonempty,
            budgets=budgets(),
            clock=clock,
        )
        begin(control)
        control.write("ocp", "application", "one")
        assert control.outcome() == Complete()

    def test_a_stale_complete_verdict_is_discarded(self) -> None:
        clock = ManualClock(start=START)
        holder: list[Control] = []
        calls: list[int] = []

        def predicate(reader: BoardReader) -> TerminationDecision:
            calls.append(len(calls))
            if len(calls) == 1:
                holder[0].write("noise", "application", "moved the board")
                return TerminationDecision.COMPLETE
            return TerminationDecision.CONTINUE

        control = Control(
            regions=[Level("application")],
            termination_predicate=predicate,
            budgets=budgets(),
            clock=clock,
        )
        holder.append(control)
        begin(control)
        control.write("ocp", "application", "one")
        assert control.outcome() is None
        assert len(calls) == 2


class TestBudgets:
    def test_budgets_are_positive(self) -> None:
        with pytest.raises(ValueError, match="wall clock"):
            budgets(wall_clock=timedelta(0))
        with pytest.raises(ValueError, match="total writes"):
            budgets(total_writes=0)
        with pytest.raises(ValueError, match="total notifications"):
            budgets(total_notifications=0)

    def test_the_write_that_would_exceed_the_budget_trips_it(self) -> None:
        clock = ManualClock(start=START)
        control = Control(
            regions=[Level("application")],
            termination_predicate=keep_open,
            budgets=budgets(total_writes=2),
            clock=clock,
        )
        control.write("ocp", "application", "one")
        control.write("ocp", "application", "two")
        third = control.write("ocp", "application", "three")
        assert third == Rejected(
            cause=RejectionCause.BUDGET_EXHAUSTED,
            reason="the total-writes budget is exhausted",
        )
        assert control.outcome() == BudgetExhausted(budget=BudgetKind.TOTAL_WRITES)
        reached = [e for e in control.read_audit() if isinstance(e, BudgetReached)]
        assert reached == [BudgetReached(at=START, budget=BudgetKind.TOTAL_WRITES)]
        fourth = control.write("ocp", "application", "four")
        assert fourth == Rejected(
            cause=RejectionCause.RUN_CLOSED, reason="the run has closed"
        )

    def test_an_exact_fit_run_closes_complete(self) -> None:
        clock = ManualClock(start=START)
        control = Control(
            regions=[Level("application")],
            budgets=budgets(total_writes=1),
            clock=clock,
        )
        begin(control)
        result = control.write("ocp", "application", "one")
        assert result.sequence == 1  # type: ignore[union-attr]  # an Accepted result carries the sequence
        assert control.outcome() == Complete()

    def test_the_dispatch_that_would_exceed_the_budget_is_withheld(self) -> None:
        clock = ManualClock(start=START)
        first, second = Recorder(), Recorder()
        control = Control(
            regions=[Register("window")],
            budgets=budgets(total_notifications=1),
            clock=clock,
        )
        for name, recorder in (("a", first), ("b", second)):
            control.register_agent(
                Agent(
                    name=name,
                    acknowledgment_deadline=DEADLINE,
                    wake_cap=10,
                    notify=recorder,
                )
            )
        result = control.set_register("operator", "window", "w", expected_version=0)
        assert isinstance(result, Written)
        assert len(first.received) == 1
        assert second.received == []
        assert control.outcome() == BudgetExhausted(
            budget=BudgetKind.TOTAL_NOTIFICATIONS
        )
        dispatched = [
            e for e in control.read_audit() if isinstance(e, NotificationDispatched)
        ]
        assert len(dispatched) == 1

    def test_wall_clock_expiry_closes_an_idle_run(self) -> None:
        clock = ManualClock(start=START)
        control = Control(
            regions=[Level("application")],
            termination_predicate=keep_open,
            budgets=budgets(wall_clock=timedelta(hours=1)),
            clock=clock,
        )
        control.write("ocp", "application", "one")
        assert control.outcome() is None
        clock.advance(timedelta(hours=1))
        assert control.outcome() == BudgetExhausted(budget=BudgetKind.WALL_CLOCK)


class TestAbortAndAfterClose:
    def test_abort_closes_the_run_and_the_model_stops_accepting(self) -> None:
        clock = ManualClock(start=START)
        recorder = Recorder()
        control = Control(
            regions=[Level("application"), Register("window")],
            termination_predicate=keep_open,
            budgets=budgets(),
            clock=clock,
        )
        control.register_agent(
            Agent(
                name="ocp",
                acknowledgment_deadline=DEADLINE,
                wake_cap=10,
                notify=recorder,
            )
        )
        control.set_register("operator", "window", "w", expected_version=0)
        control.abort("the operator stopped the run")
        assert control.outcome() == Aborted(reason="the operator stopped the run")

        control.abort("a second reason")
        assert control.outcome() == Aborted(reason="the operator stopped the run")

        notification_id = recorder.received[0].notification_id
        control.ack("ocp", notification_id)
        assert control.extend("ocp", notification_id) is None
        with pytest.raises(RunClosedError):
            control.declare(Level("change"))
        with pytest.raises(RunClosedError):
            control.register_agent(
                Agent(
                    name="late",
                    acknowledgment_deadline=DEADLINE,
                    wake_cap=1,
                    notify=recorder,
                )
            )
        assert control.reader.read_register("window").value == "w"
        assert isinstance(control.read_audit()[-1], RunClosed)

    def test_a_deadline_does_not_fire_after_close(self) -> None:
        clock = ManualClock(start=START)
        recorder = Recorder()
        control = Control(
            regions=[Register("window")],
            termination_predicate=keep_open,
            budgets=budgets(),
            clock=clock,
        )
        control.register_agent(
            Agent(
                name="ocp",
                acknowledgment_deadline=DEADLINE,
                wake_cap=10,
                notify=recorder,
            )
        )
        control.set_register("operator", "window", "w", expected_version=0)
        control.abort("stopped")
        clock.advance(DEADLINE * 2)
        assert control.outcome() == Aborted(reason="stopped")
        assert not any(
            event.__class__.__name__ == "PresumedFailed"
            for event in control.read_audit()
        )

    def test_wait_closed_returns_the_outcome(self) -> None:
        clock = ManualClock(start=START)
        control = Control(
            regions=[Level("application")],
            termination_predicate=keep_open,
            budgets=budgets(),
            clock=clock,
        )
        assert control.wait_closed(timeout=timedelta(0)) is None
        control.abort("stopped")
        assert control.wait_closed() == Aborted(reason="stopped")


class TestSequencingRaces:
    def test_a_write_admitted_while_the_run_closes_is_refused(self) -> None:
        clock = ManualClock(start=START)
        holder: list[Control] = []

        def abort_mid_admission(proposed: object, reader: object) -> Accept | Reject:
            holder[0].abort("closed mid-admission")
            return Accept()

        control = Control(
            regions=[Level("application")],
            admission_rule=abort_mid_admission,
            termination_predicate=keep_open,
            budgets=budgets(),
            clock=clock,
        )
        holder.append(control)
        result = control.write("ocp", "application", "one")
        assert result == Rejected(
            cause=RejectionCause.RUN_CLOSED, reason="the run has closed"
        )
        assert control.reader.read_level("application") == []

    def test_a_nested_write_cannot_overrun_the_write_budget(self) -> None:
        clock = ManualClock(start=START)
        holder: list[Control] = []
        nested: list[object] = []

        def rule(proposed: object, reader: BoardReader) -> Accept:
            if not nested:
                nested.append(True)
                holder[0].write("nested", "application", "first")
            return Accept()

        control = Control(
            regions=[Level("application")],
            admission_rule=rule,
            termination_predicate=keep_open,
            budgets=budgets(total_writes=1),
            clock=clock,
        )
        holder.append(control)
        outer = control.write("ocp", "application", "second")
        assert outer == Rejected(
            cause=RejectionCause.BUDGET_EXHAUSTED,
            reason="the total-writes budget is exhausted",
        )
        assert len(control.reader.read_level("application")) == 1
        assert control.outcome() == BudgetExhausted(budget=BudgetKind.TOTAL_WRITES)

    def test_a_rejected_write_still_triggers_the_completion_check(self) -> None:
        clock = ManualClock(start=START)
        holder: list[Control] = []
        received: list[Notification] = []

        def ack_then_reject(proposed: object, reader: BoardReader) -> Reject:
            holder[0].ack("ocp", received[0].notification_id)
            return Reject("nothing further enters")

        control = Control(
            regions=[Register("window")],
            admission_rule=None,
            budgets=budgets(),
            clock=clock,
        )
        holder.append(control)
        control.register_agent(
            Agent(
                name="ocp",
                acknowledgment_deadline=DEADLINE,
                wake_cap=10,
                notify=received.append,
            )
        )
        control.set_register("operator", "window", "w", expected_version=0)
        control._admission_rule = ack_then_reject
        result = control.set_register("late", "window", "x", expected_version=1)
        assert isinstance(result, Rejected)
        assert control.outcome() == Complete()

    def test_wait_closed_wakes_a_blocked_waiter(self) -> None:
        import threading

        clock = ManualClock(start=START)
        control = Control(
            regions=[Level("application")],
            termination_predicate=keep_open,
            budgets=budgets(),
            clock=clock,
        )
        seen: list[object] = []
        waiter = threading.Thread(target=lambda: seen.append(control.wait_closed()))
        waiter.start()
        control.abort("stopped")
        waiter.join(timeout=5)
        assert not waiter.is_alive()
        assert seen == [Aborted(reason="stopped")]

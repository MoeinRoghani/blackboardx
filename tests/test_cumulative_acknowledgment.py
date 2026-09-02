"""Acknowledging a range acknowledges every range it already covers.

The cursor an acknowledgment advances is cumulative, so acknowledging the
widest range answers the narrower ones inside it. Without that, an agent is
named unfinished for work it did, and a notification that never arrived holds
a run open until its idle limit.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from blackboard import (
    Agent,
    Control,
    InMemoryStore,
    Level,
    ManualClock,
    RunLimits,
    Settled,
    create_model,
)

LIMITS = RunLimits(wall_clock=timedelta(minutes=5), idle=timedelta(seconds=10))
IDLE = timedelta(seconds=11)


def a_run(notify: Any, clock: ManualClock) -> Control:
    model = create_model(
        board_id="b",
        store=InMemoryStore(),
        regions=[Level("signals")],
        premises={},
        agents=[
            Agent(name="triage", subscribes_to={"signals"}, notify=notify),
            Agent(name="src", notify=lambda n: None),
        ],
        limits=LIMITS,
        clock=clock,
    )
    return model.control


def test_acknowledging_the_newest_answers_the_ones_it_covers() -> None:
    seen: list[Any] = []
    clock = ManualClock()
    control = a_run(seen.append, clock)
    for n in range(3):
        control.write("signals", {"n": n}, writer="src")
    assert len(seen) == 3

    control.ack(seen[-1].notification_id, agent="triage")

    clock.advance(IDLE)
    outcome = control.outcome()
    assert isinstance(outcome, Settled)
    assert outcome.unfinished == frozenset()


def test_a_notification_that_never_arrived_does_not_hold_the_run_open() -> None:
    """The agent never saw it, so it can never acknowledge it by name."""
    seen: list[Any] = []
    clock = ManualClock()

    def refuse_the_first(notification: Any) -> None:
        if not seen:
            seen.append(notification)
            raise RuntimeError("the agent was restarting")
        seen.append(notification)

    control = a_run(refuse_the_first, clock)
    control.write("signals", {"n": 0}, writer="src")
    control.write("signals", {"n": 1}, writer="src")

    control.ack(seen[-1].notification_id, agent="triage")

    clock.advance(IDLE)
    outcome = control.outcome()
    assert isinstance(outcome, Settled)
    assert outcome.unfinished == frozenset()


def test_acknowledging_an_older_range_leaves_a_newer_one_outstanding() -> None:
    """Comparison is on the range, not on arrival, so nothing newer is dropped."""
    seen: list[Any] = []
    clock = ManualClock()
    control = a_run(seen.append, clock)
    control.write("signals", {"n": 0}, writer="src")
    control.write("signals", {"n": 1}, writer="src")

    control.ack(seen[0].notification_id, agent="triage")

    clock.advance(IDLE)
    outcome = control.outcome()
    assert isinstance(outcome, Settled)
    assert outcome.unfinished == frozenset({"triage"})


def test_acknowledging_twice_still_changes_nothing() -> None:
    seen: list[Any] = []
    clock = ManualClock()
    control = a_run(seen.append, clock)
    control.write("signals", {"n": 0}, writer="src")
    control.ack(seen[-1].notification_id, agent="triage")
    control.ack(seen[-1].notification_id, agent="triage")
    clock.advance(IDLE)
    assert control.outcome() == Settled(unfinished=frozenset())

"""A level carries a batch window, so a burst costs one wake rather than many."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from blackboard import (
    Agent,
    InMemoryStore,
    Level,
    ManualClock,
    Premise,
    RunLimits,
    create_model,
)

LIMITS = RunLimits(wall_clock=timedelta(minutes=10), idle=timedelta(minutes=10))
WINDOW = timedelta(seconds=5)


def a_run(window: timedelta, clock: ManualClock) -> tuple[Any, list[Any]]:
    seen: list[Any] = []
    model = create_model(
        board_id="b",
        store=InMemoryStore(),
        regions=[Level("findings", batch_window=window)],
        premises={},
        agents=[
            Agent(name="triage", subscribes_to={"findings"}, notify=seen.append),
            Agent(name="src", notify=lambda n: None),
        ],
        limits=LIMITS,
        clock=clock,
    )
    return model, seen


def test_a_burst_inside_the_window_is_one_notification() -> None:
    clock = ManualClock()
    model, seen = a_run(WINDOW, clock)
    before = len(seen)
    for n in range(10):
        model.control.write("findings", {"n": n}, writer="src")
    assert len(seen) == before
    clock.advance(WINDOW + timedelta(seconds=1))
    assert len(seen) == before + 1


def test_the_one_notification_covers_the_whole_burst() -> None:
    clock = ManualClock()
    model, seen = a_run(WINDOW, clock)
    for n in range(4):
        model.control.write("findings", {"n": n}, writer="src")
    clock.advance(WINDOW + timedelta(seconds=1))
    last = seen[-1]
    assert last.to_sequence == 4
    assert "findings" in last.regions


def test_a_level_with_no_window_still_dispatches_inline() -> None:
    clock = ManualClock()
    model, seen = a_run(timedelta(0), clock)
    before = len(seen)
    for n in range(3):
        model.control.write("findings", {"n": n}, writer="src")
    assert len(seen) == before + 3


def test_a_negative_window_is_refused() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        Level("findings", batch_window=timedelta(seconds=-1))


def test_a_level_is_still_hashable_and_compares_by_value() -> None:
    assert Level("f") == Level("f")
    assert len({Level("f"), Level("f")}) == 1
    assert Level("f") != Level("f", batch_window=WINDOW)


def test_registering_still_wakes_an_agent_at_once() -> None:
    """Registration is a catch-up on what is there, not a burst to damp."""
    clock = ManualClock()
    model, _ = a_run(WINDOW, clock)
    model.control.write("findings", {"n": 1}, writer="src")
    clock.advance(WINDOW + timedelta(seconds=1))
    seen: list[Any] = []
    model.control.register_agent(
        Agent(name="late", subscribes_to={"findings"}, notify=seen.append)
    )
    assert len(seen) == 1


def test_a_premise_window_still_works() -> None:
    clock = ManualClock()
    seen: list[Any] = []
    model = create_model(
        board_id="b",
        store=InMemoryStore(),
        regions=[Premise("severity", batch_window=WINDOW)],
        premises={"severity": "unknown"},
        agents=[
            Agent(name="triage", subscribes_to={"severity"}, notify=seen.append),
            Agent(name="src", notify=lambda n: None),
        ],
        limits=LIMITS,
        clock=clock,
    )
    before = len(seen)
    version = model.reader.read_premise("severity").version
    for n in range(3):
        model.control.set_premise("severity", n, version + n, writer="src")
    assert len(seen) == before
    clock.advance(WINDOW + timedelta(seconds=1))
    assert len(seen) == before + 1

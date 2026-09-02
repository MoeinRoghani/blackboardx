"""A level write notifications the agents that subscribed to that level."""

from datetime import UTC, datetime, timedelta

from blackboard import (
    Agent,
    BoardReader,
    InMemoryStore,
    Level,
    ManualClock,
    Notification,
    Premise,
    RunLimits,
    TerminationDecision,
)
from blackboard._control import Control

START = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
LIMITS = RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(minutes=30))


def keep_open(reader: BoardReader) -> TerminationDecision:
    return TerminationDecision.CONTINUE


def make_control(clock: ManualClock) -> Control:
    return Control(
        regions=[Level("platform"), Level("application"), Premise("window")],
        termination_predicate=keep_open,
        limits=LIMITS,
        clock=clock,
        board_id="test-board",
        store=InMemoryStore(),
    )


def declaration(
    name: str,
    received: list[Notification],
    subscribes_to: list[str] | None = None,
) -> Agent:
    return Agent(
        name=name,
        notify=received.append,
        subscribes_to=subscribes_to,
    )


def test_a_subscriber_is_woken_by_a_write_to_that_level() -> None:
    clock = ManualClock(start=START)
    control = make_control(clock)
    received: list[Notification] = []
    control.register_agent(declaration("git", received, subscribes_to=["platform"]))
    control.write("ocp", "platform", "a finding")
    (notification,) = received
    assert notification.regions == frozenset({"platform"})


def test_the_writer_is_not_woken_by_its_own_contribution() -> None:
    clock = ManualClock(start=START)
    control = make_control(clock)
    writer: list[Notification] = []
    control.register_agent(declaration("ocp", writer, subscribes_to=["platform"]))
    control.write("ocp", "platform", "a finding")
    assert writer == []


def test_an_agent_not_subscribed_to_that_level_is_unaffected() -> None:
    clock = ManualClock(start=START)
    control = make_control(clock)
    received: list[Notification] = []
    control.register_agent(declaration("git", received, subscribes_to=["application"]))
    control.write("ocp", "platform", "a finding")
    assert received == []


def test_omitting_the_declaration_subscribes_to_no_level() -> None:
    clock = ManualClock(start=START)
    control = make_control(clock)
    received: list[Notification] = []
    control.register_agent(declaration("git", received))
    control.write("ocp", "platform", "a finding")
    assert received == []


def test_registering_notifies_for_a_level_that_already_holds_contributions() -> None:
    clock = ManualClock(start=START)
    control = make_control(clock)
    control.write("ocp", "platform", "written before anyone registered")
    received: list[Notification] = []
    control.register_agent(declaration("late", received, subscribes_to=["platform"]))
    (notification,) = received
    assert notification.regions == frozenset({"platform"})


def test_an_empty_level_is_not_named_in_the_opening_notification() -> None:
    clock = ManualClock(start=START)
    control = make_control(clock)
    received: list[Notification] = []
    control.register_agent(
        declaration("late", received, subscribes_to=["platform", "window"])
    )
    assert received == []

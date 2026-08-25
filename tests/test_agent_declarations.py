"""An agent declares which regions notification it and which levels it may write."""

from datetime import UTC, datetime, timedelta

import pytest

from blackboard import (
    Agent,
    BoardReader,
    InMemoryBoard,
    Level,
    ManualClock,
    Notification,
    Premise,
    Rejected,
    RejectionCause,
    RunLimits,
    TerminationDecision,
    UndeclaredRegionError,
    Written,
)
from blackboard._control import Control

START = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
LIMITS = RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(minutes=30))


def keep_open(reader: BoardReader) -> TerminationDecision:
    return TerminationDecision.CONTINUE


def make_control(clock: ManualClock) -> Control:
    return Control(
        regions=[
            Level("platform"),
            Level("application"),
            Premise("window"),
            Premise("namespace"),
        ],
        termination_predicate=keep_open,
        limits=LIMITS,
        clock=clock,
        board=InMemoryBoard(),
    )


def declaration(
    name: str,
    received: list[Notification],
    subscribes_to: list[str] | None = None,
    writes_to: list[str] | None = None,
) -> Agent:
    return Agent(
        name=name,
        notify=received.append,
        subscribes_to=subscribes_to,
        writes_to=writes_to,
    )


class TestSubscription:
    def test_omitting_it_subscribes_to_every_register(self) -> None:
        clock = ManualClock(start=START)
        control = make_control(clock)
        received: list[Notification] = []
        control.register_agent(declaration("ocp", received))
        control.set_premise("operator", "window", "w", expected_version=0)
        control.set_premise("operator", "namespace", ("ns",), expected_version=0)
        assert [n.regions for n in received] == [
            frozenset({"window"}),
            frozenset({"namespace"}),
        ]

    def test_a_register_outside_the_declaration_does_not_wake_the_agent(self) -> None:
        clock = ManualClock(start=START)
        control = make_control(clock)
        received: list[Notification] = []
        control.register_agent(declaration("ocp", received, subscribes_to=["window"]))
        control.set_premise("operator", "namespace", ("ns",), expected_version=0)
        assert received == []
        control.set_premise("operator", "window", "w", expected_version=0)
        assert [n.regions for n in received] == [frozenset({"window"})]

    def test_the_opening_notification_names_only_subscribed_registers(self) -> None:
        clock = ManualClock(start=START)
        control = make_control(clock)
        control.set_premise("operator", "window", "w", expected_version=0)
        control.set_premise("operator", "namespace", ("ns",), expected_version=0)
        received: list[Notification] = []
        control.register_agent(declaration("late", received, subscribes_to=["window"]))
        (notification,) = received
        assert notification.regions == frozenset({"window"})


class TestWritePermission:
    def test_omitting_it_permits_every_level(self) -> None:
        clock = ManualClock(start=START)
        control = make_control(clock)
        control.register_agent(declaration("ocp", []))
        assert control.write("ocp", "platform", "a") == Written(sequence=1)
        assert control.write("ocp", "application", "b") == Written(sequence=2)

    def test_a_level_outside_the_declaration_is_refused(self) -> None:
        clock = ManualClock(start=START)
        control = make_control(clock)
        control.register_agent(declaration("ocp", [], writes_to=["platform"]))
        assert control.write("ocp", "platform", "a") == Written(sequence=1)
        result = control.write("ocp", "application", "b")
        assert result == Rejected(
            cause=RejectionCause.NOT_PERMITTED,
            reason="'ocp' may not write to 'application'",
        )
        assert control.reader.read_level("application") == []

    def test_an_unregistered_writer_is_unrestricted(self) -> None:
        clock = ManualClock(start=START)
        control = make_control(clock)
        assert control.write("operator", "platform", "a") == Written(sequence=1)


class TestValidation:
    def test_an_undeclared_region_in_either_list_is_refused(self) -> None:
        clock = ManualClock(start=START)
        control = make_control(clock)
        with pytest.raises(UndeclaredRegionError, match="missing"):
            control.register_agent(declaration("a", [], subscribes_to=["missing"]))
        with pytest.raises(UndeclaredRegionError, match="missing"):
            control.register_agent(declaration("b", [], writes_to=["missing"]))

    def test_a_level_named_as_a_subscription_is_refused_for_now(self) -> None:
        clock = ManualClock(start=START)
        control = make_control(clock)
        with pytest.raises(UndeclaredRegionError):
            control.register_agent(declaration("c", [], writes_to=["window"]))

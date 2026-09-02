"""A creator names the agents, and one may still join a run already under way."""

from datetime import UTC, datetime, timedelta

import pytest

from blackboard import (
    Agent,
    DuplicateAgentError,
    InMemoryStore,
    Level,
    ManualClock,
    Model,
    Notification,
    NotificationDispatched,
    Premise,
    PremiseOpened,
    RunLimits,
    create_model,
)

START = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
LIMITS = RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(minutes=30))


def a_model(agents: list[Agent] | None = None) -> Model:
    return create_model(
        regions=[Level("platform"), Premise("window")],
        premises={"window": "w"},
        agents=agents,
        limits=LIMITS,
        clock=ManualClock(start=START),
        board_id="test-board",
        store=InMemoryStore(),
    )


class TestNamingAgentsAtCreation:
    def test_every_named_agent_is_premiseed(self) -> None:
        first: list[Notification] = []
        second: list[Notification] = []
        model = a_model(
            [
                Agent(name="ocp", notify=first.append),
                Agent(name="dynatrace", notify=second.append),
            ]
        )
        assert [n.agent for n in first] == ["ocp"]
        assert [n.agent for n in second] == ["dynatrace"]
        assert model.control.outcome() is None

    def test_each_one_is_told_about_a_premise_opened_before_it_arrived(self) -> None:
        received: list[Notification] = []
        a_model([Agent(name="ocp", notify=received.append)])
        (notification,) = received
        assert notification.regions == frozenset({"window"})
        assert notification.from_sequence == 1

    def test_the_premises_open_before_any_agent_is_premiseed(self) -> None:
        model = a_model([Agent(name="ocp", notify=lambda n: None)])
        events = model.control.read_audit()
        opened = next(i for i, e in enumerate(events) if isinstance(e, PremiseOpened))
        dispatched = next(
            i for i, e in enumerate(events) if isinstance(e, NotificationDispatched)
        )
        assert opened < dispatched

    def test_naming_none_leaves_the_run_with_no_agents(self) -> None:
        model = a_model()
        assert model.control.outcome() is None
        assert model.reader.read_premise("window").value == "w"

    def test_a_duplicate_name_inside_the_roster_is_refused(self) -> None:
        with pytest.raises(DuplicateAgentError):
            a_model(
                [
                    Agent(name="ocp", notify=lambda n: None),
                    Agent(name="ocp", notify=lambda n: None),
                ]
            )


class TestJoiningMidRun:
    def test_an_agent_may_still_register_after_creation(self) -> None:
        early: list[Notification] = []
        late: list[Notification] = []
        model = a_model([Agent(name="ocp", notify=early.append)])
        model.control.write("ocp", "platform", "a finding")
        model.control.register_agent(Agent(name="netops", notify=late.append))
        (joined,) = late
        assert joined.agent == "netops"
        # It is out of date with the whole board, so its notification covers
        # the range back to the first write, not just what happened next.
        assert joined.from_sequence == 1
        assert joined.to_sequence == 2

    def test_a_late_agent_subscribed_to_a_level_hears_what_it_missed(self) -> None:
        late: list[Notification] = []
        model = a_model([Agent(name="ocp", notify=lambda n: None)])
        model.control.write("ocp", "platform", "a finding")
        model.control.register_agent(
            Agent(name="netops", notify=late.append, subscribes_to=["platform"])
        )
        (joined,) = late
        assert joined.regions == frozenset({"platform"})

    def test_a_late_agent_with_the_default_subscription_hears_no_level(self) -> None:
        late: list[Notification] = []
        model = a_model([Agent(name="ocp", notify=lambda n: None)])
        model.control.write("ocp", "platform", "a finding")
        model.control.register_agent(Agent(name="netops", notify=late.append))
        (joined,) = late
        assert joined.regions == frozenset({"window"})

    def test_a_late_name_already_in_the_roster_replaces_that_agent(self) -> None:
        model = a_model([Agent(name="ocp", notify=lambda n: None)])
        returning: list[Notification] = []
        model.control.register_agent(Agent(name="ocp", notify=returning.append))
        assert len(returning) == 1, "a returning agent is told what it missed"

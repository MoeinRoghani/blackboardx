"""Six things configure a model, and a seventh says where its record is kept."""

import threading
from datetime import UTC, datetime, timedelta

import pytest

from blackboard import (
    Agent,
    BoardReader,
    DuplicateAgentError,
    InMemoryStore,
    Level,
    ManualClock,
    Notification,
    Premise,
    PremiseError,
    PremiseOpened,
    RunClosedError,
    RunLimits,
    Settled,
    SqliteStore,
    TerminationDecision,
    Written,
    create_model,
)

START = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
LIMITS = RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(minutes=30))


def keep_open(reader: BoardReader) -> TerminationDecision:
    return TerminationDecision.CONTINUE


def declaration(name: str, notify: object) -> Agent:
    return Agent(
        name=name,
        notify=notify,  # type: ignore[arg-type]  # the callers pass list.append
    )


class TestCreation:
    def test_a_model_without_a_board_is_refused(self) -> None:
        with pytest.raises(TypeError, match="board"):
            create_model(  # type: ignore[call-arg]  # the omission is the subject
                regions=[Level("platform")],
                premises={},
                limits=LIMITS,
                clock=ManualClock(start=START),
            )

    def test_the_store_passed_is_the_store_written_to(self) -> None:
        store = SqliteStore()
        model = create_model(
            board_id="test-board",
            store=store,
            regions=[Level("platform"), Premise("window")],
            premises={"window": "w"},
            termination_predicate=keep_open,
            limits=LIMITS,
            clock=ManualClock(start=START),
        )
        model.control.write("ocp", "platform", "a finding")
        assert [c.content for c in store.read_level("test-board", "platform")] == [
            "a finding"
        ]
        assert store.read_premise("test-board", "window").value == "w"
        store.close()

    def test_creation_takes_no_agents_and_wakes_nobody(self) -> None:
        model = create_model(
            regions=[Level("platform"), Premise("window")],
            premises={"window": "w"},
            termination_predicate=keep_open,
            limits=LIMITS,
            clock=ManualClock(start=START),
            board_id="test-board",
            store=InMemoryStore(),
        )
        opened = [e for e in model.control.read_audit() if isinstance(e, PremiseOpened)]
        assert [e.premise for e in opened] == ["window"]
        assert model.reader.read_premise("window").value == "w"

    def test_the_opening_premises_name_exactly_the_declared_premises(self) -> None:
        with pytest.raises(PremiseError, match="miss"):
            create_model(
                regions=[Premise("window"), Premise("service")],
                premises={"window": "w"},
                limits=LIMITS,
                clock=ManualClock(start=START),
                board_id="test-board",
                store=InMemoryStore(),
            )
        with pytest.raises(PremiseError, match="undeclared"):
            create_model(
                regions=[Premise("window")],
                premises={"window": "w", "unknown": "x"},
                limits=LIMITS,
                clock=ManualClock(start=START),
                board_id="test-board",
                store=InMemoryStore(),
            )

    def test_opening_writes_bypass_admission(self) -> None:
        model = create_model(
            regions=[Level("platform"), Premise("window"), Premise("service")],
            premises={"window": "w", "service": "s"},
            termination_predicate=keep_open,
            limits=RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(minutes=30)),
            clock=ManualClock(start=START),
            board_id="test-board",
            store=InMemoryStore(),
        )
        assert model.control.write("ocp", "platform", "fits") == Written(sequence=3)


class TestRegistration:
    def test_registering_wakes_the_agent_once(self) -> None:
        notifications: list[Notification] = []
        model = create_model(
            regions=[Premise("window"), Premise("service")],
            premises={"window": "w", "service": "s"},
            termination_predicate=keep_open,
            limits=LIMITS,
            clock=ManualClock(start=START),
            board_id="test-board",
            store=InMemoryStore(),
        )
        model.control.register_agent(declaration("ocp", notifications.append))
        (notification,) = notifications
        assert notification.agent == "ocp"
        assert notification.regions == frozenset({"window", "service"})

    def test_each_agent_is_woken_when_it_registers(self) -> None:
        first: list[Notification] = []
        second: list[Notification] = []
        model = create_model(
            regions=[Premise("window")],
            premises={"window": "w"},
            termination_predicate=keep_open,
            limits=LIMITS,
            clock=ManualClock(start=START),
            board_id="test-board",
            store=InMemoryStore(),
        )
        model.control.register_agent(declaration("ocp", first.append))
        assert len(first) == 1
        assert second == []
        model.control.register_agent(declaration("git", second.append))
        assert len(first) == 1
        assert len(second) == 1

    def test_a_register_with_no_value_is_not_named_in_the_opening_notification(
        self,
    ) -> None:
        notifications: list[Notification] = []
        model = create_model(
            regions=[Premise("window")],
            premises={"window": "w"},
            termination_predicate=keep_open,
            limits=LIMITS,
            clock=ManualClock(start=START),
            board_id="test-board",
            store=InMemoryStore(),
        )
        model.control.declare(Premise("trigger"))
        model.control.register_agent(declaration("ocp", notifications.append))
        (notification,) = notifications
        assert notification.regions == frozenset({"window"})

    def test_a_duplicate_name_is_refused(self) -> None:
        notifications: list[Notification] = []
        model = create_model(
            regions=[Premise("window")],
            premises={"window": "w"},
            termination_predicate=keep_open,
            limits=LIMITS,
            clock=ManualClock(start=START),
            board_id="test-board",
            store=InMemoryStore(),
        )
        model.control.register_agent(declaration("ocp", notifications.append))
        with pytest.raises(DuplicateAgentError):
            model.control.register_agent(declaration("ocp", notifications.append))

    def test_registering_into_a_closed_run_is_refused(self) -> None:
        model = create_model(
            regions=[Premise("window")],
            premises={"window": "w"},
            limits=LIMITS,
            clock=ManualClock(start=START),
            board_id="test-board",
            store=InMemoryStore(),
        )
        model.control.abort("stopped")
        with pytest.raises(RunClosedError):
            model.control.register_agent(declaration("ocp", [].append))


class TestFullCycle:
    def test_a_run_from_creation_to_settled(self) -> None:
        notifications: list[Notification] = []
        clock = ManualClock(start=START)
        model = create_model(
            regions=[Level("platform"), Premise("window")],
            premises={"window": ["t1", "t2"]},
            limits=LIMITS,
            clock=clock,
            board_id="test-board",
            store=InMemoryStore(),
        )
        model.control.register_agent(declaration("ocp", notifications.append))

        (notification,) = notifications
        window = model.reader.read_premise("window").value
        model.control.write("ocp", "platform", {"window": window, "findings": ["oom"]})
        model.control.ack("ocp", notification.notification_id)

        clock.advance(timedelta(minutes=30))
        assert model.control.outcome() == Settled()
        (contribution,) = model.reader.read_level("platform")
        assert contribution.content == {"window": ["t1", "t2"], "findings": ["oom"]}


class TestSystemClockIntegration:
    def test_wait_closed_returns_the_outcome_under_the_default_clock(self) -> None:
        holder: list[object] = []

        def hand_off(notification: Notification) -> None:
            def work() -> None:
                model = holder[0]
                model.control.write("ocp", "platform", "bundle")  # type: ignore[attr-defined]
                model.control.ack("ocp", notification.notification_id)  # type: ignore[attr-defined]

            threading.Timer(0.03, work).start()

        model = create_model(
            regions=[Level("platform"), Premise("window")],
            premises={"window": "w"},
            limits=RunLimits(
                wall_clock=timedelta(minutes=1), idle=timedelta(seconds=1)
            ),
            board_id="test-board",
            store=InMemoryStore(),
        )
        holder.append(model)
        model.control.register_agent(
            Agent(
                name="ocp",
                notify=hand_off,
            )
        )
        assert model.control.wait_closed(timeout=timedelta(seconds=10)) == Settled()
        (contribution,) = model.reader.read_level("platform")
        assert contribution.content == "bundle"

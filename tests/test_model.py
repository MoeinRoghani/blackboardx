"""A model is created from five inputs, and agents register themselves into it."""

import threading
from datetime import UTC, datetime, timedelta

import pytest

from blackboard import (
    Accepted,
    Agent,
    BoardReader,
    Complete,
    DuplicateAgentError,
    Level,
    ManualClock,
    Notification,
    Register,
    RegisterSeeded,
    RunBudgets,
    RunClosedError,
    SeedError,
    TerminationDecision,
    create_model,
)

START = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
DEADLINE = timedelta(minutes=5)
BUDGETS = RunBudgets(
    wall_clock=timedelta(hours=1), total_writes=1000, total_notifications=1000
)


def keep_open(reader: BoardReader) -> TerminationDecision:
    return TerminationDecision.CONTINUE


def declaration(name: str, notify: object, wake_cap: int = 100) -> Agent:
    return Agent(
        name=name,
        acknowledgment_deadline=DEADLINE,
        wake_cap=wake_cap,
        notify=notify,  # type: ignore[arg-type]  # the callers pass list.append
    )


class TestCreation:
    def test_creation_takes_no_agents_and_wakes_nobody(self) -> None:
        model = create_model(
            regions=[Level("platform"), Register("window")],
            seed={"window": "w"},
            termination_predicate=keep_open,
            budgets=BUDGETS,
            clock=ManualClock(start=START),
        )
        seeded = [
            e for e in model.control.read_audit() if isinstance(e, RegisterSeeded)
        ]
        assert [e.register for e in seeded] == ["window"]
        assert model.reader.read_register("window").value == "w"

    def test_the_seed_names_exactly_the_declared_registers(self) -> None:
        with pytest.raises(SeedError, match="misses"):
            create_model(
                regions=[Register("window"), Register("service")],
                seed={"window": "w"},
                budgets=BUDGETS,
                clock=ManualClock(start=START),
            )
        with pytest.raises(SeedError, match="undeclared"):
            create_model(
                regions=[Register("window")],
                seed={"window": "w", "unknown": "x"},
                budgets=BUDGETS,
                clock=ManualClock(start=START),
            )

    def test_seed_writes_bypass_the_write_budget(self) -> None:
        model = create_model(
            regions=[Level("platform"), Register("window"), Register("service")],
            seed={"window": "w", "service": "s"},
            termination_predicate=keep_open,
            budgets=RunBudgets(
                wall_clock=timedelta(hours=1),
                total_writes=1,
                total_notifications=1000,
            ),
            clock=ManualClock(start=START),
        )
        assert model.control.write("ocp", "platform", "fits") == Accepted(sequence=3)


class TestRegistration:
    def test_registering_wakes_the_agent_once(self) -> None:
        wakes: list[Notification] = []
        model = create_model(
            regions=[Register("window"), Register("service")],
            seed={"window": "w", "service": "s"},
            termination_predicate=keep_open,
            budgets=BUDGETS,
            clock=ManualClock(start=START),
        )
        model.control.register_agent(declaration("ocp", wakes.append))
        (wake,) = wakes
        assert wake.agent == "ocp"
        assert wake.registers == frozenset({"window", "service"})

    def test_each_agent_is_woken_when_it_registers(self) -> None:
        first: list[Notification] = []
        second: list[Notification] = []
        model = create_model(
            regions=[Register("window")],
            seed={"window": "w"},
            termination_predicate=keep_open,
            budgets=BUDGETS,
            clock=ManualClock(start=START),
        )
        model.control.register_agent(declaration("ocp", first.append))
        assert len(first) == 1
        assert second == []
        model.control.register_agent(declaration("git", second.append))
        assert len(first) == 1
        assert len(second) == 1

    def test_a_register_with_no_value_is_not_named_in_the_opening_wake(self) -> None:
        wakes: list[Notification] = []
        model = create_model(
            regions=[Register("window")],
            seed={"window": "w"},
            termination_predicate=keep_open,
            budgets=BUDGETS,
            clock=ManualClock(start=START),
        )
        model.control.declare(Register("trigger"))
        model.control.register_agent(declaration("ocp", wakes.append))
        (wake,) = wakes
        assert wake.registers == frozenset({"window"})

    def test_a_duplicate_name_is_refused(self) -> None:
        wakes: list[Notification] = []
        model = create_model(
            regions=[Register("window")],
            seed={"window": "w"},
            termination_predicate=keep_open,
            budgets=BUDGETS,
            clock=ManualClock(start=START),
        )
        model.control.register_agent(declaration("ocp", wakes.append))
        with pytest.raises(DuplicateAgentError):
            model.control.register_agent(declaration("ocp", wakes.append))

    def test_registering_into_a_closed_run_is_refused(self) -> None:
        model = create_model(
            regions=[Register("window")],
            seed={"window": "w"},
            budgets=BUDGETS,
            clock=ManualClock(start=START),
        )
        model.control.abort("stopped")
        with pytest.raises(RunClosedError):
            model.control.register_agent(declaration("ocp", [].append))


class TestFullCycle:
    def test_a_run_from_creation_to_complete(self) -> None:
        wakes: list[Notification] = []
        model = create_model(
            regions=[Level("platform"), Register("window")],
            seed={"window": ("t1", "t2")},
            budgets=BUDGETS,
            clock=ManualClock(start=START),
        )
        model.control.register_agent(declaration("ocp", wakes.append))

        (wake,) = wakes
        window = model.reader.read_register("window").value
        model.control.write("ocp", "platform", {"window": window, "findings": ["oom"]})
        model.control.ack("ocp", wake.notification_id)

        assert model.control.outcome() == Complete()
        (contribution,) = model.reader.read_level("platform")
        assert contribution.content == {"window": ("t1", "t2"), "findings": ["oom"]}


class TestSystemClockIntegration:
    def test_wait_closed_returns_complete_under_the_default_clock(self) -> None:
        holder: list[object] = []

        def hand_off(notification: Notification) -> None:
            def work() -> None:
                model = holder[0]
                model.control.write("ocp", "platform", "bundle")  # type: ignore[attr-defined]
                model.control.ack("ocp", notification.notification_id)  # type: ignore[attr-defined]

            threading.Timer(0.03, work).start()

        model = create_model(
            regions=[Level("platform"), Register("window")],
            seed={"window": "w"},
            budgets=RunBudgets(
                wall_clock=timedelta(minutes=1),
                total_writes=10,
                total_notifications=10,
            ),
        )
        holder.append(model)
        model.control.register_agent(
            Agent(
                name="ocp",
                acknowledgment_deadline=timedelta(seconds=30),
                wake_cap=10,
                notify=hand_off,
            )
        )
        assert model.control.wait_closed(timeout=timedelta(seconds=10)) == Complete()
        (contribution,) = model.reader.read_level("platform")
        assert contribution.content == "bundle"

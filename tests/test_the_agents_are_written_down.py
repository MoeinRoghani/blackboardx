"""An agent is part of the run, so the run records it.

A board starts with its agents and `register_agent` adds one mid-run. Those
are two doors to one fact, and both leave the same row: the name, what wakes
it, what it may write to, and where to reach it. What is not recorded is the
callable, because a function is not data.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from blackboard import (
    Agent,
    InMemoryStore,
    Level,
    Notification,
    Premise,
    RunLimits,
    create_model,
)

LIMITS = RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(seconds=30))


def a_board(store: Any, **overrides: Any) -> Any:
    settings: dict[str, Any] = {
        "board_id": "incident-1",
        "store": store,
        "regions": [Level("findings"), Premise("severity")],
        "premises": {"severity": "unknown"},
        "limits": LIMITS,
    }
    settings.update(overrides)
    return create_model(**settings)


class TestBothDoorsWriteTheSameRow:
    def test_an_agent_named_at_creation_is_recorded(self) -> None:
        store = InMemoryStore()
        a_board(
            store,
            agents=[
                Agent(
                    name="triage",
                    notify=lambda n: None,
                    subscribes_to=["findings"],
                    writes_to=["findings"],
                    address="https://triage.internal/notify",
                )
            ],
        )
        (declared,) = store.read_agents("incident-1")
        assert declared.subscribes_to == frozenset({"findings"})
        assert declared.writes_to == frozenset({"findings"})
        assert declared.address == "https://triage.internal/notify"

    def test_an_agent_joining_mid_run_is_recorded_the_same_way(self) -> None:
        store = InMemoryStore()
        model = a_board(store)
        model.control.register_agent(
            Agent(
                name="latecomer",
                notify=lambda n: None,
                subscribes_to=["findings"],
                address="https://latecomer.internal/notify",
            )
        )
        (declared,) = store.read_agents("incident-1")
        assert declared.agent == "latecomer"
        assert declared.address == "https://latecomer.internal/notify"

    def test_an_agent_in_this_process_records_no_address(self) -> None:
        store = InMemoryStore()
        a_board(store, agents=[Agent(name="triage", notify=lambda n: None)])
        (declared,) = store.read_agents("incident-1")
        assert declared.address is None


class TestADeclarationNeedsAWayToBeReached:
    def test_naming_neither_is_refused(self) -> None:
        with pytest.raises(ValueError, match="neither"):
            Agent(name="triage")

    def test_an_address_alone_is_enough(self) -> None:
        assert Agent(name="triage", address="https://triage/notify").notify is None

    def test_a_callable_alone_is_enough(self) -> None:
        assert Agent(name="triage", notify=lambda n: None).address is None


class TestAWriteOnAReplicaThatHoldsNoAgent:
    def test_it_records_who_should_hear_from_the_run(self) -> None:
        """The defect this closes: it used to read its own empty roster."""
        store = InMemoryStore()
        holder = a_board(store)
        holder.control.register_agent(
            Agent(name="latecomer", notify=lambda n: None, subscribes_to=["findings"])
        )
        elsewhere = a_board(store)
        assert elsewhere.control._agents == {}

        elsewhere.control.write("findings", "oom", writer="scanner")
        assert [row.agent for row in store.unsent()] == ["latecomer"]

    def test_it_does_not_record_the_writer_against_its_own_write(self) -> None:
        store = InMemoryStore()
        holder = a_board(store)
        holder.control.register_agent(
            Agent(name="triage", notify=lambda n: None, subscribes_to=["findings"])
        )
        a_board(store).control.write("findings", "mine", writer="triage")
        assert store.unsent() == []


class TestReachingAnAgentByAddress:
    def test_a_replica_holding_no_callable_still_delivers(self) -> None:
        """The registering replica is gone, and the agent is still reached."""
        store = InMemoryStore()
        gone = a_board(store)
        gone.control.register_agent(
            Agent(
                name="latecomer",
                notify=lambda n: None,
                subscribes_to=["findings"],
                address="https://latecomer.internal/notify",
            )
        )
        del gone

        posted: list[tuple[str, str]] = []
        surviving = a_board(
            store, reach=lambda address, n: posted.append((address, n.agent))
        )
        surviving.control.write("findings", "oom on web-3", writer="scanner")
        assert surviving.control.relay() == ["latecomer"]
        assert posted == [("https://latecomer.internal/notify", "latecomer")]
        assert store.unsent() == []

    def test_the_notification_names_the_regions_that_changed(self) -> None:
        store = InMemoryStore()
        a_board(store).control.register_agent(
            Agent(
                name="latecomer",
                notify=lambda n: None,
                subscribes_to=["findings"],
                address="https://latecomer.internal/notify",
            )
        )
        seen: list[Notification] = []
        serving = a_board(store, reach=lambda address, n: seen.append(n))
        serving.control.write("findings", "oom", writer="scanner")
        serving.control.relay()
        (told,) = seen
        assert told.regions == frozenset({"findings"})
        assert told.from_sequence == 1

    def test_a_send_that_raises_leaves_the_row(self) -> None:
        def dies(address: str, notification: Notification) -> None:
            raise RuntimeError("unreachable")

        store = InMemoryStore()
        a_board(store).control.register_agent(
            Agent(
                name="latecomer",
                notify=lambda n: None,
                subscribes_to=["findings"],
                address="https://latecomer.internal/notify",
            )
        )
        serving = a_board(store, reach=dies)
        serving.control.write("findings", "oom", writer="scanner")
        assert serving.control.relay() == []
        assert len(store.unsent()) == 1

    def test_without_a_transport_the_row_is_left_for_someone_else(self) -> None:
        store = InMemoryStore()
        a_board(store).control.register_agent(
            Agent(
                name="latecomer",
                notify=lambda n: None,
                subscribes_to=["findings"],
                address="https://latecomer.internal/notify",
            )
        )
        serving = a_board(store)
        serving.control.write("findings", "oom", writer="scanner")
        assert serving.control.relay() == []
        assert len(store.unsent()) == 1

    def test_an_agent_with_no_address_is_left_for_a_process_that_holds_it(
        self,
    ) -> None:
        store = InMemoryStore()
        a_board(store).control.register_agent(
            Agent(name="inprocess", notify=lambda n: None, subscribes_to=["findings"])
        )
        posted: list[str] = []
        serving = a_board(store, reach=lambda address, n: posted.append(address))
        serving.control.write("findings", "oom", writer="scanner")
        assert serving.control.relay() == []
        assert posted == []

"""A write notifies every agent this process can reach, not only the held ones.

The replica taking a write reads who should hear of it from the run, and the
run says where each is reached. So a replica given a transport and no agents,
which is the shape a service that builds a `Control` per request has, wakes
them on the write path rather than leaving every notification to a relay pass.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

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
ADDRESS = "https://ocp.internal/notify"


def a_board(store: Any, **overrides: Any) -> Any:
    settings: dict[str, Any] = {
        "board_id": "incident-1",
        "store": store,
        "regions": [Level("findings"), Level("signals"), Premise("severity")],
        "premises": {"severity": "unknown"},
        "limits": LIMITS,
    }
    settings.update(overrides)
    return create_model(**settings)


def declared(store: Any, **overrides: Any) -> None:
    """An agent on the run that neither process holds a callable for."""
    settings: dict[str, Any] = {
        "name": "ocp",
        "subscribes_to": ["findings"],
        "address": ADDRESS,
    }
    settings.update(overrides)
    a_board(store).control.register_agent(Agent(**settings))


class TestAReplicaThatHoldsNoAgent:
    def test_it_reaches_one_the_run_records(self) -> None:
        store = InMemoryStore()
        declared(store)
        told: list[Notification] = []
        serving = a_board(store, reach=lambda address, n: told.append(n))

        serving.control.write("findings", "oom on web-3", writer="scanner")

        (notification,) = told
        assert notification.agent == "ocp"
        assert notification.regions == frozenset({"findings"})
        assert store.unsent() == []

    def test_the_agent_can_answer_what_it_was_told(self) -> None:
        store = InMemoryStore()
        declared(store)
        told: list[Notification] = []
        serving = a_board(store, reach=lambda address, n: told.append(n))
        serving.control.write("findings", "oom", writer="scanner")

        serving.control.ack(told[-1].notification_id, agent="ocp")

        (progress,) = store.read_agents("incident-1")
        assert progress.acknowledged_through == progress.notified_through

    def test_it_names_every_region_the_agent_has_not_been_told_of(self) -> None:
        store = InMemoryStore()
        declared(store, subscribes_to=["findings", "signals"])
        told: list[Notification] = []
        quiet = a_board(store)
        quiet.control.write("signals", "cpu", writer="scanner")
        serving = a_board(store, reach=lambda address, n: told.append(n))

        serving.control.write("findings", "oom", writer="scanner")

        (notification,) = told
        assert notification.regions == frozenset({"findings", "signals"})

    def test_its_own_write_does_not_wake_it(self) -> None:
        store = InMemoryStore()
        declared(store)
        told: list[Notification] = []
        serving = a_board(store, reach=lambda address, n: told.append(n))

        serving.control.write("findings", "oom", writer="ocp")

        assert told == []

    def test_a_region_it_does_not_subscribe_to_does_not_wake_it(self) -> None:
        store = InMemoryStore()
        declared(store)
        told: list[Notification] = []
        serving = a_board(store, reach=lambda address, n: told.append(n))

        serving.control.write("signals", "cpu", writer="scanner")

        assert told == []


class TestWhatTheWritePathStillLeaves:
    def test_a_batch_window_is_left_to_the_relay(self) -> None:
        """No pending set here to batch into, so the window is not bypassed."""
        store = InMemoryStore()
        declared(store)
        told: list[Notification] = []
        serving = create_model(
            board_id="incident-1",
            store=store,
            regions=[
                Level("findings", batch_window=timedelta(seconds=5)),
                Level("signals"),
                Premise("severity"),
            ],
            premises={"severity": "unknown"},
            limits=LIMITS,
            reach=lambda address, n: told.append(n),
        )

        serving.control.write("findings", "oom", writer="scanner")

        assert told == []
        assert len(store.unsent()) == 1

    def test_a_process_with_no_transport_leaves_the_row(self) -> None:
        store = InMemoryStore()
        declared(store)
        serving = a_board(store)

        serving.control.write("findings", "oom", writer="scanner")

        assert len(store.unsent()) == 1

    def test_an_agent_with_no_address_is_left_for_one_that_holds_it(self) -> None:
        store = InMemoryStore()
        a_board(store).control.register_agent(
            Agent(name="inprocess", subscribes_to=["findings"], notify=lambda n: None)
        )
        posted: list[str] = []
        serving = a_board(store, reach=lambda address, n: posted.append(address))

        serving.control.write("findings", "oom", writer="scanner")

        assert posted == []
        assert len(store.unsent()) == 1

    def test_the_callable_wins_where_this_process_holds_one(self) -> None:
        store = InMemoryStore()
        posted: list[str] = []
        woken: list[Notification] = []
        serving = a_board(
            store,
            agents=[
                Agent(
                    name="ocp",
                    subscribes_to=["findings"],
                    notify=woken.append,
                    address=ADDRESS,
                )
            ],
            reach=lambda address, n: posted.append(address),
        )
        woken.clear()

        serving.control.write("findings", "oom", writer="scanner")

        assert posted == []
        assert len(woken) == 1

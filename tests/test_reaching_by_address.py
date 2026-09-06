"""An agent declared with an address is reached, and its answer is taken.

The run records where an agent is reached, so a process holding a transport
delivers to it whether or not it holds a callable for it. What follows from
that is the part #280 left undone: the agent has to be woken when it joins and
on every write it subscribes to, and the acknowledgment it sends back has to
be accepted.
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
    UnknownNotificationError,
    create_model,
)

LIMITS = RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(seconds=30))
ADDRESS = "https://remote.internal/notify"

#: The opening value of `severity` takes sequence 1, so the first write to
#: `findings` takes 2, and a notification covering it ends there.
FIRST_WRITE = 2


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


class TestAnAgentReachedByAddressCanAnswer:
    """`mark_notified` advances at dispatch, whichever transport carries it."""

    def test_the_relay_records_that_it_told_the_agent(self) -> None:
        store = InMemoryStore()
        stopped = a_board(store)  # no transport, so the write leaves the row
        stopped.control.register_agent(
            Agent(name="remote", subscribes_to=["findings"], address=ADDRESS)
        )
        stopped.control.write("findings", "oom", writer="scanner")

        serving = a_board(store, reach=lambda address, n: None)
        assert serving.control.relay() == ["remote"]

        (progress,) = store.read_agents("incident-1")
        assert progress.notified_through == FIRST_WRITE

    def test_the_acknowledgment_is_accepted(self) -> None:
        store = InMemoryStore()
        stopped = a_board(store)
        stopped.control.register_agent(
            Agent(name="remote", subscribes_to=["findings"], address=ADDRESS)
        )
        stopped.control.write("findings", "oom", writer="scanner")

        told: list[Notification] = []
        serving = a_board(store, reach=lambda address, n: told.append(n))
        serving.control.relay()

        (notification,) = told
        serving.control.ack(notification.notification_id, agent="remote")

        (progress,) = store.read_agents("incident-1")
        assert progress.acknowledged_through == FIRST_WRITE

    def test_a_send_that_raises_still_leaves_the_agent_able_to_answer(self) -> None:
        """Intent is recorded at dispatch, so a resend answers the same range."""

        def dies(address: str, notification: Notification) -> None:
            raise RuntimeError("unreachable")

        store = InMemoryStore()
        stopped = a_board(store)
        stopped.control.register_agent(
            Agent(name="remote", subscribes_to=["findings"], address=ADDRESS)
        )
        stopped.control.write("findings", "oom", writer="scanner")

        serving = a_board(store, reach=dies)
        assert serving.control.relay() == []

        assert len(store.unsent()) == 1
        (progress,) = store.read_agents("incident-1")
        assert progress.notified_through == FIRST_WRITE


class TestOneReplicaOnItsOwn:
    """A process reaches an agent it declared itself, given a transport."""

    def test_a_write_wakes_an_agent_this_process_declared_by_address(self) -> None:
        store = InMemoryStore()
        posted: list[tuple[str, str]] = []
        model = a_board(
            store, reach=lambda address, n: posted.append((address, n.agent))
        )
        model.control.register_agent(
            Agent(name="remote", subscribes_to=["findings"], address=ADDRESS)
        )
        posted.clear()

        model.control.write("findings", "oom", writer="scanner")

        assert posted == [(ADDRESS, "remote")]
        assert store.unsent() == []

    def test_it_answers_and_the_run_completes(self) -> None:
        store = InMemoryStore()
        told: list[Notification] = []
        model = a_board(store, reach=lambda address, n: told.append(n))
        model.control.register_agent(
            Agent(name="remote", subscribes_to=["findings"], address=ADDRESS)
        )
        model.control.write("findings", "oom", writer="scanner")

        model.control.ack(told[-1].notification_id, agent="remote")

        (progress,) = store.read_agents("incident-1")
        assert progress.acknowledged_through == FIRST_WRITE


class TestJoiningIsTheSameThroughEitherTransport:
    """An agent that has just joined is out of date with the whole board."""

    def test_an_agent_declared_by_address_is_woken_when_it_joins(self) -> None:
        store = InMemoryStore()
        told: list[Notification] = []
        model = a_board(store, reach=lambda address, n: told.append(n))
        model.control.write("findings", "oom", writer="scanner")

        model.control.register_agent(
            Agent(name="remote", subscribes_to=["findings"], address=ADDRESS)
        )

        (joined,) = told
        assert joined.regions == frozenset({"findings"})
        assert joined.from_sequence == 1

    def test_it_is_woken_at_creation_the_way_a_callable_is(self) -> None:
        store = InMemoryStore()
        told: list[Notification] = []
        a_board(
            store,
            agents=[Agent(name="remote", address=ADDRESS)],
            reach=lambda address, n: told.append(n),
        )

        (joined,) = told
        assert joined.regions == frozenset({"severity"})

    def test_without_a_transport_it_waits_for_a_process_that_has_one(self) -> None:
        store = InMemoryStore()
        model = a_board(store)
        model.control.register_agent(
            Agent(name="remote", subscribes_to=["findings"], address=ADDRESS)
        )
        model.control.write("findings", "oom", writer="scanner")

        assert len(store.unsent()) == 1
        (progress,) = store.read_agents("incident-1")
        assert progress.notified_through == 0


class TestWhatIsStillLeftForAnotherProcess:
    def test_an_agent_with_a_callable_here_is_not_reached_by_address(self) -> None:
        store = InMemoryStore()
        posted: list[str] = []
        woken: list[Notification] = []
        model = a_board(store, reach=lambda address, n: posted.append(address))
        model.control.register_agent(
            Agent(
                name="both",
                subscribes_to=["findings"],
                notify=woken.append,
                address=ADDRESS,
            )
        )
        woken.clear()

        model.control.write("findings", "oom", writer="scanner")

        assert posted == []
        assert len(woken) == 1

    def test_a_row_the_agent_has_already_answered_is_not_left_unsent(self) -> None:
        """Nothing is owed, so the row goes rather than being scanned for ever."""
        store = InMemoryStore()
        gone = a_board(store)
        gone.control.register_agent(
            Agent(name="remote", subscribes_to=["findings"], address=ADDRESS)
        )
        gone.control.write("findings", "oom", writer="scanner")
        store.mark_notified("incident-1", "remote", through=FIRST_WRITE)
        store.acknowledge("incident-1", "remote", through=FIRST_WRITE)
        assert len(store.unsent()) == 1

        surviving = a_board(store, reach=lambda address, n: None)
        assert surviving.control.relay() == []
        assert store.unsent() == []


class TestTheAnswerStillHasToBeOwed:
    def test_an_identifier_nobody_issued_still_raises(self) -> None:
        store = InMemoryStore()
        model = a_board(store, reach=lambda address, n: None)
        model.control.register_agent(
            Agent(name="remote", subscribes_to=["findings"], address=ADDRESS)
        )
        with pytest.raises(UnknownNotificationError):
            model.control.ack(99, agent="remote")

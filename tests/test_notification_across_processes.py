"""A write taken by one process reaches an agent registered with another.

How far an agent has been told is on the record rather than in whichever
process told it, so the process holding the agent reads the board and
decides. It is the only one that can reach the agent, because it is the
only one holding the callback.
"""

from __future__ import annotations

import warnings
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
    Settled,
    UnknownNotificationError,
    close_expired,
    create_model,
)

LIMITS = RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(seconds=30))
REGIONS = [Level("findings"), Premise("severity")]


def elsewhere(store: Any, **overrides: Any) -> Any:
    """A second process serving a board another one created."""
    from blackboard import attach_model

    settings: dict[str, Any] = {
        "board_id": "incident-1",
        "store": store,
        "regions": REGIONS,
        "limits": LIMITS,
    }
    settings.update(overrides)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return attach_model(**settings)


def here(store: Any, **overrides: Any) -> Any:
    settings: dict[str, Any] = {
        "board_id": "incident-1",
        "store": store,
        "regions": REGIONS,
        "premises": {"severity": "unknown"},
        "limits": LIMITS,
    }
    settings.update(overrides)
    return create_model(**settings)


def watcher(seen: list[Notification], name: str = "triage") -> Agent:
    return Agent(name=name, notify=seen.append, subscribes_to=["findings"])


class TestAWriteOnAnotherProcess:
    def test_the_agent_is_told_when_its_own_process_polls(self) -> None:
        store = InMemoryStore()
        seen: list[Notification] = []
        mine = here(store, agents=[watcher(seen)])
        elsewhere(store).control.write("findings", "oom on web-3", writer="scanner")

        assert seen == [], "the writing process holds no callback for this agent"
        assert mine.control.notify_due() == ["triage"]
        (told,) = seen
        assert told.regions == frozenset({"findings"})
        assert (told.from_sequence, told.to_sequence) == (1, 2)

    def test_polling_again_tells_it_nothing(self) -> None:
        store = InMemoryStore()
        seen: list[Notification] = []
        mine = here(store, agents=[watcher(seen)])
        elsewhere(store).control.write("findings", "oom", writer="scanner")
        mine.control.notify_due()
        assert mine.control.notify_due() == []
        assert len(seen) == 1

    def test_a_write_this_process_took_needs_no_poll(self) -> None:
        store = InMemoryStore()
        seen: list[Notification] = []
        mine = here(store, agents=[watcher(seen)])
        mine.control.write("findings", "oom", writer="scanner")
        assert len(seen) == 1
        assert mine.control.notify_due() == []

    def test_an_agent_is_not_told_of_its_own_write(self) -> None:
        store = InMemoryStore()
        seen: list[Notification] = []
        mine = here(store, agents=[watcher(seen)])
        elsewhere(store).control.write("findings", "oom", writer="triage")
        assert mine.control.notify_due() == []

    def test_a_region_it_does_not_subscribe_to_tells_it_nothing(self) -> None:
        store = InMemoryStore()
        seen: list[Notification] = []
        mine = here(store, agents=[watcher(seen)])
        other = elsewhere(store)
        other.control.set_premise("severity", "high", 1, writer="operator")
        assert mine.control.notify_due() == []

    def test_a_poll_on_a_process_holding_no_agent_tells_nobody(self) -> None:
        store = InMemoryStore()
        seen: list[Notification] = []
        here(store, agents=[watcher(seen)])
        assert elsewhere(store).control.notify_due() == []

    def test_one_notification_covers_every_change_since_the_cursor(self) -> None:
        store = InMemoryStore()
        seen: list[Notification] = []
        mine = here(store, agents=[watcher(seen)])
        other = elsewhere(store)
        for finding in ("oom", "restart loop", "disk full"):
            other.control.write("findings", finding, writer="scanner")
        assert mine.control.notify_due() == ["triage"]
        (told,) = seen
        assert (told.from_sequence, told.to_sequence) == (1, 4)


class TestAnAcknowledgmentIsServedAnywhere:
    def test_a_process_that_never_registered_the_agent_takes_its_answer(
        self,
    ) -> None:
        store = InMemoryStore()
        seen: list[Notification] = []
        mine = here(store, agents=[watcher(seen)])
        other = elsewhere(store)
        other.control.write("findings", "oom", writer="scanner")
        mine.control.notify_due()
        (told,) = seen

        other.control.ack(told.notification_id, agent="triage")
        assert [p.outstanding for p in store.read_agents("incident-1")] == [False]

    def test_an_answer_naming_a_range_never_handed_out_is_refused(self) -> None:
        store = InMemoryStore()
        seen: list[Notification] = []
        mine = here(store, agents=[watcher(seen)])
        elsewhere(store).control.write("findings", "oom", writer="scanner")
        mine.control.notify_due()
        with pytest.raises(UnknownNotificationError):
            mine.control.ack(99, agent="triage")

    def test_an_agent_the_board_never_notified_is_refused(self) -> None:
        store = InMemoryStore()
        with pytest.raises(UnknownNotificationError):
            here(store).control.ack(1, agent="stranger")


class TestTheRecordSaysWhoDidNotFinish:
    def test_the_sweep_names_the_agents_it_did_not_hear_back_from(self) -> None:
        store = InMemoryStore()
        seen: list[Notification] = []
        mine = here(
            store,
            agents=[watcher(seen)],
            limits=RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(seconds=30)),
        )
        elsewhere(store).control.write("findings", "oom", writer="scanner")
        mine.control.notify_due()
        store.open_run("incident-1", wall_clock=3600.0, idle=-1.0)

        assert close_expired(store) == ["incident-1"]
        run = store.read_run("incident-1")
        assert run is not None
        assert run.unfinished == frozenset({"triage"})

    def test_the_sweep_names_nobody_once_every_agent_has_answered(self) -> None:
        store = InMemoryStore()
        seen: list[Notification] = []
        mine = here(store, agents=[watcher(seen)])
        elsewhere(store).control.write("findings", "oom", writer="scanner")
        mine.control.notify_due()
        mine.control.ack(seen[-1].notification_id, agent="triage")
        store.open_run("incident-1", wall_clock=3600.0, idle=-1.0)

        close_expired(store)
        run = store.read_run("incident-1")
        assert run is not None
        assert run.unfinished == frozenset()


class TestAnAgentThatMoved:
    def test_it_resumes_from_what_it_answered_rather_than_the_whole_board(
        self,
    ) -> None:
        """The cursor is on the record, so a process that never saw it has it."""
        store = InMemoryStore()
        seen: list[Notification] = []
        mine = here(store, agents=[watcher(seen)])
        mine.control.write("findings", "oom", writer="scanner")
        mine.control.ack(seen[-1].notification_id, agent="triage")
        mine.control.write("findings", "restart loop", writer="scanner")

        moved: list[Notification] = []
        replacement = elsewhere(store)
        replacement.control.register_agent(watcher(moved))
        (told,) = moved
        assert told.from_sequence == 3, "everything it answered is behind it"

    def test_an_agent_that_answered_everything_is_told_nothing(self) -> None:
        store = InMemoryStore()
        seen: list[Notification] = []
        mine = here(store, agents=[watcher(seen)])
        mine.control.write("findings", "oom", writer="scanner")
        mine.control.ack(seen[-1].notification_id, agent="triage")

        moved: list[Notification] = []
        elsewhere(store).control.register_agent(watcher(moved))
        assert moved == []


class TestTheRunStillClosesOnce:
    def test_two_processes_reaching_the_deadline_name_one_outcome(self) -> None:
        store = InMemoryStore()
        closings: list[str] = []
        mine = here(store, on_closed=lambda o: closings.append("mine"))
        other = elsewhere(store, on_closed=lambda o: closings.append("other"))
        store.open_run("incident-1", wall_clock=3600.0, idle=-1.0)

        assert close_expired(store) == ["incident-1"]
        mine.control.notify_due()
        other.control.notify_due()

        run = store.read_run("incident-1")
        assert run is not None
        assert run.closed_as == "settled"
        assert isinstance(mine.control.outcome(), Settled)
        assert isinstance(other.control.outcome(), Settled)

"""A notification the process lost is sent by the relay.

The intent to notify is a row written with the contribution, so a process
that commits a write and stops before delivering has not lost it. Whoever
holds the agent sends it and marks it sent, and marking after sending is what
makes delivery at least once.
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
    RunLimits,
    UnknownNotificationError,
    close_expired,
    create_model,
)

LIMITS = RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(seconds=30))


def a_model(store: Any, notify: Any, **overrides: Any) -> Any:
    settings: dict[str, Any] = {
        "board_id": "incident-1",
        "store": store,
        "regions": [Level("findings")],
        "premises": {},
        "limits": LIMITS,
        "agents": [Agent(name="triage", notify=notify, subscribes_to=["findings"])],
    }
    settings.update(overrides)
    return create_model(**settings)


class TestAWriteRecordsWhoShouldHear:
    def test_the_write_records_a_row_for_each_subscriber(self) -> None:
        store = InMemoryStore()
        a_model(store, lambda n: None)
        # The inline send clears it, so look at a write whose delivery raised.
        assert store.unsent() == []

    def test_a_delivery_that_raised_leaves_the_row_unsent(self) -> None:
        def dies(notification: Notification) -> None:
            raise RuntimeError("the pod died before sending")

        store = InMemoryStore()
        model = a_model(store, dies)
        model.control.write("findings", "oom on web-3", writer="scanner")

        (row,) = store.unsent()
        assert (row.agent, row.through) == ("triage", 1)

    def test_a_delivery_that_succeeded_leaves_nothing_unsent(self) -> None:
        seen: list[Notification] = []
        store = InMemoryStore()
        model = a_model(store, seen.append)
        model.control.write("findings", "oom on web-3", writer="scanner")

        assert len(seen) == 1
        assert store.unsent() == []

    def test_an_agent_is_not_recorded_against_its_own_write(self) -> None:
        def dies(notification: Notification) -> None:
            raise RuntimeError("never reached")

        store = InMemoryStore()
        model = a_model(store, dies)
        model.control.write("findings", "mine", writer="triage")
        assert store.unsent() == []


class TestTheRelaySendsWhatWasLost:
    def test_a_lost_notification_is_sent_by_a_later_pass(self) -> None:
        """The failure this closes: the send raised and nothing recovered it."""
        lost: list[Notification] = []

        def dies(notification: Notification) -> None:
            raise RuntimeError("the pod died before sending")

        store = InMemoryStore()
        writing = a_model(store, dies)
        # A second replica, same roster, already serving when the write lands.
        # It registers before there is anything to catch up on, so what it
        # delivers below came from the relay and not from registering.
        surviving = a_model(store, lost.append)
        assert lost == []

        writing.control.write("findings", "oom on web-3", writer="scanner")
        assert store.unsent(), "the intent survived the failed send"

        assert surviving.control.relay() == ["triage"]
        assert [n.regions for n in lost] == [frozenset({"findings"})]
        assert store.unsent() == [], "and it is marked sent"

    def test_a_relay_pass_with_nothing_owed_does_nothing(self) -> None:
        seen: list[Notification] = []
        store = InMemoryStore()
        model = a_model(store, seen.append)
        model.control.write("findings", "oom", writer="scanner")
        assert model.control.relay() == []

    def test_it_sends_only_for_agents_this_process_holds(self) -> None:
        def dies(notification: Notification) -> None:
            raise RuntimeError("the pod died before sending")

        store = InMemoryStore()
        writing = a_model(store, dies)
        writing.control.write("findings", "oom", writer="scanner")

        # A replica that holds a different agent leaves the row alone.
        other = create_model(
            board_id="incident-1",
            store=store,
            regions=[Level("findings")],
            premises={},
            limits=LIMITS,
            agents=[Agent(name="capacity", notify=lambda n: None)],
        )
        assert other.control.relay() == []
        assert len(store.unsent()) == 1

    def test_a_send_that_raises_again_leaves_the_row_for_the_next_pass(self) -> None:
        def dies(notification: Notification) -> None:
            raise RuntimeError("still down")

        store = InMemoryStore()
        model = a_model(store, dies)
        model.control.write("findings", "oom", writer="scanner")
        assert model.control.relay() == []
        assert len(store.unsent()) == 1, "nothing is marked sent that was not sent"

    def test_the_repeat_is_absorbed_by_cumulative_acknowledgment(self) -> None:
        """At least once is safe because a notification carries no values."""
        seen: list[Notification] = []
        store = InMemoryStore()
        model = a_model(store, seen.append)
        model.control.write("findings", "oom", writer="scanner")
        # Send the same range again, as a relay that sent and did not mark would.
        store.mark_notified("incident-1", "triage", through=0)
        model.control.as_agent("triage").ack(seen[-1].notification_id)

        progress = next(
            p for p in store.read_agents("incident-1") if p.agent == "triage"
        )
        assert not progress.outstanding


class TestAfterTheRunCloses:
    """Closing clears what each agent was owed, so a late answer finds none."""

    def test_an_answer_arriving_after_the_close_changes_nothing(self) -> None:
        seen: list[Notification] = []
        store = InMemoryStore()
        model = a_model(store, seen.append)
        model.control.write("findings", "oom", writer="scanner")
        model.control.abort("the caller stopped it")

        model.control.ack(seen[-1].notification_id, agent="triage")

    def test_an_answer_naming_nothing_still_raises_while_the_run_is_open(
        self,
    ) -> None:
        store = InMemoryStore()
        model = a_model(store, lambda n: None)
        with pytest.raises(UnknownNotificationError):
            model.control.ack(99, agent="triage")

    def test_closing_leaves_nothing_owed(self) -> None:
        seen: list[Notification] = []
        store = InMemoryStore()
        model = a_model(store, seen.append)
        model.control.write("findings", "oom", writer="scanner")
        model.control.abort("done")

        assert store.read_agents("incident-1") == []
        assert store.unsent() == []

    def test_the_outcome_still_names_who_did_not_finish(self) -> None:
        """It is stamped as the run closes, before anything is cleared."""

        def dies(notification: Notification) -> None:
            raise RuntimeError("never answered")

        store = InMemoryStore()
        model = a_model(store, dies)
        model.control.write("findings", "oom", writer="scanner")
        store.open_run("incident-1", wall_clock=3600.0, idle=-1.0)
        close_expired(store)

        run = store.read_run("incident-1")
        assert run is not None
        assert run.unfinished == frozenset({"triage"})
        assert store.read_agents("incident-1") == []

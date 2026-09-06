"""A lane accepts a notification; the outbox keeps it until one is sent.

`Control` marks a row sent when `Agent.notify` returns, which is right for a
callable that delivers and wrong for one that queues. A lane queues, so the
lane is what marks the row, after the send it made.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from blackboard import Agent, InMemoryStore, Level, RunLimits, create_model
from blackboard.delivery import DeliveryFailed, HttpNotifier

LIMITS = RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(seconds=30))
URL = "https://ocp.internal/notify"


class Held:
    """A transport that answers only when the test lets it."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.refusing = True

    def send(self, url: str, body: dict[str, Any]) -> None:
        if self.refusing:
            raise DeliveryFailed("not yet")
        self.sent.append(url)

    def close(self) -> None:
        pass


def a_board(store: Any, lane: Any) -> Any:
    return create_model(
        board_id="incident-1",
        store=store,
        regions=[Level("findings")],
        premises={},
        agents=[Agent(name="ocp", subscribes_to=["findings"], notify=lane)],
        limits=LIMITS,
    )


class TestWhatALaneLeavesOnTheRecord:
    def test_a_notification_only_queued_is_still_owed(self) -> None:
        transport = Held()
        store = InMemoryStore()
        with HttpNotifier(
            store=store, transport=transport, attempts=1, close_timeout=1.0
        ) as notifier:
            a_board(store, notifier.to(URL)).control.write(
                "findings", "oom", writer="scanner"
            )

        assert transport.sent == []
        assert [row.agent for row in store.unsent()] == ["ocp"]

    def test_a_notification_the_lane_sent_is_marked(self) -> None:
        transport = Held()
        transport.refusing = False
        store = InMemoryStore()
        with HttpNotifier(
            store=store, transport=transport, attempts=1, close_timeout=1.0
        ) as notifier:
            lane = notifier.to(URL)
            a_board(store, lane).control.write("findings", "oom", writer="scanner")

        assert transport.sent == [URL]
        assert store.unsent() == []

    def test_what_it_never_sent_is_delivered_when_the_process_comes_back(self) -> None:
        transport = Held()
        store = InMemoryStore()
        notifier = HttpNotifier(
            store=store, transport=transport, attempts=1, close_timeout=1.0
        )
        a_board(store, notifier.to(URL)).control.write(
            "findings", "oom", writer="scanner"
        )
        notifier.close()
        assert len(store.unsent()) == 1

        transport.refusing = False
        second = HttpNotifier(
            store=store, transport=transport, attempts=1, close_timeout=1.0
        )
        create_model(
            board_id="incident-1",
            store=store,
            regions=[Level("findings")],
            premises={},
            agents=[
                Agent(name="ocp", subscribes_to=["findings"], notify=second.to(URL))
            ],
            limits=LIMITS,
        )
        second.close()  # drains the lane, so the send has happened by here

        assert transport.sent == [URL]
        assert store.unsent() == []


class Queueing:
    """A callable that takes a notification and clears the row itself."""

    marks_sent = True

    def __init__(self) -> None:
        self.taken: list[Any] = []

    def __call__(self, notification: Any) -> None:
        self.taken.append(notification)


class TestARelayToACallableThatQueues:
    def test_it_names_the_agent_it_handed_over_to(self) -> None:
        """It cannot wait for the send, so handing over is what it reports."""
        store = InMemoryStore()
        queueing = Queueing()
        model = a_board(store, queueing)
        model.control.write("findings", "oom", writer="scanner")
        assert len(store.unsent()) == 1
        queueing.taken.clear()

        assert model.control.relay() == ["ocp"]
        assert len(queueing.taken) == 1
        assert len(store.unsent()) == 1


class TestANotifierWithoutAStore:
    def test_it_behaves_as_it_did(self) -> None:
        """The row is marked when the lane accepts, which is at most once."""
        transport = Held()
        store = InMemoryStore()
        with HttpNotifier(
            transport=transport, attempts=1, close_timeout=1.0
        ) as notifier:
            a_board(store, notifier.to(URL)).control.write(
                "findings", "oom", writer="scanner"
            )

        assert transport.sent == []
        assert store.unsent() == []

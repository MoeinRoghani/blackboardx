"""The blackboard's sending half.

The notifier is exercised through a transport the test supplies, so nothing
here needs a running HTTP server. ``backoff`` is passed as a function
returning zero wherever a test would otherwise wait; the default policy is
tested on its own, as the pure function it is.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from blackboard import Agent, Notification, NotificationId
from blackboard.delivery import (
    DeliveryFailed,
    DeliveryFailure,
    DeliveryRefused,
    HttpNotifier,
    default_backoff,
)

NOW = 0.0


def notification(agent: str = "ocp", notification_id: int = 1) -> Notification:
    return Notification(
        notification_id=NotificationId(notification_id),
        board_id="board-1",
        agent=agent,
        from_sequence=1,
        to_sequence=3,
        regions=frozenset({"signals"}),
    )


class Recorder:
    """A transport that records what it was asked to send."""

    def __init__(self, outcomes: list[Exception | None] | None = None) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self._outcomes = list(outcomes or [])
        self._lock = threading.Lock()
        self.arrived = threading.Event()
        self.closed = False

    def send(self, url: str, body: dict[str, Any]) -> None:
        with self._lock:
            self.sent.append((url, body))
            outcome = self._outcomes.pop(0) if self._outcomes else None
        self.arrived.set()
        if outcome is not None:
            raise outcome

    def close(self) -> None:
        self.closed = True


def nowait(attempt: int, retry_after: float | None) -> float:
    return 0.0


def test_a_notification_reaches_the_transport_as_wire_json() -> None:
    recorder = Recorder()
    with HttpNotifier(transport=recorder, backoff=nowait) as notifier:
        notifier.to("https://ocp.example/notify")(notification())
        assert recorder.arrived.wait(5)
    url, body = recorder.sent[0]
    assert url == "https://ocp.example/notify"
    assert body == {
        "board_id": "board-1",
        "notification_id": 1,
        "agent": "ocp",
        "from_sequence": 1,
        "to_sequence": 3,
        "regions": ["signals"],
    }


def test_the_writer_does_not_wait_for_the_delivery() -> None:
    holding = threading.Event()

    class Slow(Recorder):
        def send(self, url: str, body: dict[str, Any]) -> None:
            holding.wait(5)
            super().send(url, body)

    slow = Slow()
    with HttpNotifier(transport=slow, backoff=nowait) as notifier:
        notify = notifier.to("https://ocp.example/notify")
        notify(notification())
        # The call returned while the transport is still held.
        assert slow.sent == []
        holding.set()
        assert slow.arrived.wait(5)


def test_two_agents_are_reached_at_once() -> None:
    both = threading.Barrier(2, timeout=5)

    class Meeting(Recorder):
        def send(self, url: str, body: dict[str, Any]) -> None:
            both.wait()
            super().send(url, body)

    meeting = Meeting()
    with HttpNotifier(transport=meeting, backoff=nowait) as notifier:
        notifier.to("https://ocp.example/notify")(notification(agent="ocp"))
        notifier.to("https://triage.example/notify")(notification(agent="triage"))
    # The barrier releases only if both were in flight together.
    assert len(meeting.sent) == 2


def test_one_stalled_agent_does_not_hold_up_another() -> None:
    stalled = threading.Event()

    class Stalling(Recorder):
        def send(self, url: str, body: dict[str, Any]) -> None:
            if "slow" in url:
                stalled.wait(5)
            super().send(url, body)

    stalling = Stalling()
    notifier = HttpNotifier(transport=stalling, backoff=nowait)
    try:
        notifier.to("https://slow.example/notify")(notification(agent="slow"))
        notifier.to("https://quick.example/notify")(notification(agent="quick"))
        assert stalling.arrived.wait(5)
        assert [url for url, _ in stalling.sent] == ["https://quick.example/notify"]
    finally:
        stalled.set()
        notifier.close()


def test_a_failed_delivery_is_retried_until_it_lands() -> None:
    recorder = Recorder([DeliveryFailed("no route"), DeliveryFailed("no route")])
    with HttpNotifier(transport=recorder, backoff=nowait, attempts=4) as notifier:
        notifier.to("https://ocp.example/notify")(notification())
    assert len(recorder.sent) == 3


def test_a_refusal_is_not_retried() -> None:
    failures: list[DeliveryFailure] = []
    recorder = Recorder([DeliveryRefused("that is not a notification")])
    with HttpNotifier(
        transport=recorder, backoff=nowait, attempts=4, on_failure=failures.append
    ) as notifier:
        notifier.to("https://ocp.example/notify")(notification())
    assert len(recorder.sent) == 1
    assert failures[0].attempts == 1
    assert failures[0].agent == "ocp"


def test_giving_up_reports_the_failure() -> None:
    failures: list[DeliveryFailure] = []
    recorder = Recorder([DeliveryFailed("no route")] * 9)
    with HttpNotifier(
        transport=recorder, backoff=nowait, attempts=3, on_failure=failures.append
    ) as notifier:
        notifier.to("https://ocp.example/notify")(notification())
    assert len(recorder.sent) == 3
    assert len(failures) == 1
    failure = failures[0]
    assert failure.attempts == 3
    assert failure.url == "https://ocp.example/notify"
    assert failure.notification.notification_id == 1
    assert isinstance(failure.error, DeliveryFailed)


def test_giving_up_is_logged_where_an_operator_will_see_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING", logger="blackboard.delivery")
    recorder = Recorder([DeliveryFailed("no route")] * 9)
    with HttpNotifier(transport=recorder, backoff=nowait, attempts=2) as notifier:
        notifier.to("https://ocp.example/notify")(notification())
    messages = [r.getMessage() for r in caplog.records]
    assert any("ocp" in m and "2 attempts" in m for m in messages)


def test_a_failing_agent_does_not_stop_the_next_notification() -> None:
    recorder = Recorder([DeliveryRefused("no")])
    with HttpNotifier(transport=recorder, backoff=nowait, attempts=2) as notifier:
        notify = notifier.to("https://ocp.example/notify")
        notify(notification(notification_id=1))
        notify(notification(notification_id=2))
    assert [body["notification_id"] for _, body in recorder.sent] == [1, 2]


def test_closing_stops_the_transport_too() -> None:
    recorder = Recorder()
    notifier = HttpNotifier(transport=recorder, backoff=nowait)
    notifier.close()
    assert recorder.closed


def test_closing_twice_is_not_an_error() -> None:
    notifier = HttpNotifier(transport=Recorder(), backoff=nowait)
    notifier.close()
    notifier.close()


def test_a_notification_after_close_is_reported_rather_than_dropped() -> None:
    failures: list[DeliveryFailure] = []
    recorder = Recorder()
    notifier = HttpNotifier(
        transport=recorder, backoff=nowait, on_failure=failures.append
    )
    notify = notifier.to("https://ocp.example/notify")
    notifier.close()
    notify(notification())
    assert recorder.sent == []
    assert len(failures) == 1
    assert failures[0].attempts == 0


def test_a_failure_report_that_raises_does_not_kill_the_lane() -> None:
    def explode(failure: DeliveryFailure) -> None:
        raise RuntimeError("the metrics endpoint is down")

    recorder = Recorder([DeliveryRefused("no"), None])
    with HttpNotifier(
        transport=recorder, backoff=nowait, attempts=2, on_failure=explode
    ) as notifier:
        notify = notifier.to("https://ocp.example/notify")
        notify(notification(notification_id=1))
        notify(notification(notification_id=2))
    assert len(recorder.sent) == 2


class TestDefaultBackoff:
    def test_it_grows_with_each_attempt(self) -> None:
        first = default_backoff(1, None)
        fourth = default_backoff(4, None)
        assert first < fourth

    def test_it_is_capped(self) -> None:
        assert default_backoff(30, None) <= 30.0

    def test_it_is_never_the_same_wait_for_every_caller(self) -> None:
        waits = {default_backoff(3, None) for _ in range(50)}
        assert len(waits) > 1

    def test_a_server_asking_for_a_delay_gets_exactly_that_delay(self) -> None:
        assert default_backoff(1, 7.0) == 7.0
        assert default_backoff(4, 7.0) == 7.0

    def test_a_server_asking_for_an_unreasonable_delay_is_capped(self) -> None:
        assert default_backoff(1, 3600.0) == 30.0


class TestHttpxTransport:
    """The shipped transport, exercised through httpx's own mock transport."""

    def answering(self, handler: Any) -> Any:
        httpx = pytest.importorskip("httpx")
        from blackboard.delivery import HttpxTransport

        transport = HttpxTransport()
        transport._client = httpx.Client(transport=httpx.MockTransport(handler))
        return transport

    def answer(self, status: int, headers: dict[str, str] | None = None) -> Any:
        httpx = pytest.importorskip("httpx")
        return lambda request: httpx.Response(status, headers=headers or {})

    def test_a_2xx_is_delivered(self) -> None:
        self.answering(self.answer(204)).send("https://ocp.example/notify", {})

    def test_a_400_is_a_refusal(self) -> None:
        with pytest.raises(DeliveryRefused, match="answered 400"):
            self.answering(self.answer(400)).send("https://ocp.example/notify", {})

    def test_a_503_is_worth_another_attempt(self) -> None:
        with pytest.raises(DeliveryFailed) as raised:
            self.answering(self.answer(503)).send("https://ocp.example/notify", {})
        assert not isinstance(raised.value, DeliveryRefused)

    def test_a_429_is_worth_another_attempt(self) -> None:
        with pytest.raises(DeliveryFailed) as raised:
            self.answering(self.answer(429)).send("https://ocp.example/notify", {})
        assert not isinstance(raised.value, DeliveryRefused)

    def test_retry_after_in_seconds_is_carried_out(self) -> None:
        answer = self.answer(429, {"Retry-After": "12"})
        with pytest.raises(DeliveryFailed) as raised:
            self.answering(answer).send("https://ocp.example/notify", {})
        assert raised.value.retry_after == 12.0

    def test_a_retry_after_date_is_ignored_rather_than_guessed_at(self) -> None:
        answer = self.answer(503, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
        with pytest.raises(DeliveryFailed) as raised:
            self.answering(answer).send("https://ocp.example/notify", {})
        assert raised.value.retry_after is None

    def test_an_unreachable_address_is_worth_another_attempt(self) -> None:
        httpx = pytest.importorskip("httpx")

        def refuse(request: Any) -> Any:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(DeliveryFailed) as raised:
            self.answering(refuse).send("https://ocp.example/notify", {})
        assert not isinstance(raised.value, DeliveryRefused)

    def test_the_body_arrives_as_json(self) -> None:
        seen: list[Any] = []
        httpx = pytest.importorskip("httpx")

        def record(request: Any) -> Any:
            seen.append((request.headers["content-type"], request.read()))
            return httpx.Response(200)

        self.answering(record).send("https://ocp.example/notify", {"agent": "ocp"})
        content_type, body = seen[0]
        assert content_type == "application/json"
        assert json.loads(body) == {"agent": "ocp"}


def test_a_run_reaches_its_agents_through_the_notifier() -> None:
    """The notifier is what an Agent is created with, end to end."""
    from datetime import timedelta

    from blackboard import InMemoryStore, Level, RunLimits, create_model

    recorder = Recorder()
    store = InMemoryStore()
    with HttpNotifier(transport=recorder, backoff=nowait) as notifier:
        model = create_model(
            board_id="board-1",
            store=store,
            regions=[Level("signals")],
            premises={},
            agents=[
                Agent(name="source", notify=notifier.to("https://source.example/n")),
                Agent(
                    name="triage",
                    subscribes_to={"signals"},
                    notify=notifier.to("https://triage.example/notify"),
                ),
            ],
            limits=RunLimits(
                wall_clock=timedelta(minutes=1), idle=timedelta(minutes=1)
            ),
        )
        model.control.write("source", "signals", {"n": 1})
        assert recorder.arrived.wait(5)
    sent = [(url, body) for url, body in recorder.sent]
    assert [url for url, _ in sent] == ["https://triage.example/notify"]
    (body,) = [b for _, b in sent]
    assert body["agent"] == "triage"
    assert body["regions"] == ["signals"]
    assert body["board_id"] == "board-1"

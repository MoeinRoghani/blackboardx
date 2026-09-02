"""Sending notifications to agents over HTTP.

The control component reaches an agent by calling ``Agent.notify``. Left to
itself that call runs on the thread of whichever agent just wrote, one agent
after another, so a write that took microseconds pays for every agent's
round trip and one agent whose endpoint hangs stalls the writer for the
whole timeout.

:class:`HttpNotifier` supplies that callable. It puts the notification on a
queue and returns, and a worker sends it. Each call to
:meth:`HttpNotifier.to` opens its own lane, so agents are reached at the same
time and one that is slow, retrying, or down delays only itself.

    from blackboard import Agent, create_model
    from blackboard.delivery import HttpNotifier

    with HttpNotifier() as notifier:
        model = create_model(
            board_id=board_id,
            store=store,
            regions=[...],
            premises={...},
            agents=[
                Agent(
                    name="ocp",
                    subscribes_to={"signals"},
                    notify=notifier.to("https://ocp.internal/notify"),
                )
            ],
            limits=limits,
        )

The queue is held in memory. A process that stops loses whatever had not been
sent, which usually costs nothing, because a notification carries no values
and the next one covers the range a lost one would have covered. It costs
something when the lost notification is the last, and the run then waits for
an acknowledgment that no agent knows to send until its idle limit closes it.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlsplit

from blackboard._board import BlackboardError
from blackboard._retrying import MAX_BACKOFF, default_backoff
from blackboard.wire import NotificationBody

if TYPE_CHECKING:
    from blackboard._control import Notification

__all__ = [
    "MAX_BACKOFF",
    "DeliveryFailed",
    "DeliveryFailure",
    "DeliveryRefused",
    "HttpNotifier",
    "HttpxTransport",
    "Transport",
    "default_backoff",
]

logger = logging.getLogger(__name__)


class DeliveryFailed(BlackboardError):
    """A delivery did not land and is worth attempting again.

    A refused connection, a timeout, a 5xx, a 429. ``retry_after`` carries
    what the server asked for when it said so.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class DeliveryRefused(DeliveryFailed):
    """A delivery the agent will refuse again.

    A 400, a 404, a 422. Sending the same body a second time produces the
    same answer, so the notifier reports it rather than retrying.
    """


@dataclass(frozen=True)
class DeliveryFailure:
    """What the notifier reports when it stops trying.

    ``attempts`` is how many times the transport was called, so it is zero
    for a notification handed to a notifier that had already closed.
    """

    url: str
    agent: str
    notification: Notification
    attempts: int
    error: Exception


class Transport(Protocol):
    """How one notification leaves the process.

    :class:`HttpxTransport` is the implementation the library ships. Supply
    another to send over something else, or to send nothing in a test.
    """

    def send(self, url: str, body: dict[str, Any]) -> None:
        """Delivers ``body`` to ``url`` as JSON.

        Returns when the agent accepted it. Raises :class:`DeliveryRefused`
        when the agent will refuse it again, and :class:`DeliveryFailed`, or
        any other exception, when another attempt might land.
        """

    def close(self) -> None:
        """Releases whatever the transport holds open."""


class HttpxTransport:
    """Sends over HTTP with ``httpx``.

    Install it with ``pip install blackboardx[notifier]``. One client is held
    open for the life of the transport, so agents reached repeatedly reuse a
    connection.
    """

    def __init__(self, *, timeout: float = 10.0, **client_options: Any) -> None:
        try:
            import httpx
        except ModuleNotFoundError as absent:  # pragma: no cover
            raise ModuleNotFoundError(
                "HttpxTransport needs httpx: pip install blackboardx[notifier]"
            ) from absent
        self._httpx = httpx
        self._client = httpx.Client(timeout=timeout, **client_options)

    def send(self, url: str, body: dict[str, Any]) -> None:
        """Posts ``body`` as JSON and maps the answer onto the two failures."""
        try:
            response = self._client.post(url, json=body)
        except self._httpx.HTTPError as unreachable:
            raise DeliveryFailed(str(unreachable)) from unreachable
        if response.is_success:
            return
        status = response.status_code
        described = f"{url} answered {status}"
        if status in (408, 425, 429) or status >= 500:
            raise DeliveryFailed(described, retry_after=_retry_after(response))
        raise DeliveryRefused(described)

    def close(self) -> None:
        """Closes the client and the connections it holds."""
        self._client.close()


def _retry_after(response: Any) -> float | None:
    # Only the seconds form is read. The HTTP-date form needs the client's
    # clock to agree with the server's, and a wrong one there parks the lane.
    header = response.headers.get("retry-after")
    if header is None:
        return None
    try:
        return float(header.strip())
    except ValueError:
        return None


@dataclass(frozen=True)
class _Sending:
    """What a lane needs to send: the transport and the policy around it."""

    transport: Transport
    attempts: int
    backoff: Callable[[int, float | None], float]
    report: Callable[[DeliveryFailure], None]
    stopping: threading.Event


_STOP = object()


class HttpNotifier:
    """Sends notifications to agents without the writer waiting.

    ``transport`` defaults to :class:`HttpxTransport`, built when the
    notifier is. ``attempts`` counts every call to the transport, so 4 means
    one send and three retries. ``backoff`` decides the wait between them.
    ``on_failure`` is called once for every notification the notifier gives
    up on, on the lane's own thread; a failure is logged at ``ERROR``
    whatever it does.

    Closing drains what is queued, waiting up to ``close_timeout`` seconds,
    and then closes the transport.
    """

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        attempts: int = 4,
        backoff: Callable[[int, float | None], float] = default_backoff,
        on_failure: Callable[[DeliveryFailure], None] | None = None,
        close_timeout: float = 10.0,
    ) -> None:
        if attempts < 1:
            raise ValueError(f"attempts is at least 1, not {attempts}")
        self._transport = transport if transport is not None else HttpxTransport()
        self._on_failure = on_failure
        self._close_timeout = close_timeout
        self._stopping = threading.Event()
        self._sending = _Sending(
            transport=self._transport,
            attempts=attempts,
            backoff=backoff,
            report=self._report,
            stopping=self._stopping,
        )
        self._lock = threading.Lock()
        self._lanes: list[_Lane] = []
        self._closed = False

    def to(self, url: str) -> Callable[[Notification], None]:
        """Returns the ``notify`` callable for one agent.

        Each call opens a lane of its own, so give every agent its own
        callable even when they share an address. Two agents behind one
        callable share a queue and take turns.
        """
        lane = _Lane(url, self._sending)
        with self._lock:
            if self._closed:
                raise RuntimeError("this notifier is closed")
            self._lanes.append(lane)
        return lane.enqueue

    def close(self) -> None:
        """Sends what is queued, stops the lanes, and closes the transport.

        Returns once every lane has stopped or ``close_timeout`` has passed.
        Whatever is still queued at that point is reported through
        ``on_failure`` by the lane that held it. Closing twice does nothing
        the second time.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            lanes = list(self._lanes)
        for lane in lanes:
            lane.stop()
        for lane in lanes:
            lane.join(self._close_timeout)
        self._stopping.set()
        self._transport.close()

    def __enter__(self) -> HttpNotifier:
        return self

    def __exit__(self, *exception: object) -> None:
        self.close()

    def _report(self, failure: DeliveryFailure) -> None:
        logger.error(
            "notification %d for %s was not delivered to %s after %d attempts: %s",
            failure.notification.notification_id,
            failure.agent,
            failure.url,
            failure.attempts,
            failure.error,
        )
        if self._on_failure is None:
            return
        try:
            self._on_failure(failure)
        except Exception:
            logger.exception("the on_failure handler raised")


class _Lane:
    """One agent's queue and the thread that drains it."""

    def __init__(self, url: str, sending: _Sending) -> None:
        self._url = url
        self._sending = sending
        self._queue: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stopped = False

    def enqueue(self, notification: Notification) -> None:
        """Queues one notification and returns. Called on the writer's thread."""
        with self._lock:
            if self._stopped:
                self._sending.report(
                    DeliveryFailure(
                        url=self._url,
                        agent=notification.agent,
                        notification=notification,
                        attempts=0,
                        error=DeliveryFailed("the notifier is closed"),
                    )
                )
                return
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._drain,
                    name=f"blackboard-notify-{urlsplit(self._url).netloc or self._url}",
                    daemon=True,
                )
                self._thread.start()
        self._queue.put(notification)

    def stop(self) -> None:
        """Asks the lane to finish what is queued and end."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        self._queue.put(_STOP)

    def join(self, timeout: float) -> None:
        """Waits for the lane's thread to end."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _drain(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                self._abandon_what_is_left()
                return
            self._send(item)

    def _abandon_what_is_left(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            if item is _STOP:
                continue
            self._sending.report(
                DeliveryFailure(
                    url=self._url,
                    agent=item.agent,
                    notification=item,
                    attempts=0,
                    error=DeliveryFailed("the notifier closed before this was sent"),
                )
            )

    def _send(self, notification: Notification) -> None:
        body = NotificationBody(
            board_id=notification.board_id,
            notification_id=int(notification.notification_id),
            agent=notification.agent,
            from_sequence=notification.from_sequence,
            to_sequence=notification.to_sequence,
            regions=sorted(notification.regions),
        ).to_json()
        for attempt in range(1, self._sending.attempts + 1):
            try:
                self._sending.transport.send(self._url, body)
                return
            except DeliveryRefused as refused:
                self._fail(notification, attempt, refused)
                return
            except Exception as failed:
                if attempt == self._sending.attempts:
                    self._fail(notification, attempt, failed)
                    return
                retry_after = getattr(failed, "retry_after", None)
                wait = self._sending.backoff(attempt, retry_after)
                logger.warning(
                    "notification %d for %s did not reach %s on attempt %d,"
                    " retrying in %.1fs: %s",
                    notification.notification_id,
                    notification.agent,
                    self._url,
                    attempt,
                    wait,
                    failed,
                )
                if self._sending.stopping.wait(wait):
                    self._fail(notification, attempt, failed)
                    return

    def _fail(
        self, notification: Notification, attempts: int, error: Exception
    ) -> None:
        self._sending.report(
            DeliveryFailure(
                url=self._url,
                agent=notification.agent,
                notification=notification,
                attempts=attempts,
                error=error,
            )
        )

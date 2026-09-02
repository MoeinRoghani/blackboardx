"""Calling a blackboard from an agent.

An agent that runs as its own service reads and writes over HTTP. This module
is the agent's side of the protocol :mod:`blackboard.wire` states, so an agent
names no URL, no status code, and no header.

A client is bound to one board and one agent name, the way
:class:`~blackboard.BoardReader` is bound to one board. Its methods are the
ones :class:`~blackboard.Control` and :class:`~blackboard.BoardReader` already
have, spelled the same and returning the same types, so an agent that ran in
the same process as the blackboard moves out of it without relearning
anything. :class:`BoardClient` satisfies ``BoardReader``, so an admission rule
or a termination predicate written against that protocol reads a remote board
as well as a local one.

    from blackboard.agent import BoardClient
    from blackboard.wire import NotificationBody

    @app.post("/notify")
    def notify(body: dict):
        notification = NotificationBody.from_json(body)
        with BoardClient(
            base_url="https://blackboard.internal/v1",
            board_id=notification.board_id,
            agent="triage",
        ) as board:
            for change in board.read_level("signals", notification.from_sequence):
                ...
            board.write("findings", {"cause": "a bad deploy"})
            board.ack(notification.notification_id)

:class:`AsyncBoardClient` is the same surface with ``await`` in front of every
method. Neither is written in terms of the other: a synchronous method that
starts an event loop per call throws away the connection pool, so both are
real and the request they build is one piece of shared code.

Install it with ``pip install blackboardx[agent]``.

What comes back, and what is raised
-----------------------------------

A write answers the way :meth:`~blackboard.Control.write` answers, with
:class:`~blackboard.Written` or :class:`~blackboard.Rejected`, and setting a
premise adds :class:`~blackboard.Conflict`. Those are answers rather than
faults, and sending the same request again gets the same one.

Everything else is raised, and where the blackboard has an exception for it
the client raises that one: :class:`~blackboard.UndeclaredRegionError`,
:class:`~blackboard.RegionKindError`, :class:`~blackboard.UnsetPremiseError`,
:class:`~blackboard.UnknownNotificationError`. A board this blackboard does
not hold raises :class:`UnknownBoardError`, a blackboard that could not be
reached raises :class:`Unreachable`, and an answer the two halves disagree
about raises :class:`ProtocolError`.

What is attempted again
-----------------------

A read and an acknowledgment are attempted again when the blackboard cannot
be reached or answers 5xx. Reading twice returns the same thing, and an
acknowledgment of a notification no longer outstanding changes nothing.

A write is not. A request that timed out may still have been received, and a
contribution appended twice is not the same board. The write raises
:class:`Unreachable` and the agent decides, which is the only honest answer
until the blackboard deduplicates on a key the client sends.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import TracebackType
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from blackboard._board import (
    BlackboardError,
    BoardChange,
    Conflict,
    Contribution,
    Level,
    Premise,
    PremiseState,
    RegionKindError,
    UndeclaredRegionError,
    UnsetPremiseError,
    Written,
)
from blackboard._control import (
    NotificationId,
    Rejected,
    RejectionCause,
    UnknownNotificationError,
)
from blackboard._retrying import default_backoff
from blackboard.wire import (
    ACK,
    FROM_SEQUENCE,
    LIMIT,
    READ_BOARD,
    READ_LEVEL,
    READ_PREMISE,
    READ_REGIONS,
    SET_PREMISE,
    WRITE,
    AckRequest,
    BoardPage,
    ConflictBody,
    ErrorBody,
    LevelPage,
    PremiseBody,
    RegionList,
    RejectedBody,
    SetPremiseRequest,
    WireError,
    WriteRequest,
    WrittenBody,
)

if TYPE_CHECKING:
    import httpx

__all__ = [
    "AsyncBoardClient",
    "BoardClient",
    "ProtocolError",
    "UnknownBoardError",
    "Unreachable",
    "default_backoff",
]

_T = TypeVar("_T")


class UnknownBoardError(BlackboardError):
    """The blackboard answered that it holds no board by that identifier."""


class Unreachable(BlackboardError):
    """The blackboard could not be reached, or kept answering 5xx."""


class ProtocolError(BlackboardError):
    """The blackboard answered something this client cannot make sense of.

    A body that will not decode, a status no operation produces, or a
    request this client built that the blackboard refused as malformed. It
    means the two halves are out of step rather than that the call was wrong.
    """


@dataclass(frozen=True)
class _Call(Generic[_T]):
    """One request, and how to read the answer, built once for both clients."""

    method: str
    path: str
    read: Callable[[int, object], _T]
    query: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] | None = None
    #: Whether sending it a second time is safe.
    repeatable: bool = True


# Building the requests


def _bounds(from_sequence: int, limit: int | None) -> dict[str, str]:
    query = {FROM_SEQUENCE: str(from_sequence)}
    if limit is not None:
        query[LIMIT] = str(limit)
    return query


def _read_regions_call(board_id: str) -> _Call[list[Level | Premise]]:
    return _Call(
        method=READ_REGIONS.method,
        path=READ_REGIONS.path(board_id=board_id),
        read=_regions,
    )


def _read_level_call(
    board_id: str, level: str, from_sequence: int, limit: int | None
) -> _Call[LevelPage]:
    return _Call(
        method=READ_LEVEL.method,
        path=READ_LEVEL.path(board_id=board_id, level=level),
        query=_bounds(from_sequence, limit),
        read=_level_page,
    )


def _read_premise_call(board_id: str, premise: str) -> _Call[PremiseState]:
    return _Call(
        method=READ_PREMISE.method,
        path=READ_PREMISE.path(board_id=board_id, premise=premise),
        read=_premise,
    )


def _read_board_call(
    board_id: str, from_sequence: int, limit: int | None
) -> _Call[BoardPage]:
    return _Call(
        method=READ_BOARD.method,
        path=READ_BOARD.path(board_id=board_id),
        query=_bounds(from_sequence, limit),
        read=_board_page,
    )


def _write_call(
    board_id: str, agent: str, level: str, content: object
) -> _Call[Written | Rejected]:
    return _Call(
        method=WRITE.method,
        path=WRITE.path(board_id=board_id, level=level),
        body=WriteRequest(writer=agent, level=level, content=content).to_json(),
        read=_write_outcome,
        repeatable=False,
    )


def _set_premise_call(
    board_id: str, agent: str, premise: str, value: object, expected_version: int
) -> _Call[Written | Conflict | Rejected]:
    return _Call(
        method=SET_PREMISE.method,
        path=SET_PREMISE.path(board_id=board_id, premise=premise),
        body=SetPremiseRequest(
            writer=agent,
            premise=premise,
            value=value,
            expected_version=expected_version,
        ).to_json(),
        read=_premise_outcome,
        repeatable=False,
    )


def _ack_call(board_id: str, agent: str, notification_id: int) -> _Call[None]:
    return _Call(
        method=ACK.method,
        path=ACK.path(board_id=board_id),
        body=AckRequest(agent=agent, notification_id=notification_id).to_json(),
        read=_nothing,
    )


# Reading the answers


def _regions(status: int, body: object) -> list[Level | Premise]:
    _refuse(status, body, expected=(200,))
    return [region.declaration() for region in _decode(RegionList, body).regions]


def _level_page(status: int, body: object) -> LevelPage:
    _refuse(status, body, expected=(200,))
    return _decode(LevelPage, body)


def _board_page(status: int, body: object) -> BoardPage:
    _refuse(status, body, expected=(200,))
    return _decode(BoardPage, body)


def _premise(status: int, body: object) -> PremiseState:
    _refuse(status, body, expected=(200,))
    found = _decode(PremiseBody, body)
    return PremiseState(value=found.value, version=found.version)


def _write_outcome(status: int, body: object) -> Written | Rejected:
    if status == 201:
        written = _decode(WrittenBody, body)
        return Written(sequence=written.sequence, version=written.version)
    if status == 422:
        return _rejection(body)
    if status == 410:
        return Rejected(cause=RejectionCause.RUN_CLOSED, reason=_detail(body))
    _refuse(status, body, expected=())
    raise ProtocolError(f"the blackboard answered {status} to a write")


def _premise_outcome(status: int, body: object) -> Written | Conflict | Rejected:
    if status == 409:
        return Conflict(current_version=_decode(ConflictBody, body).current_version)
    return _write_outcome(status, body)


def _nothing(status: int, body: object) -> None:
    _refuse(status, body, expected=(200, 204))


def _rejection(body: object) -> Rejected:
    refused = _decode(RejectedBody, body)
    try:
        cause = RejectionCause(refused.cause)
    except ValueError as unknown:
        raise ProtocolError(
            f"the blackboard refused a write for {refused.cause!r},"
            " which this version does not know"
        ) from unknown
    return Rejected(cause=cause, reason=refused.reason)


def _decode(kind: type[_T], body: object) -> _T:
    try:
        return kind.from_json(body)  # type: ignore[attr-defined,no-any-return]
    except WireError as unreadable:
        raise ProtocolError(
            f"the blackboard answered with {unreadable}"
        ) from unreadable


def _detail(body: object) -> str:
    if isinstance(body, dict) and isinstance(body.get("detail"), str):
        return str(body["detail"])
    return ""


#: The error name each 4xx carries, and what it means to an agent.
_RAISES: Mapping[str, type[BlackboardError]] = {
    "unknown_board": UnknownBoardError,
    "unknown_region": UndeclaredRegionError,
    "wrong_region_kind": RegionKindError,
    "unset_premise": UnsetPremiseError,
    "unknown_notification": UnknownNotificationError,
}


def _refuse(status: int, body: object, expected: tuple[int, ...]) -> None:
    if status in expected:
        return None
    if 400 <= status < 500:
        named = _RAISES.get(_named(body))
        if named is not None:
            raise named(_detail(body) or f"the blackboard answered {status}")
        raise ProtocolError(
            f"the blackboard answered {status}: {_detail(body) or _named(body)}"
        )
    raise ProtocolError(f"the blackboard answered {status} where it answers 200")


def _named(body: object) -> str:
    if not isinstance(body, dict):
        return ""
    try:
        return ErrorBody.from_json(body).error
    except WireError:
        return ""


#: Statuses worth another attempt. Everything else is an answer.
_WORTH_ANOTHER_ATTEMPT = frozenset({408, 425, 429})


def _again(status: int) -> bool:
    return status >= 500 or status in _WORTH_ANOTHER_ATTEMPT


def _retry_after(headers: Mapping[str, str]) -> float | None:
    header = headers.get("retry-after")
    if header is None:
        return None
    try:
        return float(header.strip())
    except ValueError:
        return None


def _next_page(sequences: list[int], has_more: bool) -> int | None:
    """Returns where the next page starts, or ``None`` when there is no next.

    A page that says there is more and returned nothing would be read for
    ever, so it ends the reading instead.
    """
    if not has_more or not sequences:
        return None
    return max(sequences) + 1


def _import_httpx() -> Any:
    try:
        import httpx
    except ModuleNotFoundError as absent:  # pragma: no cover
        raise ModuleNotFoundError(
            "the agent client needs httpx: pip install blackboardx[agent]"
        ) from absent
    return httpx


class _Bound:
    """What both clients hold: where the blackboard is, and who is calling."""

    def __init__(
        self,
        base_url: str,
        board_id: str,
        agent: str,
        attempts: int,
        backoff: Callable[[int, float | None], float],
    ) -> None:
        if attempts < 1:
            raise ValueError(f"attempts is at least 1, not {attempts}")
        self.base_url = base_url.rstrip("/")
        self.board_id = board_id
        self.agent = agent
        self.attempts = attempts
        self.backoff = backoff

    def url(self, path: str) -> str:
        return self.base_url + path

    def tries(self, call: _Call[Any]) -> int:
        return self.attempts if call.repeatable else 1


class BoardClient:
    """Reads and writes one board, as one agent, over HTTP.

    ``base_url`` is where the blackboard mounted the operations, including
    any prefix. ``http_client`` takes an ``httpx.Client`` you configured
    yourself, with the authentication, the certificates, or the proxy your
    deployment needs; without one the client builds a plain client and closes
    it with itself.
    """

    def __init__(
        self,
        *,
        base_url: str,
        board_id: str,
        agent: str,
        http_client: httpx.Client | None = None,
        attempts: int = 3,
        backoff: Callable[[int, float | None], float] = default_backoff,
        timeout: float = 10.0,
    ) -> None:
        self._bound = _Bound(base_url, board_id, agent, attempts, backoff)
        self._httpx = _import_httpx()
        self._ours = http_client is None
        self._client = (
            http_client
            if http_client is not None
            else self._httpx.Client(timeout=timeout)
        )

    @property
    def board_id(self) -> str:
        """The board this client reads and writes."""
        return self._bound.board_id

    def read_regions(self) -> list[Level | Premise]:
        """Returns the regions declared on this board, with their kinds."""
        return self._send(_read_regions_call(self._bound.board_id))

    def read_level(
        self, level: str, from_sequence: int = 0, limit: int | None = None
    ) -> list[Contribution]:
        """Returns a level's contributions from the sequence bound, inclusive.

        Without a ``limit`` this reads the level to its end, following the
        blackboard's pages. With one it returns at most that many.
        """
        found: list[Contribution] = []
        start: int | None = from_sequence
        while start is not None:
            page = self._send(
                _read_level_call(self._bound.board_id, level, start, limit)
            )
            found.extend(
                Contribution(sequence=c.sequence, content=c.content)
                for c in page.contributions
            )
            if limit is not None:
                break
            start = _next_page([c.sequence for c in page.contributions], page.has_more)
        return found

    def read_premise(self, premise: str) -> PremiseState:
        """Returns a premise's current value and version."""
        return self._send(_read_premise_call(self._bound.board_id, premise))

    def read_board(
        self, from_sequence: int = 0, limit: int | None = None
    ) -> list[BoardChange]:
        """Returns every write to every region, in sequence order, from the bound."""
        found: list[BoardChange] = []
        start: int | None = from_sequence
        while start is not None:
            page = self._send(_read_board_call(self._bound.board_id, start, limit))
            found.extend(
                BoardChange(sequence=c.sequence, region=c.region, content=c.content)
                for c in page.changes
            )
            if limit is not None:
                break
            start = _next_page([c.sequence for c in page.changes], page.has_more)
        return found

    def write(self, level: str, content: object) -> Written | Rejected:
        """Proposes a contribution to a level, as this client's agent."""
        return self._send(
            _write_call(self._bound.board_id, self._bound.agent, level, content)
        )

    def set_premise(
        self, premise: str, value: object, expected_version: int
    ) -> Written | Conflict | Rejected:
        """Sets a premise, provided it is still at ``expected_version``."""
        return self._send(
            _set_premise_call(
                self._bound.board_id,
                self._bound.agent,
                premise,
                value,
                expected_version,
            )
        )

    def ack(self, notification_id: NotificationId | int) -> None:
        """Records that this agent finished responding to a notification."""
        self._send(
            _ack_call(self._bound.board_id, self._bound.agent, int(notification_id))
        )

    def close(self) -> None:
        """Closes the HTTP client, unless one was supplied."""
        if self._ours:
            self._client.close()

    def __enter__(self) -> BoardClient:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        raised: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _send(self, call: _Call[_T]) -> _T:
        tries = self._bound.tries(call)
        for attempt in range(1, tries + 1):
            try:
                answer = self._client.request(
                    call.method,
                    self._bound.url(call.path),
                    params=call.query or None,
                    json=call.body,
                )
            except self._httpx.HTTPError as unreachable:
                if attempt == tries:
                    raise Unreachable(str(unreachable)) from unreachable
                _pause(self._bound.backoff(attempt, None))
                continue
            if _again(answer.status_code) and attempt < tries:
                _pause(self._bound.backoff(attempt, _retry_after(answer.headers)))
                continue
            if _again(answer.status_code):
                raise Unreachable(
                    f"the blackboard answered {answer.status_code} on {tries} attempts"
                )
            return call.read(answer.status_code, _json(answer))
        raise AssertionError("unreachable")  # pragma: no cover


class AsyncBoardClient:
    """:class:`BoardClient` with ``await`` in front of every method.

    It builds the same requests and reads the same answers; only the sending
    differs. ``http_client`` takes an ``httpx.AsyncClient``.
    """

    def __init__(
        self,
        *,
        base_url: str,
        board_id: str,
        agent: str,
        http_client: httpx.AsyncClient | None = None,
        attempts: int = 3,
        backoff: Callable[[int, float | None], float] = default_backoff,
        timeout: float = 10.0,
    ) -> None:
        self._bound = _Bound(base_url, board_id, agent, attempts, backoff)
        self._httpx = _import_httpx()
        self._ours = http_client is None
        self._client = (
            http_client
            if http_client is not None
            else self._httpx.AsyncClient(timeout=timeout)
        )

    @property
    def board_id(self) -> str:
        """The board this client reads and writes."""
        return self._bound.board_id

    async def read_regions(self) -> list[Level | Premise]:
        """Returns the regions declared on this board, with their kinds."""
        return await self._send(_read_regions_call(self._bound.board_id))

    async def read_level(
        self, level: str, from_sequence: int = 0, limit: int | None = None
    ) -> list[Contribution]:
        """Returns a level's contributions from the sequence bound, inclusive."""
        found: list[Contribution] = []
        start: int | None = from_sequence
        while start is not None:
            page = await self._send(
                _read_level_call(self._bound.board_id, level, start, limit)
            )
            found.extend(
                Contribution(sequence=c.sequence, content=c.content)
                for c in page.contributions
            )
            if limit is not None:
                break
            start = _next_page([c.sequence for c in page.contributions], page.has_more)
        return found

    async def read_premise(self, premise: str) -> PremiseState:
        """Returns a premise's current value and version."""
        return await self._send(_read_premise_call(self._bound.board_id, premise))

    async def read_board(
        self, from_sequence: int = 0, limit: int | None = None
    ) -> list[BoardChange]:
        """Returns every write to every region, in sequence order, from the bound."""
        found: list[BoardChange] = []
        start: int | None = from_sequence
        while start is not None:
            page = await self._send(
                _read_board_call(self._bound.board_id, start, limit)
            )
            found.extend(
                BoardChange(sequence=c.sequence, region=c.region, content=c.content)
                for c in page.changes
            )
            if limit is not None:
                break
            start = _next_page([c.sequence for c in page.changes], page.has_more)
        return found

    async def write(self, level: str, content: object) -> Written | Rejected:
        """Proposes a contribution to a level, as this client's agent."""
        return await self._send(
            _write_call(self._bound.board_id, self._bound.agent, level, content)
        )

    async def set_premise(
        self, premise: str, value: object, expected_version: int
    ) -> Written | Conflict | Rejected:
        """Sets a premise, provided it is still at ``expected_version``."""
        return await self._send(
            _set_premise_call(
                self._bound.board_id,
                self._bound.agent,
                premise,
                value,
                expected_version,
            )
        )

    async def ack(self, notification_id: NotificationId | int) -> None:
        """Records that this agent finished responding to a notification."""
        await self._send(
            _ack_call(self._bound.board_id, self._bound.agent, int(notification_id))
        )

    async def close(self) -> None:
        """Closes the HTTP client, unless one was supplied."""
        if self._ours:
            await self._client.aclose()

    async def __aenter__(self) -> AsyncBoardClient:
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        raised: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def _send(self, call: _Call[_T]) -> _T:
        tries = self._bound.tries(call)
        for attempt in range(1, tries + 1):
            try:
                answer = await self._client.request(
                    call.method,
                    self._bound.url(call.path),
                    params=call.query or None,
                    json=call.body,
                )
            except self._httpx.HTTPError as unreachable:
                if attempt == tries:
                    raise Unreachable(str(unreachable)) from unreachable
                await _apause(self._bound.backoff(attempt, None))
                continue
            if _again(answer.status_code) and attempt < tries:
                await _apause(
                    self._bound.backoff(attempt, _retry_after(answer.headers))
                )
                continue
            if _again(answer.status_code):
                raise Unreachable(
                    f"the blackboard answered {answer.status_code} on {tries} attempts"
                )
            return call.read(answer.status_code, _json(answer))
        raise AssertionError("unreachable")  # pragma: no cover


def _json(answer: Any) -> object:
    if not answer.content:
        return None
    try:
        return answer.json()
    except ValueError as unreadable:
        raise ProtocolError(
            f"the blackboard answered {answer.status_code} with something"
            " that is not JSON"
        ) from unreadable


def _pause(seconds: float) -> None:
    import time

    time.sleep(seconds)


async def _apause(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)

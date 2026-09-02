"""Answering an agent's request from the blackboard's side.

An agent that runs as its own service reads and writes over HTTP. The
operations it needs are the ones :class:`~blackboard.Control` already has, so
the only questions left are which path carries which one and which status
code carries which answer. Both halves of this SDK take those answers from
:mod:`blackboard.wire`, so a service and its agents cannot disagree about
them.

:class:`BoardService` is the blackboard's side. It takes a method, a path,
and a decoded body, and returns a status, headers, and a body. It imports no
web framework and opens no socket: the service keeps its own server, its
routing prefix, and its authentication, and hands each request through.

    from blackboard.server import BoardService, Request

    runs: dict[str, Control] = {}
    service = BoardService(control_for=runs.get, prefix="/v1")

    @app.route("/v1/<path:rest>", methods=["GET", "POST", "PUT"])
    def blackboard(rest: str):
        answer = service.handle(
            Request(
                method=flask.request.method,
                path=flask.request.path,
                body=flask.request.get_json(silent=True),
                query=flask.request.args,
            )
        )
        return answer.body or "", answer.status, answer.headers

One :class:`~blackboard.Control` serves one board, and ``control_for`` finds
the one a request names. A dictionary's ``get`` is the usual answer; a
callable that builds a run on first sight is another.

``path`` is matched a segment at a time and each variable is decoded once, so
a board identifier that holds a slash arrives whole as long as the framework
hands over the path it received. Some frameworks decode the path before you
see it, and a board identifier is easiest to keep opaque: a UUID travels
through every one of them unchanged.

What the status codes mean
--------------------------

======  ==========================================================
Status  Meaning
======  ==========================================================
200     A read, or a write this key had already made
201     A write reached the board for the first time
204     An acknowledgment was recorded
400     The body or a query parameter could not be read
404     No such board, region, notification, or path
405     That path takes a different method
409     A premise moved on, or a key named another region
410     The run has closed and takes no more writes
422     The write was refused; the answer names the cause
======  ==========================================================

409 and 422 are answers rather than faults, so a client that sends the same
request again gets the same one. Only 5xx and a failure to connect are worth
another attempt.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

from blackboard._board import (
    Conflict,
    IdempotencyKeyError,
    RegionKindError,
    UndeclaredRegionError,
    UnsetPremiseError,
    Written,
)
from blackboard._control import (
    BoardReader,
    BoardStore,
    Control,
    Rejected,
    RejectionCause,
    RunClosedError,
    UnknownNotificationError,
    reader_for,
)
from blackboard.wire import (
    ACK,
    FROM_SEQUENCE,
    LIMIT,
    OPERATIONS,
    READ_BOARD,
    READ_LEVEL,
    READ_PREMISE,
    READ_REGIONS,
    SET_PREMISE,
    WRITE,
    AckRequest,
    BoardChangeBody,
    BoardPage,
    ConflictBody,
    ContributionBody,
    ErrorBody,
    LevelPage,
    Operation,
    PremiseBody,
    RegionBody,
    RegionList,
    RejectedBody,
    SetPremiseRequest,
    WriteRequest,
    WrittenBody,
)

__all__ = ["BoardService", "Request", "Response"]


#: How one operation is answered, once its path has been matched.
@dataclass(frozen=True)
class _Serving:
    """What answering one request needs: always a reader, sometimes a run."""

    reader: BoardReader
    control: Control | None

    def run(self) -> Control:
        # Only a write reaches this, and a write without a run answered 404
        # before it got here.
        assert self.control is not None
        return self.control


_Answer = Callable[[dict[str, str], "Request", _Serving], "Response"]


@dataclass(frozen=True)
class Request:
    """One request, as the service's own framework already holds it.

    ``body`` is JSON that has already been parsed, or ``None`` where the
    request carried none. ``query`` is the query string parsed into single
    values; a parameter given twice keeps whichever the framework kept.
    """

    method: str
    path: str
    body: object = None
    query: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Response:
    """What to answer with: a status, headers, and JSON or nothing."""

    status: int
    body: dict[str, Any] | None = None
    headers: Mapping[str, str] = field(default_factory=dict)


def _error(status: int, error: str, detail: str = "") -> Response:
    return Response(status, ErrorBody(error=error, detail=detail).to_json())


class BoardService:
    """Answers the operations in :mod:`blackboard.wire` from a set of runs.

    ``control_for`` returns the control component for a board identifier, or
    ``None`` when this service holds no run for it. ``prefix`` is where the
    service mounted these paths, and is stripped before matching.
    """

    def __init__(
        self,
        control_for: Callable[[str], Control | None],
        *,
        store: BoardStore | None = None,
        prefix: str = "",
    ) -> None:
        self._control_for = control_for
        self._store = store
        self._prefix = prefix.rstrip("/")
        # Named rather than ordered, so an operation the wire adds and this
        # service has not answered raises instead of falling into another.
        self._answering: dict[str, _Answer] = {
            READ_REGIONS.name: self._read_regions,
            READ_LEVEL.name: self._read_level,
            READ_PREMISE.name: self._read_premise,
            READ_BOARD.name: self._read_board,
            WRITE.name: self._write,
            SET_PREMISE.name: self._set_premise,
            ACK.name: self._ack,
        }

    def handle(self, request: Request) -> Response:
        """Answers one request. Raises nothing the caller has to catch."""
        path = request.path
        if self._prefix:
            if not path.startswith(self._prefix + "/"):
                return _error(404, "no_such_route", f"{path} is outside this service")
            path = path[len(self._prefix) :]
        method = request.method.upper()

        matched: list[tuple[Operation, dict[str, str]]] = []
        for operation in OPERATIONS:
            variables = _match(operation.template, path)
            if variables is not None:
                matched.append((operation, variables))
        if not matched:
            return _error(404, "no_such_route", f"no operation takes {path}")

        for operation, variables in matched:
            if operation.method == method:
                return self._answer(operation, variables, request)
        allowed = ", ".join(sorted({o.method for o, _ in matched}))
        return Response(
            405,
            ErrorBody(error="wrong_method", detail=f"{path} takes {allowed}").to_json(),
            # RFC 9110 requires Allow on a 405.
            {"Allow": allowed},
        )

    def _answer(
        self, operation: Operation, variables: dict[str, str], request: Request
    ) -> Response:
        board_id = variables["board_id"]
        control = self._control_for(board_id)
        reader: BoardReader | None = None
        if control is None:
            reader = self._reader_for(board_id, operation)
            if reader is None:
                return _error(404, "unknown_board", f"this service holds no {board_id}")
        try:
            return self._act(operation, variables, request, control, reader)
        except UndeclaredRegionError as absent:
            return _error(404, "unknown_region", str(absent))
        except RegionKindError as wrong:
            return _error(404, "wrong_region_kind", str(wrong))
        except UnsetPremiseError as unset:
            return _error(404, "unset_premise", str(unset))
        except UnknownNotificationError as unknown:
            return _error(404, "unknown_notification", str(unknown))
        except IdempotencyKeyError as reused:
            # The key named another region. Sending it again cannot help.
            return _error(409, "idempotency_key_reused", str(reused))
        except RunClosedError as closed:
            return _error(410, "run_closed", str(closed))

    def _reader_for(self, board_id: str, operation: Operation) -> BoardReader | None:
        """Returns a reader over the record, for a board no run is held for.

        A read needs the record and not the run, so any replica holding the
        store can answer one. A write needs the run. A board the store never
        held returns nothing, so a mistyped identifier is not answered with an
        empty list.
        """
        if self._store is None or operation.method != "GET":
            return None
        if not self._store.read_regions(board_id):
            return None
        return reader_for(self._store, board_id)

    def _act(
        self,
        operation: Operation,
        variables: dict[str, str],
        request: Request,
        control: Control | None,
        reader: BoardReader | None,
    ) -> Response:
        answer = self._answering.get(operation.name)
        if answer is None:
            raise NotImplementedError(
                f"{operation.name} is on the wire and this service does not answer it"
            )
        bound = control.reader if control is not None else reader
        assert bound is not None
        serving = _Serving(reader=bound, control=control)
        return answer(variables, request, serving)

    def _read_regions(
        self, variables: dict[str, str], request: Request, serving: _Serving
    ) -> Response:
        regions = serving.reader.read_regions()
        return Response(200, RegionList([RegionBody.of(r) for r in regions]).to_json())

    def _read_level(
        self, variables: dict[str, str], request: Request, serving: _Serving
    ) -> Response:
        bounds = _bounds(request.query)
        if isinstance(bounds, Response):
            return bounds
        from_sequence, limit = bounds
        found = serving.reader.read_level(
            variables["level"], from_sequence, _one_more(limit)
        )
        page, more = _trim(found, limit)
        return Response(
            200,
            LevelPage(
                contributions=[
                    ContributionBody(sequence=c.sequence, content=c.content)
                    for c in page
                ],
                has_more=more,
            ).to_json(),
        )

    def _read_premise(
        self, variables: dict[str, str], request: Request, serving: _Serving
    ) -> Response:
        state = serving.reader.read_premise(variables["premise"])
        return Response(
            200, PremiseBody(version=state.version, value=state.value).to_json()
        )

    def _read_board(
        self, variables: dict[str, str], request: Request, serving: _Serving
    ) -> Response:
        bounds = _bounds(request.query)
        if isinstance(bounds, Response):
            return bounds
        from_sequence, limit = bounds
        changes = serving.reader.read_board(from_sequence, _one_more(limit))
        page, more = _trim(changes, limit)
        return Response(
            200,
            BoardPage(
                changes=[
                    BoardChangeBody(
                        sequence=c.sequence, region=c.region, content=c.content
                    )
                    for c in page
                ],
                has_more=more,
            ).to_json(),
        )

    def _write(
        self, variables: dict[str, str], request: Request, serving: _Serving
    ) -> Response:
        asked = _decode(WriteRequest, request.body)
        if isinstance(asked, Response):
            return asked
        # The path names the level, so a body disagreeing with it changes
        # nothing: a caller cannot write somewhere it did not address.
        return _outcome(
            serving.run().write(
                variables["level"],
                asked.content,
                asked.idempotency_key,
                writer=asked.writer,
            )
        )

    def _set_premise(
        self, variables: dict[str, str], request: Request, serving: _Serving
    ) -> Response:
        setting = _decode(SetPremiseRequest, request.body)
        if isinstance(setting, Response):
            return setting
        return _outcome(
            serving.run().set_premise(
                variables["premise"],
                setting.value,
                setting.expected_version,
                setting.idempotency_key,
                writer=setting.writer,
            )
        )

    def _ack(
        self, variables: dict[str, str], request: Request, serving: _Serving
    ) -> Response:
        acknowledgment = _decode(AckRequest, request.body)
        if isinstance(acknowledgment, Response):
            return acknowledgment
        serving.run().ack(acknowledgment.notification_id, agent=acknowledgment.agent)
        return Response(204)


def _match(template: str, path: str) -> dict[str, str] | None:
    """Returns the variables ``path`` fills in, or ``None`` if it does not fit."""
    wanted = template.split("/")
    given = path.split("/")
    if len(wanted) != len(given):
        return None
    variables: dict[str, str] = {}
    for expected, actual in zip(wanted, given, strict=True):
        if expected.startswith("{") and expected.endswith("}"):
            if not actual:
                return None
            variables[expected[1:-1]] = unquote(actual)
        elif expected != actual:
            return None
    return variables


def _bounds(query: Mapping[str, str]) -> tuple[int, int | None] | Response:
    """Reads the sequence bound and the maximum count, or says why it could not."""
    from_sequence = 0
    limit: int | None = None
    if FROM_SEQUENCE in query:
        read = _whole(query[FROM_SEQUENCE], FROM_SEQUENCE)
        if isinstance(read, Response):
            return read
        from_sequence = read
    if LIMIT in query:
        read = _whole(query[LIMIT], LIMIT)
        if isinstance(read, Response):
            return read
        limit = read
    return from_sequence, limit


def _whole(given: str, name: str) -> int | Response:
    try:
        number = int(given)
    except ValueError:
        return _error(400, "bad_query", f"{name} is a whole number, not {given!r}")
    if number < 0:
        return _error(400, "bad_query", f"{name} is not negative, and was {number}")
    return number


def _one_more(limit: int | None) -> int | None:
    # One past what the caller wanted answers has_more without a second read.
    return None if limit is None else limit + 1


def _trim(found: list[Any], limit: int | None) -> tuple[list[Any], bool]:
    if limit is None or len(found) <= limit:
        return found, False
    return found[:limit], True


def _decode(kind: Any, body: object) -> Any:
    from blackboard.wire import WireError

    try:
        return kind.from_json(body)
    except WireError as unreadable:
        return _error(400, "bad_body", str(unreadable))


def _outcome(result: Written | Conflict | Rejected) -> Response:
    if isinstance(result, Written):
        body = WrittenBody(
            sequence=result.sequence, version=result.version, repeated=result.repeated
        ).to_json()
        # 201 says something was created. A repeat created nothing, so a
        # client tells the two apart before it reads a body.
        return Response(200 if result.repeated else 201, body)
    if isinstance(result, Conflict):
        return Response(
            409, ConflictBody(current_version=result.current_version).to_json()
        )
    if result.cause is RejectionCause.RUN_CLOSED:
        # The run is over rather than the write being wrong, and no later
        # attempt gets a different answer.
        return _error(410, "run_closed", result.reason)
    return Response(
        422, RejectedBody(cause=result.cause.value, reason=result.reason).to_json()
    )

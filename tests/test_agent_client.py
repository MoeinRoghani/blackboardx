"""The agent's side, tested against the blackboard's side.

The client's transport is wired straight into `BoardService`, so every case
here proves the two halves agree rather than proving the client matches a
double someone wrote. Nothing opens a socket.

Every case runs twice, once through `BoardClient` and once through
`AsyncBoardClient`, so the two cannot answer differently.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest

from blackboard import (
    Agent,
    BoardReader,
    Conflict,
    Contribution,
    Control,
    InMemoryStore,
    Level,
    Premise,
    PremiseState,
    Reject,
    Rejected,
    RejectionCause,
    RunLimits,
    Written,
    create_model,
)
from blackboard.agent import (
    AsyncBoardClient,
    BoardClient,
    ProtocolError,
    UnknownBoardError,
    Unreachable,
)
from blackboard.server import BoardService, Request

httpx = pytest.importorskip("httpx")

BOARD = "board-1"
BASE = "https://blackboard.test/v1"


def build(**overrides: Any) -> Control:
    settings: dict[str, Any] = {
        "board_id": BOARD,
        "store": InMemoryStore(),
        "regions": [Level("signals"), Level("findings"), Premise("severity")],
        "premises": {"severity": "unknown"},
        "agents": [Agent(name="triage", notify=lambda notification: None)],
        "limits": RunLimits(wall_clock=timedelta(minutes=5), idle=timedelta(minutes=5)),
    }
    settings.update(overrides)
    return create_model(**settings).control


def serving(control: Control) -> Callable[[Any], Any]:
    """Answers an httpx request from a real BoardService."""
    service = BoardService(control_for={BOARD: control}.get, prefix="/v1")

    def answer(request: Any) -> Any:
        body = json.loads(request.content) if request.content else None
        reply = service.handle(
            Request(
                method=request.method,
                path=request.url.path,
                body=body,
                query=dict(request.url.params),
            )
        )
        if reply.body is None:
            return httpx.Response(reply.status, headers=dict(reply.headers))
        return httpx.Response(
            reply.status, json=reply.body, headers=dict(reply.headers)
        )

    return answer


class Sync:
    """Calls a BoardClient. The interface both kinds are exercised through."""

    kind = "sync"

    def __init__(self, handler: Callable[[Any], Any], **settings: Any) -> None:
        self._client = BoardClient(
            base_url=BASE,
            board_id=BOARD,
            agent="triage",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
            **settings,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def close(self) -> None:
        self._client.close()


class Async:
    """Calls an AsyncBoardClient on one event loop, so the pool survives."""

    kind = "async"

    def __init__(self, handler: Callable[[Any], Any], **settings: Any) -> None:
        self._loop = asyncio.new_event_loop()
        self._client = AsyncBoardClient(
            base_url=BASE,
            board_id=BOARD,
            agent="triage",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            **settings,
        )

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._client, name)
        if not callable(attribute):
            return attribute

        def call(*args: Any, **keywords: Any) -> Any:
            return self._loop.run_until_complete(attribute(*args, **keywords))

        return call

    def close(self) -> None:
        self._loop.run_until_complete(self._client.close())
        self._loop.close()


@pytest.fixture(params=[Sync, Async], ids=["sync", "async"])
def kind(request: pytest.FixtureRequest) -> Any:
    return request.param


@pytest.fixture
def control() -> Control:
    return build()


@pytest.fixture
def board(kind: Any, control: Control) -> Iterator[Any]:
    client = kind(serving(control))
    yield client
    client.close()


class TestReading:
    def test_the_declared_regions_come_back_as_declarations(self, board: Any) -> None:
        assert sorted((r.name, type(r).__name__) for r in board.read_regions()) == [
            ("findings", "Level"),
            ("severity", "Premise"),
            ("signals", "Level"),
        ]

    def test_a_level_comes_back_as_contributions(
        self, board: Any, control: Control
    ) -> None:
        control.write("signals", {"n": 1}, writer="triage")
        assert board.read_level("signals") == control.reader.read_level("signals")

    def test_a_level_is_read_to_its_end_across_pages(
        self, kind: Any, control: Control
    ) -> None:
        for n in range(7):
            control.write("signals", {"n": n}, writer="triage")

        capped = _capping(serving(control), at=2)
        client = kind(capped)
        try:
            assert [c.content for c in client.read_level("signals")] == [
                {"n": n} for n in range(7)
            ]
        finally:
            client.close()

    def test_a_limit_asks_for_one_page_and_stops(
        self, board: Any, control: Control
    ) -> None:
        for n in range(5):
            control.write("signals", {"n": n}, writer="triage")
        assert [c.content for c in board.read_level("signals", limit=2)] == [
            {"n": 0},
            {"n": 1},
        ]

    def test_a_level_is_read_from_a_sequence(
        self, board: Any, control: Control
    ) -> None:
        control.write("signals", {"n": 0}, writer="triage")
        second = control.write("signals", {"n": 1}, writer="triage")
        assert isinstance(second, Written)
        assert [c.content for c in board.read_level("signals", second.sequence)] == [
            {"n": 1}
        ]

    def test_a_premise_comes_back_as_its_state(
        self, board: Any, control: Control
    ) -> None:
        assert board.read_premise("severity") == control.reader.read_premise("severity")
        assert board.read_premise("severity") == PremiseState(
            value="unknown", version=1
        )

    def test_the_board_comes_back_as_changes_in_order(
        self, board: Any, control: Control
    ) -> None:
        control.write("signals", {"n": 1}, writer="triage")
        control.write("findings", {"n": 2}, writer="triage")
        assert board.read_board() == control.reader.read_board()

    def test_the_board_is_read_to_its_end_across_pages(
        self, kind: Any, control: Control
    ) -> None:
        for n in range(6):
            control.write("signals", {"n": n}, writer="triage")
        client = kind(_capping(serving(control), at=2))
        try:
            assert client.read_board() == control.reader.read_board()
        finally:
            client.close()

    def test_a_region_nobody_declared_raises_what_the_board_raises(
        self, board: Any
    ) -> None:
        from blackboard import UndeclaredRegionError

        with pytest.raises(UndeclaredRegionError):
            board.read_level("rumours")

    def test_reading_a_premise_as_a_level_raises_what_the_board_raises(
        self, board: Any
    ) -> None:
        from blackboard import RegionKindError

        with pytest.raises(RegionKindError):
            board.read_level("severity")


def _capping(handler: Callable[[Any], Any], at: int) -> Callable[[Any], Any]:
    """A blackboard that never answers with more than `at` rows at a time."""

    def answer(request: Any) -> Any:
        if request.url.params.get("limit") is None:
            request.url = request.url.copy_merge_params({"limit": str(at)})
        return handler(request)

    return answer


class TestWriting:
    def test_an_admitted_write_answers_the_way_control_does(
        self, board: Any, control: Control
    ) -> None:
        outcome = board.write("signals", {"n": 1})
        assert isinstance(outcome, Written)
        assert control.reader.read_level("signals") == [
            Contribution(sequence=outcome.sequence, content={"n": 1})
        ]

    def test_the_writer_is_the_agent_the_client_was_built_for(
        self, board: Any, control: Control
    ) -> None:
        board.write("signals", {"n": 1})
        from blackboard import WriteAccepted

        accepted = [e for e in control.read_audit() if isinstance(e, WriteAccepted)]
        assert accepted[-1].writer == "triage"

    def test_a_refused_write_comes_back_as_a_rejection_not_an_exception(
        self, kind: Any
    ) -> None:
        control = build(admission_rule=lambda proposed, reader: Reject("not this one"))
        client = kind(serving(control))
        try:
            outcome = client.write("signals", {"n": 1})
        finally:
            client.close()
        assert outcome == Rejected(
            cause=RejectionCause.ADMISSION, reason="not this one"
        )

    def test_a_write_to_a_level_nobody_declared_raises_what_a_read_raises(
        self, board: Any
    ) -> None:
        from blackboard import UndeclaredRegionError

        with pytest.raises(UndeclaredRegionError):
            board.write("rumours", {"n": 1})

    def test_a_write_to_a_closed_run_is_a_rejection_naming_that(
        self, board: Any, control: Control
    ) -> None:
        control.abort("the incident was stood down")
        outcome = board.write("signals", {"n": 1})
        assert isinstance(outcome, Rejected)
        assert outcome.cause is RejectionCause.RUN_CLOSED


class TestSettingAPremise:
    def test_a_set_under_the_current_version_answers_with_the_next(
        self, board: Any
    ) -> None:
        outcome = board.set_premise("severity", "high", 1)
        assert isinstance(outcome, Written)
        assert outcome.version == 2

    def test_a_stale_version_comes_back_as_a_conflict(self, board: Any) -> None:
        board.set_premise("severity", "high", 1)
        assert board.set_premise("severity", "low", 1) == Conflict(current_version=2)

    def test_a_conflict_is_answered_by_reading_and_deciding_again(
        self, board: Any
    ) -> None:
        board.set_premise("severity", "high", 1)
        conflict = board.set_premise("severity", "low", 1)
        assert isinstance(conflict, Conflict)
        current = board.read_premise("severity")
        assert isinstance(
            board.set_premise("severity", "low", current.version), Written
        )


class TestAcknowledging:
    def test_an_acknowledgment_is_recorded(self, kind: Any) -> None:
        seen: list[Any] = []
        control = build(
            agents=[
                Agent(name="triage", subscribes_to={"signals"}, notify=seen.append),
                Agent(name="source", notify=lambda n: None),
            ]
        )
        control.write("signals", {"n": 1}, writer="source")
        client = kind(serving(control))
        try:
            client.ack(seen[-1].notification_id)
        finally:
            client.close()
        from blackboard import NotificationAcknowledged

        assert any(
            isinstance(e, NotificationAcknowledged) for e in control.read_audit()
        )

    def test_a_notification_never_issued_raises_what_control_raises(
        self, board: Any
    ) -> None:
        from blackboard import UnknownNotificationError

        with pytest.raises(UnknownNotificationError):
            board.ack(99)


class TestWhenTheBlackboardIsNotThere:
    def test_a_board_it_does_not_hold_is_named(self, kind: Any) -> None:
        service = BoardService(control_for=lambda board_id: None, prefix="/v1")

        def answer(request: Any) -> Any:
            reply = service.handle(
                Request(method=request.method, path=request.url.path)
            )
            return httpx.Response(reply.status, json=reply.body)

        client = kind(answer)
        try:
            with pytest.raises(UnknownBoardError):
                client.read_regions()
        finally:
            client.close()

    def test_a_connection_that_fails_is_tried_again_then_raised(
        self, kind: Any
    ) -> None:
        tries: list[int] = []

        def refuse(request: Any) -> Any:
            tries.append(1)
            raise httpx.ConnectError("connection refused")

        client = kind(refuse, attempts=3, backoff=lambda attempt, after: 0.0)
        try:
            with pytest.raises(Unreachable):
                client.read_regions()
        finally:
            client.close()
        assert len(tries) == 3

    def test_a_read_that_meets_a_503_is_tried_again(
        self, kind: Any, control: Control
    ) -> None:
        answers = serving(control)
        tries: list[int] = []

        def flaky(request: Any) -> Any:
            tries.append(1)
            if len(tries) < 3:
                return httpx.Response(503)
            return answers(request)

        client = kind(flaky, attempts=3, backoff=lambda attempt, after: 0.0)
        try:
            assert client.read_regions() != []
        finally:
            client.close()
        assert len(tries) == 3

    def test_a_write_is_never_sent_twice(self, kind: Any) -> None:
        """A retried write would append the contribution twice."""
        tries: list[int] = []

        def refuse(request: Any) -> Any:
            tries.append(1)
            raise httpx.ConnectError("connection refused")

        client = kind(refuse, attempts=5, backoff=lambda attempt, after: 0.0)
        try:
            with pytest.raises(Unreachable):
                client.write("signals", {"n": 1})
        finally:
            client.close()
        assert len(tries) == 1

    def test_a_premise_is_never_set_twice(self, kind: Any) -> None:
        tries: list[int] = []

        def unavailable(request: Any) -> Any:
            tries.append(1)
            return httpx.Response(503)

        client = kind(unavailable, attempts=5, backoff=lambda attempt, after: 0.0)
        try:
            with pytest.raises(Unreachable):
                client.set_premise("severity", "high", 1)
        finally:
            client.close()
        assert len(tries) == 1

    def test_a_rejection_is_never_sent_again(self, kind: Any) -> None:
        """422 is an answer, so trying again gets the same one."""
        control = build(admission_rule=lambda proposed, reader: Reject("no"))
        answers = serving(control)
        tries: list[int] = []

        def counted(request: Any) -> Any:
            tries.append(1)
            return answers(request)

        client = kind(counted, attempts=5, backoff=lambda attempt, after: 0.0)
        try:
            assert isinstance(client.write("signals", {"n": 1}), Rejected)
        finally:
            client.close()
        assert len(tries) == 1

    def test_retry_after_is_the_wait_the_client_takes(
        self, kind: Any, control: Control
    ) -> None:
        asked: list[float | None] = []
        answers = serving(control)
        tries: list[int] = []

        def busy(request: Any) -> Any:
            tries.append(1)
            if len(tries) == 1:
                return httpx.Response(429, headers={"Retry-After": "12"})
            return answers(request)

        def record(attempt: int, retry_after: float | None) -> float:
            asked.append(retry_after)
            return 0.0

        client = kind(busy, attempts=3, backoff=record)
        try:
            client.read_regions()
        finally:
            client.close()
        assert asked == [12.0]

    def test_an_answer_that_is_not_json_is_a_protocol_error(self, kind: Any) -> None:
        client = kind(lambda request: httpx.Response(200, content=b"<html>"))
        try:
            with pytest.raises(ProtocolError):
                client.read_regions()
        finally:
            client.close()

    def test_a_status_no_operation_produces_is_a_protocol_error(
        self, kind: Any
    ) -> None:
        client = kind(lambda request: httpx.Response(418, json={"error": "teapot"}))
        try:
            with pytest.raises(ProtocolError, match="418"):
                client.read_regions()
        finally:
            client.close()


class TestTheSurface:
    def test_a_client_is_a_board_reader(self, control: Control) -> None:
        """An admission rule written against BoardReader reads a remote board."""
        client = BoardClient(
            base_url=BASE,
            board_id=BOARD,
            agent="triage",
            http_client=httpx.Client(transport=httpx.MockTransport(serving(control))),
        )
        # mypy checks this line; the assertions check that it works.
        reader: BoardReader = client
        control.write("signals", {"n": 1}, writer="triage")
        assert [r.name for r in reader.read_regions()] != []
        assert reader.read_level("signals") == control.reader.read_level("signals")
        assert reader.read_premise("severity") == control.reader.read_premise(
            "severity"
        )
        assert reader.read_board() == control.reader.read_board()
        client.close()

    def test_attempts_below_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            BoardClient(base_url=BASE, board_id=BOARD, agent="triage", attempts=0)

    def test_a_supplied_http_client_is_not_closed_by_the_board_client(
        self, control: Control
    ) -> None:
        supplied = httpx.Client(transport=httpx.MockTransport(serving(control)))
        with BoardClient(
            base_url=BASE, board_id=BOARD, agent="triage", http_client=supplied
        ) as board:
            board.read_regions()
        assert not supplied.is_closed
        supplied.close()

    def test_the_base_url_may_carry_a_trailing_slash(self, control: Control) -> None:
        board = BoardClient(
            base_url=BASE + "/",
            board_id=BOARD,
            agent="triage",
            http_client=httpx.Client(transport=httpx.MockTransport(serving(control))),
        )
        assert board.read_regions() != []
        board.close()

    def test_both_clients_carry_the_same_methods(self) -> None:
        public = {
            name
            for name in dir(BoardClient)
            if not name.startswith("_") and name != "close"
        }
        assert public <= {n for n in dir(AsyncBoardClient) if not n.startswith("_")}

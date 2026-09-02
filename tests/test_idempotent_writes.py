"""A key sent by an agent reaches the row, and a repeat is not an event."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest

from blackboard import (
    Agent,
    Control,
    InMemoryStore,
    Level,
    ManualClock,
    Premise,
    Rejected,
    RejectionCause,
    RunLimits,
    WriteAccepted,
    Written,
    create_model,
)
from blackboard.agent import BoardClient, Unreachable
from blackboard.server import BoardService, Request
from blackboard.wire import WRITE, WrittenBody

httpx = pytest.importorskip("httpx")

BOARD = "board-1"
BASE = "https://blackboard.test/v1"
LIMITS = RunLimits(wall_clock=timedelta(minutes=5), idle=timedelta(minutes=5))


def build(**overrides: Any) -> Control:
    settings: dict[str, Any] = {
        "board_id": BOARD,
        "store": InMemoryStore(),
        "regions": [Level("signals"), Level("findings"), Premise("severity")],
        "premises": {"severity": "unknown"},
        "agents": [Agent(name="triage", notify=lambda notification: None)],
        "limits": LIMITS,
    }
    settings.update(overrides)
    return create_model(**settings).control


class TestTheControlComponent:
    def test_a_key_writes_once(self) -> None:
        control = build()
        first = control.write("triage", "signals", {"n": 1}, "k1")
        again = control.write("triage", "signals", {"n": 1}, "k1")
        assert isinstance(first, Written)
        assert again == Written(sequence=first.sequence, repeated=True)
        assert len(control.reader.read_level("signals")) == 1

    def test_a_repeat_is_absent_from_the_audit(self) -> None:
        control = build()
        control.write("triage", "signals", {"n": 1}, "k1")
        control.write("triage", "signals", {"n": 1}, "k1")
        accepted = [e for e in control.read_audit() if isinstance(e, WriteAccepted)]
        assert len(accepted) == 1

    def test_a_repeat_wakes_nobody(self) -> None:
        woken: list[Any] = []
        control = build(
            agents=[
                Agent(name="triage", subscribes_to={"signals"}, notify=woken.append),
                Agent(name="source", notify=lambda n: None),
            ]
        )
        before = len(woken)
        control.write("source", "signals", {"n": 1}, "k1")
        after_first = len(woken)
        control.write("source", "signals", {"n": 1}, "k1")
        assert after_first > before
        assert len(woken) == after_first

    def test_a_key_reused_for_another_region_is_a_rejection(self) -> None:
        control = build()
        control.write("triage", "signals", {"n": 1}, "k1")
        outcome = control.write("triage", "findings", {"n": 1}, "k1")
        assert isinstance(outcome, Rejected)
        assert outcome.cause is RejectionCause.IDEMPOTENCY_KEY_REUSED

    def test_a_premise_is_set_once_under_one_key(self) -> None:
        control = build()
        first = control.set_premise("triage", "severity", "high", 1, "k1")
        assert isinstance(first, Written)
        again = control.set_premise("triage", "severity", "low", 1, "k1")
        assert again == Written(
            sequence=first.sequence, version=first.version, repeated=True
        )
        assert control.reader.read_premise("severity").value == "high"

    def test_a_repeat_does_not_push_the_idle_deadline_out(self) -> None:
        clock = ManualClock(start=None)
        control = build(
            clock=clock,
            limits=RunLimits(
                wall_clock=timedelta(minutes=5), idle=timedelta(seconds=10)
            ),
        )
        control.write("triage", "signals", {"n": 1}, "k1")
        clock.advance(timedelta(seconds=9))
        control.write("triage", "signals", {"n": 1}, "k1")
        clock.advance(timedelta(seconds=2))
        assert control.outcome() is not None


def serving(control: Control) -> Callable[[Any], Any]:
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


class TestTheBlackboardsAnswer:
    def request(self, service: BoardService, key: str) -> Any:
        return service.handle(
            Request(
                method="POST",
                path=WRITE.path(board_id=BOARD, level="signals"),
                body={
                    "writer": "triage",
                    "level": "signals",
                    "content": {"n": 1},
                    "idempotency_key": key,
                },
            )
        )

    def test_a_first_write_is_created_and_a_repeat_is_not(self) -> None:
        control = build()
        service = BoardService(control_for={BOARD: control}.get)
        first = self.request(service, "k1")
        again = self.request(service, "k1")
        assert first.status == 201
        assert again.status == 200
        assert WrittenBody.from_json(again.body).repeated is True
        assert len(control.reader.read_level("signals")) == 1

    def test_a_reused_key_is_refused_the_way_any_write_is(self) -> None:
        control = build()
        service = BoardService(control_for={BOARD: control}.get)
        self.request(service, "k1")
        answer = service.handle(
            Request(
                method="POST",
                path=WRITE.path(board_id=BOARD, level="findings"),
                body={
                    "writer": "triage",
                    "level": "findings",
                    "content": {"n": 1},
                    "idempotency_key": "k1",
                },
            )
        )
        assert answer.status == 422
        assert answer.body is not None
        assert answer.body["cause"] == "idempotency_key_reused"


class TestTheAgentsClient:
    def client(self, handler: Callable[[Any], Any]) -> BoardClient:
        return BoardClient(
            base_url=BASE,
            board_id=BOARD,
            agent="triage",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
            attempts=3,
            backoff=lambda attempt, after: 0.0,
        )

    def test_a_key_reaches_the_row(self) -> None:
        control = build()
        with self.client(serving(control)) as board:
            board.write("signals", {"n": 1}, idempotency_key="k1")
            board.write("signals", {"n": 1}, idempotency_key="k1")
        assert len(control.reader.read_level("signals")) == 1

    def test_a_repeat_comes_back_marked(self) -> None:
        control = build()
        with self.client(serving(control)) as board:
            board.write("signals", {"n": 1}, idempotency_key="k1")
            again = board.write("signals", {"n": 1}, idempotency_key="k1")
        assert again == Written(sequence=2, repeated=True)

    def test_a_write_without_a_key_is_never_sent_twice(self) -> None:
        sent: list[int] = []

        def drop(request: Any) -> Any:
            sent.append(1)
            raise httpx.ConnectError("connection refused")

        with self.client(drop) as board, pytest.raises(Unreachable):
            board.write("signals", {"n": 1})
        assert len(sent) == 1

    def test_a_write_with_a_key_is_sent_again(self) -> None:
        control = build()
        answers = serving(control)
        sent: list[int] = []

        def flaky(request: Any) -> Any:
            sent.append(1)
            if len(sent) == 1:
                raise httpx.ConnectError("connection refused")
            return answers(request)

        with self.client(flaky) as board:
            outcome = board.write("signals", {"n": 1}, idempotency_key="k1")
        assert len(sent) == 2
        assert isinstance(outcome, Written)
        assert len(control.reader.read_level("signals")) == 1

    def test_a_write_whose_answer_was_lost_is_written_once(self) -> None:
        """The blackboard took the first attempt; the answer never arrived."""
        control = build()
        answers = serving(control)
        sent: list[int] = []

        def swallow(request: Any) -> Any:
            sent.append(1)
            reply = answers(request)
            if len(sent) == 1:
                raise httpx.ReadTimeout("the answer never came")
            return reply

        with self.client(swallow) as board:
            outcome = board.write("signals", {"n": 1}, idempotency_key="k1")
        assert outcome == Written(sequence=2, repeated=True)
        assert len(control.reader.read_level("signals")) == 1

    def test_a_premise_set_with_a_key_is_sent_again(self) -> None:
        control = build()
        answers = serving(control)
        sent: list[int] = []

        def flaky(request: Any) -> Any:
            sent.append(1)
            if len(sent) == 1:
                return httpx.Response(503)
            return answers(request)

        with self.client(flaky) as board:
            outcome = board.set_premise("severity", "high", 1, idempotency_key="k1")
        assert isinstance(outcome, Written)
        assert control.reader.read_premise("severity").value == "high"

    def test_a_reused_key_reaches_the_agent_as_a_rejection(self) -> None:
        control = build()
        with self.client(serving(control)) as board:
            board.write("signals", {"n": 1}, idempotency_key="k1")
            outcome = board.write("findings", {"n": 1}, idempotency_key="k1")
        assert isinstance(outcome, Rejected)
        assert outcome.cause is RejectionCause.IDEMPOTENCY_KEY_REUSED

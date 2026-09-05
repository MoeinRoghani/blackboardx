"""One agent body, run in process and over HTTP, with no edit between.

`AgentBoard` is what an agent is written against. `Control.as_agent` returns
it in process and `BoardClient` satisfies it over HTTP, so this file writes
the body once and runs it twice.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest

from blackboard import (
    Agent,
    AgentBoard,
    Conflict,
    Control,
    InMemoryStore,
    Level,
    Premise,
    RunLimits,
    Written,
    create_model,
)
from blackboard.agent import BoardClient
from blackboard.server import BoardService, Request

httpx = pytest.importorskip("httpx")

BOARD = "incident-1"
LIMITS = RunLimits(wall_clock=timedelta(minutes=5), idle=timedelta(minutes=5))


def investigate(board: AgentBoard, from_sequence: int) -> None:
    """The agent's body. Written once, against the protocol, and never edited."""
    severity = board.read_premise("severity")
    for signal in board.read_level("signals", from_sequence):
        board.write("findings", {"saw": signal.content, "at": severity.value})
    outcome = board.set_premise("severity", "high", severity.version)
    if isinstance(outcome, Conflict):
        board.set_premise("severity", "high", outcome.current_version)


def a_run() -> Control:
    model = create_model(
        board_id=BOARD,
        store=InMemoryStore(),
        regions=[Level("signals"), Level("findings"), Premise("severity")],
        premises={"severity": "unknown"},
        agents=[Agent(name="triage", notify=lambda n: None)],
        limits=LIMITS,
    )
    model.control.write("signals", {"host": "web-3"}, writer="triage")
    return model.control


def over_http(control: Control) -> BoardClient:
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
            return httpx.Response(reply.status)
        return httpx.Response(reply.status, json=reply.body)

    return BoardClient(
        base_url="https://blackboard.test/v1",
        board_id=BOARD,
        agent="triage",
        http_client=httpx.Client(transport=httpx.MockTransport(answer)),
    )


def test_the_same_body_lands_the_same_contributions_either_way() -> None:
    in_process = a_run()
    investigate(in_process.as_agent("triage"), 0)

    remote_control = a_run()
    with over_http(remote_control) as remote:
        investigate(remote, 0)

    assert [c.content for c in in_process.reader.read_level("findings")] == [
        c.content for c in remote_control.reader.read_level("findings")
    ]
    here = in_process.reader.read_premise("severity")
    there = remote_control.reader.read_premise("severity")
    # Two runs, so two instants. The body landed the same value and version.
    assert (here.value, here.version) == (there.value, there.version)


def test_both_satisfy_the_protocol() -> None:
    """mypy checks these two lines; the assertions check they work."""
    control = a_run()
    local: AgentBoard = control.as_agent("triage")
    assert local.board_id == BOARD
    assert local.read_regions() != []
    with over_http(a_run()) as client:
        remote: AgentBoard = client
        assert remote.board_id == BOARD
        assert remote.read_regions() != []


class TestTheInProcessObject:
    def test_it_writes_as_the_agent_it_names(self) -> None:
        control = a_run()
        control.as_agent("netops").write("findings", {"n": 1})
        from blackboard import WriteAccepted

        accepted = [e for e in control.read_audit() if isinstance(e, WriteAccepted)]
        assert accepted[-1].writer == "netops"

    def test_it_acknowledges_as_the_agent_it_names(self) -> None:
        seen: list[Any] = []
        model = create_model(
            board_id=BOARD,
            store=InMemoryStore(),
            regions=[Level("signals")],
            premises={},
            agents=[
                Agent(name="triage", subscribes_to={"signals"}, notify=seen.append),
                Agent(name="src", notify=lambda n: None),
            ],
            limits=LIMITS,
        )
        model.control.write("signals", {"n": 1}, writer="src")
        model.control.as_agent("triage").ack(seen[-1].notification_id)
        from blackboard import NotificationAcknowledged

        assert any(
            isinstance(e, NotificationAcknowledged) for e in model.control.read_audit()
        )

    def test_an_agent_need_not_be_registered_to_write_as_itself(self) -> None:
        control = a_run()
        assert isinstance(control.as_agent("passer-by").write("findings", {}), Written)


class TestTheClientSpellingIsNoLongerSilent:
    def test_the_remote_write_order_raises_against_a_control(self) -> None:
        """It used to type-check and file the write under the wrong name."""
        control = a_run()
        with pytest.raises(TypeError, match="writer"):
            control.write("findings", {"n": 1}, "triage-1-0")  # type: ignore[call-arg]

    def test_the_remote_ack_order_raises_against_a_control(self) -> None:
        control = a_run()
        with pytest.raises(TypeError, match="agent"):
            control.ack(1)  # type: ignore[call-arg]

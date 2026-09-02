"""A body carries what the path cannot, and a page over HTTP has a size."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest

from blackboard import Agent, InMemoryStore, Level, Premise, RunLimits, create_model
from blackboard.server import BoardService, Request
from blackboard.wire import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    READ_LEVEL,
    LevelPage,
    NotificationBody,
    SetPremiseRequest,
    WireError,
    WriteRequest,
)

BOARD = "b"
LIMITS = RunLimits(wall_clock=timedelta(minutes=5), idle=timedelta(minutes=5))


class TestABodyCarriesWhatThePathCannot:
    def test_a_write_with_no_content_is_refused(self) -> None:
        with pytest.raises(WireError, match="content"):
            WriteRequest.from_json({"writer": "a"})

    def test_a_write_carrying_null_content_is_legal(self) -> None:
        assert WriteRequest.from_json({"writer": "a", "content": None}).content is None

    def test_a_write_need_not_repeat_the_level_the_path_names(self) -> None:
        assert WriteRequest.from_json({"writer": "a", "content": 1}).level == ""

    def test_a_premise_set_with_no_value_is_refused(self) -> None:
        with pytest.raises(WireError, match="value"):
            SetPremiseRequest.from_json({"writer": "a", "expected_version": 1})

    def test_a_premise_set_to_null_is_legal(self) -> None:
        asked = SetPremiseRequest.from_json(
            {"writer": "a", "expected_version": 1, "value": None}
        )
        assert asked.value is None
        assert asked.premise == ""

    @pytest.mark.parametrize("lost", ["from_sequence", "to_sequence"])
    def test_a_notification_missing_a_bound_is_refused(self, lost: str) -> None:
        body = {
            "board_id": BOARD,
            "notification_id": 1,
            "agent": "a",
            "from_sequence": 4,
            "to_sequence": 9,
        }
        del body[lost]
        with pytest.raises(WireError, match=lost):
            NotificationBody.from_json(body)


def a_service(rows: int) -> BoardService:
    model = create_model(
        board_id=BOARD,
        store=InMemoryStore(),
        regions=[Level("signals"), Premise("severity")],
        premises={"severity": "high"},
        agents=[Agent(name="src", notify=lambda n: None)],
        limits=LIMITS,
    )
    for n in range(rows):
        model.control.write("signals", {"n": n}, writer="src")
    return BoardService(control_for={BOARD: model.control}.get)


class TestAPageHasASize:
    def test_a_read_with_no_limit_answers_a_page_not_a_level(self) -> None:
        answer = a_service(DEFAULT_LIMIT + 5).handle(
            Request(method="GET", path=READ_LEVEL.path(board_id=BOARD, level="signals"))
        )
        page = LevelPage.from_json(answer.body)
        assert len(page.contributions) == DEFAULT_LIMIT
        assert page.has_more is True

    def test_a_level_smaller_than_a_page_says_there_is_no_more(self) -> None:
        answer = a_service(3).handle(
            Request(method="GET", path=READ_LEVEL.path(board_id=BOARD, level="signals"))
        )
        page = LevelPage.from_json(answer.body)
        assert len(page.contributions) == 3
        assert page.has_more is False

    def test_a_limit_above_the_maximum_is_capped(self) -> None:
        answer = a_service(3).handle(
            Request(
                method="GET",
                path=READ_LEVEL.path(board_id=BOARD, level="signals"),
                query={"limit": str(MAX_LIMIT + 500)},
            )
        )
        assert answer.status == 200
        assert len(LevelPage.from_json(answer.body).contributions) == 3

    def test_a_smaller_limit_is_still_honoured(self) -> None:
        answer = a_service(50).handle(
            Request(
                method="GET",
                path=READ_LEVEL.path(board_id=BOARD, level="signals"),
                query={"limit": "5"},
            )
        )
        assert len(LevelPage.from_json(answer.body).contributions) == 5


def test_the_client_reads_a_level_larger_than_a_page() -> None:
    """The cap is silent, so the cursor carries the reader past it."""
    httpx = pytest.importorskip("httpx")
    from blackboard.agent import BoardClient

    service = a_service(DEFAULT_LIMIT * 2 + 7)

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
        return httpx.Response(reply.status, json=reply.body)

    with BoardClient(
        base_url="https://b.test",
        board_id=BOARD,
        agent="a",
        http_client=httpx.Client(transport=httpx.MockTransport(answer)),
    ) as board:
        assert len(board.read_level("signals")) == DEFAULT_LIMIT * 2 + 7

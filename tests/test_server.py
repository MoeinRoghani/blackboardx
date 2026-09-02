"""The receiving half: an agent's request turned into a call on `Control`.

Nothing here runs an HTTP server. `BoardService.handle` takes a method, a
path, and a decoded body, which is what a route in any framework already
holds by the time it calls into the library.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from blackboard import (
    Agent,
    Control,
    InMemoryStore,
    Level,
    Premise,
    Reject,
    RunLimits,
    create_model,
)
from blackboard.server import BoardService, Request, Response
from blackboard.wire import (
    ACK,
    READ_BOARD,
    READ_LEVEL,
    READ_PREMISE,
    READ_REGIONS,
    SET_PREMISE,
    WRITE,
    ErrorBody,
    LevelPage,
    PremiseBody,
    RegionList,
)

BOARD = "board-1"


@pytest.fixture
def control() -> Control:
    model = create_model(
        board_id=BOARD,
        store=InMemoryStore(),
        regions=[Level("signals"), Level("findings"), Premise("severity")],
        premises={"severity": "unknown"},
        agents=[Agent(name="triage", notify=lambda notification: None)],
        limits=RunLimits(wall_clock=timedelta(minutes=5), idle=timedelta(minutes=5)),
    )
    return model.control


@pytest.fixture
def service(control: Control) -> BoardService:
    return BoardService(control_for={BOARD: control}.get)


def get(service: BoardService, path: str, **query: str) -> Response:
    return service.handle(Request(method="GET", path=path, query=query))


class TestReading:
    def test_the_declared_regions_come_back_with_their_kinds(
        self, service: BoardService
    ) -> None:
        answer = get(service, READ_REGIONS.path(board_id=BOARD))
        assert answer.status == 200
        regions = RegionList.from_json(answer.body)
        assert {r.name: r.kind for r in regions.regions} == {
            "signals": "level",
            "findings": "level",
            "severity": "premise",
        }

    def test_a_level_comes_back_as_a_page(
        self, service: BoardService, control: Control
    ) -> None:
        control.write("signals", {"n": 1}, writer="triage")
        answer = get(service, READ_LEVEL.path(board_id=BOARD, level="signals"))
        assert answer.status == 200
        page = LevelPage.from_json(answer.body)
        assert [c.content for c in page.contributions] == [{"n": 1}]
        assert page.has_more is False

    def test_a_level_read_is_bounded_and_says_there_is_more(
        self, service: BoardService, control: Control
    ) -> None:
        for n in range(5):
            control.write("signals", {"n": n}, writer="triage")
        answer = get(
            service, READ_LEVEL.path(board_id=BOARD, level="signals"), limit="2"
        )
        page = LevelPage.from_json(answer.body)
        assert [c.content for c in page.contributions] == [{"n": 0}, {"n": 1}]
        assert page.has_more is True

    def test_a_level_read_continues_from_a_sequence(
        self, service: BoardService, control: Control
    ) -> None:
        for n in range(3):
            control.write("signals", {"n": n}, writer="triage")
        first = LevelPage.from_json(
            get(
                service, READ_LEVEL.path(board_id=BOARD, level="signals"), limit="1"
            ).body
        )
        after = first.contributions[-1].sequence + 1
        rest = LevelPage.from_json(
            get(
                service,
                READ_LEVEL.path(board_id=BOARD, level="signals"),
                from_sequence=str(after),
            ).body
        )
        assert [c.content for c in rest.contributions] == [{"n": 1}, {"n": 2}]

    def test_a_premise_comes_back_with_its_version(self, service: BoardService) -> None:
        answer = get(service, READ_PREMISE.path(board_id=BOARD, premise="severity"))
        assert answer.status == 200
        premise = PremiseBody.from_json(answer.body)
        assert premise.value == "unknown"
        assert premise.version == 1

    def test_the_board_comes_back_as_changes_in_order(
        self, service: BoardService, control: Control
    ) -> None:
        control.write("signals", {"n": 1}, writer="triage")
        control.write("findings", {"n": 2}, writer="triage")
        answer = get(service, READ_BOARD.path(board_id=BOARD))
        assert answer.status == 200
        changes = [c.region for c in _board_page(answer).changes]
        assert changes[-2:] == ["signals", "findings"]

    def test_a_level_nobody_declared_is_not_found(self, service: BoardService) -> None:
        answer = get(service, READ_LEVEL.path(board_id=BOARD, level="rumours"))
        assert answer.status == 404
        assert ErrorBody.from_json(answer.body).error == "unknown_region"

    def test_reading_a_premise_as_a_level_says_so(self, service: BoardService) -> None:
        answer = get(service, READ_LEVEL.path(board_id=BOARD, level="severity"))
        assert answer.status == 404
        assert ErrorBody.from_json(answer.body).error == "wrong_region_kind"

    def test_a_limit_that_is_not_a_number_is_refused(
        self, service: BoardService
    ) -> None:
        answer = get(
            service, READ_LEVEL.path(board_id=BOARD, level="signals"), limit="soon"
        )
        assert answer.status == 400
        assert ErrorBody.from_json(answer.body).error == "bad_query"


def _board_page(answer: Response) -> Any:
    from blackboard.wire import BoardPage

    return BoardPage.from_json(answer.body)


class TestWriting:
    def test_an_admitted_write_answers_with_its_sequence(
        self, service: BoardService, control: Control
    ) -> None:
        answer = service.handle(
            Request(
                method="POST",
                path=WRITE.path(board_id=BOARD, level="signals"),
                body={"writer": "triage", "level": "signals", "content": {"n": 1}},
            )
        )
        assert answer.status == 201
        assert answer.body is not None
        stored = control.reader.read_level("signals")
        assert [c.sequence for c in stored] == [answer.body["sequence"]]

    def test_the_level_in_the_path_is_the_one_written(
        self, service: BoardService, control: Control
    ) -> None:
        service.handle(
            Request(
                method="POST",
                path=WRITE.path(board_id=BOARD, level="signals"),
                body={"writer": "triage", "level": "findings", "content": {"n": 1}},
            )
        )
        assert control.reader.read_level("signals") != []
        assert control.reader.read_level("findings") == []

    def test_a_write_to_a_level_nobody_declared_is_refused_not_crashed(
        self, service: BoardService
    ) -> None:
        answer = service.handle(
            Request(
                method="POST",
                path=WRITE.path(board_id=BOARD, level="rumours"),
                body={"writer": "triage", "level": "rumours", "content": {}},
            )
        )
        assert answer.status == 422
        assert answer.body is not None
        assert answer.body["cause"] == "undeclared_region"

    def test_a_write_the_admission_rule_refused_is_unprocessable(self) -> None:
        model = create_model(
            board_id=BOARD,
            store=InMemoryStore(),
            regions=[Level("signals")],
            premises={},
            agents=[],
            admission_rule=lambda proposed, reader: Reject("not this one"),
            limits=RunLimits(
                wall_clock=timedelta(minutes=5), idle=timedelta(minutes=5)
            ),
        )
        service = BoardService(control_for={BOARD: model.control}.get)
        answer = service.handle(
            Request(
                method="POST",
                path=WRITE.path(board_id=BOARD, level="signals"),
                body={"writer": "triage", "level": "signals", "content": {}},
            )
        )
        assert answer.status == 422
        assert answer.body is not None
        assert answer.body["cause"] == "admission"
        assert answer.body["reason"] == "not this one"

    def test_a_body_missing_a_field_is_refused(self, service: BoardService) -> None:
        answer = service.handle(
            Request(
                method="POST",
                path=WRITE.path(board_id=BOARD, level="signals"),
                body={"content": {"n": 1}},
            )
        )
        assert answer.status == 400
        assert ErrorBody.from_json(answer.body).error == "bad_body"

    def test_a_body_that_is_not_an_object_is_refused(
        self, service: BoardService
    ) -> None:
        answer = service.handle(
            Request(
                method="POST",
                path=WRITE.path(board_id=BOARD, level="signals"),
                body=["writer"],
            )
        )
        assert answer.status == 400


class TestSettingAPremise:
    def path(self) -> str:
        return SET_PREMISE.path(board_id=BOARD, premise="severity")

    def set(self, service: BoardService, version: int, value: object) -> Response:
        return service.handle(
            Request(
                method="PUT",
                path=self.path(),
                body={
                    "writer": "triage",
                    "premise": "severity",
                    "expected_version": version,
                    "value": value,
                },
            )
        )

    def test_a_set_under_the_current_version_answers_with_the_next_one(
        self, service: BoardService
    ) -> None:
        answer = self.set(service, 1, "high")
        assert answer.status == 201
        assert answer.body is not None
        assert answer.body["version"] == 2

    def test_a_stale_version_conflicts_and_names_the_current_one(
        self, service: BoardService
    ) -> None:
        self.set(service, 1, "high")
        answer = self.set(service, 1, "low")
        assert answer.status == 409
        assert answer.body is not None
        assert answer.body["current_version"] == 2

    def test_the_premise_in_the_path_is_the_one_set(
        self, service: BoardService, control: Control
    ) -> None:
        service.handle(
            Request(
                method="PUT",
                path=self.path(),
                body={
                    "writer": "triage",
                    "premise": "signals",
                    "expected_version": 1,
                    "value": "high",
                },
            )
        )
        assert control.reader.read_premise("severity").value == "high"


class TestAcknowledging:
    def test_an_acknowledgment_is_taken(self) -> None:
        notifications: list[Any] = []
        model = create_model(
            board_id=BOARD,
            store=InMemoryStore(),
            regions=[Level("signals")],
            premises={},
            agents=[
                Agent(
                    name="triage",
                    subscribes_to={"signals"},
                    notify=notifications.append,
                ),
                Agent(name="source", notify=lambda n: None),
            ],
            limits=RunLimits(
                wall_clock=timedelta(minutes=5), idle=timedelta(minutes=5)
            ),
        )
        service = BoardService(control_for={BOARD: model.control}.get)
        model.control.write("signals", {"n": 1}, writer="source")
        answer = service.handle(
            Request(
                method="POST",
                path=ACK.path(board_id=BOARD),
                body={
                    "agent": "triage",
                    "notification_id": int(notifications[-1].notification_id),
                },
            )
        )
        assert answer.status == 204
        assert answer.body is None

    def test_a_notification_that_was_never_issued_is_not_found(
        self, service: BoardService
    ) -> None:
        answer = service.handle(
            Request(
                method="POST",
                path=ACK.path(board_id=BOARD),
                body={"agent": "triage", "notification_id": 99},
            )
        )
        assert answer.status == 404
        assert ErrorBody.from_json(answer.body).error == "unknown_notification"


class TestRouting:
    def test_a_board_the_service_does_not_hold_is_not_found(
        self, service: BoardService
    ) -> None:
        answer = get(service, READ_REGIONS.path(board_id="board-9"))
        assert answer.status == 404
        assert ErrorBody.from_json(answer.body).error == "unknown_board"

    def test_a_path_no_operation_takes_is_not_found(
        self, service: BoardService
    ) -> None:
        assert get(service, "/boards/board-1/rumours").status == 404

    def test_a_known_path_with_the_wrong_method_says_which_are_allowed(
        self, service: BoardService
    ) -> None:
        answer = service.handle(
            Request(method="DELETE", path=READ_LEVEL.path(board_id=BOARD, level="s"))
        )
        assert answer.status == 405
        assert answer.headers["Allow"] == "GET, POST"

    def test_a_service_mounted_under_a_prefix_answers_under_it(
        self, control: Control
    ) -> None:
        service = BoardService(control_for={BOARD: control}.get, prefix="/v1")
        assert get(service, "/v1" + READ_REGIONS.path(board_id=BOARD)).status == 200
        assert get(service, READ_REGIONS.path(board_id=BOARD)).status == 404

    def test_a_board_id_with_a_slash_in_it_survives_the_round_trip(self) -> None:
        odd = "tenant/4471"
        model = create_model(
            board_id=odd,
            store=InMemoryStore(),
            regions=[Level("signals")],
            premises={},
            agents=[],
            limits=RunLimits(
                wall_clock=timedelta(minutes=5), idle=timedelta(minutes=5)
            ),
        )
        service = BoardService(control_for={odd: model.control}.get)
        answer = get(service, READ_REGIONS.path(board_id=odd))
        assert answer.status == 200

    def test_a_method_is_matched_whatever_its_case(self, service: BoardService) -> None:
        answer = service.handle(
            Request(method="get", path=READ_REGIONS.path(board_id=BOARD))
        )
        assert answer.status == 200


class TestAClosedRun:
    def test_writing_to_a_closed_run_is_gone(self, control: Control) -> None:
        service = BoardService(control_for={BOARD: control}.get)
        control.abort("the incident was stood down")
        answer = service.handle(
            Request(
                method="POST",
                path=WRITE.path(board_id=BOARD, level="signals"),
                body={"writer": "triage", "level": "signals", "content": {}},
            )
        )
        assert answer.status == 410
        assert ErrorBody.from_json(answer.body).error == "run_closed"

    def test_reading_a_closed_run_still_works(self, control: Control) -> None:
        service = BoardService(control_for={BOARD: control}.get)
        control.write("signals", {"n": 1}, writer="triage")
        control.abort("the incident was stood down")
        answer = get(service, READ_LEVEL.path(board_id=BOARD, level="signals"))
        assert answer.status == 200


def test_every_operation_the_wire_names_is_answered(service: BoardService) -> None:
    """An operation added to the wire fails here until the service answers it."""
    from blackboard.wire import OPERATIONS

    for operation in OPERATIONS:
        fill = {
            name: BOARD if name == "board_id" else "signals"
            for name in operation.variables
        }
        answer = service.handle(
            Request(method=operation.method, path=operation.path(**fill), body={})
        )
        assert answer.body is None or "no_such_route" not in str(answer.body)


def test_an_operation_the_service_does_not_answer_is_not_mistaken_for_another(
    service: BoardService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A silent fall-through would record an acknowledgment for a read."""
    from blackboard import server as server_module
    from blackboard import wire

    later = wire.Operation("read_audit", "GET", "/boards/{board_id}/audit")
    monkeypatch.setattr(
        server_module, "OPERATIONS", (*wire.OPERATIONS, later), raising=True
    )
    with pytest.raises(NotImplementedError, match="read_audit"):
        service.handle(Request(method="GET", path=later.path(board_id=BOARD)))

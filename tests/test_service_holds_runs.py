"""A service holds many runs, and the library helps at both ends of one.

A read is answerable from the record by any replica holding the store. The
caller is told when a run opens, before any agent is woken, and when it closes.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from blackboard import (
    Aborted,
    Agent,
    Control,
    InMemoryStore,
    Level,
    ManualClock,
    Premise,
    RunLimits,
    RunOutcome,
    Settled,
    attach_model,
    create_model,
)
from blackboard.server import BoardService, Request
from blackboard.wire import (
    ACK,
    READ_BOARD,
    READ_LEVEL,
    READ_PREMISE,
    READ_REGIONS,
    WRITE,
    ErrorBody,
    RegionList,
)

BOARD = "incident-1"
LIMITS = RunLimits(wall_clock=timedelta(minutes=5), idle=timedelta(seconds=10))


def a_record(store: InMemoryStore) -> Control:
    model = create_model(
        board_id=BOARD,
        store=store,
        regions=[Level("signals"), Premise("severity")],
        premises={"severity": "high"},
        agents=[],
        limits=LIMITS,
    )
    model.control.write("signals", {"n": 1}, writer="src")
    return model.control


class TestAReadWithoutARun:
    @pytest.fixture
    def store(self) -> InMemoryStore:
        store = InMemoryStore()
        a_record(store)
        return store

    def service(self, store: InMemoryStore) -> BoardService:
        """A replica that runs nothing, holding the same store."""
        return BoardService(control_for=lambda board_id: None, store=store)

    def test_the_regions_are_answered_from_the_record(
        self, store: InMemoryStore
    ) -> None:
        answer = self.service(store).handle(
            Request(method="GET", path=READ_REGIONS.path(board_id=BOARD))
        )
        assert answer.status == 200
        assert {r.name for r in RegionList.from_json(answer.body).regions} == {
            "signals",
            "severity",
        }

    @pytest.mark.parametrize(
        "path",
        [
            READ_LEVEL.path(board_id=BOARD, level="signals"),
            READ_PREMISE.path(board_id=BOARD, premise="severity"),
            READ_BOARD.path(board_id=BOARD),
        ],
    )
    def test_every_read_is_answered_from_the_record(
        self, store: InMemoryStore, path: str
    ) -> None:
        answer = self.service(store).handle(Request(method="GET", path=path))
        assert answer.status == 200

    def test_a_write_still_needs_a_run(self, store: InMemoryStore) -> None:
        answer = self.service(store).handle(
            Request(
                method="POST",
                path=WRITE.path(board_id=BOARD, level="signals"),
                body={"writer": "a", "level": "signals", "content": {}},
            )
        )
        assert answer.status == 404
        assert ErrorBody.from_json(answer.body).error == "unknown_board"

    def test_an_acknowledgment_still_needs_a_run(self, store: InMemoryStore) -> None:
        answer = self.service(store).handle(
            Request(
                method="POST",
                path=ACK.path(board_id=BOARD),
                body={"agent": "a", "notification_id": 1},
            )
        )
        assert answer.status == 404

    def test_a_board_the_store_never_held_is_still_not_found(
        self, store: InMemoryStore
    ) -> None:
        """A mistyped identifier must not answer 200 with an empty list."""
        answer = self.service(store).handle(
            Request(method="GET", path=READ_REGIONS.path(board_id="typo"))
        )
        assert answer.status == 404
        assert ErrorBody.from_json(answer.body).error == "unknown_board"

    def test_without_a_store_nothing_changes(self, store: InMemoryStore) -> None:
        service = BoardService(control_for=lambda board_id: None)
        answer = service.handle(
            Request(method="GET", path=READ_REGIONS.path(board_id=BOARD))
        )
        assert answer.status == 404

    def test_a_live_run_is_preferred_to_the_store(self, store: InMemoryStore) -> None:
        control = a_record(InMemoryStore())
        service = BoardService(control_for={BOARD: control}.get, store=store)
        answer = service.handle(
            Request(
                method="POST",
                path=WRITE.path(board_id=BOARD, level="signals"),
                body={"writer": "a", "level": "signals", "content": {}},
            )
        )
        assert answer.status == 201


class TestBeingToldTheRunOpened:
    def test_an_agent_woken_at_creation_can_read_the_board_creating_it(self) -> None:
        """Registration runs notify on this thread, before create_model returns."""
        runs: dict[str, Control] = {}
        service = BoardService(control_for=runs.get)
        seen: list[int] = []

        def route(model: Any) -> None:
            runs[model.control.board_id] = model.control

        def wake(notification: Any) -> None:
            answer = service.handle(
                Request(
                    method="GET",
                    path=READ_REGIONS.path(board_id=notification.board_id),
                )
            )
            seen.append(answer.status)

        create_model(
            board_id=BOARD,
            store=InMemoryStore(),
            regions=[Premise("severity")],
            premises={"severity": "high"},
            agents=[Agent(name="triage", notify=wake)],
            limits=LIMITS,
            on_open=route,
        )
        assert seen == [200]

    def test_it_is_called_before_any_agent_is_woken(self) -> None:
        order: list[str] = []
        create_model(
            board_id=BOARD,
            store=InMemoryStore(),
            regions=[Premise("severity")],
            premises={"severity": "high"},
            agents=[Agent(name="a", notify=lambda n: order.append("woken"))],
            limits=LIMITS,
            on_open=lambda m: order.append("open"),
        )
        assert order == ["open", "woken"]

    def test_attaching_tells_the_caller_too(self) -> None:
        store = InMemoryStore()
        a_record(store)
        told: list[Any] = []
        attach_model(
            board_id=BOARD,
            store=store,
            regions=[Level("signals"), Premise("severity")],
            agents=[],
            limits=LIMITS,
            on_open=told.append,
        )
        assert len(told) == 1

    def test_a_handler_that_raises_does_not_stop_the_run(self) -> None:
        def explode(model: Any) -> None:
            raise RuntimeError("the router is down")

        model = create_model(
            board_id=BOARD,
            store=InMemoryStore(),
            regions=[Level("signals")],
            premises={},
            agents=[],
            limits=LIMITS,
            on_open=explode,
        )
        assert model.control.outcome() is None


class TestBeingToldTheRunClosed:
    def test_an_abort_tells_the_caller_once(self) -> None:
        closed: list[RunOutcome] = []
        model = create_model(
            board_id=BOARD,
            store=InMemoryStore(),
            regions=[Level("signals")],
            premises={},
            agents=[],
            limits=LIMITS,
            on_closed=closed.append,
        )
        model.control.abort("stood down")
        model.control.abort("stood down again")
        assert len(closed) == 1
        assert isinstance(closed[0], Aborted)

    def test_settling_on_silence_tells_the_caller(self) -> None:
        clock = ManualClock()
        closed: list[RunOutcome] = []
        create_model(
            board_id=BOARD,
            store=InMemoryStore(),
            regions=[Level("signals")],
            premises={},
            agents=[],
            limits=LIMITS,
            clock=clock,
            on_closed=closed.append,
        )
        clock.advance(timedelta(seconds=11))
        assert len(closed) == 1
        assert isinstance(closed[0], Settled)

    def test_the_registry_can_be_emptied_from_it(self) -> None:
        runs: dict[str, Control] = {}

        def forget(outcome: RunOutcome) -> None:
            runs.pop(BOARD, None)

        model = create_model(
            board_id=BOARD,
            store=InMemoryStore(),
            regions=[Level("signals")],
            premises={},
            agents=[],
            limits=LIMITS,
            on_open=lambda m: runs.__setitem__(m.control.board_id, m.control),
            on_closed=forget,
        )
        assert runs == {BOARD: model.control}
        model.control.abort("stood down")
        assert runs == {}

    def test_a_handler_that_raises_does_not_reach_the_caller(self) -> None:
        def explode(outcome: RunOutcome) -> None:
            raise RuntimeError("the registry is down")

        model = create_model(
            board_id=BOARD,
            store=InMemoryStore(),
            regions=[Level("signals")],
            premises={},
            agents=[],
            limits=LIMITS,
            on_closed=explode,
        )
        model.control.abort("stood down")
        assert isinstance(model.control.outcome(), Aborted)

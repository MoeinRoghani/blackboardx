"""One store holds many boards, and every call names the board it acts on."""

from datetime import UTC, datetime, timedelta

import pytest

from blackboard import (
    Agent,
    InMemoryStore,
    Level,
    ManualClock,
    Notification,
    Premise,
    RunLimits,
    UndeclaredRegionError,
    create_model,
)

START = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
LIMITS = RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(minutes=30))


class TestOneStoreManyBoards:
    def test_a_store_is_built_without_naming_a_board(self) -> None:
        store = InMemoryStore()
        store.declare("board-a", Level("platform"))
        assert store.read_level("board-a", "platform") == []

    def test_two_boards_in_one_store_see_none_of_each_other(self) -> None:
        store = InMemoryStore()
        store.declare("board-a", Level("platform"))
        store.declare("board-b", Level("platform"))
        store.append("board-a", "platform", "from a")
        store.append("board-b", "platform", "from b")
        assert [c.content for c in store.read_level("board-a", "platform")] == [
            "from a"
        ]
        assert [c.content for c in store.read_level("board-b", "platform")] == [
            "from b"
        ]

    def test_each_board_counts_its_own_sequence(self) -> None:
        store = InMemoryStore()
        for name in ("board-a", "board-b"):
            store.declare(name, Level("platform"))
        assert store.append("board-a", "platform", "one").sequence == 1
        assert store.append("board-a", "platform", "two").sequence == 2
        assert store.append("board-b", "platform", "one").sequence == 1

    def test_a_region_declared_on_one_board_is_undeclared_on_the_other(self) -> None:
        store = InMemoryStore()
        store.declare("board-a", Level("platform"))
        with pytest.raises(UndeclaredRegionError):
            store.read_level("board-b", "platform")


class TestTheModelNamesItsBoard:
    def test_create_model_takes_a_board_id_and_a_store(self) -> None:
        store = InMemoryStore()
        model = create_model(
            board_id="board-a",
            store=store,
            regions=[Level("platform"), Premise("window")],
            premises={"window": "w"},
            limits=LIMITS,
            clock=ManualClock(start=START),
        )
        model.control.write("ocp", "platform", "a finding")
        # The store, addressed directly, holds what the model wrote.
        assert [c.content for c in store.read_level("board-a", "platform")] == [
            "a finding"
        ]

    def test_two_models_share_one_store_without_colliding(self) -> None:
        store = InMemoryStore()
        for name in ("board-a", "board-b"):
            create_model(
                board_id=name,
                store=store,
                regions=[Level("platform")],
                premises={},
                limits=LIMITS,
                clock=ManualClock(start=START),
            ).control.write("ocp", "platform", f"written by {name}")
        assert [c.content for c in store.read_level("board-a", "platform")] == [
            "written by board-a"
        ]
        assert [c.content for c in store.read_level("board-b", "platform")] == [
            "written by board-b"
        ]

    def test_a_notification_names_the_board_it_came_from(self) -> None:
        got: list[Notification] = []
        create_model(
            board_id="board-a",
            store=InMemoryStore(),
            regions=[Premise("window")],
            premises={"window": "w"},
            agents=[Agent(name="ocp", notify=got.append)],
            limits=LIMITS,
            clock=ManualClock(start=START),
        )
        (n,) = got
        assert n.board_id == "board-a"

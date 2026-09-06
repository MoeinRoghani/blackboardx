"""The loop that runs on a schedule runs both jobs, not one.

`close_expired` takes a store, because closing needs nothing else. The relay
needs the callables, which no store holds, so it takes what `BoardService`
takes: a callable of the application's that answers with the `Control` for a
board. A sweep given none reaps and nothing more.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from blackboard import (
    Agent,
    InMemoryStore,
    Level,
    Notification,
    RunLimits,
    Sweep,
    create_model,
    relay_unsent,
)

LIMITS = RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(seconds=30))
ADDRESS = "https://ocp.internal/notify"


def a_board(store: Any, board_id: str = "incident-1", **overrides: Any) -> Any:
    settings: dict[str, Any] = {
        "board_id": board_id,
        "store": store,
        "regions": [Level("findings")],
        "premises": {},
        "limits": LIMITS,
    }
    settings.update(overrides)
    return create_model(**settings)


def one_owed(store: Any, board_id: str = "incident-1") -> None:
    """Leaves a row that nothing has sent, the way a stopped process would."""
    model = a_board(store, board_id)
    model.control.register_agent(
        Agent(name="ocp", subscribes_to=["findings"], address=ADDRESS)
    )
    model.control.write("findings", "oom", writer="scanner")


class TestRelayingOverEveryBoardThatIsOwed:
    def test_it_sends_for_a_board_the_callable_answers_for(self) -> None:
        store = InMemoryStore()
        one_owed(store)
        posted: list[tuple[str, str]] = []
        serving = a_board(
            store, reach=lambda address, n: posted.append((address, n.agent))
        )

        assert relay_unsent(store, lambda board_id: serving.control) == ["ocp"]
        assert posted == [(ADDRESS, "ocp")]
        assert store.unsent() == []

    def test_a_board_nothing_answers_for_is_left(self) -> None:
        store = InMemoryStore()
        one_owed(store)

        assert relay_unsent(store, lambda board_id: None) == []
        assert len(store.unsent()) == 1

    def test_every_board_that_is_owed_is_asked_for(self) -> None:
        store = InMemoryStore()
        one_owed(store, "incident-1")
        one_owed(store, "incident-2")
        asked: list[str] = []

        def control_for(board_id: str) -> Any:
            asked.append(board_id)
            return None

        relay_unsent(store, control_for)
        assert sorted(asked) == ["incident-1", "incident-2"]

    def test_one_board_failing_does_not_end_the_pass(self) -> None:
        store = InMemoryStore()
        one_owed(store, "incident-1")
        one_owed(store, "incident-2")
        posted: list[str] = []
        serving = a_board(
            store, "incident-2", reach=lambda address, n: posted.append(n.agent)
        )

        def control_for(board_id: str) -> Any:
            if board_id == "incident-1":
                raise RuntimeError("this board is not mine")
            return serving.control

        assert relay_unsent(store, control_for) == ["ocp"]
        assert posted == ["ocp"]


class TestTheSweepDoingBothJobs:
    def test_a_sweep_given_a_callable_relays(self) -> None:
        store = InMemoryStore()
        one_owed(store)
        posted: list[Notification] = []
        serving = a_board(store, reach=lambda address, n: posted.append(n))

        with Sweep(
            store,
            control_for=lambda board_id: serving.control,
            interval=0.05,
            jitter=0.0,
        ) as sweep:
            assert sweep.wait_for_pass(timeout=5.0)

        assert [n.agent for n in posted] == ["ocp"]
        assert store.unsent() == []

    def test_a_sweep_given_none_reaps_and_nothing_more(self) -> None:
        store = InMemoryStore()
        one_owed(store)

        with Sweep(store, interval=0.05, jitter=0.0) as sweep:
            assert sweep.wait_for_pass(timeout=5.0)

        assert len(store.unsent()) == 1

    def test_a_relay_that_raises_does_not_end_the_loop(self) -> None:
        store = InMemoryStore()
        one_owed(store)

        def control_for(board_id: str) -> Any:
            raise RuntimeError("no")

        with Sweep(store, control_for=control_for, interval=0.05, jitter=0.0) as sweep:
            assert sweep.wait_for_pass(timeout=5.0, passes=2)

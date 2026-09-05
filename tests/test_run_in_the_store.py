"""A run any process can see, close, and be told about.

The deadlines are instants the store holds, so a process that did not open
the run still knows when it ends. Closing is a write only one caller wins,
so a run closes once however many callers reach the deadline together.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from blackboard import (
    Agent,
    InMemoryStore,
    Level,
    ManualClock,
    Premise,
    RunLimits,
    Settled,
    SqliteStore,
    WallClockExpired,
    close_expired,
    create_model,
)

LIMITS = RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(seconds=30))


def a_model(store: Any, clock: ManualClock | None = None, **overrides: Any) -> Any:
    settings: dict[str, Any] = {
        "board_id": "incident-1",
        "store": store,
        "regions": [Level("findings"), Premise("severity")],
        "premises": {"severity": "unknown"},
        "limits": LIMITS,
    }
    settings.update(overrides)
    if clock is not None:
        settings["clock"] = clock
    return create_model(**settings)


class TestTheStoreHoldsTheRun:
    def test_opening_a_board_opens_a_run_in_the_store(self) -> None:
        store = InMemoryStore()
        a_model(store)
        run = store.read_run("incident-1")
        assert run is not None
        assert run.closed_as is None
        assert run.now < run.idle_deadline < run.wall_deadline

    def test_a_write_pushes_the_idle_deadline_in_the_store(self) -> None:
        store = InMemoryStore()
        model = a_model(store)
        before = store.read_run("incident-1")
        assert before is not None
        model.control.write("findings", "oom", writer="triage")
        after = store.read_run("incident-1")
        assert after is not None
        assert after.idle_deadline > before.idle_deadline

    def test_closing_records_the_outcome_in_the_store(self) -> None:
        store = InMemoryStore()
        model = a_model(store)
        model.control.abort("the operator stopped it")
        run = store.read_run("incident-1")
        assert run is not None
        assert run.closed_as == "aborted"
        assert run.reason == "the operator stopped it"

    def test_a_settled_run_records_its_unfinished_agents(self) -> None:
        store = InMemoryStore()
        clock = ManualClock()
        model = a_model(
            store,
            clock,
            agents=[
                Agent(name="triage", notify=lambda n: None, subscribes_to=["findings"])
            ],
        )
        model.control.write("findings", "oom", writer="collector")
        clock.advance(timedelta(seconds=31))
        run = store.read_run("incident-1")
        assert run is not None
        assert run.closed_as == "settled"
        assert run.unfinished == frozenset({"triage"})


class TestAnotherProcessCanClose:
    """The point of the deadlines being in the store."""

    def test_the_sweep_closes_a_run_whose_process_never_noticed(
        self, tmp_path: Any
    ) -> None:
        path = str(tmp_path / "board.sqlite3")
        store = SqliteStore(path)
        a_model(store)
        # The run went quiet. RunLimits refuses a duration in the past, so
        # the deadline is put there through the store, which is the state a
        # quiet run reaches on its own.
        store.open_run("incident-1", wall_clock=3600.0, idle=-1.0)

        # A second process, holding only the store and no run.
        elsewhere = SqliteStore(path)
        try:
            assert close_expired(elsewhere) == ["incident-1"]
            run = elsewhere.read_run("incident-1")
            assert run is not None
            assert run.closed_as == "settled"
        finally:
            elsewhere.close()
            store.close()

    def test_the_sweep_leaves_a_run_inside_its_deadlines(self) -> None:
        store = InMemoryStore()
        a_model(store)
        assert close_expired(store) == []

    def test_the_sweep_closes_a_run_past_its_wall_clock_as_expired(self) -> None:
        store = InMemoryStore()
        a_model(store)
        store.open_run("incident-1", wall_clock=-1.0, idle=3600.0)
        assert close_expired(store) == ["incident-1"]
        run = store.read_run("incident-1")
        assert run is not None
        assert run.closed_as == "wall_clock_expired"

    def test_the_sweep_returns_no_more_than_it_was_asked_for(self) -> None:
        store = InMemoryStore()
        for n in range(3):
            a_model(store, board_id=f"incident-{n}")
            store.open_run(f"incident-{n}", wall_clock=3600.0, idle=-1.0)
        assert len(close_expired(store, limit=2)) == 2

    def test_closing_twice_closes_once(self) -> None:
        """Two callers reaching one deadline, and one outcome."""
        store = InMemoryStore()
        a_model(store)
        store.open_run("incident-1", wall_clock=3600.0, idle=-1.0)
        first = close_expired(store)
        second = close_expired(store)
        assert first == ["incident-1"]
        assert second == []


class TestTheProcessThatHoldsTheRunStillWorks:
    """Nothing about single-process use changes."""

    def test_the_idle_limit_still_settles_the_run_in_process(self) -> None:
        clock = ManualClock()
        model = a_model(InMemoryStore(), clock)
        model.control.write("findings", "oom", writer="triage")
        clock.advance(timedelta(seconds=31))
        assert model.control.outcome() == Settled(unfinished=frozenset())

    def test_the_wall_clock_still_expires_the_run_in_process(self) -> None:
        clock = ManualClock()
        model = a_model(
            InMemoryStore(),
            clock,
            limits=RunLimits(wall_clock=timedelta(seconds=10), idle=timedelta(hours=1)),
        )
        clock.advance(timedelta(seconds=11))
        assert isinstance(model.control.outcome(), WallClockExpired)

    def test_a_control_component_adopts_the_outcome_the_sweep_wrote(self) -> None:
        """Two callers, one outcome: the loser takes the winner's, not its own."""
        store = InMemoryStore()
        model = a_model(store)
        store.open_run("incident-1", wall_clock=3600.0, idle=-1.0)
        close_expired(store)

        # This process then reaches its own deadline and loses the race.
        model.control.abort("too late")
        assert model.control.outcome() == Settled(unfinished=frozenset())

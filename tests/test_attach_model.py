"""Opening a run over a board that already holds a record.

The service page says a replica that dies is replaced and the run it held is
started again against the record it left. These are the cases that makes true.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from blackboard import (
    Agent,
    InMemoryStore,
    Level,
    Premise,
    RegionKindError,
    RunLimits,
    SqliteStore,
    UndeclaredRegionError,
    Written,
    attach_model,
    create_model,
)

LIMITS = RunLimits(wall_clock=timedelta(minutes=5), idle=timedelta(minutes=5))
REGIONS: list[Any] = [Level("signals"), Level("findings"), Premise("severity")]


def a_finished_run(store: Any, board_id: str = "incident-1") -> None:
    model = create_model(
        board_id=board_id,
        store=store,
        regions=list(REGIONS),
        premises={"severity": "unknown"},
        agents=[],
        limits=LIMITS,
    )
    model.control.write("signals", {"n": 1}, writer="src")
    model.control.set_premise("severity", "high", 1, writer="src")
    model.control.abort("the replica died")


class TestAttaching:
    def test_a_record_left_by_a_finished_run_is_written_to_again(self) -> None:
        store = InMemoryStore()
        a_finished_run(store)
        model = attach_model(
            board_id="incident-1",
            store=store,
            regions=list(REGIONS),
            agents=[],
            limits=LIMITS,
        )
        outcome = model.control.write("findings", {"cause": "a bad deploy"}, writer="a")
        assert isinstance(outcome, Written)

    def test_the_sequence_continues_the_record(self) -> None:
        store = InMemoryStore()
        a_finished_run(store)
        ended_at = store.read_board("incident-1")[-1].sequence
        model = attach_model(
            board_id="incident-1",
            store=store,
            regions=list(REGIONS),
            agents=[],
            limits=LIMITS,
        )
        outcome = model.control.write("findings", {"n": 1}, writer="a")
        assert isinstance(outcome, Written)
        assert outcome.sequence == ended_at + 1

    def test_the_premise_keeps_its_value_and_version(self) -> None:
        store = InMemoryStore()
        a_finished_run(store)
        model = attach_model(
            board_id="incident-1",
            store=store,
            regions=list(REGIONS),
            agents=[],
            limits=LIMITS,
        )
        held = model.reader.read_premise("severity")
        assert held.value == "high"
        assert isinstance(
            model.control.set_premise("severity", "low", held.version, writer="a"),
            Written,
        )

    def test_an_agent_is_woken_by_what_the_record_already_holds(self) -> None:
        store = InMemoryStore()
        a_finished_run(store)
        seen: list[Any] = []
        attach_model(
            board_id="incident-1",
            store=store,
            regions=list(REGIONS),
            agents=[
                Agent(name="triage", subscribes_to={"signals"}, notify=seen.append)
            ],
            limits=LIMITS,
        )
        assert seen != []
        assert "signals" in seen[-1].regions

    def test_a_notification_covers_a_range_that_ends_at_the_record(self) -> None:
        store = InMemoryStore()
        a_finished_run(store)
        ended_at = store.read_board("incident-1")[-1].sequence
        seen: list[Any] = []
        attach_model(
            board_id="incident-1",
            store=store,
            regions=list(REGIONS),
            agents=[
                Agent(name="triage", subscribes_to={"signals"}, notify=seen.append)
            ],
            limits=LIMITS,
        )
        assert seen[-1].to_sequence == ended_at

    def test_a_file_written_by_a_process_that_ended_is_attached(
        self, tmp_path: Path
    ) -> None:
        path = str(tmp_path / "incident.sqlite3")
        first = SqliteStore(path)
        a_finished_run(first)
        first.close()

        second = SqliteStore(path)
        try:
            model = attach_model(
                board_id="incident-1",
                store=second,
                regions=list(REGIONS),
                agents=[],
                limits=LIMITS,
            )
            assert [c.content for c in model.reader.read_level("signals")] == [{"n": 1}]
            assert isinstance(model.control.write("findings", {}, writer="a"), Written)
        finally:
            second.close()


class TestWhatAttachingRefuses:
    def test_a_board_the_store_never_held_is_refused(self) -> None:
        with pytest.raises(UndeclaredRegionError, match="no regions"):
            attach_model(
                board_id="never-existed",
                store=InMemoryStore(),
                regions=list(REGIONS),
                agents=[],
                limits=LIMITS,
            )

    def test_a_region_the_record_does_not_hold_is_refused(self) -> None:
        store = InMemoryStore()
        a_finished_run(store)
        with pytest.raises(UndeclaredRegionError, match="rumours"):
            attach_model(
                board_id="incident-1",
                store=store,
                regions=[*REGIONS, Level("rumours")],
                agents=[],
                limits=LIMITS,
            )

    def test_a_region_the_run_does_not_declare_is_refused(self) -> None:
        store = InMemoryStore()
        a_finished_run(store)
        with pytest.raises(UndeclaredRegionError, match="findings"):
            attach_model(
                board_id="incident-1",
                store=store,
                regions=[Level("signals"), Premise("severity")],
                agents=[],
                limits=LIMITS,
            )

    def test_a_kind_that_disagrees_with_the_record_is_refused(self) -> None:
        store = InMemoryStore()
        a_finished_run(store)
        with pytest.raises(RegionKindError, match="severity"):
            attach_model(
                board_id="incident-1",
                store=store,
                regions=[Level("signals"), Level("findings"), Level("severity")],
                agents=[],
                limits=LIMITS,
            )

    def test_a_roster_naming_an_undeclared_region_is_refused(self) -> None:
        store = InMemoryStore()
        a_finished_run(store)
        with pytest.raises(UndeclaredRegionError, match="typo"):
            attach_model(
                board_id="incident-1",
                store=store,
                regions=list(REGIONS),
                agents=[Agent(name="a", subscribes_to={"typo"}, notify=lambda n: None)],
                limits=LIMITS,
            )


def test_creating_over_an_existing_board_is_still_refused() -> None:
    """The two doors stay separate: one opens a board, one opens a run."""
    from blackboard import DuplicateRegionError

    store = InMemoryStore()
    a_finished_run(store)
    with pytest.raises(DuplicateRegionError):
        create_model(
            board_id="incident-1",
            store=store,
            regions=list(REGIONS),
            premises={"severity": "unknown"},
            agents=[],
            limits=LIMITS,
        )

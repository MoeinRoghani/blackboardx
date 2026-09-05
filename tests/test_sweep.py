"""The convenience loop over `close_expired`, for an application that wants one.

The library ships mechanism and the application owns the loop. This is the
loop, for the common case where the application would otherwise write the
same thread. Ignoring it and calling `close_expired` from a scheduled job is
equally supported.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import Any

import pytest

from blackboard import InMemoryStore, Level, RunLimits, Sweep, create_model

LIMITS = RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(seconds=30))


def a_board(store: Any, board_id: str, idle: float = -1.0) -> None:
    """A board whose run is already past its idle deadline."""
    create_model(
        board_id=board_id,
        store=store,
        regions=[Level("findings")],
        premises={},
        limits=LIMITS,
    )
    store.open_run(board_id, wall_clock=3600.0, idle=idle)


class TestItClosesWhatNobodyIsWatching:
    def test_a_pass_closes_an_expired_run(self) -> None:
        store = InMemoryStore()
        a_board(store, "incident-1")
        with Sweep(store, interval=0.01, jitter=0.0) as sweep:
            assert sweep.wait_for_pass(timeout=2.0), "a pass ran"
        run = store.read_run("incident-1")
        assert run is not None
        assert run.closed_as == "settled"

    def test_a_run_inside_its_deadlines_is_left_alone(self) -> None:
        store = InMemoryStore()
        a_board(store, "incident-1", idle=3600.0)
        with Sweep(store, interval=0.01, jitter=0.0) as sweep:
            sweep.wait_for_pass(timeout=2.0)
        run = store.read_run("incident-1")
        assert run is not None
        assert run.closed_as is None

    def test_it_keeps_going_after_a_pass_that_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A store that fails must not end the loop, or nothing sweeps again."""
        store = _FailsOnce()
        a_board(store, "incident-1")
        with (
            caplog.at_level(logging.ERROR, logger="blackboard"),
            Sweep(store, interval=0.01, jitter=0.0) as sweep,
        ):
            assert sweep.wait_for_pass(timeout=2.0, passes=2)
        run = store.read_run("incident-1")
        assert run is not None
        assert run.closed_as == "settled", "the pass after the failure closed it"
        assert any(r.levelno >= logging.ERROR for r in caplog.records)


class TestItIsTheApplicationsToOwn:
    def test_closing_it_stops_the_thread(self) -> None:
        store = InMemoryStore()
        sweep = Sweep(store, interval=0.01, jitter=0.0)
        sweep.start()
        sweep.close()
        assert not sweep.running

    def test_closing_twice_is_harmless(self) -> None:
        sweep = Sweep(InMemoryStore(), interval=0.01, jitter=0.0)
        sweep.start()
        sweep.close()
        sweep.close()

    def test_it_is_not_running_before_it_is_started(self) -> None:
        assert not Sweep(InMemoryStore()).running

    def test_a_negative_interval_is_refused(self) -> None:
        with pytest.raises(ValueError):
            Sweep(InMemoryStore(), interval=-1.0)

    def test_jitter_outside_zero_to_one_is_refused(self) -> None:
        with pytest.raises(ValueError):
            Sweep(InMemoryStore(), jitter=1.5)


class _FailsOnce(InMemoryStore):
    """Raises on the first sweep query and answers normally after it."""

    def __init__(self) -> None:
        super().__init__()
        self._raised = False
        self._lock_for_test = threading.Lock()

    def runs_past_deadline(self, limit: int = 100) -> list[str]:
        with self._lock_for_test:
            if not self._raised:
                self._raised = True
                raise RuntimeError("the connection went away")
        return super().runs_past_deadline(limit)

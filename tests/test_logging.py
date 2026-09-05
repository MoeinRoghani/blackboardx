"""What the control component says, and what it deliberately does not.

The rule is one line: log only what the other side cannot see. An agent
knows what it wrote, what it was refused, what it was notified of and what
it acknowledged, because each of those reached it. It cannot know that the
run closed, or that a store failed under a write it never saw succeed.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import pytest

from blackboard import (
    SCHEMA_VERSION,
    Agent,
    InMemoryStore,
    Level,
    ManualClock,
    Premise,
    Reject,
    RunLimits,
    SchemaVersionError,
    close_expired,
    create_model,
)
from blackboard._schema import stamp_to_write

LOGGER = "blackboard"


def a_model(clock: ManualClock | None = None, **overrides: Any) -> Any:
    settings: dict[str, Any] = {
        "board_id": "incident-1",
        "store": InMemoryStore(),
        "regions": [Level("findings"), Premise("severity")],
        "premises": {"severity": "unknown"},
        "limits": RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(seconds=30)),
    }
    settings.update(overrides)
    if clock is not None:
        settings["clock"] = clock
    return create_model(**settings)


class TestWhatIsLogged:
    def test_a_run_that_settles_says_so_with_its_outcome(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock = ManualClock()
        with caplog.at_level(logging.INFO, logger=LOGGER):
            model = a_model(clock)
            model.control.write("findings", "oom", writer="triage")
            clock.advance(timedelta(seconds=31))
        said = "\n".join(r.getMessage() for r in caplog.records)
        assert "incident-1" in said
        assert "settled" in said.lower()

    def test_a_closed_run_names_the_agents_that_did_not_finish(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock = ManualClock()
        with caplog.at_level(logging.INFO, logger=LOGGER):
            model = a_model(
                clock,
                agents=[
                    Agent(
                        name="triage",
                        notify=lambda n: None,
                        subscribes_to=["findings"],
                    )
                ],
            )
            model.control.write("findings", "oom", writer="collector")
            clock.advance(timedelta(seconds=31))
        said = "\n".join(r.getMessage() for r in caplog.records)
        assert "triage" in said

    def test_an_aborted_run_says_who_stopped_it_and_why(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger=LOGGER):
            model = a_model()
            model.control.abort("the operator stopped it")
        said = "\n".join(r.getMessage() for r in caplog.records)
        assert "the operator stopped it" in said


class TestWhatIsNotLogged:
    """Each of these reached the agent, so the agent logs it, not this."""

    def test_an_accepted_write_says_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger=LOGGER):
            model = a_model()
            model.control.write("findings", "oom", writer="triage")
        assert caplog.records == []

    def test_a_refused_write_says_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """It is returned to its caller, which is where it is logged."""
        with caplog.at_level(logging.DEBUG, logger=LOGGER):
            model = a_model(admission_rule=lambda proposed, reader: Reject("no"))
            model.control.write("findings", "oom", writer="triage")
        assert caplog.records == []

    def test_a_notification_and_its_acknowledgment_say_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        held: list[Any] = []
        model = a_model(
            agents=[
                Agent(name="triage", notify=held.append, subscribes_to=["findings"])
            ]
        )
        with caplog.at_level(logging.DEBUG, logger=LOGGER):
            model.control.write("findings", "oom", writer="collector")
            model.control.ack(held[-1].notification_id, agent="triage")
        assert caplog.records == []


class _StoreThatFails(InMemoryStore):
    """A store whose run reads raise for one board, and work for the rest."""

    def __init__(self, failing: str) -> None:
        super().__init__()
        self._failing = failing

    def read_run(self, board_id: str) -> Any:
        if board_id == self._failing:
            raise RuntimeError("the connection went away")
        return super().read_run(board_id)


class TestTheStoreFailingUnderTheSweep:
    """A scheduled sweep has no caller, so a failure there reaches nobody."""

    def test_a_board_that_raises_does_not_stop_the_sweep(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        store = _StoreThatFails("board-b")
        for board_id in ("board-a", "board-b", "board-c"):
            store.declare(board_id, Level("findings"))
            store.open_run(board_id, wall_clock=3600.0, idle=-1.0)

        with caplog.at_level(logging.ERROR, logger="blackboard"):
            closed = close_expired(store)

        assert sorted(closed) == ["board-a", "board-c"], "the others still close"
        assert any("board-b" in r.getMessage() for r in caplog.records)

    def test_the_failure_is_logged_as_an_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        store = _StoreThatFails("board-b")
        store.declare("board-b", Level("findings"))
        store.open_run("board-b", wall_clock=3600.0, idle=-1.0)

        with caplog.at_level(logging.ERROR, logger="blackboard"):
            close_expired(store)

        (record,) = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert "board-b" in record.getMessage()
        assert record.exc_info is not None, "the cause travels with the line"


class TestARecordThisVersionCannotRead:
    def test_a_refusal_is_logged_before_it_raises(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The caller sees the exception; a scheduled job may see neither."""
        with (
            caplog.at_level(logging.ERROR, logger="blackboard"),
            pytest.raises(SchemaVersionError),
        ):
            stamp_to_write(SCHEMA_VERSION + 1, where="the test database")
        (record,) = caplog.records
        assert "the test database" in record.getMessage()

    def test_stamping_a_record_forward_is_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="blackboard"):
            assert stamp_to_write(SCHEMA_VERSION - 1, where="the test database") == (
                SCHEMA_VERSION
            )
        (record,) = caplog.records
        assert str(SCHEMA_VERSION) in record.getMessage()

    def test_a_record_already_at_this_schema_says_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="blackboard"):
            assert stamp_to_write(SCHEMA_VERSION, where="the test database") is None
        assert caplog.records == []

"""Two names on their way out, and the warning that says so.

`attach_model` and `Control.read_audit` are deprecated together. Both exist
because the run lived in one process's memory: one opened a run over a record
whose run had died, and the other read a history that died with it. Neither
answers a question the record does not, and both keep working until the
removal date the warning names.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

import pytest

from blackboard import (
    InMemoryStore,
    Level,
    Premise,
    RunLimits,
    attach_model,
    create_model,
)

LIMITS = RunLimits(wall_clock=timedelta(minutes=5), idle=timedelta(minutes=5))
#: The earliest date either name may be removed, named by both warnings.
REMOVAL = "2026-12-05"


def a_board(store: InMemoryStore) -> Any:
    return create_model(
        board_id="b",
        store=store,
        regions=[Level("findings"), Premise("severity")],
        premises={"severity": "unknown"},
        limits=LIMITS,
    )


class TestAttachModel:
    def test_it_warns_and_still_opens_the_run(self) -> None:
        store = InMemoryStore()
        a_board(store)
        with pytest.warns(DeprecationWarning) as caught:
            model = attach_model(
                board_id="b",
                store=store,
                regions=[Level("findings"), Premise("severity")],
                limits=LIMITS,
            )
        assert model.reader.read_premise("severity").value == "unknown"
        assert len(caught) == 1

    def test_the_warning_names_a_replacement_and_a_date(self) -> None:
        store = InMemoryStore()
        a_board(store)
        with pytest.warns(DeprecationWarning) as caught:
            attach_model(
                board_id="b",
                store=store,
                regions=[Level("findings"), Premise("severity")],
                limits=LIMITS,
            )
        said = str(caught[0].message)
        assert "create_model" in said
        assert REMOVAL in said
        assert re.search(r"\d{4}-\d{2}-\d{2}", said)


class TestReadAudit:
    def test_it_warns_and_still_answers(self) -> None:
        model = a_board(InMemoryStore())
        model.control.write("findings", "oom", writer="triage")
        with pytest.warns(DeprecationWarning) as caught:
            events = model.control.read_audit()
        assert events
        assert len(caught) == 1

    def test_the_warning_names_a_replacement_and_a_date(self) -> None:
        model = a_board(InMemoryStore())
        with pytest.warns(DeprecationWarning) as caught:
            model.control.read_audit()
        said = str(caught[0].message)
        assert REMOVAL in said
        assert "log" in said.lower()


def test_neither_warning_fires_on_the_path_that_replaces_it() -> None:
    """A caller using the replacement is not warned about anything."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        model = a_board(InMemoryStore())
        model.control.write("findings", "oom", writer="triage")
        assert model.reader.read_level("findings")[0].writer == "triage"

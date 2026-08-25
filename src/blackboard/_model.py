"""Creating a model.

A model is created by supplying five things: region declarations, seed
register values, an admission rule, a termination predicate, and run
budgets. Nothing else configures it, and the clock is dependency injection
rather than configuration.

Where the record is kept is stated, never defaulted. An application names
the board it wants, so none reaches deployment holding its record in
process memory because an argument was omitted.

No agent is named at creation. An agent comes to exist by registering,
which is the only path carrying its callback, and a creator cannot know
in advance which agents will join.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass

from blackboard._board import Level, Register
from blackboard._clock import Clock, SystemClock
from blackboard._control import (
    AdmissionRule,
    BoardReader,
    BoardStore,
    Control,
    RunBudgets,
    RunClosedError,
    TerminationPredicate,
)


@dataclass(frozen=True)
class Model:
    """A running shared solution model: its read handle and its control component.

    Board reads go to ``reader`` directly, consume no control capacity, and
    cannot be refused. Writes, acknowledgments, and lifecycle calls go
    through ``control``.
    """

    reader: BoardReader
    control: Control


def create_model(
    *,
    regions: Iterable[Level | Register],
    seed: Mapping[str, object],
    admission_rule: AdmissionRule | None = None,
    termination_predicate: TerminationPredicate | None = None,
    budgets: RunBudgets,
    board: BoardStore,
    clock: Clock | None = None,
) -> Model:
    """Opens a run and returns the model.

    ``board`` says where the record is kept and has no default, because a
    run whose record no second process can read is not a shared solution
    model. ``SqliteBoard`` serves one machine; an adapter against your own
    database serves a deployment.

    The seed writes every declared register once, bypassing admission. No
    agent is registered yet, so the seed wakes nobody; an agent registering
    afterwards is woken then. With no admission rule a write is accepted
    subject to the region existing, the limits holding, and the run being
    open; with no termination predicate the run closes when nothing has
    happened for the idle limit; with no clock the operating system clock
    serves.
    """
    control = Control(
        regions=regions,
        admission_rule=admission_rule,
        termination_predicate=termination_predicate,
        budgets=budgets,
        board=board,
        clock=clock if clock is not None else SystemClock(),
    )
    # The wall clock can expire while the run is opening, in which case the
    # model returns already closed and the seed never lands.
    with suppress(RunClosedError):
        control._seed(dict(seed))
    return Model(reader=control.reader, control=control)

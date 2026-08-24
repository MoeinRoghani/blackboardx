"""Creating a model.

A model is created by supplying six things: region declarations, agent
declarations, seed register values, an admission rule, a termination
predicate, and run budgets; nothing else configures it, and the clock is
dependency injection rather than configuration. Opening the run creates
the board, registers the agents, writes the seed, and dispatches every
opening wake a zero batch window makes due; a run with no work
outstanding and no predicate holding it open closes on creation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from blackboard._board import Level, Register
from blackboard._clock import Clock, SystemClock
from blackboard._control import (
    AdmissionRule,
    Agent,
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
    agents: Iterable[Agent],
    seed: Mapping[str, object],
    admission_rule: AdmissionRule | None = None,
    termination_predicate: TerminationPredicate | None = None,
    budgets: RunBudgets,
    board: BoardStore | None = None,
    clock: Clock | None = None,
) -> Model:
    """Opens a run and returns the model.

    The seed writes every declared register once, bypassing admission and
    the write budget, and the opening wakes follow, each counting against
    its agent's cap and the notification budget. With no admission rule a
    write is accepted subject to the region existing, the budgets holding,
    and the run being open; with no termination predicate the run closes
    as soon as no work is outstanding; with no clock the operating system
    clock serves.
    """
    control = Control(
        regions=regions,
        admission_rule=admission_rule,
        termination_predicate=termination_predicate,
        budgets=budgets,
        board=board,
        clock=clock if clock is not None else SystemClock(),
    )
    try:
        for agent in agents:
            control.register_agent(agent)
        control._seed(dict(seed))
    except RunClosedError:
        # The wall clock expired while the run was opening; the model
        # returns already closed.
        pass
    return Model(reader=control.reader, control=control)

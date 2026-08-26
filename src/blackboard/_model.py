"""Creating a model.

Six things configure a model: region declarations, opening premise values,
the agents it starts with, an admission rule, a termination predicate, and
run limits. A seventh argument, the board, says where the record is kept
rather than configuring what the model is. The clock is dependency
injection rather than configuration.

Where the record is kept is stated, never defaulted. An application names
the board it wants, so none reaches deployment holding its record in
process memory because an argument was omitted.

The creator names the agents the run starts with, and each is registered
once the premises hold their opening values. The run is therefore ready to
work the moment it exists. An agent that joins a run already under way
registers itself instead.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass

from blackboard._board import Level, Premise
from blackboard._clock import Clock, SystemClock
from blackboard._control import (
    AdmissionRule,
    Agent,
    BoardReader,
    BoardStore,
    Control,
    RunClosedError,
    RunLimits,
    TerminationPredicate,
    _resolve_limits,
    _resolve_premises,
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
    regions: Iterable[Level | Premise],
    premises: Mapping[str, object] | None = None,
    agents: Iterable[Agent] | None = None,
    admission_rule: AdmissionRule | None = None,
    termination_predicate: TerminationPredicate | None = None,
    limits: RunLimits | None = None,
    board: BoardStore,
    clock: Clock | None = None,
    budgets: RunLimits | None = None,
    seed: Mapping[str, object] | None = None,
) -> Model:
    """Opens a run and returns the model.

    ``limits`` carries the run's wall clock and idle durations. The
    ``budgets`` keyword is the former name for it, accepted for one release.

    ``board`` says where the record is kept and has no default, because a
    run whose record no second process can read is not a shared solution
    model. ``SqliteBoard`` serves one machine; an adapter against your own
    database serves a deployment.

    ``agents`` names the agents the run starts with. Each is registered
    once the premises hold their opening values, so each receives one
    notification covering everything already on the board. An agent that
    joins a run already under way registers itself through
    ``Control.register_agent`` instead.

    ``premises`` gives every declared premise its opening value, naming
    each one exactly once. Those writes bypass admission, because they are
    the application's own input rather than a proposal from a writer. The
    ``seed`` keyword is the former name for it, accepted for one release.

    No agent is registered yet, so opening the premises wakes nobody; an
    agent registering afterwards is woken then. With no admission rule a
    write is accepted subject to the region existing, the limits holding,
    and the run being open; with no termination predicate the run closes
    when nothing has happened for the idle limit; with no clock the
    operating system clock serves.
    """
    opening = _resolve_premises(premises, seed)
    control = Control(
        regions=regions,
        admission_rule=admission_rule,
        termination_predicate=termination_predicate,
        limits=_resolve_limits(limits, budgets),
        board=board,
        clock=clock if clock is not None else SystemClock(),
    )
    # The wall clock can expire while the run is opening, in which case the
    # model returns already closed, no premise receives its value, and no
    # agent is registered.
    with suppress(RunClosedError):
        control._open_premises(dict(opening))
        # After the premises, so that each agent's one notification covers
        # the values they opened with.
        for agent in agents or ():
            control.register_agent(agent)
    return Model(reader=control.reader, control=control)

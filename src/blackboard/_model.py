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
    DuplicateAgentError,
    RunClosedError,
    RunLimits,
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
    board_id: str,
    store: BoardStore,
    regions: Iterable[Level | Premise],
    premises: Mapping[str, object],
    agents: Iterable[Agent] | None = None,
    admission_rule: AdmissionRule | None = None,
    termination_predicate: TerminationPredicate | None = None,
    limits: RunLimits,
    clock: Clock | None = None,
) -> Model:
    """Opens a run and returns the model.

    ``limits`` carries the run's wall clock and idle durations.

    ``board_id`` names this board inside the store. The caller supplies it,
    because only the caller knows what identifies a run in its own system.
    The library never reads it.

    ``store`` says where the record is kept and has no default, because a run
    whose record no second process can read is not a shared solution model.
    One store serves every board an application runs. ``SqliteStore`` serves
    one machine; an adapter against your own database serves a deployment.

    ``agents`` names the agents the run starts with, and naming one twice
    is refused, because a roster is one list written at one moment. An agent
    that registers again later is a returning agent rather than a mistake,
    and replaces its own declaration.

    ``agents`` names the agents the run starts with. Each is registered
    once the premises hold their opening values, so each receives one
    notification covering everything already on the board. An agent that
    joins a run already under way registers itself through
    ``Control.register_agent`` instead.

    ``premises`` gives every declared premise its opening value, naming
    each one exactly once. Those writes bypass admission, because they are
    the application's own input rather than a proposal from a writer.

    No agent is registered yet, so opening the premises wakes nobody; an
    agent registering afterwards is woken then. With no admission rule a
    write is accepted subject to the region existing, the limits holding,
    and the run being open; with no termination predicate the run closes
    when nothing has happened for the idle limit; with no clock the
    operating system clock serves.
    """
    named = [a.name for a in agents or ()]
    twice = sorted({n for n in named if named.count(n) > 1})
    if twice:
        raise DuplicateAgentError(
            "the roster names " + ", ".join(repr(n) for n in twice) + " more than once"
        )
    control = Control(
        regions=regions,
        admission_rule=admission_rule,
        termination_predicate=termination_predicate,
        limits=limits,
        board_id=board_id,
        store=store,
        clock=clock if clock is not None else SystemClock(),
    )
    # The wall clock can expire while the run is opening, in which case the
    # model returns already closed, no premise receives its value, and no
    # agent is registered.
    with suppress(RunClosedError):
        control._open_premises(dict(premises))
        # After the premises, so that each agent's one notification covers
        # the values they opened with.
        for agent in agents or ():
            control.register_agent(agent)
    return Model(reader=control.reader, control=control)

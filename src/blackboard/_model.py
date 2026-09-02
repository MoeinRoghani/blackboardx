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

import json
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass

from blackboard._board import (
    DuplicateRegionError,
    Level,
    Premise,
    UndeclaredRegionError,
)
from blackboard._clock import Clock, SystemClock
from blackboard._control import (
    AdmissionRule,
    Agent,
    BoardReader,
    BoardStore,
    Control,
    DuplicateAgentError,
    PremiseError,
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
    and replaces its own declaration. Each is registered once the premises
    hold their opening values, so each receives one notification covering
    everything already on the board. An agent that joins a run already under
    way registers itself through ``Control.register_agent`` instead.

    Everything the arguments alone can settle is settled before the store is
    touched: the regions naming each other once, the opening premises being
    exactly the declared premises, each opening value being JSON, the roster
    naming each agent once, and every region an agent subscribes to or writes
    to being declared. A call that raises here has written nothing, so the
    corrected call opens the board it was going to open.

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
    declarations = list(regions)
    roster = list(agents or ())
    _check(declarations, premises, roster)
    control = Control(
        regions=declarations,
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
    try:
        with suppress(RunClosedError):
            control._open_premises(dict(premises))
            # After the premises, so that each agent's one notification
            # covers the values they opened with.
            for agent in roster:
                control.register_agent(agent)
    except BaseException as failed:
        # _check reads everything this can still raise on, so reaching here
        # means the store or a clock failed. Closing the run cancels the two
        # timers the control component armed, which would otherwise outlive a
        # model the caller never received.
        control.abort(f"creation failed: {failed}")
        raise
    return Model(reader=control.reader, control=control)


def _check(
    declarations: list[Level | Premise],
    premises: Mapping[str, object],
    roster: list[Agent],
) -> None:
    """Refuses a configuration that cannot open, before the store is touched.

    Everything here is decided by the arguments alone. Reaching the store
    first would leave a board behind that the corrected call then collides
    with, and the caller never receives a model to abort.
    """
    names = [region.name for region in declarations]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise DuplicateRegionError(
            "the regions name " + ", ".join(repr(n) for n in twice) + " more than once"
        )
    declared_premises = {r.name for r in declarations if isinstance(r, Premise)}
    levels = {r.name for r in declarations if isinstance(r, Level)}
    missing = sorted(declared_premises - set(premises))
    if missing:
        raise PremiseError(
            "the opening premises miss " + ", ".join(repr(n) for n in missing)
        )
    undeclared = sorted(set(premises) - declared_premises)
    if undeclared:
        raise PremiseError(
            "the opening premises name undeclared "
            + ", ".join(repr(n) for n in undeclared)
        )
    for name, value in premises.items():
        try:
            json.dumps(value)
        except TypeError as carried:
            raise PremiseError(
                f"the opening value for {name!r} is not JSON: {carried}"
            ) from carried

    agent_names = [a.name for a in roster]
    repeated = sorted({n for n in agent_names if agent_names.count(n) > 1})
    if repeated:
        raise DuplicateAgentError(
            "the roster names "
            + ", ".join(repr(n) for n in repeated)
            + " more than once"
        )
    for agent in roster:
        _refuse_unknown(agent.name, "subscribe to", agent.subscribes_to, set(names))
        _refuse_unknown(agent.name, "write to", agent.writes_to, levels)


def _refuse_unknown(
    agent: str, doing: str, named: Iterable[str] | None, allowed: set[str]
) -> None:
    unknown = sorted(set(named or ()) - allowed)
    if unknown:
        kind = "level" if doing == "write to" else "region"
        raise UndeclaredRegionError(
            ", ".join(repr(n) for n in unknown)
            + f" names no declared {kind}, so {agent!r} cannot {doing} it"
        )

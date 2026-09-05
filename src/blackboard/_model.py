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
import warnings
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass

from blackboard._board import (
    DuplicateRegionError,
    Level,
    Premise,
    RegionKindError,
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
    RunOutcome,
    TerminationPredicate,
)


@dataclass(frozen=True)
class Model:
    """A handle to a board that lives in the store: its reader and its control.

    **It holds nothing.** Every call reads what it needs from the store, so
    holding one keeps no run open, caches no registry, and reserves nothing.
    Build one wherever it is convenient, on any process, and discard it; the
    board is unaffected either way. Dropping the last one closes nothing,
    because a run ends on its deadlines or on ``control.abort`` and on nothing
    else.

    The name invites the opposite reading, so it is said plainly here. The
    same shape appears elsewhere: a boto3 ``Bucket`` is a handle to something
    living in S3 rather than a copy of it, and a LangGraph graph is stateless
    with the thread identifier travelling in the config on every call.

    Board reads go to ``reader`` directly, consume no control capacity, and
    cannot be refused. Writes, acknowledgments, and lifecycle calls go
    through ``control``.

    ``reader`` is ``control.reader``, and is a field of its own so a component
    that only reads takes the reader alone. ``board_id`` names the board, so a
    registry keyed by it does not have to repeat the string the caller passed.
    """

    board_id: str
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
    on_open: Callable[[Model], None] | None = None,
    on_closed: Callable[[RunOutcome], None] | None = None,
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
    touched: the declarations naming each region once, the opening premises being
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
        on_closed=on_closed,
    )
    model = Model(board_id=board_id, reader=control.reader, control=control)
    # The wall clock can expire while the run is opening, in which case the
    # model returns already closed, no premise receives its value, and no
    # agent is registered.
    try:
        with suppress(RunClosedError):
            control._open_premises(dict(premises))
            _opened(on_open, model)
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
    return model


def attach_model(
    *,
    board_id: str,
    store: BoardStore,
    regions: Iterable[Level | Premise],
    agents: Iterable[Agent] | None = None,
    admission_rule: AdmissionRule | None = None,
    termination_predicate: TerminationPredicate | None = None,
    limits: RunLimits,
    clock: Clock | None = None,
    on_open: Callable[[Model], None] | None = None,
    on_closed: Callable[[RunOutcome], None] | None = None,
) -> Model:
    """Opens a run over a board that already holds a record.

    ``create_model`` declares the regions, so it opens a board once. This
    opens a run over one that exists, which is what a replica replacing the
    one that died does, and what a service resuming a run it evicted does.

    There is no ``premises`` argument. The record already holds their values,
    and the versions they are at.

    ``regions`` still names what the run expects, and is checked against what
    the record holds. A board holding no regions is refused rather than
    quietly created, because attaching to nothing would build a run whose
    every write is rejected. A name or a kind that disagrees with the record
    is refused naming the region.

    What the record holds carries over: the regions, the contributions, the
    premise values and their versions, the sequence, the idempotency keys,
    and how far each agent has been notified and has answered. What the
    process held does not: the agent registry and the audit.

    So an agent registered against the attached run resumes from what it
    answered rather than being told the whole board again, and an agent that
    had answered everything is registered without being woken.
    """
    warnings.warn(
        "attach_model is deprecated and may be removed on or after 2026-12-05. "
        "create_model converges on a board the store already holds, so it "
        "serves both intents and a process need not know which it is.",
        DeprecationWarning,
        stacklevel=2,
    )
    declarations = list(regions)
    roster = list(agents or ())
    _check_roster(declarations, roster)
    _agree(store.read_regions(board_id), declarations, board_id)
    control = Control(
        regions=declarations,
        admission_rule=admission_rule,
        termination_predicate=termination_predicate,
        limits=limits,
        board_id=board_id,
        store=store,
        clock=clock if clock is not None else SystemClock(),
        adopt=True,
        on_closed=on_closed,
    )
    model = Model(board_id=board_id, reader=control.reader, control=control)
    try:
        with suppress(RunClosedError):
            _opened(on_open, model)
            for agent in roster:
                control.register_agent(agent)
    except BaseException as failed:
        control.abort(f"attaching failed: {failed}")
        raise
    return model


def _opened(on_open: Callable[[Model], None] | None, model: Model) -> None:
    """Tells the caller the run exists, before any agent is woken.

    Application code at the library's boundary, so an exception here is
    suppressed rather than left to abort a run that has already opened.
    """
    if on_open is None:
        return
    with suppress(Exception):
        on_open(model)


def _agree(
    held: list[Level | Premise], declared: list[Level | Premise], board_id: str
) -> None:
    """Refuses a board whose record does not hold what the caller declared."""
    if not held:
        raise UndeclaredRegionError(
            f"the store holds no regions for {board_id!r}, so there is no run to"
            " attach to. create_model opens a board that does not exist yet."
        )
    on_record = {r.name: type(r).__name__ for r in held}
    wanted = {r.name: type(r).__name__ for r in declared}
    missing = sorted(set(wanted) - set(on_record))
    if missing:
        raise UndeclaredRegionError(
            "the record holds no "
            + ", ".join(repr(n) for n in missing)
            + f" on {board_id!r}"
        )
    absent = sorted(set(on_record) - set(wanted))
    if absent:
        raise UndeclaredRegionError(
            "the record holds "
            + ", ".join(repr(n) for n in absent)
            + ", which this run does not declare"
        )
    wrong = sorted(n for n, kind in wanted.items() if on_record[n] != kind)
    if wrong:
        named = ", ".join(
            f"{n!r} is a {on_record[n].lower()} on the record" for n in wrong
        )
        raise RegionKindError(named)


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

    _check_roster(declarations, roster)


def _check_roster(declarations: list[Level | Premise], roster: list[Agent]) -> None:
    """Refuses a roster that names itself twice or names a region nobody declared."""
    names = [region.name for region in declarations]
    levels = {r.name for r in declarations if isinstance(r, Level)}
    agent_names = [a.name for a in roster]
    repeated = sorted({n for n in agent_names if agent_names.count(n) > 1})
    if repeated:
        raise DuplicateAgentError(
            "the roster names "
            + ", ".join(repr(n) for n in repeated)
            + " more than once"
        )
    for agent in roster:
        _refuse_unknown(agent.name, agent.subscribes_to, set(names))
        for named in agent.writes_to or ():
            if named not in names:
                raise UndeclaredRegionError(
                    f"{named!r} is not a declared region, so {agent.name!r} "
                    "cannot write to it"
                )
            if named not in levels:
                raise RegionKindError(
                    f"{named!r} names a premise, and {agent.name!r} can only "
                    "be permitted to write to a level"
                )


def _refuse_unknown(agent: str, named: Iterable[str] | None, allowed: set[str]) -> None:
    """Refuses a subscription to a region nobody declared, naming all of them.

    The wording matches ``Control.register_agent``, because the roster and a
    later registration are the same declaration arriving at two moments and
    must answer a mistake the same way.
    """
    unknown = sorted(set(named or ()) - allowed)
    if unknown:
        raise UndeclaredRegionError(
            ", ".join(repr(n) for n in unknown)
            + f" is not a declared region, so {agent!r} cannot subscribe to it"
        )

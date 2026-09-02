"""The control component.

A write made through the control component passes the application's
admission rule before the board sequences it. The rule sees the proposed
write with a read handle on the board and returns accept or a reasoned
rejection. An admitted level write is sequenced and audited. An admitted
premise write may still fail with a conflict, which returns to the writer
unaudited. A rejected write returns its reason to the writer, never reaches
the board, and is audited without a sequence number.

An admitted premise write also notifies the registered agents, each
through its batch window, except the agent that wrote the change. Which
agents hold an unacknowledged notification is tracked here.

The run closes in exactly one of three states: settled, wall clock
expired, or aborted. It closes on silence: every write, premise write,
registration and acknowledgment pushes the idle deadline out, and when
that deadline passes the control component consults the application's
termination predicate, which with none supplied lets the run close.
Sequencing rechecks closure under the lock, so no write lands after the
closing event, and reads and the audit stay open on a closed run.

The agent registry, the outstanding notifications, the audit, and both
deadlines are held in this process. The board is given, not owned, and it
is the only part of a run that a second process can read.

The rule runs without the control component's lock, so two writes judged
at the same moment are both judged against the board as it was before
either landed. A premise write closes that window with its expected
version; a level write does not, so a rule refusing duplicates bounds
concurrent duplicates rather than preventing them.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from functools import partial
from typing import NewType, Protocol, TypeAlias, runtime_checkable

from blackboard._board import (
    BlackboardError,
    BoardChange,
    Conflict,
    Contribution,
    Deleted,
    Level,
    Premise,
    PremiseState,
    RegionKindError,
    UndeclaredRegionError,
    UnsetPremiseError,
    Written,
)
from blackboard._clock import Clock, ScheduledCall


class BoardStore(Protocol):
    """The operations the control component performs on a store.

    A store holds many boards. Every call names the board it acts on, so one
    connection to a database serves every board an application runs.

    ``InMemoryStore`` and ``SqliteStore`` satisfy it, as does any adapter an
    application writes against its own database. The control component names
    no concrete type, and holds every implementation to one conformance suite.

    Content crosses this protocol as JSON. An implementation returns what
    JSON carries, so a tuple written comes back a list, and content JSON
    cannot carry raises ``TypeError`` before anything is stored.
    """

    def declare(self, board_id: str, region: Level | Premise) -> None:
        """Creates a region on one board."""
        ...

    def append(
        self,
        board_id: str,
        level: str,
        content: object,
        idempotency_key: str | None = None,
    ) -> Written:
        """Adds one contribution to a level and returns where it landed.

        ``idempotency_key`` names one write on one board. A key the store has
        already written answers with what that write produced, marked
        ``repeated``, and adds nothing. A key that named a different region
        raises ``IdempotencyKeyError``, because that is a mistake rather than
        a retry. Without a key nothing is deduplicated.
        """
        ...

    def set(
        self,
        board_id: str,
        premise: str,
        value: object,
        expected_version: int,
        idempotency_key: str | None = None,
    ) -> Written | Conflict:
        """Replaces a premise's value under the version the caller expects.

        ``idempotency_key`` works as it does for ``append``. A conflict
        writes nothing, so it uses up no key.
        """
        ...

    def read_level(
        self,
        board_id: str,
        level: str,
        from_sequence: int = 0,
        limit: int | None = None,
    ) -> list[Contribution]:
        """Returns a level's contributions from the sequence bound, inclusive.

        ``limit`` caps how many come back. A caller continues from one past
        the last sequence it received, which an offset could not do, because
        an offset shifts when a concurrent write lands and a sequence number
        does not.
        """
        ...

    def read_premise(self, board_id: str, premise: str) -> PremiseState:
        """Returns a premise's current value and version."""
        ...

    def read_board(
        self, board_id: str, from_sequence: int = 0, limit: int | None = None
    ) -> list[BoardChange]:
        """Returns every write to every region, in sequence order, from the bound."""
        ...

    def delete(self, board_id: str) -> Deleted:
        """Removes one board's regions, record, premise values, and counter.

        Everything or nothing: a caller that gets an answer got a board that
        is gone. A board the store never held names nothing rather than
        failing, so a delete that runs twice is safe.

        Nothing in the library calls this. Deleting is a retention decision
        and the control component makes none.
        """
        ...

    def read_regions(self, board_id: str) -> list[Level | Premise]:
        """Returns the regions declared on one board, with their kinds.

        A store records a region's name and its kind, and nothing else. A
        premise comes back with the default batch window whatever window it
        was declared with, because the window tells the control component
        when to notify and is no part of the record.

        A board nobody declared a region on returns nothing rather than
        refusing, because a board comes into being by being written to.
        """
        ...


class BoardReader(Protocol):
    """The four read operations, the handle the admission rule receives."""

    def read_level(
        self, level: str, from_sequence: int = 0, limit: int | None = None
    ) -> list[Contribution]:
        """Returns a level's contributions from the sequence bound, inclusive."""
        ...

    def read_premise(self, premise: str) -> PremiseState:
        """Returns a premise's current value and version."""
        ...

    def read_board(
        self, from_sequence: int = 0, limit: int | None = None
    ) -> list[BoardChange]:
        """Returns every write to every region, in sequence order, from the bound."""
        ...

    def read_regions(self) -> list[Level | Premise]:
        """Returns the regions declared on this board, with their kinds."""
        ...


@dataclass(frozen=True)
class ProposedContribution:
    """A level write as the admission rule sees it, before sequencing."""

    writer: str
    level: str
    content: object


@dataclass(frozen=True)
class ProposedPremiseWrite:
    """A premise write as the admission rule sees it, before sequencing."""

    writer: str
    premise: str
    value: object
    expected_version: int


ProposedWrite: TypeAlias = ProposedContribution | ProposedPremiseWrite
"""A proposed write of either kind, as the admission rule receives it."""


@dataclass(frozen=True)
class Accept:
    """The admission rule's verdict admitting a proposed write."""


@dataclass(frozen=True)
class Reject:
    """The admission rule's verdict refusing a proposed write, with its reason."""

    reason: str


AdmissionRule: TypeAlias = Callable[[ProposedWrite, "BoardReader"], Accept | Reject]
"""The rule the control component calls on every proposed write."""


class RejectionCause(Enum):
    """Why the control component refused a write.

    Every cause is a decision the run made about a write it understood. What
    the application's own configuration settles, such as a region nobody
    declared, raises instead.

    ``ADMISSION``: the admission rule rejected it. ``NOT_PERMITTED``: the
    writing agent did not declare that level. ``RUN_CLOSED``: the run has
    closed.
    """

    ADMISSION = "admission"
    NOT_PERMITTED = "not_permitted"
    RUN_CLOSED = "run_closed"


@dataclass(frozen=True)
class Rejected:
    """A write the control component refused, with the cause and its reason."""

    cause: RejectionCause
    reason: str


@dataclass(frozen=True)
class WriteAccepted:
    """The audit record of a write that reached the board."""

    at: datetime
    writer: str
    region: str
    sequence: int


@dataclass(frozen=True)
class WriteRejected:
    """The audit record of a refused write; it never reached the board."""

    at: datetime
    writer: str
    region: str
    cause: RejectionCause
    reason: str


NotificationId = NewType("NotificationId", int)
"""The identifier an acknowledgment names."""


@dataclass(frozen=True)
class Notification:
    """One notification: the range it covers and the regions that changed.

    It carries no values. The agent reads the board itself, over any range
    and any region, not only the regions this names.
    """

    notification_id: NotificationId
    board_id: str
    agent: str
    from_sequence: int
    to_sequence: int
    regions: frozenset[str]


@dataclass(frozen=True)
class Agent:
    """An agent declaration: identity, delivery, and what it wants.

    ``subscribes_to`` names the regions, of either kind, whose changes wake
    this agent, and naming any excludes every region not named. Omitting it
    subscribes the agent to every premise and to no level, which is the
    common case: a premise holds something the work was given, which bears
    on any agent's work, while another agent's conclusion does not.
    ``writes_to`` names the levels the agent may write to, and omitting it
    permits every level.

    The control component invokes ``notify`` to deliver a notification,
    holding no lock, on the thread that closed the batch window or, when
    deliveries chain, on a thread already draining them; two notifications
    to one agent can therefore arrive on two threads at once. The callback
    may run the whole agent cycle inline or hand the notification to the
    application's own execution. A callback that blocks on ``wait_closed``
    holds the run open, because its own unacknowledged notification counts
    as outstanding work.

    Both ``subscribes_to`` and ``writes_to`` are read on every write and
    every notification, so each is kept as a ``frozenset`` of what was given.
    A declaration built from a generator therefore behaves like one built
    from a set, rather than emptying itself on first use.
    """

    name: str
    notify: Callable[[Notification], None]
    subscribes_to: Iterable[str] | None = None
    writes_to: Iterable[str] | None = None

    def __post_init__(self) -> None:
        for field_name in ("subscribes_to", "writes_to"):
            given = getattr(self, field_name)
            if given is not None and not isinstance(given, frozenset):
                object.__setattr__(self, field_name, frozenset(given))


class DuplicateAgentError(BlackboardError):
    """One roster named the same agent twice.

    A roster is one list written at one moment, so a repeat in it is a
    mistake. Registering a name again later is a returning agent rather than
    a duplicate, and replaces that agent's declaration.
    """


class PremiseError(BlackboardError):
    """The opening premises are not exactly the premises that were declared."""


class UnknownNotificationError(BlackboardError):
    """The named notification was never issued to the acknowledging agent."""


@dataclass(frozen=True)
class NotificationDispatched:
    """The audit record of one notification leaving the control component."""

    at: datetime
    notification: Notification


@dataclass(frozen=True)
class NotificationAcknowledged:
    """The audit record of an agent reporting that it stopped."""

    at: datetime
    agent: str
    notification_id: NotificationId


class RunClosedError(BlackboardError):
    """A declaration or registration reached a run that has closed."""


class TerminationDecision(Enum):
    """The termination predicate's answer when no work is outstanding."""

    CONTINUE = "continue"
    COMPLETE = "complete"


TerminationPredicate: TypeAlias = Callable[["BoardReader"], TerminationDecision]
"""The predicate the control component calls when no work is outstanding."""


@dataclass(frozen=True, kw_only=True)
class RunLimits:
    """The two limits on a run, both durations, both required.

    Time is the only bound. A count of writes limits the cause of a
    notification and a count of notifications limits the effect, and
    limiting the effect can leave a change that no agent hears while the run
    is still open, which ends the shared record without closing the run.
    """

    wall_clock: timedelta
    idle: timedelta

    def __post_init__(self) -> None:
        if self.wall_clock <= timedelta(0):
            raise ValueError("the wall clock limit is a positive duration")
        if self.idle <= timedelta(0):
            raise ValueError("the idle limit is a positive duration")


@dataclass(frozen=True)
class Settled:
    """Nothing happened for the idle limit, so the run closed."""

    unfinished: frozenset[str] = frozenset()


@dataclass(frozen=True)
class WallClockExpired:
    """The wall clock limit passed while the run was open."""

    unfinished: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Aborted:
    """A caller closed the run."""

    reason: str
    unfinished: frozenset[str] = frozenset()


RunOutcome: TypeAlias = Settled | WallClockExpired | Aborted
"""The three states a run closes in.

Each names the agents that did not finish, meaning those holding an
unacknowledged notification. Why a run ended and which agents failed to
finish are separate facts, and a run settles normally while one agent
never returns.
"""


@dataclass(frozen=True)
class PremiseOpened:
    """The audit record of one premise receiving its opening value."""

    at: datetime
    premise: str
    sequence: int
    version: int


@dataclass(frozen=True)
class RunClosed:
    """The audit record of the run closing, with its outcome."""

    at: datetime
    outcome: RunOutcome


AuditEvent: TypeAlias = (
    PremiseOpened
    | WriteAccepted
    | WriteRejected
    | NotificationDispatched
    | NotificationAcknowledged
    | RunClosed
)
"""Every kind of event the audit records."""

_Delivery: TypeAlias = tuple[Callable[[Notification], None], Notification]


@dataclass
class _AgentState:
    declaration: Agent
    cursor: int
    pending: dict[str, datetime] = field(default_factory=dict)
    window_call: ScheduledCall | None = None
    window_due: datetime | None = None
    window_generation: int = 0


@dataclass
class _Outstanding:
    to_sequence: int


def _subscribed(state: _AgentState, region: str, kind: _RegionKind) -> bool:
    # Omitting the declaration subscribes an agent to every premise and to
    # no level, because a premise bears on any agent's work while another
    # agent's conclusion does not.
    declared = state.declaration.subscribes_to
    if declared is not None:
        return region in set(declared)
    return kind is _RegionKind.PREMISE


def _accept_every_write(
    proposed: ProposedWrite, reader: BoardReader
) -> Accept | Reject:
    return Accept()


class _RegionKind(Enum):
    LEVEL = "level"
    PREMISE = "premise"


class _BoundReader:
    """A read handle on one board of a store.

    The store names a board on every call. A rule, a predicate and the model
    read one board and should not repeat its identifier, so this binds it.
    """

    def __init__(self, store: BoardStore, board_id: str) -> None:
        self._store = store
        self._board_id = board_id

    def read_level(
        self, level: str, from_sequence: int = 0, limit: int | None = None
    ) -> list[Contribution]:
        """Returns a level's contributions from the sequence bound, inclusive."""
        return self._store.read_level(self._board_id, level, from_sequence, limit)

    def read_premise(self, premise: str) -> PremiseState:
        """Returns a premise's current value and version."""
        return self._store.read_premise(self._board_id, premise)

    def read_board(
        self, from_sequence: int = 0, limit: int | None = None
    ) -> list[BoardChange]:
        """Returns every write to every region, in sequence order, from the bound."""
        return self._store.read_board(self._board_id, from_sequence, limit)

    def read_regions(self) -> list[Level | Premise]:
        """Returns the regions declared on this board, with their kinds."""
        return self._store.read_regions(self._board_id)


def reader_for(store: BoardStore, board_id: str) -> BoardReader:
    """Returns a read handle over one board of a store, with no run behind it.

    A read needs the record and not the run, so a process holding the store
    can serve one for any board in it. Writing needs a ``Control``.
    """
    return _BoundReader(store, board_id)


@runtime_checkable
class AgentBoard(Protocol):
    """One board, as one agent sees it: what an agent body is written against.

    ``BoardClient`` satisfies this over HTTP and ``Control.as_agent`` returns
    it in process, so a body written once serves either deployment. Every
    method omits the agent's own name, because the object already carries it.

    Reads are the four ``BoardReader`` has. Writes are the three
    ``Control`` has, without the identity argument.
    """

    @property
    def board_id(self) -> str:
        """The board this object reads and writes."""
        ...

    def read_regions(self) -> list[Level | Premise]:
        """Returns the regions declared on this board, with their kinds."""
        ...

    def read_level(
        self, level: str, from_sequence: int = 0, limit: int | None = None
    ) -> list[Contribution]:
        """Returns a level's contributions from the sequence bound, inclusive."""
        ...

    def read_premise(self, premise: str) -> PremiseState:
        """Returns a premise's current value and version."""
        ...

    def read_board(
        self, from_sequence: int = 0, limit: int | None = None
    ) -> list[BoardChange]:
        """Returns every write to every region, in sequence order, from the bound."""
        ...

    def write(
        self, level: str, content: object, idempotency_key: str | None = None
    ) -> Written | Rejected:
        """Proposes a contribution to a level, as this object's agent."""
        ...

    def set_premise(
        self,
        premise: str,
        value: object,
        expected_version: int,
        idempotency_key: str | None = None,
    ) -> Written | Conflict | Rejected:
        """Sets a premise, provided it is still at ``expected_version``."""
        ...

    def ack(self, notification_id: NotificationId | int) -> None:
        """Records that this agent finished responding to a notification."""
        ...


class _AgentBoard:
    """One agent's view of a live run. What ``Control.as_agent`` returns."""

    def __init__(self, control: Control, agent: str) -> None:
        self._control = control
        self._agent = agent

    @property
    def board_id(self) -> str:
        return self._control.board_id

    def read_regions(self) -> list[Level | Premise]:
        return self._control.reader.read_regions()

    def read_level(
        self, level: str, from_sequence: int = 0, limit: int | None = None
    ) -> list[Contribution]:
        return self._control.reader.read_level(level, from_sequence, limit)

    def read_premise(self, premise: str) -> PremiseState:
        return self._control.reader.read_premise(premise)

    def read_board(
        self, from_sequence: int = 0, limit: int | None = None
    ) -> list[BoardChange]:
        return self._control.reader.read_board(from_sequence, limit)

    def write(
        self, level: str, content: object, idempotency_key: str | None = None
    ) -> Written | Rejected:
        return self._control.write(level, content, idempotency_key, writer=self._agent)

    def set_premise(
        self,
        premise: str,
        value: object,
        expected_version: int,
        idempotency_key: str | None = None,
    ) -> Written | Conflict | Rejected:
        return self._control.set_premise(
            premise, value, expected_version, idempotency_key, writer=self._agent
        )

    def ack(self, notification_id: NotificationId | int) -> None:
        self._control.ack(notification_id, agent=self._agent)


class Control:
    """The control component's write path, over the board it is given.

    The board holds the record and outlives this object where the board is
    a database. Everything else a run knows, which agents registered, what
    each is owed, the audit, and the two deadlines, is held here and ends
    with the process.
    """

    def __init__(
        self,
        *,
        regions: Iterable[Level | Premise] = (),
        admission_rule: AdmissionRule | None = None,
        termination_predicate: TerminationPredicate | None = None,
        limits: RunLimits,
        board_id: str,
        store: BoardStore,
        clock: Clock,
        adopt: bool = False,
        on_closed: Callable[[RunOutcome], None] | None = None,
    ) -> None:
        resolved = limits
        self._board_id = board_id
        self._store: BoardStore = store
        self._reader = _BoundReader(store, board_id)
        self._clock = clock
        self._admission_rule = (
            admission_rule if admission_rule is not None else _accept_every_write
        )
        self._lock = threading.Lock()
        self._kinds: dict[str, _RegionKind] = {}
        self._batch_windows: dict[str, timedelta] = {}
        self._audit: list[AuditEvent] = []
        self._last_sequence = 0
        self._agents: dict[str, _AgentState] = {}
        self._issued: set[tuple[str, NotificationId]] = set()
        self._outstanding: dict[tuple[str, NotificationId], _Outstanding] = {}
        self._next_notification_id = 1
        self._closing: list[RunOutcome] = []
        self._delivery_queue: deque[_Delivery] = deque()
        self._delivering = threading.local()
        self._termination_predicate = termination_predicate
        self._limits = resolved
        self._outcome: RunOutcome | None = None
        self._on_closed = on_closed
        self._wall_call: ScheduledCall | None = None
        self._idle_call: ScheduledCall | None = None
        self._idle_generation = 0
        self._condition = threading.Condition(self._lock)
        if adopt:
            # The record already holds these regions, so recording their
            # kinds is all that is left. The sequence continues from what the
            # record ends at, or a notification would cover a range that has
            # already been read.
            for region in regions:
                self._record_kind(region)
            written = store.read_board(board_id)
            self._last_sequence = written[-1].sequence if written else 0
        else:
            for region in regions:
                self.declare(region)
        self._wall_call = clock.call_at(
            clock.now() + resolved.wall_clock, self._wall_clock_expired
        )

    @property
    def board_id(self) -> str:
        """The board this control component runs over."""
        return self._board_id

    def as_agent(self, name: str) -> AgentBoard:
        """Returns this board as one agent sees it.

        The object carries the agent's name, so its methods are the ones
        ``BoardClient`` has and an agent body written against ``AgentBoard``
        runs either in this process or against a blackboard over HTTP.

        The agent need not be registered. Registering decides what wakes it;
        this decides what it writes as.
        """
        return _AgentBoard(self, name)

    @property
    def reader(self) -> BoardReader:
        """The board's read side. Reads bypass the control component entirely."""
        return self._reader

    def declare(self, region: Level | Premise) -> None:
        """Creates a region on the board and records its kind."""
        with self._lock:
            if self._outcome is not None:
                raise RunClosedError("the run has closed")
            self._store.declare(self._board_id, region)
            self._record_kind(region)

    def _record_kind(self, region: Level | Premise) -> None:
        kind = _RegionKind.LEVEL if isinstance(region, Level) else _RegionKind.PREMISE
        self._kinds[region.name] = kind
        self._batch_windows[region.name] = region.batch_window

    def register_agent(self, agent: Agent) -> None:
        """Registers an agent and wakes it.

        The agent is out of date with everything already on the board, so
        registering issues one notification covering the regions it
        subscribes to that already hold something. Its cursor starts at
        zero, since it has read nothing.

        Registering a name that is already registered replaces that agent's
        declaration, including its callback and its subscriptions, which is
        what an agent that restarted or moved needs. Its cursor survives,
        because it has not forgotten what it acknowledged. Whatever
        notification it was still holding is discarded, and the one issued
        here covers everything since that cursor. One notification says
        what several would have said, because a notification carries no
        values.
        """
        deliveries: list[_Delivery] = []
        with self._lock:
            if self._outcome is not None:
                raise RunClosedError("the run has closed")
            for named in agent.subscribes_to or ():
                if named not in self._kinds:
                    raise UndeclaredRegionError(
                        f"{named!r} is not a declared region, so {agent.name!r} "
                        "cannot subscribe to it"
                    )
            for named in agent.writes_to or ():
                if named not in self._kinds:
                    raise UndeclaredRegionError(
                        f"{named!r} is not a declared region, so {agent.name!r} "
                        "cannot write to it"
                    )
                if self._kinds[named] is not _RegionKind.LEVEL:
                    raise RegionKindError(
                        f"{named!r} names a premise, and {agent.name!r} can only "
                        "be permitted to write to a level"
                    )
            returning = self._agents.get(agent.name)
            if returning is not None:
                self._forget_outstanding_locked(agent.name)
                if returning.window_call is not None:
                    returning.window_call.cancel()
            state = _AgentState(
                declaration=agent, cursor=0 if returning is None else returning.cursor
            )
            self._agents[agent.name] = state
            now = self._clock.now()
            for name, kind in self._kinds.items():
                if not _subscribed(state, name, kind):
                    continue
                if kind is _RegionKind.PREMISE and self._has_value(name):
                    state.pending[name] = now + self._batch_windows[name]
                elif kind is _RegionKind.LEVEL and self._store.read_level(
                    self._board_id, name
                ):
                    state.pending[name] = now
            delivery = self._evaluate_dispatch_locked(state, now)
            if delivery is not None:
                deliveries.append(delivery)
        # Registering never completes a run. An agent joining is the start of
        # work, not the end of it.
        self._deliver(deliveries)

    def _has_value(self, premise: str) -> bool:
        # Callers hold self._lock.
        try:
            self._store.read_premise(self._board_id, premise)
        except UnsetPremiseError:
            return False
        return True

    def write(
        self,
        level: str,
        content: object,
        idempotency_key: str | None = None,
        *,
        writer: str,
    ) -> Written | Rejected:
        """Runs one level write through admission and, if admitted, the board.

        ``idempotency_key`` names one write. A key already written answers
        with what that write produced, marked ``repeated``, and changes
        nothing: no audit event, no notification, and no push of the idle
        deadline, because nothing happened. A key that named a different
        region raises ``IdempotencyKeyError``, because the caller chose it.
        """
        refusal = self._refuse_region(writer, level, _RegionKind.LEVEL)
        if refusal is not None:
            return refusal
        with self._lock:
            writer_state = self._agents.get(writer)
            declared = (
                None if writer_state is None else writer_state.declaration.writes_to
            )
            if declared is not None and level not in set(declared):
                return self._reject_locked(
                    writer,
                    level,
                    RejectionCause.NOT_PERMITTED,
                    f"{writer!r} may not write to {level!r}",
                )
        proposed = ProposedContribution(writer=writer, level=level, content=content)
        verdict = self._admission_rule(proposed, self._reader)
        deliveries: list[_Delivery] = []
        result: Written | Rejected
        if isinstance(verdict, Reject):
            result = self._reject(
                writer, level, RejectionCause.ADMISSION, verdict.reason
            )
        else:
            with self._lock:
                gate = self._sequencing_gate_locked(writer, level)
                if gate is not None:
                    result = gate
                else:
                    # A key naming two regions is the caller's mistake, so it
                    # raises here as it does in the store. ADR 0016.
                    result = self._store.append(
                        self._board_id, level, content, idempotency_key
                    )
                    if result.repeated:
                        # Nothing reached the board, so nothing about the run
                        # changed either.
                        return result
                    self._last_sequence = result.sequence
                    self._audit.append(
                        WriteAccepted(
                            at=self._clock.now(),
                            writer=writer,
                            region=level,
                            sequence=result.sequence,
                        )
                    )
                    deliveries = self._note_region_change(level, writer)
        self._deliver(deliveries)
        self._check_completion()
        return result

    def set_premise(
        self,
        premise: str,
        value: object,
        expected_version: int,
        idempotency_key: str | None = None,
        *,
        writer: str,
    ) -> Written | Conflict | Rejected:
        """Runs one premise write through admission and, if admitted, the board.

        ``idempotency_key`` works as it does for ``write``. A conflict stores
        nothing, so it uses up no key.
        """
        refusal = self._refuse_region(writer, premise, _RegionKind.PREMISE)
        if refusal is not None:
            return refusal
        proposed = ProposedPremiseWrite(
            writer=writer,
            premise=premise,
            value=value,
            expected_version=expected_version,
        )
        verdict = self._admission_rule(proposed, self._reader)
        deliveries: list[_Delivery] = []
        result: Written | Conflict | Rejected
        if isinstance(verdict, Reject):
            result = self._reject(
                writer, premise, RejectionCause.ADMISSION, verdict.reason
            )
        else:
            with self._lock:
                gate = self._sequencing_gate_locked(writer, premise)
                if gate is not None:
                    result = gate
                else:
                    result = self._store.set(
                        self._board_id,
                        premise,
                        value,
                        expected_version,
                        idempotency_key,
                    )
                    if isinstance(result, Written) and result.repeated:
                        return result
                    if isinstance(result, Written):
                        self._last_sequence = result.sequence
                        self._audit.append(
                            WriteAccepted(
                                at=self._clock.now(),
                                writer=writer,
                                region=premise,
                                sequence=result.sequence,
                            )
                        )
                        deliveries = self._note_region_change(premise, writer)
        self._deliver(deliveries)
        self._check_completion()
        return result

    def read_audit(self) -> list[AuditEvent]:
        """Returns every audit event in the order each occurred."""
        with self._lock:
            return list(self._audit)

    def abort(self, reason: str) -> None:
        """Closes the run as aborted. A run already closed keeps its outcome."""
        with self._lock:
            self._close_locked(Aborted(reason=reason))
        self._tell_closed()

    def outcome(self) -> RunOutcome | None:
        """Returns the closed run's outcome, or nothing while the run is open."""
        with self._lock:
            return self._outcome

    def wait_closed(self, timeout: timedelta | None = None) -> RunOutcome | None:
        """Blocks until the run closes and returns its outcome.

        With a timeout, returns nothing when the run is still open after it.
        """
        with self._condition:
            seconds = timeout.total_seconds() if timeout is not None else None
            self._condition.wait_for(lambda: self._outcome is not None, timeout=seconds)
            return self._outcome

    def ack(self, notification_id: NotificationId | int, *, agent: str) -> None:
        """Records that the agent finished responding to a notification.

        The cursor advances to the end of the range the notification
        covered, and every notification to this agent whose range ends at or
        before that one is acknowledged with it, because the cursor is
        cumulative and answering the wider range answered the narrower ones.

        An acknowledgment of a notification no longer outstanding changes
        nothing; one naming a notification never issued to that agent raises.
        """
        acknowledged = NotificationId(notification_id)
        with self._lock:
            key = (agent, acknowledged)
            outstanding = self._outstanding.pop(key, None)
            if outstanding is None:
                if key in self._issued:
                    return
                raise UnknownNotificationError(
                    f"no notification {notification_id} was issued to {agent!r}"
                )
            state = self._agents[agent]
            state.cursor = max(state.cursor, outstanding.to_sequence)
            # The cursor is cumulative, so acknowledging this range
            # acknowledges every range it already covers. Otherwise an agent
            # that answered the newest of several overlapping notifications
            # is named unfinished for work it did, and a notification whose
            # delivery raised holds the run open for ever.
            covered = [
                held
                for held, still in self._outstanding.items()
                if held[0] == agent and still.to_sequence <= outstanding.to_sequence
            ]
            for held in covered:
                del self._outstanding[held]
            self._audit.append(
                NotificationAcknowledged(
                    at=self._clock.now(), agent=agent, notification_id=acknowledged
                )
            )
        self._check_completion()

    def _note_region_change(self, region: str, writer: str) -> list[_Delivery]:
        # Callers hold self._lock. Returns the deliveries the caller makes
        # after releasing it. Both region kinds carry a window; the default
        # is zero, which dispatches inline.
        now = self._clock.now()
        window = self._batch_windows[region]
        deliveries: list[_Delivery] = []
        for state in self._agents.values():
            if self._outcome is not None:
                break
            if state.declaration.name == writer:
                continue
            if not _subscribed(state, region, self._kinds[region]):
                continue
            due = now + window
            existing = state.pending.get(region)
            state.pending[region] = due if existing is None else min(existing, due)
            delivery = self._evaluate_dispatch_locked(state, now)
            if delivery is not None:
                deliveries.append(delivery)
        return deliveries

    def _evaluate_dispatch_locked(
        self, state: _AgentState, now: datetime
    ) -> _Delivery | None:
        # Callers hold self._lock. Dispatches the agent's pending set when
        # a change is due; arms or re-arms the batch window when the
        # earliest due instant is ahead.
        if not state.pending:
            return None
        earliest = min(state.pending.values())
        if earliest <= now:
            return self._dispatch(state, now)
        if state.window_due is None or earliest < state.window_due:
            if state.window_call is not None:
                state.window_call.cancel()
            name = state.declaration.name
            state.window_due = earliest
            state.window_generation += 1
            state.window_call = self._clock.call_at(
                earliest, partial(self._close_window, name, state.window_generation)
            )
        return None

    def _open_premises(self, premises: dict[str, object]) -> None:
        # Called by create_model while the run opens; not a proposed
        # write, so admission does not apply.
        deliveries: list[_Delivery] = []
        with self._lock:
            if self._outcome is not None:
                return
            declared = {
                name
                for name, kind in self._kinds.items()
                if kind is _RegionKind.PREMISE
            }
            missing = declared - set(premises)
            unknown = set(premises) - declared
            if missing or unknown:
                parts = []
                if missing:
                    parts.append(
                        "the opening premises miss "
                        + ", ".join(sorted(repr(n) for n in missing))
                    )
                if unknown:
                    parts.append(
                        "the opening premises name undeclared "
                        + ", ".join(sorted(repr(n) for n in unknown))
                    )
                raise PremiseError("; ".join(parts))
            now = self._clock.now()
            for premise, value in premises.items():
                result = self._store.set(
                    self._board_id, premise, value, expected_version=0
                )
                assert isinstance(result, Written)  # a fresh premise cannot conflict
                assert result.version is not None  # a premise write carries one
                self._last_sequence = result.sequence
                self._audit.append(
                    PremiseOpened(
                        at=now,
                        premise=premise,
                        sequence=result.sequence,
                        version=result.version,
                    )
                )
                window = self._batch_windows[premise]
                due = now + window
                for state in self._agents.values():
                    existing = state.pending.get(premise)
                    state.pending[premise] = (
                        due if existing is None else min(existing, due)
                    )
            for state in self._agents.values():
                if self._outcome is not None:
                    break
                delivery = self._evaluate_dispatch_locked(state, now)
                if delivery is not None:
                    deliveries.append(delivery)
        self._deliver(deliveries)
        self._check_completion()

    def _dispatch(self, state: _AgentState, now: datetime) -> _Delivery:
        # Callers hold self._lock. Every call issues a notification: nothing
        # counts them, so nothing can withhold one.
        if state.window_call is not None:
            state.window_call.cancel()
            state.window_call = None
            state.window_due = None
            state.window_generation += 1
        regions = frozenset(state.pending)
        state.pending.clear()
        notification_id = NotificationId(self._next_notification_id)
        self._next_notification_id += 1
        notification = Notification(
            notification_id=notification_id,
            board_id=self._board_id,
            agent=state.declaration.name,
            from_sequence=state.cursor + 1,
            to_sequence=self._last_sequence,
            regions=regions,
        )
        key = (state.declaration.name, notification_id)
        self._issued.add(key)
        self._outstanding[key] = _Outstanding(to_sequence=self._last_sequence)
        self._audit.append(NotificationDispatched(at=now, notification=notification))
        return (state.declaration.notify, notification)

    def _close_window(self, agent_name: str, generation: int) -> None:
        # A cancelled timer whose call already started still runs; the
        # generation identifies the armed window, so a stale call changes
        # nothing.
        deliveries: list[_Delivery] = []
        with self._lock:
            state = self._agents.get(agent_name)
            if (
                state is None
                or self._outcome is not None
                or state.window_generation != generation
            ):
                return
            state.window_call = None
            state.window_due = None
            state.window_generation += 1
            if state.pending:
                deliveries.append(self._dispatch(state, self._clock.now()))
        self._deliver(deliveries)
        self._check_completion()

    def _deliver(self, deliveries: list[_Delivery]) -> None:
        # One flat drain loop per thread: a callback that writes a premise
        # enqueues the resulting deliveries and returns, so chained notifications
        # cost queue entries, not stack frames.
        self._delivery_queue.extend(deliveries)
        if getattr(self._delivering, "active", False):
            return
        self._delivering.active = True
        try:
            while True:
                try:
                    notify, notification = self._delivery_queue.popleft()
                except IndexError:
                    return
                # The callback is application code at the library's
                # boundary. An agent whose delivery raised never
                # acknowledges, so it is named unfinished when the run
                # closes; raising here would abort the rest of the batch and
                # reach an unrelated writer.
                with suppress(Exception):
                    notify(notification)
        finally:
            self._delivering.active = False

    def _touch_idle_locked(self) -> None:
        # Callers hold self._lock. Silence is measured from the last event,
        # so every event pushes the deadline out.
        if self._outcome is not None:
            return
        if self._idle_call is not None:
            self._idle_call.cancel()
        self._idle_generation += 1
        self._idle_call = self._clock.call_at(
            self._clock.now() + self._limits.idle,
            partial(self._idle_passed, self._idle_generation),
        )

    def _idle_passed(self, generation: int) -> None:
        # A cancelled timer whose call already started still runs, so the
        # generation identifies the armed deadline.
        with self._lock:
            if self._outcome is not None or generation != self._idle_generation:
                return
            predicate = self._termination_predicate
            if predicate is None:
                self._close_locked(Settled(unfinished=self._unfinished_locked()))
                closed = True
            else:
                closed = False
        if closed:
            self._tell_closed()
            return
        assert predicate is not None
        decision = predicate(self._reader)
        with self._lock:
            if self._outcome is not None or generation != self._idle_generation:
                return
            if decision is TerminationDecision.COMPLETE:
                self._close_locked(Settled(unfinished=self._unfinished_locked()))
            else:
                self._touch_idle_locked()
        self._tell_closed()

    def _check_completion(self) -> None:
        """Records that something happened, which pushes the idle deadline out.

        A run does not close because nothing is outstanding at some instant.
        Agents are idle between notifications and they finish at different
        times, so an instant of quiet is the gap before the work rather than
        the end of it. A run closes when the quiet lasts long
        enough, and the idle timer measures it.
        """
        with self._lock:
            self._touch_idle_locked()

    def _sequencing_gate_locked(self, writer: str, region: str) -> Rejected | None:
        # The pre-admission checks ran without holding the lock across the
        # admission rule, so a close or a competing writer can land between
        # them and sequencing. This re-check under the lock is what makes
        # the refusals race-free.
        if self._outcome is not None:
            return self._reject_locked(
                writer, region, RejectionCause.RUN_CLOSED, "the run has closed"
            )
        return None

    def _forget_outstanding_locked(self, agent_name: str) -> None:
        """Drops what an agent was holding, because it is no longer there.

        Callers hold self._lock. A returning agent is told again, in one
        notification covering everything since its cursor, so nothing it
        was owed is lost by forgetting what the old process held.
        """
        for key in [k for k in self._outstanding if k[0] == agent_name]:
            del self._outstanding[key]

    def _unfinished_locked(self) -> frozenset[str]:
        # An agent has not finished when it still holds an unacknowledged
        # notification.
        holding = {agent for agent, _ in self._outstanding}
        return frozenset(holding)

    def _close_locked(self, outcome: RunOutcome) -> None:
        if self._outcome is not None:
            return
        self._outcome = outcome
        if self._wall_call is not None:
            self._wall_call.cancel()
        if self._idle_call is not None:
            self._idle_call.cancel()
            self._idle_call = None
        self._idle_generation += 1
        for state in self._agents.values():
            if state.window_call is not None:
                state.window_call.cancel()
                state.window_call = None
                state.window_due = None
                state.window_generation += 1
            state.pending.clear()
        self._outstanding.clear()
        self._audit.append(RunClosed(at=self._clock.now(), outcome=outcome))
        self._condition.notify_all()
        self._closing.append(outcome)

    def _tell_closed(self) -> None:
        # Called with the lock released, once, on whichever thread closed the
        # run. Application code at the boundary, so a raise here must not
        # reach the writer that happened to be the one to close it.
        while self._closing:
            outcome = self._closing.pop()
            if self._on_closed is None:
                continue
            with suppress(Exception):
                self._on_closed(outcome)

    def _wall_clock_expired(self) -> None:
        with self._lock:
            if self._outcome is not None:
                return
            self._close_locked(WallClockExpired(unfinished=self._unfinished_locked()))
        self._tell_closed()

    def _refuse_region(
        self, writer: str, region: str, expected: _RegionKind
    ) -> Rejected | None:
        with self._lock:
            if self._outcome is not None:
                return self._reject_locked(
                    writer, region, RejectionCause.RUN_CLOSED, "the run has closed"
                )
            if not isinstance(region, str):
                raise TypeError(
                    f"a region is named by a string, not {type(region).__name__}"
                )
            kind = self._kinds.get(region)
            if kind is None:
                # The application declared the regions, so a name that is not
                # among them is a defect in the application rather than a
                # decision the run made about this write.
                raise UndeclaredRegionError(
                    f"no region is declared with the name {region!r}"
                )
            if kind is not expected:
                if expected is _RegionKind.LEVEL:
                    raise RegionKindError(
                        f"{region!r} names a premise, and this operation takes a level"
                    )
                raise RegionKindError(
                    f"{region!r} names a level, and this operation takes a premise"
                )
            return None

    def _reject(
        self, writer: str, region: str, cause: RejectionCause, reason: str
    ) -> Rejected:
        with self._lock:
            return self._reject_locked(writer, region, cause, reason)

    def _reject_locked(
        self, writer: str, region: str, cause: RejectionCause, reason: str
    ) -> Rejected:
        self._audit.append(
            WriteRejected(
                at=self._clock.now(),
                writer=writer,
                region=region,
                cause=cause,
                reason=reason,
            )
        )
        return Rejected(cause=cause, reason=reason)

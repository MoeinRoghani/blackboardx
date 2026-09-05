"""The control component.

A write made through the control component passes the application's
admission rule before the board sequences it. The rule sees the proposed write with a
read handle on the board and returns an acceptance or a reasoned rejection. An admitted
level write is sequenced and audited. An admitted
premise write may still fail with a conflict, which returns to the writer
unaudited. A rejected write returns its reason to the writer, never reaches
the board, and is audited without a sequence number.

An admitted premise write also notifies the registered agents, each
through its batch window, except the agent that wrote the change. The control component
tracks which agents hold an unacknowledged notification.

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
at the same moment are both judged against the board as it was before the first of them
landed. A premise write closes that window with its expected
version; a level write does not, so a rule refusing duplicates bounds
concurrent duplicates rather than preventing them.
"""

from __future__ import annotations

import logging
import random
import threading
import warnings
from collections import deque
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from functools import partial
from typing import NewType, Protocol, TypeAlias, runtime_checkable

from blackboard._board import (
    AgentProgress,
    BlackboardError,
    BoardChange,
    Conflict,
    Contribution,
    Deleted,
    Level,
    Premise,
    PremiseState,
    RegionKindError,
    RunRecord,
    UndeclaredRegionError,
    Unsent,
    UnsetPremiseError,
    Written,
)
from blackboard._clock import Clock, ScheduledCall

logger = logging.getLogger(__name__)

#: How much of the board one poll reads. A page short of the board leaves
#: the rest for the next poll, because the cursor it advances is the agent's
#: and not the reader's.
_TAIL = 1000


class BoardStore(Protocol):
    """The operations the control component performs on a store.

    A store holds many boards. Every call names the board it acts on, so one
    connection to a database serves every board an application runs.

    ``InMemoryStore`` and ``SqliteStore`` satisfy this protocol, as does any adapter an
    application writes against its own database. The control component names
    no concrete type, and holds every implementation to one conformance suite.

    Content crosses this protocol as JSON. An implementation returns what JSON carries,
    so a tuple that is written comes back as a list, and content that JSON cannot carry
    raises ``TypeError`` before anything is stored.
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
        writer: str | None = None,
        notify: frozenset[str] = frozenset(),
    ) -> Written:
        """Adds one contribution to a level and returns where it landed.

        ``notify`` names the agents that should hear of this write. One row
        per name is recorded in the same transaction as the contribution, so
        a process that commits and stops before delivering has not lost the
        intent. A write that is a repeat records none, because nothing
        happened.

        ``writer`` is the name the write carried, recorded on the row so the
        record answers who wrote it. The store stamps the instant itself.

        ``idempotency_key`` names one write on one board. A key that the store has
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
        writer: str | None = None,
        notify: frozenset[str] = frozenset(),
    ) -> Written | Conflict:
        """Replaces a premise's value under the version the caller expects.

        ``writer`` is recorded as it is on ``append``.

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
        the last sequence it received, which an offset could not do, because an offset
        shifts when a concurrent write lands, and a sequence number does not.
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

        Delete removes everything or nothing: a caller that gets an answer got a board
        that is gone. A board that the store never held names nothing rather than
        failing, so a delete that runs twice is safe.

        Nothing in the library calls this. Deleting is a retention decision
        and the control component makes none.
        """
        ...

    def open_run(self, board_id: str, *, wall_clock: float, idle: float) -> None:
        """Records that a run is open over this board, with its two limits.

        Both limits are seconds. The store computes the two deadlines from
        its own clock, so no instant crosses the call and no caller's clock
        decides when a run ends.
        """
        ...

    def read_run(self, board_id: str) -> RunRecord | None:
        """Returns the run over this board, or nothing where none was opened.

        The record carries the store's clock alongside the deadlines, so a
        caller compares two instants that came from one clock.
        """
        ...

    def touch_run(self, board_id: str, *, idle: float) -> None:
        """Pushes the idle deadline out by ``idle`` seconds from now.

        The wall clock is left where it was, because it bounds the whole run
        rather than the quiet in it. A run already closed is unchanged.
        """
        ...

    def close_run(
        self,
        board_id: str,
        *,
        closed_as: str,
        reason: str | None = None,
        unfinished: frozenset[str] = frozenset(),
    ) -> bool:
        """Records how the run ended, and says which caller recorded it.

        Answers ``True`` to the one caller that closed the run and ``False``
        to every other, so a run closes once however many callers reach the
        deadline together. That is what makes closing safe without a lock.
        """
        ...

    def runs_past_deadline(self, limit: int = 100) -> list[str]:
        """Returns boards whose run is open and past one of its deadlines.

        A run closes because nothing happened, so no request is in flight to
        notice. This is what a caller polls to close those runs.
        """
        ...

    def unsent(self, limit: int = 100) -> list[Unsent]:
        """Returns notifications recorded by a write and not yet sent.

        Across every board, oldest first by the sequence the notification
        ends at, so the process that reads them sends the earliest work
        first. A caller filters by the agents it holds, because only the
        process holding an agent can reach it.
        """
        ...

    def mark_sent(self, board_id: str, agent: str, *, through: int) -> None:
        """Records that this notification was sent, so nothing sends it again.

        A row already marked, or one that never existed, changes nothing.
        Marking after sending rather than before is what makes delivery at
        least once: a process that sends and stops before marking sends
        again, and a repeat costs nothing.
        """
        ...

    def read_agents(self, board_id: str) -> list[AgentProgress]:
        """Returns how far each agent on this board has been told and answered.

        One entry per agent the board has notified, in any order. A board
        that has notified nobody returns nothing rather than refusing.

        Each entry is internally consistent, so ``acknowledged_through`` never
        exceeds ``notified_through`` on a row that comes back. The entries are
        not read as one snapshot across agents, and nothing needs them to be.
        """
        ...

    def mark_notified(self, board_id: str, agent: str, *, through: int) -> None:
        """Records that the agent has been told everything through ``through``.

        Creates the entry where none exists. A ``through`` below what is
        already recorded changes nothing, so two processes notifying one
        agent leave the higher of the two whichever order they arrive in.
        That is what makes notifying from more than one process safe without
        a lock.
        """
        ...

    def acknowledge(
        self, board_id: str, agent: str, *, through: int
    ) -> AgentProgress | None:
        """Records the agent's answer, and returns the entry as it stood before.

        Answers ``None`` where the agent has no entry, and where ``through``
        is beyond what the agent was told. Both mean the acknowledgment names
        a range the store never handed out.

        ``acknowledged_through`` only rises, so of several callers naming one
        ``through`` exactly one is answered with an entry below it. That is
        how a first acknowledgment is told from a repeat without a second
        read.
        """
        ...

    def read_regions(self, board_id: str) -> list[Level | Premise]:
        """Returns the regions declared on one board, with their kinds.

        A store records a region's name and its kind, and nothing else. A premise comes
        back with the default batch window, whatever window it was declared with,
        because the window tells the control component
        when to notify and is no part of the record.

        A board that nobody declared a region on returns nothing rather than refusing,
        because a board comes into being by being written to.
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
"""A proposed write, level or premise, as the admission rule receives it."""


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
    the application's own configuration settles, such as a region nobody declared,
    raises an error instead.

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
    and any region, not only the regions this notification names.
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

    ``subscribes_to`` names the regions, level or premise, whose changes wake
    this agent, and naming any region excludes every region not named. Omitting it
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
    mistake. Registering a name again later names a returning agent rather than a
    duplicate, and replaces that agent's declaration.
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

    Time is the only bound. A count of writes would limit the cause of a notification
    and a count of notifications would limit the effect, and
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


#: What each outcome is called in the store.
_CLOSED_AS: dict[type, str] = {
    Settled: "settled",
    WallClockExpired: "wall_clock_expired",
    Aborted: "aborted",
}


def _outcome_of(run: RunRecord) -> RunOutcome:
    """Builds the outcome a closed run recorded."""
    if run.closed_as == "aborted":
        return Aborted(reason=run.reason or "", unfinished=run.unfinished)
    if run.closed_as == "wall_clock_expired":
        return WallClockExpired(unfinished=run.unfinished)
    return Settled(unfinished=run.unfinished)


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
    #: How far this agent has been told, and how far it has answered. Both
    #: are the store's numbers, cached here so the write path does not read
    #: them back. Both only rise, so a stale one is behind and never ahead:
    #: it costs a repeated notification, which the wire contract makes free.
    notified_through: int = 0
    acknowledged_through: int = 0
    pending: dict[str, datetime] = field(default_factory=dict)
    window_call: ScheduledCall | None = None
    window_due: datetime | None = None
    window_generation: int = 0


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

    A read needs the record and not the run, so a process holding the store can serve a
    read for any board in it. Writing needs a ``Control``.
    """
    return _BoundReader(store, board_id)


@runtime_checkable
class AgentBoard(Protocol):
    """One board, as one agent sees it: what an agent body is written against.

    ``BoardClient`` satisfies this over HTTP and ``Control.as_agent`` returns one in
    process, so a body written once serves both deployments. Every
    method omits the agent's own name, because the object already carries it.

    Reads are the four operations that ``BoardReader`` has. Writes are the three that
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
        # A declaration converges rather than being made once. A region the
        # record already holds with the same kind is recorded and not
        # written again, so any process builds this over a board another
        # already opened. A name held as the other kind is still refused:
        # that is a disagreement about the board and not a repeat.
        held = {region.name: region for region in store.read_regions(board_id)}
        for region in regions:
            standing = held.get(region.name)
            if standing is None:
                if not adopt:
                    store.declare(board_id, region)
                    self._record_kind(region)
                    continue
                raise UndeclaredRegionError(
                    f"{region.name!r} is not declared on {board_id!r}"
                )
            if type(standing) is not type(region):
                raise RegionKindError(
                    f"{region.name!r} is a "
                    f"{'level' if isinstance(standing, Level) else 'premise'}"
                    f" on {board_id!r}, and was declared here as a "
                    f"{'level' if isinstance(region, Level) else 'premise'}"
                )
            self._record_kind(region)
        # The sequence continues from what the record ends at, or a
        # notification would cover a range that has already been read.
        written = store.read_board(board_id)
        self._last_sequence = written[-1].sequence if written else 0
        # The store holds the deadlines, so a process that did not open this
        # run still knows when it ends. The timer below stays as the local
        # path: it closes the run promptly here, and `close_expired` closes
        # the runs no process is watching.
        store.open_run(
            board_id,
            wall_clock=resolved.wall_clock.total_seconds(),
            idle=resolved.idle.total_seconds(),
        )
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
        ``BoardClient`` has and an agent body written against ``AgentBoard`` runs in
        this process or against a blackboard over HTTP.

        The agent need not be registered. Registering decides what wakes it; this call
        decides what it writes as.
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
        here covers everything since that cursor, because a notification carries no
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
            if returning is not None and returning.window_call is not None:
                returning.window_call.cancel()
            # How far this agent answered is on the record, so an agent
            # returning to a process that never saw it resumes where it left
            # off rather than being told the board again. What it was still
            # holding is handed to it below, because whatever the previous
            # process dispatched did not reach it.
            answered = next(
                (
                    progress.acknowledged_through
                    for progress in self._store.read_agents(self._board_id)
                    if progress.agent == agent.name
                ),
                0,
            )
            state = _AgentState(
                declaration=agent,
                notified_through=answered,
                acknowledged_through=answered,
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
                        self._board_id, level, content, idempotency_key, writer=writer
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
                        writer=writer,
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
        """Returns every audit event in the order each occurred.

        Deprecated. The audit answered two questions, and the record now
        answers one of them: a contribution carries its writer and the instant
        it was written. What is left is what a log line says, and a log line
        survives the process where this list does not.
        """
        warnings.warn(
            "Control.read_audit is deprecated and may be removed on or after "
            "2026-12-05. A contribution carries its writer and the instant it "
            "was written, and what the audit said besides is written to the "
            "log under the 'blackboard' logger.",
            DeprecationWarning,
            stacklevel=2,
        )
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

        An acknowledgment of a notification no longer outstanding changes nothing; an
        acknowledgment that names a notification never issued to that agent raises an
        error.
        """
        acknowledged = NotificationId(notification_id)
        # The store holds how far this agent has answered, so an
        # acknowledgment is served by any process and not only the one that
        # issued it. The call is outside the lock: it is a round trip, and
        # nothing local is consulted to decide it.
        prior = self._store.acknowledge(
            self._board_id, agent, through=int(acknowledged)
        )
        if prior is None:
            raise UnknownNotificationError(
                f"no notification {notification_id} was issued to {agent!r}"
            )
        # A repeat changes nothing, and is told from a first answer by what
        # the store held before this call. The identifier is the end of the
        # range it covers, so answering a wider range answers every narrower
        # one with it.
        if prior.acknowledged_through >= int(acknowledged):
            return
        with self._lock:
            state = self._agents.get(agent)
            if state is not None:
                state.acknowledged_through = max(
                    state.acknowledged_through, int(acknowledged)
                )
            self._audit.append(
                NotificationAcknowledged(
                    at=self._clock.now(), agent=agent, notification_id=acknowledged
                )
            )
        self._check_completion()

    def notify_due(self) -> list[str]:
        """Delivers what this process's agents are owed, and names them.

        A write taken by one process reaches the agents registered with
        another here. The process that took the write records that it
        landed and nothing about who should hear of it; the process holding
        an agent is the only one that can reach it, so it is the one that
        reads the record and decides.

        A run inside one process never needs this. That process notifies on
        the write path and closes its own windows on a timer, so this
        finds nothing to do. Call it on whatever schedule suits the
        deployment, beside ``close_expired``.
        """
        deliveries: list[_Delivery] = []
        with self._lock:
            if self._outcome is not None:
                return []
            run = self._store.read_run(self._board_id)
            if run is None:
                return []
            # The run is read before the agents are, so a process holding
            # none still learns that the run it was serving has closed.
            if run.closed_as is not None:
                self._close_locked(_outcome_of(run))
                closed = True
            elif not self._agents:
                return []
            else:
                closed = False
                self._take_tail_locked(run)
                now = self._clock.now()
                for state in self._agents.values():
                    delivery = self._evaluate_dispatch_locked(state, now)
                    if delivery is not None:
                        deliveries.append(delivery)
        if closed:
            self._tell_closed()
            return []
        self._deliver(deliveries)
        return [notification.agent for _, notification in deliveries]

    def _take_tail_locked(self, run: RunRecord) -> None:
        # Callers hold self._lock. Reads the board since the least advanced
        # agent and folds what is new into each agent's pending set, which is
        # the same set the write path fills. One read serves every agent, and
        # an agent already told of a change skips it.
        floor = min(state.acknowledged_through for state in self._agents.values())
        tail = self._store.read_board(
            self._board_id, from_sequence=floor + 1, limit=_TAIL
        )
        if not tail:
            return
        self._last_sequence = max(self._last_sequence, tail[-1].sequence)
        now = self._clock.now()
        for state in self._agents.values():
            for change in tail:
                if change.sequence <= state.notified_through:
                    continue
                if change.writer == state.declaration.name:
                    continue
                kind = self._kinds.get(change.region)
                if kind is None or not _subscribed(state, change.region, kind):
                    continue
                # The window is measured from when the write landed, which
                # the store stamped, and the store answers with its own clock
                # beside it. Subtracting one from the other gives what is
                # left of the window as a duration, which is what this
                # process can hold against its own clock.
                stamped = change.written_at
                left = (
                    self._batch_windows[change.region]
                    if stamped is None
                    else max(
                        stamped + self._batch_windows[change.region] - run.now,
                        timedelta(0),
                    )
                )
                due = now + left
                existing = state.pending.get(change.region)
                state.pending[change.region] = (
                    due if existing is None else min(existing, due)
                )

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
        if self._last_sequence <= state.acknowledged_through:
            # Everything on the board is behind this agent's answer, so the
            # range would start after it ended. A returning agent that had
            # answered everything is registered without being woken.
            state.pending.clear()
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
            standing = {
                name
                for name, kind in self._kinds.items()
                if kind is _RegionKind.PREMISE and self._has_value(name)
            }
            for premise, value in premises.items():
                if premise in standing:
                    # The record already holds a value and the version it is
                    # at. An opening value is what a board starts from, not
                    # what every process asserts on arrival.
                    continue
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
        # The identifier is the end of the range the notification covers.
        # A board's sequence is the one number every process already agrees
        # on, so two processes never have to agree on a counter: two
        # notifications carrying one identifier carry one instruction, and
        # one acknowledgment answers both.
        notification_id = NotificationId(self._last_sequence)
        notification = Notification(
            notification_id=notification_id,
            board_id=self._board_id,
            agent=state.declaration.name,
            from_sequence=state.acknowledged_through + 1,
            to_sequence=self._last_sequence,
            regions=regions,
        )
        # Handing an agent work is an event, so the run does not then close on
        # the agent it just woke. A write already pushed the deadline out
        # before reaching here; a registration pushed nothing, and a
        # registration that hands out no notification never reaches here.
        self._touch_idle_locked()
        self._store.mark_notified(
            self._board_id, state.declaration.name, through=self._last_sequence
        )
        state.notified_through = max(state.notified_through, self._last_sequence)
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
        # so every event pushes the deadline out, in the store where any
        # process reads it and on the timer that closes it here.
        if self._outcome is not None:
            return
        self._store.touch_run(self._board_id, idle=self._limits.idle.total_seconds())
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

    def _unfinished_locked(self) -> frozenset[str]:
        # An agent has not finished when it has been told further than it has
        # answered. The store holds both numbers, so this names the agents no
        # process in the deployment heard back from rather than the ones this
        # one is waiting on.
        return _unfinished(self._store, self._board_id)

    def _close_locked(self, outcome: RunOutcome) -> None:
        if self._outcome is not None:
            return
        # The store decides who closes a run: it answers True to one caller
        # and False to every other, so a run closes once however many
        # callers reach the deadline together. A caller that loses adopts
        # the outcome the winner wrote rather than its own.
        if not self._store.close_run(
            self._board_id,
            closed_as=_CLOSED_AS[type(outcome)],
            reason=getattr(outcome, "reason", None),
            unfinished=getattr(outcome, "unfinished", frozenset()),
        ):
            written = self._store.read_run(self._board_id)
            if written is not None and written.closed_as is not None:
                outcome = _outcome_of(written)
        self._outcome = outcome
        # No agent learns that the run ended, or that it was named unfinished,
        # so this is the blackboard's to say. What an agent already received,
        # a write accepted or refused, a notification, an acknowledgment, is
        # the agent's to log and is not repeated here.
        unfinished: frozenset[str] = getattr(outcome, "unfinished", frozenset())
        logger.info(
            "run on %s closed as %s%s%s",
            self._board_id,
            type(outcome).__name__.lower(),
            f", reason {outcome.reason!r}" if isinstance(outcome, Aborted) else "",
            (", unfinished " + ", ".join(sorted(unfinished)) if unfinished else ""),
        )
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
        # What each agent was owed stays on the record. The outcome named
        # the unfinished ones as it was written, and an acknowledgment
        # arriving after that still advances a cursor without changing an
        # outcome already stamped.
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


def _unfinished(store: BoardStore, board_id: str) -> frozenset[str]:
    """Names the agents told further than they have answered.

    Any process may ask, because both numbers are on the record rather than
    in whichever process did the telling.
    """
    return frozenset(
        progress.agent
        for progress in store.read_agents(board_id)
        if progress.outstanding
    )


def close_expired(store: BoardStore, limit: int = 100) -> list[str]:
    """Closes the runs past a deadline that no process is watching, and names them.

    A run closes because nothing happened, so no request is in flight to
    notice. Any process holding the store may call this: closing is a write
    only one caller wins, so several callers running it together still close
    each run once.

    Call it on whatever schedule suits the deployment, being a thread beside
    the service, a scheduled job, or a test advancing time by hand. The
    library takes no view. ``limit`` bounds one pass.
    """
    closed: list[str] = []
    for board_id in store.runs_past_deadline(limit):
        # A sweep has no caller waiting on it, so a store that fails here
        # reaches nobody unless this says so. One board failing is also no
        # reason to leave the rest of the pass undone.
        try:
            run = store.read_run(board_id)
            if run is None:
                continue
            expired = run.expired
            if expired is None:
                continue
            unfinished = _unfinished(store, board_id)
            won = store.close_run(board_id, closed_as=expired, unfinished=unfinished)
        except Exception:
            logger.exception("the sweep could not close the run on %s", board_id)
            continue
        if won:
            closed.append(board_id)
            logger.info(
                "run on %s closed as %s by the sweep%s",
                board_id,
                expired,
                (", unfinished " + ", ".join(sorted(unfinished)) if unfinished else ""),
            )
    return closed


class Sweep:
    """Calls :func:`close_expired` on an interval, on a thread of its own.

    A convenience, the way ``HttpNotifier`` is a convenience over the wire
    protocol. The library ships the mechanism and the application owns the
    loop: a scheduled job, a serverless invocation, or a thread beside the
    service are all equally supported, and this is only the last of those
    written out so an application does not have to.

    ``interval`` is the seconds between passes. ``jitter`` spreads the first
    pass over a fraction of that interval, chosen once when the sweep starts,
    so replicas that started together do not query together for ever after.
    Pass ``jitter=0.0`` for a test that wants the first pass promptly.

    A pass that raises is logged and the loop continues. A loop that died on
    one failure would leave every later run open, which is the failure the
    sweep exists to prevent.
    """

    def __init__(
        self,
        store: BoardStore,
        *,
        interval: float = 30.0,
        limit: int = 100,
        jitter: float = 1.0,
    ) -> None:
        if interval <= 0:
            raise ValueError(
                f"interval is a positive number of seconds, not {interval}"
            )
        if limit < 1:
            raise ValueError(f"limit is at least 1, not {limit}")
        if not 0.0 <= jitter <= 1.0:
            raise ValueError(f"jitter is a fraction between 0 and 1, not {jitter}")
        self._store = store
        self._interval = interval
        self._limit = limit
        self._jitter = jitter
        self._stopping = threading.Event()
        self._passed = threading.Condition()
        self._passes = 0
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        """True between ``start`` and ``close``."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Starts the thread. Starting a running sweep does nothing."""
        if self.running:
            return
        self._stopping.clear()
        self._thread = threading.Thread(
            target=self._run, name="blackboard-sweep", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        """Stops the thread and waits for the pass in flight to finish."""
        self._stopping.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join()

    def wait_for_pass(self, timeout: float, passes: int = 1) -> bool:
        """Blocks until this many passes have run. True if they did.

        For a test that needs a pass to have happened before it asserts.
        """
        target = passes
        with self._passed:
            return self._passed.wait_for(lambda: self._passes >= target, timeout)

    def __enter__(self) -> Sweep:
        self.start()
        return self

    def __exit__(self, *exception: object) -> None:
        self.close()

    def _run(self) -> None:
        first = self._interval * self._jitter * random.random()
        if self._stopping.wait(first):
            return
        while not self._stopping.is_set():
            try:
                close_expired(self._store, self._limit)
            except Exception:
                # The store failed the query itself, not one board. Say so and
                # keep the loop, because nothing else will sweep.
                logger.exception("a sweep pass failed")
            with self._passed:
                self._passes += 1
                self._passed.notify_all()
            if self._stopping.wait(self._interval):
                return

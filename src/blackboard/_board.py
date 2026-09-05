"""A board stores contributions and decides nothing about them.

Writes go to declared regions of two kinds. A level accumulates contributions
in arrival order. A premise holds one current value under a version number,
and a write naming a version other than the current one fails. One counter
orders every write across all regions of one board, and reads are open to any
caller.

A store holds many boards. Every call names the board it acts on, so one connection to a
database serves every board an application runs rather than one board. The board
identifier is opaque to the library.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

_ZERO_WINDOW = timedelta(0)


def _as_json(content: object) -> Any:
    """Returns the content as JSON carries it, raising when it cannot.

    Every board in the package carries content this way, because a deployed
    board crosses a process boundary. The boards that encode on the way to
    a database get the same result from their own ``json.dumps``.
    """
    return json.loads(json.dumps(content))


class BlackboardError(Exception):
    """The base of every error this library raises."""


class UndeclaredRegionError(BlackboardError):
    """An operation named a region that no declaration created."""


class DuplicateRegionError(BlackboardError):
    """A declaration named a region that already exists."""


class RegionKindError(BlackboardError):
    """An operation that takes a level named a premise, or the reverse."""


class IdempotencyKeyError(BlackboardError):
    """An idempotency key was used for a region it did not name before.

    A key names one write. Sending it again for a different region is a
    caller's mistake rather than a retry, and the store refuses it instead of
    silently answering with the write it does name.
    """


class UnsetPremiseError(BlackboardError):
    """A premise was read before any write gave it a value."""


@dataclass(frozen=True)
class Level:
    """A declaration of a region that accumulates contributions in arrival order.

    The control component reads the batch window when a change to this level
    lands, to schedule its notification. The board ignores it. A window
    collapses a burst of writes into one notification, which costs a
    subscriber one wake instead of one per write.
    """

    name: str
    batch_window: timedelta = _ZERO_WINDOW

    def __post_init__(self) -> None:
        if self.batch_window < _ZERO_WINDOW:
            raise ValueError("a batch window is a non-negative duration")


@dataclass(frozen=True)
class Premise:
    """A declaration of a region that holds one current value under a version.

    The control component reads the batch window when a change to this
    premise lands, to schedule its notification. The board ignores it.
    """

    name: str
    batch_window: timedelta = _ZERO_WINDOW

    def __post_init__(self) -> None:
        if self.batch_window < _ZERO_WINDOW:
            raise ValueError("a batch window is a non-negative duration")


@dataclass(frozen=True)
class Written:
    """A write the board sequenced, at the sequence number it received.

    ``version`` is the premise's new revision count. A level write leaves
    it ``None``, because a level holds no version to replace.

    ``repeated`` says the store had already written this idempotency key and
    answered with what that write produced. Nothing was added.
    """

    sequence: int
    version: int | None = None
    repeated: bool = False


@dataclass(frozen=True)
class Deleted:
    """What removing one board took with it.

    A board the store never held names nothing rather than failing, so
    deleting one twice is safe. Both fields are counts, and are named as
    counts because ``regions`` elsewhere on this surface holds names.
    """

    board_id: str
    regions_removed: int
    writes_removed: int


@dataclass(frozen=True)
class Conflict:
    """A premise write that failed because the version it named is not current."""

    current_version: int


@dataclass(frozen=True)
class RunRecord:
    """What the store holds about one board's run.

    ``now`` is the store's clock at the moment of the read, so a caller
    decides that a deadline has passed by comparing two instants that came
    from the same clock. Pods disagree about the time and the store does not.

    ``closed_as`` is ``None`` while the run is open, and otherwise names the
    outcome: ``settled``, ``wall_clock_expired`` or ``aborted``.
    """

    now: datetime
    idle_deadline: datetime
    wall_deadline: datetime
    closed_as: str | None = None
    reason: str | None = None
    unfinished: frozenset[str] = frozenset()

    @property
    def expired(self) -> str | None:
        """Names the limit that has passed, or nothing while both hold."""
        if self.closed_as is not None:
            return None
        if self.now >= self.wall_deadline:
            return "wall_clock_expired"
        if self.now >= self.idle_deadline:
            return "settled"
        return None


@dataclass(frozen=True)
class AgentProgress:
    """How far one agent has been told, and how far it has answered.

    Both are sequence numbers on this board, so they are comparable with a
    contribution's ``sequence`` and with each other. An agent is unfinished
    when it has been told further than it has answered, which is what any
    process reads to name the agents a closing run did not hear back from.

    Both only ever rise. Two processes notifying one agent leave the higher
    of what they wrote, so neither can undo the other and no lock is needed
    to keep them ordered.
    """

    agent: str
    notified_through: int
    acknowledged_through: int

    @property
    def outstanding(self) -> bool:
        """True while the agent owes an answer for what it was told."""
        return self.notified_through > self.acknowledged_through


@dataclass(frozen=True)
class Unsent:
    """One notification a write recorded and nothing has sent yet.

    The row exists because the intent to notify is written in the same
    transaction as the contribution, so a process that commits a write and
    stops before delivering has not lost it. Whoever holds the agent sends it
    and marks it sent.

    ``through`` is the sequence the notification's range ends at, which is
    also the identifier it carries.
    """

    board_id: str
    agent: str
    through: int


@dataclass(frozen=True)
class Contribution:
    """One unit stored in a level, at its position in the total order.

    ``writer`` is the name the write carried and ``written_at`` is the
    store's clock when it landed. Both are ``None`` on a record written
    before the library recorded them, and ``writer`` is ``None`` for an
    opening premise value, which the application supplied rather than any
    agent.
    """

    sequence: int
    content: object
    writer: str | None = None
    written_at: datetime | None = None


@dataclass(frozen=True)
class PremiseState:
    """The current value of a premise and the version its write produced.

    ``writer`` and ``written_at`` describe the write that set the current
    value, and are ``None`` where the record predates them or the value is
    the opening one.
    """

    value: object
    version: int
    writer: str | None = None
    written_at: datetime | None = None


@dataclass(frozen=True)
class BoardChange:
    """One write to any region, at its position in the total order.

    ``writer`` and ``written_at`` follow the same rules as on
    :class:`Contribution`.
    """

    sequence: int
    region: str
    content: object
    writer: str | None = None
    written_at: datetime | None = None


@dataclass
class _BoardState:
    """One board's contents inside a store."""

    sequence: int = 0
    levels: dict[str, list[Contribution]] = field(default_factory=dict)
    premises: dict[str, PremiseState | None] = field(default_factory=dict)
    changes: list[BoardChange] = field(default_factory=list)
    #: Every idempotency key this board has written, and what it produced.
    keys: dict[str, tuple[str, Written]] = field(default_factory=dict)
    #: The run over this board, once one is opened.
    run: _RunState | None = None
    #: How far each agent has been notified and has acknowledged.
    agents: dict[str, _AgentProgress] = field(default_factory=dict)
    #: Notifications a write recorded and nothing has sent yet, by agent.
    outbox: dict[str, set[int]] = field(default_factory=dict)


@dataclass
class _AgentProgress:
    """One agent's two watermarks, as the in-memory store holds them."""

    notified_through: int = 0
    acknowledged_through: int = 0


@dataclass
class _RunState:
    """One board's run, as the in-memory store holds it."""

    idle_deadline: datetime
    wall_deadline: datetime
    closed_as: str | None = None
    reason: str | None = None
    unfinished: frozenset[str] = frozenset()


def _already_written(
    board: _BoardState, idempotency_key: str | None, region: str
) -> Written | None:
    """Returns what this key already produced, or ``None`` if it is new.

    A key that named a different region is a mistake rather than a retry.
    """
    if idempotency_key is None:
        return None
    seen = board.keys.get(idempotency_key)
    if seen is None:
        return None
    named, written = seen
    if named != region:
        raise IdempotencyKeyError(
            f"{idempotency_key!r} named {named!r} and is now naming {region!r}"
        )
    return replace(written, repeated=True)


class InMemoryStore:
    """A store held in process memory. A test double, not a way to run a deployment.

    Nothing it holds outlives the process, and two processes running the same
    code share nothing. An application keeps its record in a database through
    an adapter; a test that wants no file uses this.

    Content is carried as JSON, as it is in every store that crosses a process
    boundary. Holding a Python object as it stands would make this store accept
    what a deployment then refuses, and preserve types, such as a tuple, that no
    other store returns. Content that JSON cannot carry raises ``TypeError``
    before anything is stored.

    Sequence assignment is the only point where two writes to one board wait on
    each other, so writes from concurrent threads all succeed with distinct
    sequence numbers.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._boards: dict[str, _BoardState] = {}

    def declare(self, board_id: str, region: Level | Premise) -> None:
        """Creates a region on one board. A name already declared is refused."""
        if not isinstance(region, Level | Premise):
            raise TypeError(
                "a region declaration is a Level or a Premise, "
                f"not {type(region).__name__}"
            )
        with self._lock:
            board = self._boards.setdefault(board_id, _BoardState())
            if region.name in board.levels or region.name in board.premises:
                raise DuplicateRegionError(
                    f"a region named {region.name!r} is already declared"
                )
            if isinstance(region, Level):
                board.levels[region.name] = []
            else:
                board.premises[region.name] = None

    def append(
        self,
        board_id: str,
        level: str,
        content: object,
        idempotency_key: str | None = None,
        writer: str | None = None,
        notify: frozenset[str] = frozenset(),
    ) -> Written:
        """Adds one contribution to a level and returns where it landed."""
        carried = _as_json(content)
        with self._lock:
            board = self._board(board_id)
            contributions = self._level_contributions(board, level)
            done = _already_written(board, idempotency_key, level)
            if done is not None:
                return done
            board.sequence += 1
            now = datetime.now(UTC)
            contributions.append(
                Contribution(
                    sequence=board.sequence,
                    content=carried,
                    writer=writer,
                    written_at=now,
                )
            )
            board.changes.append(
                BoardChange(
                    sequence=board.sequence,
                    region=level,
                    content=carried,
                    writer=writer,
                    written_at=now,
                )
            )
            written = Written(sequence=board.sequence)
            if idempotency_key is not None:
                board.keys[idempotency_key] = (level, written)
            self._enqueue(board, notify, board.sequence)
            return written

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

        A write naming any version other than the premise's current one fails:
        it returns a conflict carrying the current version, takes no sequence
        number, and leaves the premise unchanged. A premise never written has
        version 0. A conflict writes nothing, so it uses up no key.
        """
        carried = _as_json(value)
        with self._lock:
            board = self._board(board_id)
            state = self._premise_state(board, premise)
            done = _already_written(board, idempotency_key, premise)
            if done is not None:
                return done
            current_version = 0 if state is None else state.version
            if expected_version != current_version:
                return Conflict(current_version=current_version)
            board.sequence += 1
            now = datetime.now(UTC)
            board.premises[premise] = PremiseState(
                value=carried,
                version=current_version + 1,
                writer=writer,
                written_at=now,
            )
            board.changes.append(
                BoardChange(
                    sequence=board.sequence,
                    region=premise,
                    content=carried,
                    writer=writer,
                    written_at=now,
                )
            )
            written = Written(sequence=board.sequence, version=current_version + 1)
            if idempotency_key is not None:
                board.keys[idempotency_key] = (premise, written)
            self._enqueue(board, notify, board.sequence)
            return written

    def read_level(
        self,
        board_id: str,
        level: str,
        from_sequence: int = 0,
        limit: int | None = None,
    ) -> list[Contribution]:
        """Returns a level's contributions from the sequence bound, inclusive."""
        with self._lock:
            board = self._board(board_id)
            contributions = self._level_contributions(board, level)
            found = [c for c in contributions if c.sequence >= from_sequence]
            return found if limit is None else found[:limit]

    def read_premise(self, board_id: str, premise: str) -> PremiseState:
        """Returns a premise's current value and version."""
        with self._lock:
            board = self._board(board_id)
            state = self._premise_state(board, premise)
            if state is None:
                raise UnsetPremiseError(
                    f"the premise {premise!r} has no value until one is written"
                )
            return state

    def read_board(
        self, board_id: str, from_sequence: int = 0, limit: int | None = None
    ) -> list[BoardChange]:
        """Returns every write to every region, in sequence order, from the bound."""
        with self._lock:
            board = self._board(board_id)
            found = [c for c in board.changes if c.sequence >= from_sequence]
            return found if limit is None else found[:limit]

    def delete(self, board_id: str) -> Deleted:
        """Removes one board's regions, record, premise values, and counter.

        The application decides when a board is deleted; nothing in the
        library calls this. Close the run first: a `Control` still serving
        this board goes on writing to a record that is no longer there.
        """
        with self._lock:
            board = self._boards.pop(board_id, None)
            if board is None:
                return Deleted(board_id=board_id, regions_removed=0, writes_removed=0)
            return Deleted(
                board_id=board_id,
                regions_removed=len(board.levels) + len(board.premises),
                writes_removed=len(board.changes),
            )

    def open_run(self, board_id: str, *, wall_clock: float, idle: float) -> None:
        with self._lock:
            board = self._board(board_id)
            now = datetime.now(UTC)
            board.run = _RunState(
                idle_deadline=now + timedelta(seconds=idle),
                wall_deadline=now + timedelta(seconds=wall_clock),
            )

    def read_run(self, board_id: str) -> RunRecord | None:
        with self._lock:
            board = self._boards.get(board_id)
            run = None if board is None else board.run
            if run is None:
                return None
            return RunRecord(
                now=datetime.now(UTC),
                idle_deadline=run.idle_deadline,
                wall_deadline=run.wall_deadline,
                closed_as=run.closed_as,
                reason=run.reason,
                unfinished=run.unfinished,
            )

    def touch_run(self, board_id: str, *, idle: float) -> None:
        with self._lock:
            board = self._boards.get(board_id)
            run = None if board is None else board.run
            if run is None or run.closed_as is not None:
                return
            run.idle_deadline = datetime.now(UTC) + timedelta(seconds=idle)

    def close_run(
        self,
        board_id: str,
        *,
        closed_as: str,
        reason: str | None = None,
        unfinished: frozenset[str] = frozenset(),
    ) -> bool:
        with self._lock:
            board = self._boards.get(board_id)
            run = None if board is None else board.run
            if run is None or run.closed_as is not None:
                return False
            run.closed_as = closed_as
            run.reason = reason
            run.unfinished = unfinished
            return True

    def runs_past_deadline(self, limit: int = 100) -> list[str]:
        with self._lock:
            now = datetime.now(UTC)
            found = [
                board_id
                for board_id, board in self._boards.items()
                if board.run is not None
                and board.run.closed_as is None
                and (now >= board.run.idle_deadline or now >= board.run.wall_deadline)
            ]
        return found[:limit]

    def _enqueue(
        self, board: _BoardState, notify: frozenset[str], through: int
    ) -> None:
        # Callers hold self._lock, so the rows land with the write itself.
        for agent in notify:
            board.outbox.setdefault(agent, set()).add(through)

    def unsent(self, limit: int = 100) -> list[Unsent]:
        with self._lock:
            found = [
                Unsent(board_id=board_id, agent=agent, through=through)
                for board_id, board in self._boards.items()
                for agent, pending in board.outbox.items()
                for through in sorted(pending)
            ]
        return sorted(found, key=lambda row: (row.through, row.board_id, row.agent))[
            :limit
        ]

    def mark_sent(self, board_id: str, agent: str, *, through: int) -> None:
        with self._lock:
            board = self._boards.get(board_id)
            if board is None:
                return
            pending = board.outbox.get(agent)
            if pending is None:
                return
            pending -= {n for n in pending if n <= through}
            if not pending:
                del board.outbox[agent]

    def read_agents(self, board_id: str) -> list[AgentProgress]:
        with self._lock:
            board = self._boards.get(board_id)
            if board is None:
                return []
            return [
                AgentProgress(
                    agent=name,
                    notified_through=progress.notified_through,
                    acknowledged_through=progress.acknowledged_through,
                )
                for name, progress in board.agents.items()
            ]

    def mark_notified(self, board_id: str, agent: str, *, through: int) -> None:
        with self._lock:
            board = self._board(board_id)
            progress = board.agents.setdefault(agent, _AgentProgress())
            progress.notified_through = max(progress.notified_through, through)

    def acknowledge(
        self, board_id: str, agent: str, *, through: int
    ) -> AgentProgress | None:
        with self._lock:
            board = self._boards.get(board_id)
            progress = None if board is None else board.agents.get(agent)
            if progress is None or through > progress.notified_through:
                return None
            prior = AgentProgress(
                agent=agent,
                notified_through=progress.notified_through,
                acknowledged_through=progress.acknowledged_through,
            )
            progress.acknowledged_through = max(progress.acknowledged_through, through)
            return prior

    def read_regions(self, board_id: str) -> list[Level | Premise]:
        """Returns the regions declared on one board, with their kinds."""
        with self._lock:
            board = self._board(board_id)
            regions: list[Level | Premise] = [Level(n) for n in board.levels]
            regions.extend(Premise(n) for n in board.premises)
            return sorted(regions, key=lambda r: r.name)

    def _board(self, board_id: str) -> _BoardState:
        # Callers hold self._lock. A board nobody declared a region on holds
        # nothing, and every read of it refuses for the region it names.
        return self._boards.setdefault(board_id, _BoardState())

    def _level_contributions(self, board: _BoardState, name: str) -> list[Contribution]:
        # Callers hold self._lock.
        if name in board.levels:
            return board.levels[name]
        if name in board.premises:
            raise RegionKindError(
                f"{name!r} names a premise, and this operation takes a level"
            )
        raise UndeclaredRegionError(f"no region is declared with the name {name!r}")

    def _premise_state(self, board: _BoardState, name: str) -> PremiseState | None:
        # Callers hold self._lock.
        if name in board.premises:
            return board.premises[name]
        if name in board.levels:
            raise RegionKindError(
                f"{name!r} names a level, and this operation takes a premise"
            )
        raise UndeclaredRegionError(f"no region is declared with the name {name!r}")

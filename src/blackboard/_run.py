"""What a run holds, and where it is kept.

The record is what the agents wrote. The run is what the control component
knows about the work in progress over it: which agents are registered and what
each has read, which notifications are outstanding, how notifications on this
board are numbered, when the run's two deadlines fall, and how it ended.

``BoardStore`` keeps the record. This keeps the run, so that a process which
did not open a board can still serve one, and so that a notification the
control component decided on survives the process deciding it.

``InMemoryRunStore`` keeps it in dictionaries and is what a control component
uses when the caller names no store. An application embedding a blackboard in
one process therefore pays no round trip for state only that process reads.

Three things a run knows are not here, because each is derived rather than
held. ``Agent.notify`` is a callable, and the address behind it belongs to the
application, which rebuilds the callable in each process. A region's kind and
its batch window come from the declarations the caller passes when the run
opens, which every process has. The audit is what one process observed.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Protocol

from blackboard._board import BlackboardError

__all__ = [
    "Acknowledged",
    "Closure",
    "Dispatched",
    "InMemoryRunStore",
    "RegisteredAgent",
    "RunStore",
    "UnknownRunError",
    "sweep",
]


class UnknownRunError(BlackboardError):
    """A run store was asked about a board it holds no run for.

    Opening a run is what creates one. A store asked to register an agent
    against a board nothing opened raises rather than creating the run, so a
    board identifier that was mistyped is not answered with a run of its own.
    """


@dataclass(frozen=True)
class RegisteredAgent:
    """One agent's registration, as the store holds it.

    ``subscribes_to`` and ``writes_to`` carry what the declaration carried,
    and ``None`` means the declaration named neither, which subscribes the
    agent to every premise and to no level and permits every level.

    ``cursor`` is the sequence this agent has acknowledged through. It
    survives a re-registration, because an agent that comes back has not
    forgotten what it read.
    """

    name: str
    subscribes_to: frozenset[str] | None = None
    writes_to: frozenset[str] | None = None
    cursor: int = 0


@dataclass(frozen=True)
class Dispatched:
    """One notification the store recorded, and whether it was answered."""

    notification_id: int
    agent: str
    to_sequence: int
    acknowledged: bool = False


@dataclass(frozen=True)
class Closure:
    """How a run ended, as the store holds it.

    ``outcome`` is the outcome class's name rather than the class, because a
    store records what a column can hold and a class renamed in a later
    release would silently change what a column has meant since the first.
    """

    outcome: str
    unfinished: frozenset[str] = frozenset()
    reason: str = ""


@dataclass(frozen=True)
class Acknowledged:
    """What acknowledging one notification did.

    ``cursor`` is where the agent's cursor now stands. ``covered`` counts the
    notifications the acknowledgment closed, which is more than one where the
    agent held several overlapping ranges, because the cursor is cumulative.
    """

    cursor: int
    covered: int


class RunStore(Protocol):
    """The operations a control component performs on a run.

    A store holds many runs. Every call names the board it acts on, so one
    connection to a database serves every run an application holds.

    Every method is one complete state transition. Two processes serving one
    board reach these at the same time, and the store is where that is made
    safe: a control component's own lock orders the threads of one process and
    says nothing about another.
    """

    def open_run(
        self, board_id: str, *, wall_deadline: datetime, idle_deadline: datetime
    ) -> None:
        """Records that a run over this board is open, with its two deadlines.

        A board that already holds an open run keeps it, and its deadlines are
        left alone, so a second process attaching to a run does not extend the
        wall clock the first one started.
        """
        ...

    def register(
        self,
        board_id: str,
        name: str,
        subscribes_to: Iterable[str] | None,
        writes_to: Iterable[str] | None,
    ) -> RegisteredAgent:
        """Adds an agent to the run, or replaces what a returning one declared.

        Returns the registration as it now stands, including the cursor, which
        is zero for an agent the run has not held and unchanged for one it has.
        """
        ...

    def registered(self, board_id: str) -> list[RegisteredAgent]:
        """Returns every agent registered on this run."""
        ...

    def registration(self, board_id: str, name: str) -> RegisteredAgent | None:
        """Returns one agent's registration, or ``None`` where it has none.

        What a dispatch reads to find the cursor. The cursor moves when the
        agent acknowledges, which it may do to any process, so the process
        about to notify it asks rather than remembering.
        """
        ...

    def issue(self, board_id: str, agent: str, to_sequence: int) -> int:
        """Numbers one notification to this agent and records it outstanding.

        The number counts notifications on this board, so two processes
        issuing at the same time take two numbers rather than one.
        """
        ...

    def acknowledge(
        self, board_id: str, agent: str, notification_id: int
    ) -> Acknowledged | None:
        """Records that an agent finished with a notification.

        Advances the agent's cursor to the end of the range that notification
        covered, and closes every notification to this agent ending at or
        before it, because the cursor is cumulative and answering the wider
        range answered the narrower ones.

        Returns ``None`` where the notification was already acknowledged, and
        raises where it was never issued to that agent.
        """
        ...

    def outstanding(self, board_id: str) -> list[Dispatched]:
        """Returns the notifications on this run that are still unanswered."""
        ...

    def forget(self, board_id: str, agent: str) -> None:
        """Drops what one agent was holding, because it is no longer there.

        A returning agent is told again in one notification covering
        everything since its cursor, so nothing it was owed is lost.
        """
        ...

    def dispatched_through(self, board_id: str) -> int:
        """The highest sequence this run has issued notifications for.

        A process that stopped between a write committing and its notification
        being issued leaves this behind the board's own head, and what covers
        the gap reads the two and compares them.
        """
        ...

    def note_dispatched(self, board_id: str, sequence: int) -> None:
        """Records that notifications have been issued through this sequence."""
        ...

    def push_idle(self, board_id: str, until: datetime) -> None:
        """Moves the idle deadline out, because something happened."""
        ...

    def deadlines(self, board_id: str) -> tuple[datetime, datetime]:
        """The wall clock deadline and the idle deadline, in that order."""
        ...

    def close(
        self,
        board_id: str,
        outcome: str,
        unfinished: Iterable[str],
        reason: str = "",
    ) -> bool:
        """Closes the run, and answers whether this caller is what closed it.

        Conditional on the run still being open, so a local timer and a sweep
        racing, or two sweeps racing, produce one winner and one outcome.

        ``outcome`` is the outcome's class name, ``unfinished`` names the
        agents holding an unanswered notification, which the caller reads
        before closing, and ``reason`` carries what an abort was given.
        """
        ...

    def closed_as(self, board_id: str) -> Closure | None:
        """How the run ended, or ``None`` while it is open."""
        ...

    def expired(self, now: datetime, limit: int | None = None) -> list[str]:
        """The open runs whose wall clock or idle deadline has passed."""
        ...

    def delete(self, board_id: str) -> None:
        """Removes one run's state.

        Nothing in the library calls this. Retention is a decision the
        application makes and the control component makes none.
        """
        ...


@dataclass
class _Run:
    """One board's run, as the in-memory store holds it."""

    wall_deadline: datetime
    idle_deadline: datetime
    agents: dict[str, RegisteredAgent] = field(default_factory=dict)
    notifications: list[Dispatched] = field(default_factory=list)
    next_notification_id: int = 1
    dispatched_through: int = 0
    closure: Closure | None = None


class InMemoryRunStore:
    """Keeps every run in this process. Satisfies ``RunStore``.

    What a control component uses when the caller names no store, and what a
    test uses to drive the run without a database. A second store built in one
    process shares nothing with the first.
    """

    def __init__(self) -> None:
        self._runs: dict[str, _Run] = {}
        self._lock = threading.Lock()

    def _run(self, board_id: str) -> _Run:
        run = self._runs.get(board_id)
        if run is None:
            raise UnknownRunError(f"no run is open on {board_id!r}")
        return run

    def open_run(
        self, board_id: str, *, wall_deadline: datetime, idle_deadline: datetime
    ) -> None:
        with self._lock:
            if board_id in self._runs:
                return
            self._runs[board_id] = _Run(
                wall_deadline=wall_deadline, idle_deadline=idle_deadline
            )

    def register(
        self,
        board_id: str,
        name: str,
        subscribes_to: Iterable[str] | None,
        writes_to: Iterable[str] | None,
    ) -> RegisteredAgent:
        with self._lock:
            run = self._run(board_id)
            returning = run.agents.get(name)
            registered = RegisteredAgent(
                name=name,
                subscribes_to=None
                if subscribes_to is None
                else frozenset(subscribes_to),
                writes_to=None if writes_to is None else frozenset(writes_to),
                cursor=0 if returning is None else returning.cursor,
            )
            run.agents[name] = registered
            return registered

    def registered(self, board_id: str) -> list[RegisteredAgent]:
        with self._lock:
            return list(self._run(board_id).agents.values())

    def registration(self, board_id: str, name: str) -> RegisteredAgent | None:
        with self._lock:
            return self._run(board_id).agents.get(name)

    def issue(self, board_id: str, agent: str, to_sequence: int) -> int:
        with self._lock:
            run = self._run(board_id)
            notification_id = run.next_notification_id
            run.next_notification_id += 1
            run.notifications.append(
                Dispatched(
                    notification_id=notification_id,
                    agent=agent,
                    to_sequence=to_sequence,
                )
            )
            return notification_id

    def acknowledge(
        self, board_id: str, agent: str, notification_id: int
    ) -> Acknowledged | None:
        with self._lock:
            run = self._run(board_id)
            named = [
                held
                for held in run.notifications
                if held.agent == agent and held.notification_id == notification_id
            ]
            if not named:
                raise UnknownRunError(
                    f"no notification {notification_id} was issued to {agent!r}"
                )
            if named[0].acknowledged:
                return None
            through = named[0].to_sequence
            covered = 0
            for index, held in enumerate(run.notifications):
                if (
                    held.agent == agent
                    and not held.acknowledged
                    and held.to_sequence <= through
                ):
                    run.notifications[index] = replace(held, acknowledged=True)
                    covered += 1
            standing = run.agents.get(agent)
            cursor = through
            if standing is not None:
                cursor = max(standing.cursor, through)
                run.agents[agent] = replace(standing, cursor=cursor)
            return Acknowledged(cursor=cursor, covered=covered)

    def outstanding(self, board_id: str) -> list[Dispatched]:
        with self._lock:
            return [h for h in self._run(board_id).notifications if not h.acknowledged]

    def forget(self, board_id: str, agent: str) -> None:
        with self._lock:
            run = self._run(board_id)
            for index, held in enumerate(run.notifications):
                if held.agent == agent and not held.acknowledged:
                    run.notifications[index] = replace(held, acknowledged=True)

    def dispatched_through(self, board_id: str) -> int:
        with self._lock:
            return self._run(board_id).dispatched_through

    def note_dispatched(self, board_id: str, sequence: int) -> None:
        with self._lock:
            run = self._run(board_id)
            run.dispatched_through = max(run.dispatched_through, sequence)

    def push_idle(self, board_id: str, until: datetime) -> None:
        with self._lock:
            run = self._run(board_id)
            if run.closure is None:
                run.idle_deadline = until

    def deadlines(self, board_id: str) -> tuple[datetime, datetime]:
        with self._lock:
            run = self._run(board_id)
            return run.wall_deadline, run.idle_deadline

    def close(
        self,
        board_id: str,
        outcome: str,
        unfinished: Iterable[str],
        reason: str = "",
    ) -> bool:
        with self._lock:
            run = self._run(board_id)
            if run.closure is not None:
                return False
            run.closure = Closure(
                outcome=outcome, unfinished=frozenset(unfinished), reason=reason
            )
            return True

    def closed_as(self, board_id: str) -> Closure | None:
        with self._lock:
            return self._run(board_id).closure

    def expired(self, now: datetime, limit: int | None = None) -> list[str]:
        with self._lock:
            passed = [
                board_id
                for board_id, run in self._runs.items()
                if run.closure is None
                and (run.wall_deadline <= now or run.idle_deadline <= now)
            ]
        return passed if limit is None else passed[:limit]

    def delete(self, board_id: str) -> None:
        with self._lock:
            self._runs.pop(board_id, None)


def sweep(run_store: RunStore, *, now: datetime, limit: int | None = None) -> list[str]:
    """Closes every run whose wall clock or idle deadline has passed.

    A process holding a run closes it on its own timer, which is prompt and
    applies the application's termination predicate. This is for a run no
    process holds, because the one that held it stopped: nothing is working
    it, so the deadline is the whole of the decision.

    Runs closed this way settle. A wall clock that has passed is a run that
    ran out of time, and an idle deadline that has passed with nobody holding
    the run is a run nothing is going to add to.

    Returns the boards it closed, which excludes any a process closed first,
    because closing is conditional on the run still being open.

    Call it from a process that holds the store, on whatever schedule the
    application chooses. That interval is how late a run closes when the
    process holding it stopped, and it bounds nothing else.
    """
    closed = []
    for board_id in run_store.expired(now, limit):
        unfinished = sorted(held.agent for held in run_store.outstanding(board_id))
        if run_store.close(board_id, "Settled", unfinished):
            closed.append(board_id)
    return closed

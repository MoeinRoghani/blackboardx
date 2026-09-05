"""A board kept in SQLite, which is a database and is in the standard library.

This is what a developer runs locally and what a test runs against when it
wants the storage semantics a deployment has: one sequence across every
region, and a premise write guarded by the version it expects to replace.
Where the store is pointed at a file, a run's record survives the process that made it.

One file holds many boards, each under its own identifier, as one server
does. Moving from a file to a server changes the store that is constructed and nothing
else.

Content is stored as JSON, so a contribution must be serialisable. That is
the same contract any adapter across a process boundary imposes, and meeting
it locally means meeting it in deployment.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from typing import Any

from blackboard._board import (
    AgentProgress,
    BoardChange,
    Conflict,
    Contribution,
    Deleted,
    DuplicateRegionError,
    IdempotencyKeyError,
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
from blackboard._schema import stamp_to_write

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_stamp (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS regions (
    board_id TEXT NOT NULL,
    name     TEXT NOT NULL,
    kind     TEXT NOT NULL CHECK (kind IN ('level', 'premise')),
    PRIMARY KEY (board_id, name)
);
CREATE TABLE IF NOT EXISTS contributions (
    board_id        TEXT NOT NULL,
    sequence        INTEGER NOT NULL,
    region          TEXT NOT NULL,
    content         TEXT NOT NULL,
    version         INTEGER,
    idempotency_key TEXT,
    writer          TEXT,
    written_at      TEXT,
    PRIMARY KEY (board_id, sequence)
);
CREATE TABLE IF NOT EXISTS premises (
    board_id TEXT NOT NULL,
    name     TEXT NOT NULL,
    value      TEXT,
    version    INTEGER NOT NULL DEFAULT 0,
    writer     TEXT,
    written_at TEXT,
    PRIMARY KEY (board_id, name)
);
CREATE TABLE IF NOT EXISTS runs (
    board_id       TEXT NOT NULL PRIMARY KEY,
    idle_deadline  TEXT NOT NULL,
    wall_deadline  TEXT NOT NULL,
    closed_as      TEXT,
    reason         TEXT,
    unfinished     TEXT
);
CREATE TABLE IF NOT EXISTS outbox (
    board_id TEXT    NOT NULL,
    agent    TEXT    NOT NULL,
    through  INTEGER NOT NULL,
    PRIMARY KEY (board_id, agent, through)
);
CREATE TABLE IF NOT EXISTS agent_progress (
    board_id             TEXT    NOT NULL,
    agent                TEXT    NOT NULL,
    notified_through     INTEGER NOT NULL DEFAULT 0,
    acknowledged_through INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (board_id, agent)
);
CREATE INDEX IF NOT EXISTS contributions_by_region
    ON contributions (board_id, region, sequence);
CREATE UNIQUE INDEX IF NOT EXISTS contributions_by_key
    ON contributions (board_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
"""

#: Columns added after the first release. A file written by an earlier
#: version is opened by this one, so they are added where they are absent
#: rather than assumed.
_ADDED_COLUMNS = (
    ("version", "INTEGER"),
    ("idempotency_key", "TEXT"),
    ("writer", "TEXT"),
    ("written_at", "TEXT"),
)

#: An instant a given offset after SQLite's clock, for a deadline the store
#: sets. The offset is built in Python because SQLite's modifier needs an
#: explicit sign and a negative one concatenated after a plus does not parse.
_AFTER = "strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now', ?)"


def _offset(seconds: float) -> str:
    """Renders a number of seconds as a SQLite date modifier."""
    return f"{seconds:+f} seconds"


#: SQLite's clock, as an ISO-8601 instant in UTC. The store stamps every
#: write itself, because callers' clocks disagree and the record's do not.
_NOW = "strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')"

_LEVEL = "level"
_PREMISE = "premise"


def _instant(stored: str | None) -> datetime | None:
    """Parses a stamped instant, and passes through the None of an old row."""
    return None if stored is None else datetime.fromisoformat(stored)


class SqliteStore:
    """Keeps the board in SQLite. Satisfies ``BoardStore``.

    ``path`` names the database file and has no default, because where the
    record is kept is stated rather than defaulted. A path on disk suits
    local development, where the record outlives the run that made it.
    ``":memory:"`` keeps it in the process, which suits a test and nothing
    else: a second store over ``":memory:"`` in the same process shares
    nothing with the first, so it reads an empty board.

    One file holds many boards. Every call names the board it acts on, and
    two boards under different identifiers share the file and see none of each other's
    writes, sequence numbers included.

    The schema is created on construction, because SQLite has no server to
    migrate separately and the file is the application's own.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._add_missing_columns()
        self._connection.executescript(_SCHEMA)
        self._stamp(path)
        self._connection.commit()

    def close(self) -> None:
        """Closes the connection. Reopening the same path reads the record back."""
        with self._lock:
            self._connection.close()

    def declare(self, board_id: str, region: Level | Premise) -> None:
        if not isinstance(region, Level | Premise):
            raise TypeError(
                "a region declaration is a Level or a Premise, "
                f"not {type(region).__name__}"
            )
        kind = _LEVEL if isinstance(region, Level) else _PREMISE
        with self._lock, self._connection:
            if self._kind_of(board_id, region.name) is not None:
                raise DuplicateRegionError(
                    f"a region named {region.name!r} is already declared"
                )
            try:
                self._connection.execute(
                    "INSERT INTO regions (board_id, name, kind) VALUES (?, ?, ?)",
                    (board_id, region.name, kind),
                )
            except sqlite3.IntegrityError as clash:
                # Another connection declared the name between the read above
                # and this insert. The refusal is the same either way.
                raise DuplicateRegionError(
                    f"a region named {region.name!r} is already declared"
                ) from clash
            if kind == _PREMISE:
                self._connection.execute(
                    "INSERT INTO premises (board_id, name, value, version) "
                    "VALUES (?, ?, NULL, 0)",
                    (board_id, region.name),
                )

    def _add_missing_columns(self) -> None:
        # executescript runs its own transaction, so this happens first.
        for table in ("contributions", "premises"):
            present = {
                row[1]
                for row in self._connection.execute(
                    f"SELECT * FROM pragma_table_info('{table}')"
                )
            }
            if not present:
                continue
            with self._connection:
                for name, kind in _ADDED_COLUMNS:
                    if name not in present:
                        self._connection.execute(
                            f"ALTER TABLE {table} ADD COLUMN {name} {kind}"
                        )

    def _stamp(self, where: str) -> None:
        # Once, when the file opens, so a record this version cannot read is
        # refused at the door rather than at whichever query touches the
        # missing piece first.
        row = self._connection.execute(
            "SELECT version FROM schema_stamp WHERE id = 1"
        ).fetchone()
        writing = stamp_to_write(None if row is None else int(row[0]), where=where)
        if writing is None:
            return
        with self._connection:
            self._connection.execute(
                "INSERT INTO schema_stamp (id, version) VALUES (1, ?) "
                "ON CONFLICT (id) DO UPDATE SET version = excluded.version",
                (writing,),
            )

    def _already_written(
        self, board_id: str, idempotency_key: str | None, region: str
    ) -> Written | None:
        if idempotency_key is None:
            return None
        row = self._connection.execute(
            "SELECT sequence, region, version FROM contributions "
            "WHERE board_id = ? AND idempotency_key = ?",
            (board_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row[1] != region:
            raise IdempotencyKeyError(
                f"{idempotency_key!r} named {row[1]!r} and is now naming {region!r}"
            )
        return Written(sequence=int(row[0]), version=row[2], repeated=True)

    def append(
        self,
        board_id: str,
        level: str,
        content: object,
        idempotency_key: str | None = None,
        writer: str | None = None,
        notify: frozenset[str] = frozenset(),
    ) -> Written:
        carried = json.dumps(content)
        with self._lock, self._connection:
            self._require(board_id, level, _LEVEL)
            done = self._already_written(board_id, idempotency_key, level)
            if done is not None:
                return done
            sequence = self._next_sequence(board_id)
            self._connection.execute(
                "INSERT INTO contributions "
                "(board_id, sequence, region, content, idempotency_key, writer, "
                f"written_at) VALUES (?, ?, ?, ?, ?, ?, {_NOW})",
                (board_id, sequence, level, carried, idempotency_key, writer),
            )
            self._enqueue(board_id, notify, sequence)
            return Written(sequence=sequence)

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
        carried = json.dumps(value)
        with self._lock, self._connection:
            self._require(board_id, premise, _PREMISE)
            done = self._already_written(board_id, idempotency_key, premise)
            if done is not None:
                return done
            row = self._connection.execute(
                "SELECT version FROM premises WHERE board_id = ? AND name = ?",
                (board_id, premise),
            ).fetchone()
            current = int(row[0])
            if expected_version != current:
                return Conflict(current_version=current)
            sequence = self._next_sequence(board_id)
            self._connection.execute(
                "UPDATE premises SET value = ?, version = ?, writer = ?, "
                f"written_at = {_NOW} WHERE board_id = ? AND name = ?",
                (carried, current + 1, writer, board_id, premise),
            )
            self._connection.execute(
                "INSERT INTO contributions "
                "(board_id, sequence, region, content, version, idempotency_key, "
                f"writer, written_at) VALUES (?, ?, ?, ?, ?, ?, ?, {_NOW})",
                (
                    board_id,
                    sequence,
                    premise,
                    carried,
                    current + 1,
                    idempotency_key,
                    writer,
                ),
            )
            self._enqueue(board_id, notify, sequence)
            return Written(sequence=sequence, version=current + 1)

    def read_level(
        self,
        board_id: str,
        level: str,
        from_sequence: int = 0,
        limit: int | None = None,
    ) -> list[Contribution]:
        with self._lock:
            self._require(board_id, level, _LEVEL)
            rows = self._connection.execute(
                "SELECT sequence, content, writer, written_at FROM contributions "
                "WHERE board_id = ? AND region = ? AND sequence >= ? "
                "ORDER BY sequence LIMIT ?",
                (board_id, level, from_sequence, -1 if limit is None else limit),
            ).fetchall()
        return [
            Contribution(
                sequence=r[0],
                content=json.loads(r[1]),
                writer=r[2],
                written_at=_instant(r[3]),
            )
            for r in rows
        ]

    def read_premise(self, board_id: str, premise: str) -> PremiseState:
        with self._lock:
            self._require(board_id, premise, _PREMISE)
            row = self._connection.execute(
                "SELECT value, version, writer, written_at FROM premises "
                "WHERE board_id = ? AND name = ?",
                (board_id, premise),
            ).fetchone()
        if int(row[1]) == 0:
            raise UnsetPremiseError(
                f"the premise {premise!r} has no value until one is written"
            )
        return PremiseState(
            value=json.loads(row[0]),
            version=int(row[1]),
            writer=row[2],
            written_at=_instant(row[3]),
        )

    def read_board(
        self, board_id: str, from_sequence: int = 0, limit: int | None = None
    ) -> list[BoardChange]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT sequence, region, content, writer, written_at "
                "FROM contributions "
                "WHERE board_id = ? AND sequence >= ? ORDER BY sequence LIMIT ?",
                (board_id, from_sequence, -1 if limit is None else limit),
            ).fetchall()
        return [
            BoardChange(
                sequence=r[0],
                region=r[1],
                content=json.loads(r[2]),
                writer=r[3],
                written_at=_instant(r[4]),
            )
            for r in rows
        ]

    def delete(self, board_id: str) -> Deleted:
        with self._lock, self._connection:
            regions = self._connection.execute(
                "SELECT COUNT(*) FROM regions WHERE board_id = ?", (board_id,)
            ).fetchone()
            writes = self._connection.execute(
                "SELECT COUNT(*) FROM contributions WHERE board_id = ?", (board_id,)
            ).fetchone()
            for table in (
                "contributions",
                "premises",
                "regions",
                "runs",
                "agent_progress",
                "outbox",
            ):
                self._connection.execute(
                    f"DELETE FROM {table} WHERE board_id = ?", (board_id,)
                )
            return Deleted(
                board_id=board_id,
                regions_removed=int(regions[0]),
                writes_removed=int(writes[0]),
            )

    def open_run(self, board_id: str, *, wall_clock: float, idle: float) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO runs "
                "(board_id, idle_deadline, wall_deadline, closed_as, reason, "
                " unfinished) VALUES (?, "
                f"{_AFTER}, {_AFTER}, NULL, NULL, NULL) "
                "ON CONFLICT(board_id) DO UPDATE SET "
                f"idle_deadline = {_AFTER}, wall_deadline = {_AFTER}, "
                "closed_as = NULL, reason = NULL, unfinished = NULL",
                (
                    board_id,
                    _offset(idle),
                    _offset(wall_clock),
                    _offset(idle),
                    _offset(wall_clock),
                ),
            )

    def read_run(self, board_id: str) -> RunRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT idle_deadline, wall_deadline, closed_as, reason, unfinished, "
                f"{_NOW} FROM runs WHERE board_id = ?",
                (board_id,),
            ).fetchone()
        if row is None:
            return None
        return RunRecord(
            now=datetime.fromisoformat(row[5]),
            idle_deadline=datetime.fromisoformat(row[0]),
            wall_deadline=datetime.fromisoformat(row[1]),
            closed_as=row[2],
            reason=row[3],
            unfinished=frozenset(json.loads(row[4])) if row[4] else frozenset(),
        )

    def touch_run(self, board_id: str, *, idle: float) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                f"UPDATE runs SET idle_deadline = {_AFTER} "
                "WHERE board_id = ? AND closed_as IS NULL",
                (_offset(idle), board_id),
            )

    def close_run(
        self,
        board_id: str,
        *,
        closed_as: str,
        reason: str | None = None,
        unfinished: frozenset[str] = frozenset(),
    ) -> bool:
        with self._lock, self._connection:
            changed = self._connection.execute(
                "UPDATE runs SET closed_as = ?, reason = ?, unfinished = ? "
                "WHERE board_id = ? AND closed_as IS NULL",
                (closed_as, reason, json.dumps(sorted(unfinished)), board_id),
            ).rowcount
        return changed == 1

    def runs_past_deadline(self, limit: int = 100) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT board_id FROM runs WHERE closed_as IS NULL "
                f"AND ({_NOW} >= idle_deadline OR {_NOW} >= wall_deadline) "
                "ORDER BY idle_deadline LIMIT ?",
                (limit,),
            ).fetchall()
        return [r[0] for r in rows]

    def _enqueue(self, board_id: str, notify: frozenset[str], through: int) -> None:
        # Callers are inside the write's transaction, so the rows commit with
        # the contribution or not at all.
        if not notify:
            return
        self._connection.executemany(
            "INSERT OR IGNORE INTO outbox (board_id, agent, through) VALUES (?, ?, ?)",
            [(board_id, agent, through) for agent in sorted(notify)],
        )

    def unsent(self, limit: int = 100) -> list[Unsent]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT board_id, agent, through FROM outbox "
                "ORDER BY through, board_id, agent LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            Unsent(board_id=str(r[0]), agent=str(r[1]), through=int(r[2])) for r in rows
        ]

    def mark_sent(self, board_id: str, agent: str, *, through: int) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM outbox WHERE board_id = ? AND agent = ? AND through = ?",
                (board_id, agent, through),
            )

    def read_agents(self, board_id: str) -> list[AgentProgress]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT agent, notified_through, acknowledged_through "
                "FROM agent_progress WHERE board_id = ? ORDER BY agent",
                (board_id,),
            ).fetchall()
        return [
            AgentProgress(
                agent=str(r[0]),
                notified_through=int(r[1]),
                acknowledged_through=int(r[2]),
            )
            for r in rows
        ]

    def mark_notified(self, board_id: str, agent: str, *, through: int) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO agent_progress "
                "(board_id, agent, notified_through, acknowledged_through) "
                "VALUES (?, ?, ?, 0) "
                "ON CONFLICT (board_id, agent) DO UPDATE SET "
                "notified_through = MAX(notified_through, excluded.notified_through)",
                (board_id, agent, through),
            )

    def acknowledge(
        self, board_id: str, agent: str, *, through: int
    ) -> AgentProgress | None:
        # The read and the write are one transaction, so the entry returned
        # is the one this call raised and not one a concurrent caller left.
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT notified_through, acknowledged_through FROM agent_progress "
                "WHERE board_id = ? AND agent = ?",
                (board_id, agent),
            ).fetchone()
            if row is None or through > int(row[0]):
                return None
            self._connection.execute(
                "UPDATE agent_progress SET acknowledged_through = "
                "MAX(acknowledged_through, ?) WHERE board_id = ? AND agent = ?",
                (through, board_id, agent),
            )
            return AgentProgress(
                agent=agent,
                notified_through=int(row[0]),
                acknowledged_through=int(row[1]),
            )

    def read_regions(self, board_id: str) -> list[Level | Premise]:
        """Returns the regions declared on one board, with their kinds."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT name, kind FROM regions WHERE board_id = ? ORDER BY name",
                (board_id,),
            ).fetchall()
        return [
            Level(str(r[0])) if r[1] == _LEVEL else Premise(str(r[0])) for r in rows
        ]

    def _kind_of(self, board_id: str, name: str) -> str | None:
        # Callers hold self._lock.
        row = self._connection.execute(
            "SELECT kind FROM regions WHERE board_id = ? AND name = ?",
            (board_id, name),
        ).fetchone()
        return None if row is None else str(row[0])

    def _require(self, board_id: str, name: str, kind: str) -> None:
        # Callers hold self._lock.
        found = self._kind_of(board_id, name)
        if found is None:
            raise UndeclaredRegionError(f"no region is declared with the name {name!r}")
        if found != kind:
            if kind == _LEVEL:
                raise RegionKindError(
                    f"{name!r} names a premise, and this operation takes a level"
                )
            raise RegionKindError(
                f"{name!r} names a level, and this operation takes a premise"
            )

    def _next_sequence(self, board_id: str) -> int:
        # Callers hold self._lock. One counter across every region of this
        # board, taken from the record itself so that reopening a file
        # continues where it left off.
        row: Any = self._connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) FROM contributions WHERE board_id = ?",
            (board_id,),
        ).fetchone()
        return int(row[0]) + 1

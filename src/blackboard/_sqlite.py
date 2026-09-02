"""A board kept in SQLite, which is a database and is in the standard library.

This is what a developer runs locally and what a test runs against when it
wants the storage semantics a deployment has: one sequence across every
region, and a premise write guarded by the version it expects to replace.
Pointed at a file, a run's record survives the process that made it.

One file holds many boards, each under its own identifier, as one server
does. Moving from a file to a server changes the board that is constructed
and nothing else.

Content is stored as JSON, so a contribution must be serialisable. That is
the same contract any adapter across a process boundary imposes, and meeting
it locally means meeting it in deployment.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

from blackboard._board import (
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
    UndeclaredRegionError,
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
    PRIMARY KEY (board_id, sequence)
);
CREATE TABLE IF NOT EXISTS premises (
    board_id TEXT NOT NULL,
    name     TEXT NOT NULL,
    value    TEXT,
    version  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (board_id, name)
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
_ADDED_COLUMNS = (("version", "INTEGER"), ("idempotency_key", "TEXT"))

_LEVEL = "level"
_PREMISE = "premise"


class SqliteStore:
    """Keeps the board in SQLite. Satisfies ``BoardStore``.

    ``path`` names the database file and has no default, because where the
    record is kept is stated rather than defaulted. A path on disk suits
    local development, where the record outlives the run that made it.
    ``":memory:"`` keeps it in the process, which suits a test and nothing
    else: a second store over ``":memory:"`` in the same process shares
    nothing with the first, so it reads an empty board.

    ``board_id`` names the board within that file. Two boards under
    different identifiers share the file and see none of each other's
    writes, including the sequence.

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
        present = {
            row[1]
            for row in self._connection.execute(
                "SELECT * FROM pragma_table_info('contributions')"
            )
        }
        if not present:
            return
        with self._connection:
            for name, kind in _ADDED_COLUMNS:
                if name not in present:
                    self._connection.execute(
                        f"ALTER TABLE contributions ADD COLUMN {name} {kind}"
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
                "(board_id, sequence, region, content, idempotency_key) "
                "VALUES (?, ?, ?, ?, ?)",
                (board_id, sequence, level, carried, idempotency_key),
            )
            return Written(sequence=sequence)

    def set(
        self,
        board_id: str,
        premise: str,
        value: object,
        expected_version: int,
        idempotency_key: str | None = None,
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
                "UPDATE premises SET value = ?, version = ? "
                "WHERE board_id = ? AND name = ?",
                (carried, current + 1, board_id, premise),
            )
            self._connection.execute(
                "INSERT INTO contributions "
                "(board_id, sequence, region, content, version, idempotency_key) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (board_id, sequence, premise, carried, current + 1, idempotency_key),
            )
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
                "SELECT sequence, content FROM contributions "
                "WHERE board_id = ? AND region = ? AND sequence >= ? "
                "ORDER BY sequence LIMIT ?",
                (board_id, level, from_sequence, -1 if limit is None else limit),
            ).fetchall()
        return [Contribution(sequence=r[0], content=json.loads(r[1])) for r in rows]

    def read_premise(self, board_id: str, premise: str) -> PremiseState:
        with self._lock:
            self._require(board_id, premise, _PREMISE)
            row = self._connection.execute(
                "SELECT value, version FROM premises WHERE board_id = ? AND name = ?",
                (board_id, premise),
            ).fetchone()
        if int(row[1]) == 0:
            raise UnsetPremiseError(
                f"the premise {premise!r} has no value until one is written"
            )
        return PremiseState(value=json.loads(row[0]), version=int(row[1]))

    def read_board(
        self, board_id: str, from_sequence: int = 0, limit: int | None = None
    ) -> list[BoardChange]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT sequence, region, content FROM contributions "
                "WHERE board_id = ? AND sequence >= ? ORDER BY sequence LIMIT ?",
                (board_id, from_sequence, -1 if limit is None else limit),
            ).fetchall()
        return [
            BoardChange(sequence=r[0], region=r[1], content=json.loads(r[2]))
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
            for table in ("contributions", "premises", "regions"):
                self._connection.execute(
                    f"DELETE FROM {table} WHERE board_id = ?", (board_id,)
                )
            return Deleted(
                board_id=board_id, regions=int(regions[0]), writes=int(writes[0])
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

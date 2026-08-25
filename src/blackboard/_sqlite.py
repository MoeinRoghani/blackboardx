"""A board kept in SQLite, which is a database and is in the standard library.

This is what a developer runs locally and what a test runs against when it
wants the storage semantics a deployment has: one sequence across every
region, and a register write guarded by the version it expects to replace.
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
    DuplicateRegionError,
    Level,
    RegionKindError,
    Register,
    RegisterState,
    UndeclaredRegionError,
    UnsetRegisterError,
    Written,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS regions (
    board_id TEXT NOT NULL,
    name     TEXT NOT NULL,
    kind     TEXT NOT NULL CHECK (kind IN ('level', 'register')),
    PRIMARY KEY (board_id, name)
);
CREATE TABLE IF NOT EXISTS contributions (
    board_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    region   TEXT NOT NULL,
    content  TEXT NOT NULL,
    PRIMARY KEY (board_id, sequence)
);
CREATE TABLE IF NOT EXISTS registers (
    board_id TEXT NOT NULL,
    name     TEXT NOT NULL,
    value    TEXT,
    version  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (board_id, name)
);
CREATE INDEX IF NOT EXISTS contributions_by_region
    ON contributions (board_id, region, sequence);
"""

_LEVEL = "level"
_REGISTER = "register"


class SqliteBoard:
    """Keeps the board in SQLite. Satisfies ``BoardStore``.

    ``path`` names the database file. The default, ``":memory:"``, keeps it
    in the process, which suits a test; a path on disk suits local
    development, where the record outlives the run that made it.

    ``board_id`` names the board within that file. Two boards under
    different identifiers share the file and see none of each other's
    writes, including the sequence.

    The schema is created on construction, because SQLite has no server to
    migrate separately and the file is the application's own.
    """

    def __init__(self, path: str = ":memory:", *, board_id: str = "default") -> None:
        self._path = path
        self._board_id = board_id
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.executescript(_SCHEMA)
        self._connection.commit()

    def close(self) -> None:
        """Closes the connection. Reopening the same path reads the record back."""
        with self._lock:
            self._connection.close()

    def declare(self, region: Level | Register) -> None:
        if not isinstance(region, Level | Register):
            raise TypeError(
                "a region declaration is a Level or a Register, "
                f"not {type(region).__name__}"
            )
        kind = _LEVEL if isinstance(region, Level) else _REGISTER
        with self._lock, self._connection:
            if self._kind_of(region.name) is not None:
                raise DuplicateRegionError(
                    f"a region named {region.name!r} is already declared"
                )
            self._connection.execute(
                "INSERT INTO regions (board_id, name, kind) VALUES (?, ?, ?)",
                (self._board_id, region.name, kind),
            )
            if kind == _REGISTER:
                self._connection.execute(
                    "INSERT INTO registers (board_id, name, value, version) "
                    "VALUES (?, ?, NULL, 0)",
                    (self._board_id, region.name),
                )

    def append(self, level: str, content: object) -> int:
        carried = json.dumps(content)
        with self._lock, self._connection:
            self._require(level, _LEVEL)
            sequence = self._next_sequence()
            self._connection.execute(
                "INSERT INTO contributions (board_id, sequence, region, content) "
                "VALUES (?, ?, ?, ?)",
                (self._board_id, sequence, level, carried),
            )
            return sequence

    def set(
        self, register: str, value: object, expected_version: int
    ) -> Written | Conflict:
        carried = json.dumps(value)
        with self._lock, self._connection:
            self._require(register, _REGISTER)
            row = self._connection.execute(
                "SELECT version FROM registers WHERE board_id = ? AND name = ?",
                (self._board_id, register),
            ).fetchone()
            current = int(row[0])
            if expected_version != current:
                return Conflict(current_version=current)
            sequence = self._next_sequence()
            self._connection.execute(
                "UPDATE registers SET value = ?, version = ? "
                "WHERE board_id = ? AND name = ?",
                (carried, current + 1, self._board_id, register),
            )
            self._connection.execute(
                "INSERT INTO contributions (board_id, sequence, region, content) "
                "VALUES (?, ?, ?, ?)",
                (self._board_id, sequence, register, carried),
            )
            return Written(sequence=sequence, version=current + 1)

    def read_level(self, level: str, from_sequence: int = 0) -> list[Contribution]:
        with self._lock:
            self._require(level, _LEVEL)
            rows = self._connection.execute(
                "SELECT sequence, content FROM contributions "
                "WHERE board_id = ? AND region = ? AND sequence >= ? "
                "ORDER BY sequence",
                (self._board_id, level, from_sequence),
            ).fetchall()
        return [Contribution(sequence=r[0], content=json.loads(r[1])) for r in rows]

    def read_register(self, register: str) -> RegisterState:
        with self._lock:
            self._require(register, _REGISTER)
            row = self._connection.execute(
                "SELECT value, version FROM registers WHERE board_id = ? AND name = ?",
                (self._board_id, register),
            ).fetchone()
        if int(row[1]) == 0:
            raise UnsetRegisterError(
                f"the register {register!r} has no value until one is written"
            )
        return RegisterState(value=json.loads(row[0]), version=int(row[1]))

    def read_board(self, from_sequence: int = 0) -> list[BoardChange]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT sequence, region, content FROM contributions "
                "WHERE board_id = ? AND sequence >= ? ORDER BY sequence",
                (self._board_id, from_sequence),
            ).fetchall()
        return [
            BoardChange(sequence=r[0], region=r[1], content=json.loads(r[2]))
            for r in rows
        ]

    def _kind_of(self, name: str) -> str | None:
        # Callers hold self._lock.
        row = self._connection.execute(
            "SELECT kind FROM regions WHERE board_id = ? AND name = ?",
            (self._board_id, name),
        ).fetchone()
        return None if row is None else str(row[0])

    def _require(self, name: str, kind: str) -> None:
        # Callers hold self._lock.
        found = self._kind_of(name)
        if found is None:
            raise UndeclaredRegionError(f"no region is declared with the name {name!r}")
        if found != kind:
            if kind == _LEVEL:
                raise RegionKindError(
                    f"{name!r} names a register, and this operation takes a level"
                )
            raise RegionKindError(
                f"{name!r} names a level, and this operation takes a register"
            )

    def _next_sequence(self) -> int:
        # Callers hold self._lock. One counter across every region of this
        # board, taken from the record itself so that reopening a file
        # continues where it left off.
        row: Any = self._connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) FROM contributions WHERE board_id = ?",
            (self._board_id,),
        ).fetchone()
        return int(row[0]) + 1

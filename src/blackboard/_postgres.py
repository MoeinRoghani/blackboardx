"""A board kept in Postgres, through a connection the application supplies.

The library owns no server, no credentials, and no migration tool. It is
handed a connection pool the application already configures, so pooling,
failover, and secrets stay where an operator manages them.

One database holds many boards, each under its own identifier. Every row
carries it, so a deployment serving many concurrent runs is the ordinary
case.

Two guarantees hold across processes, not merely across the threads of one:

The sequence is gapless. Every write takes it by incrementing a row of
``blackboard_boards`` under the row lock that update acquires, and holds
that lock until the transaction commits. Writes to one board are therefore
serialised, and a number a rolled-back write took is returned rather than
skipped. A Postgres sequence would be faster and would leave gaps, and a
gap is a hole in a record whose numbers are addresses.

A register write is a conditional update on the version. Two writers
naming the same version produce one winner and one ``Conflict``, whichever
processes reach the row.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Protocol

from psycopg_pool import ConnectionPool as _PsycopgPool

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

if TYPE_CHECKING:
    from contextlib import AbstractContextManager


_SCHEMA = """
CREATE TABLE IF NOT EXISTS blackboard_boards (
    board_id      TEXT PRIMARY KEY,
    next_sequence BIGINT NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS blackboard_regions (
    board_id TEXT NOT NULL,
    name     TEXT NOT NULL,
    kind     TEXT NOT NULL CHECK (kind IN ('level', 'register')),
    PRIMARY KEY (board_id, name)
);
CREATE TABLE IF NOT EXISTS blackboard_contributions (
    board_id TEXT   NOT NULL,
    sequence BIGINT NOT NULL,
    region   TEXT   NOT NULL,
    content  JSONB  NOT NULL,
    PRIMARY KEY (board_id, sequence)
);
CREATE TABLE IF NOT EXISTS blackboard_registers (
    board_id TEXT   NOT NULL,
    name     TEXT   NOT NULL,
    value    JSONB,
    version  BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (board_id, name)
);
CREATE INDEX IF NOT EXISTS blackboard_contributions_by_region
    ON blackboard_contributions (board_id, region, sequence);
"""

_LEVEL = "level"
_REGISTER = "register"


class _RollBack(Exception):
    """Leaves a transaction by the only door that undoes it."""


class ConnectionPool(Protocol):
    """What this adapter needs of a connection pool.

    ``psycopg_pool.ConnectionPool`` satisfies it. So does anything else that
    hands out a connection for the duration of a ``with`` block and takes it
    back at the end.
    """

    def connection(self, *args: Any, **kwargs: Any) -> AbstractContextManager[Any]:
        """Lends a connection for the duration of a ``with`` block."""
        ...


class PostgresBoard:
    """Keeps the board in Postgres. Satisfies ``BoardStore``.

    ``pool`` is the application's own connection pool, and this adapter
    neither opens nor closes it. ``board_id`` names the board within the
    database; two boards under different identifiers share the tables and
    see none of each other's writes.

    Requires the ``postgres`` extra::

        pip install 'blackboardx[postgres]'

    Call :meth:`create_schema` once against a database that has none, or
    run the equivalent DDL from whatever migration tool the application
    already uses.
    """

    def __init__(self, pool: ConnectionPool, *, board_id: str = "default") -> None:
        self._pool = pool
        self._board_id = board_id

    @classmethod
    @contextmanager
    def from_dsn(
        cls, dsn: str, *, board_id: str = "default", **pool_kwargs: Any
    ) -> Iterator[PostgresBoard]:
        """Opens a pool for the duration of a ``with`` block, for a script or test.

        An application that already runs a pool passes it to the constructor
        instead. This is for the cases that have none to pass.
        """
        with _PsycopgPool(dsn, **pool_kwargs) as pool:
            yield cls(pool, board_id=board_id)

    def create_schema(self) -> None:
        """Creates the tables this adapter reads, if they are not there.

        Every statement is ``IF NOT EXISTS``, so calling it against a
        database that already has them changes nothing.
        """
        with self._pool.connection() as connection:
            connection.execute(_SCHEMA)

    def declare(self, region: Level | Register) -> None:
        if not isinstance(region, Level | Register):
            raise TypeError(
                "a region declaration is a Level or a Register, "
                f"not {type(region).__name__}"
            )
        kind = _LEVEL if isinstance(region, Level) else _REGISTER
        with self._pool.connection() as connection, connection.transaction():
            self._open_board(connection)
            if self._kind_of(connection, region.name) is not None:
                raise DuplicateRegionError(
                    f"a region named {region.name!r} is already declared"
                )
            connection.execute(
                "INSERT INTO blackboard_regions (board_id, name, kind) "
                "VALUES (%s, %s, %s)",
                (self._board_id, region.name, kind),
            )
            if kind == _REGISTER:
                connection.execute(
                    "INSERT INTO blackboard_registers (board_id, name, value, version) "
                    "VALUES (%s, %s, NULL, 0)",
                    (self._board_id, region.name),
                )

    def append(self, level: str, content: object) -> int:
        carried = json.dumps(content)
        with self._pool.connection() as connection, connection.transaction():
            self._require(connection, level, _LEVEL)
            sequence = self._take_sequence(connection)
            connection.execute(
                "INSERT INTO blackboard_contributions "
                "(board_id, sequence, region, content) VALUES (%s, %s, %s, %s::jsonb)",
                (self._board_id, sequence, level, carried),
            )
            return sequence

    def set(
        self, register: str, value: object, expected_version: int
    ) -> Written | Conflict:
        carried = json.dumps(value)
        outcome: Written | Conflict
        with self._pool.connection() as connection:
            try:
                with connection.transaction():
                    self._require(connection, register, _REGISTER)
                    # Taking the sequence first locks the board row, so no
                    # concurrent write to this board can interleave with the
                    # conditional update below.
                    sequence = self._take_sequence(connection)
                    updated = connection.execute(
                        "UPDATE blackboard_registers SET value = %s::jsonb, "
                        "version = version + 1 "
                        "WHERE board_id = %s AND name = %s AND version = %s "
                        "RETURNING version",
                        (carried, self._board_id, register, expected_version),
                    ).fetchone()
                    if updated is None:
                        outcome = Conflict(
                            current_version=self._current_version(connection, register)
                        )
                        # Leaving by an exception is what undoes the
                        # sequence this write took, so a conflict skips no
                        # number.
                        raise _RollBack
                    connection.execute(
                        "INSERT INTO blackboard_contributions "
                        "(board_id, sequence, region, content) "
                        "VALUES (%s, %s, %s, %s::jsonb)",
                        (self._board_id, sequence, register, carried),
                    )
                    outcome = Written(sequence=sequence, version=int(updated[0]))
            except _RollBack:
                pass
        return outcome

    def read_level(self, level: str, from_sequence: int = 0) -> list[Contribution]:
        with self._pool.connection() as connection:
            self._require(connection, level, _LEVEL)
            rows = connection.execute(
                "SELECT sequence, content FROM blackboard_contributions "
                "WHERE board_id = %s AND region = %s AND sequence >= %s "
                "ORDER BY sequence",
                (self._board_id, level, from_sequence),
            ).fetchall()
        return [Contribution(sequence=int(r[0]), content=r[1]) for r in rows]

    def read_register(self, register: str) -> RegisterState:
        with self._pool.connection() as connection:
            self._require(connection, register, _REGISTER)
            row = connection.execute(
                "SELECT value, version FROM blackboard_registers "
                "WHERE board_id = %s AND name = %s",
                (self._board_id, register),
            ).fetchone()
        if row is None or int(row[1]) == 0:
            raise UnsetRegisterError(
                f"the register {register!r} has no value until one is written"
            )
        return RegisterState(value=row[0], version=int(row[1]))

    def read_board(self, from_sequence: int = 0) -> list[BoardChange]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                "SELECT sequence, region, content FROM blackboard_contributions "
                "WHERE board_id = %s AND sequence >= %s ORDER BY sequence",
                (self._board_id, from_sequence),
            ).fetchall()
        return [
            BoardChange(sequence=int(r[0]), region=r[1], content=r[2]) for r in rows
        ]

    def _open_board(self, connection: Any) -> None:
        connection.execute(
            "INSERT INTO blackboard_boards (board_id) VALUES (%s) "
            "ON CONFLICT (board_id) DO NOTHING",
            (self._board_id,),
        )

    def _take_sequence(self, connection: Any) -> int:
        row = connection.execute(
            "UPDATE blackboard_boards SET next_sequence = next_sequence + 1 "
            "WHERE board_id = %s RETURNING next_sequence - 1",
            (self._board_id,),
        ).fetchone()
        if row is None:
            # A write can only reach here through a declared region, and
            # declaring one opens the board.
            raise UndeclaredRegionError(
                f"no board is open under the identifier {self._board_id!r}"
            )
        return int(row[0])

    def _kind_of(self, connection: Any, name: str) -> str | None:
        row = connection.execute(
            "SELECT kind FROM blackboard_regions WHERE board_id = %s AND name = %s",
            (self._board_id, name),
        ).fetchone()
        return None if row is None else str(row[0])

    def _current_version(self, connection: Any, register: str) -> int:
        row = connection.execute(
            "SELECT version FROM blackboard_registers "
            "WHERE board_id = %s AND name = %s",
            (self._board_id, register),
        ).fetchone()
        return 0 if row is None else int(row[0])

    def _require(self, connection: Any, name: str, kind: str) -> None:
        found = self._kind_of(connection, name)
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

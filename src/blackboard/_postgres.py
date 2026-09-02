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

A premise write is a conditional update on the version. Two writers
naming the same version produce one winner and one ``Conflict``, whichever
processes reach the row.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Protocol

from psycopg.errors import UniqueViolation
from psycopg_pool import ConnectionPool as _PsycopgPool

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

if TYPE_CHECKING:
    from contextlib import AbstractContextManager


_SCHEMA = """
CREATE TABLE IF NOT EXISTS blackboard_schema (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    version BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS blackboard_boards (
    board_id      TEXT PRIMARY KEY,
    next_sequence BIGINT NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS blackboard_regions (
    board_id TEXT NOT NULL,
    name     TEXT NOT NULL,
    kind     TEXT NOT NULL CHECK (kind IN ('level', 'premise')),
    PRIMARY KEY (board_id, name)
);
CREATE TABLE IF NOT EXISTS blackboard_contributions (
    board_id        TEXT   NOT NULL,
    sequence        BIGINT NOT NULL,
    region          TEXT   NOT NULL,
    content         JSONB  NOT NULL,
    version         BIGINT,
    idempotency_key TEXT,
    PRIMARY KEY (board_id, sequence)
);
ALTER TABLE blackboard_contributions
    ADD COLUMN IF NOT EXISTS version BIGINT;
ALTER TABLE blackboard_contributions
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
CREATE TABLE IF NOT EXISTS blackboard_premises (
    board_id TEXT   NOT NULL,
    name     TEXT   NOT NULL,
    value    JSONB,
    version  BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (board_id, name)
);
CREATE INDEX IF NOT EXISTS blackboard_contributions_by_region
    ON blackboard_contributions (board_id, region, sequence);
CREATE UNIQUE INDEX IF NOT EXISTS blackboard_contributions_by_key
    ON blackboard_contributions (board_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
"""

_LEVEL = "level"
_PREMISE = "premise"


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


class PostgresStore:
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

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool
        self._stamped = False

    @classmethod
    @contextmanager
    def from_dsn(cls, dsn: str, **pool_kwargs: Any) -> Iterator[PostgresStore]:
        """Opens a pool for the duration of a ``with`` block, for a script or test.

        An application that already runs a pool passes it to the constructor
        instead. This is for the cases that have none to pass.
        """
        with _PsycopgPool(dsn, **pool_kwargs) as pool:
            yield cls(pool)

    def create_schema(self) -> None:
        """Creates the tables this adapter reads, if they are not there.

        Every statement is ``IF NOT EXISTS``, so calling it against a
        database that already has them changes nothing.
        """
        with self._pool.connection() as connection:
            connection.execute(_SCHEMA)
        self._stamp()

    def declare(self, board_id: str, region: Level | Premise) -> None:
        self._checked()
        if not isinstance(region, Level | Premise):
            raise TypeError(
                "a region declaration is a Level or a Premise, "
                f"not {type(region).__name__}"
            )
        kind = _LEVEL if isinstance(region, Level) else _PREMISE
        with self._pool.connection() as connection, connection.transaction():
            self._open_board(connection, board_id)
            if self._kind_of(connection, board_id, region.name) is not None:
                raise DuplicateRegionError(
                    f"a region named {region.name!r} is already declared"
                )
            try:
                connection.execute(
                    "INSERT INTO blackboard_regions (board_id, name, kind) "
                    "VALUES (%s, %s, %s)",
                    (board_id, region.name, kind),
                )
            except UniqueViolation as clash:
                # Another process declared the name between the read above and
                # this insert. The refusal is the same either way.
                raise DuplicateRegionError(
                    f"a region named {region.name!r} is already declared"
                ) from clash
            if kind == _PREMISE:
                connection.execute(
                    "INSERT INTO blackboard_premises (board_id, name, value, version) "
                    "VALUES (%s, %s, NULL, 0)",
                    (board_id, region.name),
                )

    def _stamp(self) -> None:
        """Records the schema this version writes, or refuses one it cannot read.

        Called when the schema is created and again before the first
        operation, so a database this store never created is checked too.
        """
        with self._pool.connection() as connection, connection.transaction():
            connection.execute(
                "CREATE TABLE IF NOT EXISTS blackboard_schema ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), version BIGINT NOT NULL)"
            )
            row = connection.execute(
                "SELECT version FROM blackboard_schema WHERE id = 1"
            ).fetchone()
            writing = stamp_to_write(
                None if row is None else int(row[0]), where="this database"
            )
            if writing is not None:
                connection.execute(
                    "INSERT INTO blackboard_schema (id, version) VALUES (1, %s) "
                    "ON CONFLICT (id) DO UPDATE SET version = excluded.version",
                    (writing,),
                )
        self._stamped = True

    def _checked(self) -> None:
        # Once per store. An application that points this at a database it
        # did not create never calls create_schema, and the check has to
        # happen anyway.
        if not self._stamped:
            self._stamp()

    def _already_written(
        self, connection: Any, board_id: str, idempotency_key: str | None, region: str
    ) -> Written | None:
        if idempotency_key is None:
            return None
        row = connection.execute(
            "SELECT sequence, region, version FROM blackboard_contributions "
            "WHERE board_id = %s AND idempotency_key = %s",
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
        self._checked()
        carried = json.dumps(content)
        with self._pool.connection() as connection, connection.transaction():
            self._require(connection, board_id, level, _LEVEL)
            done = self._already_written(connection, board_id, idempotency_key, level)
            if done is not None:
                return done
            sequence = self._take_sequence(connection, board_id)
            connection.execute(
                "INSERT INTO blackboard_contributions "
                "(board_id, sequence, region, content, idempotency_key) "
                "VALUES (%s, %s, %s, %s::jsonb, %s)",
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
        self._checked()
        carried = json.dumps(value)
        outcome: Written | Conflict
        with self._pool.connection() as connection:
            try:
                with connection.transaction():
                    self._require(connection, board_id, premise, _PREMISE)
                    done = self._already_written(
                        connection, board_id, idempotency_key, premise
                    )
                    if done is not None:
                        return done
                    # Taking the sequence first locks the board row, so no
                    # concurrent write to this board can interleave with the
                    # conditional update below.
                    sequence = self._take_sequence(connection, board_id)
                    updated = connection.execute(
                        "UPDATE blackboard_premises SET value = %s::jsonb, "
                        "version = version + 1 "
                        "WHERE board_id = %s AND name = %s AND version = %s "
                        "RETURNING version",
                        (carried, board_id, premise, expected_version),
                    ).fetchone()
                    if updated is None:
                        outcome = Conflict(
                            current_version=self._current_version(
                                connection, board_id, premise
                            )
                        )
                        # Leaving by an exception is what undoes the
                        # sequence this write took, so a conflict skips no
                        # number.
                        raise _RollBack
                    connection.execute(
                        "INSERT INTO blackboard_contributions (board_id, sequence,"
                        " region, content, version, idempotency_key) "
                        "VALUES (%s, %s, %s, %s::jsonb, %s, %s)",
                        (
                            board_id,
                            sequence,
                            premise,
                            carried,
                            int(updated[0]),
                            idempotency_key,
                        ),
                    )
                    outcome = Written(sequence=sequence, version=int(updated[0]))
            except _RollBack:
                pass
        return outcome

    def read_level(
        self,
        board_id: str,
        level: str,
        from_sequence: int = 0,
        limit: int | None = None,
    ) -> list[Contribution]:
        self._checked()
        with self._pool.connection() as connection:
            self._require(connection, board_id, level, _LEVEL)
            rows = connection.execute(
                "SELECT sequence, content FROM blackboard_contributions "
                "WHERE board_id = %s AND region = %s AND sequence >= %s "
                "ORDER BY sequence LIMIT %s",
                (board_id, level, from_sequence, limit),
            ).fetchall()
        return [Contribution(sequence=int(r[0]), content=r[1]) for r in rows]

    def read_premise(self, board_id: str, premise: str) -> PremiseState:
        self._checked()
        with self._pool.connection() as connection:
            self._require(connection, board_id, premise, _PREMISE)
            row = connection.execute(
                "SELECT value, version FROM blackboard_premises "
                "WHERE board_id = %s AND name = %s",
                (board_id, premise),
            ).fetchone()
        if row is None or int(row[1]) == 0:
            raise UnsetPremiseError(
                f"the premise {premise!r} has no value until one is written"
            )
        return PremiseState(value=row[0], version=int(row[1]))

    def read_board(
        self, board_id: str, from_sequence: int = 0, limit: int | None = None
    ) -> list[BoardChange]:
        self._checked()
        with self._pool.connection() as connection:
            rows = connection.execute(
                "SELECT sequence, region, content FROM blackboard_contributions "
                "WHERE board_id = %s AND sequence >= %s ORDER BY sequence LIMIT %s",
                (board_id, from_sequence, limit),
            ).fetchall()
        return [
            BoardChange(sequence=int(r[0]), region=r[1], content=r[2]) for r in rows
        ]

    def delete(self, board_id: str) -> Deleted:
        self._checked()
        with self._pool.connection() as connection, connection.transaction():
            regions = connection.execute(
                "SELECT COUNT(*) FROM blackboard_regions WHERE board_id = %s",
                (board_id,),
            ).fetchone()
            writes = connection.execute(
                "SELECT COUNT(*) FROM blackboard_contributions WHERE board_id = %s",
                (board_id,),
            ).fetchone()
            for table in (
                "blackboard_contributions",
                "blackboard_premises",
                "blackboard_regions",
                "blackboard_boards",
            ):
                connection.execute(
                    f"DELETE FROM {table} WHERE board_id = %s", (board_id,)
                )
            return Deleted(
                board_id=board_id, regions=int(regions[0]), writes=int(writes[0])
            )

    def read_regions(self, board_id: str) -> list[Level | Premise]:
        self._checked()
        """Returns the regions declared on one board, with their kinds."""
        with self._pool.connection() as connection:
            rows = connection.execute(
                "SELECT name, kind FROM blackboard_regions "
                "WHERE board_id = %s ORDER BY name",
                (board_id,),
            ).fetchall()
        return [
            Level(str(r[0])) if r[1] == _LEVEL else Premise(str(r[0])) for r in rows
        ]

    def _open_board(self, connection: Any, board_id: str) -> None:
        connection.execute(
            "INSERT INTO blackboard_boards (board_id) VALUES (%s) "
            "ON CONFLICT (board_id) DO NOTHING",
            (board_id,),
        )

    def _take_sequence(self, connection: Any, board_id: str) -> int:
        row = connection.execute(
            "UPDATE blackboard_boards SET next_sequence = next_sequence + 1 "
            "WHERE board_id = %s RETURNING next_sequence - 1",
            (board_id,),
        ).fetchone()
        if row is None:
            # A write can only reach here through a declared region, and
            # declaring one opens the board.
            raise UndeclaredRegionError(
                f"no board is open under the identifier {board_id!r}"
            )
        return int(row[0])

    def _kind_of(self, connection: Any, board_id: str, name: str) -> str | None:
        row = connection.execute(
            "SELECT kind FROM blackboard_regions WHERE board_id = %s AND name = %s",
            (board_id, name),
        ).fetchone()
        return None if row is None else str(row[0])

    def _current_version(self, connection: Any, board_id: str, premise: str) -> int:
        row = connection.execute(
            "SELECT version FROM blackboard_premises WHERE board_id = %s AND name = %s",
            (board_id, premise),
        ).fetchone()
        return 0 if row is None else int(row[0])

    def _require(self, connection: Any, board_id: str, name: str, kind: str) -> None:
        found = self._kind_of(connection, board_id, name)
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

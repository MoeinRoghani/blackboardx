"""A board kept in Postgres, through a connection the application supplies.

The library owns no server, no credentials, and no migration tool. It is
handed a connection pool the application already configures, so pooling,
failover, and secrets stay where an operator manages them.

One database holds many boards, each under its own identifier. Every row
carries it, so a deployment serving many concurrent runs is the ordinary
case.

Two guarantees hold across processes, not merely across the threads of one process:

The sequence is gapless. Every write takes it by incrementing a row of
``blackboard_boards`` under the row lock that the update acquires, and holds
that lock until the transaction commits. Writes to one board are therefore
serialised, and a number that a rolled-back write took is returned rather than skipped.
A Postgres sequence would be faster and would leave gaps, and a
gap is a hole in a record whose numbers are addresses.

A premise write is a conditional update on the version. Two writers
naming the same version produce one winner and one ``Conflict``, whichever
processes reach the row.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
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
from blackboard._run import (
    Acknowledged,
    Closure,
    Dispatched,
    RegisteredAgent,
    UnknownRunError,
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
    """Raised to leave a transaction, because raising is what undoes it."""


class ConnectionPool(Protocol):
    """What this adapter needs of a connection pool.

    ``psycopg_pool.ConnectionPool`` satisfies this protocol. So does anything else that
    hands out a connection for the duration of a ``with`` block and takes it
    back at the end.
    """

    def connection(self, *args: Any, **kwargs: Any) -> AbstractContextManager[Any]:
        """Lends a connection for the duration of a ``with`` block."""
        ...


class PostgresStore:
    """Keeps the board in Postgres. Satisfies ``BoardStore``.

    ``pool`` is the application's own connection pool, and this adapter does not open it
    or close it. Every call names the board within the
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
        instead. This is for the callers that have no pool to pass.
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
                board_id=board_id,
                regions_removed=int(regions[0]),
                writes_removed=int(writes[0]),
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


_RUN_SCHEMA = """
CREATE TABLE IF NOT EXISTS blackboard_runs (
    board_id             TEXT PRIMARY KEY,
    wall_deadline        TIMESTAMPTZ NOT NULL,
    idle_deadline        TIMESTAMPTZ NOT NULL,
    next_notification_id BIGINT      NOT NULL DEFAULT 1,
    dispatched_through   BIGINT      NOT NULL DEFAULT 0,
    outcome              TEXT,
    unfinished           JSONB,
    reason               TEXT        NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS blackboard_run_agents (
    board_id      TEXT   NOT NULL
        REFERENCES blackboard_runs(board_id) ON DELETE CASCADE,
    name          TEXT   NOT NULL,
    subscribes_to JSONB,
    writes_to     JSONB,
    read_through  BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (board_id, name)
);
CREATE TABLE IF NOT EXISTS blackboard_run_notifications (
    board_id        TEXT    NOT NULL
        REFERENCES blackboard_runs(board_id) ON DELETE CASCADE,
    notification_id BIGINT  NOT NULL,
    agent           TEXT    NOT NULL,
    to_sequence     BIGINT  NOT NULL,
    acknowledged    BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (board_id, notification_id)
);
CREATE INDEX IF NOT EXISTS blackboard_run_notifications_unanswered
    ON blackboard_run_notifications (board_id, agent)
    WHERE NOT acknowledged;
CREATE INDEX IF NOT EXISTS blackboard_runs_by_idle
    ON blackboard_runs (idle_deadline) WHERE outcome IS NULL;
CREATE INDEX IF NOT EXISTS blackboard_runs_by_wall
    ON blackboard_runs (wall_deadline) WHERE outcome IS NULL;
"""


def _declared(named: Any) -> Any:
    """A declaration as a column holds it, keeping absent apart from empty.

    Null is what a control component reads as the default: every premise and
    no level, and every level writable. An empty array is an agent nothing
    wakes and an agent that may write nowhere.
    """
    if named is None:
        return None
    return Jsonb(sorted(named))


class PostgresRunStore:
    """Keeps the run in Postgres. Satisfies ``RunStore``.

    ``pool`` is the application's own connection pool, and this adapter does
    not open it or close it. Every call names the board within the database,
    so one pool serves every run an application holds.

    Every method is one transaction, which is what makes two processes serving
    one board safe. Numbering a notification takes the run's row lock, so two
    processes issuing at the same moment take two numbers. Closing is
    conditional on the run still being open, so two callers racing produce one
    winner.

    Requires the ``postgres`` extra::

        pip install 'blackboardx[postgres]'

    Call :meth:`create_schema` once against a database that has none, or run
    the equivalent DDL from whatever migration tool the application uses.
    """

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def create_schema(self) -> None:
        """Creates the tables this adapter reads, if they are not there."""
        with self._pool.connection() as connection:
            connection.execute(_RUN_SCHEMA)

    @contextmanager
    def _open(self, board_id: str) -> Iterator[Any]:
        """One transaction over a run that exists, or refuses naming it."""
        with self._pool.connection() as connection, connection.transaction():
            held = connection.execute(
                "SELECT 1 FROM blackboard_runs WHERE board_id = %s", (board_id,)
            ).fetchone()
            if held is None:
                raise UnknownRunError(f"no run is open on {board_id!r}")
            yield connection

    def open_run(
        self, board_id: str, *, wall_deadline: datetime, idle_deadline: datetime
    ) -> None:
        with self._pool.connection() as connection, connection.transaction():
            # A board that already holds an open run keeps its deadlines, so a
            # second process attaching does not extend the wall clock the
            # first one started.
            connection.execute(
                "INSERT INTO blackboard_runs "
                "(board_id, wall_deadline, idle_deadline) VALUES (%s, %s, %s) "
                "ON CONFLICT (board_id) DO NOTHING",
                (board_id, wall_deadline, idle_deadline),
            )

    def register(
        self,
        board_id: str,
        name: str,
        subscribes_to: Any = None,
        writes_to: Any = None,
    ) -> RegisteredAgent:
        with self._open(board_id) as connection:
            row = connection.execute(
                "INSERT INTO blackboard_run_agents "
                "(board_id, name, subscribes_to, writes_to) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (board_id, name) DO UPDATE SET "
                "subscribes_to = excluded.subscribes_to, "
                "writes_to = excluded.writes_to "
                "RETURNING name, subscribes_to, writes_to, read_through",
                (board_id, name, _declared(subscribes_to), _declared(writes_to)),
            ).fetchone()
            assert row is not None  # the upsert always returns its row
            return _agent_of(row)

    def registered(self, board_id: str) -> list[RegisteredAgent]:
        with self._open(board_id) as connection:
            rows = connection.execute(
                "SELECT name, subscribes_to, writes_to, read_through "
                "FROM blackboard_run_agents WHERE board_id = %s ORDER BY name",
                (board_id,),
            ).fetchall()
        return [_agent_of(row) for row in rows]

    def registration(self, board_id: str, name: str) -> RegisteredAgent | None:
        with self._open(board_id) as connection:
            row = connection.execute(
                "SELECT name, subscribes_to, writes_to, read_through "
                "FROM blackboard_run_agents WHERE board_id = %s AND name = %s",
                (board_id, name),
            ).fetchone()
        return None if row is None else _agent_of(row)

    def issue(self, board_id: str, agent: str, to_sequence: int) -> int:
        with self._open(board_id) as connection:
            # The update takes the run's row lock and holds it to commit, so
            # two processes issuing at once take two numbers rather than one.
            row = connection.execute(
                "UPDATE blackboard_runs SET next_notification_id = "
                "next_notification_id + 1 WHERE board_id = %s "
                "RETURNING next_notification_id - 1",
                (board_id,),
            ).fetchone()
            assert row is not None  # the run exists; _open checked
            notification_id = int(row[0])
            connection.execute(
                "INSERT INTO blackboard_run_notifications "
                "(board_id, notification_id, agent, to_sequence) "
                "VALUES (%s, %s, %s, %s)",
                (board_id, notification_id, agent, to_sequence),
            )
            return notification_id

    def acknowledge(
        self, board_id: str, agent: str, notification_id: int
    ) -> Acknowledged | None:
        with self._open(board_id) as connection:
            named = connection.execute(
                "SELECT to_sequence, acknowledged FROM blackboard_run_notifications "
                "WHERE board_id = %s AND notification_id = %s AND agent = %s "
                "FOR UPDATE",
                (board_id, notification_id, agent),
            ).fetchone()
            if named is None:
                raise UnknownRunError(
                    f"no notification {notification_id} was issued to {agent!r}"
                )
            if named[1]:
                return None
            through = int(named[0])
            # The cursor is cumulative, so acknowledging this range
            # acknowledges every range it already covers.
            covered = connection.execute(
                "UPDATE blackboard_run_notifications SET acknowledged = TRUE "
                "WHERE board_id = %s AND agent = %s AND NOT acknowledged "
                "AND to_sequence <= %s",
                (board_id, agent, through),
            ).rowcount
            moved = connection.execute(
                "UPDATE blackboard_run_agents SET read_through = GREATEST("
                "read_through, %s) WHERE board_id = %s AND name = %s "
                "RETURNING read_through",
                (through, board_id, agent),
            ).fetchone()
            cursor = through if moved is None else int(moved[0])
            return Acknowledged(cursor=cursor, covered=covered)

    def outstanding(self, board_id: str) -> list[Dispatched]:
        with self._open(board_id) as connection:
            rows = connection.execute(
                "SELECT notification_id, agent, to_sequence "
                "FROM blackboard_run_notifications "
                "WHERE board_id = %s AND NOT acknowledged ORDER BY notification_id",
                (board_id,),
            ).fetchall()
        return [
            Dispatched(
                notification_id=int(r[0]), agent=str(r[1]), to_sequence=int(r[2])
            )
            for r in rows
        ]

    def forget(self, board_id: str, agent: str) -> None:
        with self._open(board_id) as connection:
            connection.execute(
                "UPDATE blackboard_run_notifications SET acknowledged = TRUE "
                "WHERE board_id = %s AND agent = %s AND NOT acknowledged",
                (board_id, agent),
            )

    def dispatched_through(self, board_id: str) -> int:
        with self._open(board_id) as connection:
            row = connection.execute(
                "SELECT dispatched_through FROM blackboard_runs WHERE board_id = %s",
                (board_id,),
            ).fetchone()
        return 0 if row is None else int(row[0])

    def note_dispatched(self, board_id: str, sequence: int) -> None:
        with self._open(board_id) as connection:
            # Only forward. What covers the gap after a process stopped
            # compares this to the board's own head, so a lower number
            # arriving late must not undo it.
            connection.execute(
                "UPDATE blackboard_runs SET dispatched_through = GREATEST("
                "dispatched_through, %s) WHERE board_id = %s",
                (sequence, board_id),
            )

    def push_idle(self, board_id: str, until: datetime) -> None:
        with self._open(board_id) as connection:
            connection.execute(
                "UPDATE blackboard_runs SET idle_deadline = %s "
                "WHERE board_id = %s AND outcome IS NULL",
                (until, board_id),
            )

    def deadlines(self, board_id: str) -> tuple[datetime, datetime]:
        with self._open(board_id) as connection:
            row = connection.execute(
                "SELECT wall_deadline, idle_deadline FROM blackboard_runs "
                "WHERE board_id = %s",
                (board_id,),
            ).fetchone()
            assert row is not None  # the run exists; _open checked
            return row[0], row[1]

    def close(
        self,
        board_id: str,
        outcome: str,
        unfinished: Any = (),
        reason: str = "",
    ) -> bool:
        with self._open(board_id) as connection:
            # Conditional on the run still being open, so a local timer and a
            # sweep racing produce one winner and one outcome.
            changed = connection.execute(
                "UPDATE blackboard_runs SET outcome = %s, unfinished = %s, "
                "reason = %s WHERE board_id = %s AND outcome IS NULL",
                (outcome, Jsonb(sorted(unfinished)), reason, board_id),
            ).rowcount
        return bool(changed)

    def closed_as(self, board_id: str) -> Closure | None:
        with self._open(board_id) as connection:
            row = connection.execute(
                "SELECT outcome, unfinished, reason FROM blackboard_runs "
                "WHERE board_id = %s",
                (board_id,),
            ).fetchone()
        if row is None or row[0] is None:
            return None
        held = row[1] if isinstance(row[1], list) else []
        return Closure(
            outcome=str(row[0]),
            unfinished=frozenset(str(name) for name in held),
            reason=str(row[2]),
        )

    def expired(self, now: datetime, limit: int | None = None) -> list[str]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                "SELECT board_id FROM blackboard_runs WHERE outcome IS NULL "
                "AND (wall_deadline <= %s OR idle_deadline <= %s) "
                "ORDER BY idle_deadline LIMIT %s",
                (now, now, limit),
            ).fetchall()
        return [str(r[0]) for r in rows]

    def delete(self, board_id: str) -> None:
        with self._pool.connection() as connection, connection.transaction():
            connection.execute(
                "DELETE FROM blackboard_runs WHERE board_id = %s", (board_id,)
            )


def _agent_of(row: Any) -> RegisteredAgent:
    """One registration as the store returns it."""
    return RegisteredAgent(
        name=str(row[0]),
        subscribes_to=None if row[1] is None else frozenset(row[1]),
        writes_to=None if row[2] is None else frozenset(row[2]),
        cursor=int(row[3]),
    )

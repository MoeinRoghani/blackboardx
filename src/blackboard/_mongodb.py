"""A board kept in MongoDB, through a client the application supplies.

The library owns no cluster, no credentials, and no index migration. It is
handed the database the application already configures, so connection
settings, failover, and secrets stay where an operator manages them.

One database holds many boards, each under its own identifier. Every
document carries it, so a deployment serving many concurrent runs is the
ordinary case.

Two guarantees hold across processes, not merely across the threads of one,
and both span more than one document:

The sequence is gapless. A write takes it by incrementing a counter
document, and a register write that loses its version returns the number it
took rather than skipping it.

A register write is a conditional update on the version. Two writers naming
the same version produce one winner and one ``Conflict``.

Spanning documents on MongoDB means a session transaction, and a session
transaction means a replica set. Production MongoDB is a replica set and
Atlas is always one, so this adapter requires one and says so where it is
absent, rather than running against a standalone server under weaker rules
than the record needs.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, TypeVar

from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.errors import OperationFailure

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
    from pymongo.database import Database

_BOARDS = "blackboard_boards"
_REGIONS = "blackboard_regions"
_CONTRIBUTIONS = "blackboard_contributions"
_REGISTERS = "blackboard_registers"

_LEVEL = "level"
_REGISTER = "register"

_Result = TypeVar("_Result")


class MongoBoard:
    """Keeps the board in MongoDB. Satisfies ``BoardStore``.

    ``database`` is the application's own ``pymongo.database.Database``, and
    this adapter neither opens nor closes the client behind it.
    ``board_id`` names the board within that database; two boards under
    different identifiers share the collections and see none of each
    other's writes.

    Requires the ``mongodb`` extra::

        pip install 'blackboardx[mongodb]'

    Requires a replica set or a sharded cluster, because every write spans
    two documents and is therefore a transaction. A standalone server
    raises on the first write, naming the reason.

    Content is stored as a document rather than as encoded text, so the
    record is queryable in the database that was chosen for querying it.
    It goes through JSON on the way in, so what the board holds is what
    every other implementation holds.

    Call :meth:`create_indexes` once, or create the equivalent indexes from
    whatever migration the application already runs.
    """

    def __init__(self, database: Database[Any], *, board_id: str = "default") -> None:
        self._database = database
        self._board_id = board_id

    @classmethod
    @contextmanager
    def from_uri(
        cls, uri: str, database: str, *, board_id: str = "default", **client_kwargs: Any
    ) -> Iterator[MongoBoard]:
        """Opens a client for the duration of a ``with`` block, for a script or test.

        An application that already runs a client passes its database to the
        constructor instead. This is for the cases that have none to pass.
        """
        client: MongoClient[Any] = MongoClient(uri, **client_kwargs)
        try:
            yield cls(client[database], board_id=board_id)
        finally:
            client.close()

    def create_indexes(self) -> None:
        """Creates the indexes this adapter reads by, if they are not there.

        Creating an index that exists changes nothing, so calling it against
        a database that already has them is not an error.
        """
        self._database[_REGIONS].create_index(
            [("board_id", ASCENDING), ("name", ASCENDING)], unique=True
        )
        self._database[_REGISTERS].create_index(
            [("board_id", ASCENDING), ("name", ASCENDING)], unique=True
        )
        self._database[_CONTRIBUTIONS].create_index(
            [("board_id", ASCENDING), ("sequence", ASCENDING)], unique=True
        )
        self._database[_CONTRIBUTIONS].create_index(
            [("board_id", ASCENDING), ("region", ASCENDING), ("sequence", ASCENDING)]
        )

    def declare(self, region: Level | Register) -> None:
        if not isinstance(region, Level | Register):
            raise TypeError(
                "a region declaration is a Level or a Register, "
                f"not {type(region).__name__}"
            )
        kind = _LEVEL if isinstance(region, Level) else _REGISTER

        def work(session: Any) -> None:
            if self._kind_of(region.name, session) is not None:
                raise DuplicateRegionError(
                    f"a region named {region.name!r} is already declared"
                )
            self._database[_BOARDS].update_one(
                {"_id": self._board_id},
                {"$setOnInsert": {"next_sequence": 1}},
                upsert=True,
                session=session,
            )
            try:
                self._database[_REGIONS].insert_one(
                    {"board_id": self._board_id, "name": region.name, "kind": kind},
                    session=session,
                )
            except Exception as clash:  # pragma: no cover - a race on one name
                if _is_duplicate_key(clash):
                    raise DuplicateRegionError(
                        f"a region named {region.name!r} is already declared"
                    ) from clash
                raise
            if kind == _REGISTER:
                self._database[_REGISTERS].insert_one(
                    {
                        "board_id": self._board_id,
                        "name": region.name,
                        "value": None,
                        "version": 0,
                    },
                    session=session,
                )

        self._in_a_transaction(work)

    def append(self, level: str, content: object) -> int:
        carried = _as_json(content)

        def work(session: Any) -> int:
            self._require(level, _LEVEL, session)
            sequence = self._take_sequence(session)
            self._database[_CONTRIBUTIONS].insert_one(
                {
                    "board_id": self._board_id,
                    "sequence": sequence,
                    "region": level,
                    "content": carried,
                },
                session=session,
            )
            return sequence

        return self._in_a_transaction(work)

    def set(
        self, register: str, value: object, expected_version: int
    ) -> Written | Conflict:
        carried = _as_json(value)
        losses: list[Conflict] = []

        def work(session: Any) -> Written | None:
            losses.clear()
            self._require(register, _REGISTER, session)
            # The version guard comes first, so a write that loses it never
            # touches the counter. That is what keeps a conflict from taking
            # a sequence number, and it keeps three of every four writers in
            # a race off the one document they would all abort against.
            updated = self._database[_REGISTERS].find_one_and_update(
                {
                    "board_id": self._board_id,
                    "name": register,
                    "version": expected_version,
                },
                {"$set": {"value": carried}, "$inc": {"version": 1}},
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if updated is None:
                losses.append(
                    Conflict(current_version=self._current_version(register, session))
                )
                return None
            sequence = self._take_sequence(session)
            self._database[_CONTRIBUTIONS].insert_one(
                {
                    "board_id": self._board_id,
                    "sequence": sequence,
                    "region": register,
                    "content": carried,
                },
                session=session,
            )
            return Written(sequence=sequence, version=int(updated["version"]))

        written = self._in_a_transaction(work)
        return losses[0] if written is None else written

    def read_level(self, level: str, from_sequence: int = 0) -> list[Contribution]:
        self._require(level, _LEVEL, None)
        documents = (
            self._database[_CONTRIBUTIONS]
            .find(
                {
                    "board_id": self._board_id,
                    "region": level,
                    "sequence": {"$gte": from_sequence},
                }
            )
            .sort("sequence", ASCENDING)
        )
        return [
            Contribution(
                sequence=int(document["sequence"]), content=document["content"]
            )
            for document in documents
        ]

    def read_register(self, register: str) -> RegisterState:
        self._require(register, _REGISTER, None)
        document = self._database[_REGISTERS].find_one(
            {"board_id": self._board_id, "name": register}
        )
        if document is None or int(document["version"]) == 0:
            raise UnsetRegisterError(
                f"the register {register!r} has no value until one is written"
            )
        return RegisterState(value=document["value"], version=int(document["version"]))

    def read_board(self, from_sequence: int = 0) -> list[BoardChange]:
        documents = (
            self._database[_CONTRIBUTIONS]
            .find({"board_id": self._board_id, "sequence": {"$gte": from_sequence}})
            .sort("sequence", ASCENDING)
        )
        return [
            BoardChange(
                sequence=int(document["sequence"]),
                region=document["region"],
                content=document["content"],
            )
            for document in documents
        ]

    def _in_a_transaction(self, work: Callable[[Any], _Result]) -> _Result:
        """Runs the work in one transaction, retrying where the server aborts it.

        Two transactions touching one document do not queue on MongoDB: the
        server aborts one and labels the failure transient, and the caller
        is expected to run it again. ``with_transaction`` is the driver's
        loop for that, and it also covers a commit whose outcome is unknown.

        The work is therefore run more than once under contention, so it
        reads everything it decides on inside the transaction rather than
        carrying a value in from outside.
        """
        client = self._database.client
        try:
            with client.start_session() as session:
                return session.with_transaction(work)
        except OperationFailure as failure:
            if _needs_a_replica_set(failure):
                raise NotImplementedError(
                    "MongoBoard needs a replica set or a sharded cluster, because "
                    "every write spans two documents and is therefore a "
                    "transaction. A standalone server cannot run one."
                ) from failure
            raise

    def _take_sequence(self, session: Any) -> int:
        document = self._database[_BOARDS].find_one_and_update(
            {"_id": self._board_id},
            {"$inc": {"next_sequence": 1}},
            return_document=ReturnDocument.BEFORE,
            session=session,
        )
        if document is None:
            # A write can only reach here through a declared region, and
            # declaring one opens the board.
            raise UndeclaredRegionError(
                f"no board is open under the identifier {self._board_id!r}"
            )
        return int(document["next_sequence"])

    def _kind_of(self, name: str, session: Any = None) -> str | None:
        document = self._database[_REGIONS].find_one(
            {"board_id": self._board_id, "name": name}, session=session
        )
        return None if document is None else str(document["kind"])

    def _current_version(self, register: str, session: Any) -> int:
        document = self._database[_REGISTERS].find_one(
            {"board_id": self._board_id, "name": register}, session=session
        )
        return 0 if document is None else int(document["version"])

    def _require(self, name: str, kind: str, session: Any) -> None:
        found = self._kind_of(name, session)
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


def _as_json(content: object) -> Any:
    """Returns the content as JSON carries it, raising when it cannot."""
    return json.loads(json.dumps(content))


def _is_duplicate_key(error: Exception) -> bool:
    return getattr(error, "code", None) == 11000


def _needs_a_replica_set(failure: OperationFailure) -> bool:
    return "Transaction numbers" in str(failure) or "replica set" in str(failure)

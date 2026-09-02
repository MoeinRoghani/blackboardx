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
document, and a premise write that loses its version returns the number it
took rather than skipping it.

A premise write is a conditional update on the version. Two writers naming
the same version produce one winner and one ``Conflict``.

Spanning documents on MongoDB means a session transaction, and a session
transaction means a replica set. Production MongoDB is a replica set and
Atlas is always one, so this adapter requires one and says so where it is
absent, rather than running against a standalone server under weaker rules
than the record needs.
"""

from __future__ import annotations

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
    Premise,
    PremiseState,
    RegionKindError,
    UndeclaredRegionError,
    UnsetPremiseError,
    Written,
    _as_json,
)

if TYPE_CHECKING:
    from pymongo.database import Database

_BOARDS = "blackboard_boards"
_REGIONS = "blackboard_regions"
_CONTRIBUTIONS = "blackboard_contributions"
_PREMISES = "blackboard_premises"

_LEVEL = "level"
_PREMISE = "premise"

_Result = TypeVar("_Result")


class MongoStore:
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

    def __init__(self, database: Database[Any]) -> None:
        self._database = database

    @classmethod
    @contextmanager
    def from_uri(
        cls, uri: str, database: str, **client_kwargs: Any
    ) -> Iterator[MongoStore]:
        """Opens a client for the duration of a ``with`` block, for a script or test.

        An application that already runs a client passes its database to the
        constructor instead. This is for the cases that have none to pass.
        """
        client: MongoClient[Any] = MongoClient(uri, **client_kwargs)
        try:
            yield cls(client[database])
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
        self._database[_PREMISES].create_index(
            [("board_id", ASCENDING), ("name", ASCENDING)], unique=True
        )
        self._database[_CONTRIBUTIONS].create_index(
            [("board_id", ASCENDING), ("sequence", ASCENDING)], unique=True
        )
        self._database[_CONTRIBUTIONS].create_index(
            [("board_id", ASCENDING), ("region", ASCENDING), ("sequence", ASCENDING)]
        )

    def declare(self, board_id: str, region: Level | Premise) -> None:
        if not isinstance(region, Level | Premise):
            raise TypeError(
                "a region declaration is a Level or a Premise, "
                f"not {type(region).__name__}"
            )
        kind = _LEVEL if isinstance(region, Level) else _PREMISE

        def work(session: Any) -> None:
            if self._kind_of(board_id, region.name, session) is not None:
                raise DuplicateRegionError(
                    f"a region named {region.name!r} is already declared"
                )
            self._database[_BOARDS].update_one(
                {"_id": board_id},
                {"$setOnInsert": {"next_sequence": 1}},
                upsert=True,
                session=session,
            )
            try:
                self._database[_REGIONS].insert_one(
                    {"board_id": board_id, "name": region.name, "kind": kind},
                    session=session,
                )
            except Exception as clash:  # pragma: no cover - a race on one name
                if _is_duplicate_key(clash):
                    raise DuplicateRegionError(
                        f"a region named {region.name!r} is already declared"
                    ) from clash
                raise
            if kind == _PREMISE:
                self._database[_PREMISES].insert_one(
                    {
                        "board_id": board_id,
                        "name": region.name,
                        "value": None,
                        "version": 0,
                    },
                    session=session,
                )

        self._in_a_transaction(work)

    def append(self, board_id: str, level: str, content: object) -> int:
        carried = _as_json(content)

        def work(session: Any) -> int:
            self._require(board_id, level, _LEVEL, session)
            sequence = self._take_sequence(session, board_id)
            self._database[_CONTRIBUTIONS].insert_one(
                {
                    "board_id": board_id,
                    "sequence": sequence,
                    "region": level,
                    "content": carried,
                },
                session=session,
            )
            return sequence

        return self._in_a_transaction(work)

    def set(
        self, board_id: str, premise: str, value: object, expected_version: int
    ) -> Written | Conflict:
        carried = _as_json(value)
        losses: list[Conflict] = []

        def work(session: Any) -> Written | None:
            losses.clear()
            self._require(board_id, premise, _PREMISE, session)
            # The version guard comes first, so a write that loses it never
            # touches the counter. That is what keeps a conflict from taking
            # a sequence number, and it keeps three of every four writers in
            # a race off the one document they would all abort against.
            updated = self._database[_PREMISES].find_one_and_update(
                {
                    "board_id": board_id,
                    "name": premise,
                    "version": expected_version,
                },
                {"$set": {"value": carried}, "$inc": {"version": 1}},
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if updated is None:
                losses.append(
                    Conflict(
                        current_version=self._current_version(
                            board_id, premise, session
                        )
                    )
                )
                return None
            sequence = self._take_sequence(session, board_id)
            self._database[_CONTRIBUTIONS].insert_one(
                {
                    "board_id": board_id,
                    "sequence": sequence,
                    "region": premise,
                    "content": carried,
                },
                session=session,
            )
            return Written(sequence=sequence, version=int(updated["version"]))

        written = self._in_a_transaction(work)
        return losses[0] if written is None else written

    def read_level(
        self,
        board_id: str,
        level: str,
        from_sequence: int = 0,
        limit: int | None = None,
    ) -> list[Contribution]:
        self._require(board_id, level, _LEVEL, None)
        documents = (
            self._database[_CONTRIBUTIONS]
            .find(
                {
                    "board_id": board_id,
                    "region": level,
                    "sequence": {"$gte": from_sequence},
                }
            )
            .sort("sequence", ASCENDING)
        )
        documents = _bounded(documents, limit)
        return [
            Contribution(
                sequence=int(document["sequence"]), content=document["content"]
            )
            for document in documents
        ]

    def read_premise(self, board_id: str, premise: str) -> PremiseState:
        self._require(board_id, premise, _PREMISE, None)
        document = self._database[_PREMISES].find_one(
            {"board_id": board_id, "name": premise}
        )
        if document is None or int(document["version"]) == 0:
            raise UnsetPremiseError(
                f"the premise {premise!r} has no value until one is written"
            )
        return PremiseState(value=document["value"], version=int(document["version"]))

    def read_board(
        self, board_id: str, from_sequence: int = 0, limit: int | None = None
    ) -> list[BoardChange]:
        documents = (
            self._database[_CONTRIBUTIONS]
            .find({"board_id": board_id, "sequence": {"$gte": from_sequence}})
            .sort("sequence", ASCENDING)
        )
        documents = _bounded(documents, limit)
        return [
            BoardChange(
                sequence=int(document["sequence"]),
                region=document["region"],
                content=document["content"],
            )
            for document in documents
        ]

    def read_regions(self, board_id: str) -> list[Level | Premise]:
        """Returns the regions declared on one board, with their kinds."""
        documents = (
            self._database[_REGIONS]
            .find({"board_id": board_id})
            .sort("name", ASCENDING)
        )
        return [
            Level(str(d["name"])) if d["kind"] == _LEVEL else Premise(str(d["name"]))
            for d in documents
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
                    "MongoStore needs a replica set or a sharded cluster, because "
                    "every write spans two documents and is therefore a "
                    "transaction. A standalone server cannot run one."
                ) from failure
            raise

    def _take_sequence(self, session: Any, board_id: str) -> int:
        document = self._database[_BOARDS].find_one_and_update(
            {"_id": board_id},
            {"$inc": {"next_sequence": 1}},
            return_document=ReturnDocument.BEFORE,
            session=session,
        )
        if document is None:
            # A write can only reach here through a declared region, and
            # declaring one opens the board.
            raise UndeclaredRegionError(
                f"no board is open under the identifier {board_id!r}"
            )
        return int(document["next_sequence"])

    def _kind_of(self, board_id: str, name: str, session: Any = None) -> str | None:
        document = self._database[_REGIONS].find_one(
            {"board_id": board_id, "name": name}, session=session
        )
        return None if document is None else str(document["kind"])

    def _current_version(self, board_id: str, premise: str, session: Any) -> int:
        document = self._database[_PREMISES].find_one(
            {"board_id": board_id, "name": premise}, session=session
        )
        return 0 if document is None else int(document["version"])

    def _require(self, board_id: str, name: str, kind: str, session: Any) -> None:
        found = self._kind_of(board_id, name, session)
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


def _bounded(cursor: Any, limit: int | None) -> Any:
    """Applies a maximum count, treating zero as none rather than as no bound.

    ``Cursor.limit(0)`` means no limit in MongoDB, which is the opposite of
    what a caller asking for nothing means.
    """
    if limit is None:
        return cursor
    if limit <= 0:
        return []
    return cursor.limit(limit)


def _is_duplicate_key(error: Exception) -> bool:
    return getattr(error, "code", None) == 11000


def _needs_a_replica_set(failure: OperationFailure) -> bool:
    return "Transaction numbers" in str(failure) or "replica set" in str(failure)

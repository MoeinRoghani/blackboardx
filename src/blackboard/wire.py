"""What crosses between a blackboard and an agent.

The bodies, and the operations that carry them. Both halves import this module, so one
half cannot spell a field differently from the other or send to a path the other does
not answer on.

Nothing here depends on a web framework or on an HTTP library. A body is a
frozen dataclass with ``to_json`` and ``from_json``, so a route hands the result of
``to_json`` to its framework and passes what it received to ``from_json``.
``blackboard.server`` answers these operations and ``blackboard.agent`` calls
them; a service that would rather write its own routes still has the bodies.

Decoding is tolerant, because the two halves are deployed separately and are
therefore versioned separately. A field that a decoder does not recognise is ignored, a
field that is absent takes its default, and a name is never reused
for a different meaning. An older agent can therefore read a body from a newer
blackboard, and a newer agent can read a body from an older one.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, ClassVar, TypeVar
from urllib.parse import quote

from blackboard._board import Level, Premise

_T = TypeVar("_T", bound="_Body")


class WireError(Exception):
    """A body could not be decoded because a field it needs is missing."""


@dataclass(frozen=True)
class _Body:
    """A body that crosses the wire.

    ``to_json`` returns plain JSON types. ``from_json`` ignores what it does
    not know and fills in what it was not given, which is what lets the two
    halves ship on their own schedules.
    """

    #: Fields that carry no default and must be present to decode.
    _required: ClassVar[tuple[str, ...]] = ()

    def to_json(self) -> dict[str, Any]:
        """Returns this body as plain JSON types."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_json(cls: type[_T], body: object) -> _T:
        """Builds this body from what arrived, ignoring what it does not know."""
        if not isinstance(body, dict):
            raise WireError(f"a body is an object, not {type(body).__name__}")
        known = {f.name for f in fields(cls)}
        missing = [n for n in cls._required if n not in body]
        if missing:
            raise WireError(
                f"{cls.__name__} needs " + ", ".join(sorted(repr(n) for n in missing))
            )
        return cls(**{k: v for k, v in body.items() if k in known})


# What a blackboard sends to an agent


@dataclass(frozen=True)
class NotificationBody(_Body):
    """One notification, as it reaches an agent.

    It carries no values. The agent reads the board to find out what changed,
    which is why a repeat costs nothing and why order does not matter.

    Both bounds are required. A body that lost ``from_sequence`` would decode
    as zero, which is a range the control component never issues and which
    tells an agent to read the whole level. ``regions`` stays optional,
    because an agent may read past what a notification names.
    """

    _required: ClassVar[tuple[str, ...]] = (
        "board_id",
        "notification_id",
        "agent",
        "from_sequence",
        "to_sequence",
    )

    board_id: str
    notification_id: int
    agent: str
    from_sequence: int = 0
    to_sequence: int = 0
    regions: list[str] = field(default_factory=list)


# What an agent sends to a blackboard


@dataclass(frozen=True)
class WriteRequest(_Body):
    """A contribution proposed to a level.

    ``content`` is required, because a body that carries none would otherwise
    store ``null``. ``level`` has a default, because the path already names
    the region and the service takes it from there; a service that mounts one
    route of its own can still send it.
    """

    _required: ClassVar[tuple[str, ...]] = ("writer", "content")

    writer: str
    content: object = None
    level: str = ""
    idempotency_key: str | None = None


@dataclass(frozen=True)
class SetPremiseRequest(_Body):
    """A premise value proposed under the version it expects to replace."""

    _required: ClassVar[tuple[str, ...]] = (
        "writer",
        "value",
        "expected_version",
    )

    writer: str
    expected_version: int
    value: object = None
    premise: str = ""
    idempotency_key: str | None = None


@dataclass(frozen=True)
class AckRequest(_Body):
    """An agent reporting that it has stopped working on one notification."""

    _required: ClassVar[tuple[str, ...]] = ("agent", "notification_id")

    agent: str
    notification_id: int


# What a blackboard sends back


@dataclass(frozen=True)
class WrittenBody(_Body):
    """A write that reached the board, at the sequence it received.

    ``version`` is absent on a level write, because a level holds none.
    """

    _required: ClassVar[tuple[str, ...]] = ("sequence",)

    sequence: int
    version: int | None = None
    repeated: bool = False


@dataclass(frozen=True)
class ConflictBody(_Body):
    """A premise write that named a version other than the current one."""

    _required: ClassVar[tuple[str, ...]] = ("current_version",)

    current_version: int


@dataclass(frozen=True)
class RejectedBody(_Body):
    """A write the control component refused, with the cause."""

    _required: ClassVar[tuple[str, ...]] = ("cause", "reason")

    cause: str
    reason: str


@dataclass(frozen=True)
class ContributionBody(_Body):
    """One contribution read back from a level.

    ``writer`` and ``written_at`` are optional, so an older blackboard that
    sends neither decodes as ``None`` for both and a newer agent reads it
    without error. ``written_at`` crosses as an ISO-8601 string.
    """

    _required: ClassVar[tuple[str, ...]] = ("sequence",)

    sequence: int
    content: object = None
    writer: str | None = None
    written_at: str | None = None


@dataclass(frozen=True)
class BoardChangeBody(_Body):
    """One write to any region, read back from the whole board.

    ``writer`` and ``written_at`` follow the rule on :class:`ContributionBody`.
    """

    _required: ClassVar[tuple[str, ...]] = ("sequence", "region")

    sequence: int
    region: str
    content: object = None
    writer: str | None = None
    written_at: str | None = None


@dataclass(frozen=True)
class LevelPage(_Body):
    """Part of a level. ``has_more`` is set when the level holds more beyond it.

    A reader continues from one past the last sequence it received. That is
    the cursor: an offset would shift when a concurrent write lands, and a
    sequence number does not.
    """

    contributions: list[ContributionBody] = field(default_factory=list)
    has_more: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "contributions": [c.to_json() for c in self.contributions],
            "has_more": self.has_more,
        }

    @classmethod
    def from_json(cls, body: object) -> LevelPage:
        if not isinstance(body, dict):
            raise WireError(f"a body is an object, not {type(body).__name__}")
        raw = body.get("contributions") or []
        return cls(
            contributions=[ContributionBody.from_json(c) for c in raw],
            has_more=bool(body.get("has_more", False)),
        )


@dataclass(frozen=True)
class BoardPage(_Body):
    """Part of the whole board. ``has_more`` is set when the board holds more beyond
    it."""

    changes: list[BoardChangeBody] = field(default_factory=list)
    has_more: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "changes": [c.to_json() for c in self.changes],
            "has_more": self.has_more,
        }

    @classmethod
    def from_json(cls, body: object) -> BoardPage:
        if not isinstance(body, dict):
            raise WireError(f"a body is an object, not {type(body).__name__}")
        raw = body.get("changes") or []
        return cls(
            changes=[BoardChangeBody.from_json(c) for c in raw],
            has_more=bool(body.get("has_more", False)),
        )


@dataclass(frozen=True)
class PremiseBody(_Body):
    """A premise's current value and the version that produced it.

    ``writer`` and ``written_at`` follow the rule on :class:`ContributionBody`.
    """

    _required: ClassVar[tuple[str, ...]] = ("version",)

    version: int
    value: object = None
    writer: str | None = None
    written_at: str | None = None


@dataclass(frozen=True)
class RegionBody(_Body):
    """One declared region: its name and its kind.

    A store records a name and a kind and nothing else, so a premise comes
    back without the batch window it was declared with. The window tells the
    control component when to notify and is no part of the record.
    """

    _required: ClassVar[tuple[str, ...]] = ("name", "kind")

    name: str
    kind: str

    @classmethod
    def of(cls, region: Level | Premise) -> RegionBody:
        """Builds a body from a declaration."""
        return cls(
            name=region.name, kind="level" if isinstance(region, Level) else "premise"
        )

    def declaration(self) -> Level | Premise:
        """Builds a declaration from a body."""
        return Level(self.name) if self.kind == "level" else Premise(self.name)


@dataclass(frozen=True)
class RegionList(_Body):
    """Every region declared on one board."""

    regions: list[RegionBody] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"regions": [r.to_json() for r in self.regions]}

    @classmethod
    def from_json(cls, body: object) -> RegionList:
        if not isinstance(body, dict):
            raise WireError(f"a body is an object, not {type(body).__name__}")
        raw = body.get("regions") or []
        return cls(regions=[RegionBody.from_json(r) for r in raw])


# What goes wrong


@dataclass(frozen=True)
class ErrorBody(_Body):
    """What a blackboard answers with when it cannot do what was asked.

    ``error`` is a stable name a client can branch on. ``detail`` is written
    for a person reading a log and may change between versions.
    """

    _required: ClassVar[tuple[str, ...]] = ("error",)

    error: str
    detail: str = ""


# The operations, and the paths they take


@dataclass(frozen=True)
class Operation:
    """One thing an agent can ask a blackboard to do.

    ``template`` names its variables in braces. Both halves build a path from
    the same object, one to send and one to match, so a rename reaches the
    two of them together.
    """

    name: str
    method: str
    template: str

    def path(self, **variables: str) -> str:
        """Returns the path for these variables, each percent-encoded."""
        filled = self.template
        for name, value in variables.items():
            marker = "{" + name + "}"
            if marker not in filled:
                raise WireError(f"{self.name} takes no {name!r}")
            filled = filled.replace(marker, quote(str(value), safe=""))
        if "{" in filled:
            missing = filled[filled.index("{") + 1 : filled.index("}")]
            raise WireError(f"{self.name} needs {missing!r}")
        return filled

    @property
    def variables(self) -> tuple[str, ...]:
        """Returns the names this operation's template takes, in order."""
        return tuple(
            segment[1:-1]
            for segment in self.template.split("/")
            if segment.startswith("{")
        )


#: Every region declared on a board, with its kind. Answers a `RegionList`.
READ_REGIONS = Operation("read_regions", "GET", "/boards/{board_id}/regions")

#: One level, from a sequence bound. Answers a `LevelPage`.
READ_LEVEL = Operation("read_level", "GET", "/boards/{board_id}/levels/{level}")

#: One premise's current value and version. Answers a `PremiseBody`.
READ_PREMISE = Operation("read_premise", "GET", "/boards/{board_id}/premises/{premise}")

#: Every write to every region, in order, from a sequence bound. Answers a
#: `BoardPage`.
READ_BOARD = Operation("read_board", "GET", "/boards/{board_id}/changes")

#: Appends to a level. Takes a `WriteRequest`, answers a `WrittenBody`.
WRITE = Operation("write", "POST", "/boards/{board_id}/levels/{level}")

#: Sets a premise under an expected version. Takes a `SetPremiseRequest`,
#: answers a `WrittenBody` or a `ConflictBody`.
SET_PREMISE = Operation("set_premise", "PUT", "/boards/{board_id}/premises/{premise}")

#: Records that an agent finished with a notification. Takes an `AckRequest`.
ACK = Operation("ack", "POST", "/boards/{board_id}/acknowledgements")

#: Every operation, in the order the documentation lists them.
OPERATIONS: tuple[Operation, ...] = (
    READ_REGIONS,
    READ_LEVEL,
    READ_PREMISE,
    READ_BOARD,
    WRITE,
    SET_PREMISE,
    ACK,
)

#: The query parameters a bounded read takes.
FROM_SEQUENCE = "from_sequence"
LIMIT = "limit"

#: How many rows a read over HTTP answers with when it names no ``limit``.
#:
#: A read in process takes ``limit=None`` and means it. A read over HTTP is a
#: page, so it has a size with or without one from the caller.
DEFAULT_LIMIT = 100

#: The most rows a read over HTTP answers with, whatever ``limit`` asks for.
#:
#: The cap is silent. ``has_more`` and the sequence cursor carry a reader past
#: it, which is what they are for.
MAX_LIMIT = 1000

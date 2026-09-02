"""The bodies that cross between a blackboard and an agent.

Both halves import these, so neither can spell a field differently from the
other. Each team writes its own HTTP routes; this module says only what
travels through them.

Nothing here depends on a web framework or on an HTTP library. A body is a
frozen dataclass with ``to_json`` and ``from_json``, so a route hands the
result of one to its framework and passes what it received to the other.

Decoding is tolerant, because the two halves are deployed separately and are
therefore versioned separately. A field a decoder does not recognise is
ignored, a field that is absent takes its default, and a name is never reused
for a different meaning. An older agent can therefore read a body from a newer
blackboard, and a newer agent can read a body from an older one.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, ClassVar, TypeVar

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
    """

    _required: ClassVar[tuple[str, ...]] = (
        "board_id",
        "notification_id",
        "agent",
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
    """A contribution proposed to a level."""

    _required: ClassVar[tuple[str, ...]] = ("writer", "level")

    writer: str
    level: str
    content: object = None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class SetPremiseRequest(_Body):
    """A premise value proposed under the version it expects to replace."""

    _required: ClassVar[tuple[str, ...]] = ("writer", "premise", "expected_version")

    writer: str
    premise: str
    expected_version: int
    value: object = None
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
    """One contribution read back from a level."""

    _required: ClassVar[tuple[str, ...]] = ("sequence",)

    sequence: int
    content: object = None


@dataclass(frozen=True)
class BoardChangeBody(_Body):
    """One write to any region, read back from the whole board."""

    _required: ClassVar[tuple[str, ...]] = ("sequence", "region")

    sequence: int
    region: str
    content: object = None


@dataclass(frozen=True)
class LevelPage(_Body):
    """Part of a level, and whether the reader has reached the end of it.

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
    """Part of the whole board, and whether the reader has reached its end."""

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
    """A premise's current value and the version that produced it."""

    _required: ClassVar[tuple[str, ...]] = ("version",)

    version: int
    value: object = None


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

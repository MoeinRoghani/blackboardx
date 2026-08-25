"""The board stores contributions and decides nothing about them.

Writes go to declared regions of two kinds. A level accumulates contributions
in arrival order. A premise holds one current value under a version number,
and a write naming a version other than the current one fails. One counter
orders every write across all regions, and reads are open to any caller.
"""

from __future__ import annotations

import json
import threading
import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

_ZERO_WINDOW = timedelta(0)


def _as_json(content: object) -> Any:
    """Returns the content as JSON carries it, raising when it cannot.

    Every board in the package carries content this way, because a deployed
    board crosses a process boundary. The boards that encode on the way to
    a database get the same result from their own ``json.dumps``.
    """
    return json.loads(json.dumps(content))


class BlackboardError(Exception):
    """The base of every error this library raises."""


class UndeclaredRegionError(BlackboardError):
    """An operation named a region that no declaration created."""


class DuplicateRegionError(BlackboardError):
    """A declaration named a region that already exists."""


class RegionKindError(BlackboardError):
    """An operation that takes a level named a premise, or the reverse."""


class UnsetPremiseError(BlackboardError):
    """A premise was read before any write gave it a value."""


@dataclass(frozen=True)
class Level:
    """A declaration of a region that accumulates contributions in arrival order."""

    name: str


@dataclass(frozen=True)
class Premise:
    """A declaration of a region that holds one current value under a version.

    The control component reads the batch window when a change to this
    premise lands, to schedule its notification. The board ignores it.
    """

    name: str
    batch_window: timedelta = _ZERO_WINDOW

    def __post_init__(self) -> None:
        if self.batch_window < _ZERO_WINDOW:
            raise ValueError("a batch window is a non-negative duration")


@dataclass(frozen=True)
class Written:
    """A write the board sequenced, at the sequence number it received.

    ``version`` is the premise's new revision count. A level write leaves
    it ``None``, because a level holds no version to replace.
    """

    sequence: int
    version: int | None = None


@dataclass(frozen=True)
class Conflict:
    """A premise write that failed because the version it named is not current."""

    current_version: int


@dataclass(frozen=True)
class Contribution:
    """One unit stored in a level, at its position in the total order."""

    sequence: int
    content: object


@dataclass(frozen=True)
class PremiseState:
    """The current value of a premise and the version its write produced."""

    value: object
    version: int


@dataclass(frozen=True)
class BoardChange:
    """One write to any region, at its position in the total order."""

    sequence: int
    region: str
    content: object


class InMemoryBoard:
    """A board held in process memory. A test double, not a way to run anything.

    Nothing it holds outlives the process, and two processes running the same
    code share nothing. An application keeps its record in a database through
    an adapter; a test that wants no file uses this.

    Content is carried as JSON, as it is in every implementation that
    crosses a process boundary. Holding a Python object as it stands would
    make this board accept what a deployment then refuses, and preserve
    types, such as a tuple, that no other implementation returns. Content
    that JSON cannot carry raises ``TypeError`` before anything is stored.

    Sequence assignment is the only point where two writes wait on each
    other, so writes from concurrent threads all succeed with distinct
    sequence numbers.
    """

    def __init__(self, regions: Iterable[Level | Premise] = ()) -> None:
        self._lock = threading.Lock()
        self._sequence = 0
        self._levels: dict[str, list[Contribution]] = {}
        self._premises: dict[str, PremiseState | None] = {}
        self._changes: list[BoardChange] = []
        for region in regions:
            self.declare(region)

    def declare(self, region: Level | Premise) -> None:
        """Creates a region. A name already declared, of either kind, is refused."""
        if not isinstance(region, Level | Premise):
            raise TypeError(
                "a region declaration is a Level or a Premise, "
                f"not {type(region).__name__}"
            )
        with self._lock:
            if region.name in self._levels or region.name in self._premises:
                raise DuplicateRegionError(
                    f"a region named {region.name!r} is already declared"
                )
            if isinstance(region, Level):
                self._levels[region.name] = []
            else:
                self._premises[region.name] = None

    def append(self, level: str, content: object) -> int:
        """Adds one contribution to a level and returns its sequence number."""
        with self._lock:
            contributions = self._level_contributions(level)
            carried = _as_json(content)
            self._sequence += 1
            contributions.append(Contribution(sequence=self._sequence, content=carried))
            self._changes.append(
                BoardChange(sequence=self._sequence, region=level, content=carried)
            )
            return self._sequence

    def set(
        self, premise: str, value: object, expected_version: int
    ) -> Written | Conflict:
        """Replaces a premise's value under the version the caller expects.

        A write naming any version other than the premise's current one
        fails: it returns a conflict carrying the current version, takes no
        sequence number, and leaves the premise unchanged. A premise never
        written has version 0.
        """
        with self._lock:
            state = self._premise_state(premise)
            current_version = 0 if state is None else state.version
            carried = _as_json(value)
            if expected_version != current_version:
                return Conflict(current_version=current_version)
            self._sequence += 1
            self._premises[premise] = PremiseState(
                value=carried, version=current_version + 1
            )
            self._changes.append(
                BoardChange(sequence=self._sequence, region=premise, content=carried)
            )
            return Written(sequence=self._sequence, version=current_version + 1)

    def read_level(self, level: str, from_sequence: int = 0) -> list[Contribution]:
        """Returns a level's contributions from the sequence bound, inclusive."""
        with self._lock:
            contributions = self._level_contributions(level)
            return [c for c in contributions if c.sequence >= from_sequence]

    def read_premise(self, premise: str) -> PremiseState:
        """Returns a premise's current value and version."""
        with self._lock:
            state = self._premise_state(premise)
            if state is None:
                raise UnsetPremiseError(
                    f"the premise {premise!r} has no value until one is written"
                )
            return state

    def read_register(self, register: str) -> PremiseState:
        """Deprecated since 0.5.0. Use ``read_premise``; removed in 0.6.0."""
        warnings.warn(
            "read_register is renamed read_premise, and the old name is "
            "removed in 0.6.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.read_premise(register)

    def read_board(self, from_sequence: int = 0) -> list[BoardChange]:
        """Returns every write to every region, in sequence order, from the bound."""
        with self._lock:
            return [
                change for change in self._changes if change.sequence >= from_sequence
            ]

    def _level_contributions(self, name: str) -> list[Contribution]:
        # Callers hold self._lock.
        if name in self._levels:
            return self._levels[name]
        if name in self._premises:
            raise RegionKindError(
                f"{name!r} names a premise, and this operation takes a level"
            )
        raise UndeclaredRegionError(f"no region is declared with the name {name!r}")

    def _premise_state(self, name: str) -> PremiseState | None:
        # Callers hold self._lock.
        if name in self._premises:
            return self._premises[name]
        if name in self._levels:
            raise RegionKindError(
                f"{name!r} names a level, and this operation takes a premise"
            )
        raise UndeclaredRegionError(f"no region is declared with the name {name!r}")

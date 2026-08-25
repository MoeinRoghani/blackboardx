"""The board stores contributions and decides nothing about them.

Writes go to declared regions of two kinds. A level accumulates contributions
in arrival order. A register holds one current value under a version number,
and a write naming a version other than the current one fails. One counter
orders every write across all regions, and reads are open to any caller.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta

_ZERO_WINDOW = timedelta(0)


def _as_json(content: object) -> object:
    """Returns the content as JSON carries it, raising when it cannot."""
    return json.loads(json.dumps(content))


class BlackboardError(Exception):
    """The base of every error this library raises."""


class UndeclaredRegionError(BlackboardError):
    """An operation named a region that no declaration created."""


class DuplicateRegionError(BlackboardError):
    """A declaration named a region that already exists."""


class RegionKindError(BlackboardError):
    """An operation that takes a level named a register, or the reverse."""


class UnsetRegisterError(BlackboardError):
    """A register was read before any write gave it a value."""


@dataclass(frozen=True)
class Level:
    """A declaration of a region that accumulates contributions in arrival order."""

    name: str


@dataclass(frozen=True)
class Register:
    """A declaration of a region that holds one current value under a version.

    The control component reads the batch window when a change to this
    register lands, to schedule its notification. The board ignores it.
    """

    name: str
    batch_window: timedelta = _ZERO_WINDOW

    def __post_init__(self) -> None:
        if self.batch_window < _ZERO_WINDOW:
            raise ValueError("a batch window is a non-negative duration")


@dataclass(frozen=True)
class Written:
    """A register write the board sequenced, and the version it produced."""

    sequence: int
    version: int


@dataclass(frozen=True)
class Conflict:
    """A register write that failed because the version it named is not current."""

    current_version: int


@dataclass(frozen=True)
class Contribution:
    """One unit stored in a level, at its position in the total order."""

    sequence: int
    content: object


@dataclass(frozen=True)
class RegisterState:
    """The current value of a register and the version its write produced."""

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

    def __init__(self, regions: Iterable[Level | Register] = ()) -> None:
        self._lock = threading.Lock()
        self._sequence = 0
        self._levels: dict[str, list[Contribution]] = {}
        self._registers: dict[str, RegisterState | None] = {}
        self._changes: list[BoardChange] = []
        for region in regions:
            self.declare(region)

    def declare(self, region: Level | Register) -> None:
        """Creates a region. A name already declared, of either kind, is refused."""
        if not isinstance(region, Level | Register):
            raise TypeError(
                "a region declaration is a Level or a Register, "
                f"not {type(region).__name__}"
            )
        with self._lock:
            if region.name in self._levels or region.name in self._registers:
                raise DuplicateRegionError(
                    f"a region named {region.name!r} is already declared"
                )
            if isinstance(region, Level):
                self._levels[region.name] = []
            else:
                self._registers[region.name] = None

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
        self, register: str, value: object, expected_version: int
    ) -> Written | Conflict:
        """Replaces a register's value under the version the caller expects.

        A write naming any version other than the register's current one
        fails: it returns a conflict carrying the current version, takes no
        sequence number, and leaves the register unchanged. A register never
        written has version 0.
        """
        with self._lock:
            state = self._register_state(register)
            current_version = 0 if state is None else state.version
            carried = _as_json(value)
            if expected_version != current_version:
                return Conflict(current_version=current_version)
            self._sequence += 1
            self._registers[register] = RegisterState(
                value=carried, version=current_version + 1
            )
            self._changes.append(
                BoardChange(sequence=self._sequence, region=register, content=carried)
            )
            return Written(sequence=self._sequence, version=current_version + 1)

    def read_level(self, level: str, from_sequence: int = 0) -> list[Contribution]:
        """Returns a level's contributions from the sequence bound, inclusive."""
        with self._lock:
            contributions = self._level_contributions(level)
            return [c for c in contributions if c.sequence >= from_sequence]

    def read_register(self, register: str) -> RegisterState:
        """Returns a register's current value and version."""
        with self._lock:
            state = self._register_state(register)
            if state is None:
                raise UnsetRegisterError(
                    f"the register {register!r} has no value until one is written"
                )
            return state

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
        if name in self._registers:
            raise RegionKindError(
                f"{name!r} names a register, and this operation takes a level"
            )
        raise UndeclaredRegionError(f"no region is declared with the name {name!r}")

    def _register_state(self, name: str) -> RegisterState | None:
        # Callers hold self._lock.
        if name in self._registers:
            return self._registers[name]
        if name in self._levels:
            raise RegionKindError(
                f"{name!r} names a level, and this operation takes a register"
            )
        raise UndeclaredRegionError(f"no region is declared with the name {name!r}")

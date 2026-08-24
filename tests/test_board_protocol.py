"""The control component drives any board that satisfies the storage protocol."""

from datetime import UTC, datetime, timedelta

from blackboard import (
    Accepted,
    Board,
    BoardChange,
    BoardReader,
    BoardStore,
    Conflict,
    Contribution,
    Level,
    ManualClock,
    Register,
    RegisterState,
    RunBudgets,
    TerminationDecision,
    Written,
    create_model,
)

START = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
BUDGETS = RunBudgets(
    wall_clock=timedelta(hours=1), total_writes=100, total_notifications=100
)


def keep_open(reader: BoardReader) -> TerminationDecision:
    return TerminationDecision.CONTINUE


class RecordingBoard:
    """A board that satisfies the protocol and records what it was asked to do."""

    def __init__(self) -> None:
        self._inner = Board()
        self.calls: list[str] = []

    def declare(self, region: Level | Register) -> None:
        self.calls.append(f"declare:{region.name}")
        self._inner.declare(region)

    def append(self, level: str, content: object) -> int:
        self.calls.append(f"append:{level}")
        return self._inner.append(level, content)

    def set(
        self, register: str, value: object, expected_version: int
    ) -> Written | Conflict:
        self.calls.append(f"set:{register}")
        return self._inner.set(register, value, expected_version)

    def read_level(self, level: str, from_sequence: int = 0) -> list[Contribution]:
        return self._inner.read_level(level, from_sequence)

    def read_register(self, register: str) -> RegisterState:
        return self._inner.read_register(register)

    def read_board(self, from_sequence: int = 0) -> list[BoardChange]:
        return self._inner.read_board(from_sequence)


def test_a_substitute_board_satisfies_the_protocol() -> None:
    store: BoardStore = RecordingBoard()
    assert isinstance(store.read_board(), list)


def test_the_control_component_drives_the_supplied_board() -> None:
    store = RecordingBoard()
    model = create_model(
        regions=[Level("platform"), Register("window")],
        seed={"window": "w"},
        budgets=BUDGETS,
        termination_predicate=keep_open,
        board=store,
        clock=ManualClock(start=START),
    )
    assert model.control.write("ocp", "platform", "finding") == Accepted(sequence=2)
    assert store.calls == [
        "declare:platform",
        "declare:window",
        "set:window",
        "append:platform",
    ]


def test_without_one_the_in_memory_board_is_used() -> None:
    model = create_model(
        regions=[Level("platform")],
        seed={},
        budgets=BUDGETS,
        termination_predicate=keep_open,
        clock=ManualClock(start=START),
    )
    assert model.control.write("ocp", "platform", "finding") == Accepted(sequence=1)
    assert [c.content for c in model.reader.read_level("platform")] == ["finding"]

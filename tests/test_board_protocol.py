"""The control component drives any board that satisfies the storage protocol."""

from datetime import UTC, datetime, timedelta

from blackboard import (
    BoardChange,
    BoardReader,
    BoardStore,
    Conflict,
    Contribution,
    Deleted,
    InMemoryStore,
    Level,
    ManualClock,
    Premise,
    PremiseState,
    RunLimits,
    RunRecord,
    TerminationDecision,
    Written,
    create_model,
)

START = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
LIMITS = RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(minutes=30))


def keep_open(reader: BoardReader) -> TerminationDecision:
    return TerminationDecision.CONTINUE


class RecordingBoard:
    """A board that satisfies the protocol and records what it was asked to do."""

    def __init__(self) -> None:
        self._inner = InMemoryStore()
        self.calls: list[str] = []

    def declare(self, board_id: str, region: Level | Premise) -> None:
        self.calls.append(f"declare:{region.name}")
        self._inner.declare(board_id, region)

    def append(
        self,
        board_id: str,
        level: str,
        content: object,
        idempotency_key: str | None = None,
        writer: str | None = None,
    ) -> Written:
        self.calls.append(f"append:{level}")
        return self._inner.append(
            board_id, level, content, idempotency_key, writer=writer
        )

    def set(
        self,
        board_id: str,
        premise: str,
        value: object,
        expected_version: int,
        idempotency_key: str | None = None,
        writer: str | None = None,
    ) -> Written | Conflict:
        self.calls.append(f"set:{premise}")
        return self._inner.set(
            board_id, premise, value, expected_version, idempotency_key, writer=writer
        )

    def read_level(
        self,
        board_id: str,
        level: str,
        from_sequence: int = 0,
        limit: int | None = None,
    ) -> list[Contribution]:
        return self._inner.read_level(board_id, level, from_sequence, limit)

    def open_run(self, board_id: str, *, wall_clock: float, idle: float) -> None:
        self.calls.append("open_run")
        self._inner.open_run(board_id, wall_clock=wall_clock, idle=idle)

    def read_run(self, board_id: str) -> RunRecord | None:
        return self._inner.read_run(board_id)

    def touch_run(self, board_id: str, *, idle: float) -> None:
        self._inner.touch_run(board_id, idle=idle)

    def close_run(
        self,
        board_id: str,
        *,
        closed_as: str,
        reason: str | None = None,
        unfinished: frozenset[str] = frozenset(),
    ) -> bool:
        self.calls.append("close_run")
        return self._inner.close_run(
            board_id, closed_as=closed_as, reason=reason, unfinished=unfinished
        )

    def runs_past_deadline(self, limit: int = 100) -> list[str]:
        return self._inner.runs_past_deadline(limit)

    def read_regions(self, board_id: str) -> list[Level | Premise]:
        self.calls.append("read_regions")
        return self._inner.read_regions(board_id)

    def delete(self, board_id: str) -> Deleted:
        self.calls.append("delete")
        return self._inner.delete(board_id)

    def read_premise(self, board_id: str, premise: str) -> PremiseState:
        return self._inner.read_premise(board_id, premise)

    def read_board(
        self, board_id: str, from_sequence: int = 0, limit: int | None = None
    ) -> list[BoardChange]:
        return self._inner.read_board(board_id, from_sequence, limit)


def test_a_substitute_board_satisfies_the_protocol() -> None:
    store: BoardStore = RecordingBoard()
    assert isinstance(store.read_board("test-board"), list)


def test_the_control_component_drives_the_supplied_board() -> None:
    store = RecordingBoard()
    model = create_model(
        regions=[Level("platform"), Premise("window")],
        premises={"window": "w"},
        limits=LIMITS,
        termination_predicate=keep_open,
        board_id="test-board",
        store=store,
        clock=ManualClock(start=START),
    )
    assert model.control.write("platform", "finding", writer="ocp") == Written(
        sequence=2
    )
    assert store.calls == [
        "declare:platform",
        "declare:window",
        "set:window",
        "append:platform",
    ]


def test_without_one_the_in_memory_board_is_used() -> None:
    model = create_model(
        regions=[Level("platform")],
        premises={},
        limits=LIMITS,
        termination_predicate=keep_open,
        clock=ManualClock(start=START),
        board_id="test-board",
        store=InMemoryStore(),
    )
    assert model.control.write("platform", "finding", writer="ocp") == Written(
        sequence=1
    )
    assert [c.content for c in model.reader.read_level("platform")] == ["finding"]


def test_the_protocol_is_thirteen_methods() -> None:
    """The documentation counts them. A method added here updates that count.

    `docs/concepts/storage.md`, `docs/glossary.md` and `docs/concepts/service.md`
    each say how many `BoardStore` has, and a reader who counts wrong writes an
    adapter that is missing one.
    """
    named = {name for name in dir(BoardStore) if not name.startswith("_")}
    assert named == {
        "declare",
        "append",
        "set",
        "read_level",
        "read_premise",
        "read_board",
        "read_regions",
        "delete",
        "open_run",
        "read_run",
        "touch_run",
        "close_run",
        "runs_past_deadline",
    }

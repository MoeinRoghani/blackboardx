"""A write made through the control component passes admission before sequencing."""

import threading
from datetime import UTC, datetime, timedelta

import pytest

from blackboard import (
    Accept,
    AdmissionRule,
    BoardReader,
    Conflict,
    InMemoryBoard,
    Level,
    ManualClock,
    Premise,
    ProposedContribution,
    ProposedPremiseWrite,
    ProposedWrite,
    RegionKindError,
    Reject,
    Rejected,
    RejectionCause,
    RunLimits,
    TerminationDecision,
    WriteAccepted,
    WriteRejected,
    Written,
)
from blackboard._control import Control

START = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)

LIMITS = RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(minutes=30))


def keep_open(reader: BoardReader) -> TerminationDecision:
    return TerminationDecision.CONTINUE


def make_control(rule: AdmissionRule | None = None) -> Control:
    return Control(
        regions=[Level("application"), Premise("window")],
        admission_rule=rule,
        termination_predicate=keep_open,
        limits=LIMITS,
        clock=ManualClock(start=START),
        board=InMemoryBoard(),
    )


class TestAdmission:
    def test_a_rule_rejected_write_reaches_no_region_and_is_audited(self) -> None:
        def rule(proposed: ProposedWrite, reader: BoardReader) -> Accept | Reject:
            return Reject("a duplicate of a contribution already on the board")

        control = make_control(rule)
        result = control.write("dynatrace", "application", {"finding": "f1"})
        assert result == Rejected(
            cause=RejectionCause.ADMISSION,
            reason="a duplicate of a contribution already on the board",
        )
        assert control.reader.read_level("application") == []
        assert control.read_audit() == [
            WriteRejected(
                at=START,
                writer="dynatrace",
                region="application",
                cause=RejectionCause.ADMISSION,
                reason="a duplicate of a contribution already on the board",
            )
        ]

    def test_an_accepted_write_is_sequenced_and_audited(self) -> None:
        control = make_control()
        result = control.write("dynatrace", "application", {"finding": "f1"})
        assert result == Written(sequence=1)
        (contribution,) = control.reader.read_level("application")
        assert contribution.content == {"finding": "f1"}
        assert control.read_audit() == [
            WriteAccepted(
                at=START, writer="dynatrace", region="application", sequence=1
            )
        ]

    def test_no_admission_rule_accepts_every_write(self) -> None:
        control = make_control()
        assert control.write("a", "application", "one") == Written(sequence=1)
        assert isinstance(
            control.set_premise("operator", "window", "w", expected_version=0),
            Written,
        )

    def test_the_rule_reads_the_board(self) -> None:
        def rule(proposed: ProposedWrite, reader: BoardReader) -> Accept | Reject:
            if isinstance(proposed, ProposedContribution) and any(
                c.content == proposed.content for c in reader.read_level(proposed.level)
            ):
                return Reject("already on the board")
            return Accept()

        control = make_control(rule)
        assert control.write("a", "application", "one") == Written(sequence=1)
        assert control.write("a", "application", "one") == Rejected(
            cause=RejectionCause.ADMISSION, reason="already on the board"
        )

    def test_the_rule_judges_register_writes(self) -> None:
        seen: list[ProposedWrite] = []

        def rule(proposed: ProposedWrite, reader: BoardReader) -> Accept | Reject:
            seen.append(proposed)
            return Reject("nothing enters")

        control = make_control(rule)
        result = control.set_premise("operator", "window", "w", expected_version=0)
        assert result == Rejected(
            cause=RejectionCause.ADMISSION, reason="nothing enters"
        )
        assert seen == [
            ProposedPremiseWrite(
                writer="operator", premise="window", value="w", expected_version=0
            )
        ]
        assert control.read_audit() == [
            WriteRejected(
                at=START,
                writer="operator",
                region="window",
                cause=RejectionCause.ADMISSION,
                reason="nothing enters",
            )
        ]

    def test_the_rule_receives_the_content_object_itself(self) -> None:
        content = {"finding": "f1"}
        seen: list[object] = []

        def rule(proposed: ProposedWrite, reader: BoardReader) -> Accept | Reject:
            assert isinstance(proposed, ProposedContribution)
            seen.append(proposed.content)
            return Accept()

        control = make_control(rule)
        control.write("a", "application", content)
        assert seen[0] is content


class TestSetRegister:
    def test_an_accepted_register_write_returns_written_and_is_audited(self) -> None:
        control = make_control()
        result = control.set_premise("operator", "window", "w", expected_version=0)
        assert result == Written(sequence=1, version=1)
        assert control.reader.read_premise("window").value == "w"
        assert control.read_audit() == [
            WriteAccepted(at=START, writer="operator", region="window", sequence=1)
        ]

    def test_a_conflict_passes_through_without_an_audit_event(self) -> None:
        control = make_control()
        control.set_premise("operator", "window", "w1", expected_version=0)
        result = control.set_premise("late", "window", "w2", expected_version=0)
        assert result == Conflict(current_version=1)
        assert control.read_audit() == [
            WriteAccepted(at=START, writer="operator", region="window", sequence=1)
        ]


class TestRegionRefusals:
    def test_a_write_to_an_undeclared_region_is_rejected_and_recorded(self) -> None:
        control = make_control()
        result = control.write("a", "missing", "x")
        assert result == Rejected(
            cause=RejectionCause.UNDECLARED_REGION,
            reason="no region is declared with the name 'missing'",
        )
        assert control.read_audit() == [
            WriteRejected(
                at=START,
                writer="a",
                region="missing",
                cause=RejectionCause.UNDECLARED_REGION,
                reason="no region is declared with the name 'missing'",
            )
        ]

    def test_a_register_write_to_an_undeclared_region_is_rejected(self) -> None:
        control = make_control()
        result = control.set_premise("a", "missing", "x", expected_version=0)
        assert isinstance(result, Rejected)
        assert result.cause is RejectionCause.UNDECLARED_REGION

    def test_the_admission_rule_never_sees_an_undeclared_region_write(self) -> None:
        seen: list[ProposedWrite] = []

        def rule(proposed: ProposedWrite, reader: BoardReader) -> Accept | Reject:
            seen.append(proposed)
            return Accept()

        control = make_control(rule)
        control.write("a", "missing", "x")
        assert seen == []

    def test_a_kind_mismatch_raises_and_is_not_audited(self) -> None:
        control = make_control()
        with pytest.raises(RegionKindError):
            control.write("a", "window", "x")
        with pytest.raises(RegionKindError):
            control.set_premise("a", "application", "x", expected_version=0)
        assert control.read_audit() == []


class TestAudit:
    def test_timestamps_come_from_the_injected_clock(self) -> None:
        clock = ManualClock(start=START)
        control = Control(
            regions=[Level("application")],
            admission_rule=None,
            termination_predicate=keep_open,
            limits=LIMITS,
            clock=clock,
            board=InMemoryBoard(),
        )
        control.write("a", "application", "one")
        clock.advance(timedelta(seconds=90))
        control.write("a", "application", "two")
        first, second = control.read_audit()
        assert isinstance(first, WriteAccepted)
        assert isinstance(second, WriteAccepted)
        assert first.at == START
        assert second.at == START + timedelta(seconds=90)

    def test_the_audit_is_a_snapshot(self) -> None:
        control = make_control()
        control.write("a", "application", "one")
        control.read_audit().clear()
        assert len(control.read_audit()) == 1

    def test_accepted_writes_appear_in_sequence_order_under_concurrency(self) -> None:
        control = make_control()
        barrier = threading.Barrier(8)

        def run() -> None:
            barrier.wait()
            for _ in range(50):
                control.write("a", "application", "x")

        threads = [threading.Thread(target=run) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        sequences = [
            event.sequence
            for event in control.read_audit()
            if isinstance(event, WriteAccepted)
        ]
        assert sequences == sorted(sequences)
        assert len(sequences) == 400


class TestWhoWrites:
    def test_a_writer_that_never_registered_may_still_write(self) -> None:
        control = make_control()
        # Nothing named this has registered. The parameter is a writer, not
        # an agent, and the audit records it under that name.
        assert control.write("an-operator", "application", "x") == Written(sequence=1)
        (event,) = control.read_audit()
        assert isinstance(event, WriteAccepted)
        assert event.writer == "an-operator"

    def test_the_admission_rule_sees_the_same_writer_on_both_kinds(self) -> None:
        seen: list[str] = []

        def rule(proposed: ProposedWrite, reader: BoardReader) -> Accept | Reject:
            seen.append(proposed.writer)
            return Accept()

        control = make_control(rule)
        control.write("ocp", "application", "one")
        control.set_premise("ocp", "window", "w", expected_version=0)
        assert seen == ["ocp", "ocp"]

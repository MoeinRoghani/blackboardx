"""A replaced name still works for one release, and says what replaced it."""

from datetime import UTC, datetime, timedelta

import pytest

import blackboard
from blackboard import (
    InMemoryBoard,
    Level,
    ManualClock,
    Premise,
    ProposedContribution,
    RunLimits,
    Written,
    create_model,
)
from blackboard._control import Control

START = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
LIMITS = RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(minutes=30))


def a_model(**kwargs: object) -> blackboard.Model:
    return create_model(
        regions=[Level("platform"), Premise("window")],
        premises={"window": "w"},
        clock=ManualClock(start=START),
        board=InMemoryBoard(),
        **kwargs,  # type: ignore[arg-type]  # forwarded keyword arguments
    )


class TestRenamedNames:
    @pytest.mark.parametrize(
        ("old", "new"),
        [
            ("RunBudgets", "RunLimits"),
            ("Accepted", "Written"),
            ("Register", "Premise"),
            ("RegisterState", "PremiseState"),
            ("UnsetRegisterError", "UnsetPremiseError"),
            ("ProposedRegisterWrite", "ProposedPremiseWrite"),
            ("RegisterSeeded", "PremiseOpened"),
            ("SeedError", "PremiseError"),
        ],
    )
    def test_it_warns_and_names_its_replacement_and_removal(
        self, old: str, new: str
    ) -> None:
        with pytest.deprecated_call() as caught:
            resolved = getattr(blackboard, old)
        assert resolved is getattr(blackboard, new)
        message = str(caught[0].message)
        assert new in message
        assert "0.6.0" in message

    def test_the_old_name_is_still_part_of_the_declared_surface(self) -> None:
        for name in (
            "RunBudgets",
            "Accepted",
            "Register",
            "RegisterState",
            "UnsetRegisterError",
            "ProposedRegisterWrite",
            "RegisterSeeded",
            "SeedError",
        ):
            assert name in blackboard.__all__

    def test_a_region_declared_by_the_old_name_is_the_same_region(self) -> None:
        with pytest.deprecated_call():
            old = blackboard.Register("window")
        assert old == Premise("window")
        board = InMemoryBoard()
        board.declare(old)
        board.set("window", "w", expected_version=0)
        assert board.read_premise("window").value == "w"

    def test_a_value_built_from_the_old_name_equals_one_built_from_the_new(
        self,
    ) -> None:
        with pytest.deprecated_call():
            old = blackboard.Accepted(sequence=1)
        assert old == Written(sequence=1)


class TestBudgetsKeyword:
    def test_it_warns_and_the_run_still_opens(self) -> None:
        with pytest.deprecated_call(match="renamed limits"):
            model = a_model(budgets=LIMITS)
        assert model.reader.read_premise("window").value == "w"

    def test_naming_both_is_refused(self) -> None:
        with pytest.raises(TypeError, match="not both"), pytest.deprecated_call():
            a_model(limits=LIMITS, budgets=LIMITS)

    def test_naming_neither_is_refused(self) -> None:
        with pytest.raises(TypeError, match="require limits"):
            a_model()

    def test_control_takes_the_old_keyword_too(self) -> None:
        with pytest.deprecated_call():
            control = Control(
                regions=[Level("platform")],
                budgets=LIMITS,
                clock=ManualClock(start=START),
                board=InMemoryBoard(),
            )
        assert control.outcome() is None


class TestProposedContributionAgent:
    def test_the_old_field_name_warns_and_returns_the_writer(self) -> None:
        proposed = ProposedContribution(writer="ocp", level="platform", content="a")
        with pytest.deprecated_call(match="renamed writer"):
            assert proposed.agent == "ocp"


class TestOneNameForAWriteThatLanded:
    def test_a_level_write_reports_written_without_a_version(self) -> None:
        model = a_model(limits=LIMITS)
        result = model.control.write("ocp", "platform", "a finding")
        assert result == Written(sequence=2)
        assert isinstance(result, Written)
        assert result.version is None

    def test_a_register_write_reports_written_with_one(self) -> None:
        model = a_model(limits=LIMITS)
        result = model.control.set_premise("ocp", "window", "w2", expected_version=1)
        assert result == Written(sequence=2, version=2)


class TestSeedKeyword:
    def test_it_warns_and_the_premises_still_open(self) -> None:
        with pytest.deprecated_call(match="renamed premises"):
            model = create_model(
                regions=[Premise("window")],
                seed={"window": "w"},
                limits=LIMITS,
                clock=ManualClock(start=START),
                board=InMemoryBoard(),
            )
        assert model.reader.read_premise("window").value == "w"

    def test_naming_both_is_refused(self) -> None:
        with pytest.raises(TypeError, match="not both"), pytest.deprecated_call():
            create_model(
                regions=[Premise("window")],
                premises={"window": "w"},
                seed={"window": "w"},
                limits=LIMITS,
                clock=ManualClock(start=START),
                board=InMemoryBoard(),
            )

    def test_naming_neither_is_refused(self) -> None:
        with pytest.raises(TypeError, match="requires premises"):
            create_model(
                regions=[Premise("window")],
                limits=LIMITS,
                clock=ManualClock(start=START),
                board=InMemoryBoard(),
            )


class TestRenamedMethods:
    def test_read_register_warns_and_reads_the_premise(self) -> None:
        model = a_model(limits=LIMITS)
        with pytest.deprecated_call(match="renamed read_premise"):
            assert model.reader.read_register("window").value == "w"  # type: ignore[attr-defined]

    def test_set_register_warns_and_writes_the_premise(self) -> None:
        model = a_model(limits=LIMITS)
        with pytest.deprecated_call(match="renamed set_premise"):
            result = model.control.set_register("ocp", "window", "w2", 1)
        assert result == Written(sequence=2, version=2)
        assert model.reader.read_premise("window").value == "w2"

"""A replaced name still works for one release, and says what replaced it."""

from datetime import UTC, datetime, timedelta

import pytest

import blackboard
from blackboard import (
    InMemoryBoard,
    Level,
    ManualClock,
    ProposedContribution,
    Register,
    RunLimits,
    Written,
    create_model,
)
from blackboard._control import Control

START = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
LIMITS = RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(minutes=30))


def a_model(**kwargs: object) -> blackboard.Model:
    return create_model(
        regions=[Level("platform"), Register("window")],
        seed={"window": "w"},
        clock=ManualClock(start=START),
        board=InMemoryBoard(),
        **kwargs,  # type: ignore[arg-type]  # forwarded keyword arguments
    )


class TestRenamedNames:
    @pytest.mark.parametrize(
        ("old", "new"), [("RunBudgets", "RunLimits"), ("Accepted", "Written")]
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
        assert "RunBudgets" in blackboard.__all__
        assert "Accepted" in blackboard.__all__

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
        assert model.reader.read_register("window").value == "w"

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
        result = model.control.set_register("ocp", "window", "w2", expected_version=1)
        assert result == Written(sequence=2, version=2)

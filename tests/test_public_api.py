"""The package imports, and every name in its public surface resolves."""

import warnings
from datetime import timedelta

import pytest

import blackboard


def test_package_imports() -> None:
    assert blackboard.__name__ == "blackboard"


def test_public_surface_is_declared_and_resolves() -> None:
    assert isinstance(blackboard.__all__, list)
    with warnings.catch_warnings():
        # A deprecated name is still part of the surface until it is removed.
        warnings.simplefilter("ignore", DeprecationWarning)
        for name in blackboard.__all__:
            assert hasattr(blackboard, name)


REMOVED_IN_0_7 = [
    "Accepted",
    "ProposedRegisterWrite",
    "Register",
    "RegisterSeeded",
    "RegisterState",
    "RunBudgets",
    "SeedError",
    "UnsetRegisterError",
]


@pytest.mark.parametrize("name", REMOVED_IN_0_7)
def test_a_name_0_6_said_it_removed_is_gone(name: str) -> None:
    assert name not in blackboard.__all__
    with pytest.raises(AttributeError):
        getattr(blackboard, name)


def test_the_renamed_methods_are_gone() -> None:
    board = blackboard.InMemoryBoard()
    assert not hasattr(board, "read_register")
    assert not hasattr(blackboard.Control, "set_register")


def test_the_renamed_keywords_are_gone() -> None:
    limits = blackboard.RunLimits(
        wall_clock=timedelta(hours=1), idle=timedelta(minutes=5)
    )
    with pytest.raises(TypeError):
        blackboard.create_model(  # type: ignore[call-arg]  # the removal is the subject
            regions=[blackboard.Premise("window")],
            seed={"window": "w"},
            limits=limits,
            board=blackboard.InMemoryBoard(),
        )
    with pytest.raises(TypeError):
        blackboard.create_model(  # type: ignore[call-arg]  # the removal is the subject
            regions=[blackboard.Premise("window")],
            premises={"window": "w"},
            budgets=limits,
            board=blackboard.InMemoryBoard(),
        )


def test_a_proposed_contribution_has_no_agent_field() -> None:
    proposed = blackboard.ProposedContribution(
        writer="ocp", level="platform", content="a"
    )
    assert not hasattr(proposed, "agent")

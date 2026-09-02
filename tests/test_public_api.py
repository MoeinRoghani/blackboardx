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
    board = blackboard.InMemoryStore()
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
            board=blackboard.InMemoryStore(),
        )
    with pytest.raises(TypeError):
        blackboard.create_model(  # type: ignore[call-arg]  # the removal is the subject
            regions=[blackboard.Premise("window")],
            premises={"window": "w"},
            budgets=limits,
            board=blackboard.InMemoryStore(),
        )


def test_a_proposed_contribution_has_no_agent_field() -> None:
    proposed = blackboard.ProposedContribution(
        writer="ocp", level="platform", content="a"
    )
    assert not hasattr(proposed, "agent")


def test_the_public_modules_are_the_six_the_reference_lists() -> None:
    """`docs/reference.md` and `docs/index.md` both name them."""
    import pkgutil

    import blackboard as package

    public = {
        name
        for _, name, _ in pkgutil.iter_modules(package.__path__)
        if not name.startswith("_")
    }
    assert public == {"agent", "conformance", "delivery", "server", "wire"}


def test_postgres_creates_the_five_tables_the_storage_page_names() -> None:
    """`docs/concepts/storage.md` says how many, and a reader counts them."""
    import re
    from pathlib import Path

    source = Path("src/blackboard/_postgres.py").read_text()
    # Only what create_schema runs. The lazy check creates the stamp table
    # again on its own, for a store pointed at a database it did not make.
    schema = source[source.index("_SCHEMA = ") : source.index("_LEVEL = ")]
    created = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", schema)
    assert created == [
        "blackboard_schema",
        "blackboard_boards",
        "blackboard_regions",
        "blackboard_contributions",
        "blackboard_premises",
    ]


def test_the_wire_names_seven_operations() -> None:
    """`docs/guides/serving-a-blackboard.md` tabulates all of them."""
    from blackboard.wire import OPERATIONS

    assert [operation.name for operation in OPERATIONS] == [
        "read_regions",
        "read_level",
        "read_premise",
        "read_board",
        "write",
        "set_premise",
        "ack",
    ]


def test_run_limits_cannot_be_built_positionally() -> None:
    """Two same-typed durations swapped silently ended a run at the wrong time."""
    with pytest.raises(TypeError):
        blackboard.RunLimits(  # type: ignore[call-arg]
            timedelta(minutes=5), timedelta(minutes=30)
        )


def test_a_sqlite_store_names_where_it_writes() -> None:
    """Defaulting it to process memory is the thing create_model refuses to do."""
    with pytest.raises(TypeError):
        blackboard.SqliteStore()  # type: ignore[call-arg]

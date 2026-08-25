"""A board that needs an extra names the extra when the extra is missing."""

import builtins
from collections.abc import Callable
from typing import Any

import pytest

import blackboard


def test_an_unknown_name_is_an_attribute_error() -> None:
    with pytest.raises(AttributeError, match="Nonexistent"):
        getattr(blackboard, "Nonexistent")  # noqa: B009  # the lookup is the subject


def test_a_missing_driver_names_the_extra_that_supplies_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import: Callable[..., Any] = builtins.__import__

    def refuse_psycopg(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("psycopg"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(
        __import__("sys").modules, "blackboard._postgres", raising=False
    )
    monkeypatch.setattr(builtins, "__import__", refuse_psycopg)
    with pytest.raises(ImportError, match=r"blackboardx\[postgres\]"):
        getattr(blackboard, "PostgresBoard")  # noqa: B009  # the lookup is the subject

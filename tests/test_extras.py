"""A board that needs an extra names the extra when the extra is missing."""

import builtins
from collections.abc import Callable
from typing import Any

import pytest

import blackboard


def test_an_unknown_name_is_an_attribute_error() -> None:
    with pytest.raises(AttributeError, match="Nonexistent"):
        getattr(blackboard, "Nonexistent")  # noqa: B009  # the lookup is the subject


@pytest.mark.parametrize(
    ("name", "module", "driver", "extra"),
    [
        ("PostgresBoard", "blackboard._postgres", "psycopg", "postgres"),
        ("MongoBoard", "blackboard._mongodb", "pymongo", "mongodb"),
    ],
)
def test_a_missing_driver_names_the_extra_that_supplies_it(
    monkeypatch: pytest.MonkeyPatch, name: str, module: str, driver: str, extra: str
) -> None:
    real_import: Callable[..., Any] = builtins.__import__

    def refuse_the_driver(imported: str, *args: Any, **kwargs: Any) -> Any:
        if imported.startswith(driver):
            raise ImportError(f"No module named {imported!r}")
        return real_import(imported, *args, **kwargs)

    monkeypatch.delitem(__import__("sys").modules, module, raising=False)
    monkeypatch.setattr(builtins, "__import__", refuse_the_driver)
    with pytest.raises(ImportError, match=rf"blackboardx\[{extra}\]"):
        getattr(blackboard, name)  # the lookup is the subject

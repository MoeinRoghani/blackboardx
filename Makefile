.PHONY: setup lint lock-check typecheck test docs docs-serve

setup:
	uv sync --frozen
	uv run pre-commit install

lint:
	uv run ruff format --check .
	uv run ruff check .

lock-check:
	uv lock --check

typecheck:
	MYPYPATH=tests uv run mypy src tests

test:
	uv run pytest

docs:
	uv run mkdocs build --strict

docs-serve:
	uv run mkdocs serve

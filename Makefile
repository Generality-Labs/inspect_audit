.PHONY: install check test

install:
	uv venv
	uv pip install -e ".[dev]"

check:
	uv run ruff check src tests
	uv run mypy

test:
	uv run pytest

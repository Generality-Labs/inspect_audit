.PHONY: install check test test-docker

install:
	uv sync
	uv run pre-commit install

check:
	uv run pre-commit run --all-files
	uv run basedpyright src

# the default run deselects the docker suite (see pytest addopts); it is its own target
test:
	uv run pytest

test-docker:
	uv run pytest -m docker

# inspect_audit

An automated tool for auditing evals: benchmark validity auditing for Inspect AI evals.

## Development

```bash
uv sync                     # Python 3.13 via .python-version, so the Hawk extra installs
uv run pre-commit install   # optional: run the lint stack on every commit
uv run pytest               # the docker suite is deselected by default
uv run pytest -m docker     # container tests; needs a running Docker daemon
uv run basedpyright src
```

Linting (ruff, [zizmor](https://docs.zizmor.sh/), mdformat) runs via [pre-commit](https://pre-commit.com); CI runs the same stack plus basedpyright and pytest via the shared [`python-ci`](https://github.com/Generality-Labs/python-project-template) reusable workflow.

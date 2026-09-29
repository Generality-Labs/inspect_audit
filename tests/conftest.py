"""Shared fixtures."""

import pytest
from test_helpers.logs import run_fixture_eval, run_graded_eval


@pytest.fixture(scope="session")
def fixture_log(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A real single-epoch log over three samples."""
    return run_fixture_eval(str(tmp_path_factory.mktemp("fixture_log")))


@pytest.fixture(scope="session")
def fixture_log_epochs(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A real three-epoch log over the same three samples."""
    return run_fixture_eval(str(tmp_path_factory.mktemp("fixture_log_epochs")), epochs=3)


@pytest.fixture(scope="session")
def graded_log(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A real log whose recorded grades are mixed (C, I, C)."""
    return run_graded_eval(str(tmp_path_factory.mktemp("graded_log")))


@pytest.fixture(autouse=True)
def operator_openrouter_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Child jobs default to the operator's own OpenRouter key; tests supply a fake one.

    Tests about a missing key delete it themselves.
    """
    monkeypatch.setenv("INSPECT_AUDIT_OPENROUTER_API_KEY", "sk-or-test-operator")


@pytest.fixture(autouse=True)
def openrouter_serves_every_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """The start-of-run worker check would call OpenRouter; tests never reach it.

    Tests of the check itself restore the real probe behind a mock transport.
    """
    from inspect_audit import _investigate

    async def ok(client: object, model: str, key: str) -> dict[str, str]:
        return {"model": model, "status": "ok", "reason": ""}

    monkeypatch.setattr(_investigate, "_probe_worker", ok)

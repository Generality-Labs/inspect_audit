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


@pytest.fixture
def hawk(monkeypatch: pytest.MonkeyPatch):
    """A Hawk API on localhost (its own thread) and an operator token for it.

    This machine's Hawk settings name the real deployment's proxy; a decoy keeps any
    proxy call that ignored the given deployment from reaching it.
    """
    from test_helpers.fake_hawk import ThreadedHawk

    monkeypatch.setenv("HAWK_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("HAWK_MIDDLEMAN_URL", "http://127.0.0.1:9/not-the-real-proxy")
    server = ThreadedHawk()
    yield server.hawk
    server.close()

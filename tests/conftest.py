"""Shared fixtures."""

import pytest
from test_helpers.logs import run_fixture_eval


@pytest.fixture(scope="session")
def fixture_log(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A real single-epoch log over three samples."""
    return run_fixture_eval(str(tmp_path_factory.mktemp("fixture_log")))


@pytest.fixture(scope="session")
def fixture_log_epochs(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A real three-epoch log over the same three samples."""
    return run_fixture_eval(str(tmp_path_factory.mktemp("fixture_log_epochs")), epochs=3)

from pathlib import Path
from types import SimpleNamespace

import pytest
from inspect_ai.dataset import Sample
from inspect_ai.util._sandbox.context import sandbox_environments_context_var

from inspect_audit import _reset


@pytest.mark.anyio
async def test_rebuild_recreates_init_services_without_opening_sandboxes_for_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = SimpleNamespace(name="project", config=str(tmp_path / "compose.yaml"))
    default = SimpleNamespace(as_type=lambda _: SimpleNamespace(_project=project))
    environments = {"agent": object(), "target": object()}
    calls = []

    def sandbox(name: str | None = None):
        calls.append(name)
        return default if name in (None, "default") else environments[name]

    async def subprocess(args: list[str], timeout: int):
        if args[-2:] == ["config", "--services"]:
            return SimpleNamespace(success=True, stdout="agent target secrets_init\n", stderr="")
        return SimpleNamespace(success=True, stdout="", stderr="")

    monkeypatch.setattr(_reset, "sandbox", sandbox)
    monkeypatch.setattr(_reset, "async_subprocess", subprocess)
    token = sandbox_environments_context_var.set(environments)
    try:
        services = await _reset.rebuild(Sample(input="q"), [], None, None)  # type: ignore[arg-type]
    finally:
        sandbox_environments_context_var.reset(token)

    assert services == ["agent", "target", "secrets_init"]
    assert calls == [None, "default", "agent", "target"]

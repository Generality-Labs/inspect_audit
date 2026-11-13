"""Running the benchmark with code the auditor changed (FRICTION 73)."""

import asyncio
import inspect as pyinspect
import sys
from pathlib import Path
from typing import Any

import pytest
from inspect_ai.tool import ToolError

from inspect_audit import _audit, _registry
from inspect_audit._variant import check_overlay, variant_task
from inspect_audit.dispatch.policy import VARIANT_ARGS, Execution

TASK_MODULE = """
from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import match

from varbench.prompts import QUESTION


@task
def bench():
    return Task(dataset=[Sample(input=QUESTION, target="4", id="1")], scorer=match())
"""


@pytest.fixture
def varbench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An installed benchmark package of two modules: the task, and a prompt it imports."""
    site = tmp_path / "site"
    package = site / "varbench"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "prompts.py").write_text('QUESTION = "What is 2+2?"\n')
    (package / "tasks.py").write_text(TASK_MODULE)
    monkeypatch.syspath_prepend(str(site))
    yield site
    for name in [m for m in sys.modules if m == "varbench" or m.startswith("varbench.")]:
        del sys.modules[name]


def test_a_variant_builds_the_task_from_the_changed_package(varbench: Path) -> None:
    """A change to any module of the package, not only the task's, reaches the task."""
    files = {"varbench/prompts.py": 'QUESTION = "What is 2+2? Answer with a digit."\n'}
    changed = variant_task("varbench/bench", "varbench.tasks", {}, files)
    assert changed.dataset[0].input == "What is 2+2? Answer with a digit."
    # the installed package is untouched
    assert (varbench / "varbench" / "prompts.py").read_text() == 'QUESTION = "What is 2+2?"\n'


def test_a_variant_runs_through_inspect(varbench: Path) -> None:
    from inspect_ai import eval

    files = {"varbench/prompts.py": 'QUESTION = "changed"\n'}
    [log] = eval(
        variant_task("varbench/bench", "varbench.tasks", {}, files),
        model="mockllm/model",
        display="none",
        log_dir=None,
    )
    assert log.status == "success"
    assert log.samples and log.samples[0].input == "changed"


@pytest.mark.parametrize(
    ("files", "refusal"),
    [
        ({}, "holds no files"),
        ({"otherpkg/x.py": "x = 1\n"}, "starting varbench/"),
        ({"varbench/../evil.py": "x = 1\n"}, "starting varbench/"),
        ({"varbench/nowhere/x.py": "x = 1\n"}, "no such folder"),
        ({"varbench/prompts.py": 'QUESTION = "What is 2+2?"\n'}, "identical"),
        ({"varbench/prompts.py": "x" * (300 * 1024)}, "at most 256 KB"),
    ],
)
def test_a_variant_is_a_small_change_inside_the_benchmark(
    varbench: Path, files: dict[str, str], refusal: str
) -> None:
    with pytest.raises(ValueError, match=refusal):
        check_overlay("varbench/bench", "varbench.tasks", files)


def test_the_diff_records_exactly_what_changed(varbench: Path) -> None:
    diff = check_overlay(
        "varbench/bench", "varbench.tasks", {"varbench/prompts.py": 'QUESTION = "new"\n'}
    )
    assert '-QUESTION = "What is 2+2?"' in diff and '+QUESTION = "new"' in diff


def test_the_variant_task_takes_what_the_authoriser_allows() -> None:
    assert set(pyinspect.signature(_registry.variant).parameters) >= VARIANT_ARGS


class Box:
    """The auditor's box as `run_eval` reads a variant folder from it."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    async def exec(self, cmd: list[str], **kwargs: Any) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(success=True, stdout="\n".join(self.files), stderr="")

    async def read_file(self, path: str) -> str:
        return self.files[path]


def test_run_eval_with_code_launches_a_recorded_variant(
    varbench: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from inspect_ai.util import Store, store_as
    from inspect_ai.util._store import init_subtask_store
    from test_helpers.audit import make_audit

    from inspect_audit._report import AuditRecord
    from inspect_audit.dispatch.policy import PolicyStore, ensure_policy

    init_subtask_store(Store())

    class Runs:
        def __init__(self) -> None:
            self.submitted: list[Any] = []

        def address(self, run_id: str) -> str:
            return f"hawk:{run_id}"

        async def submit(self, job: Any) -> str:
            self.submitted.append(job)
            return "run-1"

    runs = Runs()
    inv = make_audit(
        root=tmp_path,
        runs=runs,
        task="varbench/bench",
        task_package="varbench",
        investigation_package="inspect_audit",
        investigator_image="image",
        menu=_audit.ModelMenu(investigators=[], subjects=["openai/subject"], judges=[]),
        supplied=[],
        source="varbench.tasks",
    )
    ensure_policy(inv.authorizer.policy, store_as(PolicyStore), require_existing=False)
    box = Box({"/workspace/v/varbench/prompts.py": 'QUESTION = "ablated"\n'})
    monkeypatch.setattr(_audit, "sandbox", lambda: box)
    run = _audit.run_eval(inv)
    asyncio.run(
        run("ablate", ["1"], ["openai/subject"], "{}", 1, [], "/workspace/v", [], None, "prompt")  # type: ignore[arg-type]
    )
    [job] = runs.submitted
    assert job.task == "inspect_audit/variant" and job.package == "inspect_audit"
    assert job.extra_packages == ["varbench"]
    assert job.task_args == {
        "task": "varbench/bench",
        "source": "varbench.tasks",
        "task_args": {},
        "files": {"varbench/prompts.py": 'QUESTION = "ablated"\n'},
    }
    [recorded] = store_as(AuditRecord).jobs
    assert recorded["kind"] == "variant" and '+QUESTION = "ablated"' in recorded["diff"]


def test_a_code_change_skips_bytecode_and_names_other_binaries(
    varbench: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Importing a change to test it writes __pycache__ beside it; that is not part of the
    change. Any other non-text file is refused by name (smoke round 7, FRICTION 97)."""
    import subprocess
    from types import SimpleNamespace

    from inspect_ai.tool import ToolError
    from inspect_ai.util import Store
    from inspect_ai.util._store import init_subtask_store
    from test_helpers.audit import make_audit

    init_subtask_store(Store())

    class LocalBox:
        """The box's `find` and file reads, on this machine's file system."""

        async def exec(self, cmd: list[str], **kwargs: Any) -> Any:
            done = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True)
            return SimpleNamespace(
                success=done.returncode == 0, stdout=done.stdout, stderr=done.stderr
            )

        async def read_file(self, path: str) -> str:
            return (await asyncio.to_thread(Path(path).read_bytes)).decode("utf-8")

    change = tmp_path / "change" / "varbench"
    (change / "__pycache__").mkdir(parents=True)
    (change / "prompts.py").write_text('QUESTION = "ablated"\n')
    (change / "__pycache__" / "prompts.cpython-313.pyc").write_bytes(b"\xa7\r\r\n\x00\xff")
    inv = make_audit(
        root=tmp_path / "audit",
        runs=None,
        task="varbench/bench",
        task_package="varbench",
        investigation_package="inspect_audit",
        investigator_image="image",
        menu=_audit.ModelMenu(investigators=[], subjects=["openai/subject"], judges=[]),
        supplied=[],
        source="varbench.tasks",
    )
    monkeypatch.setattr(_audit, "sandbox", LocalBox)
    files, _ = asyncio.run(_audit._variant_files(inv, str(tmp_path / "change")))
    assert files == {"varbench/prompts.py": 'QUESTION = "ablated"\n'}
    (change / "data.bin").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ToolError, match=r"data\.bin is not text"):
        asyncio.run(_audit._variant_files(inv, str(tmp_path / "change")))


def test_rescoring_takes_no_models(tmp_path: Path) -> None:
    from inspect_ai.util import Store, store_as
    from inspect_ai.util._store import init_subtask_store
    from test_helpers.audit import make_audit

    from inspect_audit.dispatch.policy import PolicyStore, ensure_policy

    init_subtask_store(Store())
    inv = make_audit(
        root=tmp_path,
        runs=None,
        task="b/t",
        task_package="b",
        investigation_package="inspect_audit",
        investigator_image="image",
        menu=_audit.ModelMenu(investigators=[], subjects=["openai/s"], judges=["openai/j"]),
        supplied=["hawk:logs"],
    )
    ensure_policy(inv.authorizer.policy, store_as(PolicyStore), require_existing=False)
    run = _audit.run_eval(inv)
    with pytest.raises(ToolError, match="models must be"):
        asyncio.run(
            run("r", ["1"], ["openai/s"], '{"g": "openai/j"}', 1, ["hawk:logs"], None, [], 0.5, "")
        )  # type: ignore[arg-type]


def test_hawk_child_jobs_are_restricted_unless_the_audit_says_otherwise() -> None:
    """Restricted by default; `user` only when stated, a temporary allowance while our
    Hawk has no secure token service (FRICTION 93)."""
    assert Execution.model_validate({"backend": "hawk"}).credential_mode == "restricted"
    stated = Execution.model_validate({"backend": "hawk", "credential_mode": "user"})
    assert stated.credential_mode == "user"


def test_the_benchmark_is_installed_in_the_box_as_its_jobs_install_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The agent's shell is in the box: it must be able to import and run the benchmark."""
    import json
    from types import SimpleNamespace

    from inspect_audit import _audit_workspace

    calls: list[list[str]] = []
    written: dict[str, str] = {}
    imported = {"done": False}

    class InstallBox:
        async def exec(self, cmd: list[str], **kwargs: Any) -> Any:
            calls.append(cmd)
            if cmd[:3] == ["python", "-m", "pip"]:
                imported["done"] = True
                return SimpleNamespace(success=True, stdout="Successfully installed", stderr="")
            return SimpleNamespace(success=imported["done"], stdout="", stderr="")

        async def write_file(self, path: str, text: str) -> None:
            written[path] = text

    monkeypatch.setattr(_audit_workspace, "sandbox", InstallBox)
    specs = ["bench @ git+https://x/bench@abc", "numpy==2.1.0", "inspect-ai==0.3.285"]
    solve = _audit_workspace.install_benchmark(specs, "bench.tasks")
    asyncio.run(solve(None, None))  # type: ignore[arg-type]
    assert calls[1][-3:] == specs
    record = json.loads(written[_audit_workspace.INSTALL_RECORD])
    assert record["installed"] and record["imports"] and record["specs"] == specs
    # a resumed box that still imports it is left alone
    calls.clear()
    asyncio.run(solve(None, None))  # type: ignore[arg-type]
    assert calls == [["python", "-c", "import bench.tasks"]]


def test_a_run_can_pin_other_dependency_versions() -> None:
    """Results that turn on a dependency's version are a finding: let the auditor test it."""
    from inspect_audit.dispatch.hawk import hawk_config
    from inspect_audit.dispatch.jobs import Job, with_overrides
    from inspect_audit.dispatch.policy import AuditAuthorizer, LaunchPolicy, Models

    policy = LaunchPolicy(
        task="b/t",
        task_package="b",
        task_pins=("numpy==2.1.0", "scipy==1.13.0"),
        investigation_package="ia",
        investigator_image="i",
        models=Models(subjects=("openai/s",)),
        checks=("gold-answer",),
        configuration_json="{}",
    )
    authoriser = AuditAuthorizer(policy)
    overrides = ["numpy==1.26.4", "inspect-ai==0.3.270"]
    job = Job(
        label="old-numpy",
        task="b/t",
        package="b",
        models=["openai/s"],
        extra_packages=with_overrides(list(policy.task_pins), overrides),
        package_overrides=overrides,
    )
    packages = hawk_config(authoriser.authorise(job, "benchmark", set()))["packages"]
    assert packages == ["b", "scipy==1.13.0", "numpy==1.26.4", "inspect-ai==0.3.270"]
    for bad in (["numpy>=1"], ["numpy==1.26.4", "numpy==1.26.3"]):
        with pytest.raises(ToolError):
            authoriser.authorise(
                Job(
                    label="bad",
                    task="b/t",
                    package="b",
                    models=["openai/s"],
                    extra_packages=with_overrides(list(policy.task_pins), bad),
                    package_overrides=bad,
                ),
                "benchmark",
                set(),
            )


def test_local_runs_refuse_version_overrides(tmp_path: Path) -> None:
    from inspect_audit.dispatch.jobs import Job
    from inspect_audit.dispatch.local import LocalRuns

    job = Job(label="x", task="b/t", package="b", models=["m/x"], package_overrides=["numpy==1.0"])
    with pytest.raises(ValueError, match="need the Hawk backend"):
        asyncio.run(LocalRuns(tmp_path).submit(job))


def test_the_auditors_box_takes_the_deployment_default_size(tmp_path: Path) -> None:
    """Nothing reserved unless the operator asks (FRICTION 80)."""
    import yaml

    from inspect_audit._audit_workspace import configure

    _, path = configure(tmp_path, "image")
    [values] = [d for d in yaml.safe_load_all(Path(path).read_text()) if d and "services" in d]
    assert "resources" not in values["services"]["default"]
    sized = {"requests": {"cpu": "1", "memory": "4Gi"}}
    _, path = configure(tmp_path, "image", sized)
    [values] = [d for d in yaml.safe_load_all(Path(path).read_text()) if d and "services" in d]
    assert values["services"]["default"]["resources"] == sized

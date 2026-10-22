"""Exercise investigator inputs, configuration, tools and real Docker staging."""

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from inspect_ai import eval, task_with
from inspect_ai.model import (
    ModelOutput,
    get_model,
)
from inspect_ai.solver import TaskState
from test_helpers.logs import run_fixture_eval

from inspect_audit._investigate import investigate


def every(models: list[str]):
    """A model menu letting each model play every part (auditor, subject, judge)."""
    from inspect_audit._investigate import ModelMenu

    return ModelMenu(auditors=list(models), subjects=list(models), judges=list(models))


def _snapshot_names(root: Path) -> list[str]:
    """Every file in the unpacked snapshot, relative to /inputs/source."""
    source = root / "inputs" / "source"
    return sorted(str(p.relative_to(source)) for p in source.rglob("*") if p.is_file())


def test_snapshot_paths_paper_download_and_docs_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.request

    repo = git_repo(tmp_path / "repo")
    (repo / "other_eval").mkdir()
    (repo / "other_eval" / "noise.py").write_text("# not under audit\n")
    subprocess.run(["git", "-C", str(repo), "add", "other_eval"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@e.org",
            "commit",
            "-qm",
            "more",
        ],
        check=True,
    )
    docs = tmp_path / "inspect-docs"
    docs.mkdir()
    (docs / "index.md").write_text("# docs\n")

    class Response:
        def __init__(self, body: bytes) -> None:
            self.body = body

        def read(self) -> bytes:
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    fetched: list[str] = []

    def urlopen(request, timeout=None):
        fetched.append(request.full_url)
        return Response(b"%PDF-1.4 fake")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    target = investigate(
        str(repo),
        paths=["task.py"],
        paper="https://arxiv.org/abs/2509.07968v2",
        docs=[str(docs)],
        output_dir=str(tmp_path / "runs"),
    )
    root = Path(target.metadata["investigation_dir"])
    assert _snapshot_names(root) == ["task.py"]
    seed = json.loads((root / "inputs/seed.json").read_text())
    assert seed["paths"] == ["task.py"]
    assert fetched == ["https://arxiv.org/pdf/2509.07968v2"]
    assert seed["paper"] == "/inputs/paper/2509.07968v2.pdf"
    assert (root / "inputs/paper/2509.07968v2.pdf").read_bytes().startswith(b"%PDF")
    assert seed["docs"] == ["/inputs/docs/inspect-docs"]
    assert (root / "inputs/docs/inspect-docs/index.md").is_file()


def test_log_inputs_share_storage_without_a_second_copy(tmp_path: Path) -> None:
    source = tmp_path / "source.eval"
    source.write_bytes(b"log content")
    target = investigate(
        str(git_repo(tmp_path / "repo")),
        logs=[str(source)],
        output_dir=str(tmp_path / "runs"),
    )
    root = Path(target.metadata["investigation_dir"])
    assert (root / "inputs/logs/0/source.eval").stat().st_ino == source.stat().st_ino
    assert (root / "work/findings.json").is_file()
    assert not (root / "work/report/findings.json").exists()
    assert not (root / "work/report/report.tex").exists()
    assert (root / "work/analysis/framework/checks.json").is_file()


def git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "task.py").write_text("# benchmark source\n")
    subprocess.run(["git", "-C", str(path), "add", "task.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.org",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    (path / ".env").write_text("DO_NOT_STAGE=secret")
    return path


def test_snapshot_excludes_untracked_secrets(
    tmp_path: Path,
) -> None:
    target = investigate(
        str(git_repo(tmp_path / "repo")),
        output_dir=str(tmp_path / "runs"),
    )
    root = Path(target.metadata["investigation_dir"])
    assert _snapshot_names(root) == ["task.py"]
    seed = json.loads((root / "inputs/seed.json").read_text())
    assert len(seed["revision"]) == 40
    work = root / "work"
    assert (work / "analysis/framework/checks.json").is_file()
    assert not list(work.rglob("assessments.*"))
    assert not list((work / "report").iterdir())
    assert (work / "findings.json").is_file()
    assert not (work / "register.json").exists()


def test_a_missing_log_source_fails_early(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Log source does not exist"):
        investigate(
            "https://example.org/repo",
            logs=[str(tmp_path / "missing")],
            output_dir=str(tmp_path),
        )


def test_mounted_skills_are_wellformed_and_adapted() -> None:
    """Every mounted skill parses, is named after its directory, and says where it is.

    The nine borrowed skills were written for a repo checkout with a user to ask. Each
    keeps its original text and gains an `In this container` section; the container has
    no `uv`, so a stray `uv run` would send the agent down a dead end.
    """
    from inspect_audit._investigate import ASSETS, INVESTIGATION_SKILLS

    ours = {"investigating", "running-jobs", "security-audit-eval"}
    for name in INVESTIGATION_SKILLS:
        text = (ASSETS / "skills" / name / "SKILL.md").read_text()
        import yaml

        front = yaml.safe_load(text.split("---")[1])
        assert front["name"] == name, name
        assert front["description"], name
        if name not in ours:
            assert "## In this container" in text, name
            leftovers = [
                line for line in text.splitlines() if "uv run" in line and "gnore" not in line
            ]
            assert not leftovers, (name, leftovers)


def test_what_can_be_derived_is_derived(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The audited repository already says which commit, which directory, which paper.

    Every one of these was a parameter someone had to pass correctly, and a wrong pin
    means the agent reads one commit while its jobs run another.
    """
    from inspect_audit._investigate import (
        git_package_spec,
        paper_from_metadata,
        paths_from_metadata,
    )

    repo = tmp_path / "evals"
    (repo / "src/suite/thing").mkdir(parents=True)
    (repo / "src/suite/utils").mkdir()
    (repo / "src/suite/constants.py").write_text("X = 1\n")
    (repo / "src/suite/thing/thing.py").write_text("# the task\n")
    (repo / "src/suite/other").mkdir()
    (repo / "src/suite/other/other.py").write_text("# a different eval\n")
    (repo / "src/suite/thing/eval.yaml").write_text(
        "arxiv: https://arxiv.org/abs/1111.11111,https://arxiv.org/abs/2222.22222\n"
        "tasks:\n  - name: thing_verified\n"
    )
    (repo / "pyproject.toml").write_text("[project]\nname='suite'\n")

    chosen = paths_from_metadata(repo, "suite/thing_verified")
    assert chosen is not None
    assert "src/suite/thing" in chosen
    assert "src/suite/other" not in chosen, "auditing one eval must not snapshot the rest"
    # the newest paper: an eval with two is one that was revised
    assert paper_from_metadata(repo, "suite/thing_verified") == "https://arxiv.org/abs/2222.22222"
    assert paths_from_metadata(repo, "suite/not_here") is None

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:org/suite.git"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@e.org",
            "commit",
            "-qm",
            "c",
        ],
        check=True,
    )
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    assert git_package_spec(repo) == f"git+https://github.com/org/suite@{commit}"

    # a repository with no remote cannot be installed in a runner, and says so
    bare = tmp_path / "local"
    bare.mkdir()
    subprocess.run(["git", "init", "-q", str(bare)], check=True)
    assert git_package_spec(bare) is None


def test_the_task_directory_is_found_without_inspect_evals_conventions(tmp_path: Path) -> None:
    """Not every benchmark ships an eval.yaml; most just declare a task in a file."""
    from inspect_audit._investigate import paths_from_metadata

    repo = tmp_path / "bench"
    (repo / "bench/task/example").mkdir(parents=True)
    (repo / "bench/task/__init__.py").write_text("")
    (repo / "bench/task/example/__init__.py").write_text(
        '@task(name="Example Questions")\ndef example_puzzles(epochs: int = 1) -> Task:\n    ...\n'
    )
    (repo / "bench/task/other").mkdir()
    (repo / "bench/task/other/__init__.py").write_text(
        "@task\ndef something_else() -> Task:\n    ...\n"
    )
    (repo / "tests").mkdir()
    (repo / "tests/test_example.py").write_text(
        '@task(name="Example Questions")\ndef example_puzzles():\n    ...\n'
    )

    # the registry name, which is not the function name and carries a space
    chosen = paths_from_metadata(repo, "bench/Example Questions")
    assert chosen is not None and "bench/task/example" in chosen
    assert not any("other" in c for c in chosen)
    assert not any("tests" in c for c in chosen), "a test that declares the task is not the task"

    # the function name, for a task that does not name itself
    assert "bench/task/other" in (paths_from_metadata(repo, "bench/something_else") or [])
    # and nothing invented when the task cannot be found
    assert paths_from_metadata(repo, "bench/not_here") is None


def test_an_investigation_can_be_a_file_and_the_command_line_still_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A saved investigation is a document; re-running it with one thing changed is -T."""
    repo = git_repo(tmp_path / "repo")
    config = tmp_path / "investigation.yaml"
    config.write_text(
        f"repo: {repo}\noutput_dir: {tmp_path / 'runs'}\noverview: read the grader first\n"
    )
    from_file = investigate(config=str(config))
    seed = json.loads(
        (Path(from_file.metadata["investigation_dir"]) / "inputs/seed.json").read_text()
    )
    assert seed["overview"] == "read the grader first"

    overridden = investigate(config=str(config), overview="read the dataset first")
    seed = json.loads(
        (Path(overridden.metadata["investigation_dir"]) / "inputs/seed.json").read_text()
    )
    assert seed["overview"] == "read the dataset first", "the command line beats the file"

    typo = tmp_path / "typo.yaml"
    typo.write_text(f"repo: {repo}\nbudget: 100\n")
    with pytest.raises(ValueError, match="sets things this task does not take"):
        investigate(config=str(typo))
    with pytest.raises(ValueError, match="no such investigation file"):
        investigate(config=str(tmp_path / "nowhere.yaml"))
    with pytest.raises(ValueError, match="needs a repo"):
        investigate()


def test_the_only_task_a_repository_declares_needs_no_naming(tmp_path: Path) -> None:
    from inspect_audit._investigate import _only_task

    one = tmp_path / "one"
    (one / "bench").mkdir(parents=True)
    (one / "bench/task.py").write_text(
        '@task(name="Example Questions")\ndef example_puzzles():\n    ...\n'
    )
    assert _only_task(one) == "Example Questions"

    several = tmp_path / "several"
    (several / "bench").mkdir(parents=True)
    (several / "bench/a.py").write_text("@task\ndef one_thing():\n    ...\n")
    (several / "bench/b.py").write_text("@task\ndef another():\n    ...\n")
    assert _only_task(several) is None, "a collection must be told which task to audit"


def test_the_config_file_decides_before_anything_is_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A setting in the file must be able to change what loads, not arrive too late.

    Skills were assembled and the budget validated before the file was read, so
    `extra_skills` in a config was silently ignored and a bad budget in a config was
    accepted.
    """
    repo = git_repo(tmp_path / "repo")
    skill = tmp_path / "house-style"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: house-style\ndescription: ours\n---\n\nRead this.\n"
    )
    config = tmp_path / "investigation.yaml"
    config.write_text(f"repo: {repo}\noutput_dir: {tmp_path / 'runs'}\nextra_skills: ['{skill}']\n")
    assert investigate(config=str(config)).metadata["investigation_dir"]

    # a skill directory named by the file is checked like one named on the command line;
    # before the reordering the file's extra_skills were read after the list was built
    # and went unnoticed entirely
    not_a_skill = tmp_path / "empty"
    not_a_skill.mkdir()
    broken = tmp_path / "broken.yaml"
    broken.write_text(
        f"repo: {repo}\noutput_dir: {tmp_path / 'runs'}\nextra_skills: ['{not_a_skill}']\n"
    )
    with pytest.raises(ValueError, match=r"containing SKILL\.md"):
        investigate(config=str(broken))

    bad = tmp_path / "bad.yaml"
    bad.write_text(f"repo: {repo}\nruns: elsewhere\noutput_dir: {tmp_path / 'runs'}\n")
    with pytest.raises(ValueError, match="runs must be"):
        investigate(config=str(bad))


def test_the_snapshot_follows_what_the_task_imports(tmp_path: Path) -> None:
    """A fixed list of shared filenames is a guess; the imports are the answer.

    A scorer in an ordinary sibling module would have been left out of the snapshot,
    and the agent would have audited a benchmark with a hole in it.
    """
    from inspect_audit._investigate import paths_from_metadata

    repo = tmp_path / "suite"
    package = repo / "src" / "suite"
    for part in ("thing", "shared", "unrelated"):
        (package / part).mkdir(parents=True)
        (package / part / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "oddly_named.py").write_text("def grade():\n    ...\n")
    (package / "constants.py").write_text("X = 1\n")
    (package / "thing" / "__init__.py").write_text(
        "from suite.oddly_named import grade\n"
        "from suite.shared.helpers import prepare\n"
        '@task(name="Thing")\ndef thing():\n    ...\n'
    )
    (package / "shared" / "helpers.py").write_text(
        "from suite.constants import X\n\ndef prepare():\n    ...\n"
    )
    (package / "unrelated" / "other.py").write_text("# another eval entirely\n")
    (repo / "pyproject.toml").write_text("[project]\nname='suite'\n")

    chosen = paths_from_metadata(repo, "suite/Thing")
    assert chosen is not None
    assert "src/suite/thing" in chosen
    # imported directly, and it is not on anybody's list of likely names
    assert "src/suite/oddly_named.py" in chosen
    # imported through the module that was imported: the closure, not one hop
    assert "src/suite/constants.py" in chosen
    assert any(c in chosen for c in ("src/suite/shared", "src/suite/shared/helpers.py"))
    # never named by the task's code
    assert not any("unrelated" in c for c in chosen)


def test_a_blocked_investigation_ends_and_scores_as_blocked() -> None:
    from inspect_ai.util._store import Store, init_subtask_store

    from inspect_audit import _investigate
    from inspect_audit._report import InvestigationState, investigation_outcome, report_blocker

    store = Store()
    init_subtask_store(store)
    out = asyncio.run(report_blocker()(reason="every child job dies at load: Model not found"))
    assert "blocked" in out.lower()

    class _State:
        output = None

    assert asyncio.run(_investigate._continue(_State(), interactive=True)) is False  # type: ignore[arg-type]

    class _Task:
        def store_as(self, cls: type) -> object:
            return store_as_(cls)

    from inspect_ai.util import store_as as store_as_

    scored = asyncio.run(investigation_outcome()(_Task(), None))  # type: ignore[arg-type]
    assert scored.value == "blocked" and "Model not found" in (scored.explanation or "")
    assert store_as_(InvestigationState).outcome == "blocked"


def test_every_tool_schema_survives_a_strict_provider(tmp_path: Path) -> None:
    """A schema whose `required` omits a property is refused by OpenAI-strict providers.

    Azure, which is where OpenRouter routed sol, returns 400 with "'required' is
    required to be supplied and to be an array including every key in properties". The
    first real run died on that before its first turn. Optional arguments are expressed
    as nullable and still required, which is the documented way to have both.
    """
    from inspect_ai.tool._tool_def import ToolDef

    from inspect_audit import _investigate
    from inspect_audit._findings import submit_findings
    from inspect_audit._image import view_image
    from inspect_audit._publishing import load_publisher, publish_document

    inv = _investigate.Investigation(
        root=tmp_path,
        runs=None,
        where="local",
        task="b/t",
        task_package="b",  # type: ignore[arg-type]
        audit_package="a",
        auditor_image="i",
        menu=every(["m"]),
        supplied=[],
    )
    ours = [
        _investigate.run_benchmark(inv),
        _investigate.run_audit(inv),
        _investigate.jobs(inv),
        _investigate.budget(None, openrouter=False),
        view_image(),
        submit_findings(tmp_path),
        publish_document(tmp_path, load_publisher("gl")),
    ]

    def free_form(schema: Any) -> bool:
        """An object with no declared properties: strict schemas refuse it (2026-10-20)."""
        if schema is None:
            return False
        if schema.type == "object" and not schema.properties:
            return True
        return any(free_form(s) for s in (schema.anyOf or [])) or free_form(schema.items)

    for tool in ours:
        definition = ToolDef(tool)
        missing = set(definition.parameters.properties) - set(definition.parameters.required or [])
        assert not missing, (
            f"{definition.name} has optional parameters {sorted(missing)}; a strict "
            "provider refuses the whole request. Make them nullable and required."
        )
        loose = [n for n, p in definition.parameters.properties.items() if free_form(p)]
        assert not loose, (
            f"{definition.name} takes free-form objects {loose}; Azure's strict schemas "
            "refuse them. Take a JSON string instead."
        )


def test_jobs_refuse_to_guess_a_package_they_cannot_derive(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")  # no origin remote
    with pytest.raises(ValueError, match="declare task_package and audit_package"):
        investigate(
            str(repo),
            output_dir=str(tmp_path / "runs"),
            target_task="benchmark/task",
            runs="local",
        )
    with pytest.raises(ValueError, match="target_task"):
        investigate(str(repo), output_dir=str(tmp_path / "runs"), runs="local")


def test_jobs_are_offered_only_where_they_were_declared(tmp_path: Path) -> None:
    repo = str(git_repo(tmp_path / "repo"))
    plain = investigate(repo, output_dir=str(tmp_path / "runs"))
    seed = json.loads((Path(plain.metadata["investigation_dir"]) / "inputs/seed.json").read_text())
    assert "jobs" not in seed
    package = "git+https://example.org/benchmark.git@revision#egg=benchmark[optional]"
    with_jobs = investigate(
        repo,
        output_dir=str(tmp_path / "runs"),
        target_task="benchmark/task",
        runs="local",
        task_package=package,
        audit_package="inspect_audit",
    )
    seed = json.loads(
        (Path(with_jobs.metadata["investigation_dir"]) / "inputs/seed.json").read_text()
    )
    assert seed["jobs"]["runs"] == "local" and seed["jobs"]["task"] == "benchmark/task"


def test_the_investigator_checkpoints_its_workspace_and_inputs(tmp_path: Path) -> None:
    task = investigate(str(git_repo(tmp_path / "repo")), output_dir=str(tmp_path / "runs"))
    assert task.checkpoint is not None
    assert task.checkpoint.sandbox_paths == {"default": ["/workspace", "/inputs"]}


def test_a_value_equal_to_a_default_is_still_an_override(tmp_path: Path) -> None:
    """ "Given" cannot mean "different from the default", or a file wins arguments it should not."""
    repo = git_repo(tmp_path / "repo")
    config = tmp_path / "investigation.yaml"
    config.write_text(f"repo: {repo}\noutput_dir: {tmp_path / 'runs'}\ninteractive: true\n")
    assert investigate(config=str(config)).metadata["interactive"] is True
    # the value the default would have used, passed deliberately, beats the file
    assert investigate(config=str(config), interactive=False).metadata["interactive"] is False


@pytest.mark.docker
def test_inputs_are_staged_into_a_plain_box_and_survive_nothing_mounted(tmp_path: Path) -> None:
    """The box is the only workspace: the staged seed, logs and report skeleton are in it.

    A light stand-in for the full-image tests above: the same staging on a stock
    image, without LaTeX, so it runs anywhere Docker does.
    """
    source_log = run_fixture_eval(str(tmp_path / "source_logs"))
    target = investigate(
        str(git_repo(tmp_path / "repo")),
        logs=[source_log],
        output_dir=str(tmp_path / "runs"),
    )
    compose = tmp_path / "compose.yaml"
    compose.write_text(
        "services:\n  default:\n    image: python:3.12-slim\n"
        "    command: sleep infinity\n    init: true\n    working_dir: /workspace\n"
    )
    script = (
        "python -c \"import json,pathlib; s=json.load(open('/inputs/seed.json')); "
        "print(len(s['logs']), pathlib.Path(s['logs'][0]['staged']).stat().st_size > 0, "
        "pathlib.Path('/workspace/report').is_dir(), pathlib.Path('/workspace/.staged').exists())\""
    )
    model = get_model(
        "mockllm/model",
        custom_outputs=[
            ModelOutput.for_tool_call("mockllm/model", "bash", {"command": script}),
            ModelOutput.for_tool_call("mockllm/model", "budget", {}),
            ModelOutput.for_tool_call(
                "mockllm/model", "report_blocker", {"reason": "test ends here"}
            ),
        ],
    )
    target = task_with(target, sandbox=("docker", str(compose)), checkpoint=None)
    log = eval(target, model=model, display="none", log_dir=str(tmp_path / "logs"))[0]
    assert log.status == "success", log.error
    from inspect_ai.model import ChatMessageTool

    [staged, budget, _] = [m for m in log.samples[0].messages if isinstance(m, ChatMessageTool)]
    assert staged.text.split() == ["1", "True", "True", "True"], staged.text
    assert not budget.error, budget.error


def test_every_agent_tool_schema_meets_openais_strict_rules(tmp_path: Path) -> None:
    """Strict mode as OpenAI documents it, for every tool either agent is given.

    Every object declares its properties, lists all of them as required and allows
    no others; a property may be nullable but not absent. Azure applies these rules
    to whatever OpenRouter routes there (2026-10-20: a free-form task_args object
    was refused before the investigator's first turn).
    """
    from inspect_ai.tool._tool_def import ToolDef

    from inspect_audit import _investigate
    from inspect_audit._findings import submit_findings
    from inspect_audit._image import view_image
    from inspect_audit._publishing import load_publisher, publish_document
    from inspect_audit._report import report_blocker
    from inspect_audit.publishers.gl import figure, preview_report

    inv = _investigate.Investigation(
        root=tmp_path,
        runs=None,
        where="local",
        task="b/t",
        task_package="b",  # type: ignore[arg-type]
        audit_package="a",
        auditor_image="i",
        menu=every(["m"]),
        supplied=[],
    )
    tools = [
        _investigate.run_benchmark(inv),
        _investigate.run_audit(inv),
        _investigate.jobs(inv),
        _investigate.budget(None, openrouter=False),
        view_image(),
        submit_findings(tmp_path),
        publish_document(tmp_path, load_publisher("gl")),
        report_blocker(),
        _investigate.start_register("b/t"),
        _investigate.register_view(),
        figure(),
        preview_report(tmp_path),
        *_auditor_tools(),
    ]

    def problems(schema: dict[str, Any], where: str, top: bool = False) -> list[str]:
        found: list[str] = []
        for option in schema.get("anyOf") or []:
            found += problems(option, where)
        if schema.get("type") == "object" or "properties" in schema:
            properties = schema.get("properties") or {}
            # a tool with no parameters is fine; a free-form object argument is not
            if not properties and not top:
                found.append(f"{where}: an object with no declared properties")
            if set(schema.get("required") or []) != set(properties):
                found.append(f"{where}: required must list every property")
            if schema.get("additionalProperties", False) is not False:
                found.append(f"{where}: additionalProperties must be false")
            for name, sub in properties.items():
                found += problems(sub, f"{where}.{name}")
        if isinstance(schema.get("items"), dict):
            found += problems(schema["items"], f"{where}[]")
        return found

    refused: list[str] = []
    for tool in tools:
        definition = ToolDef(tool)
        schema = definition.parameters.model_dump(exclude_none=True)
        schema.setdefault("additionalProperties", False)  # Inspect adds it when strict
        refused += problems(schema, definition.name, top=True)
    assert not refused, "\n".join(refused)


def _auditor_tools() -> list[Any]:
    """The auditor's own tools, from the factories its solver calls (never executed)."""
    from inspect_audit._grade import grade_tool
    from inspect_audit._image import view_image
    from inspect_audit._logs import log_tools
    from inspect_audit._reset import reset_tool
    from inspect_audit._verdicts import audit_items, record_verdict, submit_audit

    checks = audit_items()
    return [
        record_verdict(checks),
        submit_audit(checks),
        view_image("auditor"),
        *log_tools(None, "1"),
        grade_tool(
            TaskState(model="mockllm/model", sample_id="1", epoch=1, input="q", messages=[]),
            [],
            [],
            None,
        ),
        reset_tool(None, [], None, None),  # type: ignore[arg-type]
    ]


def test_the_investigator_builds_from_a_directory_it_cannot_write(tmp_path: Path) -> None:
    """A Hawk runner's working directory is read-only (2026-10-20: the relative
    default output folder failed task construction there)."""
    import os
    import stat

    readonly = tmp_path / "readonly"
    readonly.mkdir()
    readonly.chmod(stat.S_IRUSR | stat.S_IXUSR)
    home = tmp_path / "home"
    home.mkdir()
    cwd = Path.cwd()
    old_home = os.environ.get("HOME")
    try:
        os.chdir(readonly)
        os.environ["HOME"] = str(home)
        task = investigate("https://example.org/benchmark.git", revision="abc")
    finally:
        os.chdir(cwd)
        if old_home is not None:
            os.environ["HOME"] = old_home
        readonly.chmod(stat.S_IRWXU)
    assert Path(task.metadata["investigation_dir"]).is_relative_to(home)

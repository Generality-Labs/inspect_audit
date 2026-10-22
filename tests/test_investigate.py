"""Exercise input isolation, publication and the real Docker/Quarto path."""

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from inspect_ai import eval, task_with
from inspect_ai.model import (
    ModelCost,
    ModelInfo,
    ModelOutput,
    get_model,
    set_model_info,
)
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox
from test_helpers.logs import run_fixture_eval

from inspect_audit._investigate import investigate, publish_report, save_publication


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


def test_findings_validate_and_bundle_input_evidence(tmp_path: Path) -> None:
    from inspect_audit._report import validate_findings

    report = tmp_path / "work/report"
    inputs = tmp_path / "inputs"
    report.mkdir(parents=True)
    inputs.mkdir()
    (inputs / "trace.txt").write_text("primary evidence")
    (report / "report.tex").write_text("Report")
    for name in ("Findings.tex", "metadata.tex", "assessments.tex"):
        (report / name).write_text("Fixture")
    (report / "report.pdf").write_text("<p>Report</p>")
    finding = {
        "id": "F1",
        "section": "grading",
        "claim": "A claim",
        "status": "supported",
        "origin": "historical",
        "evidence": [{"path": "/inputs/trace.txt", "location": "line 1"}],
        "reproduce": "Read line 1",
        "limitations": "",
    }
    register = report / "findings.json"
    register.write_text(json.dumps([finding]))
    destination = save_publication(tmp_path)
    saved = json.loads((destination / "findings.json").read_text())
    assert (destination / saved[0]["evidence"][0]["path"]).read_text() == "primary evidence"
    assert json.loads(register.read_text())[0]["evidence"][0]["path"] == "/inputs/trace.txt"
    for change in [
        {"status": "confirmed"},
        {"section": "vibes"},
        {"evidence": []},
        {"evidence": [{"path": "../escape", "location": "line 1"}]},
        {"evidence": [{"path": "/etc/passwd", "location": "line 1"}]},
        {"evidence": [{"path": "missing", "location": "line 1"}]},
    ]:
        register.write_text(json.dumps([{**finding, **change}]))
        with pytest.raises(ValueError):
            validate_findings(tmp_path)
    register.write_text(json.dumps([{"id": "missing-fields"}]))
    with pytest.raises(ValueError):
        validate_findings(tmp_path)
    register.write_text(json.dumps([finding, finding]))
    with pytest.raises(ValueError, match="unique"):
        validate_findings(tmp_path)


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
    assert not (root / "work/findings.json").exists()
    assert (root / "work/report/findings.json").is_file()


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


def test_snapshot_excludes_untracked_secrets_and_retains_publications(
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
    report = root / "work/report"
    (report / "report.pdf").write_text("first version")
    _declare_fixture_assessments(root)
    first = save_publication(root)
    (report / "report.pdf").write_text("second version")
    second = save_publication(root)
    assert first != second
    assert (first / "report.pdf").read_text() == "first version"
    (report / "leak").symlink_to(tmp_path / "repo/.env")
    with pytest.raises(ValueError, match="symlinks"):
        save_publication(root)


def test_a_missing_log_source_fails_early(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Log source does not exist"):
        investigate(
            "https://example.org/repo",
            logs=[str(tmp_path / "missing")],
            output_dir=str(tmp_path),
        )


def test_publication_switches_from_work_to_discussion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from inspect_ai.agent import AgentState
    from inspect_ai.model import ChatMessageAssistant, ModelOutput

    from inspect_audit import _investigate

    record = _investigate.InvestigationState()
    monkeypatch.setattr(_investigate, "store_as", lambda _: record)
    state = AgentState(messages=[ChatMessageAssistant(content="Working")])
    state.output = ModelOutput.from_content("mockllm/model", "Working")
    assert isinstance(asyncio.run(_investigate._continue(state, True)), str)
    record.published = "/published/version1"
    assert asyncio.run(_investigate._continue(state, False)) is False

    async def operator_reply(state: AgentState) -> str:
        return "Show me the supporting transcript."

    monkeypatch.setattr(_investigate, "operator_turn", operator_reply)
    assert asyncio.run(_investigate._continue(state, True)) == "Show me the supporting transcript."


@solver
def render_probe(root: str) -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        result = await sandbox().exec(
            [
                "bash",
                "-c",
                "test -f /inputs/source/task.py && test ! -e /inputs/source/.env && test ! -w /inputs/source/task.py",
            ]
        )
        assert result.success, result.stderr
        result = await sandbox().exec(
            [
                "python",
                "-c",
                "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt; plt.bar(['Reviewed', 'Unreviewed'], [3, 1]); plt.ylabel('Attempts'); plt.savefig('/workspace/report/coverage.png')",
            ]
        )
        assert result.success, result.stderr
        # Exercise the same SVG preview that crashed real investigations.
        from inspect_audit._image import view_image

        await sandbox().write_file(
            "/workspace/report/architecture.svg",
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50">'
            '<rect width="100" height="50" fill="red"/></svg>',
        )
        preview = await view_image()("/workspace/report/architecture.svg")
        assert isinstance(preview, list)
        assert preview[0].image.startswith("data:image/png;base64,")
        _declare_fixture_assessments(Path(root))
        receipt = await publish_report(root)()
        state.store.set("publication_receipt", receipt)
        return state

    return solve


@pytest.mark.docker
def test_real_container_renders_and_exports_report(tmp_path: Path) -> None:
    set_model_info(
        "mockllm/model",
        ModelInfo(cost=ModelCost(input=0, output=0, input_cache_read=0, input_cache_write=0)),
    )
    target = investigate(
        str(git_repo(tmp_path / "repo")),
        output_dir=str(tmp_path / "runs"),
        interactive=False,
    )
    root = Path(target.metadata["investigation_dir"])
    target = task_with(target, solver=render_probe(str(root)))
    log = eval(target, model="mockllm/model", display="none", log_dir=str(tmp_path / "logs"))[0]
    assert log.status == "success", log.error
    published = list((root / "published").glob("*/report.pdf"))
    assert len(published) == 1
    assert published[0].read_bytes().startswith(b"%PDF")


@pytest.mark.docker
def test_agent_reads_logs_and_publishes_before_batch_exit(tmp_path: Path) -> None:
    set_model_info(
        "mockllm/model",
        ModelInfo(cost=ModelCost(input=0, output=0, input_cache_read=0, input_cache_write=0)),
    )
    source_log = run_fixture_eval(str(tmp_path / "source_logs"))
    target = investigate(
        str(git_repo(tmp_path / "repo")),
        logs=[source_log],
        output_dir=str(tmp_path / "runs"),
        interactive=False,
    )
    _declare_fixture_assessments(Path(target.metadata["investigation_dir"]))
    script = """python - <<'PY'
import json
from pathlib import Path
from inspect_ai.log import read_eval_log
seed = json.loads(Path('/inputs/seed.json').read_text())
log = read_eval_log(seed['logs'][0]['staged'])
assert len(log.samples) == 3
assert len(log.samples) == 3
PY"""
    model = get_model(
        "mockllm/model",
        custom_outputs=[
            ModelOutput.for_tool_call("mockllm/model", "skill", {"command": "investigating"}),
            ModelOutput.for_tool_call("mockllm/model", "bash", {"command": script}),
            ModelOutput.for_tool_call("mockllm/model", "budget", {}),
            ModelOutput.for_tool_call("mockllm/model", "publish_report", {}),
        ],
    )
    log = eval(target, model=model, display="none", log_dir=str(tmp_path / "logs"))[0]
    assert log.status == "success", log.error
    root = Path(target.metadata["investigation_dir"])
    published = list((root / "published").glob("*/report.pdf"))
    assert len(published) == 1
    assert published[0].read_bytes().startswith(b"%PDF")
    assert log.samples and log.samples[0].store
    from inspect_ai.model import ChatMessageTool

    assert not [
        m.error for m in log.samples[0].messages if isinstance(m, ChatMessageTool) and m.error
    ]


def test_the_same_input_cited_twice_publishes_once(tmp_path: Path) -> None:
    report = tmp_path / "work/report"
    inputs = tmp_path / "inputs"
    report.mkdir(parents=True)
    inputs.mkdir()
    (inputs / "paper.pdf").write_bytes(b"%PDF")
    (report / "report.tex").write_text("Report")
    for name in ("Findings.tex", "metadata.tex", "assessments.tex"):
        (report / name).write_text("Fixture")
    (report / "report.pdf").write_text("<p>Report</p>")
    finding = {
        "id": "F1",
        "section": "dataset",
        "claim": "A",
        "status": "supported",
        "origin": "source",
        "evidence": [
            {"path": "/inputs/paper.pdf", "location": "p. 1"},
            {"path": "/inputs/paper.pdf", "location": "p. 2"},
        ],
        "reproduce": "read",
        "limitations": "",
    }
    (report / "findings.json").write_text(json.dumps([finding, {**finding, "id": "F2"}]))
    destination = save_publication(tmp_path)
    saved = json.loads((destination / "findings.json").read_text())
    assert {e["path"] for f in saved for e in f["evidence"]} == {"_inputs/paper.pdf"}
    assert (destination / "_inputs/paper.pdf").read_bytes() == b"%PDF"


def test_cited_logs_are_published_as_addresses_never_as_copies(tmp_path: Path) -> None:
    """2026-09-28: the sol6 chess bundle carried three cited .eval logs, uploaded under
    Hawk's evals/ prefix, and Hawk re-pointed their shared warehouse records at them.

    A log cited from either place logs arrive under /inputs (staged at preparation,
    including downloaded hawk: sources, or collected from a job) is published as its
    address plus a checksum; a derived file cited alongside is still copied.
    """
    import hashlib

    report = tmp_path / "work/report"
    inputs = tmp_path / "inputs"
    report.mkdir(parents=True)
    supplied = "hawk:imported-epoch-chess-sour-0pexj5yimxsum0r9"
    fetched = inputs / "logs" / "0" / "gemini.eval"
    collected = inputs / "jobs" / "pilot" / "2026-09-28T10-00-00_audit.eval"
    staged = inputs / "logs" / "1" / "local.eval"
    table = inputs / "derived" / "samples.csv"
    for path, body in ((fetched, b"a"), (collected, b"b"), (staged, b"c"), (table, b"id,score\n")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    (inputs / "seed.json").write_text(
        json.dumps(
            {
                "logs": [
                    {
                        "source": f"{supplied}/gemini.eval",
                        "staged": "/inputs/logs/0/gemini.eval",
                        "remote": supplied,
                    },
                    {
                        "source": "/Users/op/runs/local.eval",
                        "staged": "/inputs/logs/1/local.eval",
                        "remote": None,
                    },
                ]
            }
        )
    )
    (report / "report.tex").write_text("Report")
    for name in ("Findings.tex", "metadata.tex", "assessments.tex"):
        (report / name).write_text("Fixture")
    (report / "report.pdf").write_text("pdf")
    cited = [fetched, collected, staged, table]
    (report / "findings.json").write_text(
        json.dumps(
            [
                {
                    "id": "F1",
                    "section": "grading",
                    "claim": "A",
                    "status": "supported",
                    "origin": "historical",
                    "reproduce": "read",
                    "limitations": "",
                    "evidence": [
                        {"path": "/" + str(p.relative_to(tmp_path)), "location": "x"} for p in cited
                    ],
                }
            ]
        )
    )

    destination = save_publication(tmp_path, {"pilot": "hawk:inv-pilot-1234abcd"})
    assert not list(destination.rglob("*.eval"))
    paths = [
        e["path"] for e in json.loads((destination / "findings.json").read_text())[0]["evidence"]
    ]
    assert paths == [
        f"{supplied}/gemini.eval",
        "hawk:inv-pilot-1234abcd/2026-09-28T10-00-00_audit.eval",
        "/Users/op/runs/local.eval",
        "_inputs/derived/samples.csv",
    ]
    assert (destination / paths[3]).read_bytes() == b"id,score\n"
    references = json.loads((destination / "_inputs/log-references.json").read_text())
    assert [r["sha256"] for r in references] == [
        hashlib.sha256(b).hexdigest() for b in (b"a", b"b", b"c")
    ]

    # an eval log placed in the report itself is refused, with the way out
    (report / "evidence").mkdir()
    (report / "evidence" / "copy.eval").write_bytes(b"a")
    with pytest.raises(ValueError, match="cite the log where it lives"):
        save_publication(tmp_path)


def test_mounted_skills_are_wellformed_and_adapted() -> None:
    """Every mounted skill parses, is named after its directory, and says where it is.

    The nine borrowed skills were written for a repo checkout with a user to ask. Each
    keeps its original text and gains an `In this container` section; the container has
    no `uv`, so a stray `uv run` would send the agent down a dead end.
    """
    from inspect_audit._investigate import ASSETS, INVESTIGATION_SKILLS

    ours = {"investigating", "writing", "running-jobs", "security-audit-eval"}
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


def _declare_fixture_assessments(root: Path) -> None:
    from inspect_audit._assessment import framework_checks

    report = root / "work/report"
    rows = [
        {
            "id": key,
            "assessment": "Not assessed",
            "result": "Infrastructure smoke test; no benchmark judgments",
            "evidence": [],
        }
        for key in framework_checks(report)
    ]
    (report / "assessments.json").write_text(json.dumps(rows))
    from inspect_audit._assessment import assessment_latex

    (report / "assessments.tex").write_text(assessment_latex(report))


def test_a_run_short_of_its_required_coverage_is_recorded_incomplete(tmp_path: Path) -> None:
    """Luna and sol6 on 09-25: 0/100 assessed, published, recorded as success."""
    from inspect_audit._report import record_outcome

    def coverage(not_assessed: int) -> None:
        (tmp_path / "coverage.json").write_text(
            json.dumps(
                {
                    "denominator": 100,
                    "counts": {"NO_ISSUE_FOUND": 100 - not_assessed, "NOT_ASSESSED": not_assessed},
                }
            )
        )

    assert record_outcome(tmp_path, None)[0] == "published_complete"
    assert record_outcome(tmp_path, 1.0) == (
        "published_incomplete",
        ["100% question coverage was required and the report has no coverage.json"],
    )
    coverage(100)
    outcome, reasons = record_outcome(tmp_path, 1.0)
    assert outcome == "published_incomplete" and reasons == [
        "0/100 questions assessed; 100% was required"
    ]
    coverage(5)
    assert record_outcome(tmp_path, 0.9) == ("published_complete", ["95/100 questions assessed"])


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
    from inspect_audit._image import view_image
    from inspect_audit._report import check_report, publish_report

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
        check_report("/tmp"),
        publish_report("/tmp"),
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
    from inspect_audit._image import view_image
    from inspect_audit._report import check_report, publish_report, report_blocker

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
        check_report("/tmp"),
        publish_report("/tmp"),
        report_blocker(),
        _investigate.start_register("b/t"),
        _investigate.register_view(),
        _investigate.review(),
        _investigate.finding(),
        _investigate.rate(),
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


def _leads_file(tmp_path: Path) -> Path:
    leads = tmp_path / "LEADS.md"
    leads.write_text(
        "# Leads for inspect_evals/x\n\n- `inspect_evals_lint-1/1` IEBP011 · shuffle without a seed\n"
    )
    return leads


def test_leads_are_staged_read_only_and_named_in_the_seed(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    leads = _leads_file(tmp_path)
    target = investigate(
        str(repo),
        paths=["task.py"],
        leads=str(leads),
        output_dir=str(tmp_path / "runs"),
    )
    root = Path(target.metadata["investigation_dir"])
    seed = json.loads((root / "inputs/seed.json").read_text())
    assert seed["leads"] == "/inputs/findings/LEADS.md"
    assert (root / "inputs/findings/LEADS.md").read_text() == leads.read_text()


def test_without_leads_the_seed_says_there_are_none(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    target = investigate(str(repo), paths=["task.py"], output_dir=str(tmp_path / "runs"))
    seed = json.loads((Path(target.metadata["investigation_dir"]) / "inputs/seed.json").read_text())
    assert seed["leads"] is None
    assert not (Path(target.metadata["investigation_dir"]) / "inputs/findings").exists()


def test_a_missing_leads_file_is_an_error(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    with pytest.raises(ValueError, match="leads"):
        investigate(
            str(repo),
            paths=["task.py"],
            leads=str(tmp_path / "nope.md"),
            output_dir=str(tmp_path / "runs"),
        )


def test_leads_can_come_from_the_investigation_file(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    leads = _leads_file(tmp_path)
    config = tmp_path / "investigation.yaml"
    config.write_text(f"repo: {repo}\npaths: [task.py]\nleads: {leads}\n")
    target = investigate(config=str(config), output_dir=str(tmp_path / "runs"))
    seed = json.loads((Path(target.metadata["investigation_dir"]) / "inputs/seed.json").read_text())
    assert seed["leads"] == "/inputs/findings/LEADS.md"


def test_the_investigating_skill_says_how_to_use_leads() -> None:
    from inspect_audit._investigate import ASSETS

    skill = (ASSETS / "skills" / "investigating" / "SKILL.md").read_text()
    assert "/inputs/findings/LEADS.md" in skill and "record id" in skill
    # a lead is a pointer, never the evidence: the skill's own rule for summaries
    assert "alongside the source, data or transcript evidence" in skill
    # LEADS.md lists three ids a group; the rest live only in the run files
    assert "or the run file under /inputs/findings/runs/ that holds the record" in skill


def test_a_leads_bundle_directory_is_staged_whole(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    bundle = tmp_path / "bundle"
    (bundle / "runs").mkdir(parents=True)
    (bundle / "LEADS.md").write_text("# Leads\n- and 2 more in `runs/lint-1.run.json`\n")
    (bundle / "runs" / "lint-1.run.json").write_text("{}")
    target = investigate(
        str(repo),
        paths=["task.py"],
        leads=str(bundle),
        output_dir=str(tmp_path / "runs"),
    )
    root = Path(target.metadata["investigation_dir"])
    seed = json.loads((root / "inputs/seed.json").read_text())
    assert seed["leads"] == "/inputs/findings/LEADS.md"
    assert (root / "inputs/findings/runs/lint-1.run.json").is_file()


def test_a_leads_directory_without_leads_md_is_an_error(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match=r"LEADS\.md"):
        investigate(
            str(repo),
            paths=["task.py"],
            leads=str(tmp_path / "empty"),
            output_dir=str(tmp_path / "runs"),
        )

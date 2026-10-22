"""The handoff: real records, phase-specific tools, frozen artifacts and fresh publishing."""

import asyncio
from pathlib import Path

import pytest
from inspect_ai import eval, task_with
from inspect_ai.model import ChatMessageTool, ModelOutput, get_model
from inspect_ai.tool import ToolDef, ToolError
from inspect_ai.util import Store, store_as
from inspect_ai.util._store import init_subtask_store

from inspect_audit._findings import (
    Findings,
    FindingsState,
    InvestigatorFindings,
    auditor_results,
    read_bundle,
    require_investigation,
    validate_draft,
)
from inspect_audit._publishing import PhaseTools, Publisher, publish
from inspect_audit._register import Register, Take
from inspect_audit._verdicts import Evidence
from inspect_audit.publishers.gl import validate_rows


def test_draft_keeps_auditors_and_requires_explained_findings() -> None:
    register = Register(
        subject={"task": "bench"},
        checks=["gold-answer"],
        samples={
            "a": {
                "gold-answer": [
                    Take(
                        by="auditor",
                        label="UNVERIFIABLE",
                        explanation="No reference",
                        evidence=[Evidence(observed="reference missing", source="task.py")],
                    )
                ]
            }
        },
    )
    draft = Findings(
        subject=register.subject,
        auditors=auditor_results(register),
        investigator=InvestigatorFindings(summary="Reference missing"),
    )
    assert validate_draft(draft.model_dump(mode="json"), register) == draft
    data = draft.model_dump(mode="json")
    data["auditors"]["samples"]["a"]["gold-answer"] = []
    with pytest.raises(ValueError, match="collected results"):
        validate_draft(data, register)
    data = draft.model_dump(mode="json")
    data["investigator"]["findings"] = [
        {
            "issue": "missing reference",
            "explanation": "read source",
            "sample_ids": ["absent"],
            "evidence": [{"observed": "empty", "source": "task.py"}],
        }
    ]
    with pytest.raises(ValueError, match="outside"):
        validate_draft(data, register)


def test_phase_tools_are_not_advertised_before_submission() -> None:
    from inspect_ai.tool import bash

    init_subtask_store(Store())

    async def build(root: Path, bundle: Path) -> Path:
        return root

    package = Publisher(skills=[], build=build)
    tools = PhaseTools(Path("."), [], [bash()], package)
    assert [ToolDef(t).name for t in asyncio.run(tools.tools())] == ["bash"]
    store_as(FindingsState).phase = "publishing"
    assert [ToolDef(t).name for t in asyncio.run(tools.tools())] == ["skill", "publish_document"]
    with pytest.raises(ToolError, match="final"):
        require_investigation()


@pytest.mark.parametrize(
    "rows",
    [
        [{"label": "a", "value": float("nan")}],
        [{"label": "a", "value": True}],
        [{"label": "a"}],
        [],
    ],
)
def test_figure_values_are_actual_finite_numbers(rows) -> None:
    with pytest.raises(ValueError):
        validate_rows("bars", rows)


@pytest.mark.docker
@pytest.mark.publishing
def test_agent_submits_then_publishes_and_a_fresh_agent_reuses_the_bundle(tmp_path: Path) -> None:
    from test_investigate import git_repo

    from inspect_audit._investigate import investigate

    repo = git_repo(tmp_path / "repo")
    target = investigate(str(repo), output_dir=str(tmp_path / "runs"))
    root = Path(target.metadata["investigation_dir"])
    assert not list((root / "work/report").iterdir())
    compose = tmp_path / "box.yaml"
    compose.write_text(
        "services:\n  default:\n    image: python:3.13-slim\n    command: sleep infinity\n    working_dir: /workspace\n"
    )
    script = """python - <<'PYCODE'
import json
from pathlib import Path
p=Path('/workspace/findings.json')
d=json.loads(p.read_text())
d['investigator']['summary']='The scripted investigation completed; these are synthetic test results.'
d['investigator']['artifacts']=['/workspace/analysis/counts.json','/workspace/analysis/stacked.json','/workspace/analysis/histogram.json']
Path('/workspace/analysis').mkdir(exist_ok=True)
Path('/workspace/analysis/counts.json').write_text('[{"label":"Reviewed","value":1,"group":"Samples"}]')
Path('/workspace/analysis/stacked.json').write_text(json.dumps([{'label':'Synthetic','segments':[{'name':'Reviewed','value':1,'tone':'good'}]}]))
Path('/workspace/analysis/histogram.json').write_text(json.dumps([{'value':i%5,'series':'Synthetic'} for i in range(20)]))
p.write_text(json.dumps(d))
PYCODE"""
    report_script = """python - <<'PYCODE'
from pathlib import Path
p=Path('/inputs/findings/findings.json')
assert p.exists()
Path('/workspace/report/report.md').write_text('# Synthetic audit\\n\\nThe submitted result is synthetic.\\n\\n{{figure:coverage}}\\n\\n{{figure:composition}}\\n\\n{{figure:distribution}}\\n')
PYCODE"""
    model = get_model(
        "mockllm/model",
        custom_outputs=[
            ModelOutput.for_tool_call("mockllm/model", "bash", {"command": script}),
            ModelOutput.for_tool_call("mockllm/model", "submit_findings", {}),
            ModelOutput.for_tool_call("mockllm/model", "skill", {"command": "publishing"}),
            ModelOutput.for_tool_call("mockllm/model", "bash", {"command": report_script}),
            ModelOutput.for_tool_call(
                "mockllm/model",
                "figure",
                {
                    "name": "coverage",
                    "component": "bars",
                    "data_path": "/workspace/analysis/counts.json",
                    "caption": "One synthetic reviewed sample; denominator one.",
                    "x_label": "Samples (count)",
                    "sources": ["evidence/workspace/analysis/counts.json"],
                },
            ),
            ModelOutput.for_tool_call(
                "mockllm/model",
                "figure",
                {
                    "name": "composition",
                    "component": "stacked-bars",
                    "data_path": "/workspace/analysis/stacked.json",
                    "caption": "One synthetic sample by status.",
                    "x_label": "Samples (count)",
                    "sources": ["evidence/workspace/analysis/stacked.json"],
                },
            ),
            ModelOutput.for_tool_call(
                "mockllm/model",
                "figure",
                {
                    "name": "distribution",
                    "component": "histogram",
                    "data_path": "/workspace/analysis/histogram.json",
                    "caption": "Distribution of 20 synthetic observations.",
                    "x_label": "Synthetic value (units)",
                    "sources": ["evidence/workspace/analysis/histogram.json"],
                },
            ),
            ModelOutput.for_tool_call("mockllm/model", "preview_report", {}),
            ModelOutput.for_tool_call("mockllm/model", "publish_document", {}),
        ],
    )
    [log] = eval(
        task_with(target, sandbox=("docker", str(compose)), message_limit=30),
        model=model,
        display="none",
        log_dir=str(tmp_path / "logs"),
    )
    assert log.status == "success", log.error
    assert log.samples
    errors = [
        m.error for m in log.samples[0].messages if isinstance(m, ChatMessageTool) and m.error
    ]
    assert not errors
    [bundle] = (root / "submitted").iterdir()
    findings, _manifest = read_bundle(bundle)
    assert findings.investigator.summary.startswith("The scripted")
    [publication] = (root / "published").iterdir()
    assert (publication / "report.pdf").read_bytes().startswith(b"%PDF")
    assert "<svg" in (publication / "report.html").read_text()
    assert "<script" not in (publication / "report.html").read_text()
    assert {p.stem for p in publication.glob("*.svg")} == {
        "coverage",
        "composition",
        "distribution",
    }
    before = (bundle / "findings.json").read_bytes()
    fresh = publish(str(bundle / "findings.json"), output_dir=str(tmp_path / "fresh"))
    fresh_root = Path(fresh.metadata["investigation_dir"])
    model = get_model(
        "mockllm/model",
        custom_outputs=[
            ModelOutput.for_tool_call("mockllm/model", "skill", {"command": "publishing"}),
            ModelOutput.for_tool_call(
                "mockllm/model",
                "bash",
                {
                    "command": "mkdir -p /workspace/report && printf '# Second presentation\\n\\nSame synthetic findings.\\n' > /workspace/report/report.md"
                },
            ),
            ModelOutput.for_tool_call("mockllm/model", "publish_document", {}),
        ],
    )
    [replayed] = eval(
        task_with(fresh, sandbox=("docker", str(compose)), message_limit=20),
        model=model,
        display="none",
        log_dir=str(tmp_path / "fresh-logs"),
    )
    assert replayed.status == "success", replayed.error
    assert list((fresh_root / "published").glob("*/report.pdf"))
    assert (bundle / "findings.json").read_bytes() == before
    # The portable bundle is independently integrity checked.
    (bundle / "evidence/workspace/analysis/counts.json").write_text("tampered")
    with pytest.raises(ValueError, match="checksum"):
        read_bundle(bundle)


def test_submitted_phase_blocks_direct_job_launches(tmp_path: Path) -> None:
    from inspect_audit._investigate import Investigation, ModelMenu
    from inspect_audit._runs import Job, LocalRuns

    init_subtask_store(Store())
    runs = LocalRuns(tmp_path / "children")
    inv = Investigation(
        root=tmp_path,
        runs=runs,
        where="local",
        task="bench/task",
        task_package="bench",
        audit_package="audit",
        auditor_image="image",
        menu=ModelMenu(auditors=[], subjects=["mockllm/model"], judges=[]),
        supplied=[],
    )
    store_as(FindingsState).phase = "publishing"
    try:
        with pytest.raises(ToolError, match="final"):
            asyncio.run(
                inv.launch(
                    Job(label="late", task="bench/task", package="bench", models=["mockllm/model"]),
                    "benchmark",
                    "late experiment",
                )
            )
        assert not list((tmp_path / "children").iterdir())
    finally:
        init_subtask_store(Store())


def test_bundle_rejects_unlisted_files_and_directory_symlinks(tmp_path: Path) -> None:
    from inspect_audit._findings import encoded, sha256

    payload = encoded(Findings().model_dump(mode="json"))
    (tmp_path / "findings.json").write_bytes(payload)
    (tmp_path / "manifest.json").write_bytes(
        encoded(
            {
                "files": {"findings.json": {"sha256": sha256(payload), "bytes": len(payload)}},
                "artifacts": {},
            }
        )
    )
    read_bundle(tmp_path)
    (tmp_path / "unexpected.txt").write_text("unlisted")
    with pytest.raises(ValueError, match="exactly"):
        read_bundle(tmp_path)
    (tmp_path / "unexpected.txt").unlink()
    (tmp_path / "escape").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        read_bundle(tmp_path)


def test_default_publisher_is_an_actual_inspect_skill() -> None:
    from inspect_ai.tool import read_skills

    from inspect_audit._investigate import INVESTIGATION_SKILLS
    from inspect_audit._publishing import load_publisher

    skills = read_skills(load_publisher("gl").skills)
    assert [skill.name for skill in skills] == ["publishing"]
    assert "writing" not in INVESTIGATION_SKILLS
    assert "eval-report-workflow" not in INVESTIGATION_SKILLS


@pytest.mark.docker
def test_submitted_bundle_restores_on_a_fresh_host_and_rejects_changed_evidence(
    tmp_path: Path,
) -> None:
    from inspect_ai import Task, eval
    from inspect_ai.dataset import Sample
    from inspect_ai.solver import solver
    from inspect_ai.util import sandbox

    from inspect_audit._findings import freeze, restore_bundle

    root = tmp_path / "first-host"
    root.mkdir()
    compose = tmp_path / "box.yaml"
    compose.write_text(
        "services:\n  default:\n    image: python:3.13-slim\n    command: sleep infinity\n    working_dir: /workspace\n"
    )

    @solver
    def restore():
        async def solve(state, generate):
            await sandbox().exec(["mkdir", "-p", "/inputs"])
            await sandbox().write_file("/workspace/table.json", "[1, 2, 3]")
            await freeze(
                root,
                Findings(
                    investigator=InvestigatorFindings(
                        summary="Synthetic", artifacts=["/workspace/table.json"]
                    )
                ),
            )
            saved = state.store_as(FindingsState)
            saved.destination = None
            reconstructed = await restore_bundle(tmp_path / "fresh-host")
            findings, manifest = read_bundle(reconstructed)
            assert findings.investigator.summary == "Synthetic"
            assert (
                reconstructed / manifest["artifacts"]["/workspace/table.json"]
            ).read_text() == "[1, 2, 3]"
            await sandbox().write_file("/inputs/findings/evidence/workspace/table.json", "changed")
            with pytest.raises(ToolError, match="evidence changed"):
                await restore_bundle(tmp_path / "another-host")
            return state

        return solve

    [log] = eval(
        Task(dataset=[Sample(input="restore")], solver=restore(), sandbox=("docker", str(compose))),
        model="mockllm/model",
        display="none",
        log_dir=str(tmp_path / "logs"),
    )
    assert log.status == "success", log.error


@pytest.mark.parametrize(
    "change", [{"explanation": " "}, {"evidence": [{"observed": "", "source": "source"}]}]
)
def test_ratings_require_explanations_and_primary_observations(change) -> None:
    register = Register()
    rating = {
        "label": "Minor",
        "explanation": "Observed",
        "evidence": [{"observed": "observation", "source": "source"}],
        **change,
    }
    data = Findings(investigator=InvestigatorFindings(summary="Summary")).model_dump(mode="json")
    data["investigator"]["ratings"] = {"grading": rating}
    with pytest.raises(ValueError):
        validate_draft(data, register)

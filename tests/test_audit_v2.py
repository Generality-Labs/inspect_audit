"""The v2 audit: declared inputs, loud refusals, and one real run inside the benchmark.

The refusal tests need no Docker. The end-to-end test runs the toy benchmark's own
task with a scripted (mockllm) auditor driving the real tools: their box, the
recorded attempts, `grade`, `reset`, a verdict and a submit. No model spend.
"""

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from inspect_ai import eval
from inspect_ai.dataset import Sample
from inspect_ai.event import ToolEvent
from inspect_ai.log import read_eval_log
from inspect_ai.model import ModelOutput, get_model

from inspect_audit._compose import UnsupportedBenchmarkError, their_compose, their_sandbox
from inspect_audit._logs import LogIndex, LogMismatchError, index_logs
from inspect_audit._task import audit_task, judge_roles

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "toy_benchmark"))
import toy

# -- refusals -------------------------------------------------------------------------


def test_samples_with_their_own_sandboxes_are_refused() -> None:
    samples = [
        Sample(input="q", sandbox=("docker", "a.yaml")),
        Sample(input="q", sandbox=("docker", "b.yaml")),
    ]
    with pytest.raises(UnsupportedBenchmarkError, match="own sandboxes"):
        their_sandbox(None, samples)


def test_identical_sample_sandboxes_stand_for_the_task() -> None:
    samples = [Sample(input="q", sandbox=("docker", "a.yaml")) for _ in range(3)]
    assert their_sandbox(None, samples).config == "a.yaml"


def test_a_non_docker_sandbox_is_refused() -> None:
    with pytest.raises(UnsupportedBenchmarkError, match="'k8s'"):
        their_sandbox(("k8s", "values.yaml"), [Sample(input="q")])


def test_no_sandbox_is_refused() -> None:
    with pytest.raises(UnsupportedBenchmarkError, match="no sandbox"):
        their_sandbox(None, [Sample(input="q")])


def test_a_bare_docker_sandbox_needs_its_compose_declared(tmp_path: Path) -> None:
    spec = their_sandbox("docker", [Sample(input="q")])
    with pytest.raises(UnsupportedBenchmarkError, match="pass compose="):
        their_compose(spec, None, tmp_path)
    assert their_compose(spec, str(toy.COMPOSE), tmp_path) == toy.COMPOSE.resolve()


def test_a_declared_compose_must_match_the_tasks_own(tmp_path: Path) -> None:
    other = tmp_path / "other.yaml"
    other.write_text("services: {}\n")
    spec = their_sandbox(("docker", str(toy.COMPOSE)), [Sample(input="q")])
    with pytest.raises(UnsupportedBenchmarkError, match="differs"):
        their_compose(spec, str(other), tmp_path)


def test_a_log_of_another_task_is_refused(fixture_log: str) -> None:
    with pytest.raises(LogMismatchError, match="records task"):
        index_logs([fixture_log], task_name="other_task", task_args={}, package=None)


def test_a_log_run_with_other_arguments_is_refused(fixture_log: str) -> None:
    with pytest.raises(LogMismatchError, match="not the declared"):
        index_logs([fixture_log], task_name="fixture_task", task_args={"n": 3}, package=None)


def test_the_same_attempt_in_two_logs_is_refused(fixture_log: str) -> None:
    with pytest.raises(LogMismatchError, match="appears in both"):
        index_logs([fixture_log, fixture_log], task_name="fixture_task", task_args={}, package=None)


def test_the_benchmark_version_must_match_the_logs(fixture_log: str) -> None:
    index = index_logs([fixture_log], task_name="fixture_task", task_args={}, package="inspect_ai")
    assert sorted(index.attempts) == ["1", "2", "3"]
    with pytest.raises(LogMismatchError, match="not installed"):
        index_logs(
            [fixture_log], task_name="fixture_task", task_args={}, package="no-such-benchmark"
        )


def test_a_declared_judge_must_agree_with_the_logs() -> None:
    index = LogIndex(judges={"grader": "openai/gpt-5"})
    assert judge_roles(index, None) == {"grader": "openai/gpt-5"}
    with pytest.raises(LogMismatchError, match="differs"):
        judge_roles(index, {"grader": "anthropic/claude"})


def test_a_scorer_that_judged_with_the_evaluated_model_is_refused() -> None:
    index = LogIndex(scorer_models={"openai/gpt-5"}, eval_models={"openai/gpt-5"})
    with pytest.raises(UnsupportedBenchmarkError, match="evaluated model"):
        judge_roles(index, None)


# -- one real audit inside the benchmark ----------------------------------------------


def _call(tool: str, **arguments: Any) -> ModelOutput:
    return ModelOutput.for_tool_call("mockllm/model", tool, arguments)


@pytest.mark.docker
def test_an_audit_runs_inside_the_benchmark(tmp_path: Path) -> None:
    # a real recorded run of the benchmark: sample a answered correctly
    recorded_model = get_model(
        "mockllm/model",
        custom_outputs=[
            ModelOutput.from_content("mockllm/model", answer) for answer in ("42", "9")
        ],
    )
    [recorded] = eval(
        toy.toy(),
        model=recorded_model,
        log_dir=str(tmp_path / "recorded"),
        display="none",
        max_samples=1,
    )
    assert recorded.status == "success", recorded.error
    eval_id = recorded.eval.eval_id

    script = [
        _call("benchmark_bash", command="cat /work/input.txt /work/setup_done"),
        _call("attempts"),
        _call("grade", answer="42"),
        _call("benchmark_bash", command="rm -rf /work"),
        _call("grade", answer="42"),
        _call("reset"),
        _call("grade", answer="42"),
        _call("grade", eval_id=eval_id, epoch=1),
        _call("auditor_bash", command="cat /audit/item.json; ls /audit/benchmark"),
        _call(
            "record_verdict",
            item="red-teaming",
            evidence=[{"observed": "wrong answers graded I", "source": "grade"}],
            approaches="one",
            tried="wrong answers",
            remarks="sound",
            grade="RESISTANT",
            details=json.dumps({"attempts": [], "mechanism": "none"}),
        ),
        _call("submit", environment_issues=[], unresolved=[], improvements=[]),
    ]
    audit = audit_task(
        toy.toy(),
        samples=["a"],
        checks=["red-teaming"],
        logs=[recorded.location],
        message_limit=50,
    )
    toy.SCORER_CALLS.clear()
    [log] = eval(
        audit,
        model=get_model("mockllm/model", custom_outputs=script),
        log_dir=str(tmp_path / "audit"),
        display="none",
    )
    assert log.status == "success", log.error
    log = read_eval_log(log.location)
    assert log.eval.task == "audit/toy"
    assert log.eval.config.message_limit == 50
    [sample] = log.samples or []
    assert sample.error is None, sample.error

    results = [(e.function, str(e.result)) for e in sample.events if isinstance(e, ToolEvent)]
    by_call = [r for _, r in results]
    assert [f for f, _ in results] == [o.message.tool_calls[0].function for o in script]  # type: ignore[index]
    assert "ready" in by_call[0]  # their files and setup script
    assert eval_id in by_call[1]  # the recorded attempt is indexed
    grades = [json.loads(r)["scores"][0]["value"] for f, r in results if f == "grade"]
    assert grades == ["C", "I", "C", "C"]  # right, broken box, rebuilt, recorded completion
    assert "rebuilt default" in by_call[5]
    staged, _, listing = by_call[8].rpartition("}")
    item = json.loads(staged + "}")
    assert item["target"] == ["42"] and "toy.py" in listing
    assert "toy.py" in item["benchmark_source"]["copied"]
    assert sample.scores and sample.scores["red-teaming"].value == "RESISTANT"
    assert toy.SCORER_CALLS == ["a"] * 4  # their grader ran only for grade, never on the auditor


# -- the benchmark's code, as staged for the auditor ----------------------------------


def test_the_source_copy_follows_imports_within_the_package() -> None:
    from inspect_audit._source import collect_source

    package = Path(__file__).parent / "fixtures" / "source_pkg"
    copied = set(collect_source(package / "bench" / "tasks" / "qa" / "qa.py").manifest()["copied"])  # type: ignore[arg-type]
    assert {
        "bench/__init__.py",
        "bench/tasks/__init__.py",
        "bench/tasks/qa/__init__.py",
        "bench/tasks/qa/qa.py",
        "bench/tasks/qa/items.csv",  # the task folder's data
        "bench/tasks/shared.py",  # a relative import
        "bench/utils/__init__.py",
        "bench/utils/data.py",
        "bench/utils/grading.py",  # imported lazily, inside a function
        "bench/utils/templates/judge.txt",  # a helper's data folder
    } <= copied
    assert "bench/other/unrelated.py" not in copied


def test_without_logs_the_judge_must_be_declared() -> None:
    with pytest.raises(ValueError, match="declare their scorers' judge"):
        judge_roles(None, None)
    assert judge_roles(None, {}) == {}
    assert judge_roles(None, {"grader": "openai/gpt-4.1"}) == {"grader": "openai/gpt-4.1"}


def test_the_checks_and_prompt_name_only_what_the_v2_auditor_has() -> None:
    """A skill pointing at a v1 path or tool sends a real auditor looking for nothing."""
    from inspect_audit import prompts
    from inspect_audit._verdicts import SKILLS, SUPPORT_SKILLS, audit_items

    stale = [
        "sample.json",
        "/audit/logs",
        "gold/grading.md",
        "samples_df(",
        "audit_probe",
        "discrepancies.md",
        "assessment_ids.json",
        "`attempt`",
        "benchmark_*",
    ]
    texts = {"prompts/auditor.md": prompts.AUDITOR}
    for name in [c.name for c in audit_items()] + list(SUPPORT_SKILLS):
        texts[name] = (SKILLS / name / "SKILL.md").read_text()
    found = {name: [s for s in stale if s in text] for name, text in texts.items()}
    assert not {k: v for k, v in found.items() if v}

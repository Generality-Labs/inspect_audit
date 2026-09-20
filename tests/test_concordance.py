"""The concordance gate: recorded grades must be reproduced before the auditor grades.

Pure logic (grade equivalence, the classifier, drift) plus end-to-end runs of the
gate inside a real audit eval over real mockllm logs.
"""

import json

from inspect_ai.log import EvalRevision, EvalSpec
from inspect_ai.scorer import Score

from inspect_audit._concordance import (
    AGREE,
    NOISY,
    STABLE,
    Concordance,
    _grade,
    classify,
    drift,
)


def test_grades_compare_the_way_inspect_metrics_do() -> None:
    assert _grade(1.0) == _grade(1) == _grade(True) == _grade("C") == 1.0
    assert _grade("I") == _grade(0) == _grade(False) == 0.0
    assert _grade("P") == 0.5
    assert _grade({"a": "C", "b": 0}) == _grade({"b": 0.0, "a": 1})


def _scores(*values: str, mismatch: bool = False) -> list[Score]:
    return [Score(value=v, metadata={"scorer_count_mismatch": mismatch}) for v in values]


def test_classify_validates_only_by_checking() -> None:
    assert classify(_scores(AGREE, AGREE), attempted=2, has_box=False, errors=[]) == ("validated", [])
    verdict, reasons = classify([], attempted=5, has_box=False, errors=[])
    assert (verdict, reasons) == ("unvalidated", ["checked_none"])
    verdict, reasons = classify(_scores(AGREE), attempted=3, has_box=False, errors=[])
    assert (verdict, reasons) == ("validated", ["partial_coverage"])


def test_classify_blocks_on_stable_disagreement_unless_a_box_could_explain_it() -> None:
    assert classify(_scores(AGREE, STABLE), attempted=2, has_box=False, errors=[])[0] == "blocked"
    assert classify(_scores(AGREE, STABLE), attempted=2, has_box=True, errors=[])[0] == "inconclusive"


def test_classify_noise_never_blocks_and_mismatch_never_validates() -> None:
    verdict, reasons = classify(_scores(AGREE, NOISY), attempted=2, has_box=False, errors=[])
    assert (verdict, reasons) == ("validated", ["noise"])
    verdict, reasons = classify(_scores(AGREE, mismatch=True), attempted=1, has_box=False, errors=[])
    assert (verdict, reasons) == ("unvalidated", ["scorer_count_mismatch"])


def _header(**overrides) -> EvalSpec:
    fields = dict(
        task="bench/task", dataset={}, model="mockllm/model", config={}, created="2026-01-01T00:00:00",
        packages={"inspect_evals": "0.1.0"}, task_args={"difficulty": "hard"},
        revision=EvalRevision(type="git", origin="https://x/y", commit="abc"),
    )
    return EvalSpec(**{**fields, **overrides})


def test_drift_flags_package_and_arg_differences() -> None:
    d = drift(_header(), task_args={"difficulty": "easy"})
    assert d["packages"]["inspect_evals"]["logged"] == "0.1.0"
    assert d["args"] == {"difficulty": {"logged": "hard", "resolved": "easy"}}
    assert drift(_header(packages={}), task_args={"difficulty": "hard"}) == {"packages": {}, "args": {}}


# ---- end to end: the gate inside a real audit eval ------------------------------


def _audit(source_log, tmp_path, body, task=None, mangle=None):
    """Run a real audit eval (local sandbox, mockllm) whose auditor is a scripted solver."""
    from inspect_ai import eval
    from inspect_ai.solver import Generate, Solver, TaskState, solver
    from test_helpers.logs import fixture_task

    from inspect_audit._audit import audit_task

    @solver
    def scripted() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            await body(state)
            return state

        return solve

    audited = audit_task(
        task or fixture_task("graded_task"), source_log, solver=scripted(), sandbox="local",
        items=["gold-answer"],
    )
    for staged in audited.dataset:
        staged.files = None  # nothing to stage into a local sandbox
        if mangle is not None:
            mangle(staged)
    log = eval(audited, model="mockllm/model", log_dir=str(tmp_path / "audit"), display="none")[0]
    assert log.status == "success", log.error
    assert log.samples
    return log


def _verdicts(log) -> list[dict]:
    return [s.scores["gold-answer"].metadata["concordance"] for s in log.samples]


async def _nothing(state) -> None:
    pass


def test_gate_validates_a_faithful_channel_and_labels_every_score(graded_log, tmp_path) -> None:
    """The fixture's grades are mixed (C, I, C) so a regrade that agrees with anything cannot pass."""
    seen = []

    async def body(state):
        seen.append(state.store_as(Concordance))

    log = _audit(graded_log, tmp_path, body)
    assert all(c.verdict == "validated" and c.checked == c.agreed == 1 for c in seen), seen
    assert all(v["verdict"] == "validated" for v in _verdicts(log))


def test_tampered_grades_block_and_grade_refuses(graded_log, tmp_path) -> None:
    from inspect_ai.log import read_eval_log, write_eval_log
    from inspect_ai.scorer import match
    from inspect_ai.tool import ToolError

    from inspect_audit._agent import grade_benchmark

    log = read_eval_log(graded_log)
    for sample in log.samples or []:
        for score in (sample.scores or {}).values():
            score.value = "I" if score.value == "C" else "C"
    tampered = str(tmp_path / "tampered.eval")
    write_eval_log(log, tampered)

    refusals = []

    async def body(state):
        try:
            await grade_benchmark([match()])(answer="ANSWER")
        except ToolError as ex:
            refusals.append(str(ex))

    audit = _audit(tampered, tmp_path, body)
    assert len(refusals) == len(audit.samples) and all("blocked" in r for r in refusals), refusals
    for v in _verdicts(audit):
        assert v["verdict"] == "blocked" and "stable_disagreement" in v["reasons"]


def test_no_attempts_is_a_label_not_a_refusal(tmp_path) -> None:
    from inspect_ai.scorer import match

    from inspect_audit._agent import grade_benchmark

    grades = []

    async def body(state):
        grades.append(json.loads(await grade_benchmark([match()])(answer="ANSWER"))["scores"]["value"])

    audit = _audit(None, tmp_path, body)
    assert grades and all(g == "C" for g in grades)
    for v in _verdicts(audit):
        assert (v["verdict"], v["reasons"]) == ("unvalidated", ["no_attempts"])


def test_an_unreadable_sliced_log_is_unvalidated(graded_log, tmp_path) -> None:
    def point_nowhere(staged) -> None:
        staged.metadata["sliced_logs"] = [str(tmp_path / "gone.eval")]

    audit = _audit(graded_log, tmp_path, _nothing, mangle=point_nowhere)
    for v in _verdicts(audit):
        assert v["verdict"] == "unvalidated"
        assert any(r.startswith("unreadable_log") for r in v["reasons"])


def test_judge_noise_is_reported_and_never_blocks(graded_log, tmp_path) -> None:
    """A scorer whose grade flips on every call disagrees, but its resamples vary: noise."""
    from collections import defaultdict

    from inspect_ai import Task
    from inspect_ai.dataset import MemoryDataset, Sample
    from inspect_ai.scorer import Target, accuracy, scorer

    calls: dict = defaultdict(int)

    @scorer(metrics=[accuracy()], name="match")
    def flipping():
        async def score(state, target: Target) -> Score:
            # first call disagrees with the recorded grade; resamples alternate, so
            # they differ from the first regrade: noise, not a fault
            calls[state.sample_id] += 1
            recorded = state.scores["match"].value
            other = "I" if recorded == "C" else "C"
            return Score(value=other if calls[state.sample_id] % 2 else recorded)

        return score

    noisy_task = Task(
        name="graded_task",
        dataset=MemoryDataset([Sample(id=i, input=f"q{i}", target="ANSWER") for i in (1, 2, 3)]),
        scorer=flipping(),
    )
    audit = _audit(graded_log, tmp_path, _nothing, task=noisy_task)
    verdicts = _verdicts(audit)
    assert all(v["verdict"] == "validated" for v in verdicts), verdicts
    assert any("noise" in v["reasons"] for v in verdicts), verdicts


def test_duplicate_named_scorers_pair_positionally(tmp_path) -> None:
    """Two scorers named `match` are keyed `match`, `match1` by inspect; order pairs them."""
    from inspect_ai import Task, eval
    from inspect_ai.dataset import MemoryDataset, Sample
    from inspect_ai.scorer import includes, match

    def task() -> Task:
        return Task(
            name="dupes",
            dataset=MemoryDataset([Sample(id=1, input="q", target="ANSWER")]),
            scorer=[match(), match(location="begin"), includes()],
        )

    source = eval(task(), model="mockllm/model", log_dir=str(tmp_path / "src"), display="none")[0].location
    audit = _audit(source, tmp_path, _nothing, task=task())
    for v in _verdicts(audit):
        assert v["verdict"] == "validated", v

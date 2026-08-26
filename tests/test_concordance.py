"""Proving the audit's own grade channel before it accuses the benchmark.

The pure logic here -- value normalisation, the classification of replay
results -- decides whether a cell's verdicts may stand. The end-to-end replay
against a real benchmark box is under Docker (`test_run.py`).
"""

from inspect_ai.log import EvalRevision, EvalSpec

from inspect_audit._concordance import (
    ScorerConcordance,
    _scorer_name,
    classify,
    normalize_value,
    resolution_report,
)


def test_normalize_collapses_equivalent_grade_shapes() -> None:
    assert normalize_value(1.0) == normalize_value(1) == "1"
    assert normalize_value("C") == normalize_value(" C ") == "C"
    assert normalize_value(True) == "True"
    assert normalize_value(0.5) == "0.5"
    # dicts compare by content regardless of key order
    assert normalize_value({"a": 1, "b": 2}) == normalize_value({"b": 2, "a": 1})


def con(scorer: str, checked: int, agreed: int, *, stable: int = 0, noisy: int = 0) -> ScorerConcordance:
    d = lambda n: [{"sample": str(i)} for i in range(n)]  # noqa: E731 - test brevity
    return ScorerConcordance(
        scorer=scorer, checked=checked, agreed=agreed,
        stable_disagreements=d(stable), noisy_disagreements=d(noisy),
    )


def test_perfect_replay_validates() -> None:
    verdict, reasons = classify([con("match", 10, 10)], has_box=False)
    assert verdict == "validated"


def test_stable_disagreement_without_a_box_blocks() -> None:
    # a deterministic, box-free scorer we cannot reproduce is OUR bug --
    # this is the cluster-1 catch, and it must block grade-dependent verdicts
    verdict, reasons = classify([con("match", 10, 4, stable=6)], has_box=False)
    assert verdict == "blocked"
    assert "reconstruction does not reproduce" in " ".join(reasons)


def test_disagreement_with_a_box_is_inconclusive_not_a_fault() -> None:
    # a box's end-state is not reproducible from a transcript, so a stable
    # disagreement there is inconclusive, not a reconstruction failure
    verdict, reasons = classify([con("test_pass", 10, 4, stable=6)], has_box=True)
    assert verdict == "inconclusive"
    assert "not reproducible from the transcript" in " ".join(reasons)


def test_judge_noise_never_blocks() -> None:
    # disagreements whose resamples flip are the scorer's own noise, reported as
    # a measured floor, never blocking
    verdict, reasons = classify([con("model_graded", 10, 7, noisy=3)], has_box=False)
    assert verdict == "validated"
    assert "scorer noise" in " ".join(reasons)


def test_one_flaky_sample_does_not_exempt_a_scorer_with_stable_faults() -> None:
    # the old bug: a single flipping sample marked the whole scorer nondeterministic
    # and exempt. Now a scorer with BOTH noise and stable faults still blocks.
    verdict, _ = classify([con("match", 10, 6, stable=3, noisy=1)], has_box=False)
    assert verdict == "blocked"


def test_mixed_scorers_block_on_the_deterministic_one() -> None:
    verdict, _ = classify(
        [con("model_graded", 10, 7, noisy=3), con("match", 10, 3, stable=7)],
        has_box=False,
    )
    assert verdict == "blocked"


def test_resolution_report_flags_package_and_arg_drift() -> None:
    header = EvalSpec.model_construct(
        task="t",
        task_args={"difficulty": "hard"},
        # a version that is certainly not what is installed
        packages={"inspect_ai": "0.0.1-not-installed"},
        revision=EvalRevision(type="git", origin="o", commit="abc123", dirty=False),
    )
    report = resolution_report(header, task_args={"difficulty": "easy"})

    assert report.drifted
    assert report.package_drift["inspect_ai"]["logged"] == "0.0.1-not-installed"
    assert report.arg_drift["difficulty"] == {"logged": "hard", "resolved": "easy"}
    assert report.revisions[0]["commit"] == "abc123"


def test_resolution_report_clean_when_aligned() -> None:
    header = EvalSpec.model_construct(
        task="t", task_args={"difficulty": "hard"}, packages={}, revision=None
    )
    assert not resolution_report(header, task_args={"difficulty": "hard"}).drifted


def test_scorer_name_matches_the_recorded_score_key() -> None:
    from inspect_ai.scorer import match

    # the log keys scores by the scorer's registry name, not its inner function
    assert _scorer_name(match()) == "match"

"""Which logs count as runs of the declared task."""

from pathlib import Path


def test_a_bare_task_name_is_the_same_task_as_the_prefixed_one() -> None:
    """A run started from its file records `cvebench` (FRICTION 61); a task rebuilt with
    changed code is named `scicode` (FRICTION 145). Both are the installed task."""
    from inspect_audit._logs import _same_task

    assert _same_task("inspect_evals/gsm8k", "inspect_evals/gsm8k")
    assert _same_task("gsm8k", "inspect_evals/gsm8k")
    assert _same_task("inspect_evals/gsm8k", "gsm8k")
    assert not _same_task("other/gsm8k", "inspect_evals/gsm8k")
    assert not _same_task("mmlu", "inspect_evals/gsm8k")


def test_the_auditor_reads_a_jobs_log_differences_when_it_collects(tmp_path: Path) -> None:
    import asyncio

    from inspect_ai import Task
    from inspect_ai import eval as inspect_eval
    from inspect_ai.dataset import Sample

    from inspect_audit._audit import _log_line
    from inspect_audit._logs import LOG_DIFFERENCES

    difference = "a.eval records task 'other/task', not the declared 'bench/task'"
    [log] = inspect_eval(
        Task(name="job", dataset=[Sample(input="q")], metadata={LOG_DIFFERENCES: [difference]}),
        model="mockllm/model",
        log_dir=str(tmp_path),
        display="none",
    )
    line = asyncio.run(_log_line(Path(log.location)))
    assert line.splitlines()[1] == f"    differs: {difference}"


def test_a_task_namespace_resolves_to_the_distribution_that_provides_it(monkeypatch):
    """Epoch's `bench/...` tasks come from the distribution `epoch-bench`; the namespace
    alone is nothing pip installed, so the identity check must look up the provider."""
    from inspect_audit import _logs

    monkeypatch.setattr(
        "importlib.metadata.packages_distributions",
        lambda: {"bench": ["epoch-bench"], "inspect_evals": ["inspect_evals"]},
    )
    assert _logs.distribution_of("bench/simpleqa_verified_anti_abstention") == "epoch-bench"
    assert _logs.distribution_of("inspect_evals/simpleqa") == "inspect_evals"
    assert _logs.distribution_of("cvebench/cvebench") == "cvebench"
    assert _logs.distribution_of("bare_task") is None

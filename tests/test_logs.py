"""Which logs count as runs of the declared task."""


def test_a_run_started_from_its_file_is_the_same_task_as_the_installed_one() -> None:
    """CVE-bench's logs say `cvebench` and `src/cvebench/cvebench.py`; its package registers
    `cvebench/cvebench` (FRICTION 61). Here with an installed benchmark of the same shape."""
    import inspect_evals.gsm8k  # noqa: F401  registers the task

    from inspect_audit._logs import _same_task

    assert _same_task("gsm8k", "src/inspect_evals/gsm8k/gsm8k.py", "inspect_evals/gsm8k")
    assert _same_task("inspect_evals/gsm8k", None, "inspect_evals/gsm8k")
    # another file, no file, or another function are not it
    assert not _same_task("gsm8k", "src/elsewhere/gsm8k.py", "inspect_evals/gsm8k")
    assert not _same_task("gsm8k", None, "inspect_evals/gsm8k")
    assert not _same_task("mmlu", "src/inspect_evals/gsm8k/gsm8k.py", "inspect_evals/gsm8k")


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

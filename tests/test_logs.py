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

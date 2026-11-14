"""Can Inspect Auditor take each of these inspect_evals benchmarks? No model calls.

For each task: read its items as the auditor's box does, run it on Inspect's mock model
for one item (in its own sandbox where it has one), regrade that log with its own
scorers, and build the investigation of that item. A failure here means an audit of
the benchmark would break before it spent anything. Opt-in (`-m sweep`): it downloads
datasets and, for sandboxed tasks, needs Docker. CI runs it on demand and weekly.
"""

import asyncio
from pathlib import Path

import pytest
from inspect_ai import eval
from test_helpers.box import local_box

from inspect_audit import _in_box
from inspect_audit._registry import resolve_source
from inspect_audit._regrade import ERROR, regrade
from inspect_audit._task import investigation_task

pytestmark = pytest.mark.sweep

# varied: multiple choice, open answers, model-graded-free scoring, and two sandboxed
# coding tasks (their compose is merged with the investigator's). No cyber or harm
# benchmarks, nothing gated.
# (task, the module that defines it: declared, as an operator declares benchmark.source)
TASKS = [
    ("inspect_evals/arc_easy", "inspect_evals.arc.arc"),
    ("inspect_evals/boolq", "inspect_evals.boolq.boolq"),
    ("inspect_evals/drop", "inspect_evals.drop.drop"),
    ("inspect_evals/hellaswag", "inspect_evals.hellaswag.hellaswag"),
    ("inspect_evals/commonsense_qa", "inspect_evals.commonsense_qa.commonsense_qa"),
    ("inspect_evals/mmlu_0_shot", "inspect_evals.mmlu.mmlu"),
    ("inspect_evals/piqa", "inspect_evals.piqa.piqa"),
    ("inspect_evals/winogrande", "inspect_evals.winogrande.winogrande"),
    ("inspect_evals/humaneval", "inspect_evals.humaneval.humaneval"),
    ("inspect_evals/mbpp", "inspect_evals.mbpp.mbpp"),
]


@pytest.mark.parametrize(("task", "source"), TASKS)
def test_an_audit_can_take_the_benchmark(task: str, source: str, tmp_path: Path) -> None:
    # 1. its items, read the way the auditor's box reads them (real subprocess)
    with local_box():
        found = asyncio.run(_in_box.population(task, {}, source))
    assert found["ids"], f"{task} has no items"
    first = found["ids"][0]

    # 2. one attempt on the mock model, through the benchmark's own task and scorers
    selection = {"limit": 1} if found["positional"] else {"sample_id": [first]}
    [log] = eval(
        task, model="mockllm/model", log_dir=str(tmp_path / "logs"), display="none", **selection
    )
    assert log.status == "success", log.error
    assert log.samples and log.samples[0].scores, f"{task} scored nothing"

    # 3. the regrade path: its scorers again, on that log
    rows = regrade(task, logs=[log.location], resamples=1)
    # agreement, noise or "needs its box" are all answers; an error is not
    assert rows and all(r.status != ERROR for r in rows), rows

    # 4. the investigation of that item builds (its sandbox merged with ours)
    investigation = investigation_task(
        task,
        samples=[first],
        checks=["gold-answer"],
        logs=[log.location],
        judge={},
        source=resolve_source(source),
    )
    assert investigation.dataset and len(investigation.dataset) == 1

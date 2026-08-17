#!/usr/bin/env python3
"""Pack one SimpleQA Verified sample into a self-contained audit cell.

Usage: python build_cell.py <item_id>

SQAV is the first (no-sandbox) benchmark. Produces cells/sqav-<item>/ following the
standard audit-cell contract. The packer piggybacks on the local data snapshots for
the sample, the gold, and the recorded field attempts, and copies the full benchmark
code (incl. the grader) in for the auditor to read and run.
"""
import sys, json, shutil
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent
GEN = ROOT.parent  # generality workspace
MATRIX = GEN / "data-snapshots/analysis/out/simpleqa_matrix.parquet"
META = GEN / "data-snapshots/analysis/out/simpleqa_item_meta.parquet"
IE = GEN / "audits/simpleqa/.venv/lib/python3.12/site-packages/inspect_evals"
BENCH_SRC = IE / "simpleqa"
# the real grading logic the scorer delegates to (imported by scorer.py / simpleqa.py) —
# copied in so the auditor can actually READ it, not just the thin wrapper
GRADER_DEPS = [IE / "utils" / "scorers.py", IE / "metadata.py"]
VENV_PY = GEN / "audits/simpleqa/.venv/bin/python"  # system python3 lacks inspect_ai/pandas
AUDIT_TEMPLATE = ROOT / "AUDIT.template.md"

ACTIVE_CONFIG = f"""# Active grading configuration (what is actually in force)

- scorer: `tool` -> `simpleqa_schema_tool_scorer` (see `simpleqa.py`).
- grader template: **VERIFIED** (`SIMPLEQA_VERIFIED_GRADER_TEMPLATE` in `grader_templates.py`),
  NOT the base `SIMPLEQA_GRADER_TEMPLATE`. They differ materially — e.g. the numeric
  "acceptable range" rule exists only in the VERIFIED template. Read the VERIFIED one.
- grader model: **gpt-4.1 at temperature 1.0** (`paper_config/simpleqa_verified.yaml`) — the
  grader is a STOCHASTIC LLM judge, not deterministic. "A wrong answer can score" is
  therefore probabilistic; weigh how reliably, not just whether.
- the grading logic is in `grader_deps/scorers.py` (`schema_tool_graded_scorer`) and
  `grader_deps/metadata.py` (`load_eval_metadata`) — copied here so you can read them.
- to RUN the grader you need the project venv python: `{VENV_PY}` (the system `python3`
  lacks `inspect_ai`/`pandas`).
"""

GRADE_MAP = {"C": "CORRECT", "I": "INCORRECT", "N": "NOT_ATTEMPTED"}


def build(item: int) -> Path:
    m = pd.read_parquet(MATRIX)
    m = m[m["item"] == item]
    if m.empty:
        sys.exit(f"item {item} not found in matrix")
    meta = pd.read_parquet(META).set_index("item").loc[item]
    gold = m["target"].iloc[0]
    question = m["question"].iloc[0]

    cell = ROOT / "cells" / f"sqav-{item}"
    if cell.exists():
        shutil.rmtree(cell)
    (cell / "reference").mkdir(parents=True)
    (cell / "attempts").mkdir()

    # task.json — benchmark-native fields ONLY (no prior audit verdicts: avoid anchoring)
    (cell / "task.json").write_text(json.dumps({
        "benchmark": "simpleqa_verified",
        "sample_id": int(item),
        "question": question,
        "target": gold,
        "topic": meta["topic"],
        "answer_type": meta["answer_type"],
    }, indent=2, ensure_ascii=False))

    # reference/solution.md — gold + benchmark-provided evidence URLs.
    # NOTE: meta["urls"] is a comma-joined string, not a list — split it so each URL is
    # its own bullet (otherwise the auditor curls one mashed-together address).
    raw = meta["urls"]
    parts = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
    urls = [u.strip() for u in parts if str(u).strip()]
    (cell / "reference" / "solution.md").write_text(
        f"# Recorded gold answer\n\n{gold}\n\n"
        "## Benchmark-provided evidence URLs\n\n"
        + "\n".join(f"- {u}" for u in urls) + "\n"
    )

    # attempts/index.jsonl — the recorded field: one line per model
    with (cell / "attempts" / "index.jsonl").open("w") as f:
        for _, r in m.iterrows():
            f.write(json.dumps({
                "model": r["model"],
                "grade": GRADE_MAP.get(r["grade"], r["grade"]),
                "answer": r["answer"],
            }, ensure_ascii=False) + "\n")

    # full benchmark code (incl. grader) for the auditor to read/run
    shutil.copytree(BENCH_SRC, cell / "benchmark", ignore=shutil.ignore_patterns("__pycache__"))
    # + the delegated grading logic (not part of the simpleqa package) so the grader is readable
    (cell / "benchmark" / "grader_deps").mkdir()
    for dep in GRADER_DEPS:
        shutil.copy(dep, cell / "benchmark" / "grader_deps" / dep.name)
    (cell / "benchmark" / "ACTIVE_CONFIG.md").write_text(ACTIVE_CONFIG)

    # the shared audit procedure + an empty verdict to fill
    shutil.copy(AUDIT_TEMPLATE, cell / "AUDIT.md")
    (cell / "verdict.json").write_text("{}\n")

    print(f"built {cell.relative_to(GEN)}  ({len(m)} attempts, gold={gold!r})")
    return cell


if __name__ == "__main__":
    build(int(sys.argv[1]))

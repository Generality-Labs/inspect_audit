"""Score an audit of defect_bench against its planted labels; write a CSV beside the log.

  uv run python evals/defect_bench/score_audit.py <audit .eval log>

One row per (item, check): the label, the auditor's grade, and whether they agree.
Then per check: agreements, and of the planted defects how many were found.
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path

from defect_bench import CLEAN, ITEMS
from inspect_ai.log import read_eval_log

EXPECTED = {sid: ({**CLEAN, **planted}, planted) for sid, _, _, planted, _ in ITEMS}


def main(path: str) -> None:
    log = read_eval_log(path)
    rows = []
    for sample in log.samples or []:
        expected, planted = EXPECTED[str(sample.id)]
        for check, score in (sample.scores or {}).items():
            name = check.removesuffix("_scorer")
            if name not in expected:
                continue
            got = str(score.value)
            rows.append(
                {
                    "item": sample.id,
                    "check": name,
                    "planted": name in planted,
                    "expected": expected[name],
                    "got": got,
                    "agree": got == expected[name],
                }
            )
    out = Path(path).with_suffix(".defects.csv")
    with open(out, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["item"])
        writer.writeheader()
        writer.writerows(rows)
    by_check: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_check[row["check"]].append(row)
    for check, checked in sorted(by_check.items()):
        planted = [r for r in checked if r["planted"]]
        found = sum(r["agree"] for r in planted)
        clean = [r for r in checked if not r["planted"]]
        false_alarms = sum(not r["agree"] for r in clean)
        print(
            f"{check}: {sum(r['agree'] for r in checked)}/{len(checked)} agree; "
            f"planted found {found}/{len(planted)}; clean items misjudged {false_alarms}/{len(clean)}"
        )
    print(f"wrote {out}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main(sys.argv[1])

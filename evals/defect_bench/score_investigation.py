"""Score an investigation of defect_bench against labels.json; write a CSV beside the log.

  uv run python evals/defect_bench/score_investigation.py <investigation .eval log>

The population is every (item, check) the labels define, not what the log holds:
a missing sample or verdict is counted as MISSING, never dropped from the
denominator. One row per pair; then per check: agreements, planted defects found,
clean items misjudged, missing.
"""

import csv
import json
import sys
from pathlib import Path

from inspect_ai.log import read_eval_log

LABELS = json.loads((Path(__file__).parent / "labels.json").read_text())
MISSING = "MISSING"


def expected() -> dict[tuple[str, str], tuple[str, bool]]:
    """(item, check) -> (expected grade, whether a defect was planted there)."""
    pairs = {}
    for item, planted in LABELS["planted"].items():
        for check, clean in LABELS["clean"].items():
            pairs[(item, check)] = (planted.get(check, clean), check in planted)
    return pairs


def main(path: str) -> None:
    log = read_eval_log(path)
    got: dict[tuple[str, str], str] = {}
    for sample in log.samples or []:
        for check, score in (sample.scores or {}).items():
            got[(str(sample.id), check)] = str(score.value)
    rows = []
    for (item, check), (want, planted) in sorted(expected().items()):
        grade = got.get((item, check), MISSING)
        rows.append(
            {
                "item": item,
                "check": check,
                "planted": planted,
                "expected": want,
                "got": grade,
                "outcome": "missing"
                if grade == MISSING
                else "agree"
                if grade == want
                else "disagree",
            }
        )
    out = Path(path).with_suffix(".defects.csv")
    with open(out, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for check in LABELS["clean"]:
        mine = [r for r in rows if r["check"] == check]
        planted = [r for r in mine if r["planted"]]
        clean = [r for r in mine if not r["planted"]]
        print(
            f"{check}: {sum(r['outcome'] == 'agree' for r in mine)}/{len(mine)} agree; "
            f"planted found {sum(r['outcome'] == 'agree' for r in planted)}/{len(planted)}; "
            f"clean misjudged {sum(r['outcome'] == 'disagree' for r in clean)}/{len(clean)}; "
            f"missing {sum(r['outcome'] == 'missing' for r in mine)}/{len(mine)}"
        )
    print(f"wrote {out}")


if __name__ == "__main__":
    main(sys.argv[1])

"""Epoch AI's benchmark review rubric (v1), as Laurence Wroe transcribed it in `rubric.yaml`.

Every row of Epoch's form is a rating in the auditor's findings, keyed by the row's id
and labelled with one of the row's statuses. The verdict is not the auditor's to write:
it follows from the rows by Epoch's rule (`verdict`), and a submission whose rows break
that rule is refused (`validate`).

The scoring row's status is checked against its measured defect rate (`measure`).
"""

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from inspect_audit.package import Findings, Rating, Rubric

from .measure import DefectRate, defect_rate

HERE = Path(__file__).parent
SOURCE = HERE / "rubric.yaml"
SKILL = HERE / "skills/epoch-rubric/SKILL.md"
NOT_REVIEWED = "Not Reviewed"
FLAG = "Flag"
GUIDE = (
    "Rate every row of Epoch's form in auditor.ratings, keyed by the row id and labelled "
    "with one of its statuses, as the epoch-rubric skill says. Stop at the first Flag: "
    "every row after it is Not Reviewed. Give each broader finding the id of its row as "
    "its criterion. The verdict is derived from the rows; do not write one."
)


@cache
def method() -> dict[str, Any]:
    return yaml.safe_load(SOURCE.read_text())


def rows() -> list[dict[str, Any]]:
    return list(method()["checks"])


def gate() -> dict[str, Any]:
    return rows()[0]


def minimum() -> list[dict[str, Any]]:
    return [r for r in rows() if r["section"].startswith("2.")]


def quality() -> list[dict[str, Any]]:
    return [r for r in rows() if r["section"].startswith("3.")]


def stage(target: Path) -> None:
    """The form as the auditor works from it: rows, statuses, guidance and the verdict rule."""
    (target / "rubric.json").write_text(
        json.dumps(
            {
                "id": method()["id"],
                "version": method()["version"],
                "source": method()["source"],
                "verdict": method()["verdict"]["rule"],
                "sampling": method()["sampling"],
                "rows": [
                    {
                        k: r[k]
                        for k in ("id", "section", "name", "question", "guidance", "scale")
                        if k in r
                    }
                    for r in rows()
                ],
            },
            indent=2,
        )
    )


def _label(ratings: dict[str, Rating], row: dict[str, Any]) -> str:
    return ratings[row["id"]].label if row["id"] in ratings else NOT_REVIEWED


def threshold() -> dict[str, Any]:
    return next(r for r in rows() if r.get("threshold"))["threshold"]


def measured(findings: Findings) -> DefectRate:
    """The scoring row's defect rate over the threshold's scoring checks."""
    return defect_rate(findings, list(threshold()["checks"]))


def exceeded(rate: DefectRate) -> bool | None:
    bound = float(threshold()["flag_when"].removeprefix(">=").strip())
    return None if rate.value is None else rate.value >= bound


def verdict(findings: Findings) -> tuple[str, list[str]]:
    """Epoch's rule: NEI, Flawed, Incomplete or Verified, with the reasons."""
    ratings = findings.auditor.ratings
    stop_at = method()["verdict"]["procedure"]["gate"]["stop_at"]
    reviewability = _label(ratings, gate())
    if reviewability == stop_at:
        return "NEI", [f"reviewability is {stop_at}: not enough information to review"]
    if reviewability == NOT_REVIEWED:
        return "Incomplete", ["reviewability has not been assessed"]
    flagged = [r["name"] for r in minimum() if _label(ratings, r) == FLAG]
    if flagged:
        return "Flawed", [f"flagged: {', '.join(flagged)}"]
    pending = [r["name"] for r in minimum() if _label(ratings, r) == NOT_REVIEWED]
    if pending:
        return "Incomplete", [f"minimum standard not reviewed: {', '.join(pending)}"]
    # a scoring Pass stands on a measured rate: with no item settled, the review is unfinished
    rate = measured(findings)
    if rate.value is None:
        return "Incomplete", [f"scoring passes without a measure: {rate.line()}"]
    return "Verified", [
        f"reviewability {reviewability}; every minimum-standard check passes",
        rate.line(),
    ]


def validate(findings: Findings) -> None:
    """Every row rated with one of its statuses, and the rows read as Epoch's form allows."""
    ratings = findings.auditor.ratings
    known = {r["id"]: r for r in rows()}
    if unknown := sorted(set(ratings) - set(known)):
        raise ValueError(
            f"Unknown Epoch rows {', '.join(unknown)}: ratings are keyed by row id "
            f"({', '.join(known)}); the verdict is derived, never rated"
        )
    if missing := [r for r in known if r not in ratings]:
        raise ValueError(
            f"Rate every row, Not Reviewed where the review stopped: missing {', '.join(missing)}"
        )
    for key, rating in ratings.items():
        if rating.label not in known[key]["scale"]:
            raise ValueError(
                f"{key}={rating.label} is not a status of this row: {', '.join(known[key]['scale'])}"
            )
    stop_at = method()["verdict"]["procedure"]["gate"]["stop_at"]
    order = [gate(), *minimum()]
    if ratings[gate()["id"]].label == stop_at:
        after = [r["id"] for r in rows()[1:] if ratings[r["id"]].label != NOT_REVIEWED]
    else:
        first = next((i for i, r in enumerate(order) if ratings[r["id"]].label == FLAG), None)
        after = (
            []
            if first is None
            else [
                r["id"]
                for r in [*order[first + 1 :], *quality()]
                if ratings[r["id"]].label != NOT_REVIEWED
            ]
        )
    if after:
        raise ValueError(
            f"The review stops at the first Flag or an Inadequate reviewability: "
            f"{', '.join(after)} must be Not Reviewed"
        )
    for row in minimum():
        rating = ratings[row["id"]]
        # a Flag states its measured effect on the headline results
        if (
            row.get("flag_requires_impact")
            and rating.label == FLAG
            and not re.search(r"\d", rating.explanation)
        ):
            raise ValueError(
                f"{row['id']} is flagged: its explanation must state the measured impact "
                "on the headline results, with numbers"
            )
    scoring = next(r for r in rows() if r.get("threshold"))
    rating = ratings[scoring["id"]]
    rate = measured(findings)
    over = exceeded(rate)
    if (
        rating.label in (FLAG, "Pass")
        and over is not None
        and (rating.label == FLAG) != over
        and not rating.explanation.startswith("Threshold override:")
    ):
        raise ValueError(
            f"{scoring['id']} is {rating.label} but the {rate.line().lower()} is "
            f"{'at or above' if over else 'below'} the threshold "
            f"({threshold()['flag_when']}): change the status, or start its explanation "
            '"Threshold override:" and say why'
        )
    for finding in findings.auditor.findings:
        if finding.criterion not in known:
            raise ValueError(
                f"Each finding names its Epoch row as its criterion ({', '.join(known)})"
            )


def skill_text() -> str:
    """The rating skill, generated from the rubric so the two cannot disagree."""
    lines = [
        "---",
        "name: epoch-rubric",
        "description: Epoch AI's benchmark review form (Rubric v1), its rows and statuses, "
        "and how the verdict follows from them. Read before rating.",
        "---",
        "",
        f"Source: {method()['source']}. {method()['notes'].strip()}",
        "",
        "## Rating",
        "",
        GUIDE,
        "",
        f"Verdict: {method()['verdict']['rule'].strip()}",
        "",
        f"Sampling: {method()['sampling']['sample'].strip()}",
        "",
        "## Rows",
    ]
    for row in rows():
        lines += [
            "",
            f"### `{row['id']}`: {row['name']} ({row['section']})",
            "",
            row["question"].strip(),
            "",
            f"Statuses: {', '.join(row['scale'])}.",
            "",
            row["guidance"].strip(),
        ]
    return "\n".join(lines) + "\n"


def rubric() -> Rubric:
    return Rubric(skill=SKILL.parent, stage=stage, guide=GUIDE, validate=validate)


if __name__ == "__main__":
    SKILL.write_text(skill_text())

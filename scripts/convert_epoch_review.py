"""Convert a pre-records Epoch bundle (review.json) into the records the methodology format uses.

    python scripts/convert_epoch_review.py <published bundle dir> <out dir> [--impact check=text ...]

review.json carried the rubric rows and the prose in one record and filed findings under
row names. The records split them: profile.json, assessments.json, sampling.json,
prose.json, and findings.json filed by methodology check id. Bundles written before the
impact rule have Flags without an impact statement; pass one per flagged check, taken
from that row's notes, or the conversion refuses.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from inspect_audit._epoch import FORMAT, METHODOLOGY, TEMPLATE, assess
from inspect_audit.methodology import load_methodology

ROW_TO_CHECK = {
    "scoring": "scoring_integrity",
    "consistency": "benchmark_consistency",
    "elicitation": "elicitation_adequacy",
    "bias": "evaluation_bias",
}
PROSE = (
    "summary",
    "methodology",
    "interpretation",
    "task_analysis",
    "elicitation_and_scaffolding",
    "limitations",
    "recommendations",
    "representative_errors",
    "error_examples",
)


def convert(bundle: Path, out: Path, impacts: dict[str, str]) -> dict:
    review = json.loads((bundle / "review.json").read_text())
    method = load_methodology(METHODOLOGY)
    out.mkdir(parents=True, exist_ok=True)
    (out / "format.json").write_text(
        json.dumps({"format": FORMAT, "methodology": method.key, "template": TEMPLATE}, indent=2)
        + "\n"
    )
    profile = {
        "benchmark": review["benchmark"],
        "creator": review.get("benchmark_creator", ""),
        "review_date": review.get("review_date", ""),
    }
    (out / "profile.json").write_text(json.dumps(profile, indent=2) + "\n")

    rows: dict[str, dict] = {}
    r = review["reviewability"]
    rows["reviewability"] = {"level": r["level"], "notes": r["notes"], "evidence": r["evidence"]}
    coverage = (bundle / "coverage.json").is_file()
    for row in review["minimum_standard"]:
        check = ROW_TO_CHECK[row["id"]]
        rec = {"level": row["status"], "notes": row["notes"], "evidence": row["evidence"]}
        if row["status"] == "Flag":
            if check not in impacts:
                raise SystemExit(
                    f"{check} is a Flag without an impact statement; pass --impact {check}=..."
                )
            rec["impact"] = impacts[check]
        if row.get("threshold_override_reason"):
            rec["threshold_override_reason"] = row["threshold_override_reason"]
        if row.get("inspected") is not None and not coverage:
            rec["measure"] = {
                "name": method.sampling.measure.name,
                "numerator": row["with_defect"],
                "denominator": row["inspected"],
            }
        rows[check] = rec
    for row in review["quality"]:
        rec = {"level": row["status"], "notes": row["notes"], "evidence": row["evidence"]}
        if row.get("fields"):
            rec["fields"] = row["fields"]
        rows[row["id"]] = rec
    findings = json.loads((bundle / "findings.json").read_text())
    for f in findings:
        f["check"] = ROW_TO_CHECK.get(f.get("section", ""), f.get("section", ""))
        f.pop("section", None)
    for check, rec in rows.items():
        rec["findings"] = [
            f["id"]
            for f in findings
            if f["check"] == check and f["status"] in ("supported", "qualified")
        ]
    assessments = [{"check": c.id, **rows[c.id]} for c in method.checks]
    (out / "assessments.json").write_text(
        json.dumps(assessments, indent=2, ensure_ascii=False) + "\n"
    )
    (out / "findings.json").write_text(json.dumps(findings, indent=2, ensure_ascii=False) + "\n")
    prose = {k: review[k] for k in PROSE if review.get(k)}
    (out / "prose.json").write_text(json.dumps(prose, indent=2, ensure_ascii=False) + "\n")
    if coverage:
        cov = json.loads((bundle / "coverage.json").read_text())
        cov.pop("sampling", None)
        (out / "coverage.json").write_text(json.dumps(cov, indent=2) + "\n")
        n = int(cov.get("denominator") or 0)
        sampling = {
            "population": n,
            "sample": n,
            "strategy": "census",
            "confirmation": "every task labelled by the per-item auditors; defects confirmed by the investigator",
        }
        (out / "sampling.json").write_text(json.dumps(sampling, indent=2) + "\n")
    derived = assess(out)
    (out / "epoch_review.md").write_text(derived.pop("page"))
    (out / "verdict.json").write_text(json.dumps(derived, indent=2) + "\n")
    return derived


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument(
        "--impact",
        action="append",
        default=[],
        help="check=text, for a Flag written before the impact rule",
    )
    args = ap.parse_args()
    impacts = dict(item.split("=", 1) for item in args.impact)
    derived = convert(args.bundle, args.out, impacts)
    print(derived["verdict"], "|", "; ".join(derived["reasons"]))

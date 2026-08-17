#!/usr/bin/env python3
"""Validate an audit cell's OUTPUTS against the contract, after an audit has run.

Usage: python validate_cell.py [cells/sqav-892 ...]   (default: all cells/*)

Catches silent contract violations (missing files, missing keys, bad tier) and applies a
mechanical version of the source-independence rule: if every web source the auditor cited to
confirm the gold is one of the benchmark's own evidence URLs, the verification is circular —
`verification_independent` must not be True.
"""
import sys, json, re
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
OUTCOMES = {"gold_correct", "gold_wrong", "multiple_valid", "flawed_question", "unverifiable"}
CONF = {"low", "medium", "high"}
REQUIRED = ["outcome", "defensible_answers", "gold_answer", "gold_defensible",
            "source_independent", "confidence"]


def domain(u: str) -> str:
    try:
        return (urlparse(u).netloc or "").lower().removeprefix("www.")
    except Exception:
        return ""


def validate(cell: Path) -> list[str]:
    fails = []
    log = cell / "audit_log.md"
    if not log.exists() or not log.read_text().strip():
        fails.append("missing/empty audit_log.md")

    vp = cell / "verdict.json"
    try:
        v = json.loads(vp.read_text())
    except Exception as e:
        return fails + [f"verdict.json unreadable: {e}"]
    if not v:
        return None  # placeholder {} from the packer — audit hasn't run yet

    for k in REQUIRED:
        if k not in v:
            fails.append(f"verdict missing key: {k}")
    if v.get("outcome") not in OUTCOMES:
        fails.append(f"bad outcome: {v.get('outcome')!r}")
    # ambiguous is an evidence-bearing defect verdict: source-conflict claims must cite the pair
    if v.get("outcome") == "multiple_valid":
        cs = v.get("conflicting_sources")
        if cs is not None and (not isinstance(cs, list) or len(cs) < 2):
            fails.append("ambiguous with conflicting_sources must cite >=2 URLs")
    if v.get("confidence") not in CONF:
        fails.append(f"bad confidence: {v.get('confidence')!r}")

    # mechanical independence check: is the cited source the benchmark's own?
    ref = (cell / "reference" / "solution.md").read_text() if (cell / "reference" / "solution.md").exists() else ""
    bench_domains = {domain(u) for u in re.findall(r"https?://\S+", ref)} - {""}
    # independence only matters when the verdict CONFIRMS the gold — a source that refutes it
    # (gold_wrong) is legitimate even if it's one of the benchmark's own cited URLs.
    # Compare full normalized URLs, not domains: a *different article* on the same site
    # (e.g. another Wikipedia page) is not circular, just weaker.
    def norm(u):
        u = u.strip().rstrip("/").split("?")[0].split("#")[0]
        return u.removeprefix("https://").removeprefix("http://").removeprefix("www.")
    bench_urls = {norm(u) for u in re.findall(r"https?://\S+", ref)}
    raw_src = ((v.get("defensible_answers") or [{}])[0]).get("source", "") or v.get("source", "")
    src_url = norm(raw_src.split()[0]) if raw_src else ""
    if (v.get("outcome") == "gold_correct" and src_url and src_url in bench_urls
            and v.get("source_independent") is True):
        fails.append(f"gold_correct claims source_independent=True but the cited source is "
                     f"one of the benchmark's own evidence URLs (circular confirmation)")
    return fails


def main(argv):
    cells = [Path(a) for a in argv] if argv else sorted((ROOT / "cells").glob("*"))
    ok = True
    for cell in cells:
        if not (cell / "verdict.json").exists():
            print(f"SKIP {cell.name}: no verdict yet")
            continue
        fails = validate(cell)
        if fails is None:
            print(f"SKIP {cell.name}: not audited yet")
            continue
        if fails:
            ok = False
            print(f"FAIL {cell.name}")
            for f in fails:
                print(f"      - {f}")
        else:
            print(f"PASS {cell.name}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main(sys.argv[1:])

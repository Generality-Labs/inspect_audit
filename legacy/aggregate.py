#!/usr/bin/env python3
"""Aggregate all audited cells into results.csv + a summary, and cross against the
original judge-panel flags (fresh_union) so we can see agreement / over-flagging.

Usage: python aggregate.py
"""
import json, csv, re
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
GEN = ROOT.parent
FLAGS = json.load(open(GEN / "audits/simpleqa/label-audit/audit_full/fresh_union.json"))
FLAGGED = {d["item"]: d for d in FLAGS}


def dom(u):
    try:
        return (urlparse(u).netloc or "").lower().removeprefix("www.")
    except Exception:
        return ""


rows = []
for cell in sorted((ROOT / "cells").glob("sqav-*")):
    vp = cell / "verdict.json"
    if not vp.exists():
        continue
    try:
        v = json.loads(vp.read_text())
    except Exception:
        continue
    if not v or "outcome" not in v:
        continue
    item = json.loads((cell / "task.json").read_text())["sample_id"]
    panel = FLAGGED.get(item)
    rows.append({
        "item": item,
        "flagged_by_panel": bool(panel),
        "panel_verdicts": "/".join(panel["verdicts"]) if panel else "",
        "outcome": v.get("outcome"),
        "confidence": v.get("confidence"),
        "correct_answer": (v.get("correct_answer") or "")[:60],
        "gold_answer": (v.get("gold_answer") or "")[:60],
        "source_independent": v.get("source_independent"),
        "source_domain": dom((v.get("source") or "").split()[0] if v.get("source") else ""),
    })

rows.sort(key=lambda r: r["item"])
out = ROOT / "results.csv"
with out.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)

# summary
from collections import Counter
n = len(rows)
oc = Counter(r["outcome"] for r in rows)
flagged = [r for r in rows if r["flagged_by_panel"]]
ctrl = [r for r in rows if not r["flagged_by_panel"]]
print(f"{n} audited cells -> {out.relative_to(GEN)}")
print("outcome distribution:", dict(oc))
print(f"\nPANEL-FLAGGED items ({len(flagged)}):", dict(Counter(r['outcome'] for r in flagged)))
print(f"  -> exonerated as gold_correct: {sum(1 for r in flagged if r['outcome']=='gold_correct')}/{len(flagged)}")
print(f"CONTROL items ({len(ctrl)}):", dict(Counter(r['outcome'] for r in ctrl)))
print(f"  -> false-flagged (not gold_correct): {sum(1 for r in ctrl if r['outcome']!='gold_correct')}/{len(ctrl)}")
defects = [r for r in rows if r["outcome"] in ("gold_wrong", "ambiguous")]
print(f"\nDEFECTS found (gold_wrong/ambiguous): {len(defects)}")
for r in defects:
    print(f"  item {r['item']:4d} [{'flagged' if r['flagged_by_panel'] else 'control'}] {r['outcome']} ({r['confidence']}) — gold={r['gold_answer']!r}")

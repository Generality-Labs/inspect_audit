#!/usr/bin/env python3
"""Land-style IRT misfit triage over the SQAV 50-model x 1000-item matrix.

Fit: model ability = logit of mean accuracy; per item, logistic regression of
correct ~ ability gives discrimination (slope) and difficulty (intercept).
Wrong-gold signature = NEGATIVE discrimination (weak models "pass" because the
gold matches the popular wrong answer; strong models "fail" by answering truly).

Outputs irt_items.csv + prints ASCII histogram of discrimination and the top
misfit items, cross-referenced against panel flags and our audit verdicts.
"""
import json
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GEN = ROOT.parent

m = pd.read_parquet(GEN / "data-snapshots/analysis/out/simpleqa_matrix.parquet")
m["y"] = (m["grade"] == "C").astype(int)

# model ability: logit of mean accuracy (clipped)
acc = m.groupby("model")["y"].mean().clip(0.02, 0.98)
theta = np.log(acc / (1 - acc))
m["theta"] = m["model"].map(theta)

def fit_item(g):
    """Logistic fit y ~ a*theta + b via Newton (50 points). Returns a, b."""
    x, y = g["theta"].to_numpy(), g["y"].to_numpy()
    if y.min() == y.max():
        return np.nan, np.nan  # degenerate: all pass or all fail
    a, b = 0.0, 0.0
    X = np.column_stack([x, np.ones_like(x)])
    w = np.zeros(2)
    for _ in range(50):
        p = 1 / (1 + np.exp(-(X @ w)))
        gvec = X.T @ (y - p)
        W = p * (1 - p)
        H = X.T @ (X * W[:, None]) + 1e-4 * np.eye(2)  # ridge for separation
        step = np.linalg.solve(H, gvec)
        w += step
        if np.abs(step).max() < 1e-8:
            break
    return w[0], w[1]

rows = []
for item, g in m.groupby("item"):
    a, b = fit_item(g)
    pr = g["y"].mean()
    # point-biserial as a fit-free sanity stat
    pb = np.corrcoef(g["theta"], g["y"])[0, 1] if 0 < pr < 1 else np.nan
    rows.append({"item": int(item), "passrate": pr, "disc": a, "pb": pb,
                 "question": g["question"].iloc[0][:80], "gold": str(g["target"].iloc[0])[:40]})
it = pd.DataFrame(rows)

# cross-reference: panel flags + our audit verdicts
panel = {d["item"] for d in json.load(open(GEN / "audits/simpleqa/label-audit/audit_full/fresh_union.json"))}
it["panel"] = it["item"].isin(panel)
verd = {}
for c in (ROOT / "cells").glob("sqav-*/verdict.json"):
    try:
        v = json.loads(c.read_text())
        if v.get("outcome"):
            verd[int(c.parent.name.split("-")[1])] = v["outcome"]
    except Exception:
        pass
it["audit"] = it["item"].map(verd).fillna("")

it.sort_values("disc").to_csv(ROOT / "irt_items.csv", index=False)

# ---- report ----
fit = it.dropna(subset=["disc"])
print(f"items: {len(it)} | fittable (0<pass<1): {len(fit)} | all-fail: {(it.passrate==0).sum()} | all-pass: {(it.passrate==1).sum()}")
neg = fit[fit.disc < 0]
print(f"NEGATIVE discrimination (wrong-gold signature): {len(neg)} items")

# ASCII histogram of discrimination
d = fit["disc"].clip(-3, 6)
bins = np.linspace(-3, 6, 28)
hist, edges = np.histogram(d, bins=bins)
peak = hist.max()
print("\ndiscrimination histogram (clipped to [-3,6]):")
for h, lo, hi in zip(hist, edges[:-1], edges[1:]):
    bar = "#" * int(round(h / peak * 60))
    mark = " <-- misfit zone" if hi <= 0 and h > 0 else ""
    print(f"{lo:6.2f}..{hi:5.2f} |{bar}{mark and ''}{' ' if not bar else ''}{h:4d}{mark}")

print("\n=== top 20 misfit items (most negative discrimination) ===")
cols = ["item", "disc", "pb", "passrate", "panel", "audit", "gold", "question"]
top = it.dropna(subset=["disc"]).sort_values("disc").head(20)
print(top[cols].to_string(index=False))

# enrichment check
flag_rate_all = it["panel"].mean()
flag_rate_neg = neg["panel"].mean() if len(neg) else float("nan")
print(f"\npanel-flag rate: all items {flag_rate_all:.1%} | negative-disc items {flag_rate_neg:.1%}")

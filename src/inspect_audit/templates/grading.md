# How this sample is graded

Scorer `{scorer}`, defined in `{module}`.

{judge_line}

**Verdict values.** This scorer can return: {values}. Counting as a pass: {passing}.

**The criteria in full** are in `../elicitation.json` under `judge` — including the
complete grading template the judge is given. Read it before deciding whether an
answer was fairly marked: a template that pins an exact string, omits a valid
alternative, or collapses "refused" into "incorrect" fails answers that were right.

**The source** is installed in this sandbox rather than copied, so read the real thing:

```
python -c "import importlib, inspect; print(inspect.getsourcefile(importlib.import_module('{module}')))"
```

{drift_line}

## What the target is, and is not

`target.txt` holds this sample's recorded `target` verbatim. It is not necessarily the
whole gold: benchmarks keep reference material in whatever shape suits them — a patch,
a test file, an acceptable numeric range, a list of accepted alternatives. Everything
this sample carries is in `../sample.json`; its metadata keys are:

{metadata_keys}

If one of those looks like reference material, treat it as part of the gold.

`sources.md`, when present, lists URLs found in this sample's metadata. Those are the
benchmark's own citations, so they are the *weakest* confirmation available: the
question was probably written from them, which makes agreement circular. An
independent source is worth more.

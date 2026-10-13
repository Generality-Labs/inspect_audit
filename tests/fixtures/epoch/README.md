# Epoch review fixtures

Two real investigations of Epoch AI's Chess Puzzles, converted from the pre-records
`review.json` form by `scripts/convert_epoch_review.py` on 2026-10-13:

- `chess-opus55`: Claude Opus 5.5 investigator, 8 October 2026, Verified.
- `chess-astra`: GPT-6 Astra investigator, 8 October 2026, Flawed on elicitation. The run
  predates the impact rule, so the Flag's `impact` was lifted verbatim from the row's notes.

`coverage.json` keeps the counts and omits the per-task rows to keep the fixtures small.
`epoch_review.md` and `verdict.json` are golden outputs of `inspect_audit._epoch.assess`.

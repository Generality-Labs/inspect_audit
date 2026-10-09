# Review decisions

Producer run files are never edited. A review decision is one object under `review/` in the store, written once and never changed: who decided what, when, and why. Rendering folds the log in time order into the view it applies to copies of the current runs: matching observations gain a suppression, matching fingerprints gain an issue id or a status. Rerunning a producer cannot lose a decision, and two writers never edit the same object, so the store works the same on a directory, S3 or R2.

Decisions are written through `inspect-audit-findings review suppress|accept|link|status|retract`, which resolves what you point at against the current view, refuses what the fold would refuse, writes the object and re-renders. Nobody writes the objects by hand.

## One decision

```json
{
  "id": "dec-20261009T041210Z-0412107f3",
  "at": "2026-10-09T04:12:10.412107Z",
  "author": "Matt Fisher <matt@generality.org>",
  "kind": "suppress",
  "reason": "struct-typed answers; inspect_dataset#26",
  "suppress": {"rule": "answer_length", "subject": "inspect_evals/stereoset", "producer": "inspect_dataset", "kind": "false_positive"}
}
```

The object key is `review/<at as YYYYMMDDTHHMMSSZ>-<id>.json`, so a listing is in time order. `kind` names the one payload present:

- `suppress`: `rule`, `subject` (`"*"` or an eval), `producer` or null, `kind` (free text: `false_positive`, `accepted_risk`, `duplicate`). Rules out every observation of the rule on the subject.
- `accept`: `issue` (`ISS-NNNN`, the next after the highest), `title`, `subject`, `fingerprints`. A problem a person has accepted, linked to the observations that evidence it by fingerprint so a rerun that observes the same thing keeps the link.
- `link`: `issue`, `url`. The latest link wins.
- `status`: `fingerprints`, `status` (`hypothesis`, `supported`, `qualified`, `retracted`). The latest status per fingerprint wins; the observation's history records who set it and why.
- `retract`: `decision`, the id of an earlier decision this one withdraws. A mistaken suppression or acceptance is undone without deleting history.

Authors are stored in full as `Name <email>`; the export shows names only.

## The fold

Suppressions accumulate. An acceptance creates an issue; a fingerprint accepted a second time moves to the later issue and the renderer prints a warning naming both decisions, which is how a race between two writers on object storage surfaces. A link to an unknown issue is a warning. An issue whose fingerprints have all moved elsewhere disappears. The Issues section of an eval's summary lists each issue with the number of current observations linked to it; an issue whose fingerprints no longer appear is shown with "no current observation" and a warning is printed, a prompt to re-check rather than evidence of a fix.

## Derived views

`suppressions.yaml` and `issues.yaml` are written from the fold on every render as read-only views; their first line is `# derived from review/; do not edit`. A store that still has hand-written versions of those files converts them once with `inspect-audit-findings review migrate --store LOCATOR`, which writes one decision per entry with the recorded author and date and is safe to run again.

You never type a fingerprint. `review accept` and `review status` take `--eval` and `--rule` (every current observation of that rule on that eval), `--id` record ids (`<run id>/<n>`, as shown in `LEADS.md` and the summaries) or `--fingerprint`, and record the fingerprints, which match observations across runs.

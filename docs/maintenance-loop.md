# The maintenance loop: from PR triage to a hill-climb

Written 2026-10-01 from the three Inspect Evals PR triages (9 September, 15 September, 1 October) and the rule-check pass that followed the last one. Those were run by hand, by agents, against briefs written for the day. This document records the process they converged on, turns it into a loop with a measurable number, and names the skills to distil so the loop runs the same way every fortnight. It is the "Rule mining" track in the [roadmap](roadmap.md).

The role it serves is the one the GL Q4 plan gives Matt: a forward-deployed engineer for Inspect Evals who develops Auditor and makes updates as Inspect Evals requires, but does not commit code to Inspect Evals. Every output of this loop is therefore an issue, a lint rule, a shared-helper proposal, a policy question or a docs change. Never a code PR against Inspect Evals.

## What the triage did, as a process

Eight steps, each of which has a home in the findings architecture.

| Step              | What happened on 1 October                                                                                                                                                              | Where it lives in Auditor                                                 |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| 1. Crawl          | Snapshot of 133 open PRs: files, bodies, reviews, checks, activity                                                                                                                      | A deterministic producer over the GitHub API (`pr-triage`, scripted part) |
| 2. Cluster        | PRs grouped by defect class: verdict parsing (22), infrastructure failure scored as a result (18), epoch metrics (10), code fences (5), unseeded shuffles (4), doc drift (6), and so on | Grouping in the store; the agentic part of `pr-triage`                    |
| 3. Mine a rule    | Per cluster: an AST shape, hits on main, estimated precision, uncovered sites, severity tier, recommended fix, known false positives                                                    | `rule-mining` skill; uncovered sites become hypothesis findings           |
| 4. Sort           | Each candidate to lint rule, shared helper, test convention, review checklist, or "needs an agent"                                                                                      | The sort decides which producer or auditor item owns the class            |
| 5. Lint issue     | One issue per strong rule on inspect-evals-lint, with the evidence (#56 to #64, #65 for the weak ones)                                                                                  | Rendered from the mined rule record                                       |
| 6. Policy issue   | One "Agree X before lint encodes it" issue per cluster where written guidance disagrees with itself (#2599 to #2602)                                                                    | `policy-question` skill; blocks the rule until closed                     |
| 7. Rule-check     | Each PR in the cluster compared with the draft rule: AGREE, PR-DEVIATES, RULE-WRONG, UNDECIDED, OUT-OF-SCOPE, with confidence and a lint fixture check                                  | `rule-check` skill; the measurement that drives the hill-climb            |
| 8. Triage actions | Merge now, re-check, needs work, close, sequence version bumps, maintainer decisions                                                                                                    | The issues view, for maintainers                                          |

Step 7 is the one that matters. It treats the PR corpus as a labelled test set for a rule. On 1 October it returned four RULE-WRONG and several UNDECIDED verdicts against draft rules that read as solid in the report. Lint rule #56 as first written would have pushed contributors to the wrong fix for sandbox reads. The policy step is not ceremony.

## The loop

```text
  merged defect-fix PRs ──► cases.yaml (eval, pre, post, class, expected finding)
                                   │
          ┌────────────────────────┴────────────────────────┐
          ▼                                                 ▼
  run producers at pre and post revisions           run auditor items on a sample
          │                                                 │
          └──────────────► recall / false positives / cost per class ◄──┘
                                   │
                           misses, sorted by rule-mining into:
                      rule │ helper │ test convention │ auditor item │ policy
                           │                                        │
                 lint issue, implement, re-measure         policy-question issue
                                                           (blocks the rule until closed)
```

**Cases.** One YAML entry per labelled defect. The three triage corpora and the 9 September VERDICT files already hold about sixty scoring cases. A merged PR that touches a scorer, metric or dataset loader becomes a candidate case automatically; the class is proposed by `pr-triage` and confirmed by a person.

```yaml
- id: CASE-2284
  eval: inspect_evals/healthbench
  pr: 2284
  class: infra_as_result            # the cluster, see the taxonomy mapping below
  check: G.1
  pre: a1b2c3d                      # revision with the defect
  post: d4e5f6a                     # revision with the fix merged
  expected:
    producer: inspect_evals_lint    # or inspect_audit_sample / inspect_audit_investigate
    rule: IESC0xx                   # or the auditor item, e.g. failure-attribution
    location: src/inspect_evals/healthbench/scorer.py:241
  verdict: AGREE                    # from rule-check, with the rule it was checked against
  held_out: false                   # some cases stay out of rule development
  source: agent_artefacts/pr_triage_2026-10-01/rule_check/A1_judge_failures.md
```

**Metric.** For each producer rule and each auditor item, over the cases of its class:

- recall at `pre`: the expected finding is present;
- false positives at `post`: the finding is gone after the fix;
- precision on main: hits that a person agrees are real, sampled;
- for auditor items, cost and wall time per case.

One table, regenerated every fortnight. Held-out cases are reported separately. This is the number the loop climbs.

**Cadence.** Fortnightly, matching the 15 September and 1 October runs. Each run: refresh cases from merges since the last run, run the measurement, triage the misses, raise or amend issues, record the table in the store beside the runs.

**What stays human.** Policy. An UNDECIDED verdict is an open question for Justin or Tania, and the rule that depends on it does not ship until the question is closed in BEST_PRACTICES or the inspect_ai scoring policy. Everything else in the loop is mechanical or agentic and is measured.

## Cluster to taxonomy mapping

So that rule-level recall rolls up into dimension-level coverage. Under `gl-audit@1`, checked against the definitions in `findings/taxonomies/gl-audit-1.json`; the v2 mapping file carries these to Implementation's contributions. `G.1` is "the grading procedure incorrectly classifies responses"; `G.4` is "otherwise equivalent responses receive different scores because of wording, formatting or presentation"; `F.1` is "tools error, malfunction or hang during episodes"; `F.4` is "the environment varies between otherwise equivalent runs".

| Cluster (class id)                                                      | Check                                                                                                                                                   | Notes                                                                                                                                |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| Verdict parsing by substring or unanchored token (`verdict_parsing`)    | G.1, G.4                                                                                                                                                | Lint #57; shared `parse_verdict` helper proposed in #2598                                                                            |
| Infrastructure or grader failure scored as a result (`infra_as_result`) | G.1, with F.1 as cause                                                                                                                                  | Lint #56; policy #2599                                                                                                               |
| Custom metric unsafe under epochs (`epoch_metric`)                      | G.1                                                                                                                                                     | Lint #58; test helper #2597; policy #2601                                                                                            |
| Grader resolves to the model under test (`grader_role`)                 | G.3                                                                                                                                                     | Lint #61; policy #2602                                                                                                               |
| Unseeded shuffle or RNG in dataset construction (`unseeded_rng`)        | F.4                                                                                                                                                     | Lint #53, #63                                                                                                                        |
| Hand-rolled code-fence extraction (`code_fence`)                        | G.4                                                                                                                                                     | Lint #62; convention first-versus-last to settle                                                                                     |
| README arguments or extras that do not exist (`doc_drift`)              | dimension `harness`, no v1 check fits (H is token, turn and time limits, not documentation); v2 `practical_reproducibility.requirements` (setup effort) | Lint #59, #60. inspect_ai drops unknown `-T` arguments silently, so a documented command runs a different configuration than it says |
| Prompt or solver construction defects (`prompt_construction`)           | D (scaffold)                                                                                                                                            | Not lintable; rendered-prompt test helper                                                                                            |
| Built-in scorer mismatched with the upstream rule (`upstream_mismatch`) | G.1                                                                                                                                                     | Not lintable; `gold-answer` auditor item, checklist "cite the upstream scoring line"                                                 |
| `dataset_samples` drift in eval.yaml (`sample_count`)                   | C                                                                                                                                                       | Header producer `header.dataset_samples` already covers it when logs exist                                                           |

"Not lintable, better caught elsewhere" in the triage report is the agentic auditor's job list. Several already exist as verdict items: first-versus-last verdict parsing is `answer-format`, upstream mismatch is `gold-answer`, empty output sent to a judge is `failure-attribution`. The triage tells us which items to run on which evals first, and the cases let us measure them the same way as lint rules.

## Skills to distil

Four skills, in `inspect_audit`, each with a frontmatter contract in the shape the auditor's item skills use, each producing a record the store can hold. `verify-scoring-change` in Inspect Evals remains the per-PR verifier they build on. The deterministic part of the first one is a producer, not a skill.

### pr-triage

```yaml
---
name: pr-triage
description: Snapshot the open PRs of an eval repository, cluster them by defect class, and propose cases for the labelled set. Run fortnightly.
metadata:
  inputs: [repo, since]
  tools: [gh]
  outputs:
    clusters: {class_id, prs[], evals[], check}
    cases: proposed cases.yaml entries for merged defect-fix PRs since `since`
    actions: merge-now, re-check, needs-work, close, sequence, decide
---
```

The crawl (open PRs, files, bodies, reviews, checks, activity CSV) is a script and should be a producer whose run records the snapshot. The skill owns the clustering and the action list. Clusters are groups in the store with PR and file locations.

### rule-mining

```yaml
---
name: rule-mining
description: Turn one defect cluster into a candidate check: a precise shape, its hits on main, estimated precision, uncovered sites, tier, recommended fix and known false positives. Decide whether it is a lint rule, a shared helper, a test convention, a review checklist item, or an auditor item.
metadata:
  inputs: [cluster, repo_revision]
  outputs:
    rule: {shape, hits, precision_estimate, tier: strong|weak, fix, false_positives[]}
    sort: lint|helper|test|checklist|auditor_item
    uncovered: hypothesis findings with code locations, for leads
    issue: the inspect-evals-lint issue body, in the format of #56
---
```

The report's "Strong" section is the output format. Uncovered sites are hypothesis findings and go into the store, so the leads file carries them to an agent for verification before anyone writes a rule around them.

### rule-check

```yaml
---
name: rule-check
description: Check a draft rule against the PRs that are its test cases. For each PR say whether its outcome agrees with the rule, and when it does not, whether the PR or the rule is wrong, or which open question decides it.
metadata:
  inputs: [draft_rule_issue, prs[], sources_of_truth[]]
  verdicts: [AGREE, PR-DEVIATES, RULE-WRONG, UNDECIDED, OUT-OF-SCOPE]
  outputs:
    per_pr: {pr, eval, head, paths_changed, rule_says, verdict, question, confidence, lint_fixture: {pre_matches, post_clean, fix_leads_right}}
    amendments: proposed changes to the draft rule
    questions: new open questions for the policy issue
---
```

The brief the agents followed on 1 October is the skill body, with its rules kept: read the sources of truth before judging (BEST_PRACTICES, the inspect_ai scoring policy, framework receipts, prior VERDICT files, and whether the head has moved); read PR code through `gh` and per-PR fetched refs, never by checking out; no probes unless a verdict cannot be reached by reading; read-only on GitHub. Each per-PR record is an Assessment-shaped record against the case.

### policy-question

```yaml
---
name: policy-question
description: Write the issue that gets a maintainer decision before a rule encodes it. Guidance that disagrees with itself, a draft rule, open questions, the PRs as test cases, and what done looks like.
metadata:
  inputs: [cluster, rule, guidance_sources[]]
  outputs:
    issue: in the format of UKGovernmentBEIS/inspect_evals#2599
    blocks: the lint issue ids that wait on it
---
```

A rule whose policy question is open is not shipped. The issue's PR table is filled in by `rule-check`, and the issue is closed when BEST_PRACTICES or the scoring policy records the decision.

## What this changes for Auditor

- The deterministic producers gain a feedback source: every fortnight, the misses on the cases say which rule to write or sharpen next.
- The agentic auditor gains a job list: the "not lintable" classes, with the evals where they recur, measured the same way.
- The store gains three record kinds it already has shapes for: cases (an Issue with pre and post revisions), rule-check verdicts (Assessments against a case), and uncovered sites (hypothesis Findings for the leads file).
- Maintainers get the backlog as rendered issue drafts and a short queue of policy questions, which is the only part that needs them.

## Not yet decided

- Where `cases.yaml` lives: beside the review files in the findings output, or in Inspect Evals under `tests/cases/` where the maintainers can see it. The loop works either way.
- Whether the fortnightly run is a GitHub Action in inspect-evals-actions or a command someone runs. Start with the command.
- How a contributor's PR is labelled with its class without asking them: a PR-template field, or `pr-triage` proposing and a maintainer confirming on merge.

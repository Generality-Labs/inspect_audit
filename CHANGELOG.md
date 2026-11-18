# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- The `epoch` audit package (`packages/epoch`, distribution `inspect-audit-epoch`): Epoch AI's benchmark review, its rubric and its page generated from the frozen findings. The rubric (checks, scales, the 20% scoring threshold, the verdict rule, the page's blocks and limits) is one description, `epoch.json`; the auditor rates its rows in `auditor.ratings` under the `epoch-review` skill, in two phases within one audit (the task audit, blind to logs, which is all an audit without logs does and can only end Flawed or Incomplete; then, when logs were supplied, the log-dependent rows in the same run), and the verdict, the defect rate from the sample labels and the rubric tables are derived at submission and publication, never written. `Rating` gains optional `fields`, `impact` and `override` for the rows that need them; the register's subject records the logs the audit was given; the package's rubric skill and staged description are given to the auditor through the package interface.
- The `grading-labels` check, phase 2's counterpart to question-labels: one investigator per item with the logs, labelling each recorded grade a false negative, false positive or grader failure, with a headline for the page. An item defective under either check counts once in the Epoch scoring row.
- `inspect-audit-epoch-review` (in the epoch package): renders the Epoch page again from what an audit saved (a `hawk download-artifacts` folder, a findings bundle, a findings.json or the audit's eval log, with the auditor's prose.json), after a renderer fix or when the pod kept nothing, without a model or a box.
- Two smoke cases for the Epoch review: `epoch-simpleqa` (phase 1, no logs) and `epoch-simpleqa-logs` (both phases, with 49 of Epoch's published SimpleQA Verified runs supplied).

### Changed

- Supplied logs that differ from the declared run (another build of the benchmark, another task name, other task arguments, another Inspect) are no longer refused by the log identity check. The difference is said instead: a warning on the job, a note to every investigator, `log_differences` in the job's log metadata (shown when the auditor collects it) and on the register's subject, and a notice on the Epoch page. An Epoch review regrades Epoch's logs with a reconstruction of a private package, which the old rule made impossible. The check also resolves a task's namespace to the distribution that provides it (`bench/...` is `epoch-bench`), so a namespace that is not itself a distribution no longer reads as "not installed".
- On a Hawk runner, `artifact_dir` defaults to the job's own artifacts folder, read from the runner's infra config, so the published report and findings are fetched with `hawk download-artifacts` instead of being lost with the pod. The eval set id is Hawk's to assign, so an operator could not name the folder in the config.
- Adopted the Generality Labs [python-project-template](https://github.com/Generality-Labs/python-project-template): CI calls the shared `python-ci` workflow on Python 3.13 (so the Hawk extra installs and type-checks), the pre-commit stack (ruff lint and format, zizmor, actionlint, shellcheck, mdformat) gates commits, basedpyright replaces mypy, dev dependencies move to a `[dependency-groups]` table, and Dependabot keeps actions and Python dependencies current.

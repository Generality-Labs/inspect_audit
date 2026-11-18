# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- The `epoch` publisher: an Epoch AI benchmark review page generated from the frozen findings. The rubric (checks, scales, the 20% scoring threshold, the verdict rule, the page's blocks and limits) is one description, `audit/epoch.json`; the auditor rates its rows in `auditor.ratings` under the `epoch-review` skill, in two phases within one audit (the task audit, blind to logs, which is all an audit without logs does and can only end Flawed or Incomplete; then, when logs were supplied, the log-dependent rows in the same run), and the verdict, the defect rate from the sample labels and the rubric tables are derived at submission and publication, never written. `Rating` gains optional `fields`, `impact` and `override` for the rows that need them; the register's subject records the logs the audit was given; a publisher may bring audit-time skills and stage its description into the workspace.
- The `grading-labels` check, phase 2's counterpart to question-labels: one investigator per item with the logs, labelling each recorded grade a false negative, false positive or grader failure, with a headline for the page. An item defective under either check counts once in the Epoch scoring row.
- A smoke case, `tests/smoke/epoch-simpleqa.eval-set.yaml`: a phase-1 Epoch review of five SimpleQA Verified items as Epoch runs them, published with the epoch publisher.

### Changed

- Adopted the Generality Labs [python-project-template](https://github.com/Generality-Labs/python-project-template): CI calls the shared `python-ci` workflow on Python 3.13 (so the Hawk extra installs and type-checks), the pre-commit stack (ruff lint and format, zizmor, actionlint, shellcheck, mdformat) gates commits, basedpyright replaces mypy, dev dependencies move to a `[dependency-groups]` table, and Dependabot keeps actions and Python dependencies current.

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- The `epoch` publisher: an Epoch AI benchmark review page generated from the frozen findings. The rubric (checks, scales, the 20% scoring threshold, the verdict rule, the page's blocks and limits) is one description, `audit/epoch.json`; the auditor rates its rows in `auditor.ratings` under the `epoch-review` skill, in two phases (the task audit without logs, which can only end Flawed or Incomplete, then the log-dependent rows with logs), and the verdict, the defect rate from the sample labels and the rubric tables are derived at submission and publication, never written. `Rating` gains optional `fields`, `impact` and `override` for the rows that need them; the register's subject records the logs the audit was given; a publisher may bring audit-time skills and stage its description into the workspace.

### Changed

- Adopted the Generality Labs [python-project-template](https://github.com/Generality-Labs/python-project-template): CI calls the shared `python-ci` workflow on Python 3.13 (so the Hawk extra installs and type-checks), the pre-commit stack (ruff lint and format, zizmor, actionlint, shellcheck, mdformat) gates commits, basedpyright replaces mypy, dev dependencies move to a `[dependency-groups]` table, and Dependabot keeps actions and Python dependencies current.

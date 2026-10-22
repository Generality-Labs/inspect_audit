# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Adopted the Generality Labs [python-project-template](https://github.com/Generality-Labs/python-project-template): CI calls the shared `python-ci` workflow on Python 3.13 (so the Hawk extra installs and type-checks), the pre-commit stack (ruff lint and format, zizmor, actionlint, shellcheck, mdformat) gates commits, basedpyright replaces mypy, dev dependencies move to a `[dependency-groups]` table, and Dependabot keeps actions and Python dependencies current.

"""Review decisions: suppressions and accepted issues, kept beside the runs and applied to copies.

Producer run files are never edited. A person records a decision here, with a reason and a
date, and rendering applies it: matching observations gain a suppression, matching
fingerprints gain an issue id. Rerunning a producer cannot lose a decision.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from .models import Finding, Provenance, Run, Suppression

SUPPRESSIONS_FILE = "suppressions.yaml"
ISSUES_FILE = "issues.yaml"


class SuppressionRule(BaseModel):
    """Rule out every observation of `rule` on `subject` (`*` for all evals), optionally for one producer."""

    model_config = ConfigDict(extra="forbid")
    rule: str
    subject: str = "*"
    producer: str | None = None
    kind: str = "false_positive"
    author: str
    reason: str
    since: date

    def matches(self, finding: Finding) -> bool:
        return (
            finding.rule == self.rule
            and self.subject in ("*", finding.subject.eval)
            and (self.producer is None or self.producer == finding.producer)
        )


class IssueEntry(BaseModel):
    """A problem a person has accepted, linked to the observations that evidence it by fingerprint."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    subject: str
    findings: list[str] = Field(min_length=1)
    author: str
    opened: date
    reason: str | None = None
    github: str | None = None


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suppressions: list[SuppressionRule] = Field(default_factory=list)
    issues: list[IssueEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_issue_per_fingerprint(self) -> Review:
        counts = Counter(fp for issue in self.issues for fp in issue.findings)
        duplicates = sorted(fp for fp, n in counts.items() if n > 1)
        if duplicates:
            raise ValueError(f"a fingerprint may belong to one issue only: {', '.join(duplicates)}")
        return self


_SUPPRESSIONS = TypeAdapter(list[SuppressionRule])
_ISSUES = TypeAdapter(list[IssueEntry])


def _load_list(path: Path, adapter: TypeAdapter[Any]) -> list[Any]:
    """The file as a validated list, or an empty list when the file is absent. Errors name the file."""
    if not path.is_file():
        return []
    loaded = yaml.safe_load(path.read_text())
    if loaded is None:
        return []
    if not isinstance(loaded, list):
        raise ValueError(f"{path} must be a YAML list")
    try:
        return adapter.validate_python(loaded)
    except ValidationError as ex:
        raise ValueError(f"{path}: {ex}") from ex


def load_review(directory: Path) -> Review:
    """`suppressions.yaml` and `issues.yaml` under `directory`; a missing file is an empty list."""
    return Review(
        suppressions=_load_list(directory / SUPPRESSIONS_FILE, _SUPPRESSIONS),
        issues=_load_list(directory / ISSUES_FILE, _ISSUES),
    )


def apply_review(runs: Sequence[Run], review: Review) -> list[Run]:
    """Copies of `runs` with suppressions attached and issue ids set. The inputs are not changed."""
    issue_of = {fp: issue.id for issue in review.issues for fp in issue.findings}
    applied: list[Run] = []
    for run in runs:
        copy = run.model_copy(deep=True)
        for finding in copy.findings:
            for rule in review.suppressions:
                if rule.matches(finding):
                    finding.suppressions.append(
                        Suppression(
                            kind=rule.kind,
                            provenance=Provenance(
                                timestamp=datetime.combine(
                                    rule.since, datetime.min.time(), tzinfo=UTC
                                ),
                                author=rule.author,
                                reason=rule.reason,
                            ),
                        )
                    )
            if finding.fingerprint in issue_of:
                finding.issue = issue_of[finding.fingerprint]
        applied.append(copy)
    return applied


def unmatched_issue_findings(review: Review, runs: Sequence[Run]) -> dict[str, list[str]]:
    """For each issue, the fingerprints it lists that no current finding carries."""
    present = {finding.fingerprint for run in runs for finding in run.findings}
    missing = {
        issue.id: [fp for fp in issue.findings if fp not in present] for issue in review.issues
    }
    return {issue_id: fps for issue_id, fps in missing.items() if fps}

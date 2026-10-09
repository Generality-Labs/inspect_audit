"""Review decisions: an append-only log of objects under `review/`, folded into the view applied to copies.

Producer run files are never edited. A decision is one object, written once, carrying its
author, time and reason: suppress a rule, accept findings as an issue, link an issue, set a
status, or retract an earlier decision. The fold applies the log in time order and yields the
`Review` that rendering applies: matching observations gain a suppression, matching
fingerprints gain an issue id or a status. `suppressions.yaml` and `issues.yaml` are derived
from the log for reading; nothing writes them directly.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import yaml
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from .fs import StoreFS, as_store_fs
from .models import Finding, Provenance, Run, Status, StatusChange, Suppression

SUPPRESSIONS_FILE = "suppressions.yaml"
ISSUES_FILE = "issues.yaml"
REVIEW_PREFIX = "review"
DERIVED_HEADER = "# derived from review/; do not edit\n"

Kind = Literal["suppress", "accept", "link", "status", "retract"]


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
    """The folded view of the decision log: what rendering applies to the current runs."""

    model_config = ConfigDict(extra="forbid")
    suppressions: list[SuppressionRule] = Field(default_factory=list)
    issues: list[IssueEntry] = Field(default_factory=list)
    statuses: dict[str, Status] = Field(default_factory=dict)
    status_provenance: dict[str, Provenance] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _issues_are_distinct(self) -> Review:
        ids = Counter(issue.id for issue in self.issues)
        repeated = sorted(issue_id for issue_id, n in ids.items() if n > 1)
        if repeated:
            raise ValueError(f"issue id used more than once: {', '.join(repeated)}")
        owners: dict[str, list[str]] = {}
        for issue in self.issues:
            for fp in issue.findings:
                owners.setdefault(fp, []).append(issue.id)
        shared = {fp: ids for fp, ids in owners.items() if len(ids) > 1}
        if shared:
            detail = "; ".join(f"{fp} in {', '.join(ids)}" for fp, ids in sorted(shared.items()))
            raise ValueError(f"a fingerprint may belong to one issue only: {detail}")
        return self


_SUPPRESSIONS = TypeAdapter(list[SuppressionRule])
_ISSUES = TypeAdapter(list[IssueEntry])


class SuppressPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule: str
    subject: str = "*"
    producer: str | None = None
    kind: str = "false_positive"


class AcceptPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue: str = Field(min_length=1)
    title: str = Field(min_length=1)
    subject: str
    fingerprints: list[str] = Field(min_length=1)


class LinkPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue: str
    url: str


class StatusPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprints: list[str] = Field(min_length=1)
    status: Status


class RetractPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str


class Decision(BaseModel):
    """One review decision, stored as its own object under review/; the log is append-only."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    at: AwareDatetime
    author: str
    kind: Kind
    reason: str | None = None
    suppress: SuppressPayload | None = None
    accept: AcceptPayload | None = None
    link: LinkPayload | None = None
    status: StatusPayload | None = None
    retract: RetractPayload | None = None

    @model_validator(mode="after")
    def _one_payload(self) -> Decision:
        kinds: tuple[Kind, ...] = ("suppress", "accept", "link", "status", "retract")
        present = [k for k in kinds if getattr(self, k) is not None]
        if present != [self.kind]:
            raise ValueError(
                f"a {self.kind} decision needs exactly the {self.kind} payload, "
                f"got {', '.join(present) or 'none'}"
            )
        return self

    def key(self) -> str:
        return f"{REVIEW_PREFIX}/{_stamp(self.at)}-{self.id}.json"


def _stamp(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def new_decision_id(at: datetime) -> str:
    """`dec-<stamp>-<microsecond><random>`: keys sort by time within a second, not by chance."""
    return f"dec-{_stamp(at)}-{at.microsecond:06d}{uuid4().hex[:3]}"


def load_decisions(fs: StoreFS) -> list[Decision]:
    """Every decision in the log, in key order (time, then id). A malformed object names itself."""
    decisions: list[Decision] = []
    for key in fs.glob(f"{REVIEW_PREFIX}/*.json"):
        try:
            decisions.append(Decision.model_validate_json(fs.read_text(key)))
        except (ValueError, ValidationError) as ex:
            raise ValueError(f"{key}: {ex}") from ex
    return decisions


def write_decision(fs: StoreFS, decision: Decision) -> str:
    return fs.write_text(
        decision.key(), decision.model_dump_json(indent=1, exclude_none=True) + "\n"
    )


class Fold(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review: Review
    warnings: list[str] = Field(default_factory=list)


def fold(decisions: Sequence[Decision]) -> Fold:
    """Apply the log in key order into the view rendering uses.

    Suppressions accumulate, acceptances create issues, the latest link and status win, a
    retraction removes the effect of the decision it names, and a fingerprint accepted twice
    goes to the later acceptance with a warning naming both.
    """
    retracted = {d.retract.decision for d in decisions if d.retract is not None}
    live = [d for d in sorted(decisions, key=lambda d: d.key()) if d.id not in retracted]
    suppressions: list[SuppressionRule] = []
    issues: dict[str, IssueEntry] = {}
    owner: dict[str, tuple[str, str]] = {}  # fingerprint -> (issue id, decision id)
    statuses: dict[str, Status] = {}
    status_provenance: dict[str, Provenance] = {}
    warnings: list[str] = []
    for d in live:
        if d.suppress is not None:
            suppressions.append(
                SuppressionRule(
                    rule=d.suppress.rule,
                    subject=d.suppress.subject,
                    producer=d.suppress.producer,
                    kind=d.suppress.kind,
                    author=d.author,
                    reason=d.reason or "",
                    since=d.at.date(),
                )
            )
        elif d.accept is not None:
            for fp in d.accept.fingerprints:
                if fp in owner:
                    previous_issue, previous_decision = owner[fp]
                    warnings.append(
                        f"{fp} accepted by {previous_decision} ({previous_issue}) and again by "
                        f"{d.id} ({d.accept.issue}); the later wins"
                    )
                    kept = [f for f in issues[previous_issue].findings if f != fp]
                    issues[previous_issue] = issues[previous_issue].model_copy(
                        update={"findings": kept}
                    )
                owner[fp] = (d.accept.issue, d.id)
            issues[d.accept.issue] = IssueEntry(
                id=d.accept.issue,
                title=d.accept.title,
                subject=d.accept.subject,
                findings=list(d.accept.fingerprints),
                author=d.author,
                opened=d.at.date(),
                reason=d.reason,
            )
        elif d.link is not None:
            if d.link.issue in issues:
                issues[d.link.issue] = issues[d.link.issue].model_copy(
                    update={"github": d.link.url}
                )
            else:
                warnings.append(f"{d.id} links unknown issue {d.link.issue}")
        elif d.status is not None:
            for fp in d.status.fingerprints:
                statuses[fp] = d.status.status
                status_provenance[fp] = Provenance(timestamp=d.at, author=d.author, reason=d.reason)
    kept_issues = [issue for issue in issues.values() if issue.findings]
    review = Review(
        suppressions=suppressions,
        issues=kept_issues,
        statuses=statuses,
        status_provenance=status_provenance,
    )
    return Fold(review=review, warnings=warnings)


def load_review(store: StoreFS | str | Path) -> Review:
    """The folded view of the store's decision log."""
    return fold(load_decisions(as_store_fs(store))).review


def write_review_views(fs: StoreFS, review: Review) -> list[str]:
    """The derived `suppressions.yaml` and `issues.yaml`, for reading only."""
    written: list[str] = []
    for name, rows in ((SUPPRESSIONS_FILE, review.suppressions), (ISSUES_FILE, review.issues)):
        rows_json = [row.model_dump(mode="json", exclude_none=True) for row in rows]
        body = yaml.safe_dump(rows_json, sort_keys=False, allow_unicode=True)
        written.append(fs.write_text(name, DERIVED_HEADER + body))
    return written


def _load_yaml_list(fs: StoreFS, key: str, adapter: TypeAdapter[Any]) -> list[Any]:
    """A YAML file as a validated list, or an empty list when absent or derived. Errors name the file."""
    if not fs.is_file(key):
        return []
    text = fs.read_text(key)
    if text.startswith(DERIVED_HEADER):
        return []
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as ex:
        raise ValueError(f"{key}: {ex}") from ex
    if loaded is None:
        return []
    if not isinstance(loaded, list):
        raise ValueError(f"{key} must be a YAML list")
    try:
        return adapter.validate_python(loaded)
    except ValidationError as ex:
        raise ValueError(f"{key}: {ex}") from ex


def _midnight(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=UTC)


def _migrated(
    at: datetime, author: str, reason: str | None, kind: Kind, payload: BaseModel
) -> Decision:
    digest = hashlib.sha1(payload.model_dump_json().encode()).hexdigest()[:4]
    return Decision.model_validate(
        {
            "id": f"dec-{_stamp(at)}-{digest}",
            "at": at,
            "author": author,
            "kind": kind,
            "reason": reason,
            kind: payload.model_dump(),
        }
    )


def migrate_review_files(fs: StoreFS, *, now: datetime) -> list[Decision]:
    """Turn hand-written `suppressions.yaml` and `issues.yaml` into decisions, once.

    Ids are deterministic, so running it again writes nothing. `now` dates link decisions, which
    the files never recorded a date for.
    """
    rules: list[SuppressionRule] = _load_yaml_list(fs, SUPPRESSIONS_FILE, _SUPPRESSIONS)
    issues: list[IssueEntry] = _load_yaml_list(fs, ISSUES_FILE, _ISSUES)
    candidates: list[Decision] = []
    for rule in rules:
        payload = SuppressPayload(
            rule=rule.rule, subject=rule.subject, producer=rule.producer, kind=rule.kind
        )
        candidates.append(
            _migrated(_midnight(rule.since), rule.author, rule.reason, "suppress", payload)
        )
    for issue in issues:
        accept = AcceptPayload(
            issue=issue.id, title=issue.title, subject=issue.subject, fingerprints=issue.findings
        )
        candidates.append(
            _migrated(_midnight(issue.opened), issue.author, issue.reason, "accept", accept)
        )
        if issue.github:
            link = LinkPayload(issue=issue.id, url=issue.github)
            candidates.append(_migrated(now, issue.author, None, "link", link))
    written = [d for d in candidates if not fs.exists(d.key())]
    for decision in written:
        write_decision(fs, decision)
    return written


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
            if finding.fingerprint in review.statuses:
                status = review.statuses[finding.fingerprint]
                finding.status = status
                provenance = review.status_provenance.get(finding.fingerprint) or Provenance(
                    timestamp=datetime.now(tz=UTC), author="review", reason=None
                )
                finding.history.append(StatusChange(status=status, provenance=provenance))
        applied.append(copy)
    return applied


def unmatched_issue_findings(review: Review, runs: Sequence[Run]) -> dict[str, list[str]]:
    """For each issue, the fingerprints it lists that no current finding carries."""
    present = {finding.fingerprint for run in runs for finding in run.findings}
    missing = {
        issue.id: [fp for fp in issue.findings if fp not in present] for issue in review.issues
    }
    return {issue_id: fps for issue_id, fps in missing.items() if fps}

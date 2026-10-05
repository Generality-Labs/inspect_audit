"""The Store: read the current view; write review decisions, the only way they are written.

The review files are the persistence format, not the interface. A decision names what the
reviewer pointed at, is resolved to fingerprints, validated as a whole `Review`, written, the
summaries re-rendered, and committed with the reviewer as author when the review directory is
inside a git work tree.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import ValidationError

from .io import read_current
from .models import Finding, Run
from .review import IssueEntry, Review, SuppressionRule, load_review, save_review

_AUTHOR = re.compile(r".+ <.+@.+>")


class StoreError(ValueError):
    """A decision that cannot be applied: nothing selected, an unknown id, or a conflict."""


@dataclass(frozen=True)
class Selection:
    """What a reviewer points at: an eval and rule, or record ids."""

    eval: str | None = None
    rule: str | None = None
    ids: tuple[str, ...] = ()


def _today() -> date:
    return datetime.now(tz=UTC).date()


def _next_issue_id(review: Review) -> str:
    numbers = [int(m.group(1)) for i in review.issues if (m := re.fullmatch(r"ISS-(\d+)", i.id))]
    return f"ISS-{max(numbers, default=0) + 1:04d}"


class Store:
    def __init__(self, root: Path, review_dir: Path | None = None) -> None:
        self.root = root
        self.review_dir = review_dir or root

    def runs(self) -> list[Run]:
        return read_current(self.root)

    def review(self) -> Review:
        return load_review(self.review_dir)

    def resolve(self, selection: Selection) -> list[Finding]:
        """The findings in the current view that the selection names."""
        findings = [f for run in self.runs() for f in run.findings]
        if selection.ids:
            by_id = {f.id: f for f in findings}
            unknown = [i for i in selection.ids if i not in by_id]
            if unknown:
                raise StoreError(f"unknown record id(s): {', '.join(unknown)}")
            chosen = [by_id[i] for i in selection.ids]
        elif selection.rule:
            chosen = [f for f in findings if f.rule == selection.rule]
        else:
            raise StoreError("a selection needs a rule or record ids")
        if selection.eval:
            chosen = [f for f in chosen if f.subject.eval == selection.eval]
        if not chosen:
            raise StoreError(f"nothing in the current view matches {selection}")
        return chosen

    def suppress(
        self,
        *,
        rule: str,
        author: str,
        reason: str,
        subject: str = "*",
        producer: str | None = None,
        kind: str = "false_positive",
    ) -> SuppressionRule:
        entry = SuppressionRule(
            rule=rule,
            subject=subject,
            producer=producer,
            kind=kind,
            author=author,
            reason=reason,
            since=_today(),
        )
        review = self.review()
        updated = review.model_copy(update={"suppressions": [*review.suppressions, entry]})
        self._save(updated, f"review: suppress {rule} on {subject}", author)
        return entry

    def accept(
        self, selection: Selection, *, title: str, author: str, reason: str | None = None
    ) -> IssueEntry:
        review = self.review()
        findings = self.resolve(selection)
        subjects = sorted({f.subject.eval for f in findings})
        if len(subjects) != 1:
            raise StoreError(
                f"an issue belongs to one eval; the selection spans {', '.join(subjects)}"
            )
        owner = {fp: issue.id for issue in review.issues for fp in issue.findings}
        taken = sorted(
            {
                f"{f.fingerprint} ({owner[f.fingerprint]})"
                for f in findings
                if f.fingerprint in owner
            }
        )
        if taken:
            raise StoreError(f"already accepted into another issue: {', '.join(taken)}")
        entry = IssueEntry(
            id=_next_issue_id(review),
            title=title,
            subject=subjects[0],
            findings=sorted({f.fingerprint for f in findings}),
            author=author,
            opened=_today(),
            reason=reason,
        )
        updated = review.model_copy(update={"issues": [*review.issues, entry]})
        self._save(updated, f"review: accept {entry.id} {title}", author)
        return entry

    def link(self, issue_id: str, url: str, *, author: str) -> IssueEntry:
        review = self.review()
        issues = list(review.issues)
        for index, issue in enumerate(issues):
            if issue.id == issue_id:
                issues[index] = issue.model_copy(update={"github": url})
                updated = review.model_copy(update={"issues": issues})
                self._save(updated, f"review: link {issue_id}", author)
                return issues[index]
        raise StoreError(f"no issue {issue_id}")

    def _save(self, review: Review, message: str, author: str) -> None:
        from .cli import render_current  # the renderer lives beside the CLI

        if not _AUTHOR.fullmatch(author):
            raise StoreError(f"author must be 'Name <email>', got {author!r}")
        try:
            review = Review.model_validate(review.model_dump())
        except ValidationError as ex:
            raise StoreError(str(ex)) from ex
        written = save_review(self.review_dir, review)
        render_current(self.root, review)
        self._commit(written, message, author)

    def _commit(self, paths: list[Path], message: str, author: str) -> None:
        """Commit the review files when the review dir is inside a git work tree; else do nothing."""
        git = ["git", "-C", str(self.review_dir)]
        probe = subprocess.run(
            [*git, "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.stdout.strip() != "true":
            return
        subprocess.run([*git, "add", "--", *map(str, paths)], check=True, capture_output=True)
        # re-rendered summaries and parquet, when the store tracks them; untracked files stay out
        subprocess.run([*git, "add", "-u", "--", str(self.root)], check=True, capture_output=True)
        committed = subprocess.run(
            [
                *git,
                "-c",
                "user.name=inspect-audit",
                "-c",
                "user.email=inspect-audit@generality.org",
                "commit",
                "-q",
                "--author",
                author,
                "-m",
                message,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if committed.returncode != 0:
            raise StoreError(f"git commit failed: {committed.stderr.strip()}")

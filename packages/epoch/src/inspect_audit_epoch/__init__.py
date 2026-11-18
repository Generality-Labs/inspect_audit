"""Epoch AI's benchmark review as an Inspect Auditor package: their rubric, their page.

`package: epoch` in an audit config. The rubric is Epoch's form (`epoch.json`), staged
for the auditor with its plain reading and rated in `auditor.ratings`; the verdict,
defect rate and page are derived from the submitted findings (`method`), and the page
(`page`) is rendered from those and the auditor's prose.
"""

from pathlib import Path

from inspect_audit.package import AuditPackage, Findings, Rubric

HERE = Path(__file__).parent
GUIDE = (
    "This audit is Epoch AI's benchmark review. The epoch-review skill says how it is "
    "conducted: its two phases, the rubric's rows and their scales in "
    "/workspace/analysis/framework/epoch/, the stop rule and the sampling. Rate every "
    "row in auditor.ratings, keyed by the row id; the verdict is derived, never written."
)


def validate(findings: Findings) -> None:
    """What submission requires of an Epoch review.

    Rubric rows on their scales, the stop rule, the log-dependent rows unrated when no
    logs were supplied, and a scoring row that agrees with its measure.
    """
    from .method import validate as check

    check(findings)


def stage(target: Path) -> None:
    from .method import stage as staged

    staged(target)


def package() -> AuditPackage:
    from inspect_audit.package import installed_requirement

    from .page import publisher

    return AuditPackage(
        rubric=Rubric(
            skill=HERE / "skills/epoch-review", stage=stage, guide=GUIDE, validate=validate
        ),
        formats={"page": publisher},
        # investigators in child jobs load the rubric skill: they install this package too
        requirement=installed_requirement("inspect-audit-epoch"),
    )

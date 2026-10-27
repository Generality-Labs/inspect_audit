"""The Epoch AI benchmark review: a methodology, a template, and the records between.

The format `epoch` pairs the `epoch-rubric@1` methodology with the `epoch-review`
template. The investigator writes records (profile.json, assessments.json, prose.json,
sampling.json, findings.json); this module stages them, validates them against the
methodology and template, derives the verdict by the methodology's procedure, computes
each thresholded check's measure from coverage, and renders the page. No rubric text,
scale or limit lives here.
"""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from .methodology import METHODOLOGIES, Methodology, load_methodology
from .records import Assessment, Profile, Prose, Sampling, validate_assessments
from .template import TEMPLATES, Template, coverage_ids, footnote_marks, load_template, render

FORMAT = "epoch"
METHODOLOGY = "epoch-rubric@1"
TEMPLATE = "epoch-review"

RECORDS = ("profile.json", "assessments.json", "prose.json", "findings.json")
PHASE_KEYS = ("phase1_closed", "phase1_closed_at")
# what close_phase1 freezes: the labels the task audit wrote blind to the logs, and
# the rows derived from them
PHASE1_SNAPSHOT = ("coverage.json", "assessments.json", "sampling.json")


def phase_marker(report: Path) -> dict[str, Any]:
    marker = report / "format.json"
    return json.loads(marker.read_text()) if marker.is_file() else {}


def phase1_open(report: Path) -> bool:
    """Whether the run holds logs it may not read yet.

    A run given logs at launch audits the tasks blind first; until close_phase1 has
    frozen those labels, no tool reads the logs and no job carries them. A run given
    no logs has nothing to read, and a run that never declared either is not a phased
    review.
    """
    marker = phase_marker(report)
    return marker.get("logs_supplied") is True and not marker.get("phase1_closed")


def close_phase1(report: Path) -> dict[str, Any]:
    """Freeze the task audit's labels and open the logs.

    Requires the labels in coverage.json and the three source-only rows assessed:
    reviewability, scoring and consistency. Copies the frozen records into
    report/phase1/ and marks the format record; the copies are what a reader
    compares the final page against to see what the logs changed.
    """
    marker = phase_marker(report)
    if marker.get("logs_supplied") is not True:
        raise ValueError(
            "no evaluation logs were supplied to this run: there is no phase 2 to open; "
            "publish the phase-1 page, and resume it with logs attached to continue"
        )
    if marker.get("phase1_closed"):
        raise ValueError(f"phase 1 was closed at {marker.get('phase1_closed_at')}; it stays closed")
    if not (report / "coverage.json").is_file():
        raise ValueError(
            "phase 1 closes on its labels: write coverage.json from the item audits "
            "(export_coverage) before closing it"
        )
    method, _ = workspace_methodology(report)
    _, assessments, _, _ = load_records(report)
    # the gate and the minimum-standard rows the source answers; every quality row
    # waits for phase 2
    blind = [
        c.id
        for c in (method.gate, *method.minimum)
        if not c.needs_logs and not assessments[c.id].assessed
    ]
    if blind:
        raise ValueError(
            "phase 1 closes with its rows written: assess "
            + ", ".join(blind)
            + " from the source and the labels before closing it"
        )
    snapshot = report / "phase1"
    snapshot.mkdir(exist_ok=True)
    for name in PHASE1_SNAPSHOT:
        if (report / name).is_file():
            shutil.copy(report / name, snapshot / name)
    marker["phase1_closed"] = True
    marker["phase1_closed_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    (report / "format.json").write_text(json.dumps(marker, indent=2) + "\n")
    return marker


def _sentence(text: str) -> str:
    text = text.strip()
    return text[:1].upper() + text[1:] + ("" if text.endswith(".") else ".")


def methodology_markdown(method: Methodology, template: Template) -> str:
    """The methodology as the investigator reads it, rendered from the data."""
    out = [f"# {method.id} v{method.version}", ""]
    if method.source:
        out += [f"Source: {method.source}", ""]
    out += [method.intro.get("rubric", ""), ""]
    out += ["## Verdict", "", method.verdict.rule, ""]
    if method.verdict.stop:
        out += [_sentence(method.verdict.stop), ""]
    if method.verdict.flag:
        out += [_sentence(method.verdict.flag), ""]
    s = method.sampling
    out += [
        "## Sampling",
        "",
        f"Unit: {s.unit}. Population: {s.population}.",
        f"Sample: {s.sample}",
        f"Expansion: {s.expansion}" if s.expansion else "",
        f"Confirmation: {s.confirmation}" if s.confirmation else "",
        f"Measure `{s.measure.name}` = {s.measure.numerator} / ({s.measure.denominator}). {s.measure.note}",
        "",
    ]
    for section in method.sections:
        out += [f"## {section}", ""]
        key = {
            method.minimum[0].section: "minimum_standard",
            method.quality[0].section: "quality",
        }.get(section)
        if key and key in method.intro:
            out += [method.intro[key], ""]
        for check in method.by_section(section):
            out += [f"### `{check.id}`: {check.name}", "", check.question, ""]
            if check.guidance:
                out += [check.guidance, ""]
            out.append(f"Scale: {', '.join(check.scale)}.")
            if check.fields:
                out.append(f"Fields an assessed level must carry: {', '.join(check.fields)}.")
            if check.threshold:
                t = check.threshold
                out.append(
                    f"Threshold: {t.measure} {t.flag_when} flags the check. {t.override}".strip()
                )
            if check.flag_requires_impact:
                out.append(
                    f"A {method.verdict.procedure.minimum['fail_at']} must carry `impact`: "
                    "the measured effect on the headline results, with numbers."
                )
            if check.evidence:
                out.append(f"Evidence expected: {', '.join(check.evidence)}.")
            out.append("")
    out += ["## The page", "", f"Template `{template.id}` renders these blocks:", ""]
    for block in template.blocks:
        limit = (
            f"at most {block.max_words} words"
            if block.max_words
            else f"bullets of at most {block.max_sentences} sentences"
            if block.kind == "bullets"
            else f"a table with columns {', '.join(c.title for c in block.columns)}"
        )
        extras = []
        if block.max_paragraphs:
            extras.append(f"at most {block.max_paragraphs} paragraphs")
        if block.required_when_defects:
            extras.append("required whenever coverage.json records a defect")
        if block.closing:
            extras.append("rendered after the rubric and the disclaimer")
        out.append(
            f"- `{block.id}` ({block.kind}, {limit}"
            + "".join(f", {x}" for x in extras)
            + f"; verdicts {', '.join(block.when)}): {block.brief}"
        )
    if template.footnotes:
        f = template.footnotes
        out.append(
            f"- `footnotes` (list of at most {f.max_items} notes of at most {f.max_words} words, "
            f"referenced as [^n] from any block or rubric note; a Flag's impact is appended as "
            f"one automatically): {f.brief}"
        )
    if template.notices:
        out.append("")
        out.append(
            "Notices the renderer prints under the header when the run calls for them: "
            + "; ".join(f"`{k}`: {v}" for k, v in template.notices.items())
        )
    out.append("")
    return "\n".join(line for line in out if line is not None)


def _prose_schema(template: Template) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for block in template.blocks:
        if block.kind == "prose":
            props[block.id] = {"type": "string", "description": block.brief}
        elif block.kind == "bullets":
            props[block.id] = {
                "type": "array",
                "items": {"type": "string"},
                "description": block.brief,
            }
        else:
            props[block.id] = {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        c.key: {"type": "string", "title": c.title} for c in block.columns
                    },
                    "required": [c.key for c in block.columns],
                    "additionalProperties": False,
                },
                "description": block.brief,
            }
    if template.footnotes:
        props["footnotes"] = {
            "type": "array",
            "items": {"type": "string"},
            "description": template.footnotes.brief,
        }
    return {"title": "Prose", "type": "object", "properties": props, "additionalProperties": False}


def skeleton_assessments(method: Methodology) -> list[dict[str, Any]]:
    level = method.verdict.procedure.unfinished["level"]
    return [Assessment(check=c.id, level=level).model_dump() for c in method.checks]


def prepare_review(report: Path, benchmark: str, logs_supplied: bool | None = None) -> None:
    """Stage the methodology, the template, the schemas and empty records.

    `logs_supplied` records whether the run was given evaluation logs; left None on a
    restore, the marker keeps what the original run wrote.
    """
    from ._report import Finding

    method = load_methodology(METHODOLOGY)
    template = load_template(TEMPLATE)
    template.check_methodology(method)
    marker: dict[str, Any] = {"format": FORMAT, "methodology": method.key, "template": template.id}
    previous = report / "format.json"
    before = json.loads(previous.read_text()) if previous.is_file() else {}
    if logs_supplied is None:
        logs_supplied = before.get("logs_supplied")
    if logs_supplied is not None:
        marker["logs_supplied"] = bool(logs_supplied)
    # a closed phase 1 stays closed across a restore: its labels were frozen when the
    # logs were first opened, and a fresh marker must not reopen them
    for key in PHASE_KEYS:
        if key in before:
            marker[key] = before[key]
    previous.write_text(json.dumps(marker, indent=2) + "\n")
    shutil.copy(METHODOLOGIES / f"{method.id}-{method.version}.yaml", report / "methodology.yaml")
    shutil.copy(TEMPLATES / f"{template.id}.yaml", report / "template.yaml")
    (report / "methodology.md").write_text(methodology_markdown(method, template))
    (report / "profile.schema.json").write_text(json.dumps(Profile.model_json_schema(), indent=2))
    (report / "assessments.schema.json").write_text(
        json.dumps(TypeAdapter(list[Assessment]).json_schema(), indent=2)
    )
    (report / "sampling.schema.json").write_text(json.dumps(Sampling.model_json_schema(), indent=2))
    (report / "prose.schema.json").write_text(json.dumps(_prose_schema(template), indent=2))
    schema = Finding.model_json_schema()
    schema["properties"]["check"] = {
        "title": "Check",
        "type": "string",
        "enum": list(method.check_ids),
        "description": f"The {method.key} check this finding bears on.",
    }
    schema["properties"].pop("section", None)
    (report / "findings.schema.json").write_text(json.dumps(schema, indent=2))
    if not (report / "profile.json").exists():
        # a registry name's package prefix is not a title
        (report / "profile.json").write_text(
            json.dumps(Profile(benchmark=benchmark.split("/")[-1]).model_dump(), indent=2) + "\n"
        )
    if not (report / "assessments.json").exists():
        (report / "assessments.json").write_text(
            json.dumps(skeleton_assessments(method), indent=2) + "\n"
        )
    if not (report / "prose.json").exists():
        (report / "prose.json").write_text("{}\n")


def workspace_methodology(report: Path) -> tuple[Methodology, Template]:
    marker = json.loads((report / "format.json").read_text())
    method = load_methodology(marker.get("methodology", METHODOLOGY))
    template = load_template(marker.get("template", TEMPLATE))
    template.check_methodology(method)
    return method, template


def load_records(report: Path) -> tuple[Profile, dict[str, Assessment], Prose, Sampling | None]:
    method, _ = workspace_methodology(report)
    profile = Profile.model_validate_json((report / "profile.json").read_text())
    rows = TypeAdapter(list[Assessment]).validate_json((report / "assessments.json").read_text())
    assessments = validate_assessments(rows, method)
    prose = Prose.model_validate_json((report / "prose.json").read_text())
    sampling_path = report / "sampling.json"
    sampling = (
        Sampling.model_validate_json(sampling_path.read_text()) if sampling_path.is_file() else None
    )
    return profile, assessments, prose, sampling


def _sum_states(expr: str, counts: dict[str, Any]) -> int:
    return sum(int(counts.get(term.strip(), 0)) for term in expr.split("+"))


def measures(
    method: Methodology,
    assessments: dict[str, Assessment],
    coverage: dict[str, Any] | None,
    sampling: Sampling | None,
) -> dict[str, dict[str, Any]]:
    """Each thresholded check's measure: from coverage counts when present, else the row's own.

    Coverage counts only count when the sample was selected for prevalence; a purposive
    sample establishes that a defect exists, not how common it is.
    """
    out: dict[str, dict[str, Any]] = {}
    spec = method.sampling.measure
    for check in method.checks:
        if check.threshold is None:
            continue
        row = assessments[check.id]
        threshold = check.threshold.bound
        if coverage is not None and (sampling is None or sampling.for_prevalence):
            counts = coverage.get("counts") or {}
            num = _sum_states(spec.numerator, counts)
            den = _sum_states(spec.denominator, counts)
            beside = {
                "unresolved": int(counts.get("UNRESOLVED", 0)),
                "not_assessed": int(counts.get("NOT_ASSESSED", 0)),
                "population": int(coverage.get("denominator") or 0),
            }
            by_check = coverage.get("counts_by_check") or {}
            if len(by_check) > 1:
                # the item as written and its recorded grades are counted apart on
                # the page; a question defective in both counts once in the measure
                beside["by_check"] = {
                    check: _sum_states(spec.numerator, states) for check, states in by_check.items()
                }
            source = "coverage.json"
        elif row.measure is not None:
            num, den, beside, source = (
                row.measure.numerator,
                row.measure.denominator,
                {},
                "assessments.json",
            )
        else:
            # counts exist but the sample was not selected for prevalence: they say
            # defects exist, not how common they are, and the row has no measure of its
            # own. The verdict procedure treats that as unfinished, not as a Pass.
            out[check.id] = {
                "name": spec.name,
                "numerator": 0,
                "denominator": 0,
                "value": None,
                "threshold": threshold,
                "exceeded": None,
                "source": None,
                "inadmissible": (
                    f"a {sampling.strategy} sample does not measure prevalence"
                    if coverage is not None and sampling is not None
                    else None
                ),
            }
            continue
        value = num / den if den else None
        out[check.id] = {
            "name": spec.name,
            "numerator": num,
            "denominator": den,
            "value": value,
            "threshold": threshold,
            "exceeded": None if value is None else check.threshold.exceeded(value),
            "source": source,
            "beside": beside,
        }
    return out


def derive_verdict(
    method: Methodology,
    assessments: dict[str, Assessment],
    measured: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Apply the methodology's verdict procedure to the recorded assessments.

    Raises when a thresholded check's level contradicts its measure without a written
    reason, so the published verdict and its numbers cannot disagree silently.
    """
    proc = method.verdict.procedure
    fail_at = proc.minimum["fail_at"]
    reasons: list[str] = []
    for check_id, m in measured.items():
        row = assessments[check_id]
        if m["exceeded"] is None or not row.assessed:
            continue
        failing = row.level == fail_at
        if failing != m["exceeded"] and not row.threshold_override_reason:
            raise ValueError(
                f"{check_id} is {row.level} but {m['numerator']}/{m['denominator']} inspected "
                f"({m['value']:.0%}) is {'at or above' if m['exceeded'] else 'below'} the "
                f"{m['threshold']:.0%} threshold ({m['source']}); set threshold_override_reason "
                "on the assessment or change the level"
            )
        if failing != m["exceeded"]:
            reasons.append(f"{check_id} threshold overridden: {row.threshold_override_reason}")

    gate = assessments[method.gate.id]
    unfinished = proc.unfinished["level"]
    if gate.level == proc.gate["stop_at"]:
        verdict = proc.gate["outcome"]
        reasons.insert(0, f"{method.gate.id} is {gate.level}: not enough information to review")
    elif gate.level == unfinished:
        verdict = proc.unfinished["outcome"]
        reasons.insert(0, f"{method.gate.id} has not been assessed")
    else:
        # the review stops at the first failing minimum-standard check: what follows it in
        # the rubric, and every quality check, is left unassessed, as the published rubric's
        # "stop" reads. A level written there is a record the methodology does not allow
        order = method.minimum
        first = next((i for i, c in enumerate(order) if assessments[c.id].level == fail_at), None)
        if first is not None:
            after = [
                c.id for c in order[first + 1 :] + method.quality if assessments[c.id].assessed
            ]
            if after:
                raise ValueError(
                    f"the review stops at the first {fail_at} ({order[first].name}): "
                    f"{', '.join(after)} must be {unfinished}, not assessed"
                )
        flagged = [c.name for c in method.minimum if assessments[c.id].level == fail_at]
        pending = [c.name for c in method.minimum if assessments[c.id].level == unfinished]
        # a thresholded check passed on counts that cannot measure prevalence is not
        # passed: the review is unfinished until the sample is one that can, the row
        # carries its own measure, or the check is flagged on a measured impact
        unmeasured = [
            (c.name, measured[c.id]["inadmissible"])
            for c in method.minimum
            if c.id in measured
            and measured[c.id].get("inadmissible")
            and assessments[c.id].level not in (fail_at, unfinished)
        ]
        if flagged:
            verdict = proc.minimum["outcome"]
            reasons.insert(0, f"{fail_at.lower()}ged: " + ", ".join(flagged))
        elif pending or unmeasured:
            verdict = proc.unfinished["outcome"]
            if pending:
                reasons.insert(0, "minimum standard not reviewed: " + ", ".join(pending))
            for name, why in unmeasured:
                reasons.insert(
                    0,
                    f"{name} passes without an admissible measure ({why}); record a random, "
                    "stratified or census design, give the assessment its own measure, or "
                    f"{fail_at} it on a measured impact",
                )
        else:
            verdict = proc.otherwise
            reasons.insert(0, f"{method.gate.id} {gate.level}; every minimum-standard check passes")
    for check_id, m in measured.items():
        if m["value"] is not None:
            reasons.append(
                f"{check_id} {m['name']} {m['numerator']}/{m['denominator']} "
                f"({m['value']:.1%}; threshold {m['threshold']:.0%}; {m['source']})"
            )
    return {
        "verdict": verdict,
        "reasons": reasons,
        "measures": measured,
        "methodology": method.key,
    }


def has_defects(coverage: dict[str, Any] | None) -> bool:
    if not coverage:
        return False
    return (
        bool(coverage_ids(coverage, "DEFECT"))
        or int((coverage.get("counts") or {}).get("DEFECT", 0)) > 0
    )


def check_prose(
    prose: Prose,
    template: Template,
    profile: Profile,
    verdict: str,
    coverage: dict[str, Any] | None = None,
    assessments: dict[str, Assessment] | None = None,
) -> None:
    prose.validate_against(template, verdict)
    if not profile.creator.strip():
        raise ValueError("profile.creator is empty")
    # the errors blocks are part of every page once an item is recorded as a defect
    if has_defects(coverage):
        missing = [
            b.id
            for b in template.blocks
            if b.required_when_defects and b.shown_for(verdict) and not prose.present(b)
        ]
        if missing:
            raise ValueError(
                f"coverage.json records a defect, so the page needs the {', '.join(missing)} "
                "block(s) whatever the verdict"
            )
    if template.footnotes:
        notes = prose.footnote_list()
        marks = prose.marks()
        for row in (assessments or {}).values():
            marks |= set(footnote_marks(row.notes))
        if dangling := sorted(n for n in marks if not 1 <= n <= len(notes)):
            raise ValueError(
                f"rubric notes refer to footnote(s) {', '.join(map(str, dangling))} but "
                f"{len(notes)} are written"
            )
        if unused := sorted(set(range(1, len(notes) + 1)) - marks):
            raise ValueError(
                f"footnote(s) {', '.join(map(str, unused))} are written but nothing refers to them"
            )
    # a table that lists the sound items beneath it must name every defect above them:
    # the reader counts the rows against the measure, so a sample of them misleads
    for block in template.blocks:
        if block.footer != "coverage_ids" or not prose.present(block):
            continue
        named = " ".join(str(row.get("task", "")) for row in prose.block(block.id))
        missing = [qid for qid in coverage_ids(coverage, "DEFECT") if qid not in named]
        if missing:
            raise ValueError(
                f"{block.id} must carry one row for every item recorded as a defect in "
                f"coverage.json, naming it in the task cell; missing {len(missing)}: "
                + ", ".join(missing)
            )


def assess(report: Path, root: Path | None = None) -> dict[str, Any]:
    """Validate the records, derive the verdict, render the page. Returns the verdict.

    With `root`, evidence paths are checked against the workspace; without it (a
    converted bundle, a fixture) they are taken on trust.
    """
    from ._report import check_evidence_path

    method, template = workspace_methodology(report)
    profile, assessments, prose, sampling = load_records(report)
    if root is not None:
        for row in assessments.values():
            for evidence in row.evidence:
                check_evidence_path(root, evidence.path)
    coverage_path = report / "coverage.json"
    coverage = json.loads(coverage_path.read_text()) if coverage_path.is_file() else None
    measured = measures(method, assessments, coverage, sampling)
    derived = derive_verdict(method, assessments, measured)
    notices: list[str] = []
    marker = phase_marker(report)
    if marker.get("logs_supplied") is False or phase1_open(report):
        # phase 1, the task audit: the checks answered from evaluation logs stay
        # unassessed until the logs are opened, which a run without logs never does
        # (the page says so, and Verified waits for phase 2) and a run with logs does
        # by closing phase 1
        unfinished = method.verdict.procedure.unfinished["level"]
        assessed = [c.id for c in method.checks if c.needs_logs and assessments[c.id].assessed]
        if assessed:
            raise ValueError(
                (
                    "phase 1 is open (the task audit, blind to the logs), so the checks "
                    f"answered from logs stay unassessed: {', '.join(assessed)} must be "
                    f"{unfinished}; close_phase1 once the labels and the source rows are "
                    "written, then answer them"
                )
                if marker.get("logs_supplied")
                else (
                    "no evaluation logs were supplied (phase 1, the task audit), so the "
                    f"checks answered from logs stay unassessed: {', '.join(assessed)} must "
                    f"be {unfinished}; attach logs in a phase-2 resume to answer them"
                )
            )
        if marker.get("logs_supplied") is False and "no_logs" in template.notices:
            notices.append(template.notices["no_logs"])
    derived["notices"] = notices
    check_prose(prose, template, profile, derived["verdict"], coverage, assessments)
    if not profile.review_date:
        profile.review_date = datetime.now(UTC).date().isoformat()
        (report / "profile.json").write_text(json.dumps(profile.model_dump(), indent=2) + "\n")
    if (
        coverage is not None
        and sampling is not None
        and coverage.get("sampling") != sampling.model_dump()
    ):
        coverage["sampling"] = sampling.model_dump()
        coverage_path.write_text(json.dumps(coverage, indent=2))
    derived["template"] = template.id
    derived["page"] = render(template, method, profile, assessments, prose, derived, coverage)
    return derived


def prepare_publication(root: Path, findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate, derive and render in a workspace; writes verdict.json and epoch_review.md."""
    report = root / "work/report"
    derived = assess(report, root)
    page = derived.pop("page")
    derived["findings"] = [
        {"id": f["id"], "check": f.get("check") or f.get("section"), "status": f["status"]}
        for f in findings
        if f.get("status") in ("supported", "qualified")
    ]
    (report / "verdict.json").write_text(json.dumps(derived, indent=2) + "\n")
    (report / "epoch_review.md").write_text(page)
    return derived

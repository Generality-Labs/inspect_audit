"""The skills an auditor is given.

Loaded through Inspect's own reader, so a broken frontmatter, a directory renamed away
from its skill name, or a duplicate name fails here rather than inside a container.
"""

from pathlib import Path

from inspect_ai.tool._tools._skill import Skill, read_skills
from inspect_ai.tool._tools._skill.validate import check_unique_skill_names

SKILLS = Path(__file__).parent.parent / "src" / "inspect_audit" / "skills"


def load() -> list[Skill]:
    return read_skills([str(p) for p in sorted(SKILLS.iterdir()) if p.is_dir()])


def test_every_skill_loads_and_names_are_unique() -> None:
    skills = load()
    assert {skill.name for skill in skills} == {
        "audit-framework",
        "answer-format",
        "approach-census",
        "contamination",
        "environment-integrity",
        "failure-attribution",
        "gold-answer",
        "ground-truth-access",
        "insufficiently-specified",
        "other-findings",
        "question-labels",
        "red-teaming",
        "reading-logs",
        "analyzing-logs",
        "map-inspect-packages",
    }
    # `skill()` and `deepagent()` both reject duplicate names across parent and
    # subagents, so uniqueness is a hard requirement rather than tidiness.
    check_unique_skill_names(skills)


def test_the_gold_skill_names_its_grades() -> None:
    """The grades are the output contract, so they belong in the skill text."""
    gold = next(skill for skill in load() if skill.name == "gold-answer")
    for grade in ("CORRECT", "INCORRECT", "ALTERNATIVES", "UNVERIFIABLE"):
        assert grade in gold.instructions


def test_descriptions_stay_within_the_always_visible_budget() -> None:
    """Descriptions cost context on every turn.

    They live in the skill tool's own description, so an audit pays for them whether or
    not a skill is ever invoked.
    """
    for skill in load():
        assert len(skill.description) <= 1024


def test_a_skill_directory_is_the_whole_contract(tmp_path: Path, monkeypatch) -> None:
    """Dropping a skill directory in is all it takes: prompt, validation and scoring derive."""
    import inspect_audit._verdicts as agent_module
    from inspect_audit._verdicts import audit_items

    # a new item, added as a directory and nothing else
    for name, grades in (
        ("gold-answer", "[CORRECT, INCORRECT]"),
        ("env-broken", "[SOUND, BROKEN]"),
    ):
        d = tmp_path / name
        d.mkdir()
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test item\nmetadata:\n  grades: {grades}\n---\n\nbody\n"
        )
    monkeypatch.setattr(agent_module, "SKILLS", tmp_path)

    items = audit_items()
    assert [item.name for item in items] == ["env-broken", "gold-answer"]
    assert items[0].grades == ["SOUND", "BROKEN"]

    # selection validates against what exists
    import pytest

    with pytest.raises(ValueError, match="Unknown audit item"):
        audit_items(["nope"])


def test_malformed_frontmatter_fails_at_load(tmp_path: Path, monkeypatch) -> None:
    """A broken skill contract must fail at load, not at verdict time.

    Silently coerced to an empty contract, a typo'd frontmatter surfaces as
    `record_verdict` rejecting every grade forever -- a paid run burned against
    its limits with no verdict. Loading is where the author is watching.
    """
    import shutil

    import pytest

    import inspect_audit._verdicts as agent_module
    from inspect_audit._verdicts import audit_items

    monkeypatch.setattr(agent_module, "SKILLS", tmp_path)

    cases = {
        "no-metadata": "",
        "scalar-grades": "metadata:\n  grades: SOUND\n",
        "int-grades": "metadata:\n  grades: [1, 2]\n",
        "typo-key": "metadata:\n  grade: [A, B]\n",
        "rogue-flags": "metadata:\n  grades: [A]\n  flags: [B]\n",
        "scalar-flags": "metadata:\n  grades: [A]\n  flags: A\n",
        "rogue-unresolved": "metadata:\n  grades: [A]\n  unresolved: [B]\n",
        "flag-and-unresolved": "metadata:\n  grades: [A]\n  flags: [A]\n  unresolved: [A]\n",
        "unknown-tool": "metadata:\n  grades: [A]\n  tools: [attempts]\n",
        "scalar-tools": "metadata:\n  grades: [A]\n  tools: reset\n",
        "list-details": "metadata:\n  grades: [A]\n  details: [x, y]\n",
    }
    for name, frontmatter in cases.items():
        d = tmp_path / name
        d.mkdir()
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test item\n{frontmatter}---\n\nbody\n"
        )
        with pytest.raises(ValueError, match=name):
            audit_items()
        shutil.rmtree(d)


def test_verdicts_are_validated_and_scored_per_item(monkeypatch) -> None:
    """record_verdict enforces the item's grades, an explanation and evidence for every
    grade, unresolved ones included; each item scores independently."""
    import asyncio

    import pytest
    from inspect_ai.tool import ToolError
    from inspect_ai.util._store import Store, init_subtask_store

    from inspect_audit._verdicts import (
        AuditItemSkill,
        Evidence,
        Verdicts,
        record_verdict,
        submit_audit,
    )

    item = AuditItemSkill(
        name="gold-answer",
        description="test",
        grades=["CORRECT", "INCORRECT", "UNVERIFIABLE"],
        flags=["INCORRECT"],
        unresolved=["UNVERIFIABLE"],
    )
    init_subtask_store(Store())
    record = record_verdict([item])
    submit = submit_audit([item])
    quote = [Evidence(observed="q", source="s")]
    base = {"item": "gold-answer", "approaches": "", "tried": "", "remarks": "", "details": {}}

    async def run() -> None:
        with pytest.raises(ToolError, match="must be one of"):
            await record(**base, grade="MAYBE", explanation="x", evidence=quote)
        with pytest.raises(ToolError, match="explanation"):
            await record(**base, grade="CORRECT", explanation=" ", evidence=quote)
        # unresolved is no exemption: what was tried and seen, with its source
        with pytest.raises(ToolError, match="at least one"):
            await record(**base, grade="UNVERIFIABLE", explanation="could not tell", evidence=[])
        with pytest.raises(ToolError, match="No verdict recorded"):
            await submit(environment_issues=[], unresolved=[], improvements=[])
        await record(**base, grade="INCORRECT", explanation="the source says 9", evidence=quote)
        await submit(environment_issues=[], unresolved=[], improvements=[])

    asyncio.run(run())

    from inspect_ai.util import store_as

    verdict = store_as(Verdicts).verdicts["gold-answer"]
    assert (verdict.grade, verdict.explanation) == ("INCORRECT", "the source says 9")


def test_the_confidential_section_is_off_by_default_and_names_the_boundary() -> None:
    """The toggle constrains what leaves, not whether the auditor may verify.

    An auditor stripped of the ability to check anything invents citations (the
    reason the audit sandbox grants egress at all), so the confidential prompt has
    to draw the line at transmission and say what to do when that is not enough.
    """
    from inspect_audit import prompts

    default = prompts.AUDITOR.format(items="- `x`: y", confidential="", notes="")
    assert "unpublished" not in default

    on = prompts.AUDITOR.format(
        items="- `x`: y",
        confidential=prompts.AUDIT_CONFIDENTIAL.format(root="/audit"),
        notes="",
    )
    assert "must not transmit" in on
    assert "/audit" in on
    # it must not read as "stop verifying"
    assert "grade on what you could establish" in on


def test_other_findings_flags_only_what_it_found() -> None:
    """The residue item must not become a place to file impressions: FOUND is the flag,
    and NONE still says, with sources, what was looked at."""
    skill = next(s for s in load() if s.name == "other-findings")
    meta = skill.metadata or {}

    assert meta["grades"] == ["FOUND", "NONE"]
    assert meta["flags"] == ["FOUND"]
    assert "unevidenced" not in meta
    # scope separates a one-item quirk from a mechanism that recurs across the bank
    assert "scope" in meta["details"]["findings"]


def test_verdict_object_contract_and_submission_debrief() -> None:
    """Expose skill fields before the first call and retain structured results."""
    import asyncio

    import pytest
    from inspect_ai.tool import ToolDef, ToolError
    from inspect_ai.util import store_as
    from inspect_ai.util._store import Store, init_subtask_store

    from inspect_audit._verdicts import (
        AuditItemSkill,
        Evidence,
        Verdicts,
        record_verdict,
        submit_audit,
    )

    item = AuditItemSkill(
        name="answer-format",
        description="Review answers",
        grades=["SOUND"],
        details={"examined": "Exact log, sample, epoch references"},
    )
    init_subtask_store(Store())
    record = record_verdict([item])
    schema = ToolDef(record).parameters.properties["details"]
    assert schema.type == "string"
    assert "Exact log, sample, epoch references" in schema.description
    assert "answer-format: examined" in schema.description
    quote = Evidence(observed="MOVE: a1a2", source="run.eval#sample=1,epoch=2,message=4")

    async def run() -> None:
        args = {
            "item": item.name,
            "evidence": [quote],
            "approaches": "reviewed",
            "tried": "read",
            "remarks": "",
            "grade": "SOUND",
            "explanation": "the format the grader expects is the one the question asks for",
        }
        with pytest.raises(ToolError, match="examined"):
            await record(**args, details={})
        # prose where the object belongs (09-29 chess run, 54 times): the refusal names
        # the fields and where the prose goes, so the auditor can correct itself
        with pytest.raises(ToolError, match=r"began 'Review table.*remarks.*examined: Exact log"):
            await record(**args, details="Review table: see /audit/review.json")
        await record(**args, details='{"examined": ["run.eval#sample=1"]}')
        await record(**args, details={"examined": [quote.source]})
        await submit_audit([item])(
            environment_issues=[
                Evidence(observed="Analysis dependency was missing", source="tool-event-1")
            ],
            unresolved=[Evidence(observed="Alternative judge not tested", source="tool-event-2")],
            improvements=[],
        )

    asyncio.run(run())
    saved = store_as(Verdicts)
    assert len(saved.rejected[item.name]) == 2  # the missing field, then the prose
    assert saved.verdicts[item.name].details["examined"] == [quote.source]
    assert saved.debrief["environment_issues"][0].source == "tool-event-1"
    assert saved.debrief["unresolved"][0].observed == "Alternative judge not tested"
    assert saved.debrief["improvements"] == []


def test_every_prompt_is_a_file_and_every_placeholder_gets_filled() -> None:
    """Prose lives in markdown; a prompt that reaches a model with a stray {field} is a bug."""
    from string import Formatter

    from inspect_audit import prompts

    filled = {
        "AUDITOR": {"items": "- `x`: y", "confidential": "", "notes": ""},
        "AUDIT_CONFIDENTIAL": {"root": "/audit"},
        "AUDIT_NOTES": {"notes": "look at the grader"},
        "INVESTIGATE": {},
    }
    assert set(filled) == set(prompts.__all__)
    for name, values in filled.items():
        template = getattr(prompts, name)
        assert (prompts.HERE / f"{name.lower()}.md").is_file(), f"{name} has no markdown file"
        fields = {field for _, field, _, _ in Formatter().parse(template) if field is not None}
        assert fields == set(values), f"{name} placeholders {fields} do not match {set(values)}"
        rendered = template.format(**values)
        for field in fields:
            assert "{" + field + "}" not in rendered, f"{name} left {field} unfilled"


def test_the_prompts_carry_no_python() -> None:
    """A prompt file is read by people who are not reading the code around it."""
    from inspect_audit import prompts

    for path in prompts.HERE.glob("*.md"):
        text = path.read_text()
        assert not text.lstrip().startswith(("import ", "from ")), path.name
        assert '"""' not in text, f"{path.name} still carries a Python string delimiter"


def test_verdict_schema_survives_openrouter_serialization() -> None:
    from inspect_ai.model._openai import openai_chat_tool_param
    from inspect_ai.tool import ToolDef, ToolInfo

    from inspect_audit._verdicts import audit_items, record_verdict

    definition = ToolDef(record_verdict(audit_items()))
    wire = openai_chat_tool_param(
        ToolInfo(
            name=definition.name,
            description=definition.description,
            parameters=definition.parameters,
        )
    )["function"]["parameters"]

    def check(schema):
        if schema.get("type") == "object":
            assert set(schema.get("required", [])) == set(schema["properties"])
            assert schema.get("additionalProperties") is False
        for child in schema.get("properties", {}).values():
            check(child)
        if "items" in schema:
            check(schema["items"])

    check(wire)
    assert wire["properties"]["details"]["type"] == "string"


def test_a_missing_verdict_says_why() -> None:
    """09-29 chess run: puzzles 40 and 75 came back NO_VERDICT with only concordance
    metadata. 40 had hit its cost limit before any verdict, 75 had every
    record_verdict refused; the investigator could see neither and blamed the wrong
    thing. Real eval: a limit hit before the auditor records anything."""
    from inspect_ai import Task, eval
    from inspect_ai.dataset import Sample
    from inspect_ai.solver import generate

    from inspect_audit._verdicts import AuditItemSkill, item_scorer

    item = AuditItemSkill(name="gold-answer", description="Check the key", grades=["CORRECT"])
    task = Task(
        dataset=[Sample(input="audit this")],
        solver=generate(),
        scorer=item_scorer(item),
        message_limit=1,
    )
    log = eval(task, model="mockllm/model", display="none")[0]
    score = log.samples[0].scores["gold-answer"]  # type: ignore[index]
    assert score.value == "NO_VERDICT"
    assert "never called record_verdict" in str(score.explanation)
    assert "hit its message limit" in str(score.explanation)

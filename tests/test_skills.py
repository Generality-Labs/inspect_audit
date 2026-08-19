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
        "gold-answer",
        "ground-truth-access",
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
    import inspect_audit._agent as agent_module
    from inspect_audit._agent import audit_items

    # a new item, added as a directory and nothing else
    for name, grades in (("gold-answer", "[CORRECT, INCORRECT]"), ("env-broken", "[SOUND, BROKEN]")):
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


def test_verdicts_are_validated_and_scored_per_item(monkeypatch) -> None:
    """record_verdict enforces the item's grades; each item scores independently."""
    import asyncio

    from inspect_ai.tool import ToolError
    from inspect_ai.util._store import Store, init_subtask_store

    from inspect_audit._agent import (
        AuditItemSkill,
        Evidence,
        Verdicts,
        record_verdict,
        submit_audit,
    )

    item = AuditItemSkill(
        name="gold-answer",
        description="test",
        grades=["CORRECT", "INCORRECT"],
        unevidenced=["INCORRECT"],
    )
    init_subtask_store(Store())
    record = record_verdict([item])
    submit = submit_audit([item])
    quote = [Evidence(observed="q", source="s")]

    async def run() -> None:
        # wrong grade rejected, missing evidence rejected, submit gated on coverage
        import pytest

        with pytest.raises(ToolError, match="must be one of"):
            await record(item="gold-answer", evidence=quote, approaches="", tried="", remarks="", grade="MAYBE", details="{}")
        with pytest.raises(ToolError, match="at least one"):
            await record(item="gold-answer", evidence=[], approaches="", tried="", remarks="", grade="CORRECT", details="{}")
        with pytest.raises(ToolError, match="No verdict recorded"):
            await submit()

        # an unevidenced grade may go without evidence; submit then passes
        await record(item="gold-answer", evidence=[], approaches="", tried="looked", remarks="", grade="INCORRECT", details="{}")
        await submit()

    asyncio.run(run())

    from inspect_ai.util import store_as

    assert store_as(Verdicts).verdicts["gold-answer"].grade == "INCORRECT"

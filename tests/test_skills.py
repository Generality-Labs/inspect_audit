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

"""Every prompt in the package, as markdown next to this file.

Prose belongs in prose files. A system prompt is the most-read and most-edited text
in an agent system, and it is reviewed by people who are not reading the code around
it, so it lives in markdown that renders and diffs like prose rather than in a Python
string that has to be escaped, indented and scrolled past.

Each constant is one file. `{name}` placeholders are filled with `str.format`, so a
literal brace in a prompt must be doubled; there are none today.
"""

from pathlib import Path

HERE = Path(__file__).parent


def _read(name: str) -> str:
    return (HERE / name).read_text()


# The sample auditor. Filled with `root` (where the item is staged), `items` (the audit
# items this auditor was granted, rendered from their skills), and the two optional
# sections below, which are empty strings when they do not apply.
AUDIT = _read("audit.md")

# Rendered into the audit prompt only when the benchmark is unpublished. The auditor
# keeps its shell and its internet -- an auditor that cannot check anything invents
# citations -- but it must not hand the item to a third party to do the checking. The
# boundary is what leaves in a request, not whether the network is reachable.
AUDIT_CONFIDENTIAL = _read("audit_confidential.md")

# Rendered into the audit prompt only when the operator sets `notes`: a free-form steer,
# kept separate from the skills so a skill stays general and the steer stays a per-run
# knob.
AUDIT_NOTES = _read("audit_notes.md")

# The outer investigator: one benchmark, its logs, its paper, a container and a budget.
# Three placeholders carry what differs between report formats: `publish` (how the
# deliverable is produced), `framework` (what the staged methodology files are and how
# to read them) and `coverage_plan` (what organises the investigation). Each format
# supplies them as three paragraphs, in that order, in `investigate_<format>.md`.
INVESTIGATE = _read("investigate.md")
_PLACEHOLDERS = ("publish", "framework", "coverage_plan")


def investigate_prompt(report_format: str = "gl") -> str:
    """The investigator's system prompt for a report format (`gl` or `epoch`)."""
    blocks = [b.strip() for b in _read(f"investigate_{report_format}.md").strip().split("\n\n")]
    if len(blocks) != len(_PLACEHOLDERS):
        raise ValueError(
            f"investigate_{report_format}.md must hold exactly {len(_PLACEHOLDERS)} paragraphs"
        )
    return INVESTIGATE.format(**dict(zip(_PLACEHOLDERS, blocks, strict=True)))


__all__ = [
    "AUDIT",
    "AUDIT_CONFIDENTIAL",
    "AUDIT_NOTES",
    "INVESTIGATE",
    "investigate_prompt",
]

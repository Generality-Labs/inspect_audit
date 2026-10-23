"""Load Markdown prompts. str.format placeholders require doubled literal braces."""

from pathlib import Path

HERE = Path(__file__).parent


def _read(name: str) -> str:
    return (HERE / name).read_text()


# The investigator, running inside the benchmark's own sample. Filled with `items`,
# `confidential` and `notes`.
INVESTIGATOR = _read("investigator.md")

# Rendered into the investigation prompt only when the benchmark is unpublished. The investigator
# keeps its shell and its internet -- an investigator that cannot check anything invents
# citations -- but it must not hand the item to a third party to do the checking. The
# boundary is what leaves in a request, not whether the network is reachable.
INVESTIGATION_CONFIDENTIAL = _read("investigation_confidential.md")

# Rendered into the investigation prompt only when the operator sets `notes`: a free-form steer,
# kept separate from the skills so a skill stays general and the steer stays a per-run
# knob.
INVESTIGATION_NOTES = _read("investigation_notes.md")

# The outer auditor: one benchmark, its logs, its paper, a container and a budget.
AUDIT = _read("audit.md")

__all__ = [
    "AUDIT",
    "INVESTIGATION_CONFIDENTIAL",
    "INVESTIGATION_NOTES",
    "INVESTIGATOR",
]

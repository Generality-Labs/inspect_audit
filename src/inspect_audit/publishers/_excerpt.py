"""Excerpts: verbatim evidence the publisher quotes into the report, declared by reference.

The publishing agent names where a piece of evidence is (a file in the findings bundle,
or one message of a recorded attempt in a log the findings reference) and which lines
of it to show. The publisher reads the text itself, so a quotation is never retyped:
what the reader sees is what the source records, shortened only where it says so and
with named strings (answer keys) replaced by "[redacted]". A declaration that does not
resolve to text in its source is an error the agent fixes before it publishes.
"""

import json
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# where prose places an excerpt: on its own, in any prose field
MARKER = re.compile(r"\{\{excerpt:([A-Za-z0-9_-]+)\}\}")
REDACTED = "[redacted]"
# a reader takes in a few lines; the box shows at most this many, then says what it left out
DEFAULT_LINES = 12
MOST_LINES = 40
# one recorded line can be a whole minified file
LINE_CHARACTERS = 240
# a recorded answer shorter than this is not redacted by itself (a lone "B" or "9" would
# blank every occurrence); the agent names it in `redact` if the excerpt shows it
TARGET_CHARACTERS = 4


class Excerpt(BaseModel):
    """One quotation, declared by where it is; the publisher reads its text."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    source: str = Field(
        description="A bundle file as the findings cite it (/workspace/... or /inputs/...), "
        "or a log the findings reference (its /inputs/... path or address)"
    )
    sample: str | None = None
    epoch: int | None = None
    message: int | str | None = Field(
        default=None, description="A message's id, or its 0-based position in the transcript"
    )
    lines: tuple[int, int] | None = Field(
        default=None, description="First and last line, 1-based and inclusive"
    )
    start: str | None = Field(
        default=None, description="Verbatim text on the first line to show (first occurrence)"
    )
    end: str | None = Field(
        default=None, description="Verbatim text on the last line to show (first one after start)"
    )
    max_lines: int = Field(default=DEFAULT_LINES, ge=1, le=MOST_LINES)
    caption: str = Field(min_length=1)
    redact: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_locator(self) -> "Excerpt":
        transcript = (self.sample, self.epoch, self.message)
        if any(v is not None for v in transcript) and any(v is None for v in transcript):
            raise ValueError(
                f"excerpt {self.id}: a transcript excerpt names sample, epoch and message"
            )
        if self.lines and (self.start or self.end):
            raise ValueError(f"excerpt {self.id}: give lines, or start/end text, not both")
        if self.end and not self.start:
            raise ValueError(f"excerpt {self.id}: end needs start")
        if self.lines and not 1 <= self.lines[0] <= self.lines[1]:
            raise ValueError(f"excerpt {self.id}: lines are [first, last], 1-based")
        if any(not r.strip() for r in self.redact):
            raise ValueError(f"excerpt {self.id}: an empty string cannot be redacted")
        return self


@dataclass(frozen=True)
class Quote:
    """An excerpt as resolved: the lines to show and where they came from."""

    id: str
    caption: str
    lines: list[str]
    elided: int
    """Selected lines not shown (beyond max_lines)."""
    redactions: int
    reference: str
    """The source as the Sources appendix lists it."""


def _read_file(excerpt: Excerpt, bundle: Path, manifest: dict[str, Any]) -> tuple[str, str]:
    artifacts: dict[str, str] = manifest.get("artifacts") or {}
    name = artifacts.get(excerpt.source) or excerpt.source.lstrip("/")
    if name not in manifest["files"] or name == "findings.json":
        cited = ", ".join(sorted(artifacts)[:5])
        raise ValueError(
            f"excerpt {excerpt.id}: {excerpt.source} is neither a file in the findings bundle "
            f"nor a log the findings reference (files are cited as, e.g., {cited})"
        )
    try:
        return (bundle / name).read_text(encoding="utf-8"), excerpt.source
    except UnicodeDecodeError as ex:
        raise ValueError(f"excerpt {excerpt.id}: {excerpt.source} is not text") from ex


def _log_path(reference: dict[str, Any], root: Path | None) -> str:
    """Where the referenced log can be read from here, without downloading it."""
    source, address = str(reference["source"]), str(reference["address"])
    if root is not None and source.startswith("/inputs/"):
        # still on the audit's host: the collected or staged copy
        local = root / "inputs" / source.removeprefix("/inputs/")
        if local.is_file():
            return str(local)
    if address.startswith("hawk:"):
        from .._log_sources import hawk_logs

        return hawk_logs(address)[0]
    if address.startswith("/inputs/"):
        raise ValueError(
            f"{source} was only in the original audit's inputs; nothing records where else "
            "to read it, so it cannot be quoted here"
        )
    return address


def _message_text(message: Any) -> str:
    """A message as the transcript shows it: its text, then any tool calls it made."""
    parts = [message.text] if message.text else []
    for call in getattr(message, "tool_calls", None) or []:
        arguments = call.arguments
        values = list(arguments.values())
        if len(values) == 1 and isinstance(values[0], str):
            # a shell command or a piece of code: as written, not JSON-quoted
            parts.append(f"{call.function}: {values[0]}")
        else:
            parts.append(f"{call.function}: {json.dumps(arguments, indent=1, ensure_ascii=False)}")
    return "\n".join(parts)


def _read_message(
    excerpt: Excerpt,
    manifest: dict[str, Any],
    root: Path | None,
    samples: dict[tuple[str, str, int], Any],
) -> tuple[str, str, list[str]]:
    from inspect_ai.log import read_eval_log_sample

    references = manifest.get("log_references") or []
    reference = next((r for r in references if excerpt.source in (r["source"], r["address"])), None)
    if reference is None:
        raise ValueError(
            f"excerpt {excerpt.id}: {excerpt.source} is not a log the findings reference; "
            "name it by its /inputs/... path or address in the manifest's log_references"
        )
    assert excerpt.sample is not None and excerpt.epoch is not None
    key = (str(reference["source"]), excerpt.sample, excerpt.epoch)
    if key not in samples:
        try:
            samples[key] = read_eval_log_sample(
                _log_path(reference, root),
                id=excerpt.sample,
                epoch=excerpt.epoch,
                resolve_attachments=True,
                exclude_fields={"events"},
            )
        except (ValueError, OSError) as ex:
            raise ValueError(f"excerpt {excerpt.id}: {ex}") from ex
        except Exception as ex:  # Inspect's own "not found" for a sample id or epoch
            raise ValueError(
                f"excerpt {excerpt.id}: cannot read sample {excerpt.sample} epoch "
                f"{excerpt.epoch} of {excerpt.source}: {ex}"
            ) from ex
    sample = samples[key]
    messages = sample.messages
    if isinstance(excerpt.message, int):
        if not 0 <= excerpt.message < len(messages):
            raise ValueError(
                f"excerpt {excerpt.id}: message {excerpt.message} is outside the "
                f"transcript's {len(messages)} messages"
            )
        message, position = messages[excerpt.message], excerpt.message
    else:
        found = [(i, m) for i, m in enumerate(messages) if m.id == excerpt.message]
        if not found:
            raise ValueError(f"excerpt {excerpt.id}: no message has id {excerpt.message}")
        position, message = found[0]
    target = sample.target if isinstance(sample.target, list) else [sample.target]
    answers = [str(t).strip() for t in target if len(str(t).strip()) >= TARGET_CHARACTERS]
    where = (
        f"{excerpt.source}, sample {excerpt.sample}, epoch {excerpt.epoch}, "
        f"message {position} ({message.role}, id {message.id})"
    )
    return _message_text(message), where, answers


def _select(excerpt: Excerpt, text: str) -> tuple[list[str], str]:
    # lines as an editor numbers them: a form feed or a Unicode separator does not end one
    lines = [line.removesuffix("\r") for line in text.expandtabs(4).removesuffix("\n").split("\n")]
    if excerpt.lines:
        first, last = excerpt.lines
        if last > len(lines):
            raise ValueError(
                f"excerpt {excerpt.id}: lines {first}-{last} run past the source's {len(lines)} lines"
            )
        return lines[first - 1 : last], f"lines {first}-{last}"
    if excerpt.start is None:
        return lines, ""
    joined = "\n".join(lines)
    begin = joined.find(excerpt.start.expandtabs(4))
    if begin < 0:
        raise ValueError(
            f"excerpt {excerpt.id}: start text {excerpt.start!r} is not in the source verbatim"
        )
    first = joined.count("\n", 0, begin)
    if excerpt.end is None:
        last = first + excerpt.max_lines - 1
    else:
        finish = joined.find(excerpt.end.expandtabs(4), begin)
        if finish < 0:
            raise ValueError(
                f"excerpt {excerpt.id}: end text {excerpt.end!r} is not in the source "
                "verbatim after its start"
            )
        last = joined.count("\n", 0, finish + len(excerpt.end.expandtabs(4)))
    last = min(last, len(lines) - 1)
    return lines[first : last + 1], f"lines {first + 1}-{last + 1}"


def resolve(
    excerpt: Excerpt,
    bundle: Path,
    manifest: dict[str, Any],
    root: Path | None = None,
    samples: dict[tuple[str, str, int], Any] | None = None,
) -> Quote:
    """Read an excerpt's text from its source; any declaration that does not resolve fails."""
    answers: list[str] = []
    if excerpt.sample is None:
        text, where = _read_file(excerpt, bundle, manifest)
    else:
        text, where, answers = _read_message(
            excerpt, manifest, root, {} if samples is None else samples
        )
    selected, span = _select(excerpt, text)
    # trim blank edges and the indentation every shown line shares
    while selected and not selected[0].strip():
        selected.pop(0)
    while selected and not selected[-1].strip():
        selected.pop()
    if not selected:
        raise ValueError(f"excerpt {excerpt.id}: the selected lines are empty")
    body = "\n".join(selected)
    for name in excerpt.redact:
        if name not in body:
            raise ValueError(
                f"excerpt {excerpt.id}: {name!r} (to redact) does not occur in the selected "
                "lines; check its spelling, or drop it"
            )
    redactions = 0
    for name in sorted({*excerpt.redact, *answers}, key=len, reverse=True):
        redactions += body.count(name)
        body = body.replace(name, REDACTED)
    shown = textwrap.dedent(body).split("\n")
    elided = max(0, len(shown) - excerpt.max_lines)
    shown = [
        line if len(line) <= LINE_CHARACTERS else line[:LINE_CHARACTERS] + " [...]"
        for line in shown[: excerpt.max_lines]
    ]
    return Quote(
        id=excerpt.id,
        caption=excerpt.caption,
        lines=shown,
        elided=elided,
        redactions=redactions,
        reference=where + (f", {span}" if span else ""),
    )


def resolve_all(
    excerpts: list[Excerpt], bundle: Path, manifest: dict[str, Any], root: Path | None = None
) -> dict[str, Quote]:
    """Every declared excerpt, resolved; ids are unique."""
    seen: set[str] = set()
    for excerpt in excerpts:
        if excerpt.id in seen:
            raise ValueError(f"excerpt id {excerpt.id} is declared twice")
        seen.add(excerpt.id)
    samples: dict[tuple[str, str, int], Any] = {}
    return {e.id: resolve(e, bundle, manifest, root, samples) for e in excerpts}


def plain(quote: Quote) -> str:
    """An excerpt as text, for the agent to check what the report quotes."""
    tail = [f"[... {quote.elided} more lines not shown]"] if quote.elided else []
    return "\n".join(
        [f"== {quote.id}: {quote.caption}", f"   from {quote.reference}", *quote.lines, *tail]
    )

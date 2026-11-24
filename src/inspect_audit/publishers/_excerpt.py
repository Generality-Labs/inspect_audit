"""Excerpts: verbatim evidence the publisher quotes into the report, declared by reference.

The publishing agent names where a piece of evidence is (a file in the findings bundle,
or one message of a recorded attempt in a log the findings reference) and which lines
of it to show. The publisher reads the text itself, so a quotation is never retyped:
what the reader sees is what the source records, shortened only where it says so. Nothing
is redacted unless the declaration names it. A declaration that does not resolve to text
in its source is an error the agent fixes before it publishes.

An excerpt is prose (`text`: a prompt, a model's message, a document) or recorded output
(`code`: a program, a command, a tool's output), and the report sets each as a reader
reads it. Its caption is the link text: an excerpt from a Hawk log links to that sample
in the Hawk log viewer. Where it was read from stays in the published bundle
(excerpts.txt), never in the report.
"""

import json
import os
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, model_validator

# where prose places an excerpt: on its own, in any prose field
MARKER = re.compile(r"\{\{excerpt:([A-Za-z0-9_-]+)\}\}")
REDACTED = "[redacted]"
# a reader takes in a few lines; the box shows at most this many, then says what it left out
DEFAULT_LINES = 12
MOST_LINES = 40
# one recorded line can be a whole minified file
LINE_CHARACTERS = 240
# a file with one of these suffixes is recorded output, set as code; any other is prose
CODE_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".go",
        ".rs",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".hpp",
        ".java",
        ".kt",
        ".scala",
        ".js",
        ".mjs",
        ".ts",
        ".tsx",
        ".jsx",
        ".rb",
        ".php",
        ".pl",
        ".lua",
        ".r",
        ".jl",
        ".hs",
        ".ml",
        ".ex",
        ".exs",
        ".erl",
        ".clj",
        ".swift",
        ".m",
        ".cs",
        ".fs",
        ".vb",
        ".sql",
        ".adb",
        ".ads",
        ".ada",
        ".f90",
        ".zig",
        ".nim",
        ".dart",
        ".sh",
        ".bash",
        ".zsh",
        ".ps1",
        ".bat",
        ".mk",
        ".cmake",
        ".json",
        ".jsonl",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".xml",
        ".csv",
        ".tsv",
        ".log",
        ".out",
        ".err",
        ".diff",
        ".patch",
        ".eval",
        ".tex",
        ".bib",
    }
)


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
    caption: str = Field(
        min_length=1,
        description="A plain label (whose message or which document); it is the link text",
    )
    kind: Literal["text", "code"] | None = Field(
        default=None,
        description="text: prose, set as a quotation; code: recorded output, set as "
        "recorded. Unset: code for a tool call or its output, or a code file; else text",
    )
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
    """Where it was read from, kept with the published bundle (excerpts.txt)."""
    kind: Literal["text", "code"] = "code"
    link: str | None = None
    """The sample in the Hawk log viewer, for an excerpt from a Hawk log."""


def viewer_base() -> str | None:
    """The Hawk log viewer's address, as the hawk CLI is configured here, or None.

    HAWK_LOG_VIEWER_URL first (the CLI's own setting), then the CLI's configuration.
    Without one, excerpts are not linked; the report is otherwise the same.
    """
    if base := os.environ.get("HAWK_LOG_VIEWER_URL"):
        return base.rstrip("/")
    try:
        from hawk.cli.config import get_config  # pyright: ignore[reportMissingImports]

        base = str(get_config().log_viewer_url or "")  # pyright: ignore[reportUnknownMemberType]
    except Exception:  # no hawk CLI, or no deployment to ask
        return None
    return base.rstrip("/") or None


def sample_link(
    address: str, sample: str, epoch: int, message: str | None, base: str | None
) -> str | None:
    """A sample's page in the Hawk log viewer, at one message, for a `hawk:` log address.

    The viewer's own sample URL (Hawk's `getSampleViewUrl`, as `hawk search` exports it):
    <viewer>/eval-set/<eval set id>#/logs/<log file>/samples/sample/<id>/<epoch>/
    """
    if not base or not address.startswith("hawk:") or "," in address:
        return None
    eval_set, _, file = address.removeprefix("hawk:").strip("/").partition("/")
    if not eval_set or not file:
        return None
    url = (
        f"{base}/eval-set/{quote(eval_set, safe='')}#/logs/{quote(file, safe='/')}"
        f"/samples/sample/{quote(sample, safe='')}/{epoch}/"
    )
    if message:
        url += f"messages?message={quote(message, safe='')}"
    return url


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


def _message_text(message: Any) -> tuple[str, int]:
    """A message as the transcript shows it: its text, then any tool calls it made.

    With how many of its lines are the message's own text (the rest are tool calls).
    """
    parts = [message.text] if message.text else []
    prose = len(message.text.split("\n")) if message.text else 0
    for call in getattr(message, "tool_calls", None) or []:
        arguments = call.arguments
        values = list(arguments.values())
        if len(values) == 1 and isinstance(values[0], str):
            # a shell command or a piece of code: as written, not JSON-quoted
            parts.append(f"{call.function}: {values[0]}")
        else:
            parts.append(f"{call.function}: {json.dumps(arguments, indent=1, ensure_ascii=False)}")
    return "\n".join(parts), prose


def _read_message(
    excerpt: Excerpt,
    manifest: dict[str, Any],
    root: Path | None,
    samples: dict[tuple[str, str, int], Any],
    viewer: str | None,
) -> tuple[str, str, int, str | None]:
    """The message's text, where it was read, how many lines are prose, and its link.

    Prose lines: all of a user's or system's message, a model's own words before its
    tool calls, none of a tool's output.
    """
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
    where = (
        f"{excerpt.source}, sample {excerpt.sample}, epoch {excerpt.epoch}, "
        f"message {position} ({message.role}, id {message.id})"
    )
    text, prose = _message_text(message)
    if message.role == "tool":
        prose = 0
    elif message.role != "assistant":
        prose = len(text.split("\n"))
    link = sample_link(str(reference["address"]), excerpt.sample, excerpt.epoch, message.id, viewer)
    return text, where, prose, link


def _select(excerpt: Excerpt, text: str) -> tuple[list[str], str, int]:
    """The selected lines, their span as words, and the 0-based index past the last."""
    # lines as an editor numbers them: a form feed or a Unicode separator does not end one
    lines = [line.removesuffix("\r") for line in text.expandtabs(4).removesuffix("\n").split("\n")]
    if excerpt.lines:
        first, last = excerpt.lines
        if last > len(lines):
            raise ValueError(
                f"excerpt {excerpt.id}: lines {first}-{last} run past the source's {len(lines)} lines"
            )
        return lines[first - 1 : last], f"lines {first}-{last}", last
    if excerpt.start is None:
        return lines, "", len(lines)
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
    return lines[first : last + 1], f"lines {first + 1}-{last + 1}", last + 1


def resolve(
    excerpt: Excerpt,
    bundle: Path,
    manifest: dict[str, Any],
    root: Path | None = None,
    samples: dict[tuple[str, str, int], Any] | None = None,
    viewer: str | None = None,
) -> Quote:
    """Read an excerpt's text from its source; any declaration that does not resolve fails.

    `viewer` is the Hawk log viewer's address (`viewer_base`), to link Hawk excerpts.
    """
    link: str | None = None
    prose = 0
    if excerpt.sample is None:
        text, where = _read_file(excerpt, bundle, manifest)
        kind = "code" if PurePosixPath(excerpt.source).suffix.lower() in CODE_SUFFIXES else "text"
    else:
        text, where, prose, link = _read_message(
            excerpt, manifest, root, {} if samples is None else samples, viewer
        )
        kind = "code"
    selected, span, past = _select(excerpt, text)
    if excerpt.sample is not None and past <= prose:
        # only the message's own words: a prompt, or what the model said
        kind = "text"
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
    for name in sorted(set(excerpt.redact), key=len, reverse=True):
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
        kind=excerpt.kind or kind,
        link=link,
    )


def resolve_all(
    excerpts: list[Excerpt],
    bundle: Path,
    manifest: dict[str, Any],
    root: Path | None = None,
    viewer: str | None = None,
) -> dict[str, Quote]:
    """Every declared excerpt, resolved; ids are unique."""
    seen: set[str] = set()
    for excerpt in excerpts:
        if excerpt.id in seen:
            raise ValueError(f"excerpt id {excerpt.id} is declared twice")
        seen.add(excerpt.id)
    samples: dict[tuple[str, str, int], Any] = {}
    return {e.id: resolve(e, bundle, manifest, root, samples, viewer) for e in excerpts}


def plain(quote: Quote) -> str:
    """An excerpt as text, for the agent to check what the report quotes.

    With where it was read from and its link: the report shows neither.
    """
    tail = [f"[... {quote.elided} more lines not shown]"] if quote.elided else []
    head = [f"== {quote.id} ({quote.kind}): {quote.caption}", f"   from {quote.reference}"]
    if quote.link:
        head.append(f"   link {quote.link}")
    return "\n".join([*head, *quote.lines, *tail])

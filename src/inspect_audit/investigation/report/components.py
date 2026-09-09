"""Figure and table helpers with a fixed, restrained style.

Call from a retained analysis script in the report directory. Titles belong in the
Quarto document, one short line each; captions carry the denominator.
"""

import csv
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure

COLOURS = ["#426b8c", "#d97757", "#5b9a6b", "#8c6b9e", "#c9a227", "#6b7c8c"]


def _axes(figsize: tuple[float, float] = (7, 4)) -> "tuple[Figure, Axes]":
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=figsize, layout="constrained")
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_axisbelow(True)
    ax.yaxis.grid(True, alpha=0.15)
    return fig, ax


def _save(fig: "Figure", output: str) -> None:
    import matplotlib.pyplot as plt

    Path(output).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160)
    plt.close(fig)


def bar_chart(labels: Sequence[str], values: Sequence[float], output: str, *, ylabel: str) -> None:
    """One bar per label."""
    if not labels or len(labels) != len(values):
        raise ValueError("Supply one value per label, with at least one bar")
    fig, ax = _axes()
    ax.bar(list(labels), list(values), color=COLOURS[0])
    ax.set_ylabel(ylabel)
    _save(fig, output)


def stacked_bars(
    labels: Sequence[str], parts: dict[str, Sequence[float]], output: str, *, ylabel: str
) -> None:
    """One bar per label, stacked by the named parts (e.g. outcome categories per model)."""
    if not labels or any(len(v) != len(labels) for v in parts.values()):
        raise ValueError("Every part needs one value per label")
    fig, ax = _axes()
    bottom = [0.0] * len(labels)
    for colour, (name, values) in zip(COLOURS * 4, parts.items(), strict=False):
        ax.bar(list(labels), list(values), bottom=bottom, label=name, color=colour)
        bottom = [b + v for b, v in zip(bottom, values, strict=True)]
    ax.set_ylabel(ylabel)
    ax.legend(frameon=False)
    _save(fig, output)


def line_chart(
    x: Sequence[float], series: dict[str, Sequence[float]], output: str, *, xlabel: str, ylabel: str, logx: bool = False
) -> None:
    """Lines over a shared x (e.g. accuracy against tokens or attempts), labelled at the end."""
    if not x or any(len(v) != len(x) for v in series.values()):
        raise ValueError("Every series needs one value per x")
    fig, ax = _axes()
    for colour, (name, values) in zip(COLOURS * 4, series.items(), strict=False):
        ax.plot(list(x), list(values), marker="o", ms=3, lw=1.5, color=colour)
        ax.annotate(name, (x[-1], values[-1]), xytext=(4, 0), textcoords="offset points", fontsize=8, va="center")
    if logx:
        ax.set_xscale("log")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    _save(fig, output)


def table_from_csv(path: str, *, columns: Sequence[str] | None = None, limit: int = 50) -> str:
    """Return a Markdown table from a CSV file, for inclusion in the Quarto document."""
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return "_(empty table)_"
    cols = list(columns or rows[0].keys())
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in rows[:limit]:
        lines.append("| " + " | ".join(str(row.get(c, "")) for c in cols) + " |")
    if len(rows) > limit:
        lines.append(f"| … {len(rows) - limit} more rows |" + " |" * (len(cols) - 1))
    return "\n".join(lines)


def transcript(speaker: str, text: str, source: str) -> str:
    """Escaped HTML for a Quarto raw HTML block, with a source locator."""
    return (
        f'<div class="transcript"><strong>{escape(speaker)}</strong>'
        f'<pre>{escape(text)}</pre><div class="source">{escape(source)}</div></div>'
    )

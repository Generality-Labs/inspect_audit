"""Small presentation helpers. Call from a retained analysis script."""

from html import escape
from pathlib import Path


def bar_chart(
    labels: list[str], values: list[float], output: str, *, ylabel: str
) -> None:
    """Save a plain bar chart; put the title and population in the Quarto document."""
    import matplotlib.pyplot as plt

    if not labels or len(labels) != len(values):
        raise ValueError("Supply one value per label, with at least one bar")
    fig, ax = plt.subplots(figsize=(7, 4), layout="constrained")
    ax.bar(labels, values, color="#426b8c")
    ax.set_ylabel(ylabel)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_axisbelow(True)
    ax.yaxis.grid(True, alpha=0.15)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160)
    plt.close(fig)


def transcript(speaker: str, text: str, source: str) -> str:
    """Return escaped HTML for a Quarto raw HTML block, with a source locator."""
    return (
        f'<div class="transcript"><strong>{escape(speaker)}</strong>'
        f'<pre>{escape(text)}</pre><div class="source">{escape(source)}</div></div>'
    )

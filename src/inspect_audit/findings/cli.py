"""`inspect-audit-findings`: run the deterministic producers over evals and write runs, parquet and summaries."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from inspect_ai.log import list_eval_logs

from .adapters import Context, slug
from .adapters import dataset as dataset_adapter
from .adapters import header as header_adapter
from .adapters import lint as lint_adapter
from .featured import FEATURED
from .io import findings_df, read_runs, runs_df, write_parquet, write_run
from .models import Run
from .producers import ProducerConfig
from .render import render_eval_summary, render_sweep_summary

EXTERNAL_PRODUCERS: dict[str, Callable[[str, Context], Run]] = {
    "lint": lint_adapter.run,
    "dataset": dataset_adapter.run,
}


def collect_logs(sources: Sequence[str]) -> list[Path]:
    """Local files and directories, and `hawk:` eval sets fetched through inspect_audit, as log paths."""
    paths: list[Path] = []
    for source in sources:
        location = source
        if source.startswith("hawk:"):
            from inspect_audit._registry import fetch_logs

            location = fetch_logs(source)
        path = Path(location.removeprefix("file://"))
        if path.is_file():
            paths.append(path)
        else:
            paths += [Path(info.name.removeprefix("file://")) for info in list_eval_logs(str(path), recursive=True)]
    return sorted(set(paths))


def sweep(targets: Sequence[str], ctx: Context, producers: set[str], *, header: bool = True) -> dict[str, list[Run]]:
    """Header first (when logs were supplied), then the selected external producers, for every target. Nothing raises."""
    runs_by_eval: dict[str, list[Run]] = {}
    for target in targets:
        runs = [header_adapter.run(target, ctx)] if header else []
        for name in sorted(producers):
            runs.append(EXTERNAL_PRODUCERS[name](target, ctx))
        runs_by_eval[target] = runs
    return runs_by_eval


_FILE_NAMES = {header_adapter.PRODUCER: "header", lint_adapter.PRODUCER: "lint", dataset_adapter.PRODUCER: "dataset"}


def write_outputs(out: Path, runs_by_eval: Mapping[str, Sequence[Run]]) -> None:
    all_runs: list[Run] = []
    for target, runs in runs_by_eval.items():
        directory = out / slug(target)
        for run in runs:
            write_run(run, directory / f"{_FILE_NAMES.get(run.producer, slug(run.producer))}.run.json")
        (directory / "SUMMARY.md").write_text(render_eval_summary(runs))
        all_runs += runs
    write_parquet(findings_df(all_runs), out / "findings.parquet")
    write_parquet(runs_df(all_runs), out / "runs.parquet")
    (out / "SUMMARY.md").write_text(render_sweep_summary(runs_by_eval))


def _summaries_from_disk(out: Path) -> int:
    runs_by_eval: dict[str, list[Run]] = {}
    for run in read_runs(out):
        runs_by_eval.setdefault(run.subject.eval, []).append(run)
    if not runs_by_eval:
        print(f"no *.run.json under {out}", file=sys.stderr)
        return 2
    write_outputs(out, runs_by_eval)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="inspect-audit-findings")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="run the producers over evals")
    run_p.add_argument("--root", required=True, type=Path, help="inspect_evals checkout")
    run_p.add_argument("--logs", action="append", default=[], help="log dir, .eval file, or hawk:<eval-set-id>; repeatable")
    run_p.add_argument("--out", required=True, type=Path)
    run_p.add_argument("--producers", default="lint,dataset", help="external producers to run; the header producer runs whenever --logs is given")
    run_p.add_argument("--resolve", action="store_true", help="resolve the task to compare logged sample ids (needs inspect_evals importable)")
    run_p.add_argument("--featured", action="store_true", help="add the 35 Featured evals")
    run_p.add_argument("targets", nargs="*", help="registry names, e.g. inspect_evals/stereoset")
    sum_p = sub.add_parser("summary", help="re-render summaries from existing run files")
    sum_p.add_argument("out", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "summary":
        return _summaries_from_disk(args.out)

    targets = list(args.targets) + ([f"inspect_evals/{name}" for name in FEATURED] if args.featured else [])
    if not targets:
        print("at least one target or --featured is required", file=sys.stderr)
        return 2
    producers = {name for name in str(args.producers).split(",") if name}
    unknown = producers - set(EXTERNAL_PRODUCERS)
    if unknown:
        print(f"unknown producers: {', '.join(sorted(unknown))}; known: {', '.join(EXTERNAL_PRODUCERS)}", file=sys.stderr)
        return 2
    ctx = Context(
        ie_root=args.root.resolve(),
        logs=collect_logs(args.logs),
        out_dir=args.out.resolve(),
        producers=ProducerConfig.from_env(),
        resolve=bool(args.resolve),
    )
    # the header producer needs logs; with no --logs it is not requested rather than skipped,
    # so a lint-and-dataset sweep exits 0 when its producers all ran
    runs_by_eval = sweep(targets, ctx, producers, header=bool(args.logs))
    write_outputs(args.out, runs_by_eval)
    skipped = any(
        run.outcomes and all(outcome.status == "skip" for outcome in run.outcomes)
        for runs in runs_by_eval.values()
        for run in runs
    )
    return 1 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())

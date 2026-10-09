"""`inspect-audit-findings`: run the deterministic producers over evals and write runs, parquet and summaries."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from inspect_ai.log import list_eval_logs
from pydantic import ValidationError

from . import hawk
from .adapters import Context, ProducerError, slug
from .adapters import dataset as dataset_adapter
from .adapters import header as header_adapter
from .adapters import lint as lint_adapter
from .config import DEFAULT_CONFIG_PATH, load_config
from .export import write_export
from .featured import FEATURED
from .fs import StoreFS, as_store_fs
from .io import findings_df, read_current, read_runs, runs_df, write_parquet, write_run
from .leads import NoRunsError, leads_markdown
from .models import Run
from .producers import ProducerConfig
from .render import render_eval_summary, render_sweep_summary
from .review import Review, apply_review, load_review, unmatched_issue_findings
from .store import Selection, Store

EXTERNAL_PRODUCERS: dict[str, Callable[[str, Context], Run]] = {
    "lint": lint_adapter.run,
    "dataset": dataset_adapter.run,
}


def collect_logs(
    sources: Sequence[str],
    *,
    hawk_cache: Path = hawk.DEFAULT_CACHE,
    producers: ProducerConfig | None = None,
) -> list[Path]:
    """Local files and directories, plus `hawk:<eval-set-id>` sets pulled into the cache, as log paths."""
    config = producers or ProducerConfig.from_env()
    paths: list[Path] = []
    for source in sources:
        if source.startswith("hawk:"):
            paths += hawk.download_eval_set(source.removeprefix("hawk:"), hawk_cache, config)
            continue
        path = Path(source.removeprefix("file://"))
        if path.is_file():
            paths.append(path)
        else:
            paths += [
                Path(info.name.removeprefix("file://"))
                for info in list_eval_logs(str(path), recursive=True)
            ]
    return sorted(set(paths))


def sweep(
    targets: Sequence[str], ctx: Context, producers: set[str], *, header: bool = True
) -> dict[str, list[Run]]:
    """Header first (when logs were supplied), then the selected external producers, for every target. Nothing raises."""
    runs_by_eval: dict[str, list[Run]] = {}
    for target in targets:
        runs = [header_adapter.run(target, ctx)] if header else []
        for name in sorted(producers):
            runs.append(EXTERNAL_PRODUCERS[name](target, ctx))
        runs_by_eval[target] = runs
    return runs_by_eval


def _run_key(fs: StoreFS, slug_dir: str, run: Run) -> str:
    """`<slug>/runs/<run id>.run.json`, never reusing a name: two sweeps in one second get distinct objects."""
    key = f"{slug_dir}/runs/{run.id}.run.json"
    counter = 2
    while fs.exists(key):
        key = f"{slug_dir}/runs/{run.id}-{counter}.run.json"
        counter += 1
    return key


def _renamed(run: Run, file_id: str) -> Run:
    """The run under a new id, with its findings' record ids and run_id moved with it."""
    findings = [
        finding.model_copy(
            update={
                "run_id": file_id,
                "id": f"{file_id}/{n}" if finding.id == f"{run.id}/{n}" else finding.id,
            }
        )
        for n, finding in enumerate(run.findings, 1)
    ]
    return run.model_copy(update={"id": file_id, "findings": findings})


def write_outputs(
    store: StoreFS | str | Path,
    runs_by_eval: Mapping[str, Sequence[Run]],
    review: Review | None = None,
) -> None:
    """Append the new runs, then render everything from the current view (newest run per producer)."""
    fs = as_store_fs(store)
    for target, runs in runs_by_eval.items():
        for run in runs:
            key = _run_key(fs, slug(target), run)
            file_id = key.rsplit("/", 1)[-1].removesuffix(".run.json")
            write_run(run if file_id == run.id else _renamed(run, file_id), fs, key)
    render_current(fs, review)


def render_current(store: StoreFS | str | Path, review: Review | None = None) -> None:
    """Parquet, summaries and export from the current view, with review decisions applied."""
    fs = as_store_fs(store)
    review = review or Review()
    runs_by_eval: dict[str, list[Run]] = {}
    for run in apply_review(read_current(fs), review):
        runs_by_eval.setdefault(run.subject.eval, []).append(run)
    all_runs: list[Run] = []
    for target, runs in runs_by_eval.items():
        fs.write_text(f"{slug(target)}/SUMMARY.md", render_eval_summary(runs, issues=review.issues))
        all_runs += runs
    write_parquet(findings_df(all_runs), fs, "findings.parquet")
    write_parquet(runs_df(all_runs), fs, "runs.parquet")
    write_export(fs, runs_by_eval, review, history=read_runs(fs))
    fs.write_text("SUMMARY.md", render_sweep_summary(runs_by_eval))
    for issue_id, fingerprints in sorted(unmatched_issue_findings(review, all_runs).items()):
        print(
            f"warning: issue {issue_id} lists {len(fingerprints)} fingerprint(s) with no current "
            f"observation: {', '.join(fingerprints)}",
            file=sys.stderr,
        )
    for issue in review.issues:
        if issue.subject not in runs_by_eval:
            print(
                f"warning: issue {issue.id} names subject {issue.subject}, which has no runs in the "
                "current view, so it appears in no summary",
                file=sys.stderr,
            )
        elsewhere = sorted(
            {
                finding.subject.eval
                for run in all_runs
                for finding in run.findings
                if finding.issue == issue.id and finding.subject.eval != issue.subject
            }
        )
        if elsewhere:
            print(
                f"warning: issue {issue.id} ({issue.subject}) is linked to findings on "
                f"{', '.join(elsewhere)}",
                file=sys.stderr,
            )


def _load_review_or_exit(directory: Path, *, explicit: bool) -> Review | None:
    """The review files under `directory`, or None after printing why they could not be loaded.

    A directory the operator named must exist: a typo would otherwise silently apply nothing.
    """
    if explicit and not directory.is_dir():
        print(f"--review {directory} is not a directory", file=sys.stderr)
        return None
    try:
        return load_review(directory)
    except (OSError, ValueError) as ex:
        print(f"could not load review files under {directory}: {ex}", file=sys.stderr)
        return None


def _summaries_from_disk(out: Path, review: Review) -> int:
    fs = as_store_fs(out)
    if not read_current(fs):
        print(f"no runs under {fs.locator}", file=sys.stderr)
        return 2
    render_current(fs, review)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="inspect-audit-findings")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="run the producers over evals")
    run_p.add_argument("--root", required=True, type=Path, help="inspect_evals checkout")
    run_p.add_argument(
        "--logs",
        action="append",
        default=[],
        help="log dir, .eval file, or hawk:<eval-set-id>; repeatable",
    )
    run_p.add_argument(
        "--hawk-task",
        action="append",
        default=[],
        help="pull every Hawk eval set that ran this task (unqualified name matches); repeatable",
    )
    run_p.add_argument(
        "--hawk-limit",
        type=int,
        default=20,
        help="refuse when --hawk-task resolves to more eval sets than this",
    )
    run_p.add_argument(
        "--hawk-cache",
        type=Path,
        default=hawk.DEFAULT_CACHE,
        help="where Hawk downloads are kept between runs",
    )
    run_p.add_argument("--out", required=True, type=Path)
    run_p.add_argument(
        "--producers",
        default="lint,dataset",
        help="external producers to run; the header producer runs whenever --logs is given",
    )
    run_p.add_argument(
        "--resolve",
        action="store_true",
        help="resolve the task to compare logged sample ids (needs inspect_evals importable)",
    )
    run_p.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="per-eval declaration of what to scan and which logs count (default: the packaged pilot.yaml)",
    )
    run_p.add_argument(
        "--review",
        type=Path,
        default=None,
        help="directory holding suppressions.yaml and issues.yaml (default: --out)",
    )
    run_p.add_argument("--featured", action="store_true", help="add the 35 Featured evals")
    run_p.add_argument("targets", nargs="*", help="registry names, e.g. inspect_evals/stereoset")
    sum_p = sub.add_parser("summary", help="re-render summaries from existing run files")
    sum_p.add_argument("out", type=Path)
    sum_p.add_argument(
        "--review",
        type=Path,
        default=None,
        help="directory holding suppressions.yaml and issues.yaml (default: the out dir)",
    )
    sets_p = sub.add_parser("hawk-sets", help="list the Hawk eval sets that ran a task")
    sets_p.add_argument("task", help="registry name, e.g. inspect_evals/scicode")
    pull_p = sub.add_parser(
        "hawk-pull",
        help="fetch the logs and artifact bundles a manifest names into its gitignored dest",
    )
    pull_p.add_argument("--manifest", type=Path, default=Path("scripts/hawk-artefacts.yaml"))
    pull_p.add_argument("--dest", type=Path, default=None, help="override the manifest's dest")
    leads_p = sub.add_parser("leads", help="one eval's reviewed findings as leads for an agent")
    leads_p.add_argument("--out", required=True, type=Path, help="the findings output directory")
    leads_p.add_argument(
        "--review",
        type=Path,
        default=None,
        help="directory holding suppressions.yaml and issues.yaml (default: --out)",
    )
    leads_p.add_argument(
        "--sample", default=None, help="only leads whose locations name this sample id"
    )
    leads_p.add_argument(
        "--write", type=Path, default=None, help="write LEADS.md here instead of printing"
    )
    leads_p.add_argument("eval", help="registry name, e.g. inspect_evals/scicode")

    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--out", required=True, type=Path, help="the findings output directory")
    shared.add_argument(
        "--review",
        type=Path,
        default=None,
        help="directory holding suppressions.yaml and issues.yaml (default: --out)",
    )
    shared.add_argument(
        "--author",
        default=None,
        help="'Name <email>' recorded on the decision and its commit (default: git config)",
    )
    review_p = sub.add_parser("review", help="record a review decision through the Store")
    verbs = review_p.add_subparsers(dest="verb", required=True)
    sup_p = verbs.add_parser("suppress", parents=[shared], help="suppress a rule as noise")
    sup_p.add_argument("--rule", required=True, help="producer rule id, e.g. IEBP008")
    sup_p.add_argument("--eval", default=None, help="only on this eval (default: every eval)")
    sup_p.add_argument("--producer", default=None, help="only from this producer")
    sup_p.add_argument("--kind", default="false_positive", help="suppression kind")
    sup_p.add_argument("--reason", required=True, help="why these observations are noise")
    acc_p = verbs.add_parser("accept", parents=[shared], help="accept findings as an issue")
    acc_p.add_argument("--eval", default=None, help="the eval the findings belong to")
    acc_p.add_argument("--rule", default=None, help="every current finding of this rule")
    acc_p.add_argument("--id", action="append", default=None, help="record id; repeatable")
    acc_p.add_argument("--title", required=True, help="issue title as a maintainer would search")
    acc_p.add_argument("--reason", default=None, help="why this is a real problem")
    link_p = verbs.add_parser("link", parents=[shared], help="record an issue's GitHub URL")
    link_p.add_argument("issue", help="store issue id, e.g. ISS-0001")
    link_p.add_argument("url", help="GitHub issue URL")
    return parser


def _git_config(directory: Path, key: str) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(directory), "config", "--get", key],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() or None


def _author(review_dir: Path, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    name = _git_config(review_dir, "user.name")
    email = _git_config(review_dir, "user.email")
    return f"{name} <{email}>" if name and email else None


def _review(args: argparse.Namespace) -> int:
    review_dir: Path = args.review or args.out
    store = Store(args.out, review_dir)
    author = _author(review_dir, args.author)
    if author is None:
        print(
            "no author: pass --author 'Name <email>' or set git user.name and user.email",
            file=sys.stderr,
        )
        return 2
    try:
        if args.verb == "suppress":
            entry = store.suppress(
                rule=args.rule,
                subject=args.eval or "*",
                producer=args.producer,
                kind=args.kind,
                author=author,
                reason=args.reason,
            )
            print(f"suppressed {entry.rule} on {entry.subject}")
        elif args.verb == "accept":
            selection = Selection(eval=args.eval, rule=args.rule, ids=tuple(args.id or ()))
            issue = store.accept(selection, title=args.title, author=author, reason=args.reason)
            print(f"accepted {issue.id}: {issue.title} ({len(issue.findings)} observation(s))")
        else:
            store.link(args.issue, args.url, author=author)
            print(f"linked {args.issue} to {args.url}")
    except ValueError as ex:  # StoreError, or a malformed review file named by load_review
        print(str(ex), file=sys.stderr)
        return 2
    return 0


def _leads(
    out: Path, review_dir: Path | None, eval: str, sample: str | None, write: Path | None
) -> int:
    review = _load_review_or_exit(review_dir or out, explicit=review_dir is not None)
    if review is None:
        return 2
    try:
        text = leads_markdown(out, eval, review, sample_id=sample)
    except NoRunsError as ex:
        print(f"{ex} under {out}", file=sys.stderr)
        return 2
    if write is not None:
        write.parent.mkdir(parents=True, exist_ok=True)
        write.write_text(text)
    else:
        print(text, end="")
    return 0


def _hawk_pull(manifest_path: Path, dest: Path | None) -> int:
    try:
        manifest = hawk.load_manifest(manifest_path)
    except (OSError, ValueError) as ex:
        print(str(ex), file=sys.stderr)
        return 2
    if dest is not None:
        manifest = manifest.model_copy(update={"dest": dest})
    results = hawk.pull_manifest(manifest, ProducerConfig.from_env())
    for result in results:
        status = (
            f"{result.files} file(s)"
            if result.error is None
            else f"FAILED: {result.error.splitlines()[0][:160]}"
        )
        print(f"{result.kind:9s} {result.id:45s} {status}")
    print(f"into {manifest.dest.resolve()}")
    return 1 if any(r.error for r in results) else 0


def _hawk_sets(task: str) -> int:
    try:
        found = hawk.find_eval_sets(task)
    except ProducerError as ex:
        print(str(ex), file=sys.stderr)
        return 2
    if not found:
        print(f"no Hawk eval sets ran {task}")
        return 0
    print(f"{'Eval set':45s} {'Created':21s} {'Evals':>5s}  Tasks")
    for entry in found:
        print(
            f"{entry.eval_set_id:45s} {entry.created_at:21s} {entry.eval_count:5d}  {', '.join(entry.task_names)}"
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "summary":
        review = _load_review_or_exit(args.review or args.out, explicit=args.review is not None)
        if review is None:
            return 2
        return _summaries_from_disk(args.out, review)
    if args.command == "hawk-sets":
        return _hawk_sets(args.task)
    if args.command == "hawk-pull":
        return _hawk_pull(args.manifest, args.dest)
    if args.command == "leads":
        return _leads(args.out, args.review, args.eval, args.sample, args.write)
    if args.command == "review":
        return _review(args)

    targets = list(args.targets) + (
        [f"inspect_evals/{name}" for name in FEATURED] if args.featured else []
    )
    if not targets:
        print("at least one target or --featured is required", file=sys.stderr)
        return 2
    producers = {name for name in str(args.producers).split(",") if name}
    unknown = producers - set(EXTERNAL_PRODUCERS)
    if unknown:
        print(
            f"unknown producers: {', '.join(sorted(unknown))}; known: {', '.join(EXTERNAL_PRODUCERS)}",
            file=sys.stderr,
        )
        return 2
    sources = list(args.logs)
    for task in args.hawk_task:
        try:
            found = hawk.find_eval_sets(task)
        except ProducerError as ex:
            print(str(ex), file=sys.stderr)
            return 2
        if len(found) > args.hawk_limit:
            print(
                f"--hawk-task {task} matches {len(found)} eval sets, more than --hawk-limit {args.hawk_limit}; "
                "raise the limit or name sets with --logs hawk:<id>",
                file=sys.stderr,
            )
            return 2
        sources += [f"hawk:{entry.eval_set_id}" for entry in found]
    try:
        config = load_config(args.config)
    except (OSError, ValueError, ValidationError) as ex:
        print(f"could not load {args.config}: {ex}", file=sys.stderr)
        return 2
    review = _load_review_or_exit(args.review or args.out, explicit=args.review is not None)
    if review is None:
        return 2
    producers_config = ProducerConfig.from_env()
    try:
        logs = collect_logs(sources, hawk_cache=args.hawk_cache, producers=producers_config)
    except ProducerError as ex:
        print(str(ex), file=sys.stderr)
        return 2
    ctx = Context(
        ie_root=args.root.resolve(),
        logs=logs,
        out_dir=args.out.resolve(),
        producers=producers_config,
        resolve=bool(args.resolve),
        config=config,
    )
    # the header producer needs logs; with no log sources it is not requested rather than skipped,
    # so a lint-and-dataset sweep exits 0 when its producers all ran
    runs_by_eval = sweep(targets, ctx, producers, header=bool(sources))
    write_outputs(args.out, runs_by_eval, review)
    skipped = any(
        run.outcomes and all(outcome.status == "skip" for outcome in run.outcomes)
        for runs in runs_by_eval.values()
        for run in runs
    )
    return 1 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())

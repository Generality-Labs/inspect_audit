"""The audit's declared inputs, staged on the host.

The repository snapshot, logs, paper and docs, and the starting workspace; the box gets
a copy when the audit starts.
"""

import errno
import json
import os
import shutil
import subprocess
import tarfile
from logging import getLogger
from pathlib import Path
from uuid import uuid4

from inspect_ai.log import list_eval_logs

from ._log_sources import fetch_logs, is_remote_logs

logger = getLogger(__name__)


def _snapshot_repo(
    repo: str, revision: str | None, inputs: Path, paths: list[str] | None
) -> dict[str, object]:
    source = Path(repo).expanduser()
    if source.is_dir():
        commit = subprocess.check_output(
            [
                "git",
                "-C",
                str(source),
                "rev-parse",
                "--verify",
                f"{revision or 'HEAD'}^{{commit}}",
            ],
            text=True,
        ).strip()
        # Limit the snapshot to the declared paths.
        archive = inputs / "source.tar"
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "archive",
                "--format=tar",
                f"--output={archive}",
                commit,
                *(["--", *paths] if paths else []),
            ],
            check=True,
        )
        tree = inputs / "source"
        tree.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(tree, filter="data")
        archive.unlink()
        return {
            "repo": str(source.resolve()),
            "revision": commit,
            "paths": paths or ["."],
            "snapshot": "/inputs/source",
            "source": "committed snapshot; excludes working-tree changes and submodule contents",
        }
    if not repo.startswith("https://"):
        raise ValueError("repo must be a local Git repository or an HTTPS Git URL")
    return {
        "repo": repo,
        "revision": revision or "HEAD",
        "paths": paths or ["."],
        "snapshot": None,
        "source": "remote repository; clone it yourself into /workspace",
    }


def _fetch_paper(paper: str, inputs: Path) -> str:
    """Stage the paper under inputs/paper/: a local file, or a URL downloaded now.

    An arXiv abstract URL is turned into its PDF. A failed download keeps the URL
    in the seed so the agent can try itself, with the failure recorded.
    """
    from ._paper import paper_file

    (inputs / "paper").mkdir(exist_ok=True)
    if not Path(paper).expanduser().is_file() and not paper.startswith(("http://", "https://")):
        return paper
    try:
        name, data = paper_file(paper)
    except Exception as ex:  # network is optional at prep time
        logger.warning(f"could not download the paper from {paper}: {ex}")
        return paper
    (inputs / "paper" / name).write_bytes(data)
    return f"/inputs/paper/{name}"


def _stage_docs(docs: list[str], inputs: Path) -> list[str]:
    """Copy documentation trees into inputs/docs/<name>/."""
    staged: list[str] = []
    for source in docs:
        path = Path(source).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"Docs directory does not exist: {source}")
        dest = inputs / "docs" / path.name
        shutil.copytree(
            path,
            dest,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", "node_modules"),
        )
        staged.append(f"/inputs/docs/{path.name}")
    return staged


def _link_or_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest)
    except OSError as ex:
        if ex.errno != errno.EXDEV:
            raise
        shutil.copyfile(src, dest)


def prepare_workspace(
    repo: str,
    revision: str | None,
    paths: list[str] | None,
    logs: list[str],
    paper: str | None,
    docs: list[str],
    overview: str,
    target_task: str | None,
    output_dir: str,
) -> Path:
    """Stage the declared inputs on the host; the box gets a copy at the start."""
    root = Path(output_dir).expanduser().resolve() / uuid4().hex
    inputs = root / "inputs"
    work = root / "work"
    inputs.mkdir(parents=True)
    work.mkdir()
    seed: dict[str, object] = dict(_snapshot_repo(repo, revision, inputs, paths))
    log_index = []
    for index, source in enumerate(logs):
        # a hawk: source is downloaded now; it keeps its address, so a job can be
        # pointed at the same logs and a citation names where they live
        local = Path(fetch_logs(source)) if is_remote_logs(source) else None
        path = local or Path(source).expanduser().resolve()
        if not path.exists():
            raise ValueError(f"Log source does not exist: {source}")
        files = (
            [path]
            if path.is_file()
            else [
                Path(info.name.removeprefix("file://")).resolve()
                for info in list_eval_logs(str(path), recursive=True)
            ]
        )
        if not files:
            raise ValueError(f"No Inspect logs found: {source}")
        for file in files:
            relative = Path(str(index)) / (file.name if path.is_file() else file.relative_to(path))
            _link_or_copy(file, inputs / "logs" / relative)
            log_index.append(
                {
                    "source": f"{source.rstrip('/')}/{file.name}" if local else str(file),
                    "staged": f"/inputs/logs/{relative}",
                    "remote": source if local else None,
                }
            )
    staged_paper = _fetch_paper(paper, inputs) if paper else None
    staged_docs = _stage_docs(docs, inputs)
    seed.update(
        task=target_task,
        overview=overview,
        paper=staged_paper,
        docs=staged_docs,
        logs=log_index,
    )
    (inputs / "seed.json").write_text(json.dumps(seed, indent=2))
    from ._framework import stage_framework

    stage_framework(work / "analysis")
    (work / "report").mkdir()
    (work / "journal.md").write_text(
        "# Activity journal\n\nAppend actions and corrections with evidence references.\n"
    )
    return root

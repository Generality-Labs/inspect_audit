r"""`inspect-audit-new`: a ready Hawk eval-set config for auditing a benchmark.

  inspect-audit-new https://github.com/UKGovernmentBEIS/inspect_evals --task swe_bench \
      --models openrouter/deepseek/deepseek-v4.1-flash-1017 > audit.eval-set.yaml
  hawk eval-set audit.eval-set.yaml

Everything an audit's task arguments need beyond the repository, the task and the models
is read from the repository at a pinned commit, the way the audit reads it itself:
the commit (HEAD unless given), the task's registry name, the module that defines it
(`source`), the pip spec that installs it on a runner, with the extra named after the
task's folder when the package has one. Models are spelled the way our Hawk's proxy
routes them (`openai/openrouter/...`, FRICTION 89). Nothing is priced or capped here:
the audit checks prices when it starts, and the auditor manages spend (FRICTION 100).
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

import yaml

from ._repository import _declaring_file, only_task  # pyright: ignore[reportPrivateUsage]

AUDIT_REPO = "https://github.com/Generality-Labs/inspect_audit"


class NewAuditError(ValueError):
    """The repository does not say something the config needs, and nobody declared it."""


def hawk_model(name: str) -> str:
    """A model as our Hawk's proxy routes it: OpenRouter models through its OpenAI API."""
    if name.startswith("openrouter/"):
        return f"openai/{name}"
    return name


def checkout(repo: str, revision: str | None, into: Path) -> tuple[Path, str]:
    """The repository at `revision` (default HEAD), shallow; and its full commit."""
    local = Path(repo).expanduser()
    if local.is_dir():
        commit = _git(local, "rev-parse", "--verify", f"{revision or 'HEAD'}^{{commit}}")
        return local, commit
    _git(into, "init", "-q")
    _git(into, "remote", "add", "origin", repo)
    _git(into, "fetch", "-q", "--depth", "1", "origin", revision or "HEAD")
    _git(into, "checkout", "-q", "FETCH_HEAD")
    return into, _git(into, "rev-parse", "HEAD")


def _git(where: Path, *args: str) -> str:
    where.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(
        ["git", "-C", str(where), *args], capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise NewAuditError(f"git {' '.join(args)} failed: {done.stderr.strip()[-500:]}")
    return done.stdout.strip()


def _pyproject(root: Path) -> dict[str, Any]:
    path = root / "pyproject.toml"
    return tomllib.loads(path.read_text()) if path.is_file() else {}


def _declaring_module(root: Path, name: str) -> tuple[str, Path]:
    """The dotted module that defines task `name`, and its file."""
    directory = _declaring_file(root, name)
    if directory is None:
        raise NewAuditError(f"no @task named {name!r} in the repository: pass --task exactly")
    pattern = re.compile(rf"^\s*def {re.escape(name)}\s*\(", re.MULTILINE)
    candidates = sorted(directory.glob("*.py")) if directory.is_dir() else [directory]
    found = [p for p in candidates if "@task" in p.read_text(errors="ignore")]
    file = next((p for p in found if pattern.search(p.read_text(errors="ignore"))), None)
    if file is None and len(found) == 1:
        file = found[0]
    if file is None:
        raise NewAuditError(f"cannot tell which file in {directory} defines {name!r}")
    # the top-level package: up while the folder above is still a package
    top = file.parent
    while (top.parent / "__init__.py").is_file():
        top = top.parent
    parts = file.relative_to(top.parent).with_suffix("").parts
    return ".".join(parts if parts[-1] != "__init__" else parts[:-1]), file


def config(
    repo: str,
    *,
    models: dict[str, list[str]],
    task: str | None = None,
    revision: str | None = None,
    logs: list[str] | None = None,
    budget_usd: float | None = None,
    audit_revision: str = "main",
    task_args: dict[str, Any] | None = None,
    name: str | None = None,
    workdir: Path | None = None,
) -> dict[str, Any]:
    """The eval-set config, as a mapping (`main` prints it as YAML)."""
    if not models.get("auditor") or not models.get("investigators"):
        raise NewAuditError("name the auditor and investigator models (there are no defaults)")
    root, commit = checkout(repo, revision, workdir or Path(tempfile.mkdtemp(prefix="ia-new-")))
    function = (task or "").split("/")[-1] or only_task(root)
    if not function:
        raise NewAuditError("the repository declares several tasks: pass --task")
    source, file = _declaring_module(root, function)

    project = _pyproject(root)
    dist = str(project.get("project", {}).get("name") or "")
    if not dist:
        raise NewAuditError("the repository has no pyproject.toml [project] name to install")
    plugins = project.get("project", {}).get("entry-points", {}).get("inspect_ai", {})
    registered = f"{next(iter(plugins))}/{function}" if plugins else function
    extras = project.get("project", {}).get("optional-dependencies", {})
    folder = file.parent.name
    extra = f"[{folder}]" if folder in extras else ""
    url = repo.removesuffix(".git") if not Path(repo).expanduser().is_dir() else _origin(root)
    task_package = f"{dist}{extra} @ git+{url}@{commit}"

    from .dispatch.jobs import INSPECT_VERSION

    ours = f"inspect-audit[runner,k8s] @ git+{AUDIT_REPO}@{_commit_of(AUDIT_REPO, audit_revision)}"
    spelled = {role: [hawk_model(m) for m in names] for role, names in models.items()}
    limits: dict[str, Any] = {"budget_usd": budget_usd} if budget_usd is not None else {}
    args: dict[str, Any] = {
        "repo": url,
        "revision": commit,
        "target_task": registered,
        "source": source,
        "task_package": task_package,
        # benchmark-specific settings the repository cannot say (e.g. sandbox_type: k8s)
        **({"task_args": dict(task_args)} if task_args else {}),
        "investigation_samples": "all",
        "policy": {"models": spelled, **({"limits": limits} if limits else {})},
        **({"logs": list(logs)} if logs else {}),
        "runs": "hawk",
        # TEMPORARY (FRICTION 93): children run with the operator's login until our
        # Hawk has its token service; restricted is the default once it does
        "credential_mode": "user",
        "box": "k8s",
    }
    return {
        "name": name or f"audit-{function.replace('_', '-')}"[:40],
        "packages": [f"inspect-ai=={INSPECT_VERSION}", ours, task_package],
        "tasks": [
            {"package": ours, "name": "inspect_audit", "items": [{"name": "audit", "args": args}]}
        ],
        "models": [{"package": "inspect-ai", "items": [{"name": m} for m in spelled["auditor"]]}],
        "retry_attempts": 0,
    }


def _commit_of(repo: str, ref: str) -> str:
    """A branch or tag as the commit it names now: runners install a commit, not a branch."""
    if re.fullmatch(r"[0-9a-f]{40}", ref):
        return ref
    done = subprocess.run(
        ["git", "ls-remote", repo, ref], capture_output=True, text=True, check=False
    )
    line = done.stdout.split("\n", 1)[0]
    if done.returncode != 0 or not line:
        raise NewAuditError(f"{repo} has no {ref!r}: pass --audit-revision as a commit")
    return line.split()[0]


def _origin(root: Path) -> str:
    origin = _git(root, "remote", "get-url", "origin")
    return origin.removesuffix(".git").replace("git@github.com:", "https://github.com/")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("repo", help="the benchmark's Git repository (URL or local checkout)")
    parser.add_argument("--task", help="the task to audit; default: the only one it declares")
    parser.add_argument("--revision", help="commit, branch or tag; default HEAD, pinned")
    parser.add_argument(
        "--models",
        help="one model for every role (auditor, investigators, subjects); roles below override",
    )
    for role in ("auditor", "investigators", "subjects", "judges"):
        parser.add_argument(f"--{role}", nargs="+", default=None)
    parser.add_argument("--logs", nargs="+", help="supplied logs, e.g. hawk:<eval set id>")
    parser.add_argument("--budget-usd", type=float, help="the audit's one spend stop")
    parser.add_argument(
        "--audit-revision", default="main", help="the inspect_audit commit the runners install"
    )
    parser.add_argument(
        "--task-args",
        type=json.loads,
        help='the benchmark\'s task arguments as JSON, e.g. \'{"sandbox_type": "k8s"}\'',
    )
    parser.add_argument("--name", help="the eval set's name")
    args = parser.parse_args(argv)
    base = [args.models] if args.models else []
    models = {
        "auditor": args.auditor or base,
        "investigators": args.investigators or base,
        "subjects": args.subjects or base,
        "judges": args.judges or [],
    }
    try:
        built = config(
            args.repo,
            models=models,
            task=args.task,
            revision=args.revision,
            logs=args.logs,
            budget_usd=args.budget_usd,
            audit_revision=args.audit_revision,
            task_args=args.task_args,
            name=args.name,
        )
    except NewAuditError as ex:
        print(f"inspect-audit-new: {ex}", file=sys.stderr)
        return 2
    sys.stdout.write(yaml.safe_dump(built, sort_keys=False, width=100))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

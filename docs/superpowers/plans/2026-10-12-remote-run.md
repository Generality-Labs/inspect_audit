# Remote run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `inspect-audit-findings run` works against any store locator, so a sweep on a laptop or in a workflow can write straight to an R2 or S3 bucket. Slice two of the portable store spec, inspect_audit side.

**Architecture:** Producers keep writing to a local work directory. For a local store that directory is the store root, as today. For a remote store it is a temporary directory, and `write_outputs` uploads what the producers left there: the run files, which already go through `StoreFS`, and the dataset scan directories, which gain a store key `<slug>/scans/<run id>/` instead of a random `inspect_dataset_*` directory beside the evals. A run's `inputs.scan_dir` records that key, so the evidence a dataset run cites is addressable in the store whichever backend it is on.

**Tech Stack:** Python 3.13, fsspec via inspect_ai, pytest with the `memory://` backend. Gate: `uv run pre-commit run --all-files`, `uv run basedpyright src`, `uv run pytest -q -p no:cacheprovider`.

**Spec:** `docs/superpowers/specs/2026-10-09-portable-store-design.md`, "The store" (backend in the code, layout), migration step 3, "Testing" (the R2 integration test).

## Global Constraints

- Python `>=3.11`; develop on 3.13. Ruff and basedpyright strict per `pyproject.toml`; no new ignores; `DTZ` rules.
- No new dependency.
- Runs are immutable objects; a scan directory is written once under its run's key and never edited.
- Local stores keep today's behaviour and performance: nothing is copied when the work directory is the store root.
- Nothing in the package names a bucket, account or organisation; the R2 test reads its locator from the environment and skips without it.
- Stage new files before the gate. Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Branch `feat/remote-run` targets `feat/findings`.

## Review Focus

1. Two dataset sweeps of one eval in the same second: the second run file gets the `-2` suffix, and its scan directory must land under the `-2` key with `inputs.scan_dir` updated, on both backends. Test in Task 2.
2. A dataset run that skipped (no scan directory produced) must upload nothing and record no `scan_dir`. Test in Task 2.
3. A remote sweep must leave no temporary directory behind on success or on a producer failure. Test in Task 2.
4. A local store must not copy its scan directories onto themselves or rename them when the run id did not change. Test in Task 1.
5. `summary` and `review` on a store written by a remote `run` must render the same files as a local one, scan keys included. Test in Task 2.

______________________________________________________________________

### Task 1: Scan directories get store keys

**Files:**

- Modify: `src/inspect_audit/findings/adapters/dataset.py` (`run`: the two `mkdtemp` sites), `src/inspect_audit/findings/cli.py` (`_renamed`, `write_outputs`)
- Test: `tests/findings/test_dataset_adapter.py`, `tests/findings/test_cli.py`

**Interfaces:**

- Produces: `scan_key(target, run_id) -> str` in `adapters/dataset.py` returning `f"{slug(target)}/scans/{run_id}"`. The dataset adapter writes its scan directory at `ctx.out_dir / scan_key(...)` (parents created) and records `inputs["scan_dir"] = scan_key(...)`, a relative key, not an absolute path. `_renamed(run, file_id)` also rewrites `inputs["scan_dir"]` when it names the old run id. `write_outputs` renames the scan directory on disk when a local run was renamed.

- [ ] **Step 1: Write the failing tests**

In `tests/findings/test_dataset_adapter.py`, next to `test_task_scan_dumps_in_the_eval_environment_then_scans_the_replay`:

```python
def test_scan_directory_lives_under_the_runs_key_and_is_recorded_relatively(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    monkeypatch.setenv("STUB_OUTPUT_DIR", str(TASK_FIXTURE))
    ctx = _task_ctx(root, tmp_path)
    result = run("inspect_evals/stereoset", ctx)
    key = result.inputs["scan_dir"]
    assert key == f"inspect-evals-stereoset/scans/{result.id}"
    assert (ctx.out_dir / key / "scan_summary.json").is_file()
    assert not list(ctx.out_dir.glob("inspect_dataset_*"))
```

In `tests/findings/test_cli.py`, after `test_a_renamed_run_file_carries_its_own_record_ids`:

```python
def test_a_renamed_run_takes_its_scan_directory_with_it(tmp_path: Path, run: Run) -> None:
    from inspect_audit.findings.cli import write_outputs

    out = tmp_path / "out"
    key = "inspect-evals-stereoset/scans/lint-1"
    (out / key).mkdir(parents=True)
    (out / key / "scan_summary.json").write_text("{}")
    with_scan = run.model_copy(update={"inputs": {"scan_dir": key}})
    write_outputs(out, {"inspect_evals/stereoset": [with_scan]})
    assert (out / key / "scan_summary.json").is_file()  # first write: same id, nothing moves
    (out / key).mkdir(exist_ok=True)
    (out / key / "scan_summary.json").write_text("{}")
    write_outputs(out, {"inspect_evals/stereoset": [with_scan]})  # same second: renamed to -2
    fs = StoreFS.from_locator(out)
    second = read_run(fs, "inspect-evals-stereoset/runs/lint-1-2.run.json")
    assert second.inputs["scan_dir"] == "inspect-evals-stereoset/scans/lint-1-2"
    assert (out / "inspect-evals-stereoset/scans/lint-1-2/scan_summary.json").is_file()
```

The existing `test_a_failed_dump_leaves_no_scan_dir_and_records_the_dump` keeps its meaning; change any `glob("inspect_dataset_*")` assertion in it to `not (ctx.out_dir / "inspect-evals-stereoset" / "scans").exists()`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/findings/test_dataset_adapter.py tests/findings/test_cli.py -q --no-cov -p no:cacheprovider -k "scan_directory or scan_dir"` Expected: FAIL; `inputs["scan_dir"]` is an absolute `inspect_dataset_*` path, and the renamed run keeps the old key.

- [ ] **Step 3: Implement**

In `adapters/dataset.py` add near `REPLAY_SCRIPT`:

```python
def scan_key(target: str, run_id: str) -> str:
    """Where a dataset run's scan directory lives in the store: beside its run, named by it."""
    return f"{slug(target)}/scans/{run_id}"
```

In `run`, compute `run_id = new_run_id(PRODUCER, target, timestamp)` once after `timestamp = utcnow()`, and replace both `scan_dir = Path(tempfile.mkdtemp(prefix="inspect_dataset_", dir=ctx.out_dir))` with:

```python
            scan_dir = ctx.out_dir / scan_key(target, run_id)
            scan_dir.mkdir(parents=True, exist_ok=True)
```

and `inputs["scan_dir"] = str(scan_dir)` with `inputs["scan_dir"] = scan_key(target, run_id)`. `parse` already takes the local `scan_dir` path. Drop the `tempfile` import if unused.

In `cli.py`, `_renamed` gains:

```python
    inputs = dict(run.inputs)
    scan = inputs.get("scan_dir")
    if isinstance(scan, str) and scan.endswith(f"/{run.id}"):
        inputs["scan_dir"] = scan[: -len(run.id)] + file_id
    return run.model_copy(update={"id": file_id, "findings": findings, "inputs": inputs})
```

and `write_outputs`, inside the loop, after computing `stored`:

```python
            if stored is not run:
                _move_scan_dir(fs, run, stored)
```

with

```python
def _move_scan_dir(fs: StoreFS, before: Run, after: Run) -> None:
    """A renamed local run's scan directory follows it; remote uploads use the new key directly."""
    old, new = before.inputs.get("scan_dir"), after.inputs.get("scan_dir")
    if not (isinstance(old, str) and isinstance(new, str) and old != new):
        return
    source, target = fs.local_path(old), fs.local_path(new)
    if source is not None and target is not None and source.is_dir():
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
```

- [ ] **Step 4: Run the findings suite**

Run: `uv run pytest tests/findings -q --no-cov -p no:cacheprovider` Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "feat(findings): dataset scan directories live under <slug>/scans/<run id> and are recorded as store keys

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

______________________________________________________________________

### Task 2: `run` against a remote store

**Files:**

- Modify: `src/inspect_audit/findings/fs.py` (`put_tree`), `src/inspect_audit/findings/cli.py` (`write_outputs`, the `run` command)
- Test: `tests/findings/test_fs.py`, `tests/findings/test_cli.py`

**Interfaces:**

- Produces: `StoreFS.put_tree(local_dir: Path, prefix: str) -> list[str]` uploading every file under `local_dir` to `prefix/<relative path>` and returning the keys, sorted. `write_outputs(store, runs_by_eval, review=None, *, work_dir: Path | None = None)`: when `work_dir` is given and the store is not that directory, each run's `inputs.scan_dir` directory under `work_dir` is uploaded under the run's final key. The `run` command uses a `tempfile.TemporaryDirectory()` as `Context.out_dir` when `fs.local_path("")` is None, passes it as `work_dir`, and the refusal of remote stores goes.

- [ ] **Step 1: Write the failing tests**

`tests/findings/test_fs.py`:

```python
def test_put_tree_uploads_every_file_under_a_prefix(tmp_path: Path) -> None:
    fs = StoreFS.from_locator("memory://put-tree")
    (tmp_path / "scan" / "deep").mkdir(parents=True)
    (tmp_path / "scan" / "scan_summary.json").write_text("{}")
    (tmp_path / "scan" / "deep" / "rows.json").write_text("[]")
    keys = fs.put_tree(tmp_path / "scan", "e/scans/run-1")
    assert keys == ["e/scans/run-1/deep/rows.json", "e/scans/run-1/scan_summary.json"]
    assert fs.read_text("e/scans/run-1/scan_summary.json") == "{}"
```

`tests/findings/test_cli.py`, replacing `test_run_requires_a_local_store_for_now`:

```python
def _remote_sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locator: str) -> int:
    root = make_root(tmp_path, extra=ASSET)
    _stubbed_env(monkeypatch)
    monkeypatch.setenv("STUB_OUTPUT_DIR", str(TASK_FIXTURE))
    args = ["run", "--config", str(PILOT), "--root", str(root), "--store", locator]
    return main([*args, "--producers", "dataset", "inspect_evals/stereoset"])


def test_run_writes_runs_and_scan_directories_to_a_remote_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tempfile

    before = set(Path(tempfile.gettempdir()).glob("inspect_audit_run_*"))
    assert _remote_sweep(tmp_path, monkeypatch, "memory://remote-run") == 0
    fs = StoreFS.from_locator("memory://remote-run")
    (run_key,) = fs.glob("inspect-evals-stereoset/runs/inspect_dataset-*.run.json")
    stored = read_run(fs, run_key)
    assert stored.inputs["scan_dir"] == f"inspect-evals-stereoset/scans/{stored.id}"
    assert fs.is_file(f"{stored.inputs['scan_dir']}/scan_summary.json")
    assert fs.is_file("export/index.json") and fs.is_file("inspect-evals-stereoset/SUMMARY.md")
    assert set(Path(tempfile.gettempdir()).glob("inspect_audit_run_*")) == before  # cleaned up
    assert main(["summary", "--store", "memory://remote-run"]) == 0


def test_remote_rerun_in_the_same_second_keeps_both_scan_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _remote_sweep(tmp_path, monkeypatch, "memory://remote-twice") == 0
    assert _remote_sweep(tmp_path, monkeypatch, "memory://remote-twice") == 0
    fs = StoreFS.from_locator("memory://remote-twice")
    runs = [read_run(fs, k) for k in fs.glob("inspect-evals-stereoset/runs/inspect_dataset-*.run.json")]
    assert len(runs) == 2
    for stored in runs:
        assert stored.inputs["scan_dir"] == f"inspect-evals-stereoset/scans/{stored.id}"
        assert fs.is_file(f"{stored.inputs['scan_dir']}/scan_summary.json")


def test_a_skipped_remote_dataset_run_uploads_no_scan_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    _stubbed_env(monkeypatch)
    monkeypatch.setenv("STUB_DUMP_EXIT", "1")
    args = ["run", "--config", str(PILOT), "--root", str(root), "--store", "memory://remote-skip"]
    assert main([*args, "--producers", "dataset", "inspect_evals/stereoset"]) == 1
    fs = StoreFS.from_locator("memory://remote-skip")
    assert fs.glob("inspect-evals-stereoset/scans/*") == []
```

The stubbed producers in `_stubbed_env` and `_task_ctx` write the fixture scan into whatever `-o` directory they are given, so they work unchanged under a temporary work directory. `TASK_FIXTURE`, `PILOT`, `ASSET`, `make_root` and `_stubbed_env` exist in the test module already.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/findings/test_fs.py tests/findings/test_cli.py -q --no-cov -p no:cacheprovider -k "put_tree or remote"` Expected: FAIL with `AttributeError: 'StoreFS' object has no attribute 'put_tree'` and `run` exiting 2 with "local store".

- [ ] **Step 3: Implement**

`fs.py`:

```python
    def put_tree(self, local_dir: Path, prefix: str) -> list[str]:
        """Upload every file under `local_dir` to `prefix/<relative path>`; returns the keys, sorted."""
        keys: list[str] = []
        for path in sorted(p for p in local_dir.rglob("*") if p.is_file()):
            keys.append(self.write_bytes(self.key(prefix, path.relative_to(local_dir).as_posix()), path.read_bytes()))
        return keys
```

`cli.py` `write_outputs`:

```python
def write_outputs(
    store: StoreFS | str | Path,
    runs_by_eval: Mapping[str, Sequence[Run]],
    review: Review | None = None,
    *,
    work_dir: Path | None = None,
) -> None:
    """Append the new runs and what the producers left in `work_dir`, then render the current view.

    `work_dir` is where producers wrote; for a local store it is the store root and nothing is
    copied. For any other store the scan directories are uploaded under their runs' keys.
    """
    fs = as_store_fs(store)
    local_root = fs.local_path("")
    uploading = work_dir is not None and (local_root is None or work_dir.resolve() != local_root.resolve())
    for target, runs in runs_by_eval.items():
        for run in runs:
            key = _run_key(fs, slug(target), run)
            file_id = key.rsplit("/", 1)[-1].removesuffix(".run.json")
            stored = run if file_id == run.id else _renamed(run, file_id)
            if stored is not run:
                _move_scan_dir(fs, run, stored)
            if uploading and work_dir is not None:
                scan = run.inputs.get("scan_dir")  # the directory on disk still has the old name
                if isinstance(scan, str) and (work_dir / scan).is_dir():
                    fs.put_tree(work_dir / scan, str(stored.inputs["scan_dir"]))
            write_run(stored, fs, key)
    render_current(fs, review)
```

`_move_scan_dir` only renames when both paths are local, so for a remote store it is a no-op and the upload above uses the new key. In the `run` command:

```python
    out_dir = fs.local_path("")
    with tempfile.TemporaryDirectory(prefix="inspect_audit_run_") as scratch:
        work_dir = out_dir if out_dir is not None else Path(scratch)
        review = _fold_or_exit(fs)
        if review is None:
            return 2
        ...
        ctx = Context(ie_root=..., logs=logs, out_dir=work_dir, ...)
        runs_by_eval = sweep(targets, ctx, producers, header=bool(sources))
        write_outputs(fs, runs_by_eval, review, work_dir=work_dir)
```

Remove the "run needs a local store in this release" branch. The `with` block guarantees cleanup on failure, which is Review Focus 3.

- [ ] **Step 4: Run the findings suite**

Run: `uv run pytest tests/findings -q --no-cov -p no:cacheprovider` Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "feat(findings): run writes to any store; producers work in a scratch directory and scan directories are uploaded under their runs' keys

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

______________________________________________________________________

### Task 3: The R2 integration test, docs, gate

**Files:**

- Create: `tests/findings/test_store_remote.py`

- Modify: `docs/findings-cli.md`, `docs/roadmap.md`, `docs/superpowers/specs/2026-10-09-portable-store-design.md` (migration step 3 ticked)

- [ ] **Step 1: Write the guarded test**

```python
"""Against a real S3-compatible store, when INSPECT_AUDIT_TEST_STORE names one. Skips otherwise."""

import os
import uuid

import pytest

from inspect_audit.findings.cli import write_outputs
from inspect_audit.findings.fs import StoreFS
from inspect_audit.findings.io import read_current
from inspect_audit.findings.models import Run
from inspect_audit.findings.store import Selection, Store

LOCATOR = os.environ.get("INSPECT_AUDIT_TEST_STORE")
pytestmark = pytest.mark.skipif(not LOCATOR, reason="INSPECT_AUDIT_TEST_STORE not set")


def test_round_trip_on_a_real_bucket(run: Run) -> None:
    fs = StoreFS.from_locator(f"{LOCATOR}/test-{uuid.uuid4().hex[:8]}")
    write_outputs(fs, {"inspect_evals/stereoset": [run]})
    assert [r.id for r in read_current(fs)] == ["lint-1"]
    Store(fs).accept(Selection(ids=("lint-1/1",)), title="t", author="Test Runner <t@example.com>")
    assert fs.is_file("export/index.json") and len(fs.glob("review/*.json")) == 1
    assert "ISS-0001" in fs.read_text("inspect-evals-stereoset/SUMMARY.md")
```

Run: `uv run pytest tests/findings/test_store_remote.py -q --no-cov -p no:cacheprovider` Expected: `1 skipped` locally. With `INSPECT_AUDIT_TEST_STORE=s3://<bucket>/ci` and `AWS_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` set it must pass; record in the ledger whether a real bucket was available when this ran.

- [ ] **Step 2: Docs**

`docs/findings-cli.md`: `run` works on any store; scan directories live at `<slug>/scans/<run id>/` and a dataset run's `inputs.scan_dir` is that key; a remote run uses a scratch directory and uploads. Add a short "Using an S3 or R2 bucket" paragraph: `--store s3://bucket/prefix`, `AWS_ENDPOINT_URL=https://<account id>.r2.cloudflarestorage.com` for R2, `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` from an R2 API token with object read and write on that bucket; bucket versioning recommended. No account or bucket name in the doc.

`docs/roadmap.md` milestone 3: "Done 2026-10-12: `run` on any store; scan directories keyed by run." Spec: migration step 3's code half is done; the bucket and the first remote sweep are the deployment's step.

- [ ] **Step 3: Gate**

Run: `git add -A src tests docs && uv run pre-commit run --all-files; uv run pre-commit run --all-files && uv run basedpyright src && uv run pytest -q -p no:cacheprovider 2>&1 | tail -1` Expected: second pre-commit pass clean, 0 errors, suite green.

- [ ] **Step 4: Commit**

```bash
git add -A src tests docs
git commit -m "test(findings): round trip against a real bucket when INSPECT_AUDIT_TEST_STORE names one; docs for remote stores

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

______________________________________________________________________

## Self-review notes

- Spec coverage: layout (`<slug>/scans/<run id>/` is new; the spec's layout block gains it in the docs step), remote `run` (migration step 3's code half), the R2 integration test from "Testing". The bucket itself, the token, and the first remote sweep are deployment steps for Matt, listed in the final message.
- Type consistency: `scan_key` is the one way the key is built; `inputs["scan_dir"]` is always that key; `put_tree` returns keys like every other `StoreFS` write.
- Review Focus 1 to 5 map to Task 2 (same-second rerun, skip, cleanup, summary parity) and Task 1 (no self-copy or rename for an unchanged id).

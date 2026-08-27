"""Prove the audit's own machinery before it accuses the benchmark.

The reconstruction -- the grade channel, the state rebuild -- is now trusted
infrastructure, so its failures must land in the "us" bucket, not the
benchmark's. Two zero-spend checks earn that trust per cell:

- **Resolution identity.** The task we resolved should be the one that produced
  the logs. Package and argument drift between the log header and our
  environment is surfaced (a caveat, or a finding: the benchmark changed under
  its published results), and a wrong resolution is caught before any verdict.

- **Replay-regrade.** Each recorded attempt is rebuilt through our own state
  reconstruction and regraded with the benchmark's real scorer; the result must
  match the score the log already recorded. A deterministic scorer that
  disagrees means our channel is broken -- this is exactly the check that
  catches a state-fidelity bug before it costs a model call. A disagreement is
  resampled a few times to tell a scorer's own noise (the resamples vary) from a
  real reconstruction fault (they are identical): noise becomes a measured floor,
  a stable fault blocks grade-dependent verdicts, and it is tracked per attempt
  so one flaky sample cannot exempt a whole scorer.

Box-graded scorers are the honest exception: a recorded attempt's box end-state
is not reproducible from its transcript, so replay-regrade cannot reproduce the
score from the transcript alone. That is reported as inconclusive rather than a
fault, and the gold-injection probe covers the grade channel for those.
"""

import json
from dataclasses import asdict, dataclass, field
from importlib.metadata import PackageNotFoundError, version
from logging import getLogger
from typing import Any

from inspect_ai.log import EvalSpec
from inspect_ai.scorer import Score, Scorer
from inspect_ai.scorer._scorer import unique_scorer_name
from inspect_ai.solver._task_state import TaskState
from inspect_ai.util import sandbox_default

from ._sandbox import BENCHMARK_SERVICE

logger = getLogger(__name__)


def normalize_value(value: Any) -> str:
    """A score value as a comparable string.

    Collapses the shapes a scorer's value can take -- `'C'`/`'I'`, numbers,
    nested dicts -- to one canonical string, so equality is stable across the
    serialisation the log kept and the object our regrade produced.
    """
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        # 1 and 1.0 are the same grade; keep integers integer-shaped
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, dict):
        return json.dumps({str(k): normalize_value(v) for k, v in sorted(value.items())})
    if isinstance(value, list):
        return json.dumps([normalize_value(v) for v in value])
    return str(value).strip()


@dataclass
class ScorerConcordance:
    """One scorer's replay-regrade result over the recorded attempts."""

    scorer: str
    checked: int = 0
    agreed: int = 0
    # a disagreement whose resamples are identical is a real reconstruction fault;
    # one whose resamples vary is the scorer's own (judge) noise. Tracked per
    # attempt so a single flaky sample cannot exempt a whole scorer from blocking.
    stable_disagreements: list[dict[str, str]] = field(default_factory=list)
    noisy_disagreements: list[dict[str, str]] = field(default_factory=list)

    @property
    def agreement(self) -> float:
        return self.agreed / self.checked if self.checked else 1.0

    @property
    def noise_rate(self) -> float:
        d = len(self.stable_disagreements) + len(self.noisy_disagreements)
        return len(self.noisy_disagreements) / d if d else 0.0


@dataclass
class ResolutionReport:
    """Package and argument drift between the logs and our resolution."""

    package_drift: dict[str, dict[str, str | None]] = field(default_factory=dict)
    arg_drift: dict[str, dict[str, Any]] = field(default_factory=dict)
    revisions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def drifted(self) -> bool:
        return bool(self.package_drift or self.arg_drift)


def resolution_report(header: EvalSpec, task_args: dict[str, Any]) -> ResolutionReport:
    """Compare a log's recorded task against how we resolved it.

    Args:
        header: The log's `eval` spec (recorded packages, args, revision).
        task_args: The arguments we resolved the audited task with.
    """
    report = ResolutionReport()
    for package, recorded in (header.packages or {}).items():
        try:
            installed: str | None = version(package)
        except PackageNotFoundError:
            installed = None
        if installed != recorded:
            report.package_drift[package] = {"logged": recorded, "resolved": installed}

    # only args the operator explicitly set can 'drift'; a log-resolved audit
    # passes task_args={} and adopts the log's own args, so those are not drift
    recorded_args = header.task_args or {}
    for key in task_args:
        logged, resolved = recorded_args.get(key), task_args.get(key)
        if logged != resolved:
            report.arg_drift[key] = {"logged": logged, "resolved": resolved}

    if header.revision is not None:
        report.revisions.append(
            {
                "origin": header.revision.origin,
                "commit": header.revision.commit,
                "dirty": header.revision.dirty,
            }
        )
    return report


@dataclass
class Concordance:
    """The full concordance report for one cell, machine-readable for resume."""

    resolution: dict[str, Any] = field(default_factory=dict)
    scorers: list[dict[str, Any]] = field(default_factory=list)
    verdict: str = "validated"
    """`validated`, `blocked` (a stable deterministic disagreement with no box to
    explain it), or `inconclusive` (disagreement a benchmark box could explain)."""
    reasons: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1, default=str)


def classify(
    scorers: list[ScorerConcordance], *, has_box: bool
) -> tuple[str, list[str]]:
    """The blocking decision from the per-scorer results.

    A perfect replay is validated. A stable deterministic disagreement is our
    reconstruction failing -- blocked when there is no box that could explain
    it, inconclusive when a box's unreproducible end-state could. A judge's
    measured noise never blocks: it is a floor, reported not enforced.
    """
    reasons: list[str] = []
    blocking = inconclusive = False
    for s in scorers:
        if s.noisy_disagreements:
            reasons.append(
                f"{s.scorer}: {s.noise_rate:.0%} of disagreements are scorer noise "
                f"(a measured floor over {s.checked} attempts), not a fault"
            )
        if not s.stable_disagreements:
            continue  # only noise, or a clean replay -- nothing blocks
        # stable disagreements are the reconstruction failing to reproduce a grade
        if has_box:
            inconclusive = True
            reasons.append(
                f"{s.scorer}: {len(s.stable_disagreements)} stable disagreement(s), but a "
                "benchmark box's end-state is not reproducible from the transcript -- inconclusive"
            )
        else:
            blocking = True
            reasons.append(
                f"{s.scorer}: {len(s.stable_disagreements)} stable disagreement(s) on a "
                "deterministic, box-free scorer -- the reconstruction does not reproduce "
                "recorded grades, so grade-dependent verdicts are blocked"
            )
    if blocking:
        return "blocked", reasons
    if inconclusive:
        return "inconclusive", reasons
    return "validated", reasons or ["replay-regrade reproduced every recorded grade"]


async def replay_regrade(
    samples: list[Any],
    scorers: list[Scorer],
    header_log: Any,
    *,
    limit: int,
) -> list[ScorerConcordance]:
    """Regrade recorded attempts through inspect's own re-score path and compare.

    Each recorded attempt is re-scored with `score_async` -- the same
    `_run_score_task` path `inspect score` uses -- so the sample's own transcript,
    store, input, target and output are restored, not a state we hand-build. The
    regraded value must match the score the log already recorded.

    Args:
        samples: Recorded attempt samples (from the sliced logs), each carrying
            its own recorded `scores`.
        scorers: The benchmark's scorers, named as the log names them.
        header_log: A log header (`read_eval_log(..., header_only=True)`) whose
            `eval`/`plan` give the model context `score_async` re-scores under.
        limit: Regrade at most this many attempts (a judge-cost bound).
    """
    batch = samples[:limit]
    if not batch:
        return []
    names = _scorer_names(scorers)  # keyed the way the log keys scores
    regraded = await _score_batch(batch, scorers, header_log)

    results: dict[str, ScorerConcordance] = {}
    disagreed: set[int] = set()
    for i, sample in enumerate(batch):
        recorded_scores = sample.scores or {}
        for name in names:
            recorded = recorded_scores.get(name)
            ours = (regraded[i] or {}).get(name)
            if recorded is None or ours is None:
                continue
            con = results.setdefault(name, ScorerConcordance(scorer=name))
            con.checked += 1
            if normalize_value(ours.value) == normalize_value(recorded.value):
                con.agreed += 1
            else:
                disagreed.add(i)

    # resample each disagreeing attempt to tell a real reconstruction fault
    # (resamples identical to the first regrade) from the scorer's own noise
    for i in sorted(disagreed):
        sample = batch[i]
        resample_runs = [await _score_batch([sample], scorers, header_log) for _ in range(RESAMPLES)]
        for name in names:
            recorded = (sample.scores or {}).get(name)
            first = (regraded[i] or {}).get(name)
            if recorded is None or first is None:
                continue
            if normalize_value(first.value) == normalize_value(recorded.value):
                continue  # this scorer agreed on this sample
            resamples = [
                normalize_value(run[0][name].value)
                for run in resample_runs
                if run and run[0] is not None and name in run[0]
            ]
            stable = bool(resamples) and all(v == normalize_value(first.value) for v in resamples)
            record = {
                "sample": str(sample.id),
                "epoch": str(sample.epoch),
                "recorded": normalize_value(recorded.value),
                "regraded": normalize_value(first.value),
            }
            (results[name].stable_disagreements if stable else results[name].noisy_disagreements).append(record)
    return list(results.values())


# resamples per disagreeing attempt, to separate a stable reconstruction fault
# from scorer noise; a handful is enough to catch a flipping judge
RESAMPLES = 3


async def _score_batch(
    samples: list[Any], scorers: list[Scorer], header_log: Any
) -> list[dict[str, Score] | None]:
    """Re-score recorded samples through inspect's own `score_async`.

    Returns each sample's fresh scores keyed as the log keys them, or `None` for a
    sample the scorers could not be run against. Sandbox calls are aimed at the
    benchmark box, so a box-graded scorer runs against the live environment the
    way `grade` does. `score_async` restores each sample's transcript/store/state
    from the recorded sample itself -- the reconstruction we are proving.
    """
    from copy import copy as shallow_copy

    from inspect_ai._eval.score import score_async
    from inspect_ai.model import get_model

    log = shallow_copy(header_log)  # score_async deepcopies (copy=True); don't mutate the header
    log.samples = list(samples)
    # re-score under the audit's own (installed) model, not the log's recorded one:
    # the recorded attempts span many providers whose packages this env need not
    # have, and get_model would eagerly import them. A deterministic scorer ignores
    # the model (the blocking case is exactly these); a model-graded scorer that
    # binds its own grader still uses that, and if its provider is absent it errors
    # here and the attempt is skipped -- never a false block.
    try:
        with sandbox_default(BENCHMARK_SERVICE):
            scored = await score_async(
                log,
                scorers,
                action="overwrite",
                model=get_model(),
                copy=True,
                display="plain",
            )
    except Exception as ex:  # a batch we cannot re-score is not a disagreement
        logger.debug("could not re-score a concordance batch: %s", ex)
        return [None] * len(samples)
    return [(s.scores or None) for s in (scored.samples or [])]


async def probe_concordance(
    audit_state: TaskState,
    scorers: list[Scorer],
    *,
    has_box: bool,
    limit: int = 15,
) -> Concordance:
    """Run both concordance checks for one cell, zero model spend.

    Reads the item's recorded attempts from their own logs, checks the resolved
    task against the log header, replays each attempt through our grade channel,
    and classifies the result.

    Args:
        audit_state: The live audit `TaskState`.
        scorers: The benchmark's scorers.
        has_box: Whether a benchmark environment is present (box-graded scorers
            cannot be reproduced from a transcript).
        limit: Regrade at most this many attempts (a judge-cost bound).
    """
    from inspect_ai.log import read_eval_log, read_eval_log_sample

    metadata = audit_state.metadata or {}
    item = metadata.get("audit_item") or {}
    attempts = item.get("attempts") or []
    concordance = Concordance()
    if not attempts:
        concordance.reasons.append("no recorded attempts to replay")
        return concordance

    # resolution identity, from the header of the first attempt's log. the full
    # header log (not just its spec) is also the model/plan context score_async
    # re-scores under, so keep it.
    header_log = None
    try:
        header_log = read_eval_log(attempts[0]["log_file"], header_only=True)
    except Exception as ex:  # a missing header costs the resolution check, not the cell
        logger.warning("could not read a log header for the resolution check: %s", ex)
    if header_log is not None:
        report = resolution_report(header_log.eval, item.get("task_args") or {})
        concordance.resolution = asdict(report)
        if report.drifted:
            concordance.reasons.append(
                "the resolved task drifted from the logs (see resolution); verdicts "
                "may be auditing a different version than the field ran"
            )
    else:
        concordance.reasons.append("could not read a log header to re-score against")
        return concordance

    # replay-regrade over the recorded attempt samples
    samples = []
    for ref in attempts:
        try:
            samples.append(
                read_eval_log_sample(
                    ref["log_file"], id=ref["sample_id"], epoch=ref["epoch"],
                    resolve_attachments=True,
                )
            )
        except Exception as ex:
            logger.warning("could not read a recorded attempt for replay: %s", ex)
    scored = await replay_regrade(samples, scorers, header_log, limit=limit)
    concordance.scorers = [asdict(s) for s in scored]

    concordance.verdict, reasons = classify(scored, has_box=has_box)
    concordance.reasons.extend(reasons)
    return concordance


def _scorer_name(scorer: Scorer) -> str:
    # the name the log keys this scorer's score by: its registry name, with the
    # inspect_ai prefix stripped the way the recorder strips it
    from inspect_ai._util.registry import is_registry_object, registry_unqualified_name

    if is_registry_object(scorer):
        return registry_unqualified_name(scorer)
    fn = getattr(scorer, "__wrapped__", scorer)
    return str(getattr(fn, "__name__", "?"))


def _scorer_names(scorers: list[Scorer]) -> list[str]:
    # the keys the log stores scores under, in order: the unqualified registry
    # name with inspect's own duplicate-name suffixing (name, name1, ...), so two
    # scorers sharing a name resolve to the distinct keys the recorder wrote --
    # otherwise the second collides on the first's key and goes silently unchecked
    used: list[str] = []
    for scorer in scorers:
        used.append(unique_scorer_name(scorer, used))
    return used

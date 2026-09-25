# A finding schema for eval audits: envelope and verbatim source

Alternative proposal, 2026-09-25, to compare with `finding-schema.md`. The first document normalises every fact a producer emits into a shared field. This one normalises only the fields the aggregator has to act on and copies the producer's record in whole. The same prior art applies: SARIF keeps tool-specific data in a `properties` bag, OSV in `database_specific`, and Scout copies the entire transcript envelope onto each result row.

## Design rules

- **Ten fields are the contract.** They are what join, dedupe, filter, sort, render and diff need. Nothing else is required of a producer.
- **Everything else is copied, not translated.** The producer's native record travels verbatim, tagged with its format and version. Fidelity is exact and a reviewer can always see what the producer actually said.
- **Envelope fields are materialised at write time.** Adapters compute them once and store them. Nothing that participates in identity or joins is a computed property, because a fingerprint that changes when the recipe changes breaks every vote and every before-and-after comparison.
- **Adapters live with the schema.** One small package holds the models, the fingerprint recipe, the IO helpers and one adapter per producer. Producers depend on nothing new. A producer may adopt the models directly later.
- **Pydantic v2.** Inspect, Scout and inspect_audit already are. `model_json_schema()` publishes the schema. Validation on ingest catches producer drift. Lint and inspect-dataset use dataclasses internally, and `asdict()` is the whole conversion.

## The envelope

| Field | Type | Derived from | Why the aggregator needs it |
| --- | --- | --- | --- |
| `fingerprint` | str | producer, rule, `subject.eval`, primary location `key()` | Identity across runs. The key for votes and `baseline_state`. |
| `fingerprint_version` | int | constant per recipe | A recipe change is a migration, not a silent re-key. |
| `subject.eval` | str | `EvalSpec.task_registry_name`, lint package name, dataset scan target | The join key for the hosted index. |
| `subject.revision` | `{commit, package_version, dirty}` | `EvalSpec.revision`, `EvalSpec.packages`, `git rev-parse` for source scans | Which code. Required. |
| `subject.task_version` | `{full, comparability, interface}` or null | `EvalSpec.task_version`, `EvalSpec.metadata.full_task_version`, `eval.yaml` | The unit inspect_evals uses to say when scores stop being comparable. |
| `subject.dataset` | `{path, config, split, revision}` or null | `EvalSpec.dataset`, inspect-dataset `scan_summary` | Which data. `revision` is null when unknown. |
| `dimension` | enum of the nine GL framework dimensions | adapter table keyed by rule code | One taxonomy shared with the prose reports. |
| `severity` | `none`, `minor`, `major`, `critical` | adapter mapping from the producer's own scale | Sorting and the scorecard. The mapping is written in the adapter. |
| `status` | `hypothesis`, `supported`, `qualified`, `retracted` | deterministic producers emit `supported`; agents start at `hypothesis` | Lifecycle. Retracted rows stay so false positives can be counted. |
| `summary` | str | producer message | One renderable sentence. |
| `locations[]` | list of typed locations, at least one `primary` | producer record | Where. The primary location feeds the fingerprint. |
| `run_id` | str | `Run.id` | Every finding traces to one invocation. |

Optional, filled when known: `aliases[]` (OSV), `suppressions[]` and `history[]` (each entry stamped with Inspect's `ProvenanceData` shape of timestamp, author, reason), `introduced` and `fixed` (commit or comparability version), `effect` (`affected`, `denominator`, `score`).

## The source block

```
source:
  format:    "inspect_evals_lint.Diagnostic@0.7.0"     # type and package version
  record:    { ...verbatim producer record... }
  eval_spec: { ...EvalSpec header minus dataset.sample_ids... } | null
```

`record` is whatever the producer emitted: a lint `Diagnostic`, an inspect-dataset `Finding`, a Scout `Result` with its `references`, an inspect_audit `Finding` with its `EvidenceRef`s, or a `record_verdict` payload. `eval_spec` is present for anything derived from a log and null otherwise. `sample_ids` is dropped because it can hold thousands of entries; `dataset.samples` stays.

Consumers depend only on the envelope. `record` and `eval_spec` are `JsonValue`, tolerant of unknown keys, and re-validated against the producer's own model only when a consumer chooses to.

## Locations

The one place a verbatim copy is not enough. The fingerprint hashes the primary location, so every kind must reduce to one comparable string.

```python
class Location(BaseModel):
    model_config = ConfigDict(extra="allow")
    kind: Literal["code", "sample", "transcript", "log", "scorer", "artifact", "url"]
    role: Literal["primary", "related"] = "related"
    quote: str | None = None

class CodeLocation(Location):       # lint, SARIF physical location
    kind: Literal["code"]; file: str; line: int | None = None; end_line: int | None = None; column: int | None = None
    def key(self) -> str: return f"code:{self.file}:{self.line or 0}"

class TranscriptLocation(Location): # Scout Reference, Inspect sample and event ids
    kind: Literal["transcript"]; eval_id: str; sample_uuid: str
    message_id: str | None = None; event_uuid: str | None = None
    sample_id: str | None = None; epoch: int | None = None   # display only
    def key(self) -> str: return f"transcript:{self.eval_id}:{self.sample_uuid}:{self.message_id or self.event_uuid or ''}"

class SampleLocation(Location):     # inspect-dataset
    kind: Literal["sample"]; dataset: str; sample_id: str; field: str | None = None
    def key(self) -> str: return f"sample:{self.dataset}:{self.sample_id}"

class LogLocation(Location):        # a header fact, e.g. eval.dataset.samples
    kind: Literal["log"]; eval_id: str; path: str; location_hint: str | None = None
    def key(self) -> str: return f"log:{self.eval_id}:{self.path}"
```

A discriminated union on `kind`. Each subclass allows extra keys so a producer can carry more than the schema names without a release. `ScorerLocation`, `ArtifactLocation` and `UrlLocation` follow the same pattern.

## Models

```python
class Subject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eval: str
    revision: Revision
    task_version: TaskVersion | None = None
    dataset: DatasetRef | None = None
    task_args: dict[str, JsonValue] = {}

class Source(BaseModel):
    format: str
    record: JsonValue
    eval_spec: dict[str, JsonValue] | None = None

class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["0.1"] = "0.1"
    fingerprint: str
    fingerprint_version: int = 1
    subject: Subject
    dimension: Dimension
    severity: Literal["none", "minor", "major", "critical"]
    status: Literal["hypothesis", "supported", "qualified", "retracted"]
    summary: str
    locations: list[AnyLocation] = Field(min_length=1)
    run_id: str
    source: Source
    aliases: list[str] = []
    suppressions: list[Suppression] = []
    history: list[StatusChange] = []
    introduced: VersionRef | None = None
    fixed: VersionRef | None = None
    effect: Effect | None = None

    @computed_field
    @property
    def primary_location(self) -> AnyLocation:
        return next(l for l in self.locations if l.role == "primary")

class Run(BaseModel):
    id: str
    timestamp: AwareDatetime
    producer: str                      # "inspect_evals_lint", "inspect_audit/audit", ...
    producer_version: str
    git_commit: str | None = None
    inputs: dict[str, JsonValue] = {}  # logs by eval_id, dataset revision, source commit
    model: str | None = None
    cost_usd: float | None = None
    outcomes: list[Outcome] = []       # {rule, status: pass|fail|skip} so absence is recorded
    findings: list[Finding] = []
```

`fingerprint()` is a module function, not a method, so the recipe is versioned in one place:

```python
def fingerprint(producer: str, rule: str, eval: str, primary: Location) -> str:
    return "sha256:" + sha256(f"{producer}|{rule}|{eval}|{primary.key()}".encode()).hexdigest()
```

Agent-produced findings whose primary location is a claim rather than a line use an `ArtifactLocation` pointing at the evidence file, and a human merges duplicates through `aliases`.

## IO

- `write_run(run, path)` writes one JSON document per producer invocation.
- `read_runs(paths) -> list[Run]` validates on read.
- `findings_df(runs)` flattens the envelope to columns and keeps `source` as a JSON string, Scout's shape, for pandas and parquet.
- `diff(baseline: list[Finding], candidate: list[Finding])` assigns `baseline_state` by fingerprint.
- Exporters: `to_sarif(run)` for the `code` subset, so GitHub renders it on PRs; `to_code_quality(run)` as the smallest interchange form.

## Adapters

One module each, in the schema package, reading the producer's native output:

| Adapter | Reads | Envelope derivation |
| --- | --- | --- |
| `lint` | lint `--output-format json` | `subject.eval` from package name; `revision` from `git rev-parse` of `--root`; `dimension` and `severity` from a table keyed by rule code; `code` location from `file`, `line`, `column`; `outcomes` from every pass, fail and skip row. |
| `dataset` | inspect-dataset findings JSON and `scan_summary.json` | `subject.dataset` from the summary; `sample` location; `severity` low, medium, high to minor, major, critical; `dimension` `dataset`. |
| `audit` | inspect_audit `findings.json`, `record_verdict` payloads in `.eval` logs | `eval_spec` from the audited log header; `transcript` and `artifact` locations from `EvidenceRef` and verdict evidence; `status` and `severity` copied. |
| `scout` | `scan_results_df` | `transcript` locations from `references`; `eval_spec` from the transcript source when it is an Inspect log; `dimension` from the scanner's declared tag. |
| `header` | any `.eval` header | The fourth deterministic producer: sample count against `dataset_samples`, package and task-version drift, unscored samples, unset `model_roles`. Emits `log` locations. |

## Worked example

The StereoSet duplicate-id defect as the lint adapter would emit it. Compare with the merged record in `finding-schema.md`.

```json
{
  "schema_version": "0.1",
  "fingerprint": "sha256:9c2e…",
  "fingerprint_version": 1,
  "subject": {
    "eval": "inspect_evals/stereoset",
    "revision": {"commit": "5687c5cdf", "package_version": "0.21.1.dev24+g5687c5cdf", "dirty": false},
    "task_version": {"full": "3-A", "comparability": 3, "interface": "A"},
    "dataset": null,
    "task_args": {}
  },
  "dimension": "dataset",
  "severity": "minor",
  "status": "supported",
  "summary": "filter_duplicate_ids() without max_duplicates= or reason=",
  "locations": [
    {"kind": "code", "role": "primary", "file": "src/inspect_evals/stereoset/stereoset.py", "line": 64, "column": 15}
  ],
  "run_id": "lint-2026-09-25-stereoset",
  "source": {
    "format": "inspect_evals_lint.Diagnostic@0.7.0",
    "record": {
      "rule": "duplicate_filter_acknowledged", "code": "IEBP008", "category": "best_practices",
      "severity": "error", "status": "fail",
      "message": "filter_duplicate_ids() without max_duplicates= or reason=",
      "file": "src/inspect_evals/stereoset/stereoset.py", "line": 64, "column": 15,
      "hint": "measure the duplicates on the pinned dataset and pass max_duplicates=<n>, and give a reason= that links the upstream report; see BEST_PRACTICES.md on deduplicating by id"
    },
    "eval_spec": null
  },
  "aliases": ["https://github.com/UKGovernmentBEIS/inspect_evals/pull/2524"],
  "suppressions": [],
  "history": [{"status": "supported", "provenance": {"timestamp": "2026-09-25T04:20:50Z", "author": "inspect_evals_lint", "reason": null}}]
}
```

The inspect-dataset and header adapters emit sibling records with their own fingerprints. The aggregator links the three through `aliases` once a human confirms they are one defect. The first document's merged record is what a consumer renders after that link; this document's records are what producers write.

## Comparison with `finding-schema.md`

| | Full normalisation (first doc) | Envelope and source (this doc) |
| --- | --- | --- |
| Required of a producer | Around thirty fields, many translated | Ten envelope fields plus the native record |
| Adapter size | Large, and every producer change is a schema change | A few dozen lines each; producer changes land in `record` untouched |
| Fidelity | Lossy where the mapping is imperfect | Exact |
| Consumer burden | Reads one shape | Reads one shape for the envelope; opts into `record` per producer when it needs more |
| Schema stability | Changes whenever a producer adds a field worth keeping | Changes only when the aggregator needs a new join or sort key |
| Cross-producer merging | Native, one record per defect | Through `aliases`, one record per producer per defect, merged at render time |
| Risk | The mapping tables drift from the producers | Consumers reach into `record` and quietly depend on producer internals |

Recommendation: this one. The first document remains useful as the derivation notes for the envelope and as the description of what a merged, rendered finding looks like.

## Open questions

- Where the package lives. A tiny standalone package with a pydantic dependency only, or a module inside inspect_audit that lint and inspect-dataset never import because the adapters read files. The second is cheaper today; the first is what the modularity goal implies.
- Whether `record` should be re-validated against the producer's model on ingest when the producer package is installed. Cheap insurance, optional dependency.
- Whether merged records, the first document's shape, are stored or only rendered. Storing them makes the hosted index simpler; rendering them keeps one source of truth.

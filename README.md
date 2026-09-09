# inspect_audit

Point it at an Inspect benchmark task and its eval logs; get back an Inspect task whose
samples are an LLM auditor investigating one benchmark item each.

```python
from inspect_audit import audit_task

task = audit_task("inspect_evals/simpleqa_verified", logs="logs/")
```

Run it with `inspect eval`, read it with `inspect view`. One score column per audit item
(gold-answer, red-teaming, ...) per sample, with the auditor's evidence in the score
metadata.

## Status

Working, under active development, and the design is not yet written up. The long
explanation that used to live here described intended behaviour in places where the code
does something narrower, so it has been removed until it can be written to match. The code
and its comments are the reference for now; `LOG.md` records what was tried and why.

Known constraints:

- Hard reset of the benchmark box is Docker only. On k8s (Hawk) the auditor runs from a
  published image without the audited task's packages installed, so the grader can be read
  but not imported in place.
- Programmatic `ComposeConfig` sandboxes are not reproduced.
- Concordance (re-grading the recorded attempts through the same channel the auditor
  grades with) runs in the zero-model `audit_probe` solver and writes
  `/audit/concordance.json`. The auditor does not yet read it; nothing is gated on it.
- `confidential=True` is a prompt-level boundary, not a network one.

## Development

The new local investigator reads a repository and existing logs, publishes a Quarto
report, and supports ACP follow-up. See [the setup and limitations](docs/investigation.md).
Hawk dispatch from the investigator is not implemented yet.

```bash
make install      # uv venv + editable install
make check        # ruff + strict mypy
make test         # pure tests, no docker needed
make test-docker  # the container e2e suite
```

`reference/` holds a local copy of the inspect docs and related papers. `AUDIT_CATALOGUE.md`
is a design note, not a description of the code.

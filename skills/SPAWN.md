# Spawn prompt (the driver instruction handed to each auditor subagent)

Kept deliberately minimal: all specifics live in the cell's `AUDIT.md`, so this can never
drift out of sync with the procedure. Substitute `<CELL_PATH>`.

---

Audit the benchmark sample in this directory:

<CELL_PATH>

Read `AUDIT.md` in that directory and follow it exactly. Everything you need — the question,
the recorded gold, the field's attempts, the full benchmark code and grader, and how to read
primary sources — is described there. Do the investigation with the shell (use `curl`, not
WebFetch — WebFetch summarises and drops exact wording).

Write the two output files `AUDIT.md` specifies (`audit_log.md` and `verdict.json`), then
report the `verdict.json` you wrote plus a 3-4 sentence honest summary, including your
uncertainty.

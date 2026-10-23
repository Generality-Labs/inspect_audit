# Hawk 4 migration

Hawk 4 is the target for audit execution on Hawk. The current deployment and package constraints remain on 3.x while the existing functionality is landed. This checklist tracks the migration separately; successful benchmark probes on 3.7.2 do not establish that restricted child execution works.

## Dependencies and deployment

- [ ] Upgrade the deployment and its runner images; record exact revisions.
- [ ] Update the `remote`, `runner` and development Hawk version constraints.
- [ ] Verify operator CLI and runner client installs against the deployed release and its supported Inspect version. Revisit our explicit Inspect package pin.
- [ ] Check identification of submitted code and required code-access permissions for audit packages, benchmark packages and repositories used at runtime.

## Credentials and child jobs

- [ ] Replace `_runs.HawkLogin` private refresh-hook access, including `_current_refresh_token`, with Hawk 4's supported runner credential interface.
- [ ] Validate the existing restricted `create_eval_set` path against the actual client and server, not just method signatures.
- [ ] Make restricted credentials the Hawk default at every entry point. Remove the ordinary audit path that forwards a user refresh token to children.
- [ ] Verify subject, investigator, judge and additional model approvals, including models selected through task arguments.
- [ ] Verify declared transcript access and output destinations; use Hawk's restricted transcript reader when required rather than broad warehouse access.
- [ ] Verify credentials cannot submit unapproved work and stop working when the execution finishes or is cancelled. Test denial cases with the real service.

## Launcher, routing and spending

- [ ] Review `dispatch/hawk.py` authentication and audit submission against the new API. Keep administrative launch authority outside agent sandboxes.
- [ ] Verify Middleman routes all calls intended to share a capped OpenRouter key through that assigned key, including judges and child experiments.
- [ ] Check provider passthrough routes and remove dependencies on retired unified Middleman endpoints, if present.
- [ ] Confirm balance queries and usage attribution against the deployed API. A key balance remains distinct from an audit's attributed usage.

## Acceptance

- [ ] Run a complete auditor job on Hawk 4: launch an investigation and benchmark experiment, collect logs and verdicts, and publish a report.
- [ ] Exercise cancellation, failed submissions and checkpoint resume.
- [ ] Rebuild and pin the auditor sandbox image from the trimmed Dockerfile; existing image digests still contain the old TeX publishing dependencies.
- [ ] Provision the publishing host or separate publishing image with TeX and access to the checksum-pinned private template. Prefer a preverified cache layered into the Python publishing runner image, so no GitHub token is needed in the pod. For the optional GL web publisher, install Playwright and Chromium. Verify findings submission, artifact persistence and both publishers on the actual runner image.
- [ ] Rerun the API-free benchmark compatibility suite.
- [ ] Document tested versions and remaining restrictions before declaring the Hawk 4 integration supported.

Automatic provider-key minting and detailed spending attribution are separate features; they are not implied by the credential migration.

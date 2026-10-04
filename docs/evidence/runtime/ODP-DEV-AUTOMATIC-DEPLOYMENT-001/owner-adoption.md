# ODP-DEV-AUTOMATIC-DEPLOYMENT-001 owner adoption

- Source author: Codex (foreground), commits `e376d1c2` and `fa211987`.
- Delivery owner: Claude2 (adoption only; no source changes in this commit).
- Assigned independent reviewer: Codex2. The original author is Codex, so the
  reviewer must inspect the code independently and must not reuse any prior
  review or the author's own verification as approval.
- The earlier `Reviewer: Claude2` trailers predate the reassignment; this
  commit carries the current owner/reviewer pairing.

## Owner read of the delivered diff

- `ci.yml` adds `automatic-dev-release`, gated on `push` to `refs/heads/dev`
  and success of orchestrator/product (performance and product E2E may be
  skipped by scope). It dispatches Runtime Release with `GITHUB_TOKEN` and
  prints that a request is not a deployment success.
- `deploy-dev.yml` adds `phase: auto` (dev only). `release_phase` checks out
  the trusted `github.sha` and `automatic_dev preflight` requires a
  `workflow_dispatch` on `refs/heads/dev` whose SHA equals the candidate, a
  protected dev tip equal to the candidate, and a completed successful
  same-repository dev push CI run for that exact SHA. Already-deployed
  candidates short-circuit (`proceed=false`).
- Build reuses the existing build job; `automatic_dev recovery` discovers the
  last live-verified dev manifest from `deployed-release-manifest-dev`
  artifacts of runs whose deploy job succeeded, or enables the existing
  first-release absence probe when none exists.
- `automatic_admission` re-verifies CI and validates the exact built
  manifest digest, four component digests, release admission and the
  sources-off posture; its receipt records `live_deployment_verified: false`.
- `deploy` re-checks for supersession before mutation, then runs the existing
  deploy and live validation; only after success is the manifest retained as
  the next predecessor.
- Staging/production keep signed manual admission and GitHub environment
  reviewers; `check_release_phase.py` refuses `auto` outside dev.
- Network IaC (`infra/terraform/dev_connector_egress/`) is untouched.

## Verification

The declared verification commands are run once by `task_verification.py`
at the final head; receipts live in the supervisor evidence store and the
results are published in the task note. No live deployment was dispatched by
this task.

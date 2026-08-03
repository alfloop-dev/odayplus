# ODP-OPERATOR-SMOKE-RBAC-LIVE-002 — Activate Operator smoke composite roles in live external configuration

Owner: Codex4 · Reviewer: Codex8 · Date: 2026-08-03

## Scope

This task covers only live external runtime configuration for the authenticated operator smoke principal:

1. Read existing `ODP_OPERATOR_SMOKE_ROLE` (GitHub Actions environment variable `dev`).
2. Update `ODP_OPERATOR_SMOKE_ROLE` to the canonical composite roles.
3. Read and preserve Secret Manager principal mapping for the smoke principal and confirm roles.
4. Re-run Deploy Dev at exact `origin/dev` SHA and verify live E2E gate auth dependencies for operator smoke endpoints.

No code under `shared/**`, `apps/**`, `scripts/**`, or workflow file contents were edited in this task. `ODP-OPERATOR-SMOKE-RBAC-LIVE-001` already covered code-path RBAC matrix work.

## Execution Log

- `ODP_OPERATOR_SMOKE_ROLE` pre-check (GitHub Actions Environment `dev`): `operations_manager`
  - command: `gh api repos/alfloop-dev/odayplus/environments/dev/variables --paginate -q '.variables[] | select(.name=="ODP_OPERATOR_SMOKE_ROLE") | .value'`
- `ODP_OPERATOR_SMOKE_ROLE` updated: `operations_manager,model_owner,data_owner`
  - command: `gh variable set ODP_OPERATOR_SMOKE_ROLE --env dev --body "operations_manager,model_owner,data_owner" --repo alfloop-dev/odayplus`
- `ODP_OPERATOR_SMOKE_ROLE` post-check: `operations_manager,model_owner,data_owner`
  - same readback command as above

## Secret Mapping Readback

Attempted to read Secret Manager secret version:
`oday-plus-dev-auth-principal-map` / project `alfaloop-data-project`.

Result:
- `gcloud` requests failed with non-interactive refresh error (`reauth related error (invalid_rapt)`), so no fresh read/write/patch of Secret Manager was possible in this environment session.

Recorded errors were:
- `There was a problem refreshing your current auth tokens: Reauthentication failed. cannot prompt during non-interactive execution.`
- `Please run: gcloud auth login`
- `error_description": "reauth related error (invalid_rapt)"

No secrets/tokens were printed.

## Next Step (Required by owner)

1. Re-authenticate GCP session for a non-interactive-capable credential and read back principal mapping for:
   - subject `110296401444439097904`
   - email `oday-dev-smoke-operator@alfaloop-data-project.iam.gserviceaccount.com`
2. Ensure mapped roles for both keys are exactly `operations_manager,model_owner,data_owner`.
3. Run Deploy Dev on latest `origin/dev` SHA and capture updated cloud-run-dev-validation artifact proving:
   - `operator bootstrap`, `models`, `ingestion-runs`, `audit` auth paths are consistent with updated roles
   - negative access controls remain denied.


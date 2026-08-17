# ODP-AUTH-RUNTIME-RECONCILE-001

- Status: `blocked_on_accepted_dependencies`
- Owner: `Claude`
- Reviewer: `Antigravity4`
- Target: `dev`
- Package 10 consumer: `ODP-P10-CAN-004-R3`
- Prepared: `2026-07-26T22:03:00Z`

## Objective

Reconcile the mutually incompatible authentication and deployment changes in
PRs #384, #386, and #388 without reintroducing long-lived GCP credentials,
weakening application authorization, bypassing the live gate, or changing any
Package 10 visual surface.

This task is a required integration predecessor for Package 10 release closure.
It does not authorize a merge or deployment by itself.

## Exact Audited Inputs

| Input | Audited head | Role |
|---|---|---|
| PR #384 | `b0116794cb0d93f219427e094cbad2df2e6d44e8` | OIDC session token and application principal-mapping changes; contains conflicting SA-key/deploy behavior |
| PR #386 | `d139aa1210df129ce8b193d6f51905e6c57e2b45` | WIF-only deployment and GCP IAM authority |
| PR #388 | `11439d92de4eb43e223f44e2a3b6081c4f62d4f9` | Fail-closed live E2E gate before `DEPLOYMENT_COMMITTED=true` |
| `origin/dev` | `941db22dafe226cb6349fa1a706d10bbda21e7c6` | Audit-time integration base |

All SHAs are pickup-time inputs, not permanent merge authority. The owner must
fetch and record the accepted exact SHA for every dependency before editing.

## Coordinator Decision

1. PR #386 is authoritative for GCP deployment identity:
   Workload Identity Federation is mandatory and `GCP_SA_KEY` must not return.
2. PR #384 must not be merged wholesale. Its unique OIDC/API authorization
   changes may be ported only after proving they remain required on the current
   integration base.
3. A verified IdP claim must not self-assign platform roles, tenant scope, store
   scope, modules, or clearance. Production principal mapping remains
   deployment-owned, fail-closed, and sourced through Secret Manager or an
   equivalent reviewed authority.
4. The smoke credential must be runtime-valid, short-lived, masked, and absent
   from images, artifacts, logs, and committed configuration. A static
   long-lived bearer token is a release blocker.
5. PR #388's live non-mock gate must remain after traffic promotion and before
   `DEPLOYMENT_COMMITTED=true`. No integration may move, skip, soften, or
   replace it with fixture/seed evidence.
6. Package 10 visual code, canonical E2E assertions, archived designs, and
   retired visual implementations are outside this task.

## Dependencies

The task remains blocked until all are `done` at exact accepted and merged SHAs:

- `ODP-RUNTIME-GCP-001`
- `ODP-LIVE-E2E-001`

If either dependency changes an overlapping file after pickup, stop and issue a
conflict report before continuing.

## Writable Paths

```text
apps/web/src/lib/auth/**
modules/opsboard/auth/**
tests/security/test_opsboard_auth_boundary.py
.github/workflows/deploy-dev.yml
.github/workflows/deploy-staging.yml
product_ops/deployment/deploy_cloud_run_waji.sh
product_ops/deployment/validate_cloud_run_live_deployment.py
tests/ops/test_cloud_run_live_deployment.py
docs/deployment/GCP_DEPLOY_GUIDE.md
docs/evidence/runtime/**
docs/evidence/fleet_dispatch/ODP-AUTH-RUNTIME-RECONCILE-001/**
```

Workflow, deployment-script, and deployment-test edits are allowed only to
compose the accepted #386 and #388 contracts. They may not restore #384's
SA-key fallback.

## Forbidden Paths

```text
apps/web/features/operator/**
apps/web/src/app/operator/**
apps/web/src/app/intake/**
tests/e2e/operator-*.spec.ts
tests/e2e/e2e-operator-console.spec.ts
docs_archive/**
```

No legacy visual, compatibility markup, fixture fallback, seed fallback, or
test-only production path may be added.

## Required Verification

The owner must record exact commands, exit codes, counts, and the pushed SHA:

```text
uv run pytest -q tests/security/test_opsboard_auth_boundary.py
uv run pytest -q tests/ops/test_cloud_run_live_deployment.py
uv run pytest -q tests/e2e/test_live_e2e_gate.py
python3 infra/terraform/validate_contract.py
python3 product_ops/deployment/validate_cloud_run_live_deployment.py preflight ...
bash -n product_ops/deployment/deploy_cloud_run_waji.sh
git diff --check
```

Required negative proofs:

- missing/invalid principal mapping grants no application role or tenant scope;
- generic verified claims cannot elevate authorization;
- missing WIF configuration fails before deployment;
- no workflow or script references `GCP_SA_KEY`;
- smoke credentials are redacted and not persisted;
- the live gate still executes before release commit;
- Package 10 and retired visual paths are unchanged.

## Handoff

After Claude pushes an exact SHA, Antigravity4 must independently inspect the
diff, rerun the focused gates, and issue an explicit approve/reject decision.
Only an approved merged SHA may be consumed by `ODP-P10-CAN-004-R3`.

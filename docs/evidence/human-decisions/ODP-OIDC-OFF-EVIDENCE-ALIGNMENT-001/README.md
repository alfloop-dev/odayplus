# ODP-OIDC-OFF-EVIDENCE-ALIGNMENT-001: Password-First / OIDC-Off Evidence Alignment

- Task: ODP-OIDC-OFF-EVIDENCE-ALIGNMENT-001
- Work Package: WP-20
- Decision Ref: D15 (Google OIDC disabled; password-first is default)
- Owner: Antigravity2
- Reviewer: Codex2
- Inspected Head: `cf04c0467f2f03017301838268d5f3f055f29ed3` (composed from `9048161e058b` via base advance merge)
- Inspection Date: 2026-09-08T16:33:00Z
- Stage: A (engineering evidence preparation; B-stage awaits OAuth provider data)

## Purpose

This evidence package reconciles the password-first / OIDC-off implementation
state against the D15 decision from the [execution plan](../../../plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md):

> **D15**: Google OIDC → A：帳密為預設，Google OIDC 不啟用

The package:

1. Verifies that prerequisite implementation tasks (ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001 and ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002) are merged and their evidence is reachable.
2. Documents the full authentication mode resolver precedence across backend Python and frontend TypeScript runtimes, clearly distinguishing explicit mode flags, legacy boolean flags, configured legacy issuers, and unconfigured default states.
3. Quantifies auth-surface code drift (5 files, 379 insertions, 25 deletions) between the historical E2E-002 merge (`2377168c`) and current HEAD (`cf04c046`), detailing impact control-by-control and separating historical test passes, static inspection, exact-head CI regression, and live deployment unknown status.
4. Cites the exact fail-closed callback route at [`apps/web/src/app/auth/callback/route.ts`](../../../../apps/web/src/app/auth/callback/route.ts) lines 47-87 (local rejection at 64-73 before code exchange at 104).
5. Updates the standby handoff for the HUMAN-GCP-WEB-OAUTH-CLIENTS-001 human task, detailing WP-90 / canonical-writer handoff and later B-stage OAuth enablement entry conditions with responsible parties.

## Artifacts Index

| File | Purpose |
|---|---|
| [auth-mode-evidence-matrix.json](auth-mode-evidence-matrix.json) | 13-control evidence matrix: 11 pass (static inspection / historical receipt), 2 unknown (live deployment readback, OAuth provider verification — intentionally B-stage) |
| [human-task-handoff.json](human-task-handoff.json) | HUMAN-GCP-WEB-OAUTH-CLIENTS-001 standby handoff with condition dependencies, responsible parties, and entry criteria |

## Prior Task History & Lineage

### ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001

- **PR**: [#1074](https://github.com/alfloop-dev/odayplus/pull/1074)
- **Merge commit**: `840081001084ad9586421de908530a41f3a17333`
- **Merged into dev**: ✅ (verified ancestor of current HEAD)
- **Scope**: Terraform / deploy conditional OIDC; `shared/auth/mode.py` and `apps/web/src/lib/auth/runtime.ts` resolver; deployment validation conditional OIDC secret requirement.
- **Canonical lookup**: `ai-status.sh show` returned exit 1 (`Unknown task`); `gh pr view 1074` returned exit 0 confirming `MERGED`.
- **Canonical history gap**: Task records not in active `ai-status.json` or `ai-task-archive/`; task definitions preserved in `.orchestrator/task-briefs/` and git commit history.

### ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-001

- **Branch**: `task/ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-001`
- **Commit SHAs**: `ea64f02e0366a48df2bcfda8f23f418d7f7e34b2`, `46077fe886417473189b11331e119daa66f1c50a`
- **Status**: Superseded by E2E-002
- **Scope**: Initial security E2E verification attempt against Wave Auth 1/2 contract.
- **Blocker discovery**: Discovered two critical architecture blockers:
  - B1: `LoginThrottleService` had no production caller in `POST /login`.
  - B2: Throttle layer lacked an `identity.login_attempts` SQL persistence repository.
- **Remediation**: Blockers triggered remediation task `ODP-WEB-LOGIN-THROTTLE-REMEDIATION-001` (PR [#1085](https://github.com/alfloop-dev/odayplus/pull/1085), merge `3f8eb309`), implementing `PostgresLoginThrottleStore` and TypeScript throttle wiring.

### ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002

- **PR**: [#1096](https://github.com/alfloop-dev/odayplus/pull/1096)
- **Merge commit**: `2377168c2cc07cd2470dd8f43de0486fe8d8fc08`
- **Composition base**: `d0c81635df8e48dc803272e5ee6fc1fca3fa32cb` (merge commit `f3095a0eb3f03aa788a10747cf3721ae54a1be70`)
- **Merged into dev**: ✅ (verified ancestor of current HEAD)
- **Receipt**: [`ODP_WEB_PASSWORD_FIRST_SECURITY_E2E_RECEIPT.md`](../../e2e/ODP_WEB_PASSWORD_FIRST_SECURITY_E2E_RECEIPT.md) (receipt date 2026-09-01 UTC)
- **Verdict**: pass (151 Python tests passed, 22 skipped; 474 Web tests passed across 53 files)
- **Canonical lookup**: `ai-status.sh show` returned exit 1 (`Unknown task`); `gh pr view 1096` returned exit 0 confirming `MERGED`.

### HUMAN-GCP-WEB-OAUTH-CLIENTS-001

- **Status**: `todo` (Human/Ops gate)
- **Current role**: Standby — required only if business decides to enable Google OIDC login. No longer a deployment blocker for dev/staging/production.
- See [human-task-handoff.json](human-task-handoff.json) for condition dependencies and entry criteria.

## Auth-Mode Resolver Precedence & Gate Logic

Both Python (`shared/auth/mode.py`) and TypeScript (`apps/web/src/lib/auth/runtime.ts`) implement a 4-tier resolution hierarchy:

1. **`ODP_AUTH_MODE`**: Authoritative setting (`"local"` | `"oidc"`). If set to `"local"`, OIDC is disabled. If set to `"oidc"`, OIDC is enabled. Any conflict between `ODP_AUTH_MODE` and `ODP_AUTH_OIDC_ENABLED` triggers an error.
2. **`ODP_AUTH_OIDC_ENABLED`**: Legacy boolean flag (`"true"` -> `"oidc"`, `"false"` -> `"local"`).
3. **Legacy Configured Issuer**: If neither mode flag is set, but a legacy issuer variable (`ODP_WEB_OIDC_ISSUER`, `ODP_OIDC_ISSUER`, `ODP_GCP_OIDC_ISSUER`) is configured with a non-placeholder value, the resolver selects `"oidc"` for backwards compatibility with pre-contract deployments.
4. **Unconfigured Default**: When no mode flag, no legacy flag, and no issuer variables are present, the resolver defaults to `"local"`.

> [!IMPORTANT]
> **D15 Compliance & Live Deployment Limitation**:
> Absence of `ODP_AUTH_MODE` or `ODP_AUTH_OIDC_ENABLED` does **not** guarantee `local` mode if a legacy issuer variable is present in the environment. Explicit `ODP_AUTH_MODE=local` (or `ODP_AUTH_OIDC_ENABLED=false`) forces local mode regardless of issuer variables.
> Because this documentation task does not perform live environment readback, live deployment compliance is recorded as **unknown**.

## Auth-Surface Drift Analysis

Read-only inspection via `git diff --stat 2377168c2cc07cd2470dd8f43de0486fe8d8fc08 cf04c0467f2f03017301838268d5f3f055f29ed3 -- modules/opsboard/auth shared/auth` revealed 5 changed files (379 insertions, 25 deletions):

- `modules/opsboard/auth/claims.py`: 22 insertions/deletions (claims parsing and role normalization)
- `shared/auth/__init__.py`: 24 insertions (tenant and claims exports)
- `shared/auth/abac.py`: 17 deletions (ABAC consolidation)
- `shared/auth/rbac.py`: 13 insertions/deletions (RBAC access checks)
- `shared/auth/tenant.py`: 328 insertions (multi-tenant isolation models)

### Impact by Control

- **Auth Mode Resolvers & Gates**: `shared/auth/mode.py` and `apps/web/src/lib/auth/runtime.ts` had 0 diff lines.
- **Login & Callback Routes**: `apps/web/src/app/login/route.ts` and `apps/web/src/app/auth/callback/route.ts` had 0 diff lines.
- **Tenant Isolation & RBAC**: Enhanced by subsequent PRs which passed their respective dev CI suites. Historical E2E-002 receipt proved baseline tenant isolation on `2377168c`.
- **Evidence Separation**: We explicitly distinguish (1) historical E2E-002 test pass, (2) static code inspection at current HEAD, (3) exact-head CI regression on dev, and (4) live deployment state (unknown without readback).

## Callback Fail-Closed Verification

The OIDC callback route at [`apps/web/src/app/auth/callback/route.ts`](../../../../apps/web/src/app/auth/callback/route.ts) lines 47-87 implements fail-closed protection:

- Lines 49-62: `resolveAuthMode(process.env)` is called; invalid mode returns 503 `WEB_AUTH_NOT_CONFIGURED`.
- Lines 64-73: When `authMode === "local"`, immediately returns:
  ```json
  {
    "error": {
      "code": "WEB_AUTH_PROVIDER_DISABLED",
      "summary": "OIDC authentication provider is disabled."
    }
  }
  ```
  with HTTP 503 and `Cache-Control: no-store`.
- Lines 76-88: Validates complete OIDC configuration before processing parameters.
- Line 104: Authorization code exchange (`exchangeAuthorizationCode`) is unreachable when `authMode` is `local`.

## Evidence Matrix Summary

The [auth-mode-evidence-matrix.json](auth-mode-evidence-matrix.json) covers 13 controls:

| # | Control | Evidence Method | Status |
|---|---|---|---|
| 1 | Auth mode resolver precedence (`shared/auth/mode.py:70-111`) | Static code inspection | ✅ pass |
| 2 | Web runtime OIDC gate (`apps/web/src/lib/auth/runtime.ts:83-145`) | Static code inspection | ✅ pass |
| 3 | Login route: no Google UI when OIDC off (`apps/web/src/app/login/route.ts:303-342`) | Static code inspection | ✅ pass |
| 4 | Callback fail-closed when OIDC off (`apps/web/src/app/auth/callback/route.ts:47-87`) | Static code inspection | ✅ pass |
| 5 | Deployment validation: conditional OIDC (`product_ops/deployment/validate_cloud_run_live_deployment.py:1426-1434`) | Static code inspection | ✅ pass |
| 6 | Terraform checks: conditional OIDC (`infra/terraform/checks.tf:76,238,275`) | Static code inspection | ✅ pass |
| 7 | Password login: no Google secret dependency (`apps/web/src/lib/auth/localAuth.ts`) | Static code inspection | ✅ pass |
| 8 | Session / CSRF / throttle integrity (`apps/web/src/lib/auth/session.ts`) | Static code inspection | ✅ pass |
| 9 | Historical E2E receipt regression evidence (`docs/evidence/e2e/ODP_WEB_PASSWORD_FIRST_SECURITY_E2E_RECEIPT.md`) | Historical receipt ref (merge 2377168c) | ✅ pass |
| 10 | API boundary multi-issuer fail-closed (`modules/opsboard/auth/config.py`) | Static code inspection | ✅ pass |
| 11 | Ops test conditional OIDC deployment (`tests/ops/test_conditional_oidc_deployment.py`) | Static code inspection | ✅ pass |
| 12 | Live deployment auth-mode readback (Cloud Run / staging / prod) | Live environment readback | ⬜ unknown |
| 13 | Live OAuth provider verification (Google OAuth credentials) | Live provider probe (B-stage) | ⬜ unknown |

## Identified Gaps & Mitigations

1. **Canonical task archive gap**: ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001, ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-001, and E2E-002 records are absent from active `ai-status.json` and `ai-task-archive/`.
   - *Mitigation*: PR #1074 and #1096 merge commits are cryptographically verifiable ancestors of `dev`; git object history preserves trial commits.
2. **Auth-surface code drift**: 5 files (379 insertions, 25 deletions) changed between E2E-002 merge (`2377168c`) and current HEAD (`cf04c046`).
   - *Mitigation*: Drift represents tenant context and claims additions; core resolver and callback routes are unchanged. Static inspection confirms D15 logic remains intact.
3. **Live deployment readback unknown**: Live environment configuration is not queried in this task.
   - *Mitigation*: Documented honestly as `unknown`; assigned to Release / Ops during deployment validation.
4. **OAuth provider verification unknown**: Live Google OAuth credentials unverified.
   - *Mitigation*: Standby B-stage concern under HUMAN-GCP-WEB-OAUTH-CLIENTS-001.

## Entry Conditions & Handoff

- **WP-90 / Canonical-Writer Handoff**: Upon A-stage approval, WP-90 / canonical writer updates HUMAN-GCP-WEB-OAUTH-CLIENTS-001 standby instructions on the orchestrator board without unblocking or closing the task.
- **OAuth Enablement Entry Conditions (B-Stage)**:
  1. *Product Authority*: Explicit business decision to enable Google OIDC login.
  2. *Human/Ops*: Execute HUMAN-GCP-WEB-OAUTH-CLIENTS-001, generate GCP OAuth 2.0 Web Client credentials, and populate Secret Manager (`ODP_WEB_OIDC_CLIENT_ID`, `ODP_WEB_OIDC_CLIENT_SECRET`, `ODP_WEB_OIDC_ISSUER`).
  3. *Release / Ops Engineer*: Deploy with `ODP_AUTH_MODE=oidc`, perform live environment readback, and execute live provider handshake validation.

## Not In Scope

- This package does not create or read OAuth client secrets.
- This package does not modify product code, deployment configuration, or infrastructure.
- This package does not mark HUMAN-GCP-WEB-OAUTH-CLIENTS-001 as done.
- This package does not claim the OAuth provider is verified or ready.
- B-stage data, formal approval, provider verification, and live production capabilities are explicitly deferred.

## Contract & Plan References

- [ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md](../../../plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md)
- [ODP_WEB_PASSWORD_FIRST_AUTH_CONTRACT.md](../../../design/ODP_WEB_PASSWORD_FIRST_AUTH_CONTRACT.md) (pinned SHA `04e1572f802a54c2646ba678fe2975226dfbd7c4`)
- [ODP_WEB_PASSWORD_FIRST_SECURITY_E2E_RECEIPT.md](../../e2e/ODP_WEB_PASSWORD_FIRST_SECURITY_E2E_RECEIPT.md) (pinned SHA `04e1572f802a54c2646ba678fe2975226dfbd7c4`)

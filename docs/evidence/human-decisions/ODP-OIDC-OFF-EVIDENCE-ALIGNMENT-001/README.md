# ODP-OIDC-OFF-EVIDENCE-ALIGNMENT-001: Password-First / OIDC-Off Evidence Alignment

- Task: ODP-OIDC-OFF-EVIDENCE-ALIGNMENT-001
- Work Package: WP-20
- Decision Ref: D15 (Google OIDC disabled; password-first is default)
- Owner: Claude
- Reviewer: Codex2
- Inspected Head: `95646a5c2bd598b7e219ee51efa7e410cb9fe0f4` (origin/dev, composed into this task branch by a base-advance merge)
- Inspection Date: 2026-09-08 UTC
- Stage: A (engineering evidence preparation; B-stage OAuth provider data is **not** awaited)

## Purpose

This package reconciles the password-first / OIDC-off implementation state against
the D15 decision from the [execution plan](../../../plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md):

> **D15**: Google OIDC → A：帳密為預設，Google OIDC 不啟用

It delivers three things:

1. A control-by-control evidence matrix for the auth-mode resolvers, the login UI,
   the callback route, session / CSRF / throttle integrity, the API boundary and the
   conditional deployment path, each observation backed by a read-only collection
   receipt that records its own argv, source SHA, UTC timestamp and **original exit code**.
2. An honest separation between four different kinds of evidence: dated historical
   receipts, static inspection at the inspected head, exact-head CI status, and live
   deployment / provider state that was never probed and is recorded as `unknown`.
3. A standby handoff for the HUMAN-GCP-WEB-OAUTH-CLIENTS-001 human task, with the
   concrete entry conditions and responsible parties for a later B-stage enablement.

## Artifacts Index

| File | Purpose |
|---|---|
| [auth-mode-evidence-matrix.json](auth-mode-evidence-matrix.json) | 15-control evidence matrix (12 pass, 3 unknown, 0 fail) plus 35 read-only collection receipts with original exit codes |
| [human-task-handoff.json](human-task-handoff.json) | HUMAN-GCP-WEB-OAUTH-CLIENTS-001 standby handoff: condition dependencies, regression evidence, true gaps, entry criteria |

## Corrections In This Revision

The previous revision of this package (PR #1253 at head `5871c1dd`) was rejected on five
findings. All five are addressed here; each correction is backed by receipts in
`auth-mode-evidence-matrix.json` → `collection_receipts`.

| # | Defect in the previous revision | Correction | Receipts |
|---|---|---|---|
| F1 | Composition SHAs `d0c81635df8e48dc...` and `f3095a0eb3f03aa7...` were reconstructed from the receipt's abbreviations and are not real objects (`git cat-file -e` exit 128). | Resolved from the object database: `d0c81635df8e842f7910d40873b3886d4237cee8` and `f3095a0ee3ca3dde63d98f35ae8cfedf090fd529`. The same defect in the E2E-001 remediation merge (`3f8eb309`, invalid) is corrected to `595e7501e73ff4cbb5894c27e199e02a06c2a509`. | R01–R04, R08–R09, R30 |
| F2 | Merge commit `2377168c` was labelled the historical "verified run HEAD". | The receipt records a date, a composition base/merge and prose outcomes — not a run HEAD and not exit codes. Run HEAD and original exit codes are now `unknown`; the merge lineage is recorded separately. | R05–R07, R31 |
| F3 | Cited `securityE2E.test.ts` and `apps/web/src/app/auth/callback/route.test.ts`, neither of which exists. | Replaced with tests that exist at the inspected head; every JSON source ref was probed against the pinned tree. | R14–R20 |
| F4 | Listed `ODP_OIDC_ISSUER` / `ODP_GCP_OIDC_ISSUER` as resolver inputs, said both resolvers raise, and described the local provider rejection as "404 / 503". | Per-consumer issuer inputs corrected; Python returns `(mode, error)` and never raises while TypeScript throws; the local rejection is a fixed HTTP 503. | R21–R26 |
| F5 | Observations carried prose/SHA/UTC but no original exit code, and asserted exact-head CI evidence with no run or job URL. | 35 collection receipts with original exit codes are attached, and the CI claim now names a run, a job, a measured head and a workflow-sourced command scope. | R33–R34 |

Non-zero exits among the receipts are deliberate and are reported as-is: R02/R04/R08
(exit 128) prove the previously recorded SHAs are invalid, R19/R20 (exit 128) prove the
previously cited test files do not exist, R22/R23 (exit 1) prove two environment variables
are absent from the tree, and R27/R28 (exit 1) are the canonical-archive lookups that
return `Unknown task`. None of these blocks A-stage delivery, and none is presented as a
scan, runtime, provider or security gate pass.

## Prior Task History & Lineage

### ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001

- **PR**: [#1074](https://github.com/alfloop-dev/odayplus/pull/1074) — `MERGED`
- **Merge commit**: `840081001084ad9586421de908530a41f3a17333` (ancestor of the inspected head, receipt R10)
- **Scope**: Terraform / deploy conditional OIDC; the shared auth-mode resolver in [`shared/auth/mode.py`](../../../../shared/auth/mode.py) and [`apps/web/src/lib/auth/runtime.ts`](../../../../apps/web/src/lib/auth/runtime.ts); deployment validation that requires OIDC secrets only when `auth_mode=oidc`.
- **Canonical history gap**: `ai-status.sh show` returns exit 1 (`Unknown task`) and the task is absent from `ai-task-archive`. Completion rests on the merged PR and commit ancestry. The missing record is a history gap, not evidence the work was not done; no same-named task is recreated here.

### ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-001

- **Branch**: `task/ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-001`
- **Commit SHAs**: `ea64f02e0366a48df2bcfda8f23f418d7f7e34b2`, `46077fe886417473189b11331e119daa66f1c50a`
- **Status**: superseded by E2E-002 (never merged as a standalone PR)
- **Blocker discovery**: B1 — `LoginThrottleService` had no production caller in `POST /login`; B2 — the throttle layer had no `identity.login_attempts` SQL persistence repository.
- **Remediation**: `ODP-WEB-LOGIN-THROTTLE-REMEDIATION-001`, PR [#1085](https://github.com/alfloop-dev/odayplus/pull/1085), head `94e93af9e7611d5c70dc38247db6942f0bccfad6`, merge `595e7501e73ff4cbb5894c27e199e02a06c2a509`, merged 2026-09-01T06:37:24Z. It implemented `PostgresLoginThrottleStore` and the TypeScript throttle wiring.

### ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002

- **PR**: [#1096](https://github.com/alfloop-dev/odayplus/pull/1096) — `MERGED`
- **PR head**: `69422d71e8d5ac572ade58562c0aeca28d123648`
- **Merge commit**: `2377168c2cc07cd2470dd8f43de0486fe8d8fc08`, created 2026-09-01T08:51:28Z (ancestor of the inspected head, receipt R11)
- **Receipt**: [`ODP_WEB_PASSWORD_FIRST_SECURITY_E2E_RECEIPT.md`](../../e2e/ODP_WEB_PASSWORD_FIRST_SECURITY_E2E_RECEIPT.md), verdict `pass`, dated 2026-09-01 UTC
- **Canonical history gap**: `ai-status.sh show` returns exit 1 (`Unknown task`); the immutable receipt and the merge commit remain in the repository.

### What the historical receipt does and does not prove

The receipt's section 4 records a verification **date**, a composition **base** (abbreviated
`d0c81635df8e...`), a composition **merge** (abbreviated `f3095a0e`), and seven per-layer
commands with prose outcomes. It does **not** record the commit the commands ran against,
and it does not record a single exit code.

Merge commit `2377168c` cannot be that run HEAD: the receipt text carrying the pass
outcomes was committed in `d3e6d64a6fab35662811fab2551f40e4b02c3fbe` at 2026-09-01T08:13:31Z,
38 minutes before `2377168c` was created. `2377168c` is the PR #1096 merge commit — it
proves the merge lineage, not the execution.

The historical pass is therefore preserved as **dated evidence about the 2026-09-01
version**, while the run HEAD and the original exit codes are recorded as **unknown**. They
are not reconstructed from merge lineage, and the 2026-09-01 suite is not re-executed to
backfill historical provenance.

### HUMAN-GCP-WEB-OAUTH-CLIENTS-001

- **Status**: `todo`, owner `Human/Ops` (canonical lookup exit 0, receipt R29)
- **Current role**: standby — required only if the business decides to enable Google OIDC login. It is no longer a deployment blocker for dev / staging / production, and it is not done, waived or superseded.
- See [human-task-handoff.json](human-task-handoff.json) for condition dependencies and entry criteria.

## Auth-Mode Resolver Precedence & Gate Logic

Both runtimes resolve in the same order — explicit mode, legacy boolean, configured
issuer, default local — but their **issuer inputs and error behaviour differ per consumer**.

| | Python release path | Python API boundary | Web runtime (TypeScript) |
|---|---|---|---|
| Entry point | `resolve_auth_mode` ([`shared/auth/mode.py`](../../../../shared/auth/mode.py):68–110) | `oidc_provider_enabled(..., oidc_issuer_vars=API_OIDC_ISSUER_VARS)` (`modules/opsboard/auth/config.py`:302) | `resolveAuthMode` ([`apps/web/src/lib/auth/runtime.ts`](../../../../apps/web/src/lib/auth/runtime.ts):83–119) |
| 1. Explicit mode | `ODP_AUTH_MODE` | `ODP_AUTH_MODE` | `ODP_AUTH_MODE` |
| 2. Legacy boolean | `ODP_AUTH_OIDC_ENABLED` | `ODP_AUTH_OIDC_ENABLED` | `ODP_AUTH_OIDC_ENABLED` |
| 3. Configured issuer | `ODP_WEB_OIDC_ISSUER` only (`DEPLOYMENT_OIDC_ISSUER_VARS`, mode.py:51) | `ODP_AUTH_OIDC_ISSUER`, then `ODP_WEB_OIDC_ISSUER` (`API_OIDC_ISSUER_VARS`, mode.py:56–59) | `ODP_WEB_OIDC_ISSUER` only (runtime.ts:114) |
| 4. Default | `local` | `local` | `local` |
| Invalid / conflicting config | returns `(mode, error)`; **never raises** (mode.py:95–104) | same tuple; `oidc_provider_enabled` is true only when `error is None` (mode.py:126–127) | **throws** `Error` (runtime.ts:89–105) |

`ODP_OIDC_ISSUER` and `ODP_GCP_OIDC_ISSUER` are **not** resolver inputs — neither string
occurs anywhere in the tree (`git grep` exit 1, receipts R22 and R23). The API accepts
`ODP_AUTH_OIDC_ISSUER` in addition because the API process never receives the Web variable;
that is a per-consumer difference, not a third global issuer variable.

> [!IMPORTANT]
> **D15 compliance and the live limitation.** An absent `ODP_AUTH_MODE` does **not**
> guarantee `local`: a configured, non-placeholder `ODP_WEB_OIDC_ISSUER` still resolves to
> `oidc` for pre-contract deployments. Explicit `ODP_AUTH_MODE=local` (or
> `ODP_AUTH_OIDC_ENABLED=false`) forces `local` regardless. Because this task performs no
> live environment readback, **live deployment compliance is recorded as `unknown`**, not
> as pass.

## Auth-Surface Drift Analysis

`git diff --stat 2377168c2cc07cd2470dd8f43de0486fe8d8fc08 95646a5c2bd598b7e219ee51efa7e410cb9fe0f4 -- modules/opsboard/auth shared/auth`
(exit 0, receipt R12) reports 5 files changed, 379 insertions(+), 25 deletions(-):

- `modules/opsboard/auth/claims.py` — 22 ++- (claims parsing and role normalisation)
- `shared/auth/__init__.py` — 24 +++ (tenant and claims exports)
- `shared/auth/abac.py` — 17 --- (ABAC consolidation)
- `shared/auth/rbac.py` — 13 +- (RBAC access checks)
- `shared/auth/tenant.py` — 328 +++ (multi-tenant isolation models)

None of the seven files that implement the D15 controls changed across that same range:
`git diff --quiet 2377168c 95646a5c --` over `shared/auth/mode.py`, `runtime.ts`,
`login/route.ts`, `auth/callback/route.ts`, `localAuth.ts`, `session.ts` and
`loginThrottle.ts` returned **exit 0** (receipt R13), which for `--quiet` means zero
differences. That is a static conclusion about the control surface, not a regression run;
regression coverage is the next section.

## Exact-Head CI Regression

At the inspected head `95646a5c`, GitHub Actions run
[34252508138](https://github.com/alfloop-dev/odayplus/actions/runs/34252508138) (workflow
`CI`, event `merge_group`, conclusion `success`) contains job
[102150167268](https://github.com/alfloop-dev/odayplus/actions/runs/34252508138/job/102150167268)
named `product`, conclusion `success`, completed 2026-09-08T17:05:24Z (receipts R33, R34).

- Step **Test product code** = `success`. Its command, quoted from `.github/workflows/ci.yml`:216–217, is
  `uv run pytest -m "not requires_live_env and not performance" tests modules apps shared models -n auto`.
  `tests/e2e/test_password_first_security_e2e.py`, `tests/security/test_login_throttle_wiring.py`
  and `tests/ops/test_conditional_oidc_deployment.py` all live under `tests/` and carry no
  `requires_live_env` or `performance` marker, so the marker expression does not exclude them.
- Step **Run Node workspace checks** = `success`, i.e. `make node-check`, which per
  `Makefile`:90–97 runs `npm run test --workspaces --if-present` — the `apps/web` vitest
  suite, including `apps/web/tests/login-route.test.ts`.
- Steps **Lint product code** and **Run security checks** also report `success`.

Honest limits: this is a **job-level** CI status observation at a named head with a scope
quoted from workflow source. Per-test outcomes inside the job were not retrieved, so no
test count is claimed for the current head, and the scope was not re-derived from the
runtime log. This documentation task executed no product suite of its own; its own
validation receipts (`git diff --check` and the artifact / JSON / link check) prove the
evidence package, not product regression.

## Callback Fail-Closed Verification

The OIDC callback route at
[`apps/web/src/app/auth/callback/route.ts`](../../../../apps/web/src/app/auth/callback/route.ts)
fails closed across lines 47–88:

- Lines 49–62: `resolveAuthMode(process.env)` inside a `try`; a throw becomes HTTP 503 `WEB_AUTH_NOT_CONFIGURED`.
- Lines 64–74: when `authMode === "local"`, the handler immediately returns HTTP 503 with `Cache-Control: no-store`:
  ```json
  {
    "error": {
      "code": "WEB_AUTH_PROVIDER_DISABLED",
      "summary": "OIDC authentication provider is disabled."
    }
  }
  ```
  This happens before any query parameter is read and before any cookie is opened.
- Lines 76–88: an incomplete OIDC configuration is rejected with HTTP 503 `WEB_AUTH_NOT_CONFIGURED`.
- Line 104: `exchangeAuthorizationCode` — unreachable while `authMode` is `local`.

The login page applies the same rule on the UI side: `showOidc` is
`authMode === "oidc"` ([`apps/web/src/app/login/route.ts`](../../../../apps/web/src/app/login/route.ts):342),
and the OIDC anchor is only built when `showOidc` is true (lines 68–75), so in local mode
the button is absent from the HTML rather than merely hidden. A direct
`GET /login?provider=oidc` in local mode is refused at lines 302–312 with the same fixed
HTTP 503 `WEB_AUTH_PROVIDER_DISABLED` — there is no 404 path and no provider call.

## Evidence Matrix Summary

[auth-mode-evidence-matrix.json](auth-mode-evidence-matrix.json) covers 15 controls
(12 pass, 3 unknown, 0 fail):

| # | Control | Evidence Method | Status |
|---|---|---|---|
| 1 | Auth-mode resolver precedence (`shared/auth/mode.py`:68–110) | Static code inspection | ✅ pass |
| 2 | Web runtime OIDC gate (`apps/web/src/lib/auth/runtime.ts`:83–146) | Static code inspection | ✅ pass |
| 3 | Login route: no OIDC UI, no provider call when off (`login/route.ts`:68–75, 302–312, 342) | Static code inspection | ✅ pass |
| 4 | Callback fail-closed when OIDC off (`auth/callback/route.ts`:47–88) | Static code inspection | ✅ pass |
| 5 | Deployment validation: conditional OIDC (`validate_cloud_run_live_deployment.py`:44, 1422–1434) | Static code inspection | ✅ pass |
| 6 | Terraform checks: conditional OIDC (`infra/terraform/checks.tf`:76, 238, 275) | Static code inspection | ✅ pass |
| 7 | Password login: no Google secret dependency (`localAuth.ts`:127–313) | Static code inspection | ✅ pass |
| 8 | Session / CSRF / throttle integrity (`session.ts`:395–412, `runtime.ts`:157–204) | Static code inspection | ✅ pass |
| 9 | API boundary fail-closed on OIDC tokens (`modules/opsboard/auth/config.py`:23, 302) | Static code inspection | ✅ pass |
| 10 | Conditional-OIDC ops test present (`tests/ops/test_conditional_oidc_deployment.py`) | Static code inspection | ✅ pass |
| 11 | Historical E2E receipt evidence (2026-09-01, composition base `d0c81635df8e842f…`) | Historical receipt reference | ✅ pass |
| 12 | Exact-head product CI at `95646a5c` (run 34252508138, job 102150167268) | CI status observation | ✅ pass |
| 13 | Historical run HEAD and original exit codes for the 2026-09-01 execution | Provenance reconstruction attempt | ⬜ unknown |
| 14 | Live deployment auth-mode readback (Cloud Run dev / staging / prod) | Live environment readback | ⬜ unknown |
| 15 | Live OAuth provider verification (Google OAuth credentials) | Live provider probe (B-stage) | ⬜ unknown |

No D15 product defect was found in the inspected login, callback, session, CSRF, resolver,
API-boundary or conditional-deployment paths, so `code_defects_found` and
`minimum_repair_handoff` in the handoff are empty. That means "none found in that scope",
not "the product is defect-free".

## Identified Gaps & Mitigations

1. **Canonical task archive gap** — ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001 and
   ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002 return exit 1 (`Unknown task`) from the
   canonical writer and are absent from `ai-task-archive`.
   *Mitigation*: their PR merge commits are verifiable ancestors of `dev`, and the
   immutable E2E receipt is in the repository tree.
2. **Historical provenance gap** — the 2026-09-01 run HEAD and original exit codes are
   unknown.
   *Mitigation*: recorded as `unknown`, with the follow-up responsibility written into the
   matrix. Current-head coverage is supplied separately by the exact-head CI observation.
3. **Auth-surface code drift** — 5 files, 379 insertions, 25 deletions between the E2E-002
   merge and the inspected head.
   *Mitigation*: none of the seven D15 control files changed (receipt R13), and the product
   CI job at the inspected head is green.
4. **Live deployment readback unknown** — the deployed configuration is not queried here.
   *Mitigation*: recorded as `unknown`; assigned to Release / Ops during deployment readback.
5. **OAuth provider verification unknown** — live Google OAuth credentials are unverified.
   *Mitigation*: B-stage concern under HUMAN-GCP-WEB-OAUTH-CLIENTS-001.

## Entry Conditions & Handoff

- **WP-90 / canonical-writer handoff**: on A-stage approval, WP-90 / the canonical writer
  sets the HUMAN-GCP-WEB-OAUTH-CLIENTS-001 `next` field to the
  `updated_next_recommendation` text in [human-task-handoff.json](human-task-handoff.json).
  The task stays `todo` under `Human/Ops`. This evidence task does not write that field
  itself and does not unblock, close or waive the human task.
- **OAuth enablement entry conditions (B-stage)**:
  1. *Product Authority*: an explicit business decision to enable Google OIDC login.
  2. *Human/Ops*: execute HUMAN-GCP-WEB-OAUTH-CLIENTS-001 — create the dev, staging and
     production Google Web OAuth clients with that task's callback URLs and store the
     secrets in the existing Secret Manager entries (names only, never values, in any task,
     PR or log).
  3. *Release / Ops Engineer*: deploy with explicit `ODP_AUTH_MODE=oidc`, perform the live
     environment readback that closes control 14, and run the live provider handshake that
     closes control 15.
- **Explicitly not entry conditions**: approving this A-stage package is not an OIDC
  enablement decision, and green CI at `95646a5c` is not evidence about any deployed
  environment.

## Not In Scope

- This package does not create, read or store OAuth client secrets.
- It does not modify product code, deployment configuration or infrastructure.
- It does not mark HUMAN-GCP-WEB-OAUTH-CLIENTS-001 as done, waived or superseded.
- It does not claim the OAuth provider is verified or ready.
- It asserts no security scan, dependency audit or supply-chain gate result.
- B-stage data, formal approval, provider verification and live production capability are
  explicitly deferred; none of them is marked done.

## Contract & Plan References

- [ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md](../../../plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md)
- [ODP_WEB_PASSWORD_FIRST_AUTH_CONTRACT.md](../../../design/ODP_WEB_PASSWORD_FIRST_AUTH_CONTRACT.md) (pinned SHA `04e1572f802a54c2646ba678fe2975226dfbd7c4`)
- [ODP_WEB_PASSWORD_FIRST_SECURITY_E2E_RECEIPT.md](../../e2e/ODP_WEB_PASSWORD_FIRST_SECURITY_E2E_RECEIPT.md) (pinned SHA `04e1572f802a54c2646ba678fe2975226dfbd7c4`)

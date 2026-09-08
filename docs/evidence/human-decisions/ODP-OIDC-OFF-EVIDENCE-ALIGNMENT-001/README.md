# ODP-OIDC-OFF-EVIDENCE-ALIGNMENT-001: Password-First / OIDC-Off Evidence Alignment

- Task: ODP-OIDC-OFF-EVIDENCE-ALIGNMENT-001
- Work Package: WP-20
- Decision Ref: D15 (Google OIDC disabled; password-first is default)
- Owner: Antigravity2
- Reviewer: Codex2
- Inspected Head: `9048161e058becff5a53593a773d3c42238213fb`
- Inspection Date: 2026-09-08T15:27Z
- Stage: A (engineering evidence preparation; B-stage awaits OAuth provider data)

## Purpose

This evidence package reconciles the password-first / OIDC-off implementation
state against the D15 decision from the [execution plan](../../../plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md):

> **D15**: Google OIDC → A：帳密為預設，Google OIDC 不啟用

The package:

1. Verifies that both prerequisite implementation tasks are merged and their
   evidence is reachable.
2. Builds an evidence matrix covering auth-mode resolution, login route,
   deployment validation, Terraform checks, session/CSRF integrity, and
   regression test suites.
3. Updates the standby handoff for the HUMAN-GCP-WEB-OAUTH-CLIENTS-001 human
   task, confirming it is no longer a deployment blocker.

## Artifacts Index

| File | Purpose |
|---|---|
| [auth-mode-evidence-matrix.json](auth-mode-evidence-matrix.json) | 12-control evidence matrix: 11 pass, 1 unknown (OAuth provider — intentionally B-stage) |
| [human-task-handoff.json](human-task-handoff.json) | HUMAN-GCP-WEB-OAUTH-CLIENTS-001 standby handoff with condition dependencies and recommendation |

## Prior Task History

### ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001

- **PR**: [#1074](https://github.com/alfloop-dev/odayplus/pull/1074)
- **Merge commit**: `840081001084ad9586421de908530a41f3a17333`
- **Merged into dev**: ✅ (verified ancestor of current HEAD)
- **Scope**: Terraform / deploy conditional OIDC; `shared/auth/mode.py` resolver;
  deployment validation conditional OIDC secret requirement
- **Canonical history gap**: Not in active `ai-status.json` or `ai-task-archive/`;
  full task definitions preserved in `.orchestrator/task-briefs/` and
  `.orchestrator/github-bus-state.json`. PR merge commit is cryptographically
  verifiable.

### ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002

- **PR**: [#1096](https://github.com/alfloop-dev/odayplus/pull/1096)
- **Merge commit**: `2377168c2cc07cd2470dd8f43de0486fe8d8fc08`
- **Merged into dev**: ✅ (verified ancestor of current HEAD)
- **Receipt**: [`ODP_WEB_PASSWORD_FIRST_SECURITY_E2E_RECEIPT.md`](../../e2e/ODP_WEB_PASSWORD_FIRST_SECURITY_E2E_RECEIPT.md)
- **Verdict**: pass (151 Python tests passed, 22 skipped; 474 Web tests passed)
- **Note**: Supersedes ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-001, which was blocked
  by the login throttle remediation (PR #1085). E2E-001 contained xfail assertions
  against the retired Python throttle service; E2E-002 verified the replacement
  TypeScript throttle with durable datastore.

### HUMAN-GCP-WEB-OAUTH-CLIENTS-001

- **Status**: `todo` (Human/Ops gate)
- **Repositioned by**: ODP-WEB-OAUTH-GATE-RETARGET-001
- **Current role**: Standby — only required when business decides to enable
  Google OIDC login. No longer a deployment blocker for dev/staging/production.
- See [human-task-handoff.json](human-task-handoff.json) for condition
  dependencies and updated recommendation.

## Evidence Matrix Summary

The [auth-mode-evidence-matrix.json](auth-mode-evidence-matrix.json) covers:

| # | Control | Status |
|---|---|---|
| 1 | Auth mode resolver (`shared/auth/mode.py`) — default is `local` | ✅ pass |
| 2 | Web runtime OIDC gate (`apps/web/src/lib/auth/runtime.ts`) | ✅ pass |
| 3 | Login route: no Google button when OIDC off | ✅ pass |
| 4 | Callback fail-closed when OIDC off | ✅ pass |
| 5 | Deployment validation: conditional OIDC | ✅ pass |
| 6 | Terraform checks: conditional OIDC | ✅ pass |
| 7 | Password login: no Google secret dependency | ✅ pass |
| 8 | Session / CSRF / callback: not weakened by OIDC off | ✅ pass |
| 9 | E2E receipt regression evidence | ✅ pass |
| 10 | API boundary multi-issuer | ✅ pass |
| 11 | Ops test conditional OIDC deployment | ✅ pass |
| 12 | OAuth provider verification | ⬜ unknown |

Control #12 (OAuth provider verification) is intentionally unknown: no live GCP
or external OIDC provider is contacted, and HUMAN-GCP-WEB-OAUTH-CLIENTS-001
remains in `todo`. This is a B-stage concern that does not block the A-stage
evidence package.

## Identified Gaps

1. **Canonical task archive**: ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001 and
   ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002 are not in `ai-task-archive/`.
   Mitigation: PR merge commits are cryptographically verifiable ancestors of
   current HEAD; task briefs and bus state are preserved.

2. **E2E receipt base vs current HEAD**: The E2E receipt base (`d0c81635df8e`)
   predates current HEAD (`9048161e`). No auth-surface code changes detected
   between receipt and HEAD, but exact-head re-verification was not re-run in
   this task.

## Not In Scope

- This package does not create or read OAuth client secrets.
- This package does not modify code, deployment config, or infrastructure.
- This package does not mark HUMAN-GCP-WEB-OAUTH-CLIENTS-001 as done.
- This package does not claim the OAuth provider is verified or ready.
- B-stage data, formal approval, provider verification, and production
  capabilities are explicitly deferred; see `next_stage_entry` in the task
  definition.

## Contract Reference

- [ODP_WEB_PASSWORD_FIRST_AUTH_CONTRACT.md](https://github.com/alfloop-dev/odayplus/blob/04e1572f802a54c2646ba678fe2975226dfbd7c4/docs/design/ODP_WEB_PASSWORD_FIRST_AUTH_CONTRACT.md)
  (pinned SHA `04e1572f802a54c2646ba678fe2975226dfbd7c4`)

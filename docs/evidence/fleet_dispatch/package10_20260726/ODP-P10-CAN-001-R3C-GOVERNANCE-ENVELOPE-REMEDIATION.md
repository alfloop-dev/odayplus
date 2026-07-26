---
task_id: ODP-P10-CAN-001-R3C
title: Package 10 Governance delayed-envelope crash remediation
status: dispatched
owner: Claude2
reviewer: Antigravity4
source_branch: origin/fix/package10-final-20260725
updated_at: 2026-07-26
---

# ODP-P10-CAN-001-R3C

## Finding

`ODP-P10-CAN-003-R3A` found a deterministic product failure after the Operator
shell envelope finishes loading:

1. Open `/operator`.
2. Wait approximately three seconds for the shell bootstrap envelope.
3. Open `治理稽核`.
4. The route-level error boundary replaces the workspace.

The browser error is:

```text
TypeError: Cannot read properties of undefined (reading 'toLowerCase')
at moduleClass()
at GovernanceWorkspace.tsx:1534
```

`OperatorConsole` passes `nextEnvelope.approvals` into
`GovernanceWorkspace`. Shell approval records do not carry the required
`GovernanceApproval.module` field, but `moduleClass()` treats it as a required
string. A fast navigation before the envelope resolves can appear healthy, so
the failure must be tested after hydration and through more than one navigation
path.

## Assignment

Fix the product data boundary and rendering contract. Do not change Package 10
E2E assertions, substitute fixtures, hide the workspace, or reintroduce an old
visual implementation.

Prefer a typed normalization or source ownership correction at the boundary.
Defensive rendering may be added as a second layer, but silently fabricating a
module value is not sufficient by itself. The Governance API remains the source
of truth for governance records.

## Writable Paths

- `apps/web/features/operator/OperatorConsole.tsx`
- `apps/web/features/operator/GovernanceWorkspace.tsx`
- `apps/web/features/operator/governanceTypes.ts`
- `apps/web/features/operator/governance/**`
- `apps/web/features/operator/**/*Governance*.test.ts`
- `apps/web/features/operator/**/*Governance*.test.tsx`
- `docs/evidence/fleet_dispatch/package10_20260726/acks/ODP-P10-CAN-001-R3C.json`

## Forbidden Paths

- `tests/e2e/**`
- `apps/api/**`
- Package 10 canonical HTML and archived design evidence
- legacy or retired Operator visual implementations

## Acceptance

1. Waiting for the shell envelope and then opening Govern does not render
   `route-error` and does not emit an uncaught page error.
2. `Today -> Govern`, `Today -> wait -> Govern`, and
   `Today -> Store Ops -> Govern` all render the canonical Govern workspace.
3. Direct `/operator?workspace=govern` navigation and browser reload remain
   usable.
4. Governance approvals, decisions, evidence, reason gate, actor, correlation
   ID, and audit history continue to come from the Governance API.
5. Production mode does not fall back to fixture/seed approval data.
6. Missing or malformed external fields fail closed or render an explicit
   unavailable state; they never crash the route.
7. Add a regression test for the delayed shell-envelope shape mismatch.
8. Web unit tests, web typecheck, root typecheck, build, and the existing
   governance Playwright spec pass.
9. The final diff does not touch any of the 16 `ODP-P10-CAN-003-R3A` specs.
10. An independent Antigravity review is required before merge. If its quota is
    unavailable, leave the task in review and report the blocker.

## Conflict Gate

`ODP-P10-CAN-003-R3A` is concurrently owned by Claude and may modify only the 16
canonical E2E specs plus its ACK. This task owns the product fix and must not
edit those specs. If either worker needs the other task's writable files, stop
and report the conflict before editing.

## Verification

```bash
npm run test --workspace=@oday-plus/web
npm run typecheck --workspace=@oday-plus/web
npm run typecheck
npm run build
npx playwright test tests/e2e/operator-governance.spec.ts --project=chromium --workers=1
git diff --check
```


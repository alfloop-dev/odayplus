# ODP-OC-R4-009 — Expose and bind the complete Govern workspace

**Owner:** Claude **Reviewer:** Antigravity
**Design source (canonical):** `r4-20260707-package-6`
(`docs_archive/00_source_zips/operator_console/r4-20260707-package-6/`),
zip sha256 `db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76`,
interactive HTML `Oday Plus Operator Console.dc.html`,
data-screen-label **`Govern 治理稽核`**.

## Problem

The Govern workspace (`GovernanceWorkspace.tsx`) rendered a rich five-tab
surface but was **not bound to any API**: approvals came from the shell
bootstrap only, while the Decision Log, Audit Trail, Evidence Package export and
system status board were entirely client-side mock (`fallback*` fixtures +
`setTimeout`).  The backend produced no `governanceDecisions` /
`governanceAuditRows`, so those tabs always fell back to fixtures, and the
evidence export and return/reject policy had no server-side enforcement.

## What changed (owned layer)

### Backend

- **`modules/opsboard/application/governance.py`** — new `GovernanceService`.
  - `snapshot()` aggregates the full Govern surface: approvals, Decision Log,
    Audit Trail, status board (Data Quality / Model / Connector / **SLA** /
    **Users** / Runbook) and evidence-package history. It merges a shared
    `GrowthService`'s live Growth decisions/approvals so *Store and Growth
    decisions plus pending Network approvals appear consistently after reload*.
  - `decide()` enforces the **return/reject-requires-reason** policy
    server-side (≥10 non-whitespace chars → `422`), rejects double-decisions
    (`409`) and unknown ids (`404`), and persists a Decision Log row + Audit
    Trail event so the outcome survives reload.
  - `export_evidence_package()` records **scope, range, format, actor,
    correlation id and retention policy**, appends an audit event and a history
    entry.
- **`apps/api/app/routes/operator_modules/governance.py`** — sub-router:
  `GET /operator/governance/snapshot`, `GET /operator/governance/evidence-packages`,
  `POST /operator/governance/decisions` (APPROVE-guarded),
  `POST /operator/governance/evidence-package` (CREATE-guarded).
- **`apps/api/app/routes/operator.py`** — wires the sub-router at composition
  time, sharing the Growth service instance (the only compose-point change).

### Frontend

- **`apps/web/features/operator/governance/governanceLoader.ts`** +
  **`governance/index.ts`** — dual-mode API loader
  (`fetchGovernanceSnapshot` / `submitGovernanceDecision` /
  `exportEvidencePackage`) mirroring the Growth view-model pattern, with a
  fixture fallback so the workspace never breaks offline.
- **`apps/web/features/operator/GovernanceWorkspace.tsx`** — self-fetches the
  snapshot on mount (API becomes source of truth; fixtures remain the SSR /
  offline fallback), routes evidence export and approval decisions through the
  API, and renders the **SLA** and **Users** value builders (previously absent)
  in the status board. Adds `data-testid` hooks for e2e.

### Tests

- **`tests/contract/test_operator_governance_api.py`** — 13 contract tests
  (snapshot value builders, Store/Growth/Network aggregation, server-side reason
  policy, double-decision conflict, evidence metadata, idempotency, fail-closed).
- **`tests/e2e/operator-governance.spec.ts`** — 4 e2e tests (tab reachability +
  DQ/Model/Connector/SLA/Users board, reason gate, aggregation, export audit).
- **`tests/e2e/e2e-operator-console.spec.ts`** — one assertion updated: the
  Evidence Package export audit is now attributed to the acting role instead of
  the removed hardcoded mock actor (necessary consequence of API binding).

## Not changing (intentionally left alone)

`OperatorConsole.tsx`, `GrowthWorkspace.tsx`, `apps/web/features/operator/network/`
(task `do_not_touch`). The Govern workspace binds itself; the console shell is
untouched.

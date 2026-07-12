# Product Flow Implementation Matrix - 2026-07-12

This task-scoped matrix records the ODP-FLOW-010 implementation boundary. It is
not a full sprint status board.

| Flow | UI surface | API surface | State/audit proof | Verification |
|---|---|---|---|---|
| Today queue | `/operator` Today workspace renders API `kpis`, `workQueue`, decisions, risk rows, audit feed | `GET /api/v1/operator/bootstrap`, `GET /today` | Bootstrap state includes notifications and task follow-up | `tests/e2e/e2e-operator-console.spec.ts` |
| Store Ops workflow | Store Ops workspace opens triage/assign/action/field/outcome/escalate/purpose dialogs | `POST /issues/{issue_id}/{action}` and `POST /evidence/{evidence_id}/purpose` | Issue status, queue status, audit feed, governance audit, notification, task, platform audit event | `tests/contract/test_operator_api.py`, Playwright productization gate |
| Governance approvals | Governance workspace consumes live approvals, decision log, and audit rows | `GET /approvals`, `POST /approvals/{approval_id}/decision` | Return/reject reason gate, decision log append, audit row append, platform audit event, idempotent replay | `tests/contract/test_operator_api.py`, governance Playwright test |
| Network review | Network review callback posts decisions for `RV-701` through the same decision endpoint | `POST /approvals/RV-701/decision` | Network approval state prevents browser 404 and records decision/audit state | `ODP-OC-FE-04` Playwright coverage |
| Notification/search/task follow-up | Header notification panel, global search popover, API-backed banner task count | `GET /notifications`, `GET /search`, `GET /tasks` | Workflow writes prepend notification/searchable records/task follow-up | `tests/contract/test_operator_api.py`, browser product gate |

## Acceptance Mapping

- `/operator is React and API backed`: satisfied by React page using
  `/api/v1/operator/bootstrap` plus workflow writes observed by Playwright.
- `server RBAC state transitions persistence and idempotency work`: satisfied
  by server-side `require_permission` guards, `OperatorStateStore`, optional
  `SqliteDocumentStore` persistence, and idempotent write replay.
- `approval decision audit notifications search and task follow up work`:
  satisfied by approval/issue write methods updating decision, audit,
  notification, search, and task read models.
- `productization browser E2E passes`: satisfied by
  `ODP_OPERATOR_PRODUCT_GATE=1 npx playwright test tests/e2e/e2e-operator-console.spec.ts --project=chromium`.

# OpsBoard Module

OpsBoard owns the operator-facing control loop for ODay Plus: Today queue,
Store Ops issue workflow, Growth handoff, Network review, Governance
approvals, audit evidence, notifications, search, and task follow-up.

## Runtime Surface

- Frontend entry: `apps/web/src/app/operator/page.tsx`
- React workspace shell: `apps/web/features/operator/OperatorConsole.tsx`
- API router: `apps/api/app/routes/operator.py` mounted at `/api/v1/operator`
- Audit evidence helpers: `modules/opsboard/audit/`

## ODP-FLOW-010 Boundary

ODP-FLOW-010 makes `/operator` API-backed for product proof:

- `GET /api/v1/operator/bootstrap` returns the initial operator state.
- `GET /api/v1/operator/today`, `/issues`, `/approvals`,
  `/notifications`, `/tasks`, and `/search` expose read models.
- `POST /api/v1/operator/issues/{issue_id}/{action}` persists workflow
  transitions and writes audit, notification, search, and follow-up state.
- `POST /api/v1/operator/approvals/{approval_id}/decision` enforces the
  return/reject reason gate, persists decision log state, and records platform
  audit events.
- `POST /api/v1/operator/evidence/{evidence_id}/purpose` records purpose and
  retention metadata before privacy-scoped evidence is opened.

The default local/test mode keeps state in memory. When the product API is
started with the durable persistence bundle, the operator state is also saved
through `SqliteDocumentStore`, matching the product-grade E2E persistence path.

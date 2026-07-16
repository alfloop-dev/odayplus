# ODP-OC-R4-004 — Growth R4 create entries builder and lifecycle

Owner: Claude · Reviewer: Antigravity6 · Target branch: `dev`

## Scope

On top of the existing Growth API binding (ODP-FIN-FE-001), this task adds the
package 6 canonical Growth productization: **three create-entry cards**, a
**five-step Draft Builder**, a **server-side conflict gate**, a
**submit-for-approval → Govern item** flow, and the full
**Pending Approval → Scheduled → Running → Observing → Outcome Ready** lifecycle
with effectiveness writeback.

## Delivered

### Backend — `modules/opsboard/application/growth.py`
- `create_action` now accepts and persists a draft `kind`
  (`offpeak` / `winback` / `priceops`) plus `store` / `channel` / `budget` /
  `observationWindow` used by the conflict gate.
- Extended lifecycle map: `DRAFT → PENDING_APPROVAL → APPROVED → SCHEDULED →
  RUNNING → OBSERVING → OUTCOME_READY → CLOSED` with an `INEFFECTIVE` branch;
  `EXECUTED` kept as a `RUNNING` alias for backward compatibility.
- `check_conflicts` — five checks (overlap / priceops / budget / fatigue /
  approval). A same-store, same-window active campaign is a hard `fail`.
- `submit_for_approval` — runs the gate (blocked → `GrowthPolicyError`/422 with
  actionable reasons), otherwise creates a Govern approval item
  (`module="Growth"`) and moves the action to `PENDING_APPROVAL`.
- `resolve_approval` — approve → `APPROVED`, reject → `DRAFT`; both append a
  Decision Log entry and an Audit Trail event.
- `write_outcome` — persists `EFFECTIVE` / `INEFFECTIVE` / `INCONCLUSIVE`
  verdicts and appends a Decision Log entry alongside the Audit Trail.

### API — `apps/api/app/routes/operator_modules/growth.py`
New routes (all under `/api/v1/operator/growth`):
`POST /conflicts/check`, `POST /actions/{id}/submit`,
`GET /approvals`, `POST /approvals/{id}/decision`, `GET /decisions`.
`POST /actions` extended with the new draft fields. Write routes keep the
intervention CREATE auth guard, `Idempotency-Key` de-dup and
`X-Correlation-Id` round-trip.

### Frontend — `apps/web/features/operator/growthViewModel.ts` + `GrowthWorkspace.tsx`
- `GrowthKind` / `GrowthStatus` types, `GROWTH_ENTRY_CARDS`, `BUILDER_STEPS`,
  `GROWTH_KIND_PRESETS`, and typed API clients (`checkGrowthConflicts`,
  `submitGrowthForApproval`, `resolveGrowthApproval`, extended
  `createGrowthDraft`).
- **Three entry cards** section — each opens the builder prefilled for its kind
  (`?builder=offpeak|winback|priceops`).
- **Five-step builder** (基本設定 → 客群／時段 → 預估效益 → 風險／衝突 → 送核准):
  step 4 calls the server conflict gate and disables submit when blocked;
  step 5 either creates a DRAFT or creates-and-submits for approval.
- **ApprovalFlowPanel** on DRAFT / PENDING_APPROVAL actions — submit creates a
  Govern item; approve/reject advances the Growth state.
- Existing segment / recommendation / action / closeout surfaces preserved.

### Tests
- `tests/contract/test_operator_growth_api.py` — 12 tests proving all four
  acceptance criteria at the HTTP layer.
- `tests/e2e/operator-growth.spec.ts` — 11 Playwright tests (entry cards,
  five-step builder, conflict step, approval affordance, outcome gates).

## Not changed
Auth/RBAC engine, shared audit module, `OperatorStateService`, and the
`OperatorConsole` / `NetworkFindAreas` / `Governance` workspaces
(`do_not_touch`). Cross-workspace Govern surface integration is ODP-OC-R4-009.

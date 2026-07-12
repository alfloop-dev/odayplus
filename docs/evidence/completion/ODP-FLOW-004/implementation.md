# ODP-FLOW-004 · Complete InterventionOps lifecycle flow — Implementation

- Task: ODP-FLOW-004 (Product Flow Implementation phase)
- Owner: Claude · Reviewer: Codex2
- Source design: `docs_archive/05_module_design/ODP-MOD-05_INTERVENTIONOPS.md` (§7 close-out),
  `docs_archive/06_ai_causal_optimization/ODP-ML-05_CAUSAL_INFERENCE_AND_EXPERIMENT_DESIGN.md` (§7 contamination)

## Scope

The recommendation → eligibility → action → conflict → approval → execution →
observation → matured-outcome path already shipped in `modules/intervention`
(ODP-R4-001). ODP-FLOW-004 closes the remaining gap named in the task summary —
the **close / follow-up** step that turns a matured, evaluated case into a
formally closed decision, optionally spawning the next iteration. It also binds
the intervention workspace UI to the live API.

## What changed

### Domain (`modules/intervention/domain/lifecycle.py`)
- Added the `CLOSED` terminal state. `COMPLETED` is now a **non-terminal**
  "matured outcome" state: an evaluated case still awaits an operator close.
  `TERMINAL_STATUSES` swaps `COMPLETED` → `CLOSED`; `is_terminal` reflects this.
- Added `CloseDisposition` (`KEEP` / `REVERT` / `ITERATE` / `ESCALATE`) and the
  immutable `CloseRecord` (disposition, actor, reason, closed_at, policy_version,
  snapshotted effect `recommendation`, optional `follow_up_intervention_id`).
- `Intervention` gained a `close` field, serialized in `to_dict()`.

### Workflow (`modules/intervention/application/workflow.py`)
- `close_case(...)`: only valid from `COMPLETED` (invalid states raise
  `InterventionError`), **requires a non-empty reason** (high-risk decision),
  records the disposition, transitions `COMPLETED → CLOSED`, and writes an
  audited `intervention.lifecycle.v1` event with action `close`.
- Optional `follow_up=True` opens a linked follow-up **CANDIDATE** for the same
  store via `_open_follow_up(...)`, scheduled to start after the original's
  observation window matures (or its planned end) so the two treatments do not
  overlap and contaminate attribution (ODP-ML-05 §7). The follow-up carries
  `trigger_ref = "follow-up:<original_id>"` and is linked back on the CloseRecord.

### API (`apps/api/app/routes/interventions.py`)
- `POST /interventions/{id}/close` (`ClosePayload`: actor, disposition, reason,
  follow_up, follow_up_kind), RBAC-gated on `intervention:approve`.
- `_run` now maps `ValueError` (which `InterventionError` subclasses) to HTTP 422,
  so an unknown disposition/kind returns a domain 422 rather than a 500.

### Web (`apps/web/features/intervention/InterventionWorkspace.tsx`, `apps/web/src/app/interventions/page.tsx`)
- The route is now `force-dynamic` and builds an `ApiBinding<InterventionSummary>`
  from `GET /interventions` via `getServerApiClient()` + `loadApiBinding`.
- A new `LiveInterventionCases` region renders the live lifecycle (including
  `CLOSED`) with a `DataSourceBadge` (`data-testid="intervention-data-source"`).
  The existing fixture cases remain as a documented non-product fallback for
  cold-store / unconfigured / error binding states — matching the established
  AVM / Audit API-binding pattern.

### Tests
- `tests/integration/test_intervention_workflow.py`: `_drive_to_completed`
  helper + `test_close_completed_case_records_disposition_and_is_terminal`,
  `test_close_requires_reason_and_completed_state`,
  `test_close_with_follow_up_opens_linked_candidate_after_maturity`,
  `test_api_close_case_with_follow_up_and_audit`.
- `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts`: drives the close
  step with a follow-up, asserts `CLOSED` + linked CANDIDATE, and adds `close`
  to the audit-action assertions.

## Acceptance mapping

| Acceptance criterion | Where satisfied |
| --- | --- |
| intervention state machine rejects invalid transitions | `_require_status` on every step incl. `close` (only from `COMPLETED`); `test_close_requires_reason_and_completed_state` |
| execution and observation jobs persist | `InMemoryInterventionRepository.save` on execute/observe; `run_observation_sweep` worker; unchanged, still covered |
| approval outcome and rollback hooks are audited | `approve`/`reject`/`rollback`/`close` all emit `intervention.lifecycle.v1` audit events; asserted in API + E2E |
| API backed UI idempotency and E2E pass | idempotent `POST /interventions` (Idempotency-Key); UI bound to `GET /interventions`; E2E drives the full loop through `close` |

## Post-review fix (2026-07-12 · Antigravity5) — maturity guard

Codex2 reopened the task because `evaluate_effect` always transitioned to
`COMPLETED` regardless of window maturity, and `close_case` only gated on
`COMPLETED`. This let an immature evaluation slip through to `CLOSED`.

### `modules/intervention/application/workflow.py` — `evaluate_effect`

```python
# Before (always COMPLETED)
completed = evaluating.with_transition(to_status=COMPLETED, …)
self.repository.save(completed)

# After (COMPLETED only when mature)
if mature:
    saved = evaluating.with_transition(to_status=COMPLETED, …)
else:
    saved = evaluating   # stays in EVALUATING
self.repository.save(saved)
```

### `modules/intervention/application/workflow.py` — `close_case` (defence-in-depth)

```python
if intervention.effect is not None and not intervention.effect.observation_mature:
    raise InterventionError(
        "cannot close: observation window has not matured "
        "(effect.observation_mature=False); wait until the window settles"
    )
```

Added after the status check; protects against any future code path that reaches
`COMPLETED` without a mature effect.

### Tests

Two new regression tests in `tests/integration/test_intervention_workflow.py`:
- `test_immature_evaluate_then_close_is_rejected` — reproduces the reviewer's
  exact sequence; verifies that an immature `evaluate_effect` stays in
  `EVALUATING` and `close_case` raises `InterventionError("cannot close")`.
- `test_close_defence_in_depth_rejects_immature_effect` — fabricates the
  `COMPLETED` + `observation_mature=False` state to test the second guard layer
  independently.

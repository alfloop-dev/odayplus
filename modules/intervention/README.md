# InterventionOps Module

Shared operational-intervention lifecycle (ODP-MOD-05) reused by PriceOps,
AdLift, promotion, CRM recall, maintenance and cleaning. A single state machine
plus conflict control, observation windows, outcome maturity, Evidence Level
resolution and Label Registry writeback serve every treatment type.

## Layers

- `domain/lifecycle.py` — state machine vocabulary, the `Intervention`
  aggregate, observation windows, outcome / effect / label records, the conflict
  detector and the Evidence Level (L0–L5) resolver (ODP-ML-05 §5, §6).
- `application/workflow.py` — `InterventionWorkflow`, the lifecycle engine that
  enforces the guardrails and audits every transition.
- `infrastructure/repositories.py` — in-memory intervention store (per-store
  indexed for conflict lookups) and the default `InMemoryLabelRegistry` hook.
- `workers/observation_worker.py` — observation-window maturity sweep that can
  auto-evaluate matured interventions.

## Lifecycle (ODP-MOD-05 §7)

```
CANDIDATE → ELIGIBLE/INELIGIBLE → ACTION_PROPOSED → CONFLICT_CHECKING
  → PENDING_APPROVAL → APPROVED/REJECTED → EXECUTING → OBSERVING
  → EVALUATING → COMPLETED/STOPPED/ROLLED_BACK
```

## Guardrails

- Approval and execution are separate steps; execution requires a recorded
  approval, and approval/rejection require a reason (high risk).
- An unresolved overlap conflict blocks approval and is surfaced, never silently
  overwritten (AC-05-02 / CI-003); overriding requires a resolution reason.
- The observation window only opens at execution, so it can never mature early.
- Effect and causal claims are gated: no claim before observation maturity, no
  causal claim without a matched control group and a passing pre-trend. Every
  evaluation carries an Evidence Level (L0–L5).
- A matured evaluation writes a `LabelRecord` to the Label Registry so
  ForecastOps can exclude or mark the intervened period (AC-05-05).

## API

`apps/api/app/routes/interventions.py` exposes the lifecycle (create, eligibility,
action, conflict-check, submit, approve/reject, execute, outcomes, evaluate,
label) and is wired into `apps/api/oday_api/main.py`.

## Tests

`tests/integration/test_intervention_workflow.py`.

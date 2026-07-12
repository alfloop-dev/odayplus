# Product Flow Implementation Matrix — 2026-07-12

Tracks the "Product Flow Implementation" wave (ODP-FLOW-\*): for each end-to-end
product flow, the state machine, persistence, audit, API-backed UI, and E2E
coverage that make the loop demonstrably closed.

Each FLOW owner appends/updates the row for their task. Columns:

- **State machine** — canonical states + invalid-transition rejection.
- **Jobs persist** — execution/observation/background jobs are durably recorded.
- **Audited** — approval / execution / outcome / rollback / close emit audit events.
- **API-backed UI** — UI renders live API data with a documented fixture fallback.
- **E2E** — a product-grade E2E drives the full loop.
- **Evidence** — completion implementation/verification refs.

| Task | Flow | State machine | Jobs persist | Audited | API-backed UI | E2E | Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ODP-FLOW-003 | ForecastOps timeseries → versioned forecast+uncertainty → four-light alert → **acknowledge** → **executable intervention handoff** | ✅ alert `open→acknowledged` (once-only, closed-alert reject); handoff `proposed→dispatched` (no double-dispatch) | ✅ versioned `save_forecast` + `save_alert`/`save_handoff` persist ack + dispatch state | ✅ `forecastops.alert.acknowledged.v1` + `forecastops.handoff.executed.v1` (+ existing `forecasted.v1`) | ✅ `GET /forecastops/alerts` binding + `DataSourceBadge` on Overview & Alert center, fixtures as fallback | ✅ `e2e-ops-intervention-price-ad-product.spec.ts` drives forecast → acknowledge → execute-handoff → intervention | `docs/evidence/completion/ODP-FLOW-003/{implementation,verification}.md` |

## Notes

- ODP-FLOW-003 closed the two remaining named steps of the ForecastOps loop:
  **alert acknowledgement** (a persisted, once-only human action on a four-light
  alert) and an **executable intervention handoff** (dispatching a `proposed`
  handoff records the actor, time, and linked InterventionOps case). Versioned
  forecast + uncertainty persistence already shipped in ODP-R3-001; this task
  makes the alert→acknowledge→handoff→intervention hand-off demonstrably closed,
  audited, and API-backed. See the completion evidence for the acceptance
  mapping and exact verification commands.

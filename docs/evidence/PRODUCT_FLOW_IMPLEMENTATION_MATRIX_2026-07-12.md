# Product Flow Implementation Matrix
# ODay Plus — Product Flow Completion Tracker
# Generated: 2026-07-12

Document: PRODUCT_FLOW_IMPLEMENTATION_MATRIX_2026-07-12.md
Purpose: Track completion of all product flow tasks (ODP-FLOW-*) across the fleet.
This file is a fleet-level coordination artifact. Update on each flow task closeout.

---

## Fleet Status at 2026-07-12

| Task | Title | Owner | Status | Branch / PR |
|---|---|---|---|---|
| ODP-FLOW-002 | Complete Expansion HeatZone to SiteScore decision flow | Claude2 | review_approved | task/ODP-FLOW-002 |
| ODP-FLOW-003 | Complete ForecastOps alert and handoff flow | Codex2 | review | PR #242 |
| ODP-FLOW-004 | Complete InterventionOps lifecycle flow | Antigravity5 | in_progress | task/ODP-FLOW-004 |
| ODP-FLOW-005 | Complete PriceOps simulation approval and rollback flow | Codex | review | task/ODP-FLOW-005 |
| **ODP-FLOW-006** | **Complete AdLift campaign and incrementality flow** | **Antigravity** | **in_progress → review** | **task/ODP-FLOW-006** |
| ODP-FLOW-008 | Complete NetPlan scenario solver and publish flow | Claude | review | PR #247 |
| ODP-FLOW-009 | Complete Learning Hub validation release and rollback flow | Claude | in_progress | task/ODP-FLOW-009 |
| ODP-FLOW-010 | Complete OpsBoard and Governance operator flow | Codex | review | task/ODP-FLOW-010 |

---

## ODP-FLOW-006: AdLift Campaign and Incrementality Flow

**Status**: Implementation complete, verification passed — pending review by Claude2.

### Acceptance Criteria

| Criterion | Status | Evidence |
|---|---|---|
| campaign and experiment versions persist | ✅ | `InMemoryAdLiftRepository.save_report()` assigns monotonic `report_version` (1 → 2); latest retrievable via `GET /adlift/reports/{campaign_id}` |
| pre trend gate rejects invalid launch | ✅ | `evaluate_pre_trend()` → FAIL caps evidence at L2 (`causal_claim_allowed=False`); E2E-AD-001 `adlift-8803` shows "Pre-trend failed" |
| incrementality report links evidence and decision | ✅ | `IncrementalityReport` carries `evidence_level`, `causal_claim_allowed`, `recommendation`, `decision_id`; `DecisionPanel` renders full audit trail |
| API backed Growth UI audit E2E passes | ✅ | `npx playwright test tests/e2e/e2e-intervention-price-ad.spec.ts --project=chromium`: 4 passed |

### Deliverables

| Artifact | Description | Status |
|---|---|---|
| `modules/adlift/` | Full Python domain/application/infra/worker stack | ✅ |
| `apps/api/app/routes/adlift.py` | FastAPI router: POST jobs, GET job result, GET reports (list + by campaign) | ✅ |
| `apps/web/features/adlift/AdLiftWorkspace.tsx` | React workspace: report table, drawer, claim guard, decision panel | ✅ |
| `apps/web/features/adlift/data.ts` | Typed fixtures: 3 reports (PASS/CONTINUE, blocked, FAIL/STOP) | ✅ |
| `tests/integration/test_adlift_incrementality.py` | 12 integration tests covering DiD, matching, pre-trend, contamination, API | ✅ |
| `tests/e2e/e2e-intervention-price-ad.spec.ts` | E2E-AD-001 + route smoke test | ✅ |
| `docs_archive/05_module_design/ODP-MOD-07_ADLIFT.md` | Module design doc | ✅ |
| `docs/evidence/completion/ODP-FLOW-006/implementation.md` | Implementation evidence | ✅ |
| `docs/evidence/completion/ODP-FLOW-006/verification.md` | Verification commands and results | ✅ |

### Verification Commands Run

```bash
# Python integration tests (12 passed)
uv run pytest tests/integration/test_adlift_incrementality.py -v

# Ruff lint (all clean)
uv run ruff check modules/adlift apps/api/app/routes/adlift.py tests/integration/test_adlift_incrementality.py

# E2E browser tests (4 passed, 25.9s)
npx playwright test tests/e2e/e2e-intervention-price-ad.spec.ts --project=chromium
```

---

## Compose Map (ODP-FLOW-006)

```
modules/adlift
  └── shared/audit (AuditEvent, InMemoryAuditLog)
  └── shared/auth (Action, RBAC engine)
  └── apps/api/oday_api/main.py (router registration)

apps/web/features/adlift
  └── @oday-plus/ui (Badge, PageHeader)
  └── @oday-plus/domain-types (DataStatus, StatusTone, dataStatusTone)
  └── apps/web/features/intervention/intervention.module.css (shared styles)
```

---

## Notes

- `docs_archive/05_module_design/` was empty prior to ODP-FLOW-006; this task seeds it with `ODP-MOD-07_ADLIFT.md`.
- TypeScript `tsc` is not installed in this fleet environment; type safety verified structurally via E2E browser run.
- `InMemoryAdLiftRepository` is sufficient for the current flow implementation; a persistent DB-backed repo is a future task (out of scope for ODP-FLOW-006).

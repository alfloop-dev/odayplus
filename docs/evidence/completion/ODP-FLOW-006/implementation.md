# ODP-FLOW-006: AdLift Campaign and Incrementality Flow — Implementation Evidence

Task-ID: ODP-FLOW-006
Owner: Antigravity
Reviewer: Claude2
Phase: Product Flow Implementation
Generated: 2026-07-12T15:39:00Z

## Summary

Complete AdLift campaign design → matched control → pre-trend → launch → observation →
incrementality report → decision closed loop.

## Acceptance Criteria Mapping

| Acceptance criterion | Implementation location | Status |
|---|---|---|
| campaign and experiment versions persist | `modules/adlift/infrastructure/repositories.py` → `InMemoryAdLiftRepository.save_report()` assigns `report_version` via list length; each campaign keeps a version list | ✅ |
| pre trend gate rejects invalid launch | `modules/adlift/domain/incrementality.py` → `evaluate_pre_trend()` + `assign_evidence_level()` caps evidence at `L2_MATCHED_DESCRIPTIVE` and sets `causal_claim_allowed=False` when `pre_trend_status != PASS`; UI `ReportDrawer` shows warning block with `adlift-claim-guard` testid | ✅ |
| incrementality report links evidence and decision | `modules/adlift/domain/incrementality.py` → `IncrementalityReport` carries `evidence_level`, `causal_claim_allowed`, `recommendation`, `intervention_writeback`, and `label_registry_entry`; frontend `DecisionPanel` renders `decision_id`, `model_version`, `policy_version`, `correlation_id` | ✅ |
| API backed Growth UI audit E2E passes | `apps/api/app/routes/adlift.py` + `apps/web/features/adlift/AdLiftWorkspace.tsx`; E2E spec `tests/e2e/e2e-intervention-price-ad.spec.ts` (E2E-AD-001) covers all three report states | ✅ |

## Delivered Layers

### Backend domain (`modules/adlift/`)

```
modules/adlift/
  domain/incrementality.py        — AdCampaign, StoreDayMetric, MatchedControl, PreTrendResult,
                                    IncrementalityEstimate, IncrementalityReport, EvidenceLevel,
                                    Recommendation; run_incrementality(), match_controls(),
                                    evaluate_pre_trend(), detect_contamination(),
                                    assign_evidence_level(), recommend()
  application/incrementality.py   — AdLiftService.evaluate() wraps run_incrementality + repository
  infrastructure/repositories.py  — InMemoryAdLiftRepository with per-campaign versioned list
  workers/incrementality_worker.py — AdLiftIncrementalityWorker, run_adlift_incrementality_batch()
  __init__.py                     — full public API re-export
```

Key algorithms:
- **Matched controls**: greedy nearest-neighbor by pre-period mean revenue, 1:1 no replacement (ODP-ML-05 §8)
- **Pre-trend test**: normalised slope comparison; `PASS | FAIL | INCONCLUSIVE | NOT_TESTED` (AC-07-02)
- **DiD incrementality**: `(treatment_post − treatment_pre) − (control_post − control_pre)` per store-day (ODP-ML-05 §9.1); surface revenue kept separate from incremental estimate (AC-07-03)
- **IROMI**: incremental gross margin ÷ ad spend (AC-07-04)
- **Evidence ladder**: L0–L5 causal ladder (ODP-ML-05 §5); L3 required for causal claim
- **Recommendation**: SCALE (iromi ≥ 2.0) / CONTINUE (iromi ≥ 1.0) / STOP; INCONCLUSIVE below L3

### API layer (`apps/api/app/routes/adlift.py`)

- `POST /adlift/incrementality-jobs` — runs batch, idempotency-key dedup, audit event written
- `GET /adlift/incrementality-jobs/{job_id}` — result retrieval
- `GET /adlift/reports/latest` — all latest per-campaign reports
- `GET /adlift/reports/{campaign_id}/history` — versioned report history
- RBAC: `adlift / CREATE` (authz engine + audit log wired)

### Frontend (`apps/web/features/adlift/`)

```
AdLiftWorkspace.tsx   — PageHeader, nav, overview summary cards, FilterBar (evidence/recommendation),
                         ReportTable (treatment/control/pre-trend/iROMI/evidence/recommendation columns),
                         ReportDrawer (adlift-report-card, adlift-claim-guard, DecisionPanel,
                         adlift-decision-panel with decision_id/audit trail)
data.ts               — AdLiftReport type; 3 fixture reports covering PASS (CONTINUE), INSUFFICIENT_CONTROL
                         (REVIEW_ONLY, contamination), FAILED pre-trend (STOP)
```

Page route: `apps/web/src/app/adlift/page.tsx` → `AdLiftWorkspace`.

### Integration tests (`tests/integration/test_adlift_incrementality.py`)

12 tests covering:
- DiD isolates market movement from ad lift
- Break-even / below-break-even IROMI thresholds (CONTINUE / STOP)
- Matched control pairing by pre-period level
- Pre-trend failure caps evidence at L2 and blocks causal claim
- Contamination from intervention overlap caps evidence at L2
- No-control fallback (L1 before/after)
- Idempotency: repeated campaign evaluation produces stable result
- API route: POST creates job, GET retrieves; idempotency-key dedup works

### E2E tests (`tests/e2e/e2e-intervention-price-ad.spec.ts`)

Test `E2E-AD-001` validates three AdLift states:
- `adlift-8801`: treatment/control visible, iROMI shown, claim guard says "causal incrementality claim allowed"
- `adlift-8802`: "No matched control", "Contamination", `dec-adlift-8802-review` decision_id
- `adlift-8803`: "Pre-trend failed" in claim guard

## Compose Surface

| Layer | Composes with |
|---|---|
| `modules/adlift` | `shared/audit` (AuditEvent), `shared/auth` (Action), `apps/api/oday_api/main.py` (router mount) |
| `apps/api/app/routes/adlift.py` | `apps/api/oday_api/security/dependencies` (RBAC engine), `shared/audit.InMemoryAuditLog` |
| `apps/web/features/adlift` | `@oday-plus/ui` (Badge, PageHeader), `@oday-plus/domain-types` (DataStatus, StatusTone, dataStatusTone) |

## Not Changing

- Canonical schema (`shared/domain/models.py`) — no new DB entities introduced; AdLift is in-memory
- Orchestrator dispatch config (`.orchestrator/`)
- Other module domains (priceops, interventionops, forecastops, sitescore, etc.)

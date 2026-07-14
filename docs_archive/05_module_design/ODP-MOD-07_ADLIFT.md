# ODP-MOD-07: AdLift — Ad Incrementality Measurement Module

Document-ID: ODP-MOD-07
Version: 1.0
Phase: R4 — Intervention / Price / Ad
Status: Implemented (ODP-FLOW-006)
Owner: Antigravity
Reviewer: Claude2

---

## 1. Purpose

AdLift provides ad campaign incrementality evaluation and evidence grading for the ODay Plus platform.
Given a campaign with treatment stores, candidate control stores, a pre-period, and a campaign period
of daily store metrics plus ad spend, it produces a causal incrementality report with evidence level,
recommendation, and decision linkage.

This module implements:
- ODP-ML-05 §8 (matched control selection)
- ODP-ML-05 §8.3 (pre-trend parallel assumption test)
- ODP-ML-05 §9.1 (matched-pair DiD estimation)
- AC-07-01 (campaign design inputs)
- AC-07-02 (pre-trend gate)
- AC-07-03 (surface vs incremental revenue)
- AC-07-04 (IROMI)

---

## 2. Domain Model

### 2.1 Inputs

**AdCampaign** — the experiment design spec:

| Field | Type | Description |
|---|---|---|
| `campaign_id` | str | Unique campaign identifier |
| `name` | str | Human-readable campaign name |
| `channel` | str | Ad channel (paid_search, display, etc.) |
| `audience` | str | Target audience segment |
| `treatment_store_ids` | Sequence[str] | Stores receiving the ad treatment |
| `candidate_control_store_ids` | Sequence[str] | Candidate control stores for matching |
| `pre_period_start / end` | date | Pre-period window for trend and baseline |
| `campaign_period_start / end` | date | Campaign observation window |
| `ad_spend` | float | Total ad spend (for IROMI) |
| `observations` | Sequence[StoreDayMetric] | Daily revenue/GM observations per store |
| `campaign_intervention_id` | str | Linked intervention ID for contamination check |

**StoreDayMetric** — one store's metrics for one calendar day:

| Field | Type | Description |
|---|---|---|
| `store_id` | str | Store identifier |
| `business_date` | date | Observation date |
| `revenue` | float | Revenue observed |
| `gross_margin` | float | Gross margin observed |
| `active_intervention_ids` | tuple[str, ...] | Any other active interventions (contamination guard) |
| `source_snapshot_ids` | tuple[str, ...] | Lineage back to source POS snapshots |

### 2.2 Outputs

**IncrementalityReport** — the full evaluation result:

| Field | Type | Description |
|---|---|---|
| `report_id` | str | Unique report identifier (UUID-based) |
| `report_version` | int | Monotonic version per campaign (increments on re-evaluation) |
| `campaign_id` | str | Source campaign |
| `matched_controls` | tuple[MatchedControl, ...] | Treatment→control pairs |
| `pre_trend_status` | PreTrendStatus | PASS / FAIL / INCONCLUSIVE / NOT_TESTED |
| `pre_trend` | PreTrendResult | Slope, divergence, threshold details |
| `measurement_method` | str | "DID" |
| `surface_revenue` | float | Raw observed treatment campaign revenue |
| `incremental_revenue` | float | DiD revenue estimate |
| `incremental_gross_margin` | float | DiD gross margin estimate |
| `iromi` | float | Incremental GM ÷ ad spend |
| `evidence_level` | EvidenceLevel | L0–L5 causal ladder |
| `causal_claim_allowed` | bool | True only at L3+ |
| `recommendation` | Recommendation | SCALE / CONTINUE / STOP / INCONCLUSIVE |
| `contamination` | tuple[ContaminationFinding, ...] | Stores with overlapping interventions |
| `intervention_writeback` | InterventionWriteback | Evidence summary for the intervention record |
| `label_registry_entry` | LabelRegistryEntry | ML label registration metadata |
| `model_version` | str | Pinned algorithm version |
| `feature_version` | str | Feature set version |
| `policy_version` | str | Evidence policy version |
| `generated_at` | datetime | Report generation timestamp |
| `source_snapshot_ids` | tuple[str, ...] | Source lineage from all observations |

---

## 3. Algorithms

### 3.1 Matched Control Selection (ODP-ML-05 §8)

Greedy 1:1 nearest-neighbor matching by pre-period mean revenue. No replacement.

```
For each treatment store (sorted by pre_mean desc):
  Find the unassigned control store with minimum |pre_mean_t − pre_mean_c|
  Assign as matched pair
```

Control stores that cannot be matched are excluded from the analysis.

### 3.2 Pre-Trend Test (ODP-ML-05 §8.3, AC-07-02)

Validates the parallel-trends assumption before accepting the DiD estimate.

1. Compute normalised daily growth slope for each treatment store during pre-period
2. Compute normalised daily growth slope for each matched control store during pre-period
3. Compute slope divergence = |mean_treatment_slope − mean_control_slope|
4. Compare against `threshold` (default 0.1)
   - `PASS` — divergence ≤ threshold
   - `FAIL` — divergence > threshold
   - `INCONCLUSIVE` — fewer than 2 pre-period observations
   - `NOT_TESTED` — no matched controls available

### 3.3 DiD Incrementality (ODP-ML-05 §9.1, AC-07-03)

Matched-pair difference-in-differences per store-day:

```
effect_per_store_day = (treatment_post_mean − treatment_pre_mean)
                     − (control_post_mean − control_pre_mean)
incremental_revenue  = sum over all (treatment, control) pairs of
                       effect_per_store_day × campaign_days_for_treatment_store
```

Surface revenue (raw observed treatment campaign revenue) is reported separately from the
incremental estimate so the report shows both.

### 3.4 IROMI (AC-07-04)

```
IROMI = incremental_gross_margin / ad_spend   (0.0 if ad_spend == 0)
```

### 3.5 Evidence Ladder (ODP-ML-05 §5)

| Level | Name | Condition |
|---|---|---|
| L0 | Anecdotal | No treatment data |
| L1 | Before/After | Treatment data but no control group |
| L2 | Matched Descriptive | Control present but pre-trend FAIL or contamination |
| L3 | DiD Validated | Control present, pre-trend PASS, no contamination |
| L4 | RCT Validated | (future: randomised assignment) |
| L5 | Causal Model | (future: structural causal model) |

Only L3+ allows a causal claim (`causal_claim_allowed = True`).

### 3.6 Recommendation (ODP-ML-05 §15.2, §15.3)

| Condition | Recommendation |
|---|---|
| evidence_level < L3 | INCONCLUSIVE |
| iromi ≥ 2.0 (scale threshold) | SCALE |
| iromi ≥ 1.0 (continue threshold) | CONTINUE |
| iromi < 1.0 | STOP |

---

## 4. API

`POST /adlift/incrementality-jobs` — submit a batch of campaigns for evaluation.

Request body:
```json
{
  "campaigns": [ { ...AdCampaign fields... } ],
  "generated_at": "2026-06-28T09:00:00Z",
  "idempotency_key": "optional-client-key"
}
```

Response (202 Accepted):
```json
{
  "job_id": "adlift-incrementality-<uuid>",
  "status": "succeeded",
  "reports": [ { ...IncrementalityReport.to_dict()... } ],
  "completed_at": "2026-06-28T09:00:01Z"
}
```

Idempotency: repeated calls with the same `Idempotency-Key` header or body field return the
original result without re-running.

`GET /adlift/incrementality-jobs/{job_id}` — retrieve a previous result.

`GET /adlift/reports/latest` — latest report per campaign.

`GET /adlift/reports/{campaign_id}/history` — all versioned reports for a campaign.

RBAC: `adlift / CREATE` required for job submission (via `shared/auth/engine`). Audit events
written to `InMemoryAuditLog` with type `adlift.incrementality_evaluated.v1`.

---

## 5. Contamination Guard

During the campaign period, if a treatment or control store has any active intervention that is
**not** the campaign's own `campaign_intervention_id`, it is flagged as a contamination finding.
Contamination findings cap evidence at L2 (cannot claim causality) even if pre-trend passes.

---

## 6. Versioning and Audit

Each campaign maintains a versioned list of reports. Re-evaluation increments `report_version`.
Old versions are not overwritten. The report carries:
- `model_version` — algorithm version (e.g., `adlift-incrementality-v1.1.0`)
- `feature_version` — feature set version
- `policy_version` — evidence policy version
- `source_snapshot_ids` — lineage chain to source POS data

---

## 7. UI Specification

See `docs/design/ODAY_PLUS_PRICING_AND_ADLIFT_UI_SPEC.md` for the full screen specification.

Frontend: `apps/web/features/adlift/AdLiftWorkspace.tsx`

Key UI elements:
- `data-testid="adlift-data-status"` — freshness badge
- `data-testid="adlift-page"` — main workspace
- `data-testid="adlift-table"` — report list with treatment/control/pre-trend/iROMI/evidence/recommendation
- `data-testid="adlift-report-card"` — report drawer
- `data-testid="adlift-claim-guard"` — causal claim guard block (orange warning if blocked)
- `data-testid="adlift-decision-panel"` — continue/stop decision form with audit trail

---

## 8. Source Documents

- `docs_archive/06_ai_causal_optimization/ODP-ML-05_CAUSAL_INCREMENTALITY.md` (AC-07-01..04)
- `docs/design/ODAY_PLUS_PRICING_AND_ADLIFT_UI_SPEC.md`
- `docs_archive/08_qa_acceptance/ODP-QA-03_END_TO_END_TEST_SCENARIOS.md`

---

## 9. Implementation Status

| Component | Status | Commit |
|---|---|---|
| `modules/adlift/domain/incrementality.py` | ✅ Complete | on `task/ODP-FLOW-006` |
| `modules/adlift/application/incrementality.py` | ✅ Complete | on `task/ODP-FLOW-006` |
| `modules/adlift/infrastructure/repositories.py` | ✅ Complete | on `task/ODP-FLOW-006` |
| `modules/adlift/workers/incrementality_worker.py` | ✅ Complete | on `task/ODP-FLOW-006` |
| `apps/api/app/routes/adlift.py` | ✅ Complete | on `task/ODP-FLOW-006` |
| `apps/web/features/adlift/AdLiftWorkspace.tsx` | ✅ Complete | on `task/ODP-FLOW-006` |
| `tests/integration/test_adlift_incrementality.py` | ✅ 12 passed | on `task/ODP-FLOW-006` |
| `tests/e2e/e2e-intervention-price-ad.spec.ts` (E2E-AD-001) | ✅ 4 passed | on `task/ODP-FLOW-006` |

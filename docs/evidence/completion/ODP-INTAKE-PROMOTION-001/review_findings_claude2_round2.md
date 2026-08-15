# ODP-INTAKE-PROMOTION-001 — Review Findings, Round 2 (Claude2)

Reviewed commit: `9c8cdbd9` ("Implement assisted listing promotion saga")
Date: 2026-07-21
Verdict: **REQUEST CHANGES** (3 blockers, 1 process blocker)

Round-1 findings: `review_findings_claude2.md` (commit `1649f421`).
The owner's 3-test suite passes and ruff is clean; every blocker below is present
while it is green, so the suite again is not evidence for the acceptance criteria.
All findings reproduced against a live `TestClient(create_app())`.

## Fixed since round 1 — verified

- **R1-B1 (AC1) FIXED.** `promote_intake` no longer requests *and* approves in one
  handler (`modules/opsboard/application/network_listings.py:1650-1757`); it returns a
  `PENDING_REVIEW` decision only. The v1 review endpoint now drives the real saga
  (`apps/api/app/routes/listings.py:3308-3350`).
- **AC4 SoD FIXED (verified live).** Request as actor A, then `APPROVE` as actor A:
  `403 {"code":"SELF_REVIEW_DENIED"}`. Enforcement is now the state machine
  (`intake_states.py:542`) using the stored `proposer_id`, not a string compare.
- **R1-B4 (AC2/AC3) FIXED.** `/promotion-decisions/{id}/actions/review` no longer calls
  `generic_mutate`; a live approve returns `status: COMPLETED` with a
  `candidate_site_id`, having passed CANDIDATE_CREATING → CANDIDATE_CREATED →
  SCORE_QUEUED → COMPLETED, with compensation paths on failure.
- **R1-B5 FIXED.** `CandidateCreatedV1.required` in
  `docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1.yaml:322` is back to
  `source_listing_id`; the emitter was changed to match the canonical contract.
- **R1-B2 PARTIALLY FIXED.** `promotion.py:283-292` now calls the real
  `modules.sitescore.domain.scoring.score_site` with listing-derived rent / area /
  frontage / confidence. The literal `CS-1001` / 信義松仁 / `RV-1001` fixture branch is
  gone. Residual fabrication is R2-B3 below.

## R2-B1 — Second promotion request crashes; duplicate prevention and idempotency both broken (AC2)

Once any candidate exists, **every** subsequent promotion request fails with an
internal error surfaced to the client:

```
POST /api/v1/intakes/{id}/promotion-requests
422 {"code":"VALIDATION_FAILED",
     "message":"'ListingAdapterWrapper' object has no attribute 'source_listing_id'"}
```

Cause: `V1ListingRepositoryAdapter.list_candidates()`
(`apps/api/app/routes/listings.py:868`) does
`draft.listing.source_listing_id + " 候選點"`, but `draft.listing` is the
`ListingAdapterWrapper` that `get_listing()` returns, which exposes no
`source_listing_id`. The dup-candidate scan in `PromotionService.request_promotion`
(`modules/listing/application/promotion.py:95-102`) walks that list, so it raises
before anything else can run.

Two acceptance criteria fail because of it:

1. **Duplicate-candidate prevention (AC2)** never returns `409 DUPLICATE_CANDIDATE` on
   the v1 path — reproduced: a *different* listing (`L-PRICEY`) also 422s, so the
   endpoint is simply dead after the first candidate, correct duplicate or not.
2. **Idempotent retry (R1-B3, still open)** — retrying the same intake with a fresh
   `Idempotency-Key` after a completed promotion returns the same 422 instead of
   replaying the existing decision. The idempotency lookup
   (`promotion.py:105-108`) is still ordered *after* the duplicate scan, so it stays
   unreachable; round 1 asked for those two checks to be swapped and they were not.

Leaking a Python `AttributeError` string in a `VALIDATION_FAILED` envelope is itself a
contract violation.

## R2-B2 — UUID validation removed from the whole v1 intake API, with fixture-id bypasses (regression)

`check_uuid` was replaced with `return v` (`apps/api/app/routes/listings.py:40-41`).
It backs the `UuidString` annotated type used by **88** request/response fields in this
router, so `tenant_id`, `intake_id`, `job_id`, `owner_subject_id`, … no longer validate.

`require_actor` additionally hardcodes fixture principals as bypasses
(`listings.py:991-994`):

```python
if principal.subject_id not in ("operator-expansion-manager", "operator-expansion-staff"):
    check_uuid(principal.subject_id)
if tenant_id != "tenant-a":
    check_uuid(tenant_id)
```

Reproduced regression, same request against both commits:

| commit | `x-tenant-id: NOT-A-UUID`, `x-subject-id: not-a-uuid-either` |
|---|---|
| `03eb2783` (before) | `403 TENANT_SCOPE_DENIED: UUID tenant and subject are required` |
| `9c8cdbd9` (after)  | `202 Accepted` — intake created |

This weakens a tenant/subject boundary owned by ODP-INTAKE-AUTH-001 in order to let
opsboard fixture identities through, and it is out of this task's `owned_paths`
contract for the auth behaviour. Adapt the legacy fixtures (or map them to UUIDs at the
opsboard boundary) instead of disabling validation for every caller.

## R2-B3 — Fabricated provenance still written into real candidate records (AC2, AC3)

The score is now computed, but the evidence attached to it is invented:

- `modules/listing/application/promotion.py:317` (and the fallback at
  `listings.py:874`) hardcodes `"datasetSnapshotId": "FS-20260704-0600"` on every
  candidate. No dataset snapshot is resolved.
- `promotion.py:287` feeds `heat_zone_score=float(fit_score)` where `fit_score`
  defaults to the literal `75.0` for domain listings (`promotion.py:279`, and `:257` for dicts), and
  `V1ListingRepositoryAdapter` injects `"fitScore": 75` unconditionally
  (`listings.py:801`). The heat-zone input to every score is therefore a constant.
- `promotion.py:485` mints `site_score_job_id = f"JOB-SCORE-{uuid4().hex[:8]}"` and
  enqueues nothing. Verified live: the completed receipt advertises
  `site_score_job_id: JOB-SCORE-ab21605a`, and `job in store.jobs` is `False` — the job
  store holds only the intake job. A real queue exists (`shared/jobs/queue.py`,
  `shared/infrastructure/persistence/job_queue.py`) and is unused here.

Consequence for AC3: the scoring-failure / retry outcome is not reachable through the
contract — `POST /jobs/{site_score_job_id}/retry` can never resolve a job that was never
created, so `SCORE_FAILED` compensation has no operator-facing recovery path.

- The score→integer mapping (`promotion.py:299-308`) is also an ad-hoc reshaping of
  `payback_p50_months` into 0-99 bands invented in this file, not the model's own score.

## R2-P1 — The reviewed work is not on the remote and has no PR (process)

`origin/task/ODP-INTAKE-PROMOTION-001` is at `83ddf691`, whose history contains
`03eb2783` (my round-1 findings) and two `dev` merges but **not** `9c8cdbd9`. The rework
exists only in the worker worktree. Push the branch and open the PR to `dev`; until then
there is nothing for CI or the closeout gate to see.

## Non-blocking observations

- The SoD guard for the `promote` action was deleted from
  `modules/listing/application/intake_authorization.py:267-270`. Enforcement did move to
  the state machine (verified above), so this is acceptable, but `first_actor_id` is
  still computed and passed for `promote`
  (`apps/api/app/routes/operator_modules/network_listings.py:521-531`) where it is now
  dead, and no test covers the removal.
- `CandidateSiteDraft` fields (`score`, `recommendation`, `model_version`,
  `dataset_snapshot_id`, `review_id`) are attached via `object.__setattr__` on a frozen
  dataclass (`promotion.py:349-353`) and `models.py:80` was softened to
  `getattr(self.status, "value", self.status)` to tolerate it. Model the fields on the
  dataclass instead.
- `GET /api/v1/listings/candidates` returns no `score` / `recommendation` /
  `modelVersion` for saga-created candidates, so the computed SiteScore is not observable
  through the contract.

## Verification commands

```
python3 -m pytest tests/integration/test_assisted_listing_promotion.py \
    tests/contract/test_assisted_listing_promotion_api.py -q     # 3 passed, blockers present
```
Live repros above were run with `TestClient(create_app())` against `9c8cdbd9`, and the
R2-B2 comparison additionally against a `03eb2783` worktree.

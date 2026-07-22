# ODP-INTAKE-PROMOTION-001 — Review Findings (Claude2)

Reviewed commit: `1649f4213ddb2e4a5583c98464dc571aa12e0b03`
Date: 2026-07-20
Verdict: **REQUEST CHANGES** (5 blockers)

The submitted suite (3 tests) passes and ruff is clean. It passes *with every
blocker below present*, so green tests are not evidence for this task's
acceptance criteria. Each finding was reproduced against the live app
(`TestClient(create_app())`), not inferred from reading code.

## B1 — Automatic candidate creation was not removed (AC1)

`POST /api/v1/operator/network-listings/intake/{id}/promote` still creates the
candidate site in a single call.
`modules/opsboard/application/network_listings.py:1663-1730` calls
`PromotionService.request_promotion(...)` and then immediately
`review_promotion(decision="APPROVE", ...)` inside the same request handler.

The "independent review" is synthesized, not performed: the proposer is read
from a stored field (`intake.get("submitter") or "operator-expansion-staff"`)
and the reviewer is the caller (`actor_name`). No second authenticated request
exists. `test_promotion_saga_segregation_of_duties` passes only because the
same actor happened to submit the intake — it demonstrates a string comparison,
not segregation of duties.

AC1 requires an **explicit request** plus an **independent review** before
execution. Requesting and approving in one handler is the automatic creation
the AC asks to remove.

## B2 — Fabricated SiteScore / recommendation written into real candidates

`modules/listing/application/promotion.py:243-258` hardcodes fixture data into
every candidate it creates. Reproduced — promoting a *real* intake resolved to
listing `L-2035` produced:

```json
{"id": "CS-1001", "listingId": "L-2035", "title": "信義松仁候選點",
 "address": "新北市新莊區興德路30號", "score": 82, "recommendation": "GO",
 "modelVersion": "SiteScore v2.3", "datasetSnapshotId": "FS-20260704-0600",
 "reviewId": "RV-1001"}
```

Every field after `listingId` is a literal. The title (信義松仁) contradicts the
address (新莊區) — the demo-fixture branch `if candidate_id == "CS-1001"` fires
for any first candidate because ids are minted as
`f"CS-{1000 + len(candidates) + 1}"`. `RV-1001` references a review that never
happened, and `score: 82 / GO` is a decision-grade recommendation that no model
produced. AC2 requires real SiteScore orchestration; no scoring runs at all —
`site_score_job_id` is a `uuid4` string with no job behind it.

Candidate ids are also collision-prone: `1000 + len(candidates) + 1` reuses an
id after any removal, and `save_candidate` then silently overwrites the
existing record.

## B3 — Promotion is not idempotent (AC2)

`promotion.py` checks for a duplicate candidate (lines 97-102) **before** the
idempotency lookup (lines 105-108), so the idempotency branch is unreachable
for any promotion that already completed. Reproduced: retrying the same intake
with a fresh `Idempotency-Key` returns

```
409 {"detail":"[DEPENDENCY_CONFLICT] DUPLICATE_CANDIDATE"}
```

instead of replaying the existing promotion. The only idempotency the tests
exercise is the transport-level `_idempotency_cache` keyed on the header
(`network_listings.py:1643`), which never reaches the saga. AC2's "idempotent
orchestration" is unmet. Swap the two checks: resolve an existing promotion for
the intake first, and only treat a foreign listing's candidate as a duplicate.

## B4 — The saga is unreachable from the v1 API contract (AC2, AC3)

`PromotionService` has exactly one caller — the legacy opsboard
`promote_intake`. The v1 endpoint
`POST /api/v1/promotion-decisions/{id}/actions/review`
(`apps/api/app/routes/listings.py:3047-3053`) only flips status via
`generic_mutate(..., "APPROVED")`. It never enters `CANDIDATE_CREATING`,
`CANDIDATE_CREATED`, `SCORE_QUEUED` or `COMPLETED`, never populates
`candidate_site_id` / `site_score_job_id`, and has no duplicate prevention.

The contract test asserts only `status == "APPROVED"` and the reviewer id, so it
does not cover the orchestration. The task's entire diff to `listings.py` is one
`Idempotency-Replayed` header line. AC3's authoritative success / partial
failure / compensation / retry / scoring-failure / reversal / audit outcomes
have no path on the contract surface: `promotion.py` re-raises after marking
`FAILED`/`SCORE_FAILED` with no compensation of the already-created candidate or
the already-mutated listing, and no reversal entry point exists.

## B5 — Canonical event contract edited to match the implementation

`docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1.yaml:322` changes
`CandidateCreatedV1.required` from `source_listing_id` to `listing_id`.

That contract is an approved source document for this task, and
`source_listing_id` is the name the intake schema uses for this exact
relationship — `expansion.candidate_sites.source_listing_id`
(`infra/db/migrations/assisted_listing_intake/001_baseline.sql:413`, documented
in `002_consistency.sql:173`). The emitter should publish `source_listing_id`;
renaming the contract to fit the payload breaks alignment with the schema and
with any consumer built against the approved event spec.

## AC4 status

Not met on the promoted path: the opsboard `/promote` endpoint enforces no
`If-Match` precondition at all, and segregation of duties reduces to B1.

## Reproduction

```bash
uv run pytest tests/integration/test_assisted_listing_promotion.py \
  tests/contract/test_assisted_listing_promotion_api.py -q   # 3 passed, blockers present
```

B2/B3 were reproduced by promoting a real intake through
`/api/v1/operator/network-listings/intake/{id}/promote` and retrying with a new
`Idempotency-Key`; outputs are quoted verbatim above.

## What would clear review

1. Split request and review into two separately authorized requests; no handler
   may approve what it just proposed.
2. Derive candidate fields from the listing and a real scoring call; delete the
   `CS-1001` / `信義松仁` / `82` / `RV-1001` literals and mint collision-free ids.
3. Move the idempotency lookup ahead of the duplicate-candidate check and add a
   test that retries with a *different* `Idempotency-Key`.
4. Wire the saga into the v1 review endpoint, with compensation on failure, and
   assert `candidate_site_id` / terminal state in the contract test.
5. Revert the events YAML and emit `source_listing_id`.

# ODP-INTAKE-PROMOTION-001 — Review Findings, Round 3 (Claude2)

Reviewed commit: `5e9c9d03` (PR #342 HEAD)
Date: 2026-07-22
Verdict: **REQUEST CHANGES** (3 blockers)

Round-1 findings: `review_findings_claude2.md` (`1649f421`).
Round-2 findings: `review_findings_claude2_round2.md` (`11f888d5`).
All findings below were reproduced against a live `TestClient(create_app())` at `5e9c9d03`,
or against PR #342 CI. The round is converging: every round-2 blocker was materially
addressed and most are verified fixed; the three items below are what still blocks.

## Fixed since round 2 — independently verified at runtime

- **R2-B1 FIXED (both halves).** With one candidate existing: a *different* intake
  resolved to the **same** listing → `409 DUPLICATE_CANDIDATE`; a different listing
  promotes normally end-to-end (endpoint no longer dead, no `AttributeError` leak —
  `ListingAdapterWrapper` now exposes `source_listing_id`). A re-request for a
  **COMPLETED** promotion with a **fresh** `Idempotency-Key` replays the same
  decision (`202`, same `promotion_decision_id`) instead of 409/422 — the idempotency
  lookup now precedes the duplicate scan (`promotion.py:96-114`).
- **R2-B2 regression FIXED.** `x-tenant-id: NOT-A-UUID` / `x-subject-id: not-a-uuid-either`
  → `403 TENANT_SCOPE_DENIED: UUID tenant and subject are required` again
  (`check_uuid` restored, `listings.py:46-55`; `require_actor` validates both). Residual
  laxity is note N1 below.
- **R2-B3 largely fixed.** Real `score_site` runs with real listing rent/area/frontage/
  confidence; two probe listings (rent 50k vs 180k) produced **different** scores
  (51 vs 12), real `model_version` `sitescore-baseline-v1`; `CandidateSiteDraft` now
  models `score/recommendation/model_version/dataset_snapshot_id/review_id` as proper
  fields (no `object.__setattr__`); `site_score_job_id` is registered in the job store,
  tenant-scoped, carrying `candidate_site_id`. Residuals are R3-B2/R3-B3 below.
- **AC1/AC4 hold live.** Request(A) → self-review(A) `403 SELF_REVIEW_DENIED`;
  independent reviewer(B) with manager role, `If-Match`, risk-ack → `200 COMPLETED`
  with `candidate_site_id` + `site_score_job_id`. REJECT path leaves the listing
  untouched (`watching`). Cross-tenant decision read → `403 TENANT_SCOPE_DENIED`.
- Canonical docs untouched: `git diff origin/dev...HEAD -- docs/events docs/api docs/design
  docs/data docs/operations` is empty (R1-B5 stays fixed). Ruff clean, `--check` clean.

## R3-B1 — Branch breaks the legacy operator promote contract suite; PR #342 `product` check is FAILURE

```
tests/contract/test_operator_assisted_listing_api.py::test_promote_intake_contract_test          FAILED
tests/contract/test_operator_assisted_listing_api.py::test_promote_persists_caller_risk_summary_in_audit  FAILED
```

Both tests **pass at `origin/dev` (8dc59377)** and **fail at `5e9c9d03`** (verified in a
clean dev worktree). They still assert the old one-call auto-approve contract
(`res_data["candidate"]["id"] == "CS-1001"`), which round 1 required removing — the
behaviour change is intended, but the suite asserting the old behaviour was left red.
PR #342's `product` CI check is FAILURE, consistent with this. Update those tests to the
two-step request→independent-review contract in this branch; a red product gate is not
deliverable and blocks the merge ladder regardless of review status.

## R3-B2 — Served-path scoring inputs still partially fabricated (continuation of R2-B3)

`d82a9ad9` claims "derived dynamic datasetSnapshotId and heat_zone_score", but on the
served v1 path neither derivation takes effect:

1. `V1ListingRepositoryAdapter.get_listing` still injects `"fitScore": 75`
   unconditionally (`listings.py:842` — domain `Listing` has no `fitScore` attribute, so
   the `getattr(..., 75)` is always 75). In `review_promotion` the wrapper takes the
   dict branch, so `fit_score = listing.get("heat_zone_score") or listing.get("fitScore")`
   → **every candidate scores with `heat_zone_score=75.0`**. Verified live: probe
   listings with rent 50k and 180k both recorded `score_site(heat_zone_score=75.0)`.
   The real `score_heatzones` fallback added at `promotion.py:289-295` is dead code on
   this path — it only fires when `fit_score is None`, which never happens.
   Fix: stop injecting `fitScore` in the adapter dict (let the fallback fire), or plumb a
   real heat-zone score.
2. The adapter drops `listing.snapshot_id` (the dict at `listings.py:819-843` has no
   snapshot key), so `ds_id` is always `None` and `dataset_snapshot_id` is synthesized
   as `FS-{listing_id}` (`promotion.py:297-302`) even when the listing carries a real
   snapshot reference (`SN-…`). Verified live: saved drafts show
   `ds_snapshot: FS-<listing-uuid>` while `listing.snapshot_id` is unreachable through
   the wrapper. Fix: include `snapshot_id` in the adapter dict.

## R3-B3 — Contract retry/reopen outcomes remain unreachable (AC3; continuation of R2-B3 consequence)

The canonical saga table (`ODAY_PLUS_ASSISTED_LISTING_INTAKE_STATE_CONTRACTS.md` §7)
requires `FAILED → CANDIDATE_CREATING: retry`, `SCORE_FAILED → SCORE_QUEUED: retry`
(`job.replay`), and on SCORE_FAILED "Candidate remains `SCORING_FAILED`; **no deletion**".
None of these outcomes is reachable at `5e9c9d03`:

1. **Terminal records dead-end the intake.** `request_promotion`'s intake-scoped
   idempotency scan (`promotion.py:97-100`) returns *any* existing promo regardless of
   status. Verified live: after a reviewer REJECT, a re-request with a fresh
   `Idempotency-Key` returns `202` with `status: REJECTED` — a self-contradictory
   "review requested" receipt — and there is no path to a new decision, ever. The
   contract's replay semantic is idempotency-key-scoped ("A lost HTTP response is
   recovered by replaying the same idempotency key…"); `REJECTED`/`FAILED` terminate the
   *decision*, not the intake. Replay terminal records only for the same key (or
   restrict the intake-scoped scan to non-terminal/COMPLETED statuses) so a fixed
   listing can be re-requested after rejection/failure.
2. **The registered score job is permanently frozen.** `save_promotion` writes the job
   only `if job_id not in jobs` (`listings.py:695`), so it stays `QUEUED`/`attempt 0`
   forever (never COMPLETED, never FAILED — even on the SCORE_FAILED path, since the job
   is created at the earlier SCORE_QUEUED save). And its checkpoint is `"SCORING"`
   (`listings.py:699`), which is not a legal `RetryRequest` checkpoint
   (`RETRIEVING|PARSING|MATCHING|CANDIDATE_CREATING|SCORE_QUEUED`), so `retryJob` can
   never pass validation + the checkpoint match: verified live — retry returns 422 on the
   enum, and would 409 `WORKFLOW_STATE_DENIED` (status QUEUED) regardless. Register the
   job with a legal checkpoint (`SCORE_QUEUED`) and update its status on subsequent
   promotion saves.
3. **SCORE_FAILED compensation contradicts the contract.** The `except` branch after
   SCORE_QUEUED (`promotion.py:533-583`) deletes the candidate and reverts the listing;
   the contract row says the candidate must remain (`SCORING_FAILED`), with recovery via
   `job.replay`. Candidate deletion is the compensation for `CANDIDATE_CREATING → FAILED`,
   not for score failure.

## Non-blocking notes

- **N1** `check_uuid`'s prefix allowlist (`^(L|AUD|IN|CS|HZ|JOB|RV|S|A|FORMAT|SN|FS|corr)-`)
  also applies to principals: tenant `S-evil` / subject `S-attacker` → `202` intake
  created (verified). Tenant *isolation* held in all probes (cross-tenant read 403), so
  this is validation laxity, not a boundary break — but `require_actor` should accept
  UUID + the 3 named fixture ids only; the entity-prefix regex belongs to entity-id
  fields, not tenant/subject.
- **N2** No outbox is ever wired (`app.state.outbox_repository` and
  `repository.outbox_repository` are both `None` in `create_app`), so all three emitted
  saga events are dropped at runtime, and no test covers emission. The contract table
  also names per-transition events (validating/approved/rejected/creation_started/
  sitescore.requested/failed) that are never emitted. Events infra exists
  (`shared/infrastructure/persistence/outbox.py`, ODP-INTAKE-EVENTS-001); composition
  is FLOW-011's root — wire it or get an explicit scope ruling recorded.
- **N3** The DUPLICATE_CANDIDATE 409 envelope carries `code: VERSION_CONFLICT`
  (message carries `DUPLICATE_CANDIDATE`); the code should be the authoritative field.
- **N4** `CandidateSiteDraft` now defaults to fixture provenance
  (`score=68`, `"WAIT"`, `"SiteScore v2.3"`, `"FS-20260704-0600"`, `models.py:66-70`) for
  every *other* constructor — make these `None`/required so absent provenance is absent,
  not invented.
- **N5** `reviewId` is minted (`RV-{hex}`) rather than referencing the real
  `promotion_decision_id`, which *is* the review record.
- Success receipt returns `200` where the contract text says `201 PromotionReceipt` (trivial).

## Verification transcript

```
uv run pytest tests/integration/test_assisted_listing_promotion.py \
    tests/contract/test_assisted_listing_promotion_api.py \
    tests/contract/test_assisted_listing_operations.py -q        # 52 passed
python3 -m ruff check modules/... apps/... tests                 # clean
git diff --check origin/dev...HEAD                               # clean
uv run pytest tests/contract tests/integration -q                # 2 failed (R3-B1), rest pass
# same two tests at origin/dev (8dc59377): 2 passed
```

Live probes (P1–P12) were run with `TestClient(create_app())` at `5e9c9d03`: happy path,
SoD, fresh-key replay after COMPLETED, duplicate-candidate 409, second-listing promotion,
`score_site` input recording, job-store/retry behaviour, garbage/prefix principals,
cross-tenant read, REJECT compensation, and re-request-after-REJECT.

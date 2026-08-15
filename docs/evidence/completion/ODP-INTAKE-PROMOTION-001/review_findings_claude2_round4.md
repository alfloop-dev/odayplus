# ODP-INTAKE-PROMOTION-001 — Review Findings, Round 4 (Claude2)

Reviewed commit: `6e41af41` (PR #342 HEAD)
Date: 2026-07-22
Verdict: **REQUEST CHANGES** (1 blocker)

Round-1: `review_findings_claude2.md` (`1649f421`). Round-2: `review_findings_claude2_round2.md`
(`11f888d5`). Round-3: `review_findings_claude2_round3.md` (`5453e5b2`).
All verifications below were reproduced against a live `TestClient(create_app())` at
`6e41af41` (probe transcripts P1–P8 + P5′), or against PR #342 CI. The round has
converged on the saga itself: **every round-3 blocker is materially fixed and
independently verified at runtime.** The single remaining blocker is the red
`product` CI check at HEAD.

## Fixed since round 3 — independently verified at runtime

- **R3-B1 (named tests) FIXED.** `test_promote_intake_contract_test` and
  `test_promote_persists_caller_risk_summary_in_audit` were rewritten to the two-step
  request→independent-review contract and pass at `6e41af41`; the full
  promotion/operator/operations focused suites pass (73/73), and the wide
  `tests/contract tests/integration` sweep is green (~717 passed, 0 failed).
- **R3-B2 FIXED (both halves).** The adapter no longer injects `fitScore: 75`
  (`listings.py:851` now passes `snapshot_id`); the real `score_heatzones` fallback
  fires on the served path. Verified live: `score_site` received
  `heat_zone_score=30.5` (H3-derived), not 75.0; probe listings with rent 50k vs 180k
  produced **different** scores (22 vs 0). `listing.snapshot_id` now reaches the saga:
  candidate `dataset_snapshot_id` is `FS-SN-P1` for a listing carrying `SN-P1`
  (no more `FS-{listing-uuid}` synthesis when a real snapshot exists).
- **R3-B3.1 FIXED.** The intake-scoped idempotency scan excludes terminal
  `REJECTED`/`FAILED` (`promotion.py:96-105`). Verified live: reviewer REJECT →
  re-request with a fresh key returns `202` with a **new** decision in
  `PENDING_REVIEW`, and the second attempt completes end-to-end. Fresh-key
  re-request after `COMPLETED` still replays the same decision (no duplicate saga).
- **R3-B3.2 FIXED.** `save_promotion` registers the score job at checkpoint
  `SCORE_QUEUED` (a legal `RetryRequest` checkpoint) and updates its status on
  subsequent saves (`listings.py:692-716`): `COMPLETED` on the happy path
  (asserted live and in the updated contract test), `FAILED` on scoring failure.
  Verified live: with a `FAILED @ SCORE_QUEUED` job, `retryJob` now returns
  `202 {status: QUEUED, attempt: 1}` — the retry outcome is reachable for the
  first time in this review series.
- **R3-B3.3 FIXED.** The `except` branch after `SCORE_QUEUED`
  (`promotion.py:538-566`) no longer deletes the candidate: verified live via
  fault injection at the SCORE_QUEUED save — decision → `SCORE_FAILED`, candidate
  **retained** with `site_status: SCORING_FAILED`, job `FAILED @ SCORE_QUEUED`,
  and a subsequent re-request replays the live `SCORE_FAILED` decision
  (recovery via `job.replay`, per the contract row).
- **AC1/AC4 hold.** Self-review → `403 SELF_REVIEW_DENIED`; independent
  reviewer with `If-Match` + risk-ack → `200 COMPLETED` with
  `candidate_site_id` + `site_score_job_id`. Duplicate-candidate 409 intact.
  Canonical docs untouched (`git diff origin/dev...HEAD -- docs/events docs/api
  docs/design docs/data docs/operations` empty). Ruff clean, `--check` clean.

## R4-B1 — PR #342 `product` check is FAILURE at `6e41af41`; red gate is not deliverable

Round 3 stated the requirement: a red product gate blocks the merge ladder
regardless of review status. The gate is still red, from two causes in
`tests/security` (a suite the round-3 fix did not cover):

1. **Branch-caused (must fix):**
   `tests/security/test_assisted_listing_intake_authorization_matrix.py::test_self_review_prohibition`
   fails `assert 200 == 403`. It passes at `origin/dev` (`8dc59377`, green CI) and
   fails on this branch — it still encodes the **old one-call promote=approve**
   contract (submitter calling operator `/promote` on their own intake expected
   `403 SELF_REVIEW_DENIED`). Under the two-step contract this branch introduces,
   `/promote` is a *request* (202/200 PENDING_REVIEW) and SoD is enforced at the
   review step — which I verified live (`403 SELF_REVIEW_DENIED` on self-review).
   This is the same stale-contract class as R3-B1, one suite over. Fix exactly as
   `6e41af41` already did for the operator contract tests: assert the request
   succeeds, then assert `403 SELF_REVIEW_DENIED` on the submitter's own
   `/promotion-decisions/{id}/actions/review` call, and `200 COMPLETED` for an
   independent reviewer.
2. **Environmental drift (green-fix required, not a code defect of this branch):**
   `tests/security/test_supply_chain_security_gate.py::test_npm_audit_passes` fails
   on a **newly published** advisory: `sharp <0.35.0` (CVE-2026-33327/-33328/
   -35590/-35591, GHSA-f88m-g3jw-g9cj); the branch does not touch
   `package-lock.json` (diff vs dev is empty) and dev's last green CI predates the
   advisory — dev's next CI run will fail identically. Per fleet precedent
   (ODP-INTAKE-SNAPSHOT-001 brace-expansion; green-fix ≠ scope creep), bump
   `sharp` to `^0.35.0` in this branch (lockfile-only, isolated commit) or get the
   fix landed in a separate lane before merge — either way PR #342 must be green.

## Non-blocking notes (carried from round 3, unchanged)

- N1 principal-id prefix laxity (`S-…` accepted as tenant/subject) — unchanged.
- N2 outbox never wired in `create_app`; saga events dropped at runtime
  (composition is FLOW-011's root; needs an explicit scope ruling or wiring).
- N3 DUPLICATE_CANDIDATE envelope still carries `code: VERSION_CONFLICT`.
- N4 `CandidateSiteDraft` constructor defaults still carry fixture provenance
  for other callers (`models.py:66-70`).
- N5 `reviewId` minted rather than referencing `promotion_decision_id`.
- New (informational): a `score_site` exception raised during candidate
  derivation lands in the `CANDIDATE_CREATING → FAILED` row (candidate deleted,
  re-request recovers — verified live); `SCORE_FAILED` covers failures after the
  queue transition. Both recovery paths are reachable and contract-consistent,
  so this is a scoping note, not a blocker.

## Verification transcript

```
uv run pytest tests/integration/test_assisted_listing_promotion.py \
    tests/contract/test_assisted_listing_promotion_api.py \
    tests/contract/test_assisted_listing_operations.py \
    tests/contract/test_operator_assisted_listing_api.py -q     # 73 passed
uv run pytest tests/contract tests/integration -q               # all passed (some skips)
uv run pytest tests/security -q                                 # 2 FAILED (R4-B1.1, R4-B1.2)
python3 -m ruff check modules/... apps/... tests                # clean
git diff --check origin/dev...HEAD                              # clean
git diff origin/dev...HEAD -- package-lock.json                 # empty (B1.2 is env drift)
gh pr checks 342: product FAIL, orchestrator PASS, product-e2e-gate PASS
dev CI @8dc59377: success (self-review test passes there; branch-caused)
```

Live probes at `6e41af41`: P1 happy path (202→review→COMPLETED, job
COMPLETED@SCORE_QUEUED, FS-SN-P1), P2 divergent scores across rents + real
heat-zone score, P3 SoD 403, P4 REJECT→fresh-key re-request→new decision→
COMPLETED, P5′ injected SCORE_QUEUED-save failure → SCORE_FAILED + retained
SCORING_FAILED candidate + FAILED job + retryJob 202 QUEUED, P6 fresh-key
replay after COMPLETED, P7 duplicate-candidate 409, P8 re-request after
SCORE_FAILED replays live decision.

# ODP-INTAKE-PROMOTION-001 — Review Findings, Round 5 (Claude2)

Reviewed commit: `364270a8` (PR #342 HEAD; `a3eac064` + merge of origin/dev `b63cace8`)
Date: 2026-07-22
Verdict: **APPROVED**

Round-1: `review_findings_claude2.md` (`1649f421`). Round-2: `review_findings_claude2_round2.md`
(`11f888d5`). Round-3: `review_findings_claude2_round3.md` (`5453e5b2`).
Round-4: `review_findings_claude2_round4.md` (`6857d323`).

Round 4 had converged on the saga (every code blocker independently verified fixed at
runtime) with one remaining blocker: the red `product` CI check (R4-B1). Both R4-B1
causes are now fixed and verified; no saga regression came in with the dev merge.

## R4-B1 CLEARED — verified in CI and locally

1. **R4-B1.1 (stale self-review test) FIXED exactly as prescribed.** `a3eac064`
   rewrote `tests/security/test_assisted_listing_intake_authorization_matrix.py::
   test_self_review_prohibition` to the two-step contract: submitter `/promote` →
   `200 PENDING_REVIEW`, submitter's own `/promotion-decisions/{id}/actions/review` →
   `403 SELF_REVIEW_DENIED`, independent reviewer → `200 COMPLETED`. The full
   `tests/security` suite passes locally at `364270a8` (127 passed, 0 failed).
2. **R4-B1.2 (sharp advisory) FIXED.** `package.json` adds the `next → sharp ^0.35.0`
   override (lockfile regenerated); dev independently landed the same fix
   (`e6957111`), and the merge preserved it — `git diff origin/dev...HEAD --
   package.json package-lock.json` is now **empty**. `test_npm_audit_passes` passes
   locally. The residual `ODP-PGAP-SUPPLY-001/sbom.json` diff vs dev is regeneration
   metadata only (timestamp / git-sha / content digest); the supply-chain gate
   accepts it.
3. **PR #342 CI is GREEN at `364270a8`:** `orchestrator` pass, `product` pass
   (8m20s — previously the failing check), `product-e2e-gate` pass. Only
   `task-review-gate` was pending, on this review.

## No regression from the dev merge — verified at runtime

The only change to saga-adjacent files since the round-4 verified commit
(`6e41af41`) is dev-side ODP-INTAKE-UX-ASSIGN-001 additions in
`apps/api/app/routes/listings.py` (intake-detail assignment/SLA read fields and a
transfer-handler update) — none of the promotion paths. Round-4 runtime
verification therefore carries forward; re-confirmed with live probes
(`TestClient(create_app())`) at `364270a8`:

- submit → `/promote` returns `200 PENDING_REVIEW` (no one-call auto-approve);
- self-review → `403 SELF_REVIEW_DENIED`;
- independent reviewer with `If-Match` + risk-ack → `200 COMPLETED` with real
  (non-fixture, UUID) `candidate_site_id` and `site_score_job_id`;
- fresh-key re-request after COMPLETED replays the same decision (no duplicate saga).

Focused suites at `364270a8`: promotion integration + promotion/operations/operator
contract suites 73/73 pass; ruff clean on task files; `git diff --check
origin/dev...HEAD` clean; canonical docs (`docs/events docs/api docs/design
docs/data docs/operations`) untouched vs dev.

## Acceptance criteria — all met (verified across rounds 3–5)

- **AC1** (no automatic candidate creation; explicit request + independent review):
  two-step contract live-verified; SoD enforced at the review step.
- **AC2** (idempotent orchestration + duplicate-candidate prevention): idempotency-key
  replay, intake-scoped non-terminal replay, and DUPLICATE_CANDIDATE 409 verified
  (rounds 3–4); replay re-confirmed this round.
- **AC3** (authoritative outcomes incl. compensation/retry/scoring-failure/reversal):
  verified live in round 4 (P4, P5', P8 probes — REJECT re-request, SCORE_FAILED with
  candidate retained, retryJob 202); saga code unchanged since.
- **AC4** (non-optimistic high-impact actions, If-Match + segregation of duties):
  verified rounds 3–5.

## Non-blocking notes (carried; one new)

- N1–N5 from rounds 3–4 unchanged (principal-id prefix laxity; outbox never wired in
  `create_app` — composition is FLOW-011's scope; DUPLICATE_CANDIDATE envelope
  `code: VERSION_CONFLICT`; `CandidateSiteDraft` fixture defaults for other callers;
  `reviewId` minted).
- **N6 (new, pre-existing on dev — not branch-caused):** promoting an intake whose
  `matchResult` is `None` (e.g. retrieval failed or not yet matched) crashes
  `500 AttributeError` at `modules/opsboard/application/network_listings.py:1782`
  (`intake["matchResult"].get("targetListingId")`). The intended guard ("intake must
  be resolved to a listing before promotion" → 409) sits one line below but is
  unreachable in the `None` case. The identical line exists verbatim in dev's
  `promote_intake`; this branch did not introduce it. One-line fix for a follow-up:
  `(intake.get("matchResult") or {}).get("targetListingId")`.

## Verification transcript

```
gh pr checks 342 @364270a8: orchestrator pass, product pass, product-e2e-gate pass
uv run pytest tests/integration/test_assisted_listing_promotion.py \
    tests/contract/test_assisted_listing_promotion_api.py \
    tests/contract/test_assisted_listing_operations.py \
    tests/contract/test_operator_assisted_listing_api.py -q     # 73 passed
uv run pytest tests/security -q                                 # 127 passed
python3 -m ruff check modules/... apps/... tests                # clean
git diff --check origin/dev...HEAD                              # clean
git diff origin/dev...HEAD -- package.json package-lock.json    # empty
git diff origin/dev...HEAD -- docs/events docs/api docs/design docs/data docs/operations  # empty
git diff 6e41af41..364270a8 -- modules/listing modules/opsboard \
    apps/api/app/routes/listings.py apps/api/app/routes/operator_modules/network_listings.py
                                                                # dev-side UX-ASSIGN only
Live probes at 364270a8: two-step happy path, SoD 403, independent review COMPLETED
with UUID candidate/job ids, fresh-key replay after COMPLETED.
```

Owner may proceed to finalize: after this approval re-stamps `task-review-gate` on
the current PR head and PR #342 merges into dev, run `done` from the task worktree.

# ODP-INTAKE-UX-PROMOTION-001 — Completion Evidence

- Task: Implement reviewed Candidate Site promotion and SiteScore job UI
- Owner: Claude · Reviewer: Codex2 · Target branch: `dev`
- Baseline: `origin/dev` @ `48bd7913` (includes ODP-INTAKE-PROMOTION-001 backend saga, PR #342)
- Approved design: Claude Design Package 10 (`prototype sha256
  cc4e6ae97462bc99b1c2353c792cb3bec40d51a6c5efcfde165e5f47105e661d`) together with
  `ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE_REVIEW_003.md`
  (`APPROVED_WITH_CONDITIONS`) — implemented against the System Design bundle where
  contracts differ (Review 003 §6).

## Deliverables

| Artifact | Purpose |
|---|---|
| `apps/web/features/operator/network/intake/PromotionReviewPanel.tsx` | UX-SCR-EXP-003F promotion section: explicit request, second-actor review, full saga rendering, commit-gated IDs, lost-response recovery, durable receipt |
| `apps/web/features/operator/network/intake/SiteScoreJobStatus.tsx` | SiteScore job slice: all 7 `JobReceipt` states, commit-gated job ID, authorized same-key replay from `SCORE_QUEUED` checkpoint |
| `apps/web/features/operator/network/intake/__tests__/PromotionReviewPanel.test.tsx` | 27 tests encoding the four acceptance criteria + control presence/absence per state |
| `packages/openapi-client/src/index.ts` | Generated-client methods for the four v1 saga routes (`requestCandidatePromotion`, `reviewPromotionDecision`, `getPromotionDecision`, `retryJob`) with mandatory `If-Match`/`Idempotency-Key` and `Idempotency-Replayed` capture |
| `apps/web/features/operator/network/intake/intakeClient.ts` | Guarded `intakeApi.requestPromotion` / `reviewPromotion` / `getPromotionDecision` / `retryScoreJob` wrappers + promotion error vocabulary (403/404/409/422/428) |
| `apps/web/features/operator/network/intake/AssistedIntakeSection.tsx` | **Production mounting** — the live operator container wires all four handlers, computes the gate snapshot SHA-256, holds server receipts, and renders the panel on the READY branch of the real detail dialog |
| `apps/web/features/operator/network/intake/IntakeDetailDialog.tsx` | `promotionSection` slot on the live detail (after human decision, before durable receipts) |
| `apps/web/features/operator/network/intake/IntakeProcessingDetail.tsx` | Promotion tab (`tab-promotion`) mounting the panel on the durable processing-detail surface |
| `apps/web/features/operator/network/intake/__tests__/PromotionSagaIntegration.test.tsx` | 3 integration tests mounting the REAL container against a stubbed network boundary and asserting the actual wire traffic of all four calls |

## Production integration (review round 2)

The runtime path is `ExpansionWorkspace → AssistedIntakeSection →
IntakeDetailDialog(promotionSection) → PromotionReviewPanel → SiteScoreJobStatus`.
`AssistedIntakeSection` owns the API wiring: every handler goes through
`intakeApi` → the typed `OdpApiClient` (no raw fetch), receipts are only ever set
from server responses (non-optimistic), `Idempotency-Replayed` is surfaced from
the response header, and the `gate_snapshot_sha256` is computed with WebCrypto
SHA-256 over the canonical server-provided gate inputs
(`intakeId`/`version`/`stage`/`policy`/`matchOutcome`) so the request provably
binds to the gate evaluation the operator saw. `IntakeProcessingDetail`
additionally mounts the panel as its promotion tab for the durable
processing-detail surface. For a `SCORE_FAILED` decision with no prior retry
receipt, the container bootstraps the replay view strictly from the
authoritative promotion receipt (`site_score_job_id`, `correlation_id`; status
`FAILED`/checkpoint `SCORE_QUEUED` are what `SCORE_FAILED` means by contract;
initial attempt/version use the server's job-creation values and a stale
version surfaces as the 409 conflict flow, never a silent overwrite).

The components are typed exclusively against the generated
`@oday-plus/openapi-client` v1 contract (`PromotionDecisionReceipt`, `PromotionStatus`,
`JobReceipt`, `RetryRequest` semantics). Wire calls map to:

- `POST /api/v1/intakes/{intake_id}/promotion-requests` (`PromotionRequestInput`:
  `target_format_code`, `reason`, `gate_snapshot_sha256`, `risk_acknowledged`,
  `Idempotency-Key`, `If-Match: W/"<intake version>"`)
- `POST /api/v1/promotion-decisions/{id}/actions/review` (`PromotionReviewInput`:
  `APPROVE|REJECT`, reason, risk ack, `If-Match: W/"<promotion version>"`)
- `GET  /api/v1/promotion-decisions/{id}` (lost-response decision lookup)
- `POST /api/v1/jobs/{job_id}/retry` (`ScoreReplayInput`: checkpoint `SCORE_QUEUED`,
  stable per-(job,attempt) `Idempotency-Key`, `If-Match: W/"<job version>"`)

## Acceptance Criteria → Evidence

1. **Render every approved promotion, decision, and job state without compressing the
   saga into one loading state.**
   All 11 `PromotionStatus` values have distinct labels/badges and real stepper nodes
   (`promotionStagePath` renders the actual branch taken: REJECTED / FAILED /
   SCORE_FAILED, mirroring state contracts §7). All 7 `JobStatus` values are distinct.
   Tests: "renders a distinct badge and stepper node for every canonical promotion
   state", "shows the full happy path…", "renders all seven canonical job states
   distinctly".

2. **Require explicit request, independent second-actor approval, reason, risk
   acknowledgement, If-Match, idempotency, and non-optimistic execution.**
   Request and review both hard-require reason (≥3 chars) + risk ack before the
   control unlocks; every mutation carries `If-Match` and a stable `Idempotency-Key`;
   `proposer === reviewer` removes the approve/reject controls and shows
   `SELF_REVIEW_DENIED` (authorization matrix: staff propose, manager approve,
   manager-proposer needs another manager); busy state locks submission and the UI
   never flips saga state locally — only server receipts drive it.
   Tests: "keeps submit locked…", "submits target format, gate snapshot, If-Match and
   an idempotency key — without optimistic state", "locks the submit control while
   busy…", "blocks self-review…", "lets a different manager approve…".

3. **Display Candidate and SiteScore IDs only after authoritative commit; SCORE_FAILED
   retains the Candidate and offers authorized replay with the same idempotency key.**
   `committedCandidateId`/`committedScoreJobId` gate display on saga status
   (CANDIDATE_CREATED+ / SCORE_QUEUED+) — even a receipt that leaked an ID early is
   not displayed pre-commit. SCORE_FAILED keeps the candidate ID visible with an
   explicit retention note; replay is role-gated (`canReplayScore`) and reuses one key
   per (job, attempt), rotating only when the server bumps `attempt`.
   Tests: "shows pending placeholders…", "refuses to display IDs the server leaked
   before their commit point", "reveals the candidate ID at CANDIDATE_CREATED…",
   "keeps the candidate visible on SCORE_FAILED and offers authorized same-key
   replay", "hides the replay control from unauthorized users", "rotates the replay
   idempotency key only when the server bumps the attempt".

4. **Recover lost responses without duplicate Candidate creation and expose durable
   promotion/job receipts plus audit evidence.**
   Transport-lost recovery offers same-`Idempotency-Key` retry (asserted identical
   across calls) and no-resend decision lookup; `Idempotency-Replayed` responses are
   labeled as recovered originals ("未建立第二筆 Candidate"); the durable receipt block
   exposes `promotion_decision_id`, `decision_type`/status, versions, reviewer,
   `audit_event_id`, `correlation_id`; 409 preserves operator input with a refresh
   affordance; 428 surfaces PRECONDITION_REQUIRED.
   Tests: "offers same-key retry and decision lookup after a lost response", "labels
   an idempotent replayed response as recovered…", "renders the durable promotion
   receipt with audit and correlation evidence", "preserves operator input on 409
   conflict…", "surfaces 428 PRECONDITION_REQUIRED explicitly".

## Binding VDC Conditions (Review 003) — this task's slice

- **VDC-001 (control presence/absence testing)**: the tests assert control PRESENCE
  AND ABSENCE per state — request form absent outside READY/for unauthorized roles,
  review controls absent outside PENDING_REVIEW/for self-review/for unauthorized
  roles, replay absent without authorization — not just internal defaults.
- **VDC-002 (no page-level overflow / DESKTOP_REQUIRED)**: both components are fluid
  (no fixed pixel widths); promotion review is explicitly marked `DESKTOP_REQUIRED`
  in-surface per Review 003 ("complex compare, identity graph, promotion review").
- **VDC-003 (WCAG 2.2 AA)**: semantic `<section>` landmarks with `aria-label`,
  `aria-live="polite"` status summaries, `role="alert"`/`role="status"` on error and
  retention notices, `label htmlFor`/`id` bindings on every input, and text markers
  (✓/✕/→/⊘ + state codes) so no state is colour-only.
- **VDC-004 (URL-restorable state)**: inbox/detail URL state is owned by
  ODP-INTAKE-UX-FND-001 `urlState.ts`; these panels are section content within the
  durable `#intake/<id>` detail and hold no navigation state of their own.
- **VDC-005 (discipline review outcomes)**: recorded at release gate by
  ODP-INTAKE-UX-001/QA; out of this task's artifact scope.

## Verification (reproduce from repo root)

```bash
npm ci
npm run typecheck --workspace=@oday-plus/web              # clean (tsc --noEmit)
npm run typecheck --workspace=@oday-plus/openapi-client   # clean (tsc --noEmit)
npm test --workspace=@oday-plus/web -- PromotionReviewPanel      # 27/27 passed
npm test --workspace=@oday-plus/web -- PromotionSagaIntegration  # 3/3 passed
npm test --workspace=@oday-plus/web            # full web suite 83/83 passed
git diff --check origin/dev...HEAD             # clean
```

Integration tests assert the actual wire shape leaving the generated client:
`If-Match: W/"<intake|promotion|job version>"`, stable `Idempotency-Key` reuse
across a lost-response retry (same key on both attempts), `Idempotency-Replayed`
labeling, `gate_snapshot_sha256` format, commit-gated ID display, and that the
decision lookup path never resends the review write.

Transcripts: `verification-transcript.txt` (typecheck + focused + integration +
full suite), `test-run-verbose.txt` (per-test listing, 30 passed across the
promotion slice).

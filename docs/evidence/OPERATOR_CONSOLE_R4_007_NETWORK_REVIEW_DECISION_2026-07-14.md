# ODP-OC-R4-007 — Network Review Decision & Atomic Governance Sync

Date: 2026-07-14
Owner: Claude · Reviewer: Codex2
Status: implementation + review evidence

## Canonical Design Source

- Package: **package 6** (canonical latest), archived at
  `docs_archive/00_source_zips/operator_console/r4-20260707-package-6/`.
- ZIP SHA-256 (verified): `db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76`
  (matches the task-brief verification hash). `unzip -t` reports no errors.
- Interactive HTML: `extracted/Oday Plus Operator Console.dc.html`.
- Relevant `data-screen-label` values driven from the archived HTML:
  - **`Network 選址審核`** → the review queue + detail panel (`ReviewPanel.tsx`).
  - **`Dialog Review Decision`** → the decision confirmation dialog
    (`ReviewDecisionDialog.tsx`).

## What Was Built

| Layer | File | Owns |
| --- | --- | --- |
| Domain service | `modules/opsboard/application/network_reviews.py` | `NetworkReviewService`: seeded queue + 5-record atomic decision (`decide_review`) |
| API route | `apps/api/app/routes/operator_modules/network_reviews.py` | `GET /operator/network-reviews`, `POST /reset`, `POST /{id}/decide` |
| Composition | `apps/api/app/routes/operator.py` (+ `operator_modules/__init__.py`) | Registers the review sub-router; decide guarded by `sitescore` `APPROVE` |
| Web panel | `apps/web/features/operator/network/ReviewPanel.tsx` | Queue + review detail + GO/WAIT/退回/駁回 actions |
| Web dialog | `apps/web/features/operator/network/ReviewDecisionDialog.tsx` | "Dialog Review Decision": reason / conditions / required-data / override-ack |
| Web types | `apps/web/features/operator/network/networkReviewTypes.ts` | Snapshot + decision types + mapping |
| Wiring | `apps/web/features/operator/NetworkFindAreasWorkspace.tsx` | Fetches `/network-reviews`, posts decisions as the Site Reviewer, reloads |
| Contract tests | `tests/contract/test_operator_network_review_api.py` | 11 tests |
| E2E | `tests/e2e/operator-network-review.spec.ts` | 7 tests (UI dialog + API atomic-sync) |

## Acceptance Mapping

1. **A failed transaction leaves all five records unchanged; idempotent replay
   creates no duplicates.** — Validation runs before any mutation; the commit is
   wrapped in a rollback guard (`NetworkReviewService._Transaction`). A WAIT
   decision without conditions returns 422 and leaves Candidate / Review /
   Approval / Decision / Audit untouched (`decisions == []`, `auditEvents == []`).
   Replays on the same `Idempotency-Key` return the cached result with
   `idempotentReplay: true` and add no rows.
   Proof: `test_failed_transaction_leaves_all_records_unchanged`,
   `test_idempotent_replay_creates_no_duplicate_records`, e2e
   *Failed transaction…* and *Idempotent replay…*.
2. **Authorized reviewer reaches the review from Network or Govern without
   role-navigation dead ends.** — The Site Reviewer identity reads both
   `/operator/network-reviews` and `/operator/governance/snapshot` (200/200), and
   the Network workspace review tab is reachable at tab index 5.
   Proof: `test_reviewer_reaches_review_from_network_and_govern`.
3. **Desktop and constrained-width screenshots compared with the archived
   interactive HTML.** — See *Visual Parity* below and the PNGs under
   `docs/evidence/r4-007/`.
4. **Expansion role can prepare/submit but cannot decide.** — The decide
   endpoint requires `sitescore` `APPROVE` (granted to `SITE_REVIEWER` /
   `EXECUTIVE`, not `EXPANSION_USER`), so an Expansion caller fails closed with
   403; the service adds a defense-in-depth allowlist. Reads stay open to
   Expansion.
   Proof: `test_expansion_role_may_submit_read_but_not_decide`, e2e
   *Expansion may read but not decide…*.
5. **GO → Approved, WAIT → On Hold, Return → Need Data, Reject → Rejected.** —
   Encoded in `DECISION_FINAL_LABEL` and surfaced in the snapshot
   `decisionMapping`.
   Proof: `test_go_decision_syncs_five_records_and_survives_reload`,
   `test_decision_mapping_covers_wait_return_reject`.
6. **Implementation and review evidence identify canonical package 6 and the
   relevant data-screen-label values.** — This document (§Canonical Design
   Source) and the `data-screen-label` attributes shipped on the two surfaces,
   asserted by the e2e (`toHaveAttribute("data-screen-label", …)`).

## Atomic Governance Sync (the five records)

One `decide_review` call, in order, inside a rollback-guarded transaction:

1. **Candidate** — status → mapped status; for `RETURN` the required-data list is
   written to the Candidate missing-data list.
2. **Review** — status/label → mapped; decision summary + history entry recorded.
3. **Approval** — governance approval envelope status → `approved` / `on_hold` /
   `need_data` / `rejected`, with `decidedAt` / `decidedBy`.
4. **Decision** — a new Decision Log row (`systemRecommendation`, `finalDecision`,
   reason, conditions, requiredData, override, model/snapshot, `approvalId`).
5. **Audit** — a new `review.decision` audit event with correlation id.

The `records` block of the response returns the five ids
(`candidateId`, `reviewId`, `approvalId`, `decisionId`, `auditId`) as the
sync receipt.

## Reason / Override Rules (parity with the dialog)

- Every decision requires a reason (≥ 10 chars) written to the Decision Log.
- `WAIT` requires pass conditions (condition-met → re-scoreable to GO).
- `RETURN` requires the missing-data list (synced to the Candidate).
- A decision that overrides the SiteScore recommendation (verb ≠ the model's
  natural verb; `RETURN` never overrides) requires an explicit risk
  acknowledgement.

## Visual Parity (package 6 interactive HTML vs. shipped UI)

Screenshots captured against the live API-bound UI (web dev server proxying
`/api/v1` → FastAPI at :8099):

| Evidence | Viewport | File |
| --- | --- | --- |
| Review panel | Desktop 1440×960 | `docs/evidence/r4-007/review-panel-desktop-1440.png` |
| Decision dialog (GO) | Desktop 1440×960 | `docs/evidence/r4-007/review-decision-dialog-desktop-1440.png` |
| Review panel | Constrained 768×1024 | `docs/evidence/r4-007/review-panel-constrained-768.png` |
| Decision dialog (WAIT) | Constrained 768×1024 | `docs/evidence/r4-007/review-decision-dialog-constrained-768.png` |

`Network 選址審核` field-by-field parity vs. the extracted HTML:

| HTML element | Shipped field | Match |
| --- | --- | --- |
| Queue card `rq.id` / `rq.rec` / `rq.risk` / `rq.stL` / `rq.name` / `rq.reqBy` / `rq.submitted` / `rq.due` | `review-card-*` id, SiteScore chip, risk, status badge, title, requester, 送審, 期限 | ✓ |
| Detail meta grid 申請人 / 審核角色 / 送審時間 / 期限 | `reviewMetaGrid` | ✓ |
| Detail metric grid 回本期 / M12 P50 / 租金合理性 / 自家稀釋 | `reviewMetricGrid` | ✓ |
| Facts 來源物件 / 現勘 / 仲介聯絡 / 候選備註 / 模型／快照 / 比較結果 / Candidate 狀態 | `reviewFacts` | ✓ |
| Event chips `rvEvChips` | `reviewChips` | ✓ |
| `CANDIDATE 歷程` | `reviewHistory` | ✓ |
| Pending sync note + 核准 GO / 核准 WAIT / 退回修改 / 駁回 | `reviewSyncNote` + `reviewDecisionBar` | ✓ |

`Dialog Review Decision` parity: title/sub, override warning banner, reason
textarea (required), WAIT conditions textarea, RETURN required-data input,
override risk-acknowledgement toggle, error line, sync note, 取消 / confirm
buttons — all present. The constrained viewport collapses the 390px queue +
fluid detail grid to a single column (`@media (max-width: 900px)`), and the
dialog stays centered at `max-width: 94vw`.

Layout numbers reused verbatim from the HTML: queue column `390px`, dialog
width `520px` / `max-width: 94vw`, and the same colour tokens
(GO `#1E7F4F`, WAIT border `#E4C88A`, reject `#B3261E`, override banner
`#FBF1DD`/`#EDDDB5`).

## Verification Commands

```
# Package integrity (task brief)
sha256sum "docs_archive/00_source_zips/operator_console/r4-20260707-package-6/Oday Plus 營運管理後台 (6).zip"
#  → db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76
unzip -t "docs_archive/00_source_zips/operator_console/r4-20260707-package-6/Oday Plus 營運管理後台 (6).zip"  # no errors

# Backend
uv run pytest tests/contract -k review        # 11 passed
uv run pytest tests/contract/test_operator_api.py \
              tests/contract/test_operator_network_scoring_api.py \
              tests/contract/test_operator_governance_api.py   # 36 passed (no regression)
uv run ruff check modules/opsboard/application/network_reviews.py \
                  apps/api/app/routes/operator_modules/network_reviews.py \
                  apps/api/app/routes/operator.py \
                  tests/contract/test_operator_network_review_api.py   # clean

# Frontend
apps/web: tsc --noEmit                         # clean
apps/web: next lint features/operator          # clean (pre-existing GrowthWorkspace warning only)

# E2E (Playwright, web + API webServers)
npx playwright test tests/e2e/operator-network-review.spec.ts   # 7 passed
npx playwright test tests/e2e/e2e-operator-console.spec.ts      # 4 passed (FE-04 updated to the dialog flow)
npx playwright test tests/e2e/operator-network-scoring.spec.ts  # 4 passed (no regression)
```

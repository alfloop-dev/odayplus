---
task_id: ODP-INTAKE-UX-001
title: Assisted Listing Intake R7 UI Integration Completion Evidence
status: pending-review
owner: Claude2
reviewer: Codex2
integration_verified_commit: 7a292c9c06cf80fa03130d66b7aa8d7aa6ac3b31
updated_at: 2026-07-22
---

# Assisted Listing Intake R7 UI Integration Completion Evidence

## 1. Scope

`ODP-INTAKE-UX-001` is the R7 integration capstone for the Assisted Listing
Intake v1 UI (approved system design `ODP-SD-INTAKE-001`, Claude Design
Package 10). It integrates the eight independently reviewed child tasks on
`dev`, re-verifies the six canonical flows against the real generated API
client at the exact integration commit, and closes binding visual design
conditions `VDC-001` through `VDC-005`.

This evidence does not claim production rollout approval; rollout remains
separately gated by `ODP-INTAKE-RELEASE-001`.

## 2. Exact Runtime Target

| Item | Value |
|---|---|
| Integration-verified commit (`origin/dev` tip) | `7a292c9c06cf80fa03130d66b7aa8d7aa6ac3b31` |
| Playwright | `1.61.1` |
| Browser | Chromium for Testing (Playwright-managed) |
| API | real FastAPI routes via `python3 -m uvicorn apps.api.oday_api.main:app` (no route stubs) |
| Web | Next.js runtime via `npm run dev --workspace=@oday-plus/web` |
| Node / npm | `v22.23.1` / `10.9.8` |

Note: the QA baseline commit `e034aa42625ac1745ea69a96786941c2a77e7965`
(runtime-tested by `ODP-INTAKE-UX-QA-001`) has a git tree identical to the
integration commit `7a292c9c` (`git rev-parse <sha>^{tree}` matches), so the
QA runtime conclusions transfer to the integration commit without content
drift. All commands below were nevertheless re-executed at `7a292c9c`.

## 3. Child Task Integration Matrix

All eight child tasks are `done`, independently reviewed, and their approved
deliverables are present on `dev` at the integration commit.

| Child task | Owner / Reviewer | Landed in dev via | Anchor |
|---|---|---|---|
| ODP-INTAKE-UX-FND-001 | Antigravity7 / Antigravity3 | PR #345 (merged as prerequisite at `9b9c198a`) | `f486b692` ancestor of dev |
| ODP-INTAKE-UX-INBOX-001 | Codex / Claude | PR #347 | merge `e9503854` |
| ODP-INTAKE-UX-DETAIL-001 | Antigravity3 / Antigravity5 | PR #345 (merged as prerequisite at `9b9c198a`) | impl commit `1ee3857a` in dev history |
| ODP-INTAKE-UX-REVIEW-001 | Antigravity4 / Antigravity6 | superseded — see section 4 | n/a |
| ODP-INTAKE-UX-MATCH-001 | Antigravity5 / Antigravity7 | PR #344 | merge `8dc59377` |
| ODP-INTAKE-UX-ASSIGN-001 | Codex / Antigravity5 | PR #345 | merge `b63cace8` |
| ODP-INTAKE-UX-PROMOTION-001 | Codex / Codex2 | PR #349 | merge `ba923a9b` |
| ODP-INTAKE-UX-QA-001 | Codex / Antigravity5 | PR #350 | merge `7a292c9c` |

Verification method: each archived delivery commit was checked with
`git merge-base --is-ancestor <sha> origin/dev`; where the recorded delivery
sha is not itself an ancestor (DETAIL, REVIEW), the deliverable content was
traced file-by-file against the dev tree (see section 4).

Known benign gap: DETAIL's final closeout commit `ff813259` (a 7-line
`verification.md` note only) never merged; its entire implementation is in dev
via PR #345 and was subsequently extended by later children. No code is
missing.

## 4. ODP-INTAKE-UX-REVIEW-001 Supersession Record

The archived REVIEW-001 delivery commit `36f5c919` exists only on its local
task branch: no PR was opened and no remote branch exists. Its specific
components (`AssistedEntryForm.tsx`, `FieldLineageRow.tsx`,
`ParsedDataReview.tsx`, `useCorrectionDraft.ts`) are **absent from dev by
design**: later children re-implemented and extended the same approved
behaviors on the composed surface that QA verified at runtime:

| REVIEW-001 behavior | Superseding implementation on dev |
|---|---|
| Assisted entry | `AssistedIntakeSection.tsx`, `ListingInboxIntakeView.tsx` (`ASSISTED_ENTRY_ONLY` flow) |
| Field lineage display | `AssistedIntakeSection.tsx`, `EvidencePanel.tsx`, `IdentityDecisionPanel.tsx` |
| Field correction | `IntakeFieldFixDialog.tsx`, `IntakeDetailDialog.tsx`, `IntakeProcessingDetail.tsx` |
| Retry-safe drafts / input preservation | 409-conflict input preservation in `TransferIntakeDialog.tsx` / `PauseSlaDialog.tsx` / assisted-entry validation; verified by QA "correction persistence" and mounted draft tests (`2cf2a31d`) |

The assisted-entry canonical flow, correction persistence, and lineage
surfaces were all runtime-verified against the real API by
`ODP-INTAKE-UX-QA-001` (PASS) on this tree. Integrating the stale REVIEW-001
branch back into dev would introduce a second, unexercised parallel component
set and regress the QA-verified surface; the integration decision is to keep
the superseding implementation. The functional acceptance of REVIEW-001
(assisted entry, lineage, correction, retry-safe drafts) is met on dev.

## 5. Verification Results (executed at `7a292c9c`)

| Verification | Result |
|---|---|
| `npm run typecheck --workspace=@oday-plus/web` | PASS (tsc --noEmit, clean) |
| `npm run build --workspace=@oday-plus/web` | PASS (all routes built, `/w/expansion/listings` present) |
| Three required Playwright specs, one worker, no retries | PASS, 18 tests in 4.2 minutes |
| `git diff --check origin/dev...HEAD` | PASS (clean) |

Playwright command:

```bash
CI=1 PATH="$PWD/.venv/bin:$PATH" npx playwright test \
  tests/e2e/operator-assisted-listing-intake.spec.ts \
  tests/e2e/operator-assisted-listing-intake-mobile.spec.ts \
  tests/e2e/operator-assisted-listing-intake-a11y.spec.ts \
  --workers=1 --retries=0 --reporter=line
```

No Assisted Listing Intake route was stubbed. Tests submit and read through
the real API client and assert durable IDs and persisted state.

## 6. Six Canonical Flows (real generated API client)

| Flow | Runtime assertion | Result |
|---|---|---|
| Exact duplicate | Exact URL/source identity intercepted before retrieval; opens the existing Listing | PASS |
| Assisted entry | `ASSISTED_ENTRY_ONLY` preserves the URL and validates durable manual input | PASS |
| Possible match | Human reason and risk acknowledgement required; no automatic merge | PASS |
| Promotion | Independent reviewer creates one Candidate with durable Candidate/SiteScore receipts | PASS |
| Score failure | `SCORE_FAILED` retains committed Candidate and exposes the failed score job | PASS |
| Replay | Same idempotency key recovers the result; no second Candidate, no duplicate score work | PASS |

Additional real-route assertions cover source-policy outcomes (approved /
blocked / unknown), retryable retrieval failure, correction persistence,
read-only governance access, unrelated-role denial, field masking,
self-review denial, version/owner/review conflicts, stale snapshot,
quarantine, retry exhaustion/DLQ, and durable decision/listing/Candidate/
SiteScore/correlation/audit receipts.

## 7. VDC-001 through VDC-005 Closure

| Condition | Closure | Evidence anchor |
|---|---|---|
| VDC-001 (P0) Transfer/Pause runtime branches | CLOSED — Transfer renders target + handoff note only; Pause renders reason + required editable resume time only; both require `If-Match`, preserve input on 409, bump version, and emit durable receipts | `TransferIntakeDialog.tsx`, `PauseSlaDialog.tsx`, `AssignmentSlaSummary.test.tsx` (13 tests asserting control presence/absence); `docs/evidence/completion/ODP-INTAKE-UX-ASSIGN-001/summary.md`; PR #345 |
| VDC-002 (P0) mobile overflow | CLOSED — zero page-level horizontal overflow at 390/1024/1440; complex comparison routes to desktop-required state | mobile spec assertions + section 8 screenshots at `7a292c9c`; QA defect 10 repaired |
| VDC-003 (P0) WCAG 2.2 AA | CLOSED — stable focus return, keyboard completion, landmarks, screen-reader summaries, reduced motion, zero serious/critical axe violations on queue and detail | a11y spec at `7a292c9c`; QA evidence section 5 |
| VDC-004 (P1) URL-restorable inbox state | CLOSED — filters, sort, view, selection, and active detail section serialize to the URL; direct open / reload / back / shareable-link restoration verified | `urlState.ts` (FND-001), durable `#intake/<id>` routing, QA "Assisted entry preserves the URL" PASS |
| VDC-005 (P1) discipline review outcomes | RECORDED — see table below | this document |

### VDC-005 discipline review record (implemented UI)

Recorded against Package 10 (interactive HTML SHA-256
`cc4e6ae97462bc99b1c2353c792cb3bec40d51a6c5efcfde165e5f47105e661d`) and the
implemented UI at integration commit `7a292c9c` (QA runtime commit
`a482a0ee`, identical tree):

| Discipline | Reviewer | Disposition | Anchor |
|---|---|---|---|
| Product Platform | REVIEW_003 author | APPROVED_WITH_CONDITIONS (conditions VDC-001..005 now closed/recorded) | `ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE_REVIEW_003.md` |
| System Design | ODP-SD-INTAKE-001 review | APPROVED (v0.2.1 package) | PR #319 merge `15e20b5a` |
| Frontend | Independent reviewers of the seven merged UI children (Antigravity3/5/6/7, Claude, Codex2) | APPROVED per child | section 3 matrix |
| Accessibility | ODP-INTAKE-UX-QA-001 reviewer Antigravity5 | APPROVED (VDC-002/003 verified at runtime) | QA evidence sections 5 and 8 |
| QA | Antigravity5 | APPROVED (18 runtime tests, real API) | QA evidence section 8 |
| Integration | Codex2 (this task's reviewer) | pending exact-SHA re-review | this PR |

Release-gate re-confirmation of these dispositions at rollout time remains
owned by `ODP-INTAKE-RELEASE-001`.

## 8. Responsive and Axe Evidence at the Integration Commit

Screenshots regenerated by the mobile spec during the section 5 run at
`7a292c9c` (copies in `screenshots/`, provenance: produced by the run in this
worktree):

- `screenshots/intake-390-inbox.png` — 390 px queue, no page-level overflow
- `screenshots/intake-390-detail.png` — 390 px detail
- `screenshots/intake-1024-detail.png` — 1024 px detail, reflow without overlap
- `screenshots/intake-1440-detail.png` — 1440 px full side-by-side comparison

Axe: the a11y spec asserts zero serious or critical violations on the intake
queue and detail views as part of the section 5 run.

## 9. Reproduce From Repo Root

```bash
npm ci
uv sync
npm run typecheck --workspace=@oday-plus/web
npm run build --workspace=@oday-plus/web
CI=1 PATH="$PWD/.venv/bin:$PATH" npx playwright test \
  tests/e2e/operator-assisted-listing-intake.spec.ts \
  tests/e2e/operator-assisted-listing-intake-mobile.spec.ts \
  tests/e2e/operator-assisted-listing-intake-a11y.spec.ts \
  --workers=1 --retries=0 --reporter=line
git diff --check origin/dev...HEAD
```

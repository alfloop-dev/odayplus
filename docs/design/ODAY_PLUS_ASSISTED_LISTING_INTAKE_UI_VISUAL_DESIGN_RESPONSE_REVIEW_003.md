---
doc_id: ODP-UXD-003-ADD-002-REVIEW-003
title: ODay Plus Assisted Listing Intake UI Visual Design Package 10 Review
version: 0.3.0
status: approved-with-conditions
decision: APPROVED_WITH_CONDITIONS
owner: Product Platform Engineering
reviewers: Product / System Design / Frontend / Accessibility / QA
reviews: ODP-UXD-003-ADD-002-RESPONSE
responds_to: ODP-UXD-003-ADD-002
supersedes_review: ODP-UXD-003-ADD-002-REVIEW-002
requirement_version: 1.0.1
canonical_design_tool: Claude Design
reviewed_package: operator-console-r7-20260720-package-10
reviewed_zip_sha256: d1583a00496f928b0765c1756c9671fedf615f12c84c00494d454c983645d7f8
reviewed_prototype_sha256: cc4e6ae97462bc99b1c2353c792cb3bec40d51a6c5efcfde165e5f47105e661d
reviewed_standalone_sha256: 1aefb8068faa39666599ceeafe74ba24f1ddc8abd57ba9a6513a724abaee7d0f
updated_at: 2026-07-20
---

# ODay Plus Assisted Listing Intake UI Visual Design Package 10 Review

## 1. Review Decision

Package 10 is `APPROVED_WITH_CONDITIONS` as the Claude Design visual baseline
for engineering execution. The conditions in section 3 are binding release
gates and override conflicting claims in the submitted response. Engineering
may implement the non-conflicting layout, content, state, and interaction
contracts immediately; it must implement the corrected behavior stated here
where the runnable prototype is defective.

Package 10 closes the prior source-policy, exact-duplicate, durable intake-link,
canonical-code, and package-consistency blockers. It provides usable desktop,
tablet, and constrained-width intake surfaces, correctly preserves ambiguous
match review, and includes durable receipts and expanded WORM evidence. It is
not unconditionally approved because independent runtime testing found a
Transfer/Pause conditional-rendering defect, page-level overflow at 390 px,
broken dialog focus return, unresolved contrast/landmark violations, and
session-only rather than URL-encoded inbox state.

This is a fresh review of Package 10. Every result below was re-verified against
the archived R7 files and runtime evidence; no Package 9 result was copied as
current evidence.

## 2. Finding Recheck

| Finding | Package 10 result | Re-verified disposition |
|---|---|---|
| `VDR-001` source/crawling implication | PASS | Closed; 591/Rakuya are scoped to user-submitted single-page URL retrieval and the approved feed is push-only |
| `VDR-002` exact duplicate and identity | PASS | Closed; unique `IN-3011+`, canonical URL check, and three-stage short path remain correct |
| `VDR-003` responsive design | CONDITIONAL | Tablet passes; required mobile intake jobs are reachable, but the 390 px document is 493 px wide |
| `VDR-004` accessibility | CONDITIONAL | Names, initial focus, trap, keyboard rows, live region, title, and language improved; focus return, contrast, and landmarks still fail |
| `VDR-005` durable routes | CONDITIONAL | Intake detail direct/reload/back passes; filters/sort/view/selection remain session state rather than restorable URL state |
| `VDR-006` required deliverables | CONDITIONAL | Claude Design package is canonical; remaining discipline reviewers must record outcomes before release |
| `VDR-007` assignment/evidence | CONDITIONAL | Conflict, input preservation, receipts, version bump, and 11 evidence fields pass; Transfer/Pause controls are reversed at runtime |
| `VDR-008` canonical codes | PASS | Closed; canonical match and error codes are retained |
| `VDR-009` package consistency | PASS | Closed; source and standalone are R7 and contain the same corrected intake contract markers |

## 3. Binding Conditions

### VDC-001 (P0) - Correct the Transfer and Pause runtime branches

The response section `VDR-007` at line 26 claims Transfer has no resume time and
Pause has a visible required resume time. In the canonical prototype, both
conditional blocks use `inkAsgDlg.isTransfer` (`Oday Plus Operator
Console.dc.html`, lines 2728 and 2734). Claude Design evaluates both blocks when
Transfer is true and neither when it is false.

Independent runtime result:

- Transfer renders target, resume time, and handoff note.
- Pause renders only the reason field; no resume-time control exists.
- Transfer `409 OWNER_CONFLICT`, note preservation, refresh/retry,
  `RCPT-ASG-3003-T`, owner/version update, and 11 WORM rows pass.
- Pause cannot produce a reviewer-confirmed resume-time receipt from the UI.

Engineering must render target plus handoff note only for Transfer, and reason
plus required editable resume time only for Pause. Both flows require
`If-Match`, preserved input on conflict, a version bump, timeline/audit entry,
and a durable receipt. Automated tests must assert control presence and absence,
not just internal default values.

Evidence: `package10-assignment-report.json`, `package10-transfer-dialog.png`,
and `package10-pause-dialog.png` in the Package 10 evidence directory; prototype lines
2720-2749 and 4930-4950.

### VDC-002 (P0) - Remove page-level mobile overflow

The response section `VDR-003` at line 22 claims no page-level overflow at
390 px. Chromium measured a 390 px viewport with 493 px document and body
width. The top navigation groups extend to 465-493 px; several Today actions
also extend past the viewport. The intake inbox/detail remain reachable and the
old global mobile blocker is gone, so this is a bounded responsive defect rather
than a missing mobile workflow.

Engineering must keep `documentElement.scrollWidth <= innerWidth` at 390 px,
recompose or overflow-contain the top navigation, and verify the intake inbox,
URL submit, detail/status, claim/simple confirmation, and receipt views at 390,
768/1024, and 1440 px. `DESKTOP_REQUIRED` remains limited to complex compare,
identity graph, promotion review, and restricted-evidence work.

Evidence: `package10-runtime-report.json`, `package10-mobile-inbox.png`,
`package10-mobile-detail.png`, and
prototype lines 31-98.

### VDC-003 (P0) - Complete WCAG 2.2 AA focus, contrast, and landmarks

The response section `VDR-004` at line 23 overstates completion. Initial focus
and the Tab trap pass, but closing Add URL with Escape leaves focus on `BODY`.
The stored trigger node is replaced during rerender before the return attempt
(prototype lines 3829-3835). Axe against intake detail reports:

- `color-contrast`: 125 affected nodes, including ratios 2.05, 3.01, 3.74,
  4.00, 4.32, 4.41, and 4.42 where 4.5 is required;
- `region`: 50 nodes outside landmarks.

The standalone wrapper also exposes an empty document title and language even
though its embedded R7 source is synchronized. Engineering must return focus to
a stable trigger reference, meet WCAG 2.2 AA contrast for active and disabled
states, add coherent `header`/`nav`/`main`/section landmarks, and ensure the
actual application document has a localized title and `lang="zh-Hant"`.

Evidence: `package10-runtime-report.json`, `package10-add-modal.png`, and prototype lines 25,
3829-3849.

### VDC-004 (P1) - Encode restorable inbox state in the URL

The response section `VDR-005` at line 24 correctly demonstrates durable
`#intake/IN-xxxx` detail routing. Direct open, reload, browser back, and exact
duplicate replay pass. However, the response says filters and selection are
persisted in session state, while handoff requirement sections 6 and 8.1 require
filters, sort, view, selection, and active detail section to be restorable from
the URL itself.

Engineering must define deterministic query/hash serialization for the inbox
view and active detail section, preserve unrelated query parameters, and prove
direct open, reload, back, forward, and shareable-link restoration. Session
storage may cache data but cannot be the only navigation contract.

### VDC-005 (P1) - Record discipline review outcomes

The response section `VDR-006` at line 25 still leaves System Design, Frontend,
Accessibility, and QA review unassigned. This review supplies Product Platform
and QA findings but does not impersonate the missing disciplines. Before release,
each reviewer must record `APPROVED`, `APPROVED_WITH_CONDITIONS`, or
`CHANGES_REQUESTED` against this exact package hash and the implemented UI
commit. Any defer needs an owner, interim fail-closed behavior, task, and gate.

## 4. Verified Corrections

### VDR-001, VDR-002, and VDR-008 - Closed

The runnable source contains no `系統排程`, `每日掃描`, `掃物件`, or
`來源掃描`. Source cards identify policy version/expiry and limit 591/Rakuya
retrieval to a user-submitted page; the approved feed is explicitly push-based.
The canonicalized exact-duplicate run created one unique `IN-3011`, used
`SUBMITTED -> CHECKING_IDENTITY -> READY`, never entered retrieval/parsing/
matching, and retained `EXACT_DUPLICATE` rather than a localized replacement.

### VDR-005 core intake link - Closed

Successful URL submission writes `#intake/IN-xxxx` immediately. Direct open and
reload restore the same detail, browser back returns to the inbox, and user
corrections remain in session data. The remaining query-state condition is
limited to inbox navigation state, not the intake identity deep link.

### VDR-007 evidence and conflict behavior - Closed in part

The assignment conflict displays current owner/version, preserves the handoff
note, supports refresh/retry, and emits a durable receipt. WORM evidence exposes
state, purpose, classification, expiry, retention/legal hold, masking, export,
verification, actor/time, lineage, receipt, and correlation ID. Only the
Transfer/Pause control branch remains binding under `VDC-001`.

### VDR-009 - Closed

The Package 10 source and standalone both identify R7, use the corrected intake
identity and source-policy markers, preserve canonical codes, and contain no
Package 9 banned discovery wording. The submitted file hashes match the archive.

## 5. Review Method and Evidence

Reviewed archive:

- ZIP: `docs_archive/00_source_zips/operator_console/r7-20260720-package-10/`
- Claude Design source: `extracted/Oday Plus Operator Console.dc.html`
- Runnable package: `extracted/oday-plus-console-r7-standalone.html`
- Submitted response: `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE.md`
- Runtime evidence: `docs/evidence/design_review/assisted_listing_intake_r7_package10/`

Validation performed:

1. ZIP traversal, size, CRC, MD5, SHA-256, and extracted-file verification.
2. Package 9/10 file and screen-label comparison.
3. Source/standalone R7 marker and banned-copy consistency checks.
4. Chromium runs at 1440, 1024, and 390 px with console/page-error capture.
5. URL submit, exact duplicate, direct hash, reload, and browser-back checks.
6. Modal naming, initial focus, Tab trap, Escape close, and focus-return checks.
7. Axe WCAG scan of the intake detail.
8. Transfer, Pause, owner conflict, input preservation, receipt, version, and
   WORM evidence checks.

## 6. Engineering Gate

Package 10 may be used for implementation only together with this review. The
prototype is visual/interaction evidence, not API, authorization, state, or
persistence authority. Engineering must follow the approved System Design
bundle when contracts differ and must encode `VDC-001` through `VDC-005` in
tests before `ODP-INTAKE-UX-001` can be marked complete.

Release remains fail-closed until the API dependency is merged, all UI execution
tasks have independent review, runtime E2E uses the real API client without
silent fixture fallback, and every condition above has exact-commit evidence.

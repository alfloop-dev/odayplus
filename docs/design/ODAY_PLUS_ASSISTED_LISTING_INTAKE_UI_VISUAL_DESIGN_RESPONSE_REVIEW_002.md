---
doc_id: ODP-UXD-003-ADD-002-REVIEW-002
title: ODay Plus Assisted Listing Intake UI Visual Design Resubmission Review
version: 0.2.1
status: changes-requested
decision: CHANGES_REQUESTED
owner: Product Platform Engineering
reviewers: Product / System Design / Frontend / Accessibility / QA
reviews: ODP-UXD-003-ADD-002-RESPONSE
responds_to: ODP-UXD-003-ADD-002
supersedes_review: ODP-UXD-003-ADD-002-REVIEW-001
requirement_version: 1.0.1
reviewed_package: operator-console-r6-20260719-package-9
reviewed_zip_sha256: 601a55b29f1097c6c50938f30e1acbdf4c9dc7f1ff9dfbc07021b00ac6f12abd
reviewed_prototype_sha256: b3226df775cf1bf98a6707317ee0cf1c93c5b7ce90f2fd4c0bab8fba825e5fea
updated_at: 2026-07-19
---

# ODay Plus Assisted Listing Intake UI Visual Design Resubmission Review

## 1. Review Decision

The Package 9 review decision is `CHANGES_REQUESTED`.

Package 9 closes the exact-duplicate path/identity defect and restores exact
canonical match codes. It also adds useful transfer, pause, evidence, dialog,
hash-link, and mobile-fallback concepts. The resubmission is still not an
implementation-ready handoff: the runnable prototype retains scheduled-source
language, does not provide the required tablet/mobile workflows, fails modal
focus and keyboard requirements, and does not make a newly submitted intake
reloadable. The ZIP also ships a standalone runner containing the unchanged
Package 8 implementation.

This is a fresh review of Package 9. Findings below cite the submitted response,
the Package 9 prototype, and new runtime evidence; no old finding is carried
forward without re-verification.

## 2. Previous Finding Recheck

| Finding | Package 9 result | Re-verified disposition |
|---|---|---|
| `VDR-001` source/crawling implication | OPEN | URL-receipt wording improved, but explicit daily/automatic source scanning remains |
| `VDR-002` exact duplicate and identity | CLOSED | Unique `IN-3011`; path is `SUBMITTED -> CHECKING_IDENTITY -> READY` |
| `VDR-003` responsive design | OPEN | 1024 px still overflows by 256 px; mobile blocks every screen |
| `VDR-004` accessibility | OPEN | Dialog attributes added; focus, trap, labels, rows, live/table semantics, and contrast remain incomplete |
| `VDR-005` durable routes | OPEN | Direct hash link works; submission, filters, selection, and section state are not durable |
| `VDR-006` required deliverables | PARTIAL | Claude Design is the approved canonical format; reviewer outcomes remain unassigned |
| `VDR-007` assignment/evidence | PARTIAL | Transfer works; Pause omits resume-time control and evidence remains summary-only |
| `VDR-008` canonical codes | CLOSED | `EXACT_DUPLICATE` and `POSSIBLE_MATCH` are preserved |

## 3. Blocking Findings

### VDR-001 (P0) - Scheduled discovery is still presented as product behavior

The source cards now say `最近收件` and identify 591/Rakuya ownership as
`人工送件（URL）`, which is a real improvement. However, the same runnable
workflow still says `找區域 -> 掃物件`, offers a tracked-area action whose result
is `每日掃描優先比對此區`, and says saved search conditions cause
`來源掃描自動比對此區`. Those are explicit scheduled-discovery claims, not
user-submitted URL processing.

Evidence:

- Handoff requirement section 4 prohibits recurring external-site discovery.
- Submitted response section 7, lines 51-57, claims the non-goal is accepted.
- Package 9 prototype `Network Expansion Flow Stepper`, line 818.
- Package 9 prototype `nwAreas`, lines 5406 and 5408.
- Package 9 prototype `nwRadar`, lines 5415-5418, still labels 591 and Rakuya
  `已連接` rather than a clearly scoped URL-intake policy.
- Runtime capture `listing-radar.png` in the Package 9 evidence directory.

Required correction:

- Replace `掃物件`, `每日掃描`, `來源掃描自動比對`, and ambiguous `已連接`
  labels for external listing sites with explicit user-submitted URL language.
- Keep push/scheduled status only on the named approved feed and show its policy
  scope and expiry.

### VDR-003 (P0) - Required tablet and mobile workflows are still absent

The prototype still enforces `min-width:1280px` and a 1240 px header. At
1024 x 768, the document remains 1280 px wide. At 390 x 844, a global modal
blocks the entire application, including the URL submission, status tracking,
and simple confirmation workflows that the modal itself claims mobile supports.
Closing it exposes the 1280 px desktop page with horizontal scrolling.

Measured runtime results:

| Viewport | Document width | Result |
|---|---:|---|
| 1024 x 768 | 1280 px | 256 px overflow; no tablet composition |
| 390 x 844 | 1280 px | global `DESKTOP_REQUIRED`; required mobile jobs unreachable |

Evidence:

- Handoff requirement sections 12 and 15 require actual `md` and `sm` frames.
- Submitted response sections 6-7, lines 47-57, defer tablet frames.
- Package 9 prototype root/header, lines 29-31.
- Package 9 prototype `Mobile Desktop-Required Fallback`, lines 2703-2708.
- Package 9 prototype resize handler, lines 3747-3749, applies the fallback to
  every screen below 760 px instead of only complex work.
- Runtime captures `tablet.png` and `mobile.png`.

Required correction:

- Deliver runnable tablet URL submission, status/detail, assisted entry,
  assignment, and unambiguous approval compositions.
- Deliver runnable mobile URL submission, status tracking, simple confirmation,
  claim/response, and receipt viewing.
- Limit `DESKTOP_REQUIRED` to complex compare, identity graph, promotion review,
  and restricted-evidence work.

### VDR-004 (P0) - Dialog attributes were added without the required focus and keyboard behavior

`role="dialog"`, `aria-modal`, and some labels were added, but opening Add URL
leaves focus on the background trigger. Repeated Tab reaches background toolbar
and navigation controls, so the modal has no focus entry or trap. The close
button is named only `x`, the HeatZone select has no accessible name, and intake
queue rows remain clickable `div` elements without keyboard role or tab stop.

An axe scan scoped to Add URL reports the unnamed select as a critical
`select-name` violation. A full detail scan also reports 184 contrast failures,
no document title, and no document language. The response explicitly defers
focus trap/return.

Evidence:

- Handoff requirement section 13 and deliverable 10.
- Submitted response section 7, lines 56-57.
- Package 9 prototype Add URL dialog, lines 2313-2338.
- Package 9 prototype intake rows, lines 973-985.
- Package 9 prototype has no `aria-sort`, live-region, or keyboard row contract.
- Runtime capture `add-modal.png` and DOM/keyboard/axe inspection.

Required correction:

- Implement initial focus, focus containment, focus return, and specified Escape
  behavior for every modal and drawer.
- Programmatically name all controls and icon-only actions.
- Make queue/table navigation keyboard operable with header, sort, row, and
  action semantics.
- Add live-region, field-error focus, comparison summary, language/title,
  reduced-motion, and WCAG 2.2 AA contrast evidence.

### VDR-005 (P0) - The new hash link does not make the submission workflow durable

Opening an existing queue row now writes `#intake/IN-xxxx`, and a directly
opened hash survives reload. Submission does not use that path: the prototype
sets `inkView` in memory but does not call `inkOpen` or update `location.hash`.
The exact-duplicate test created and displayed `IN-3011` while the URL remained
unchanged; reload immediately lost the record detail. Inbox filters, selected
row, active section, compare task, and receipt are also absent from the URL.

Evidence:

- Handoff requirement sections 6 and 8.1.
- Submitted response section 1, lines 15-21, claims automatic deep-link
  navigation; section 7, lines 56-57, admits only a hash simulation.
- Package 9 prototype `inkOpen`, lines 4842-4843.
- Package 9 prototype duplicate/new submission branches, lines 4947 and 4953,
  set only in-memory state.
- Runtime capture `exact-before-reload.png`; observed hash before reload was
  empty and `IN-3011` was absent after reload.

Required correction:

- Update the durable URL as part of successful intake creation, before showing
  the result.
- Provide restorable URL state for inbox filters/sort/selection, active detail
  section, task compare, and receipt.
- Re-run direct open, reload, back, forward, and lost-response recovery tests.

### VDR-009 (P0) - The ZIP contains two different R6 implementations

The declared canonical `.dc.html` changed in Package 9, but the shipped
standalone `Oday Plus 營運管理後台 R6.html` is byte-identical to Package 8. The
standalone runner still contains the old `系統排程` source ownership and lacks
the corrected exact-duplicate path. A reviewer or implementation fleet opening
the standalone artifact therefore sees a different design from the declared
canonical source.

Evidence:

- Package 9 canonical prototype SHA-256:
  `b3226df775cf1bf98a6707317ee0cf1c93c5b7ce90f2fd4c0bab8fba825e5fea`.
- Package 9 standalone SHA-256:
  `e70c34de63fd862f444c18c9721d4e80ebb887090c4c2eff7015c7b6fe28e153`,
  identical to Package 8.
- Runtime standalone source inspection found `owner:'系統排程'` and no corrected
  `EXACT_DUPLICATE` path.
- Runtime capture `standalone.png`.

Required correction:

- Rebuild the standalone runner from the exact submitted canonical prototype,
  or remove it from the delivery.
- Add a package gate that verifies source/standalone design version, state
  sequence, screen labels, and required correction markers before distribution.

## 4. Additional Required Changes

### VDR-006 (P1) - Required reviewer outcomes are not recorded

The delivery-format portion of this finding is closed. Product owner confirmed
on 2026-07-19 that Claude Design source plus its synchronized runnable package
is the canonical visual-design deliverable; Figma is not required. Handoff
requirement version 1.0.1 records that decision.

Submitted response section 8, lines 59-61, still says Product, System Design,
Frontend, and Accessibility reviewers are awaiting assignment, and it does not
record QA review. Format acceptance does not replace those review outcomes.

Required correction:

- Assign and record Product, System Design, Frontend, Accessibility, and QA
  review results against the Claude Design package.
- Give each defer an interim behavior, owner, follow-up task, and release gate.

### VDR-007 (P1) - Assignment and WORM evidence are only partially corrected

Transfer now requires a handoff note and records it in timeline/audit. The Pause
dialog does not render the resume-time control; the same control incorrectly
appears in Transfer. The pause action then records its hidden default rather
than a reviewer-confirmed value. The WORM row adds state, purpose,
classification, and retention, but the response still defers the evidence
view and provides no access-expiry, export/verification, masking, or evidence
receipt interaction.

Evidence:

- Handoff requirement sections 8.6 and 8.9.
- Submitted response section 7, lines 56-57.
- Package 9 prototype assignment dialog, lines 2673-2698.
- Package 9 prototype action logic, lines 4844-4856.
- Package 9 prototype WORM summary, line 5205.
- Runtime capture `pause-dialog.png`.

Required correction:

- Render and require the resume time only for Pause; add pause/resume receipt and
  owner/version conflict behavior.
- Add the required WORM evidence viewing, access expiry, export/verification,
  masking, and durable evidence receipt states.

## 5. Verified Corrections

### VDR-002 - Closed

Package 9 advances the next intake ID to `IN-3011`, heals old session sequence
state, and renders exact duplicates as only `SUBMITTED`, `CHECKING_IDENTITY`,
and `READY`. The canonicalized tracking-parameter test produced one unique
`IN-3011` and showed no retrieval, parsing, or matching stage.

Evidence: prototype lines 3013, 4942-4949, and 5103-5108; runtime capture
`exact-before-reload.png`.

### VDR-008 - Closed

Queue, detail, filters, and match labels preserve `EXACT_DUPLICATE` and
`POSSIBLE_MATCH` with localized labels. Evidence: prototype lines 4912-4913 and
the Package 9 queue/detail captures.

## 6. Review Method and Evidence

Reviewed archive:

- ZIP: `docs_archive/00_source_zips/operator_console/r6-20260719-package-9/`
- Canonical prototype: `extracted/Oday Plus Operator Console.dc.html`
- Submitted response:
  `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE.md`
- Runtime evidence:
  `docs/evidence/design_review/assisted_listing_intake_r6_package9/`

Validation performed:

1. ZIP traversal, size, CRC, and SHA-256 validation.
2. Per-file comparison against Package 8.
3. Chromium runtime with console/page error capture.
4. Expansion-manager URL submission and exact-duplicate replay.
5. Direct hash, reload, and post-submission reload checks.
6. 1440, 1024, and 390 px layout measurements.
7. Keyboard focus traversal, DOM semantics, and axe WCAG scan.
8. Transfer, Pause, WORM summary, canonical code, and source-policy inspection.
9. Canonical-versus-standalone bundle consistency check.

## 7. Resubmission Gate

The next package must close `VDR-001`, `VDR-003`, `VDR-004`, `VDR-005`, and
`VDR-009`; close or formally govern `VDR-006` and `VDR-007`; keep the verified
`VDR-002` and `VDR-008` corrections; and ship one internally consistent,
checksum-pinned design package. Fleet implementation remains blocked for these
surfaces until the next review decision is `APPROVED` or
`APPROVED_WITH_CONDITIONS` with explicit implementation boundaries.

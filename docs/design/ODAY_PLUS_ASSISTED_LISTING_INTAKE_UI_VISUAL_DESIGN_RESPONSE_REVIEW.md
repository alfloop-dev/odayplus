---
doc_id: ODP-UXD-003-ADD-002-REVIEW-001
title: ODay Plus Assisted Listing Intake UI Visual Design Response Review
version: 0.1.0
status: changes-requested
decision: CHANGES_REQUESTED
owner: Product Platform Engineering
reviewers: Product / System Design / Frontend / Accessibility / QA
reviews: ODP-UXD-003-ADD-002-RESPONSE
responds_to: ODP-UXD-003-ADD-002
superseded_by_review: ODP-UXD-003-ADD-002-REVIEW-002
requirement_version: 1.0.0
reviewed_package: operator-console-r6-20260718-package-8
reviewed_zip_sha256: cacd5f3ac659e5a52be4380f469c0c20082c1dd23cd430fafd1a3a60002a97f0
reviewed_prototype_sha256: 9531c3cca31fdf247a2a216c661a19a7f1f900ec7a8274f60abc9694e4d25af5
updated_at: 2026-07-19
---

# ODay Plus Assisted Listing Intake UI Visual Design Response Review

## 1. Review Decision

The review decision is `CHANGES_REQUESTED`.

R6 is a material and useful desktop interaction proposal. The intake queue,
field lineage, match comparison, role switching, non-optimistic decision
summary, assisted entry, promotion receipt, and recovery-state concepts are
substantially more complete than R5. The package is not yet an
implementation-ready visual-design handoff because it violates a product
non-goal, produces an incorrect exact-duplicate state history, and does not
deliver the required responsive, accessibility, durable-route, and handoff
artifacts.

This decision reviews the received Package 8 artifacts. It does not rely on the
design team's self-assessment in response sections 6 and 7, and it does not
approve Fleet implementation of `ODP-INTAKE-UX-001`.

## 2. Blocking Findings

### VDR-001 (P0) - The Listing Radar implies scheduled crawling of 591 and Rakuya

The product requirement prohibits any design that implies continuous crawling,
result-page scraping, or scheduled external-ID enumeration. The R6 Listing
Radar nevertheless renders 591 and Rakuya as `已連接`, gives each a recent
`掃描` time, reports discovered/parsed/geocoded counts, and names the owner as
`系統排程`.

Evidence:

- Handoff requirement section 4, lines 77-86.
- Submitted response section 7 claims all non-goals are accepted, including
  "無爬蟲暗示".
- Archived prototype lines 3254-3255 define scheduled 591/Rakuya scan records.
- Runtime capture:
  `docs/evidence/design_review/assisted_listing_intake_r6/listing-inbox.png`.

Required correction:

- Remove scheduled scan/discovery language and metrics from unapproved listing
  sites.
- Represent 591/Rakuya intake as user-submitted URL observations only.
- Keep scheduled processing only for a specifically named approved feed, with
  its policy state and approval scope visible.
- Replace real provider URLs in sample data with approved synthetic examples,
  or attach the approval that permits those samples.

### VDR-002 (P0) - Exact duplicate history and intake identity are incorrect

The exact-duplicate flow is correctly intercepted before retrieval in the
timeline, but the stage rail marks `RETRIEVING`, `PARSING`, and `MATCHING` as
completed before `READY`. This presents processing that did not happen and
contradicts the required "exact duplicate before retrieval" outcome.

The same exercised flow also created a new intake with ID `IN-3008`, while R6
already contains a seeded `IN-3008` quarantined record. The prototype therefore
shows two different records with one durable identity.

Evidence:

- Handoff requirement section 7, lines 138-146, and section 8.2, lines 173-184.
- Archived prototype line 2966 seeds the next intake sequence at `3008`.
- Archived prototype line 3040 already defines `IN-3008`.
- Archived prototype lines 4869-4878 create the duplicate receipt from that
  sequence.
- Archived prototype lines 5034-5038 force every approved source through the
  retrieval/parse/match rail even when identity checking stopped processing.
- Runtime capture:
  `docs/evidence/design_review/assisted_listing_intake_r6/exact-duplicate.png`.

Required correction:

- Give `EXACT_DUPLICATE` its own legal visual path:
  `SUBMITTED -> CHECKING_IDENTITY -> READY`, with skipped stages absent or
  explicitly marked `未執行`.
- Generate unique intake IDs and add a prototype assertion that no queue/detail
  route has duplicate identity.
- Re-run the exact URL and canonicalized tracking-parameter variants.

### VDR-003 (P0) - Tablet and mobile are declarations, not designs

The response says tablet and mobile behavior is covered, but the prototype root
has `min-width:1280px`, the header has `min-width:1240px`, and no responsive
media contract changes those dimensions. The only mobile behavior is a prose
sentence at the bottom of the desktop detail page.

Chromium measurements:

| Viewport | Document width | Horizontal overflow | Desktop-required state |
|---|---:|---:|---|
| 1440 x 1000 | 1440 | 0 | n/a |
| 1024 x 768 | 1280 | 256 px | absent |
| 390 x 844 | 1280 | 890 px | absent |

Evidence:

- Handoff requirement section 12, lines 382-395, and acceptance criterion at
  lines 475-476.
- Submitted response sections 6 and 7, especially lines 49 and 55.
- Archived prototype lines 29 and 31.
- Runtime captures `tablet-inbox.png` and `mobile-inbox.png` in the review
  evidence directory.

Required correction:

- Deliver real `lg`, `md`, and `sm` compositions for 003A-003F.
- Provide the mobile `desktop-required` state for comparison, identity graph,
  and restricted-evidence work, preserving a real deep link.
- Remove fixed minimum widths that make the required mobile workflows
  unreachable.

### VDR-004 (P0) - Modal, form, queue, and dynamic-state accessibility is missing

The Add URL surface looks like a modal but is not exposed as a dialog. It has no
`role="dialog"`, `aria-modal`, accessible name, focus trap, or initial focus.
Opening it leaves focus on the background trigger. The URL input and area
select have visual `div` labels but no programmatic labels. The icon-only close
button is named only `×`. Queue rows are clickable `div` elements with no
keyboard role or tab stop.

The archived prototype contains no `aria-*`, `role`, or `tabindex` contract for
these surfaces. A visible sentence labelled as a screen-reader summary does not
replace programmatic comparison/table semantics or live-region behavior.

Evidence:

- Handoff requirement section 13, lines 397-407, and Figma deliverable 10 at
  line 432.
- Archived prototype lines 2313-2347 for the Add URL modal.
- Runtime capture `add-url-modal.png` and DOM inspection: zero dialogs, zero
  labelled URL/area controls, and focus remained on `從網址新增物件`.

Required correction:

- Annotate and prototype dialog name, modal state, focus entry/trap/return, and
  Escape behavior.
- Associate every control with a label and error description.
- Give icon-only actions accessible names and tooltips.
- Define keyboard row/action navigation, table/header semantics, `aria-sort`,
  live regions, field-error focus, and screen-reader comparison output.
- Provide WCAG 2.2 AA contrast results and reduced-motion behavior.

### VDR-005 (P0) - Durable routes are decorative text and cannot restore state

The response calls the detail a durable page and shows
`/w/expansion/listings/intake/:id`, but opening a record does not change the
browser URL. Reloading loses the detail. Filter, selection, section, and modal
state are also not represented in the URL. `sessionStorage` does not provide a
shareable or task-deep-linkable route.

Evidence:

- Handoff requirement section 6, lines 104-117, and section 8.1, lines 170-171.
- Submitted response section 1, line 17, versus the explicit router defer in
  section 7, line 55.
- Archived prototype lines 2353-2358 render the route as text.
- Runtime: location stayed on the standalone HTML file after opening IN-3001;
  reload returned to the default workspace with no detail selected.

Required correction:

- Supply an approved route/frame contract for Inbox filters, selected intake,
  active detail section, compare task, and receipt.
- Make the canonical prototype exercise browser back/forward, reload, and a
  directly opened intake/task link without losing state.

## 3. Additional Required Changes

### VDR-006 (P1) - Required design deliverables were unilaterally removed

The handoff requires a canonical Figma package plus a response document. The
response replaces Figma with HTML without an approved requirement change,
provides no Figma/file/node links, and leaves Product, System Design, Frontend,
and Accessibility reviewers unassigned.

Evidence:

- Handoff requirement sections 15 and 16, lines 419-463.
- Submitted response lines 7-8, 54, and 57-59.

Required correction:

- Deliver the requested Figma package, or obtain a written Product + Frontend +
  Accessibility acceptance of HTML as the canonical substitute.
- Provide frame/state mapping, measurements, component properties, copy sheet,
  overflow/sticky annotations, and reviewer records.
- Every remaining defer must name interim behavior, owner, follow-up task, and
  release gate. "POC 不阻塞" is not an acceptance decision.

### VDR-007 (P1) - Assignment/evidence interactions remain incomplete

The response explicitly defers transfer/pause forms and the WORM evidence deep
view. Text history proves that a state exists, but it does not specify the
required action composition, reason/resume-time fields, permission denial,
confirmation, conflict recovery, or resulting receipt.

Evidence:

- Handoff requirement section 8.6, lines 239-251, and section 8.9, lines
  279-287.
- Submitted response section 7, line 55.

Required correction:

- Add transfer and pause/resume flows with handoff note, approved reason,
  resume time, owner/version conflict, and audit receipt.
- Add evidence-state viewing for WORM status, purpose binding, classification,
  access expiry, export/verification state, and masking.

### VDR-008 (P1) - Canonical state codes are altered in decision surfaces

The requirement says the English canonical state code must be preserved. R6
renders `POSSIBLE MATCH`, `EXACT DUPLICATE`, and queue shorthand `POSSIBLE`
instead of `POSSIBLE_MATCH` and `EXACT_DUPLICATE`.

Evidence:

- Handoff requirement section 9.1, lines 289-309.
- Archived prototype line 4843 and the IN-3003/duplicate runtime captures.

Required correction:

- Display exact canonical codes beside localized labels in the queue, detail,
  filters, state matrix, errors, and receipts.

## 4. Verified Coverage

These areas passed desktop concept review and should be retained while fixing
the blockers:

| Area | Result | Verified behavior |
|---|---|---|
| 003A URL submission | CONDITIONAL PASS | Syntax feedback, source detection, canonicalization copy, ownership context, double-submit lock |
| Exact duplicate | CONDITIONAL PASS | Canonical URL intercepted before retrieval; stage rail and ID defects remain VDR-002 |
| 003B processing detail | PASS | Real stage labels, evidence, parser/snapshot/correlation, assignment/SLA, timeline |
| 003C parsed review | PASS | Source/normalized/corrected columns, low-confidence flags, reason dialog concept |
| 003D revision/possible match | PASS | Side-by-side differences, matching/contradictory signals, four separated actions, no auto-merge |
| 003E assisted entry | PASS | `ASSISTED_ENTRY_ONLY`, no retrieval claim, required manual fields, returnable draft concept |
| 003F promotion/job | CONDITIONAL PASS | Second actor, self-review denial, committed candidate receipt, score failure/replay concept |
| Role variants | PASS | Staff, manager, steward, governance, privacy, and limited-user modes are represented |
| State/error inventory | PASS | Intake/source/match/assignment/SLA/decision/job matrix and recovery examples are present |
| Visual direction | PASS | Compact operational layout, restrained semantic palette, clear desktop hierarchy |

`PASS` in this table means the design concept is reusable. It does not override
the overall `CHANGES_REQUESTED` decision or authorize implementation.

## 5. Review Method and Evidence

Reviewed committed archive:

- ZIP:
  `docs_archive/00_source_zips/operator_console/r6-20260718-package-8/Oday Plus 營運管理後台 (8).zip`
- Canonical prototype:
  `docs_archive/00_source_zips/operator_console/r6-20260718-package-8/extracted/Oday Plus Operator Console.dc.html`
- Standalone runner:
  `docs_archive/00_source_zips/operator_console/r6-20260718-package-8/extracted/Oday Plus 營運管理後台 R6.html`
- Submitted response:
  `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE.md`
- Runtime captures:
  `docs/evidence/design_review/assisted_listing_intake_r6/`

Validation performed:

1. ZIP path decoding, entry traversal check, size check, and `unzip -t`.
2. SHA-256 verification of the ZIP and all seven submitted files.
3. Chromium rendering with no console or page errors at initial load.
4. Role switch to Expansion manager and direct exercise of 003A-003E.
5. NEW, exact duplicate, revision, possible match, assisted-entry, quarantine,
   retry/DLQ, and decision-summary state inspection.
6. DOM accessibility inspection of modal, controls, queue rows, and focus.
7. Browser URL/reload restoration test.
8. Screenshot and horizontal-overflow checks at 1440 x 1000, 1024 x 768,
   and 390 x 844.

## 6. Resubmission Gate

The next response must:

1. close VDR-001 through VDR-005 with runnable evidence;
2. resolve VDR-006 through VDR-008 or record an approved requirement change;
3. publish a new response version and immutable package checksum;
4. include desktop, tablet, and mobile captures for 003A-003F;
5. include keyboard/focus/screen-reader annotations and automated accessibility
   evidence appropriate for a design prototype; and
6. record Product, System Design, Frontend, Accessibility, and QA reviewers.

Fleet may estimate component work from the passing desktop concepts, but must
not implement the R6 source-policy panel, exact-duplicate rail, responsive
layout, modal/form semantics, or routing as currently designed.

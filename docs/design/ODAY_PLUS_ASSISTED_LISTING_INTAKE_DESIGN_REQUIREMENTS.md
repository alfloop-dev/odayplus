---
doc_id: ODP-UXD-003-ADD-001
title: ODay Plus Assisted Listing Intake Design Requirements
version: 0.1.0
status: ready-for-design
owner: Product / Expansion Operations
design_owner: Product Design
engineering_task: ODP-EXT-002
updated_at: 2026-07-14
---

# ODay Plus Assisted Listing Intake Design Requirements

## 1. Assignment

Design the human-assisted listing intake experience that extends the existing
Expansion Workspace and Listing Inbox. The design team owns the screen layout,
interaction composition, component selection, responsive behavior, and final
copy. This document defines product requirements and acceptance boundaries; it
is not a visual design.

This is an addendum to ODP-UXD-003. Do not redesign HeatZone, Candidate Site,
SiteScore, or the entire Listing Inbox unless required to integrate this flow.

## 2. Product Intent

Expansion staff discover a promising property manually on an external website.
They submit the listing URL to ODay Plus. ODay Plus then determines whether the
submission is new, already known, an update to an existing listing, ambiguous,
or unable to be processed. A human remains responsible for resolving uncertain
matches and promoting a listing to Candidate Site.

The product must not imply that ODay Plus continuously crawls 591, Rakuya, or
other listing sites. Scheduled discovery, result-page crawling, and automatic
enumeration of external listing IDs are outside this design.

## 3. Primary Users

| Role | Need | Permission boundary |
|---|---|---|
| Expansion staff | Submit URLs and verify parsed listing data | Create and edit own intake submissions |
| Expansion manager | Resolve ambiguous duplicates and approve promotion | Merge, reject, assign, and promote |
| Data steward | Review parser failures and source quality | Correct mappings and quarantine records |
| Governance reviewer | Inspect source, permission, and processing history | Read-only evidence and audit access |

## 4. Required Workflow

```text
Listing Inbox
-> Add listing from URL
-> URL validation and exact match check
-> Source access policy result
-> Parse or assisted entry
-> Parsed data review
-> Entity matching result
-> New / Duplicate / Revision / Needs review / Quarantined
-> Human confirmation
-> Save to Listing Inbox or promote to Candidate Site
```

The user must be able to leave after submission and return to the resulting
intake record without losing state.

## 5. Required Screens and Surfaces

### 5.1 UX-SCR-EXP-003A - Add Listing From URL

Entry points:

- Primary action in Listing Inbox: `從網址新增物件`.
- Optional contextual action from HeatZone detail, preserving HeatZone context.

Required content:

- URL input.
- Source detection result.
- Optional HeatZone or assigned area.
- Submitter and ownership context.
- Clear statement of what happens after submission, expressed as operational
  status rather than instructional marketing copy.

Required behavior:

- Validate URL syntax before submission.
- Normalize tracking parameters without changing the visible original URL.
- Detect an exact URL already in the system before starting parsing.
- Prevent accidental double submission while a request is running.
- Preserve the original URL for evidence and show the canonical URL separately
  when they differ.

### 5.2 UX-SCR-EXP-003B - Intake Processing Detail

This may be a page or wide drawer, but it must support a durable deep link.

Required regions:

1. Submission summary: source, URL, submitter, submitted time, owner.
2. Processing status: validation, policy check, retrieval, parsing, matching.
3. Source evidence: original URL, canonical URL, captured time, parser version,
   snapshot ID, and correlation ID.
4. Parsed listing preview: source value versus normalized value.
5. Match result and confidence.
6. Human decision actions.
7. Timeline and audit history.

Processing status must use real stages, not a fabricated percentage:

```text
SUBMITTED
CHECKING_IDENTITY
CHECKING_SOURCE_POLICY
AWAITING_ASSISTED_ENTRY
RETRIEVING
PARSING
MATCHING
NEEDS_REVIEW
READY
QUARANTINED
FAILED
```

### 5.3 UX-SCR-EXP-003C - Parsed Data Review

Required field groups:

- Identity: provider, provider listing ID, listing type, listing status.
- Location: raw address, normalized address, district, latitude/longitude when
  available, geocode confidence.
- Commercial: rent or asking price, currency, area, management fee, deposit.
- Property: floor, total floors, frontage, parking/temporary stop, available
  date, description-derived feasibility flags.
- Provenance: source URL, source snapshot, observed time, parser version.

For every parsed field, the reviewer must be able to distinguish:

- Parsed value.
- Normalized value.
- Manually corrected value.
- Missing or low-confidence value.

Manual corrections require a reason when they affect identity, address, rent,
area, or matching outcome.

### 5.4 UX-SCR-EXP-003D - Duplicate and Revision Review

The comparison must support these outcomes:

| Outcome | Meaning | Primary action |
|---|---|---|
| `NEW` | No reliable existing match | Create listing |
| `EXACT_DUPLICATE` | Same source identity or canonical URL | Open existing listing |
| `REVISION` | Same property with changed price/status/content | Append revision |
| `POSSIBLE_MATCH` | Similar but uncertain | Human compare and decide |
| `QUARANTINED` | Invalid, prohibited, or unsafe to process | Review reason |

Comparison design requirements:

- Side-by-side current and submitted values on desktop.
- Changed fields visually marked without relying only on color.
- Match evidence must name the signals used: source ID, canonical URL,
  normalized address, area, floor, listing type, and price/rent.
- Show confidence and contradictory signals.
- Never auto-merge `POSSIBLE_MATCH`.
- Actions must clearly separate `建立新物件`, `加入既有物件版本`,
  `標記重複`, and `送交資料管理員`.

### 5.5 Listing Inbox Integration

Add filters and columns for:

- Intake method: URL / manual / CSV / approved feed.
- Intake status.
- Match outcome.
- Source.
- Submitted by.
- Needs review.
- Last observed and last updated.

Rows requiring review must expose a direct review action. The existing list and
map toggle remains available; this flow does not replace the Listing Inbox.

## 6. Source Policy States

The design must handle source access separately from parsing success:

| State | User-facing behavior |
|---|---|
| `APPROVED_RETRIEVAL` | System may retrieve the submitted page and continue parsing |
| `ASSISTED_ENTRY_ONLY` | Do not fetch; retain URL and ask user to enter required fields |
| `AUTH_REQUIRED` | Explain that approved account access is required; do not request raw credentials in UI |
| `SOURCE_BLOCKED` | Stop processing and show governance reason and next action |
| `POLICY_UNKNOWN` | Fail closed and route to governance review |

Do not use copy suggesting that robots.txt alone grants permission. Credentials,
cookies, tokens, or private API endpoints must never be requested in this flow.

## 7. State Requirements

Design all of the following:

- Empty: no URL submissions yet, with the permitted next action.
- Loading: identity check, retrieval, parse, and match stages.
- Exact duplicate found before retrieval.
- Successfully parsed new listing.
- Existing listing revision with changed fields.
- Possible duplicate requiring human review.
- Assisted-entry-only source.
- Unsupported source.
- Source blocked by policy.
- Page removed or no longer available.
- Authentication wall or bot challenge encountered.
- Parser returned partial data.
- Parser failed with retryable and non-retryable variants.
- Permission-limited/read-only reviewer.
- Stale source snapshot.
- Quarantined record.

Errors must show summary, next action, error code, correlation ID, and occurred
time. User-entered corrections must survive retryable failures.

## 8. Decision and Audit Requirements

Every human decision must record:

- Actor and role.
- Timestamp.
- Decision: create, revise, duplicate, quarantine, reject, or promote.
- Reason for overrides and identity-affecting corrections.
- Before and after values.
- Related listing and candidate IDs.
- Source snapshot and parser version.

High-impact merge, split, and promotion actions require a review summary before
confirmation. They must not use optimistic UI updates.

## 9. Accessibility and Responsive Behavior

- Full compare and correction workflow is desktop-first.
- Tablet may submit URLs, review status, and approve an unambiguous result.
- Mobile supports URL submission, status tracking, and simple confirmation;
  ambiguous side-by-side matching may route to a desktop-required state.
- All stages and outcomes use text plus icon/pattern, never color alone.
- Comparison changes require a screen-reader-readable change summary.
- All forms, comparison rows, dialogs, and drawers are keyboard operable.
- External links identify the destination and open without losing intake state.

## 10. Non-Goals

- Designing or implying an automatic 591/Rakuya crawler.
- Search-result scraping or recurring external-site polling.
- Capturing provider credentials in the product UI.
- Automatically promoting parsed records to Candidate Site.
- Automatically merging ambiguous property matches.
- Redesigning the complete Expansion Workspace.

## 11. Design Deliverables

The design team must return:

1. Desktop and mobile flow map.
2. Listing Inbox integration specification.
3. URL submission screen or modal specification.
4. Processing detail and status specification.
5. Parsed-data review specification.
6. Duplicate/revision comparison specification.
7. Assisted-entry fallback specification.
8. All states listed in section 7.
9. Keyboard, focus, and accessibility annotations.
10. Component reuse/new-component inventory and implementation handoff notes.

Canonical outputs should be added under `docs/design/` and linked from the
ODP-UXD-003 Expansion workflow blueprint.

## 12. Design Acceptance Criteria

- A designer can trace every outcome from URL submission through Listing Inbox.
- New, exact duplicate, revision, possible match, and quarantine are visibly
  and behaviorally distinct.
- Users can verify source versus normalized versus corrected values.
- The design never implies continuous crawling or bypassing source controls.
- Unapproved retrieval reliably routes to assisted entry or governance review.
- Ambiguous records cannot be merged without an explicit human decision.
- Source, freshness, parser version, match evidence, decision reason, and audit
  history remain visible at the point of decision.
- Engineering can implement ODP-EXT-002 without inventing workflow states,
  actions, permissions, responsive behavior, or error handling.

## 13. Engineering Reference

- UI / visual design team handoff:
  `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_HANDOFF_REQUIREMENTS.md`
- R5 Fleet implementation addendum:
  `docs/evidence/fleet_dispatch/ODP-EXT-002-R5-ADDENDUM.md`
- Historical ingestion contract:
  `docs/evidence/fleet_dispatch/ODP-EXT-002.md`
- Existing formal screen:
  `UX-SCR-EXP-003 Listing Inbox`
- Existing downstream flow:
  `Listing -> Candidate Site -> SiteScore -> Approval`

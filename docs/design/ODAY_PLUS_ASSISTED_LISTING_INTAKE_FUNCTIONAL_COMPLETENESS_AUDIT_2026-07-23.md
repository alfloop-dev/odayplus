---
doc_id: ODP-INTAKE-FUNCTIONAL-AUDIT-001
title: ODay Plus Assisted Listing Intake Functional Completeness Audit
version: 1.0.0
status: changes-required
owner: Product Platform Engineering
audited_requirement: ODP-UXD-003-ADD-002
requirement_version: 1.0.1
audited_commit: eb94d53b0d1af9beb1cf6b290ce5e6b4d98b6585
supersedes_completion_claims:
  - ODP-INTAKE-UX-001
  - ODP-INTAKE-UX-FND-001
  - ODP-INTAKE-UX-INBOX-001
  - ODP-INTAKE-UX-DETAIL-001
  - ODP-INTAKE-UX-REVIEW-001
  - ODP-INTAKE-UX-MATCH-001
  - ODP-INTAKE-UX-ASSIGN-001
  - ODP-INTAKE-UX-PROMOTION-001
  - ODP-INTAKE-UX-QA-001
updated_at: 2026-07-23
---

# ODay Plus Assisted Listing Intake Functional Completeness Audit

## 1. Decision

The Assisted Listing Intake implementation at the audited commit is
`FUNCTIONALLY_INCOMPLETE`.

This decision is based on the complete UI and Visual Design Handoff
Requirements, not on security, rollout, or infrastructure gates. Existing
completion evidence proves that selected components and eight curated E2E
scenarios execute. It does not prove that every required route, role, field
lineage, identity decision, receipt, lifecycle, error, or audit surface is
reachable and backed by authoritative server behavior.

Previous task completion claims are not accepted as functional closure. They
remain historical implementation evidence only.

## 2. Audit Method

An item is complete only when all of the following are true:

1. the required UI is reachable from the production route;
2. the UI uses the authoritative API and persisted server state;
3. the server produces the required domain effect;
4. navigation, reload, conflict, retry, and role variants work;
5. an E2E test proves the user-visible result and persisted effect;
6. no fixture, dead component, client-generated receipt, or unit test is used
   as a substitute for the production behavior.

The following do not prove completion:

- a component file exists but has no production caller;
- a component unit test passes while the component is not mounted;
- an E2E test uses `/operator?ws=network` when the required durable route does
  not exist;
- a synchronous fixture replay is labelled as a real processing lifecycle;
- a client fabricates receipt IDs, hashes, verification, or WORM state;
- a revision operation directly overwrites the current Listing;
- a redirect back to an Inbox dialog is labelled as a durable detail page.

## 3. Executive Functional Findings

### FCF-001 - Required durable detail route does not exist

Requirement:

`/w/expansion/listings/intake/:intakeId` must be a durable page. Full compare,
identity decision, quarantine release, and promotion review must run there.

Observed:

- `apps/web/src/app/w/expansion/listings/` contains only the Inbox page,
  loading, and error files.
- `apps/web/src/app/intake/[intakeId]/page.tsx` redirects to
  `/w/expansion/listings?selected=<id>&dialog=detail`.
- the production experience mounts `IntakeDetailDialog`, not the full
  `IntakeProcessingDetail` page composition.

Disposition: `NOT_IMPLEMENTED`.

### FCF-002 - Revision does not append ListingRevision

Requirement:

The `REVISION` outcome must preview changed fields and append an immutable
ListingRevision.

Observed:

`NetworkListingService.decide_intake(... action="revise")` directly mutates
`rentPerMonth`, `areaPing`, and `floor` on the current Listing. It does not
create a ListingRevision record or expose a revision receipt.

Disposition: `CONTRACT_VIOLATION`.

### FCF-003 - Full identity comparison and reversible decision UI is unreachable

Requirement:

Desktop comparison must show current and submitted values, contradictory
signals, merge/split/unmerge/reversal plans, lineage effects, and independent
review.

Observed:

- `IdentityDecisionPanel`, `ListingCompareTable`, and `MatchEvidencePanel`
  exist and have unit coverage.
- no production caller mounts `IdentityDecisionPanel`.
- the mounted `MatchReview` shows signal label, verdict, and description only.
- normal `POSSIBLE_MATCH` decisions do not implement second-actor review.
- quarantine release and reject are not reachable from the production UI.

Disposition: `NOT_IMPLEMENTED`.

### FCF-004 - Durable receipt panel fabricates evidence

Requirement:

Success must display an authoritative decision or receipt ID, actor, time,
versions, audit ID, and correlation ID.

Observed:

`DurableReceiptPanel` constructs a client-side payload with:

- a fallback `CORR-<intake>` correlation ID;
- the constant SHA-256 of an empty payload;
- default `Verified Valid`;
- fallback `AUD-<intake>` and `AUD-DEC-99` audit IDs;
- fabricated `LISTING-<intake>` and `SITE-<target>` references;
- unconditional `SECURE WORM LOGGED`;
- export of the fabricated JSON.

Disposition: `P0_USER_MISREPRESENTATION`.

### FCF-005 - Processing lifecycle is synchronous fixture replay

Requirement:

The user must observe persisted processing stages, attempts, checkpoints, next
retry, cancellation, DLQ, and replay authority.

Observed:

- the production client calls the legacy operator intake endpoint without the
  async intake header;
- approved retrieval executes `RETRIEVING -> PARSING -> MATCHING -> terminal`
  inside the submit request;
- retrieval uses `fixture_replay`;
- only the final intake creation audit is persisted for that sequence;
- the UI reconstructs a stepper path from the final state;
- no polling or subscription refreshes intake and SiteScore progress.

Disposition: `PARTIAL_FIXTURE_ONLY`.

### FCF-006 - Required role modes are missing

Requirement:

Expansion staff, Expansion manager, Data steward, Governance reviewer, Privacy
officer, and permission-limited variants must be usable.

Observed:

- the console role model exposes no Expansion staff, Data steward, or Privacy
  officer role;
- `expansion-manager` receives all mutations;
- `pm-audit` is the only read-only variant;
- ordinary identity decisions cannot demonstrate segregation between proposer
  and reviewer.

Disposition: `NOT_IMPLEMENTED`.

### FCF-007 - Parsed field lineage is incomplete

Requirement:

Every field row must distinguish parsed, normalized, corrected, and effective
values plus missing, low-confidence, masked, actor, reason, time, snapshot, and
parser lineage.

Observed:

- the mounted table has source, normalized, and corrected columns only;
- field groups are not rendered;
- effective value, correction actor/time, snapshot, and parser lineage are
  absent from the client field type and mounted row;
- assisted-entry drafts are component state and are lost on close or reload.

Disposition: `PARTIAL`.

### FCF-008 - Audit history is not decision-grade

Requirement:

The audit surface must show action, reason, before/after, snapshot, parser run,
decision and related entity IDs, correlation, and evidence state.

Observed:

The mounted timeline renders only occurred time, actor name, actor role, and a
free-text message.

Disposition: `PARTIAL`.

## 4. Complete Requirement Trace

| Requirement | Result | Evidence and missing behavior |
|---|---|---|
| Section 1 assignment | PARTIAL | Intake screens exist, but identity, receipt, lifecycle, and audit are incomplete. |
| Section 2 authority | PASS | Claude Design is recorded as the canonical design package. |
| Section 3 experience outcomes | PARTIAL | Stage, policy, owner, and promotion can be read; complete lineage, identity reason, and authoritative receipt cannot. |
| Section 4 non-goals | PASS | No continuous crawl, automatic ambiguous merge, automatic promotion, credential prompt, or fake percentage is presented. |
| Section 5 role modes | FAIL | Only manager and read-only audit variants are reachable. |
| Section 6 routes | FAIL | The required durable intake detail page is absent. |
| Section 7 NEW flow | PARTIAL | Synthetic fixture creates a Listing; real approved retrieval lifecycle is not proven. |
| Section 7 EXACT_DUPLICATE flow | PARTIAL | Canonical URL is intercepted, but the UI reopens an Intake rather than the existing Listing. |
| Section 7 REVISION flow | FAIL | No true side-by-side preview; current Listing is overwritten instead of appending ListingRevision. |
| Section 7 POSSIBLE_MATCH flow | FAIL | No mounted full compare, reversible identity graph action, or independent identity review. |
| Section 7 ASSISTED_ENTRY_ONLY flow | PARTIAL | Required values and acknowledgement work; drafts are not durable across navigation/reload. |
| Section 7 promotion flow | PASS_WITH_GAP | Second actor, Candidate commit, SiteScore failure, and replay work; ongoing status does not auto-refresh. |
| Section 8.1 Inbox | PARTIAL | Search/method/stage/outcome work. Dedicated source, submitter, owner, date, restricted-data filters, true map, and direct claim/review/correction are missing. |
| Section 8.2 Add URL | PARTIAL | URL validation, canonicalization, policy preview, HeatZone, lock, and duplicate intercept work. Tenant/scope/owner context and existing Listing navigation are missing. |
| Section 8.3 Processing detail | PARTIAL | Summary, final state, evidence, decisions, and simple history exist. Persisted stage history, cancellation, next retry, DLQ, replay authority, and auto-refresh do not. |
| Section 8.4 Assisted entry and review | PARTIAL | Manual entry and correction work. Groups, effective values, complete lineage, and durable drafts do not. |
| Section 8.5 Compare | FAIL | Mounted UI is a signal list, not current-versus-submitted comparison or identity graph review. |
| Section 8.6 Assignment and SLA | PARTIAL | Claim/transfer/pause/resume and conflict exist. Assigned/claimed times, escalation history/action, queue truth, and full Inbox display do not. |
| Section 8.7 High-risk decision and receipt | FAIL | Reason/risk and non-optimistic calls exist. Dialog dismissal is not locked and the general receipt panel fabricates evidence. |
| Section 8.8 Candidate and SiteScore | PASS_WITH_GAP | Saga and replay are implemented. Polling/subscription and next-retry/timeout display remain missing. |
| Section 8.9 Audit and evidence | FAIL | Masking works. Structured audit, purpose-bound sensitive evidence, and authoritative WORM verification are absent from the mounted experience. |
| Section 9 canonical states | PARTIAL | Main states render. Intake CANCELLED, controlled quarantine reopening, complete decision lifecycle, timeout, next retry, and cancel controls are missing. |
| Section 10 errors and recovery | PARTIAL | Inline errors show code/correlation/time in selected cases. Required metadata and multiple named variants are not reachable. |
| Section 11 component use | PARTIAL | Required component files exist, but several are dead code; production UI uses hard-coded styles and text/emoji commands. |
| Section 12 responsive | PARTIAL | Basic viewport E2E passes. Full desktop comparison is absent, so its responsive behavior is unproven. |
| Section 13 accessibility | PARTIAL | Focus tests and live regions cover selected flows. Inbox is not a semantic table and destructive dialogs remain dismissible with Escape or overlay click. |
| Section 14 content | PARTIAL | Traditional Chinese and canonical codes are generally used. Timezone plus relative/absolute time and synthetic-only samples are incomplete. |
| Section 15 Claude Design deliverables | PARTIAL | Source and runnable package are archived. Archived runtime evidence still records mobile overflow and axe violations. |
| Section 16 design response | FAIL | It lacks a complete route/frame index, requirement matrix, component inventory, final review decisions, and exact implementation mapping. |
| Section 17 acceptance | FAIL | Six flows, five outcomes, field lineage, roles, errors, receipts, audit, and responsive comparison do not all pass. |
| Section 18 sources | PASS | Normative sources are present. |

## 5. Required Functional Closure

Functional completion requires all of the following:

1. the durable detail route exists and owns the complete workflow;
2. the production UI uses the canonical intake API and persisted asynchronous
   lifecycle, never a fixture replay path;
3. `REVISION` appends ListingRevision and proves it by API readback;
4. exact duplicate opens the existing Listing;
5. full identity comparison and merge/split/unmerge/reversal are mounted;
6. all six role and permission modes are reachable and tested;
7. parsed field lineage and assisted-entry drafts survive reload/conflict;
8. all receipts and verification fields come from server responses;
9. complete audit/evidence fields are rendered from persisted records;
10. assignment, SLA, decision, intake, promotion, and job states are complete;
11. every required error variant exposes the complete recovery contract;
12. the six canonical flows pass E2E on the required routes at desktop,
    tablet, and mobile boundaries;
13. dead components are either mounted or removed;
14. the completion audit proves every row in section 4 with exact evidence.

The execution authority for this closure is:

`docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_FUNCTIONAL_CLOSURE_EXECUTION_TASKS_2026-07-23.json`


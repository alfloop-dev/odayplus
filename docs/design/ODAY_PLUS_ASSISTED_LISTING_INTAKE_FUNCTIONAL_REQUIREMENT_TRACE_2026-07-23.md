---
doc_id: ODP-INTAKE-FUNCTIONAL-TRACE-001
title: ODay Plus Assisted Listing Intake Complete Functional Requirement Trace
version: 1.1.0
status: functionally-accepted
owner: Product Platform Engineering
source_requirement: ODP-UXD-003-ADD-002
source_requirement_version: 1.0.1
completion_audit: ODP-INTAKE-FUNCTIONAL-AUDIT-001
execution_plan: ODP-INTAKE-FUNCTIONAL-CLOSURE-EXEC-001
accepted_implementation_commit: 361d0c8e8457f8c3b969f28d34b3cd8217ab00a5
acceptance_task: ODP-INTAKE-FCL-ACCEPTANCE-001
acceptance_disposition: FUNCTIONALLY_COMPLETE
updated_at: 2026-07-23
---

# ODay Plus Assisted Listing Intake Complete Functional Requirement Trace

## 1. Purpose

This is the complete clause-level implementation and acceptance inventory for
`ODP-UXD-003-ADD-002` version `1.0.1`. It covers sections 1 through 18. It does
not reduce functional completion to security controls, component existence, or
curated screenshots.

Every row is independently accepted only when the production route is
reachable, its authoritative API and persisted domain effect are proven, reload
and conflict behavior are correct where applicable, and the listed browser or
runtime evidence passes at the exact integrated commit.

Allowed row results are `PASS`, `FAIL`, or `NOT_APPLICABLE` with an approved
reason. `PARTIAL`, a component-only test, a fixture, or an unmounted screen
cannot close a row. Independent acceptance at
`361d0c8e8457f8c3b969f28d34b3cd8217ab00a5` closed all 197 rows as `PASS`.
The row-level decision and evidence authority is
`docs/evidence/completion/ODP-INTAKE-FCL-ACCEPTANCE-001/ACCEPTANCE_MATRIX.json`.

## 2. Product Scope and Authority

| ID | Requirement | Completion test | Required evidence |
|---|---|---|---|
| `FTR-001` | Complete Assisted Intake from Inbox through audit evidence is implemented in Expansion Workspace. | All routes and six core flows complete from production AppShell. | Browser E2E + route manifest. |
| `FTR-002` | Desktop, tablet, and mobile compositions are production reachable. | Required capability works at 1440, 1024, and 390 CSS px. | Browser E2E + screenshots. |
| `FTR-003` | Canonical English state/error codes coexist with final zh-TW copy. | Codes survive render, reload, error, and receipt states. | Browser assertions. |
| `FTR-004` | Claude Design Package 10 is the canonical visual source; Figma is not required. | Archived source, runnable artifact, checksums, and review are linked. | Archive manifest + Review 003. |
| `FTR-005` | Approved system state, authorization, API, persistence, and event contracts are not redefined by UI. | Production requests and responses conform to registered contracts. | OpenAPI checks + contract tests. |
| `FTR-006` | Fixture/mock/legacy UI cannot override canonical runtime facts. | No production command or receipt falls back to fixture data. | Import/code audit + E2E network log. |

## 3. Experience Questions

| ID | User must be able to answer | Completion test |
|---|---|---|
| `FTR-007` | What real processing stage is this intake in? | Current state and persisted stage history are visible after reload. |
| `FTR-008` | May the system retrieve this source, and why? | Policy state, version/expiry, reason, and next action are visible. |
| `FTR-009` | How do parsed, normalized, corrected, and effective values differ? | All four values and lineage render in each applicable field row. |
| `FTR-010` | Why is this NEW, duplicate, revision, or possible match? | Confidence, evidence, contradictions, and recommendation render. |
| `FTR-011` | Who owns it, when is it due, and is SLA escalated? | Owner, queue, timestamps, due time, SLA state/history render. |
| `FTR-012` | What may this actor do and what needs a second actor? | Allowed/denied actions and exact reason code render per role. |
| `FTR-013` | What durable result and evidence did the action create? | Server receipt, versions, audit/correlation and entity IDs survive reload. |

## 4. Prohibited Product Behavior

| ID | Prohibition | Completion test |
|---|---|---|
| `FTR-014` | No continuous crawling, result-page scraping, or external ID enumeration. | UI copy and production job triggers only describe user submission/approved feed. |
| `FTR-015` | No provider passwords, cookies, bearer tokens, credentials, or private endpoints. | Forms, preserved input, evidence, and exports contain none. |
| `FTR-016` | No automatic `POSSIBLE_MATCH` merge. | A human proposal and independent review are required. |
| `FTR-017` | No automatic Candidate Site promotion. | Explicit proposal/review/execute receipt is required. |
| `FTR-018` | No fake processing percentage or inferred stage path. | Only persisted state/history is rendered. |
| `FTR-019` | No claim that AI guarantees identity. | Recommendation and human decision remain visibly distinct. |
| `FTR-020` | No redesign of unrelated HeatZone, Candidate, SiteScore, or AppShell surfaces. | Diff and route review show scoped integration. |

## 5. Role-Aware Functional Modes

| ID | Role/mode | Required production behavior | Proof |
|---|---|---|---|
| `FTR-021` | Expansion staff | Submit, assisted entry, correct own/assigned intake, propose identity/promotion; cannot self-approve. | Role E2E + backend readback. |
| `FTR-022` | Expansion manager | Assign, compare, decide, independently review, and promote within managed scope. | Role E2E. |
| `FTR-023` | Data steward | Source/data corrections, parser/mapping/identity work, quarantine; independent graph review remains required. | Role E2E. |
| `FTR-024` | Governance reviewer | Read-only source, permission, processing, and audit evidence; no business mutation. | Role E2E. |
| `FTR-025` | Privacy officer | Purpose-bound restricted evidence, hold/export review, classification and expiry visibility. | Role E2E. |
| `FTR-026` | Permission-limited | Structural read-only UI with field masking and no value inference. | Role E2E. |
| `FTR-027` | Backend denial integration | Hidden/disabled actions never substitute for backend authorization; reason code is rendered. | Six-role request/response matrix. |
| `FTR-028` | Role switch | Same durable route and state remain; authoritative permissions reload for stable actor subject. | Back/forward/reload E2E. |

## 6. Routes and Navigation

| ID | Surface | Required behavior | Proof |
|---|---|---|---|
| `FTR-029` | Listing Inbox | `/w/expansion/listings`; query, sort, view, cursor, selection are URL-restorable. | Direct-open/back-forward E2E. |
| `FTR-030` | Add URL | Inbox modal validates and submits, then opens authoritative intake or existing Listing target. | Browser + API log. |
| `FTR-031` | Processing Detail | `/w/expansion/listings/intake/:intakeId` is a real durable page, not an Inbox-dialog redirect. | Route manifest + reload E2E. |
| `FTR-032` | Parsed Review | Active review section is deep-linkable and reloadable. | URL/reload E2E. |
| `FTR-033` | Duplicate/Revision Compare | Desktop compare/task target deep link opens directly. | Browser E2E. |
| `FTR-034` | Assisted Entry | Draft form can be left and restored at the same intake. | Reload E2E + draft API readback. |
| `FTR-035` | Promotion/SiteScore | Promotion, candidate, job, and receipts remain reachable after state changes. | Reload/poll E2E. |
| `FTR-036` | Existing Listing | Exact duplicate opens `/w/expansion/listings/:listingId` backed by Listing API. | Navigation + Listing readback E2E. |
| `FTR-037` | Preview drawer | Drawer is preview/claim only; complete high-risk workflows open durable detail. | Import graph + browser E2E. |
| `FTR-038` | External source | Opens with destination/new-window indication and does not lose intake state. | Browser E2E. |

## 7. Core End-to-End Flows

| ID | Flow | Persisted completion effect | Proof |
|---|---|---|---|
| `FTR-039` | NEW | Approved user URL is queued, parsed, matched, human-confirmed, and creates Listing. | Worker + browser + Listing readback. |
| `FTR-040` | EXACT_DUPLICATE | Identity short-circuits retrieval and opens authoritative existing Listing. | Browser + no-retrieval assertion. |
| `FTR-041` | REVISION | Side-by-side changes append immutable `ListingRevision`; current Listing projects the new revision. | Browser + revision repository/API readback. |
| `FTR-042` | POSSIBLE_MATCH | Compare, proposal, independent review, and create/revise/duplicate/quarantine outcome complete. | Two-actor browser + identity-edge readback. |
| `FTR-043` | ASSISTED_ENTRY_ONLY | No retrieval occurs; durable manual entry and correction review continue to matching. | Browser + worker/API assertion. |
| `FTR-044` | Promotion/SiteScore | Proposal, second actor, Candidate commit, SiteScore queue/completion, `SCORE_FAILED`, and replay complete. | Browser + candidate/job/report readback. |

## 8.1 Listing Inbox

| ID | Requirement | Completion test |
|---|---|---|
| `FTR-045` | Existing list/map toggle remains available. | Both modes use the same authoritative result set and restore from URL. |
| `FTR-046` | Filters cover method, stage, outcome, source, submitter, owner/assignment, review, SLA, observed/updated, HeatZone/area, restricted, quarantined, failed, retryable. | Each filter reaches server query and changes returned rows. |
| `FTR-047` | Saved views are authoritative and URL-restorable. | Create/select/reload uses persisted saved-view ID. |
| `FTR-048` | Server pagination, opaque cursor, stable ordering, counts, and selection work. | Next/previous/back-forward E2E with request assertions. |
| `FTR-049` | Semantic table includes every required operational column. | Header/row semantics, `aria-sort`, and content assertions pass. |
| `FTR-050` | Map uses authoritative coordinates; unlocated records are explicit. | Coordinate/API comparison and map screenshot pass. |
| `FTR-051` | Direct open, claim, review, retry, and request-correction actions work. | Navigation actions open the correct durable workflow; claim/retry and the correction submitted from that workflow change persisted state and survive reload. |
| `FTR-052` | Empty, loading, partial/degraded, error, read-only, and no-results states exist. | State-specific browser/component tests pass. |
| `FTR-053` | Sensitive location/commercial columns obey masking. | Permission-limited E2E reveals no masked value. |

## 8.2 Add Listing From URL

| ID | Requirement | Completion test |
|---|---|---|
| `FTR-054` | URL syntax validation and source detection. | Invalid/valid/unsupported variants are reachable. |
| `FTR-055` | Original URL remains visible; canonical URL appears separately when changed. | Submission and detail readback match server values. |
| `FTR-056` | Optional HeatZone/area and submitter/tenant/scope/owner context. | Bootstrap facts render and are sent without client fabrication. |
| `FTR-057` | Operational source-policy expectation. | Copy states what the server will do without crawl/credential implication. |
| `FTR-058` | Request lock prevents accidental double submit. | Repeated click creates one durable intake and replays one receipt. |
| `FTR-059` | Variants: invalid, unsupported, exact duplicate, canonical difference, denied, in-flight, network retry, success. | Browser matrix passes. |
| `FTR-060` | Success uses durable receipt/navigation, not toast-only confirmation. | Reload at returned target shows same receipt. |

## 8.3 Processing Detail

| ID | Region/behavior | Completion test |
|---|---|---|
| `FTR-061` | Summary: source, URLs, submitter, owner, time, scope. | Authoritative detail fields render. |
| `FTR-062` | Status: state, assignment/SLA, retryability, freshness. | Server state/history render after poll/reload. |
| `FTR-063` | Evidence: snapshot/parser/confidence/match evidence/contradictions. | Evidence references match API. |
| `FTR-064` | Recommendation is labelled system-generated. | Human decision is a separate surface and event. |
| `FTR-065` | Allowed decisions, second actor, reason/risk requirements. | Role/second-actor matrix passes. |
| `FTR-066` | Listing revision, Candidate, SiteScore, and receipts. | IDs appear only after committed response and survive reload. |
| `FTR-067` | Version/audit: ETag/version, actor, time, reason, before/after, correlation. | Conflict and receipt E2E pass. |
| `FTR-068` | Real timeline includes checkpoint, attempt, next retry, cancel, DLQ, replay authority. | Worker/browser lifecycle tests pass. |
| `FTR-069` | Automatic refresh/poll reflects server changes without optimistic mutation. | Browser observes worker transition without page reload. |

## 8.4 Assisted Entry and Parsed Review

| ID | Requirement | Completion test |
|---|---|---|
| `FTR-070` | Identity, Location, Commercial, Property, Provenance groups. | All groups render from canonical field schema. |
| `FTR-071` | Parsed/source, normalized, corrected, and effective values. | Four-value row comparison matches readback. |
| `FTR-072` | Missing, low-confidence, and masked variants. | Each variant is text/icon/pattern distinguishable. |
| `FTR-073` | Correction actor, reason, time, snapshot, parser, supersession lineage. | Complete correction chain renders after reload. |
| `FTR-074` | Material fields require reason, risk, and independent review. | Two-actor correction E2E passes. |
| `FTR-075` | Draft survives close, reload, network failure, conflict, and retry. | Draft API and browser restoration pass. |
| `FTR-076` | Draft is not authoritative until commit/review. | Effective value remains unchanged before approval. |
| `FTR-077` | Assisted-only mode never retrieves or asks for credentials. | Worker call assertion + form audit pass. |

## 8.5 Compare and Identity Resolution

| ID | Requirement | Completion test |
|---|---|---|
| `FTR-078` | Desktop current-versus-submitted compare. | Both sides render authoritative values. |
| `FTR-079` | Changed fields use text and icon/pattern plus screen-reader summary. | Visual and accessibility assertions pass. |
| `FTR-080` | Source ID, canonical URL, address, area, floor, type, rent/price, status are compared. | All rows are present or explicitly unavailable. |
| `FTR-081` | Confidence, agreeing signals, and contradictions are named. | Match API-to-UI mapping passes. |
| `FTR-082` | NEW, EXACT_DUPLICATE, REVISION, POSSIBLE_MATCH, QUARANTINED are distinct. | Outcome matrix screenshots/assertions pass. |
| `FTR-083` | Create, append revision, duplicate, steward, reject, and quarantine actions are explicit. | Each permitted command has a persisted effect. |
| `FTR-084` | Merge/split/unmerge/reversal show graph before/after, redirects, lineage, candidate impact, reason/risk, proposer/reviewer. | Graph-plan and receipt E2E pass. |
| `FTR-085` | Identity self-review is unavailable and returns `SELF_REVIEW_DENIED`. | Same-actor denial and second-actor success pass. |

## 8.6 Assignment and SLA

| ID | Requirement | Completion test |
|---|---|---|
| `FTR-086` | Owner, queue, assigned/claimed time, due time, SLA state are visible in Inbox and Detail. | API/UI readback matches. |
| `FTR-087` | Assignment states: UNASSIGNED, ASSIGNED, CLAIMED, TRANSFERRED, ESCALATED, COMPLETED. | State transition matrix passes. |
| `FTR-088` | SLA states: ON_TRACK, DUE_SOON, OVERDUE, BREACHED, PAUSED, COMPLETED. | State presentation matrix passes. |
| `FTR-089` | Claim, transfer, pause/resume, escalation, and completion commands work. | Browser + persisted history readback. |
| `FTR-090` | Transfer requires handoff note. | Validation and receipt assertions pass. |
| `FTR-091` | Pause requires approved reason and resume time. | Validation, timer, and history assertions pass. |
| `FTR-092` | Overdue/breach is not color-only. | Text/icon accessibility assertion passes. |
| `FTR-093` | Owner/version conflict shows server owner/version and preserves draft. | 409 refresh/resubmit E2E passes. |

## 8.7 High-Risk Actions and Durable Receipts

| ID | Requirement | Completion test |
|---|---|---|
| `FTR-094` | Recommendation and human decision are separate. | Distinct UI and audit events. |
| `FTR-095` | Before action: affected entities, before/after, risk, reason, reviewer. | High-risk dialog matrix passes. |
| `FTR-096` | Self-review action unavailable with exact reason code. | Same-actor E2E. |
| `FTR-097` | Submission lock; no optimistic update. | UI changes only after authoritative response. |
| `FTR-098` | Conflict preserves input and shows current server state/version. | 409 E2E. |
| `FTR-099` | Success receipt contains server ID, actor, time, versions, audit/correlation. | Reload and API readback match. |
| `FTR-100` | No client-generated ID, hash, verification, WORM, or related entity fact. | Code audit and anti-fabrication tests pass. |

## 8.8 Candidate Promotion and SiteScore

| ID | Requirement | Completion test |
|---|---|---|
| `FTR-101` | REQUESTED, VALIDATING, APPROVED, CANDIDATE_CREATING, CANDIDATE_CREATED, SCORE_QUEUED, COMPLETED remain distinct. | Poll/reload state matrix passes. |
| `FTR-102` | REJECTED, FAILED, and SCORE_FAILED are visible. | Failure matrix passes. |
| `FTR-103` | Candidate/SiteScore IDs appear only after transaction commit. | Lost-response test proves no premature ID. |
| `FTR-104` | SCORE_FAILED keeps Candidate visible. | Candidate readback and UI assertion pass. |
| `FTR-105` | Authorized SiteScore replay works from persisted checkpoint. | Replay creates no duplicate Candidate. |
| `FTR-106` | Lost response recovers by same idempotency key or decision lookup. | Idempotency replay E2E passes. |
| `FTR-107` | Job timeout, attempt, next retry, queue, cancellation, DLQ, replay permission render. | Job lifecycle matrix passes. |

## 8.9 Audit, Evidence, and Sensitive Fields

| ID | Requirement | Completion test |
|---|---|---|
| `FTR-108` | Audit row includes actor/role/time/action/reason/before-after. | Structured audit readback matches. |
| `FTR-109` | Snapshot, parser, decision, Listing/Candidate/SiteScore, correlation, version, evidence state render. | Related-ID audit assertions pass. |
| `FTR-110` | Masked field keeps label/layout and reveals no value or inference. | Permission-limited E2E. |
| `FTR-111` | Sensitive evidence shows purpose, classification, expiry, and audit notice. | Privacy/governance role E2E. |
| `FTR-112` | Legal hold/export/verification state is server-supplied. | Evidence API/UI readback. |
| `FTR-113` | Credential-class data never appears in UI, preserved input, sample, or export. | Content and export scan. |

## 9. Canonical State Coverage

| ID | State family | Required complete set | Completion test |
|---|---|---|---|
| `FTR-114` | Intake | SUBMITTED, CHECKING_IDENTITY, CHECKING_SOURCE_POLICY, AWAITING_ASSISTED_ENTRY, RETRIEVING, PARSING, MATCHING, NEEDS_REVIEW, READY, QUARANTINED, FAILED, CANCELLED. | API/UI matrix for all 12. |
| `FTR-115` | Reopen semantics | FAILED retries from checkpoint; QUARANTINED uses proposal plus second actor; CANCELLED is terminal. | Browser + persisted readback. |
| `FTR-116` | Source policy | APPROVED_RETRIEVAL, ASSISTED_ENTRY_ONLY, AUTH_REQUIRED, SOURCE_BLOCKED, POLICY_UNKNOWN. | UI/next-action matrix. |
| `FTR-117` | Decision | DRAFT, PENDING_REVIEW, APPROVED, REJECTED, EXECUTING, EXECUTED, FAILED, REVERSAL_PENDING, REVERSED, SUPERSEDED. | API/UI matrix. |
| `FTR-118` | Job | QUEUED, RUNNING, RETRYING, SUCCEEDED, FAILED, CANCELLED, DEAD_LETTER with attempt/timeout/checkpoint/next retry/permission. | Worker/UI matrix. |
| `FTR-119` | Presentation | Every state uses text and icon/pattern; English canonical code remains visible. | Accessibility/visual assertion. |

## 10. Error, Conflict, and Recovery

| ID | Requirement | Completion test |
|---|---|---|
| `FTR-120` | Every error shows summary, exact code, correlation, time, retryability, current state/version, operation, next action. | Error-family browser matrix. |
| `FTR-121` | Conflict additionally shows server value/version and preserves user input. | Conflict E2E. |
| `FTR-122` | 428 PRECONDITION_REQUIRED. | Browser/API variant passes. |
| `FTR-123` | 409 VERSION_CONFLICT and IDEMPOTENCY_KEY_REUSED. | Browser/API variants pass. |
| `FTR-124` | 409 OWNER_CONFLICT, REVIEW_CONFLICT, WORK_INCOMPLETE, LEGAL_HOLD_CONFLICT. | Browser/API variants pass. |
| `FTR-125` | 403 SELF_REVIEW_DENIED, SOURCE_POLICY_DENIED, scope and ownership denial. | Browser/API variants pass. |
| `FTR-126` | 422 CORRECTION_INVALID and RISK_ACKNOWLEDGEMENT_REQUIRED. | Browser/API variants pass. |
| `FTR-127` | Retrieval timeout, page removed, auth wall, bot challenge. | Worker/error UI variants pass. |
| `FTR-128` | Parser partial, retryable, and permanent failure. | Worker/error UI variants pass. |
| `FTR-129` | Stale snapshot, quarantine, retry exhausted, and DLQ. | Worker/browser variants pass. |
| `FTR-130` | Major errors remain on page; toast is never the only recovery surface. | Browser persistence assertion. |

## 11. Production UI Composition

| ID | Requirement | Completion test |
|---|---|---|
| `FTR-131` | Dense operational composition, compact Inbox, comfortable detail/compare. | Desktop visual review. |
| `FTR-132` | Existing design tokens and shared components are reused; no intake-only color state system. | CSS/component inventory audit. |
| `FTR-133` | No nested cards or section-as-floating-card composition. | DOM/CSS review. |
| `FTR-134` | Fixed toolbar/table/compare dimensions avoid state-change layout shift. | Browser layout assertions. |
| `FTR-135` | Familiar icons have accessible names/tooltips; no emoji-only command. | DOM/a11y audit. |
| `FTR-136` | High-risk buttons use explicit verbs. | Copy audit. |
| `FTR-137` | IntakeStageTimeline, FieldLineageRow, ListingCompareTable, MatchEvidencePanel, AssignmentSlaSummary, DurableReceiptPanel, MaskedField have variant/a11y ownership or documented reuse. | Component inventory. |

## 12. Responsive

| ID | Requirement | Completion test |
|---|---|---|
| `FTR-138` | Desktop supports full Inbox, compare, correction, identity, promotion, audit. | 1440 E2E. |
| `FTR-139` | Tablet supports submit, status/detail, assisted entry, assignment, unambiguous review/approval. | 1024 E2E. |
| `FTR-140` | Mobile supports submit, status, simple confirmation, task claim/response, receipt. | 390 E2E. |
| `FTR-141` | Complex mobile comparison/restricted evidence uses durable desktop-required fallback and preserves state/draft. | 390 E2E + reload. |
| `FTR-142` | Long URL/address/code/correlation wraps or truncates with accessible full value and no overlap. | Overflow assertions/screenshots. |
| `FTR-143` | No page-level horizontal overflow at required viewports. | Pixel/scroll-width assertions. |

## 13. Accessibility

| ID | Requirement | Completion test |
|---|---|---|
| `FTR-144` | WCAG 2.2 AA contrast baseline. | Automated + sampled contrast evidence. |
| `FTR-145` | Complete keyboard operation, logical focus order, trap, return, and skip behavior. | Keyboard browser E2E. |
| `FTR-146` | Semantic table, row focus, headers, and `aria-sort`. | Axe + DOM assertions. |
| `FTR-147` | Compare has screen-reader change summary. | Accessibility assertion. |
| `FTR-148` | State/risk/masking/confidence/SLA are never color-only. | DOM/visual assertion. |
| `FTR-149` | Error summary is focusable and links field errors; dynamic updates use appropriate live region. | Keyboard/screen-reader assertion. |
| `FTR-150` | Modal/drawer focus returns; destructive submit cannot be dismissed while busy. | Browser E2E. |
| `FTR-151` | Reduced motion removes nonessential animation. | Emulated-media browser E2E. |
| `FTR-152` | External links identify destination/new window and preserve intake. | Browser E2E. |
| `FTR-153` | Axe reports zero serious or critical violations on core routes/states. | Axe browser run. |

## 14. Content and Samples

| ID | Requirement | Completion test |
|---|---|---|
| `FTR-154` | zh-TW copy plus canonical English code. | Copy scan. |
| `FTR-155` | Timezone is shown; relative time has absolute timestamp. | Browser assertion. |
| `FTR-156` | Samples use example.com/approved synthetic provider and fictitious Taiwan facts. | Content scan. |
| `FTR-157` | No real PII or credential in prototype/test/sample data. | Repository scan. |
| `FTR-158` | No crawl-success, whole-site automation, AI-decided, or 100%-match wording. | Copy scan. |
| `FTR-159` | Copy describes state/next action, not marketing/tutorial promotion. | Copy review. |
| `FTR-160` | robots.txt is not described as retrieval authorization. | Copy scan. |

## 15. Claude Design Package Deliverables

| ID | Deliverable | Acceptance evidence |
|---|---|---|
| `FTR-161` | Cover/scope/source/version/owner. | Archived Package 10 source. |
| `FTR-162` | Desktop/mobile six-flow map. | Package node/screen index. |
| `FTR-163` | Inbox integration and preserved list/map. | Package screen index. |
| `FTR-164` | Desktop `003A` through `003F`. | Stable screen labels. |
| `FTR-165` | Tablet/mobile and desktop-required fallback. | Package/screenshots. |
| `FTR-166` | Intake/policy/match/assignment/SLA/decision/promotion/job matrices. | Package state index. |
| `FTR-167` | Empty/loading/partial/error/permission/masked/stale/quarantined variants. | Package state index. |
| `FTR-168` | URL/correction/compare/high-risk/receipt interaction. | Runnable prototype. |
| `FTR-169` | Existing/new component inventory and properties. | Design response. |
| `FTR-170` | Keyboard/focus/screen-reader/contrast/reduced-motion annotations. | Design response/review. |
| `FTR-171` | Copy sheet. | Design response. |
| `FTR-172` | Engineering measurements, responsive/overflow/sticky/URL/test mapping. | Design response/handoff. |
| `FTR-173` | Source and standalone checksums match archived files. | SHA-256 manifest. |

## 16. Design Response

| ID | Required response content | Completion test |
|---|---|---|
| `FTR-174` | Claude source/runnable/version/checksum links. | Links resolve to archive. |
| `FTR-175` | Route/screen/state index. | Every production route/state maps to design screen. |
| `FTR-176` | Accepted/modified requirement matrix. | Every FTR row has a disposition or reference. |
| `FTR-177` | Component reuse/new decisions. | Production component inventory maps to design. |
| `FTR-178` | Responsive and accessibility decisions. | Production test mapping is present. |
| `FTR-179` | Unresolved dependency and fail-closed behavior. | No undeclared defer. |
| `FTR-180` | Final copy source. | Production copy maps to approved source or recorded modification. |
| `FTR-181` | Product/System Design/Frontend/Accessibility reviewers. | Named review status is truthful; pending is not presented as approved. |

## 17. Final Acceptance

| ID | Acceptance criterion | Required final proof |
|---|---|---|
| `FTR-182` | Six core flows complete from Inbox to durable receipt and back. | Full browser suite. |
| `FTR-183` | Five outcomes are visually and behaviorally distinct. | Browser outcome matrix. |
| `FTR-184` | Parsed/normalized/corrected/effective/confidence/masking are jointly readable. | Field-lineage E2E. |
| `FTR-185` | All canonical state families are implemented. | State matrix. |
| `FTR-186` | High-risk actions show effect/risk/reason/second actor/receipt. | Two-actor browser matrix. |
| `FTR-187` | Possible match and promotion are never automatic. | Audit/domain readback. |
| `FTR-188` | Permission, self-review, masking, purpose, and read-only modes pass. | Six-role E2E. |
| `FTR-189` | Errors expose full recovery contract. | Error-family E2E. |
| `FTR-190` | Desktop/tablet/mobile have no overlap/overflow and complex fallback is clear. | Three-viewport evidence. |
| `FTR-191` | Color-independent, keyboard, focus, screen-reader requirements pass. | Axe + keyboard suite. |
| `FTR-192` | Existing AppShell/tokens/contracts are preserved; no invented domain state. | Code/design review. |
| `FTR-193` | Frontend behavior is fully specified and testable without guessing. | Independent acceptance review. |

## 18. Source Integrity and Closure Rule

| ID | Requirement | Completion test |
|---|---|---|
| `FTR-194` | Product, system response/review/manifest, state, authorization, reliability, migration, OpenAPI overlays, workflow, visual system, tokens, and component contracts remain linked and present. | Source-path verification. |
| `FTR-195` | Exact integrated commit, request/response, persisted readback, test, and screenshot evidence are recorded. | Integration verification manifest. |
| `FTR-196` | A Fleet that did not author the integrated implementation reruns and signs every row. | Acceptance Fleet report. |
| `FTR-197` | Functional status changes to `FUNCTIONALLY_COMPLETE` only if `FTR-001` through `FTR-196` are `PASS` or approved `NOT_APPLICABLE`. | Machine-readable acceptance result. |

## 19. Execution Ownership

| Requirement range | Primary execution task |
|---|---|
| `FTR-001`–`FTR-038` | Shell, Roles, Inbox, Integration |
| `FTR-039`–`FTR-044` | Runtime, Review, Identity, Lifecycle, Integration |
| `FTR-045`–`FTR-060` | Inbox, Runtime, Integration |
| `FTR-061`–`FTR-077` | Shell, Review, Evidence, Lifecycle, Runtime |
| `FTR-078`–`FTR-100` | Identity, Evidence, Runtime, Integration |
| `FTR-101`–`FTR-130` | Lifecycle, Evidence, Runtime, Integration |
| `FTR-131`–`FTR-160` | Shell, Inbox, Review, Integration |
| `FTR-161`–`FTR-181` | Visual handoff archive and response reconciliation |
| `FTR-182`–`FTR-197` | Integration plus independent Acceptance Fleet |

No execution task may close itself by editing this trace. The independent
Acceptance Fleet owns the final result and must cite exact paths, tests, and
evidence for every row.

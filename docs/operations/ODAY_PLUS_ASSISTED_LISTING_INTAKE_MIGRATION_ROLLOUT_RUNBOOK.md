---
doc_id: ODP-SD-INTAKE-MIGRATION-001
title: ODay Plus Assisted Listing Intake Migration, Reconciliation, and Rollout Runbook
version: 1.0.0
status: proposed
owner: Data Platform / Expansion Engineering / Platform SRE
reviewers: System Design / Product / Security / QA / Release Authority
updated_at: 2026-07-17
---

# ODay Plus Assisted Listing Intake Migration, Reconciliation, and Rollout Runbook

Normative for `SDI-023` and `SDI-024`. This runbook migrates the `dev` implementation—where listing ingestion may directly create `CandidateSiteDraft` and uses memory/SQLite adapters—to the explicit Intake, Listing, Identity, Decision, Assignment, Promotion, Cloud SQL/GCS/Cloud Tasks/Pub/Sub contracts.

### Current implementation ownership

Read the actual entry point and its callers before changing this flow. The
following owners are maintained; the migration phases below govern their cutover.

| Responsibility | Existing owner and integration point |
|---|---|
| Operator URL/manual intake and decisions | `modules/opsboard/application/network_listings.py`, called by the existing operator API and UI |
| Operator field completeness and matching | `modules/external_data/application/assisted_intake.py`; both the operator owner and `apps/worker/assisted_listing_intake/worker.py` use these shared rules |
| Native Intake and parser | `apps/api/app/routes/canonical_intakes.py`, the `canonical_intake_*` factories in `shared/infrastructure/persistence/factory.py`, and the registered `oday_worker` handlers |
| Canonical match case persistence | `CanonicalMatchCaseRepository` in `shared/infrastructure/persistence/identity_cases_sql.py` |
| Canonical confirmation UI | Existing `CanonicalIntakeCasePicker` opens `CanonicalIdentityDecisionPanel`; `canonicalIntakeReadClient.ts` uses the account-bound Console transport for the existing planning, confirmation, decision and review APIs |
| Exact service approval and current evidence | `NativeIntakeIdentityOwner` reuses the case owner's parser/corpus evaluation and its existing identity graph proof; `CanonicalIdentityDecisionRepository` records the approved service decision |
| Approved identity execution | `IdentityExecutionService` and `CanonicalIdentityGraphRepository`; reuse their existing authority, version, job, audit and transaction boundaries |
| Service authentication | `ServiceIdentityVerifier` in `modules/opsboard/auth/service_identity.py`, through the existing `AuthenticationBoundary`; a signed job token alone grants no source use or business approval |
| Native target authorization projection | `native_intake_authorization_resource` in `modules/listing/application/intake_authorization.py`; the native Intake, case and identity-decision routers share target IDs and the existing Intake discriminator. Listing region comes from its actual effective Property; CandidateSite region comes from its explicitly bound Property, without reassignment. Other axes never come from the Intake. Each target retains its independent authorization check. |
| Native handoff and recovery | Existing `ODayScheduler.run_once` calls parser admission, `CanonicalMatchCaseRepository.match_partition` (also used by the parser CLI), and `NativeIntakeIdentityOwner.confirm_exact_partition`; canonical states and receipts retain progress |
| Scoped rollout policy and administration | `FeatureFlag` in `shared/auth/feature_flags.py` holds exact tenant or tenant/source units; `DurableFeatureFlagRegistry` retains one global CAS/audit owner. The existing admin routes register, configure scope, approve and enable that same record. Scope changes and explicit disable invalidate prior confirmation versions; they never reuse old approvals. |
| Release governance configuration | `modules/listing/application/intake_release_governance.py` is the shared owner of the existing governed YAML loader and release-authority/live-evidence checks, moved from the release-only config module. Runtime activation and the existing release harness use this same implementation; neither writes approvals. |
| Native target reads | The two canonical Intake GETs bind the same-engine flag owner/queue and pass `assisted_intake_v1_read` into the existing `authorize_intake_action` decision. Verified tenant eligibility is additional to current role/ownership/field scope. Missing/corrupt owner is unavailable; missing enrollment is disabled. This read flag does not select a writer or gate submission. |

The native parser can reach `MATCHING`. The canonical case and decision API now
support human NEW/REVISION proposals and manager confirmation of the exact
parser/field/resource versions. The existing signed `identity-execution` job
owns CREATE/REVISE persistence, approved field application and the final READY
transition in the same fenced SQL/audit/outbox transaction. Current SQL human
and manual-source authority are rechecked at positive execution boundaries;
MERGE/SPLIT retain independent review. Routine confirmation does not fabricate a
second reviewer. A DATA_STEWARD additionally needs a governed data-quality case;
ordinary NEW/REVISION currently requires a manager.

The existing case owner now accepts authenticated service matching commands and
uses the shared matcher to record proposals. The existing parser CLI exposes
this through `--match-completed --matching-policy-version-id VERSION`. Actual
service proposals, human NEW/REVISION confirmation and the registered executor
have engineering SQL coverage. Exact cases can be approved and admitted with
`native_intake_identity --match-cases --tenant-id UUID --source-id SOURCE
--matching-policy-version-id VERSION`. This uses the existing authenticated
identity service, decision owner, durable receipt and signed `identity-execution`
job. The registered executor resolves the Intake to the existing Listing without
changing Listings, Properties, revisions, edges or human field proposals.
Automatic handoff is opt-in through `matching_policy_version_id` on each source
in the existing `ODP_NATIVE_INTAKE_PARSER_DEPLOYMENTS_JSON` binding. The scheduler
must have the same approved local parser archives and existing service
credentials as the workers. A reference never grants source use or approves a
policy: the existing owners recheck actual current SQL authority at every
positive boundary. Each tick admits at most 25 parsers, matching proposals and
exact confirmations per configured source. Human NEW/REVISION/POSSIBLE_MATCH
continues through the existing confirmation API. Independent phases retain
durable receipts; a later failure returns an unsuccessful scheduler receipt
with class-only errors and partial progress, never rewrites a successful parser.
Idle native ticks also report their actual scan result. No second queue,
checkpoint table, service registry or execution path is introduced.

The same opt-in tick now also calls the existing tenant owners for native
submission starts, identity checks, source-policy rechecks and checks,
retrieval admission, and human assisted-entry admission. Each consumes its
actual existing event/request and retains its own explicit startup policy,
current authority and bounded transaction. All corresponding runtime settings
must be configured; a missing setting fails its phase rather than creating a
capability or silently declaring completion. These tenant phases run once per
tick, before source-bound parser/matching/exact phases. Existing partition
progress and conflicts are retained in the same receipt.

The existing canonical case picker now exposes native CREATE/REVISE and human
DUPLICATE proposals. It displays the actual parser fields and the same observed
resource plan submitted to the existing confirmation API. The case read also
returns its bounded, tenant/case-scoped `identity_decision_ids`; another operator
can explicitly select a recorded decision and use the existing review API.
Human duplicate approval reads the current unmasked parser input and checks its
recorded references before enabling submission; rejection does not require this
positive source read. Actual role, source, segregation, version and execution
authorization remain server checks. An uncertain response retains the same
command, key and versions for retry. Account/transport changes unmount old data;
busy commands block dialog dismissal and selection changes. Only a validated
server receipt is shown, and enqueue is never displayed as completed execution.

Native URL submission now reuses `AddListingFromUrlDialog` in this picker,
using explicit unscoped selection or the actual scopes of readable Intakes.
Legacy HeatZone labels are never converted into native UUIDs. The actual
submission receipt reports SUBMITTED only. A URL Intake in
AWAITING_ASSISTED_ENTRY uses the existing `AssistedEntryForm` for typed human
proposals, reason and the displayed risk acknowledgement. Its REQUESTED
receipt never claims parsing/matching is complete. Authorization wait exposes
no manual form; a separately approved manual-source path must first be
classified by the existing owner. URL, entry and identity commands share
`useCanonicalIntakeCommand`; the old decision-local retry lifecycle was removed.
The transport uses the same account-bound client and existing endpoints.

The same picker now exposes human MERGE/SPLIT proposals for a pending canonical
review case. It requests the existing graph-only planning context; no parser
result, confidence, target UUID or version is invented. Humans explicitly
choose observed active source/target Properties, assign every source edge to
nonempty split partitions, and choose a disposition for every affected
CandidateSite. The plan binds the complete observed Listing closure of all
affected Properties and the exact edges of source Properties. The displayed
plan is submitted through the same captured-command lifecycle and proposal
API; a PENDING_REVIEW receipt never means merge/split has executed. Existing
recorded-decision review and the signed identity executor remain the owners.
The current graph executor does not apply pending parser fields or advance a
native Intake to READY. The existing confirmation owner now permits a fresh
human CREATE/REVISE/DUPLICATE decision only after proving the current case's
completed independently reviewed graph execution, durable result, immutable
audit and match.decided event. The old graph decision stays EXECUTED. The
original parser proposals and current human/source/field/resource authority
are checked again; only the existing identity executor can finish ingestion.
The picker offers this continuation for a URL Intake still in NEEDS_REVIEW;
the server owns eligibility. Never infer a resolution Listing or mark an
Intake READY from the graph job receipt. This continuation has engineering SQL
coverage; browser qualification and human UAT remain open.

The planning closure remains the explicitly selected canonical case context.
The matcher does not invent extra candidate roots to make a graph operation
possible. For an Intake without a case, the existing picker reads a scoped
catalog of actual active Properties and submits the existing human case-creation
API. The human selects roots and supplies confidence, evidence, reason and risk
acknowledgement; target versions are observed, never synthesized. Catalog scope
filtering precedes pagination and each target is independently authorized;
addresses use the existing INTERNAL field mask. An uncertain command retains
the same request and versions through the existing command lifecycle.
Property-only candidates and Listing candidates share the graph repository's
redirect reader, including after a completed merge. Original candidate evidence
is retained; planning and confirmation bind current effective roots and versions.
Before a formal decision is proposed, the same POST accepts an explicit
`append_to_case` reference with its observed case version. The original case
owner appends ranked manual candidate rows and records
`match.manual_context_added.v1` through its canonical audit delivery. Original
outcome, proposer, confidence, signals and candidate evidence remain intact.
Both original and additional targets are independently authorized, including
receipt replay. The command binds the Intake and case versions, rejects any
already-proposed decision, and commits candidates, case version, audit and
receipt together. The same picker form/captured command handles creation and
append; a validated append receipt requires a fresh read before graph planning.
This is a pending-case evidence revision, not a new state machine or decision.

The existing native Intake detail GET now projects its actual resolved Listing,
current revision, effective Property and observed versions through the same
graph resolver. It authorizes both target resources after the Intake; parent
access never grants target access. The picker shows that fresh read separately
from historical matching candidates and queued-command receipts. No parsed
values or source snapshots are inferred by this metadata projection. The same
GET additionally reads saved field values only from the actual current Listing
revision's `effective_values`. Its applied field classification must bind that
Listing, parser run and source snapshot; missing or ambiguous classification
remains unknown and the shared `mask_intake` denies its value at every clearance.
The picker displays these saved fields separately from pending parser/correction
proposals. Current correction values are never substituted for saved facts.
The response is not stored in caches. Browser
qualification must exercise the real production web session: the native picker
is disabled in the older poc-mode browser composition. A poc browser run cannot
attest this native UI flow.

Complete scope-catalog selection, actual browser-to-SQL
qualification, stale exact-case adjudication/recovery and load/fairness
qualification of bounded scans remain unfinished. A conflicting earliest case
can stop its phase; errors remain visible and do not authorize bypassing the
case or fabricating a human review. This is **not
release qualified**; engineering checks do not approve cutover or replace CODE,
real human UAT or the release gates.

The existing identity-context GET can explicitly include native parser proposals
with `include_listing_input=true`. For a POSSIBLE_MATCH in NEEDS_REVIEW, the
case owner verifies its actual MATCHING-to-NEEDS_REVIEW transition, immutable
audit and outbox event. The existing parser proof then checks the unchanged
original result, its sequence and chronology. Current human/source authority and
the existing field mask still apply. This read neither approves a decision nor
rewinds the Intake or parser version. The default graph-only context remains
available for existing merge/split cases; native adjudication must request and
prove its parser input rather than treat graph metadata as that proof.

An authorized manager can now use the same CREATE/REVISE confirmation and
`identity-execution` job to adjudicate a POSSIBLE_MATCH. CREATE binds the observed
properties, Listings and source edges of every rejected candidate; REVISE binds
the explicitly selected Listing candidate and its current identity. The worker
preserves the original POSSIBLE_MATCH evidence and parser result, applies the
independently confirmed fields, and records the actual NEEDS_REVIEW-to-READY
transition. Original NEW/REVISION confirmation still uses this same path.
Approval does not supply a future grant: current SQL account/role/source
authority and the bound resource versions are checked again before acceptance
and persistence. A lost-response replay returns the existing durable receipt
without repeating writes.

Human DUPLICATE uses the same proposal/review/admission/executor owners, with a
real independent manager review for an ordinary case. Staff remain proposal-only
within their readable/owned scope; governed steward approval still requires an
implemented data-quality case. Current SQL identities, scopes and immutable
review evidence are checked again before execution. The decision retains its
actual human proposer/reviewer and published V1 event; the separate automatic
exact flow retains its verified service principal and V2 event. A public human
plan cannot borrow the service's exact authority. DUPLICATE does not apply field
proposals or change Listings, Properties, revisions or source edges. Rejection
retains the existing terminal REJECTED contract, requires no positive source or
execution capability, and creates only the required audit delivery work. It
does not reopen the case or fabricate a second actor.

Migration `0029` extends the existing `workflow.decision_policies` vocabulary
with `native_matching_proposal_authority` and protects its version history. It
creates no policies, identities, source permissions or runtime activation.
Service matching authenticates through the existing boundary and consumes an
explicit policy version with a current DATA_OWNER account, role and complete
source/resource corpus scope. The original manual-entry approval and a signed
job token remain separate facts. No deployment credentials or real source-use
approvals have been activated by the engineering integration.
Conflicting source identity targets, including a URL identifying one Listing
while its provider key identifies another, require POSSIBLE_MATCH review. The
shared matcher retains every target and the existing case owner records their
actual versions; corpus order cannot select an automatic EXACT or REVISION.
Native migration `005` extends the existing `identity.match_decisions` proposer
with truthful `native-intake-identity` service attribution for DUPLICATE only.
Human UUID and service attribution are mutually exclusive; service decisions
cannot invent a human reviewer, and the recorded proposer cannot be rewritten.
The existing upgrader preserves the exact installed `001-004` receipt, appends
`005` atomically, and rejects manifest or actor-schema drift without repair.
This schema integration grants no authority. Exact approval and every positive
execution boundary recheck the actual service credential, current DATA_OWNER
source-use authority, parser input, resource versions and identity graph. Cached
receipts cannot supply a revoked grant. A matching-purpose approval must not be recorded as its owner's
human confirmation, and a fabricated human UUID is not a service identity.
The existing `match.decided` publisher retains the published human V1 payload
and uses V2 for service-attributed DUPLICATE rows. V2 requires the established
service principal and null human proposer/reviewer. The same event validator
selects the declared version and checks producer, aggregate and schema reference;
consumer compatibility for unknown optional fields still applies. Execution
ownership remains in the existing worker audit, separately from the original
proposer. Publisher/schema checks do not establish business approval. Engineering
SQL checks cover the actual exact CLI, registered worker, HTTP readback, current
authority and version conflicts, receipt replay and transactional rollback; they
do not activate a source, approve deployment credentials or complete cutover.
The unused `native_intake_match*` experiments, their engineering admission and
configuration, dedicated tests and unpublished proposal event were removed from
the delivery candidate. Historical engineering archives do not authorize their
restoration or establish a supported runtime.

Do not add another matcher, case repository, service registry or save path to
fill this gap. Further native integration must reuse the owners above and replace
the superseded implementation in the same change. NEW/REVISION require the
existing authorized human confirmation and independent executor; unambiguous
EXACT_DUPLICATE follows the established service flow. DATA_OWNER approval still
requires a real current account, role and explicit scope checks.

Keep the operator path until backfill, reconciliation, shadow, canary and the
approved cutover are actually complete. Do not dual-write or silently fall back
between document-store and native SQL Intake records. The local state-machine
fixture in `delivery_toolchain/release/assisted_listing_intake/drills.py` is for
engineering drills only and must not become a product workflow owner.

## 1. Ownership

| Work | Responsible | Approver |
|---|---|---|
| Schema migration and backfill | Data Platform | Data owner + System Design |
| Intake/listing application compatibility | Expansion Engineering | Expansion Product + QA |
| Identity reconciliation | Data Steward lead | Expansion Manager + Data owner |
| Snapshot replay and object checks | External Data | Security + Data owner |
| Cloud SQL/GCS/Tasks/Pub/Sub rollout | Platform SRE | Release authority + Security |
| Authorization/policy rollout | Security Engineering | Security/Privacy |
| UAT and canary acceptance | Product/Expansion Ops | Release authority |
| Irreconcilable record disposition | Data Steward | Governance reviewer |

## 2. Current-to-target mapping

| Current representation | Target representation | Mapping rule |
|---|---|---|
| `ListingPipelineRecord.source_record` | `Intake` + `SourceSnapshot` or assisted-entry evidence | One current row creates one intake; source fields remain immutable evidence |
| `ListingPipelineStatus.RAW` | Intake `NEEDS_REVIEW` or `FAILED` | Validation errors -> `NEEDS_REVIEW`; platform/parser failure -> `FAILED` |
| `PARSED` / `GEOCODED` | Intake `PARSING`/`MATCHING` plus `ParserRun` | Persist parser release/input/output/field confidence |
| `DUPLICATE` | Exact service case, or POSSIBLE_MATCH with actual independent human review and decision receipt | Same existing executor; exact source-key can be system-approved, ambiguous requires human review; no fact/field application or merge |
| `FAILED_HARD_RULE` | Listing/intake `NEEDS_REVIEW` or `QUARANTINED` | Hard-rule reason codes preserved; no candidate |
| `CANDIDATE` created automatically | Listing `ACTIVE` + synthetic historical `PromotionDecision` + Candidate only after review | Existing candidates are grandfathered through reconciliation, not silently treated as compliant |
| `ListingDedupKey.property_key` including rent | Property identity excluding rent | Rent-only differences become listing revisions |
| In-memory identity mapping | `Property`, immutable `SourceIdentityEdge`, optional `PropertyRedirect` | Deterministic exact edges backfilled; ambiguous groups become cases |
| In-memory/SQLite audit/jobs | Cloud SQL target rows; WORM/outbox verification | Preserve original IDs where stable; otherwise record legacy IDs in migration metadata |

## 3. Migration artifacts

Production migration must add:

- Alembic migration implementing `docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA.sql`;
- mapping manifest `migration_id`, source table/adapter, target table, key mapping, transformation version;
- backfill receipt per partition;
- reconciliation findings table or document store record;
- rollback checkpoint and schema compatibility version;
- source replay manifest including snapshot/checksum/parser release;
- UAT and canary evidence packet.

### 3.1 Reconciliation finding schema

```json
{
  "finding_id": "uuid",
  "migration_id": "string",
  "tenant_id": "uuid",
  "source_kind": "legacy_listing|legacy_candidate|snapshot|identity|audit|job",
  "source_id": "string",
  "target_ids": ["uuid"],
  "finding_type": "COUNT_MISMATCH|CHECKSUM_MISMATCH|AMBIGUOUS_IDENTITY|DUPLICATE_CANDIDATE|MISSING_EVIDENCE|INVALID_SCOPE|ORPHAN_REFERENCE|STATE_MAPPING_CONFLICT",
  "severity": "INFO|WARNING|BLOCKING",
  "expected": {},
  "actual": {},
  "owner_role": "DATA_STEWARD|EXPANSION_MANAGER|PLATFORM_SRE|SECURITY",
  "status": "OPEN|RESOLVED|QUARANTINED|WAIVED",
  "resolution_reason": "string",
  "created_at": "date-time",
  "resolved_at": "date-time|null"
}
```

A `BLOCKING` finding prevents tenant cutover. Waiver requires Data owner and System Design approval; Security/Privacy approval is additionally required for evidence/scope/residency findings.

## 4. Execution phases

### Phase 0 — Contract gate

- Merge approved v0.2.0 design artifacts.
- Contract/schema/auth/event tests land before behavior changes.
- Feature flags default off:
  - `assisted_intake_v1_read`
  - `assisted_intake_v1_write`
  - `assisted_intake_v1_shadow`
  - `assisted_intake_v1_promotion`
  - `assisted_intake_v1_events`
- Production configuration rejects memory/SQLite mode.

Exit: OpenAPI, DDL, transition, authorization, and event compatibility tests green.

### Phase 1 — Schema and read-compatible deploy

1. Apply migration to staging using dry-run checksum manifest.
2. Create target tables/indexes/RLS policies; no production writes.
3. Deploy code capable of reading legacy and target data through a compatibility adapter.
4. Record query parity for Listing Inbox and detail views.

Exit: schema checks, RLS/ABAC tests, backup/PITR proof, no destructive change.

### Phase 2 — Backfill and shadow identity

Partition by tenant then source/month:

1. Create Intake for each legacy source record/import row.
2. Create snapshots or evidence placeholders only where source evidence exists; never fabricate raw snapshots.
3. Create Listing and immutable revision/observation rows.
4. Build exact source edges.
5. Run target matcher in shadow. Do not change live identity/candidate behavior.
6. Create `MatchCase(POSSIBLE_MATCH)` for ambiguous groups.
7. Backfill assignments/SLA only for active review work.
8. Backfill audit references to legacy evidence.

Proof per partition:

- source count = mapped + quarantined + documented excluded;
- target IDs unique per tenant;
- revision counts and fingerprints;
- no identity redirect cycles;
- candidate uniqueness;
- snapshot object count/size/checksum;
- audit count/hash continuity;
- unresolved blocking findings = 0 before canary.

### Phase 3 — Historical candidate reconciliation

Existing automatically created candidates are classified:

| Condition | Disposition |
|---|---|
| Exact listing/property identity, required gate data present, no duplicate candidate | Create `PromotionDecision(status=COMPLETED, decision_type=LEGACY_RECONCILED)` with migration actor and evidence; retain candidate |
| Required fields missing | Candidate `SCREENED`/blocked; create review task and finding |
| Duplicate active candidate for same tenant/property/format | Quarantine all but no automatic deletion; manager decides |
| Ambiguous identity | Candidate remains bound to historical property reference; create match/reassignment review |
| Missing source evidence | Retain candidate if operationally required but mark lineage `PARTIAL`; block claims of fully verified promotion |

No migration script invents a human approver. `LEGACY_RECONCILED` records explicitly identify migration authority and remain distinguishable from post-cutover approvals.

### Phase 4 — Shadow processing canary

- Enable `assisted_intake_v1_shadow` for internal test tenants and approved sources.
- Legacy path remains authoritative; target path runs side by side without external events or candidate creation.
- Compare source-policy outcome, parser values/confidence, identity outcome, listing revision, queue routing, and API projection.

Acceptance metrics over at least 7 days or 10,000 rows, whichever is later:

- 100% tenant/scope isolation tests pass;
- 100% unknown/blocked sources fail closed;
- 0 ambiguous auto-merges;
- 0 automatic candidate promotions;
- exact duplicate agreement >=99.9%; remaining differences reviewed;
- material field parity >=99.5%; all address/rent/area differences explained;
- snapshot checksum reconciliation 100%;
- audit/outbox loss 0;
- p95 latency and queue-age within proposed SLO;
- blocking findings 0.

### Phase 5 — Write canary

Tenant/source rollout units:

1. Internal tenant, assisted-entry-only source.
2. Internal tenant, one approved retrieval source.
3. One low-volume production tenant.
4. 5%, 25%, 50%, 100% eligible tenants.

`assisted_intake_v1_write` makes target Intake/Listing authoritative. Legacy writes are disabled for that tenant/source; do not use indefinite dual-write. A temporary change-data capture/compatibility projection may feed legacy readers, but only target is authoritative.

Promotion remains separately gated by `assisted_intake_v1_promotion` after identity/review UAT.

### Phase 6 — Event and promotion enablement

- Enable transactional outbox and Pub/Sub consumers after replay/DLQ tests.
- Enable promotion for a canary tenant after two-person review, duplicate-candidate constraint, lost-response replay, and SiteScore compensation tests pass.
- Remove automatic `ListingPipeline` candidate creation.

### Phase 7 — Cutover and legacy removal

- Target read/write/events/promotion enabled for all eligible tenants/sources.
- Legacy fixture/source-stub remains CI/test-only and cannot be selected by production config.
- Remove legacy runtime write paths only after 30-day stable watch window and rollback checkpoint expiry.
- Retain migration mappings, reconciliation, and legacy evidence per retention policy.

## 5. Rollback

### 5.1 Trigger

Immediate kill-switch/rollback if any occurs:

- cross-tenant or field-classification breach;
- unknown/prohibited source retrieval;
- ambiguous auto-merge or automatic promotion;
- audit/WORM loss for a high-impact decision;
- duplicate active candidate invariant violation;
- unreconciled SQL/GCS checksum mismatch;
- error budget exhausted or queue age critical for 30 minutes;
- data-loss or restore failure.

### 5.2 Mechanism

- Disable per-tenant/source flags and stop new Cloud Tasks.
- Keep target data read-only; do not delete evidence.
- Drain/park in-flight tasks at checkpoints using cancellation/fence version.
- Disable event publication; retain outbox rows.
- For pre-authoritative shadow/canary, resume legacy authoritative path.
- After target authority, rollback uses the last compatible application version against target schema; it does not reverse committed business decisions automatically.
- Identity/promotion changes are reversed only through their approved reversal state machines.
- If database restore is required, follow the reliability contract restore order and reconcile from WORM/outbox/snapshots.

Rollback evidence: trigger, actor, flag versions, task counts, last committed aggregate versions, outbox range, snapshot manifest, reconciliation results, customer/tenant impact, and release-authority approval.

## 6. Release authority and approvals

| Gate | Approval required |
|---|---|
| Contract artifacts | System Design, Product, Security, Data, Platform, QA |
| Source retrieval | Source owner, Legal/Commercial, Security |
| Staging migration | Data owner, Platform SRE, QA |
| Shadow canary | Product, Expansion Ops, Data, QA |
| Production write canary | Release authority, Platform SRE, Security, Product |
| Candidate promotion | Expansion Product, Security, QA, Release authority |
| 100% cutover | Release authority plus all P0 contract-group approvals |
| Legacy removal | Product, Architecture, Data, Platform, QA |

## 7. Required tests/evidence

- DDL migration up/down or forward-compensation test and checksum manifest;
- tenant RLS + backend ABAC matrix tests;
- source fail-closed tests;
- first submission, exact duplicate, changed-rent revision, ambiguous match, malformed row, timeout/retry, quarantine, correction lineage;
- merge/split/unmerge cycle/concurrency/rollback tests;
- assignment/SLA concurrency and escalation tests;
- promotion idempotency, duplicate prevention, lost-response replay, SiteScore failure/retry tests;
- outbox at-least-once/dedup/replay/DLQ tests;
- SQL/GCS reconciliation and WORM verification;
- restore and rollback drill;
- role-based UAT with screenshots/receipts and no hidden backend assumptions.

## 8. Deployment Configuration

The following environment configurations are required to wire the production GCS object store for snapshot persistence and residency validation:

| Variable | Description | Production Value (Example) |
| --- | --- | --- |
| `ODP_OBJECT_STORE` | Selects backend object store runtime. Must be set to `gcs` in production. Falls back to `in_memory` if unset or credentials are missing. | `gcs` |
| `ODP_RESIDENCY_APPROVED_BUCKETS` | Comma-separated list of approved GCS buckets matching residency rules (e.g. `TW_ONLY` residency). | `taiwan-snapshots,tw-intake-snapshots` |
| `GOOGLE_OAUTH_ACCESS_TOKEN` / `ODP_AUDIT_WORM_GCS_TOKEN` | OAuth token required for WORM GCS API writes. Must be provided by runtime environment/service account. | `<auth-token-material>` |


---
task_id: ODP-INTAKE-FCL-INTEGRATION-001
artifact: functional-requirement-evidence-matrix
source_trace: docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_FUNCTIONAL_REQUIREMENT_TRACE_2026-07-23.md
status: provisional-integration-evidence
row_count: 197
updated_at: 2026-07-23
---

# Assisted Listing Intake Functional Requirement Evidence Matrix

This matrix is a fresh provisional inventory of `FTR-001` through `FTR-197`.
`PASS` means concrete implementation and test/evidence already exist in this
worktree. It is not independent acceptance. `PENDING_BROWSER` means the required
browser proof is incomplete or the latest run did not execute the assertion.
`PENDING_EXACT_COMMIT` means the row cannot close before an integrated commit
and independent acceptance. `FAIL` identifies a currently observed failure or
missing required artifact.

## Path Catalog

| Ref | Concrete paths |
|---|---|
| `P-APP` | `apps/web/src/app/w/expansion/listings/`; `apps/web/src/app/w/expansion/listings/intake/[intakeId]/`; `apps/web/src/app/w/expansion/listings/[listingId]/` |
| `P-INBOX` | `apps/web/features/operator/network/intake/ListingInboxIntakeView.tsx`; `apps/web/features/operator/network/intake/IntakeInboxMap.tsx`; `apps/web/features/operator/network/intake/AddListingFromUrlDialog.tsx`; `apps/web/features/operator/network/intake/urlState.ts` |
| `P-DETAIL` | `apps/web/features/operator/network/intake/IntakeProcessingDetail.tsx`; `apps/web/features/operator/network/intake/AssistedIntakeSection.tsx`; `apps/web/features/operator/network/intake/IntakeStageTimeline.tsx`; `apps/web/features/operator/network/intake/ExistingListingDetailPage.tsx` |
| `P-REVIEW` | `apps/web/features/operator/network/intake/ParsedDataReview.tsx`; `apps/web/features/operator/network/intake/FieldLineageRow.tsx`; `apps/web/features/operator/network/intake/AssistedEntryForm.tsx`; `apps/web/features/operator/network/intake/IntakeFieldFixDialog.tsx` |
| `P-IDENTITY` | `apps/web/features/operator/network/intake/IdentityDecisionBoundary.tsx`; `apps/web/features/operator/network/intake/IdentityDecisionPanel.tsx`; `apps/web/features/operator/network/intake/IdentityGraphPlan.tsx`; `apps/web/features/operator/network/intake/ListingCompareTable.tsx`; `apps/web/features/operator/network/intake/MatchEvidencePanel.tsx` |
| `P-LIFECYCLE` | `apps/web/features/operator/network/intake/AssignmentSlaSummary.tsx`; `apps/web/features/operator/network/intake/TransferIntakeDialog.tsx`; `apps/web/features/operator/network/intake/PauseSlaDialog.tsx`; `apps/web/features/operator/network/intake/ReopenIntakeDialog.tsx`; `apps/web/features/operator/network/intake/PromotionReviewPanel.tsx`; `apps/web/features/operator/network/intake/SiteScoreJobStatus.tsx` |
| `P-EVIDENCE` | `apps/web/features/operator/network/intake/DurableReceiptPanel.tsx`; `apps/web/features/operator/network/intake/EvidencePanel.tsx`; `apps/web/features/operator/network/intake/StructuredAuditTimeline.tsx`; `apps/web/features/operator/network/intake/IntakeErrorRecovery.tsx` |
| `P-ROLE` | `apps/web/features/operator/network/intake/intakePermissions.ts`; `apps/web/features/operator/network/intake/intakeOperatorSession.ts`; `apps/web/src/lib/api/intakeOperatorSession.ts` |
| `P-API` | `apps/api/app/routes/listings.py`; `modules/opsboard/application/network_listings.py`; `apps/worker/assisted_listing_intake/worker.py`; `apps/worker/oday_worker/handlers.py` |
| `P-DOMAIN` | `modules/listing/application/intake_workflow.py`; `modules/listing/domain/intake_states.py`; `modules/listing/application/promotion.py` |
| `P-SHELL` | `packages/ui/src/styles/shell.css`; `apps/web/features/operator/network/intake/intake.module.css`; `apps/web/features/operator/network/intake/identity.module.css` |
| `P-CONTRACT` | `docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1.yaml`; registered overlays in `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_REVIEW_MANIFEST.yaml`; `packages/openapi-client/src/generated/assisted_listing_intake.ts` |
| `P-DESIGN` | `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE.md`; `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE_REVIEW_003.md` |
| `P-PACKAGE10` | `docs_archive/00_source_zips/operator_console/r7-20260720-package-10/manifest.json`; `docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/`; `docs/evidence/design_review/assisted_listing_intake_r7_package10/` |
| `E-CORE` | `tests/e2e/operator-assisted-listing-intake-functional-closure.spec.ts`; `docs/evidence/completion/ODP-INTAKE-FCL-INTEGRATION-001/E2E_VERIFICATION.md`; `docs/evidence/completion/ODP-INTAKE-FCL-INTEGRATION-001/screenshots/` |
| `E-COVERAGE` | `tests/e2e/operator-assisted-listing-intake-functional-coverage.spec.ts`; `docs/evidence/completion/ODP-INTAKE-FCL-INTEGRATION-001/coverage/COVERAGE_VERIFICATION.md`; `docs/evidence/completion/ODP-INTAKE-FCL-INTEGRATION-001/coverage/playwright-results.json`; `docs/evidence/completion/ODP-INTAKE-FCL-INTEGRATION-001/coverage/readback/`; `docs/evidence/completion/ODP-INTAKE-FCL-INTEGRATION-001/coverage/screenshots/` |
| `E-WEB` | `apps/web/features/operator/network/intake/__tests__/` |
| `E-RUNTIME` | `tests/integration/test_assisted_listing_functional_runtime.py`; `tests/integration/test_assisted_listing_intake_persistence.py`; `tests/integration/test_assisted_listing_intake_worker.py`; `tests/integration/test_assisted_listing_promotion.py`; `tests/integration/test_assisted_listing_identity.py` |
| `E-CONTRACT` | `tests/contract/test_assisted_listing_operations.py`; `tests/contract/test_assisted_listing_promotion_api.py`; `tests/contract/test_operator_assisted_listing_api.py`; `tests/contract/test_assisted_listing_openapi.py`; `tests/unit/listing/test_intake_state_machines.py` |
| `E-PERSIST` | `tests/contract/test_assisted_listing_intake_schema.py`; `tests/integration/test_assisted_listing_snapshots.py`; `tests/integration/test_assisted_listing_intake_outbox.py`; `tests/integration/test_assisted_listing_evidence_export.py` |
| `E-DESIGN` | `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE_REVIEW_003.md`; `docs/evidence/design_review/assisted_listing_intake_r7_package10/package10-runtime-report.json`; `docs/evidence/design_review/assisted_listing_intake_r7_package10/package10-assignment-report.json` |
| `E-GATES` | `docs/evidence/completion/ODP-INTAKE-FCL-SHELL-001/COMPLETION_EVIDENCE.md`; `docs/evidence/completion/ODP-INTAKE-FCL-REVIEW-001/COMPLETION_EVIDENCE.md`; `docs/evidence/completion/ODP-INTAKE-FCL-RUNTIME-001/VERIFICATION.md`; `docs/evidence/completion/ODP-INTAKE-UX-QA-001/COMPLETION_EVIDENCE.md`; `scripts/build_validate_assisted_listing_intake_openapi.py`; `scripts/validate_assisted_listing_intake_design.py` |

## Evidence Rows

| ID | Provisional status | Production path ref(s) | Test/evidence ref(s) | Factual note |
|---|---|---|---|---|
| `FTR-001` | `PASS` | `P-APP`, `P-DETAIL`, `P-API` | `E-CORE`, `E-COVERAGE`, `E-GATES` | Core 23/23 and supplemental 6/6 complete the routes, six flows, runtime readbacks, and audit surfaces. |
| `FTR-002` | `PASS` | `P-APP`, `P-SHELL` | `E-CORE`, `E-GATES` | 390, 1024, and 1440 browser assertions/screenshots pass and the production Next build succeeds. |
| `FTR-003` | `PASS` | `P-DETAIL`, `P-EVIDENCE` | `E-CORE`, `E-COVERAGE`, `E-WEB` | Canonical codes persist across state, error, recovery, reload, and receipt scenarios. |
| `FTR-004` | `PASS` | `P-DESIGN`, `P-PACKAGE10` | `E-DESIGN` | Archive manifest names Claude Design, Package 10 hashes, runnable source, and Review 003. |
| `FTR-005` | `PASS` | `P-CONTRACT`, `P-API` | `E-CONTRACT`, `E-PERSIST` | OpenAPI, generated client, state, schema, and operation tests provide concrete contract proof. |
| `FTR-006` | `PASS` | `P-API`, `P-DETAIL` | `E-CORE`, `E-RUNTIME` | Passing core cases assert no legacy request; runtime tests exercise canonical worker/readback. |
| `FTR-007` | `PASS` | `P-DETAIL`, `P-API` | `E-CORE`, `E-WEB` | Durable stage and persisted processing history pass in the complete core suite. |
| `FTR-008` | `PASS` | `P-DETAIL`, `P-API` | `E-CORE`, `E-CONTRACT` | Five policy outcomes and non-retrieval branches passed in the core prefix. |
| `FTR-009` | `PASS` | `P-REVIEW` | `E-CORE`, `E-WEB` | Correction E2E and field-lineage tests prove four value layers and reload. |
| `FTR-010` | `PASS` | `P-IDENTITY`, `P-API` | `E-CORE`, `E-WEB` | NEW, REVISION, POSSIBLE_MATCH, evidence, contradictions, and recommendation are asserted. |
| `FTR-011` | `PASS` | `P-LIFECYCLE`, `P-API` | `E-CORE`, `E-WEB` | Claim, transfer, escalation, completion, SLA pause/resume and history passed. |
| `FTR-012` | `PASS` | `P-ROLE`, `P-DETAIL` | `E-CORE`, `E-WEB` | Six role modes and backend-supplied denial/action facts passed. |
| `FTR-013` | `PASS` | `P-EVIDENCE`, `P-API` | `E-CORE`, `E-RUNTIME` | Server receipts, versions, correlation IDs and related IDs are reloaded/read back. |
| `FTR-014` | `PASS` | `P-INBOX`, `P-DETAIL`, `P-API` | `E-DESIGN`, `E-RUNTIME` | Package review and runtime path restrict work to submitted URLs/approved feeds. |
| `FTR-015` | `PASS` | `P-INBOX`, `P-REVIEW`, `P-EVIDENCE` | `E-WEB`, `E-DESIGN` | Forms and masked evidence tests do not request or reveal provider credentials. |
| `FTR-016` | `PASS` | `P-IDENTITY`, `P-API` | `E-CORE`, `E-CONTRACT` | POSSIBLE_MATCH creates a pending decision and requires a distinct reviewer. |
| `FTR-017` | `PASS` | `P-LIFECYCLE`, `P-DOMAIN` | `E-CORE`, `E-RUNTIME` | Promotion request/review/execute is explicit and second-actor gated. |
| `FTR-018` | `PASS` | `P-DETAIL` | `E-WEB`, `E-CORE` | Timeline renders persisted stages and unit tests reject fabricated percentages. |
| `FTR-019` | `PASS` | `P-IDENTITY`, `P-DETAIL` | `E-WEB`, `E-CORE` | Recommendation and human decision are separate labeled surfaces. |
| `FTR-020` | `PASS` | `P-APP`, `P-SHELL` | `P-DESIGN` | Changes are scoped to Listing/Intake integration and shared shell constraints. |
| `FTR-021` | `PASS` | `P-ROLE`, `P-REVIEW` | `E-CORE`, `E-WEB` | Expansion staff mode, own/assigned scope, proposal, and self-review denial are covered. |
| `FTR-022` | `PASS` | `P-ROLE`, `P-LIFECYCLE` | `E-CORE`, `E-WEB` | Manager assignment, review, and promotion capabilities are exercised. |
| `FTR-023` | `PASS` | `P-ROLE`, `P-IDENTITY` | `E-CORE`, `E-WEB` | Data steward source/identity scope and independent review boundary are covered. |
| `FTR-024` | `PASS` | `P-ROLE`, `P-EVIDENCE` | `E-CORE`, `E-WEB` | Governance mode receives evidence and disabled business mutations. |
| `FTR-025` | `PASS` | `P-ROLE`, `P-EVIDENCE` | `E-CORE`, `E-WEB` | Privacy mode receives purpose binding and restricted evidence facts. |
| `FTR-026` | `PASS` | `P-ROLE`, `P-EVIDENCE` | `E-CORE`, `E-WEB` | Permission-limited mode preserves structure while masking values. |
| `FTR-027` | `PASS` | `P-ROLE`, `P-API` | `E-CORE`, `E-WEB`, `E-CONTRACT` | UI action visibility follows authoritative allowed actions and renders denial codes. |
| `FTR-028` | `PASS` | `P-ROLE`, `P-APP` | `E-WEB`, `E-CORE` | Role session uses stable subject and preserves durable route. |
| `FTR-029` | `PASS` | `P-INBOX`, `P-APP` | `E-COVERAGE`, `E-WEB` | Coverage now passes filters, sort, map/list, cursor history, saved view, selection, reload, and direct workflow links. |
| `FTR-030` | `PASS` | `P-INBOX`, `P-API` | `E-CORE`, `E-WEB` | Submission and exact-duplicate navigation passed through canonical API. |
| `FTR-031` | `PASS` | `P-APP`, `P-DETAIL` | `E-CORE`, `E-WEB` | Durable page direct open and reload passed; it is not an Inbox-dialog redirect. |
| `FTR-032` | `PASS` | `P-APP`, `P-REVIEW` | `E-CORE`, `E-GATES` | Durable-route E2E opens field/identity/evidence sections directly and preserves the active section through reload/navigation. |
| `FTR-033` | `PASS` | `P-APP`, `P-IDENTITY` | `E-CORE` | Durable identity section opens the desktop compare directly. |
| `FTR-034` | `PASS` | `P-REVIEW`, `P-API` | `E-CORE`, `E-WEB` | Assisted-entry draft survives reload and a real version conflict. |
| `FTR-035` | `PASS` | `P-APP`, `P-LIFECYCLE` | `E-CORE`, `E-WEB` | Promotion/Candidate/job receipts survive durable route reload. |
| `FTR-036` | `PASS` | `P-APP`, `P-DETAIL`, `P-API` | `E-CORE`, `E-CONTRACT` | Exact duplicate opens real Listing detail backed by Listing readback. |
| `FTR-037` | `PASS` | `P-INBOX`, `P-APP` | `E-COVERAGE`, `E-WEB` | Inbox coverage proves preview/direct links route correction, compare, and replay work to durable pages. |
| `FTR-038` | `PASS` | `P-DETAIL` | `E-GATES`, `E-CORE` | Durable-route E2E verifies `_blank` source opening without replacing the intake route; masked roles hide the link. |
| `FTR-039` | `PASS` | `P-API`, `P-DOMAIN`, `P-APP` | `E-CORE`, `E-RUNTIME` | Canonical submit/worker/detail and Listing creation readback are covered. |
| `FTR-040` | `PASS` | `P-API`, `P-APP` | `E-CORE`, `E-RUNTIME` | Exact identity short-circuits retrieval and opens existing Listing. |
| `FTR-041` | `PASS` | `P-API`, `P-DOMAIN`, `P-IDENTITY` | `E-CORE`, `E-RUNTIME` | REVISION comparison and immutable ListingRevision append/readback are covered. |
| `FTR-042` | `PASS` | `P-IDENTITY`, `P-API` | `E-CORE`, `E-CONTRACT` | Possible match proposal, self-review denial, independent review, and persisted decision pass. |
| `FTR-043` | `PASS` | `P-REVIEW`, `P-API` | `E-CORE`, `E-RUNTIME` | Assisted-only source avoids retrieval and continues via durable corrections. |
| `FTR-044` | `PASS` | `P-LIFECYCLE`, `P-DOMAIN` | `E-CORE`, `E-COVERAGE`, `E-RUNTIME` | Happy path and SCORE_FAILED/Candidate retention/same-key replay/lost-response readback all pass. |
| `FTR-045` | `PASS` | `P-INBOX` | `E-COVERAGE`, `E-WEB` | List and map use the same authoritative result set and restore from URL. |
| `FTR-046` | `PASS` | `P-INBOX`, `P-API` | `E-COVERAGE`, `E-WEB` | Coverage verifies every required server filter reaches and changes the authoritative query. |
| `FTR-047` | `PASS` | `P-INBOX`, `P-API` | `E-COVERAGE`, `E-WEB` | Persisted saved-view ID survives select and reload. |
| `FTR-048` | `PASS` | `P-INBOX`, `P-API` | `E-COVERAGE`, `E-WEB` | Opaque cursor, stable ordering, counts, selection and browser history pass. |
| `FTR-049` | `PASS` | `P-INBOX` | `E-WEB` | Semantic sortable table and required column assertions exist and pass. |
| `FTR-050` | `PASS` | `P-INBOX` | `E-WEB`, `E-COVERAGE` | MapLibre coordinates and the explicit unlocated list pass in the complete supplemental suite. |
| `FTR-051` | `PASS` | `P-INBOX`, `P-APP` | `E-COVERAGE`, `E-CORE`, `E-WEB` | Direct open/claim/review/retry/correction links reach durable workflows; persisted mutations survive readback. |
| `FTR-052` | `PASS` | `P-APP`, `P-INBOX` | `E-WEB` | Loading, error, degraded, permission, and no-result component states are asserted. |
| `FTR-053` | `PASS` | `P-INBOX`, `P-ROLE` | `E-CORE`, `E-WEB` | Permission-limited browser/API and masking units reveal no sensitive value. |
| `FTR-054` | `PASS` | `P-INBOX`, `P-API` | `E-CORE`, `E-WEB` | Invalid URL, valid source, and unsupported/policy variants have concrete tests. |
| `FTR-055` | `PASS` | `P-INBOX`, `P-DETAIL`, `P-API` | `E-CORE`, `E-WEB` | Original and canonical URLs are returned and rendered separately. |
| `FTR-056` | `PASS` | `P-INBOX`, `P-ROLE` | `E-WEB`, `E-CONTRACT` | HeatZone and authoritative submitter/tenant/scope/owner context are asserted. |
| `FTR-057` | `PASS` | `P-INBOX` | `E-WEB`, `E-DESIGN` | Copy explains policy behavior without crawl/credential implication. |
| `FTR-058` | `PASS` | `P-INBOX`, `P-API` | `E-WEB`, `E-CONTRACT` | Request lock and idempotent receipt tests prevent duplicate durable intake. |
| `FTR-059` | `PASS` | `P-INBOX`, `P-API` | `E-COVERAGE`, `E-CORE`, `E-WEB` | Add URL browser matrix passes invalid, unsupported, duplicate, canonicalized, denied/in-flight/retry and receipt paths. |
| `FTR-060` | `PASS` | `P-INBOX`, `P-APP` | `E-CORE`, `E-WEB` | Success navigates to durable target and reloads server receipt. |
| `FTR-061` | `PASS` | `P-DETAIL` | `E-WEB`, `E-CORE` | Summary fields including source, URLs, owner, time and scope are asserted. |
| `FTR-062` | `PASS` | `P-DETAIL`, `P-LIFECYCLE` | `E-CORE`, `E-WEB` | State, assignment/SLA, retryability and freshness render from detail readback. |
| `FTR-063` | `PASS` | `P-EVIDENCE`, `P-IDENTITY` | `E-WEB`, `E-RUNTIME` | Snapshot/parser/confidence/match evidence and contradiction mappings are tested. |
| `FTR-064` | `PASS` | `P-IDENTITY` | `E-WEB`, `E-CORE` | Generated recommendation and human decision are separate. |
| `FTR-065` | `PASS` | `P-IDENTITY`, `P-ROLE` | `E-CORE`, `E-WEB` | Allowed decisions, second actor, reason and risk requirements are exercised. |
| `FTR-066` | `PASS` | `P-DETAIL`, `P-LIFECYCLE` | `E-CORE`, `E-WEB` | Related IDs appear from committed server responses and survive reload. |
| `FTR-067` | `PASS` | `P-DETAIL`, `P-EVIDENCE` | `E-CORE`, `E-WEB` | ETag/version, actor/time/reason/before-after/correlation are rendered. |
| `FTR-068` | `PASS` | `P-DETAIL`, `P-LIFECYCLE` | `E-CORE`, `E-WEB` | Focused E2E passed FAILED retry/cancel/DLQ/replay and authoritative job history. |
| `FTR-069` | `PASS` | `P-DETAIL` | `E-CORE`, `E-WEB` | Polling observes persisted worker changes without optimistic mutation. |
| `FTR-070` | `PASS` | `P-REVIEW` | `E-WEB` | All five canonical field groups are asserted. |
| `FTR-071` | `PASS` | `P-REVIEW` | `E-CORE`, `E-WEB` | Parsed, normalized, corrected and effective values are jointly tested. |
| `FTR-072` | `PASS` | `P-REVIEW`, `P-EVIDENCE` | `E-WEB` | Missing, low-confidence and masked presentations are non-color-only. |
| `FTR-073` | `PASS` | `P-REVIEW`, `P-API` | `E-CORE`, `E-WEB` | Actor/reason/time/snapshot/parser/supersession lineage persists after reload. |
| `FTR-074` | `PASS` | `P-REVIEW`, `P-IDENTITY` | `E-CORE`, `E-WEB` | Material assisted-entry corrections require reason/risk and three second-actor approvals. |
| `FTR-075` | `PASS` | `P-REVIEW`, `P-API` | `E-CORE`, `E-WEB` | Draft survives close/reload/failure/conflict/retry paths. |
| `FTR-076` | `PASS` | `P-REVIEW`, `P-API` | `E-CORE`, `E-WEB` | Pending correction remains non-authoritative until review. |
| `FTR-077` | `PASS` | `P-REVIEW`, `P-API` | `E-CORE`, `E-RUNTIME` | Assisted-only branch asserts no retrieval and contains no credential prompt. |
| `FTR-078` | `PASS` | `P-IDENTITY` | `E-CORE`, `E-WEB` | Desktop current/submitted values render in compare. |
| `FTR-079` | `PASS` | `P-IDENTITY` | `E-WEB` | Changed rows and screen-reader aggregate/per-row summaries are tested. |
| `FTR-080` | `PASS` | `P-IDENTITY` | `E-WEB`, `E-CORE` | Required identity/commercial/location comparison rows are mapped. |
| `FTR-081` | `PASS` | `P-IDENTITY` | `E-WEB`, `E-CORE` | Confidence, agreeing signals and contradictions are named. |
| `FTR-082` | `PASS` | `P-IDENTITY`, `P-DETAIL` | `E-CORE`, `E-WEB` | Five outcomes are distinct in outcome mappings; three compare paths and duplicate/quarantine paths are exercised. |
| `FTR-083` | `PASS` | `P-IDENTITY`, `P-API` | `E-CORE`, `E-COVERAGE`, `E-CONTRACT` | Explicit create/revision/duplicate/steward/reject/quarantine commands have persisted decision/readback coverage. |
| `FTR-084` | `PASS` | `P-IDENTITY`, `P-API` | `E-COVERAGE`, `E-CONTRACT` | Merge, split, unmerge and reversal pass with two actors, graph plans, redirects and superseded lineage. |
| `FTR-085` | `PASS` | `P-IDENTITY`, `P-API` | `E-CORE`, `E-WEB` | Same actor gets SELF_REVIEW_DENIED and distinct reviewer executes. |
| `FTR-086` | `PASS` | `P-INBOX`, `P-LIFECYCLE` | `E-CORE`, `E-WEB` | Owner/queue/timestamps/due/SLA render and match API. |
| `FTR-087` | `PASS` | `P-LIFECYCLE`, `P-API` | `E-CORE`, `E-WEB`, `E-CONTRACT` | Assignment transition states have UI/domain tests and persisted command readback. |
| `FTR-088` | `PASS` | `P-LIFECYCLE`, `P-API` | `E-WEB`, `E-CONTRACT` | Six SLA states are mapped with semantic presentation. |
| `FTR-089` | `PASS` | `P-LIFECYCLE`, `P-API` | `E-CORE`, `E-CONTRACT` | Claim, transfer, pause/resume, escalation and completion pass in the complete core suite. |
| `FTR-090` | `PASS` | `P-LIFECYCLE`, `P-API` | `E-CORE`, `E-WEB` | Transfer requires and persists handoff note. |
| `FTR-091` | `PASS` | `P-LIFECYCLE`, `P-API` | `E-CORE`, `E-WEB` | Pause requires reason/resume time and history survives readback. |
| `FTR-092` | `PASS` | `P-LIFECYCLE` | `E-WEB` | SLA tests assert text plus icon/pattern rather than color only. |
| `FTR-093` | `PASS` | `P-LIFECYCLE`, `P-EVIDENCE` | `E-WEB`, `E-CONTRACT` | Conflict retains draft and exposes current owner/version for resubmit. |
| `FTR-094` | `PASS` | `P-IDENTITY`, `P-EVIDENCE` | `E-CORE`, `E-WEB` | Recommendation and human decision use distinct UI/audit records. |
| `FTR-095` | `PASS` | `P-IDENTITY`, `P-LIFECYCLE` | `E-WEB`, `E-CORE` | High-risk dialogs include affected entities, plan, risk, reason and reviewer. |
| `FTR-096` | `PASS` | `P-IDENTITY`, `P-LIFECYCLE` | `E-CORE`, `E-WEB` | Same-actor denial uses exact SELF_REVIEW_DENIED. |
| `FTR-097` | `PASS` | `P-INBOX`, `P-IDENTITY`, `P-LIFECYCLE` | `E-WEB` | Busy controls lock and state updates only from authoritative responses. |
| `FTR-098` | `PASS` | `P-EVIDENCE`, `P-LIFECYCLE` | `E-CORE`, `E-WEB` | Real 409 and mounted dialog tests preserve input and server version. |
| `FTR-099` | `PASS` | `P-EVIDENCE`, `P-API` | `E-CORE`, `E-WEB` | Durable receipt fields match API after reload. |
| `FTR-100` | `PASS` | `P-EVIDENCE` | `E-WEB`, `E-CONTRACT` | Anti-fabrication tests reject client-created IDs, hashes, WORM and entity facts. |
| `FTR-101` | `PASS` | `P-LIFECYCLE`, `P-DOMAIN` | `E-CORE`, `E-COVERAGE`, `E-WEB` | Distinct promotion stages, poll, receipt and reload behavior pass. |
| `FTR-102` | `PASS` | `P-LIFECYCLE` | `E-COVERAGE`, `E-WEB` | REJECTED, FAILED and SCORE_FAILED have distinct tested presentations. |
| `FTR-103` | `PASS` | `P-LIFECYCLE`, `P-DOMAIN` | `E-COVERAGE`, `E-WEB` | Commit-point guards and lost-response scenario prove IDs are not exposed prematurely. |
| `FTR-104` | `PASS` | `P-LIFECYCLE` | `E-COVERAGE`, `E-WEB` | SCORE_FAILED browser/readback retains the committed Candidate. |
| `FTR-105` | `PASS` | `P-LIFECYCLE`, `P-API` | `E-COVERAGE`, `E-RUNTIME` | Authorized replay completes from checkpoint with no duplicate Candidate. |
| `FTR-106` | `PASS` | `P-LIFECYCLE`, `P-API` | `E-COVERAGE`, `E-WEB` | Same-key replay returns the identical durable receipt after simulated lost response. |
| `FTR-107` | `PASS` | `P-LIFECYCLE`, `P-API` | `E-CORE`, `E-COVERAGE`, `E-WEB` | Queue/attempt/timeout/checkpoint/retry/cancel/DLQ/replay permission are covered. |
| `FTR-108` | `PASS` | `P-EVIDENCE`, `P-API` | `E-WEB`, `E-RUNTIME` | Structured audit tests include actor/role/time/action/reason/before-after. |
| `FTR-109` | `PASS` | `P-EVIDENCE`, `P-API` | `E-WEB`, `E-RUNTIME` | Snapshot/parser/decision/entity/correlation/version fields map from server. |
| `FTR-110` | `PASS` | `P-EVIDENCE`, `P-ROLE` | `E-CORE`, `E-WEB` | Masked labels remain while all value columns are withheld. |
| `FTR-111` | `PASS` | `P-EVIDENCE`, `P-ROLE` | `E-CORE`, `E-WEB` | Purpose/classification/expiry and audit notice are rendered for governed roles. |
| `FTR-112` | `PASS` | `P-EVIDENCE`, `P-API` | `E-WEB`, `E-PERSIST`, `E-GATES` | Governed evidence tests/readbacks prove server-supplied legal hold, export, watermark/manifest and verification. |
| `FTR-113` | `PASS` | `P-EVIDENCE`, `P-ROLE` | `E-WEB`, `E-PERSIST` | Credential class is masked/omitted in evidence and export tests. |
| `FTR-114` | `PASS` | `P-DETAIL`, `P-DOMAIN` | `E-CORE`, `E-COVERAGE`, `E-WEB`, `E-CONTRACT` | Contract/UI matrices plus canonical runs cover all 12 intake states and real transition histories. |
| `FTR-115` | `PASS` | `P-LIFECYCLE`, `P-API` | `E-CORE`, `E-CONTRACT` | FAILED checkpoint retry, two-actor QUARANTINED reopen and terminal CANCELLED all pass. |
| `FTR-116` | `PASS` | `P-DETAIL`, `P-API` | `E-CORE`, `E-CONTRACT` | All five policy states and next-action branches passed. |
| `FTR-117` | `PASS` | `P-IDENTITY`, `P-DOMAIN` | `E-CORE`, `E-COVERAGE`, `E-WEB`, `E-CONTRACT` | Decision UI/contracts cover draft through execution, failure, reversal and supersession. |
| `FTR-118` | `PASS` | `P-LIFECYCLE`, `P-API` | `E-CORE`, `E-COVERAGE`, `E-WEB` | All seven job states and their attempt/checkpoint/retry facts are tested. |
| `FTR-119` | `PASS` | `P-DETAIL`, `P-LIFECYCLE` | `E-CORE`, `E-WEB` | Canonical English codes render with text and icon/pattern across tested state families. |
| `FTR-120` | `PASS` | `P-EVIDENCE` | `E-CORE`, `E-COVERAGE`, `E-WEB` | Browser error families expose the complete recovery envelope. |
| `FTR-121` | `PASS` | `P-EVIDENCE`, `P-LIFECYCLE` | `E-CORE`, `E-WEB` | 409 browser case preserves input and exposes server value/version. |
| `FTR-122` | `PASS` | `P-EVIDENCE`, `P-API` | `E-CORE`, `E-CONTRACT` | 428 PRECONDITION_REQUIRED browser/API variant passes. |
| `FTR-123` | `PASS` | `P-EVIDENCE`, `P-API` | `E-CORE`, `E-COVERAGE`, `E-CONTRACT` | VERSION_CONFLICT and idempotency replay/reuse semantics are tested. |
| `FTR-124` | `PASS` | `P-EVIDENCE`, `P-API` | `E-CORE`, `E-WEB`, `E-CONTRACT` | OWNER, REVIEW, WORK_INCOMPLETE and LEGAL_HOLD conflicts have mapped recovery facts. |
| `FTR-125` | `PASS` | `P-EVIDENCE`, `P-ROLE` | `E-CORE`, `E-WEB`, `E-CONTRACT` | SELF_REVIEW, source-policy, scope and ownership denials are reason-coded. |
| `FTR-126` | `PASS` | `P-EVIDENCE`, `P-REVIEW` | `E-CORE`, `E-WEB`, `E-CONTRACT` | 422 correction/risk validation preserves values and links field errors. |
| `FTR-127` | `PASS` | `P-EVIDENCE`, `P-API` | `E-COVERAGE`, `E-RUNTIME` | Page removed, timeout, auth wall and bot challenge persist and render. |
| `FTR-128` | `PASS` | `P-EVIDENCE`, `P-API` | `E-COVERAGE`, `E-RUNTIME` | Parser partial, retryable/permanent outcomes and assisted fallback pass. |
| `FTR-129` | `PASS` | `P-EVIDENCE`, `P-LIFECYCLE` | `E-COVERAGE`, `E-CORE` | Stale source, quarantine, retry exhaustion and DLQ are proven by durable readbacks. |
| `FTR-130` | `PASS` | `P-EVIDENCE` | `E-CORE`, `E-COVERAGE`, `E-WEB` | Major errors remain on-page with explicit recovery rather than toast-only handling. |
| `FTR-131` | `PASS` | `P-INBOX`, `P-DETAIL`, `P-SHELL` | `P-DESIGN`, `E-CORE` | Package direction and production screenshots show compact Inbox/comfortable detail. |
| `FTR-132` | `PASS` | `P-SHELL`, `P-INBOX`, `P-DETAIL` | `P-DESIGN`, `E-WEB` | Shared shell/tokens/components are reused; no separate domain status palette is introduced. |
| `FTR-133` | `PASS` | `P-DETAIL`, `P-SHELL` | `P-DESIGN` | Production composition is section-based; design response explicitly prohibits nested cards. |
| `FTR-134` | `PASS` | `P-SHELL`, `P-INBOX`, `P-IDENTITY` | `E-CORE`, `P-DESIGN` | Responsive test passed stable no-overflow layouts at required widths. |
| `FTR-135` | `PASS` | `P-INBOX`, `P-DETAIL` | `E-WEB`, `E-CORE` | Accessible names/tooltips and keyboard focus are covered. |
| `FTR-136` | `PASS` | `P-IDENTITY`, `P-LIFECYCLE` | `P-DESIGN`, `E-WEB` | High-risk controls use explicit command labels. |
| `FTR-137` | `PASS` | `P-DETAIL`, `P-REVIEW`, `P-IDENTITY`, `P-LIFECYCLE`, `P-EVIDENCE` | `P-DESIGN`, `E-WEB` | Required domain components are implemented and inventoried with variants/tests. |
| `FTR-138` | `PASS` | `P-APP`, `P-SHELL` | `E-CORE` | 1440 browser case passed and screenshot exists. |
| `FTR-139` | `PASS` | `P-APP`, `P-SHELL` | `E-CORE` | 1024 browser case passed and screenshot exists. |
| `FTR-140` | `PASS` | `P-APP`, `P-SHELL` | `E-CORE` | 390 browser case passed and screenshot exists. |
| `FTR-141` | `PASS` | `P-APP`, `P-IDENTITY`, `P-SHELL` | `E-CORE` | Mobile durable identity route shows DESKTOP_REQUIRED and preserves route. |
| `FTR-142` | `PASS` | `P-SHELL`, `P-DETAIL` | `E-CORE`, `E-WEB` | Long URL/code/correlation values use wrap/ellipsis plus full title/label, with no viewport overflow. |
| `FTR-143` | `PASS` | `P-SHELL` | `E-CORE` | Required viewport test asserts page scroll width and produced screenshots. |
| `FTR-144` | `PASS` | `P-SHELL` | `E-CORE`, `E-WEB` | Canonical route Axe run reports zero serious/critical violations, including contrast checks. |
| `FTR-145` | `PASS` | `P-APP`, `P-DETAIL` | `E-CORE`, `E-WEB`, `E-GATES` | Keyboard E2E and dialog units prove focus order, trap/return and busy-state dismissal controls. |
| `FTR-146` | `PASS` | `P-INBOX` | `E-WEB`, `E-CORE` | Semantic table tests assert headers and aria-sort; canonical Axe run passes. |
| `FTR-147` | `PASS` | `P-IDENTITY`, `P-REVIEW` | `E-WEB` | Aggregate and per-row screen-reader change summaries are asserted. |
| `FTR-148` | `PASS` | `P-REVIEW`, `P-LIFECYCLE`, `P-EVIDENCE` | `E-WEB` | State/risk/masking/confidence/SLA semantic text and marks are unit-tested. |
| `FTR-149` | `PASS` | `P-EVIDENCE`, `P-REVIEW` | `E-WEB`, `E-CORE` | Error browser/unit tests focus the summary/field row and expose dynamic updates through live regions. |
| `FTR-150` | `PASS` | `P-LIFECYCLE`, `P-DETAIL` | `E-CORE`, `E-WEB` | Focused browser test proves trap/return; dialog units block dismissal while busy. |
| `FTR-151` | `PASS` | `P-SHELL`, `P-DETAIL` | `E-CORE` | Completed reduced-motion E2E finds no infinite nonessential animation. |
| `FTR-152` | `PASS` | `P-DETAIL` | `E-GATES`, `E-CORE` | Source links identify new-window behavior and durable-route E2E preserves intake state. |
| `FTR-153` | `PASS` | `P-APP`, `P-DETAIL` | `E-CORE` | Completed Axe run reports zero serious or critical violations. |
| `FTR-154` | `PASS` | `P-INBOX`, `P-DETAIL`, `P-EVIDENCE` | `P-DESIGN`, `E-WEB` | Production copy is zh-TW and canonical codes are retained. |
| `FTR-155` | `PASS` | `P-DETAIL`, `P-EVIDENCE` | `E-WEB` | Display helpers/tests include absolute timestamp and timezone alongside relative context. |
| `FTR-156` | `PASS` | `P-INBOX`, `P-API` | `E-DESIGN`, `E-RUNTIME` | Design samples and runtime fixtures use synthetic/example sources and fictional Taiwan facts. |
| `FTR-157` | `PASS` | `P-INBOX`, `P-EVIDENCE` | `E-DESIGN`, `E-PERSIST` | Archive review and evidence/export fixtures contain synthetic data and no credential values. |
| `FTR-158` | `PASS` | `P-INBOX`, `P-DETAIL` | `E-DESIGN`, `P-DESIGN` | Package 10 review records zero banned crawl/AI-guarantee wording. |
| `FTR-159` | `PASS` | `P-INBOX`, `P-DETAIL`, `P-EVIDENCE` | `P-DESIGN`, `E-WEB` | Copy inventory is operational state/next-action language. |
| `FTR-160` | `PASS` | `P-INBOX`, `P-DETAIL` | `E-DESIGN` | Review/copy scan contains no robots.txt-as-authorization claim. |
| `FTR-161` | `PASS` | `P-PACKAGE10`, `P-DESIGN` | `E-DESIGN` | Archived source identifies scope, version R7, source and owner. |
| `FTR-162` | `PASS` | `P-PACKAGE10` | `E-DESIGN` | Package screen labels and review cover desktop/mobile flow surfaces. |
| `FTR-163` | `PASS` | `P-PACKAGE10`, `P-DESIGN` | `E-DESIGN` | Inbox/list-map integration is indexed and reviewed. |
| `FTR-164` | `PASS` | `P-PACKAGE10`, `P-DESIGN` | `E-DESIGN` | Desktop 003A-003F surfaces are mapped in design response. |
| `FTR-165` | `PASS` | `P-PACKAGE10`, `P-DESIGN` | `E-DESIGN` | Tablet/mobile captures and desktop-required behavior are archived. |
| `FTR-166` | `PASS` | `P-PACKAGE10`, `P-DESIGN` | `E-DESIGN` | State families are indexed in response/package. |
| `FTR-167` | `PASS` | `P-PACKAGE10`, `P-DESIGN` | `E-DESIGN` | Required UI variants are represented and reviewed with binding conditions. |
| `FTR-168` | `PASS` | `P-PACKAGE10` | `E-DESIGN` | Runnable Claude Design source contains submission/correction/compare/action/receipt interactions. |
| `FTR-169` | `PASS` | `P-DESIGN` | `E-DESIGN` | Response section 7 records reused and domain components. |
| `FTR-170` | `PASS` | `P-DESIGN`, `P-PACKAGE10` | `E-DESIGN` | Response/review records keyboard, focus, contrast and reduced-motion decisions/conditions. |
| `FTR-171` | `PASS` | `P-DESIGN` | `E-DESIGN` | Response section 10 is the final content authority/copy inventory. |
| `FTR-172` | `PASS` | `P-DESIGN` | `E-DESIGN` | Response records measurements, responsive constraints, overflow, URL and test mapping. |
| `FTR-173` | `PASS` | `P-PACKAGE10` | `E-DESIGN` | Manifest contains verified ZIP/source/standalone SHA-256 and copy match. |
| `FTR-174` | `PASS` | `P-DESIGN`, `P-PACKAGE10` | `E-DESIGN` | Design response and archive manifest resolve source/runnable/version/checksums. |
| `FTR-175` | `PASS` | `P-DESIGN`, `P-APP` | `P-DESIGN` | Response production route/screen/state index maps mounted surfaces. |
| `FTR-176` | `PASS` | `P-DESIGN` | `P-DESIGN` | Clause-Level Requirement Disposition Register covers contiguous FTR-001..197 ranges with ACCEPT/MODIFY binding clarifications. |
| `FTR-177` | `PASS` | `P-DESIGN` | `P-DESIGN`, `E-WEB` | Response component inventory maps to production components. |
| `FTR-178` | `PASS` | `P-DESIGN`, `P-SHELL` | `P-DESIGN`, `E-CORE` | Responsive/a11y decisions and production test ownership are documented. |
| `FTR-179` | `PASS` | `P-DESIGN` | `P-DESIGN`, `E-DESIGN` | Dependencies and fail-closed release gates are explicitly listed. |
| `FTR-180` | `PASS` | `P-DESIGN`, `P-INBOX`, `P-DETAIL` | `P-DESIGN` | Content authority maps final operational copy and canonical code format. |
| `FTR-181` | `PENDING_EXACT_COMMIT` | `P-DESIGN` | `P-DESIGN`, `E-DESIGN` | Package review is recorded; implementation-commit discipline approvals remain pending. |
| `FTR-182` | `PASS` | `P-APP`, `P-API` | `E-CORE`, `E-COVERAGE` | Core 23/23 plus coverage 6/6 prove all six flows from Inbox through durable receipt/readback. |
| `FTR-183` | `PASS` | `P-IDENTITY`, `P-DETAIL` | `E-CORE`, `E-WEB` | Outcome mappings are distinct; exercised core routes assert NEW/REVISION/POSSIBLE plus duplicate/quarantine behavior. |
| `FTR-184` | `PASS` | `P-REVIEW`, `P-EVIDENCE` | `E-CORE`, `E-WEB` | Field-lineage browser/unit evidence jointly covers four layers, confidence and masking. |
| `FTR-185` | `PASS` | `P-DETAIL`, `P-LIFECYCLE`, `P-IDENTITY` | `E-CORE`, `E-COVERAGE`, `E-WEB`, `E-CONTRACT` | Intake, source, decision, assignment/SLA, promotion and job state families have passing UI/contract/runtime evidence. |
| `FTR-186` | `PASS` | `P-IDENTITY`, `P-LIFECYCLE`, `P-EVIDENCE` | `E-CORE`, `E-WEB` | Two-actor correction/identity/promotion paths show plan, risk, reason and receipt. |
| `FTR-187` | `PASS` | `P-IDENTITY`, `P-LIFECYCLE`, `P-DOMAIN` | `E-CORE`, `E-RUNTIME` | Domain/browser evidence requires explicit match and promotion decisions. |
| `FTR-188` | `PASS` | `P-ROLE`, `P-EVIDENCE` | `E-CORE`, `E-WEB` | Six-role core case passed permissions, self-review, masking, purpose and read-only modes. |
| `FTR-189` | `PASS` | `P-EVIDENCE` | `E-CORE`, `E-COVERAGE` | Canonical error cases and supplemental retrieval/parser failures pass with durable recovery facts. |
| `FTR-190` | `PASS` | `P-APP`, `P-SHELL` | `E-CORE` | Three viewport no-overflow/fallback case passed with screenshots. |
| `FTR-191` | `PASS` | `P-APP`, `P-SHELL`, `P-DETAIL` | `E-CORE`, `E-WEB` | Axe, keyboard focus, SR summaries, semantic marks and reduced-motion assertions all pass. |
| `FTR-192` | `PASS` | `P-SHELL`, `P-CONTRACT`, `P-DETAIL` | `P-DESIGN`, `E-CONTRACT` | Existing shell/tokens/contracts and canonical state enums remain authoritative. |
| `FTR-193` | `PENDING_EXACT_COMMIT` | `P-DESIGN`, `P-APP` | `P-DESIGN` | Independent implementation acceptance has not yet verified no remaining UI guesswork. |
| `FTR-194` | `PASS` | `P-CONTRACT`, `P-DESIGN`, `P-PACKAGE10` | `E-CONTRACT`, `E-DESIGN` | Normative source paths, overlays, workflow, visual system, tokens and component contracts are present. |
| `FTR-195` | `PENDING_EXACT_COMMIT` | `P-APP`, `P-API` | `E-CORE`, `E-COVERAGE` | Worktree is uncommitted and no exact integrated-commit verification manifest exists. |
| `FTR-196` | `PENDING_EXACT_COMMIT` | `P-APP`, `P-API` | `E-CORE`, `E-COVERAGE` | Independent Acceptance Fleet has not rerun and signed all 197 rows. |
| `FTR-197` | `PENDING_EXACT_COMMIT` | `P-APP`, `P-API` | `E-CORE`, `E-COVERAGE` | Closure is impossible until all prior rows pass or receive approved NOT_APPLICABLE. |

## Provisional Counts

| Status | Count |
|---|---:|
| `PASS` | 192 |
| `PENDING_BROWSER` | 0 |
| `PENDING_EXACT_COMMIT` | 5 |
| `FAIL` | 0 |
| **Total** | **197** |

All implementation/browser rows now have concrete passing evidence. The only
non-PASS rows are `FTR-181`, `FTR-193`, and `FTR-195` through `FTR-197`, which
intrinsically require the pushed exact integrated commit and/or independent
Acceptance Fleet disposition. This document does not change the product status
to `FUNCTIONALLY_COMPLETE`.

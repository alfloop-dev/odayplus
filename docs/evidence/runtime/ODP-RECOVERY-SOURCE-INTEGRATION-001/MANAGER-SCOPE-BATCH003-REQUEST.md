# Finite batch003 source/fixture lease request — not self-granted scope

Run `pi-20261015T050836Z-858dccaf`; source anchor
`2bb63a309cde18b520b3b93f19d9b95e3fc5f975` / tree
`43be8e70b998942a50bd53aa012ce98a633234ec`. Batch002 is allocated and its
owning implementation is committed/pushed; this is NOT review-ready. Canonical
reviewer remains **Human/Ops**, final disposition **PENDING**.

## Inventory before requesting more scope

The complete original product-unit **32-node** and orchestrator **66-node**
failure inventories were read, not just the initial five CI job summaries.
All 32 original unit failure bodies were inspected, including authentication,
missing producer attribution, SQL prerequisite, GO fixture, scanner and actual
OSS/PG composition failures. The latest available terminal unit delta is
**25 failed / 9012 passed / 23 skipped** at b2c751406, run38682527301,
job116250091591. Its failure inventory is a subset of the original32, not
25 newly diagnosed common-cause failures. Full raw and inert logs are retained.
At old886c, normal CI38684815661's orchestrator, security, performance, Node and
API-contract leaves have actually succeeded; DB and E2E have failed; unit is
still IN_PROGRESS at this readback. NONE is proof for2bb or its docs successor.

Fresh local actual Docker PostgreSQL16.9/PostGIS runs all FIVE tests in the
leased assisted-listing production/restart module, exit0/no skips, with actual
signed synthetic service authentication through config_from_env/default_boundary,
forged-header401 and foreign-tenant refusal, and owned-container removal.
The prior six-test full production migration/persistence module is a DIFFERENT
selection/head. Its old pass is not transferred.

Fresh local owning audit/expansion composition at2bb exits1: the unchanged
expansion persistence flow now passes, but FIVE HTTP export tests in two
unallocated modules provide no tenant in AUDIT_HEADERS. They cannot reach their
intended positive/expiry/500-sanitization/missing-field assertions under the new
mandatory verified HTTP tenant. Preserve those assertions, repair the synthetic
principal AND owning source-event tenant, never globally admit tenantless history.

Two new mounted JWT SiteScore negatives expose a REAL foreign-tenant approval200
on the nonpartitioned local workflow fallback (both aliases). Anonymous refusal
is not the defect. Failing assertions are COMMITTED, not deleted or weakened.
The HTTP owning route is unleased. A separate privacy export producer still emits
without tenant; the broader original privacy test remains failed. These are NOT
claimed fixed by tagging SiteScore/export audit events.

All exact node IDs, original/b2c counts, raw-log hashes and author terminal
records are bound in `pi-batch002-diagnostics.json`. The original66 orchestrator
inventory includes the seven `test_release_lease.py` re-export cases as well as
integration/issuer cases; it is not only the earlier44-line diagnostic excerpt.
Batch001 changed the common issuer fixture/explicit historical clock, and old886
orchestrator is now terminal SUCCESS; no additional live control-plane scope is
requested or justified by that old failure history.

## Exact additional paths requested (25, no directories/wildcards)

None of these paths is in the live328-entry task scope at this request's
readback. Manager must authorize/serialize exact leases and allocate refreshed
context BEFORE any edit. This request is not an assignment or scope grant.

| Path | Inspected evidence / bounded purpose |
|---|---|
| `apps/api/app/routes/sitescore.py` | Both-alias foreign approval200 in new committed regression. Enforce owning decision tenant for lookup/decision in shared fallback as well as durable store; retain own approval/reason/transitions and reject missing/foreign ownership, not a request-header retag. |
| `modules/listing/application/intake_privacy.py` | Original32/b2c/local privacy test missing audit.evidence_export.v1. Attribute future event only after existing principal/tenant/entity/export checks, from verified owning manifest; never rewrite history or waive export/SoD/residency. |
| `apps/api/app/routes/avm.py` | Original/b2c AVM audit reads have unsupported principals; `_audit` also emits tenantless metadata. Resolve owning authorized server tenant on future events, retaining command-store/object scopes; no read-filter waiver. |
| `apps/api/app/routes/priceops.py` | Original/b2c replay/elasticity audit reads are unsupported; `_record_audit` currently records metadata without tenant. Carry verified authorized plan tenant to future records, not client body/correlation authority. |
| `apps/api/app/routes/interventions.py` | HTTP-to-workflow owning tenant seam required before lifecycle audit attribution; scope checked before command execution, no body-based authority or permission relaxation. |
| `modules/intervention/application/workflow.py` | Lifecycle `_audit` has store/kind/status but no tenant; bind future events to the actual owning intervention, preserve transitions/reasons/causal labels and every denial. |
| `modules/intervention/domain/lifecycle.py` | Owning Intervention currently lacks tenant. If needed for the preceding seam, persist immutable tenant on NEW interventions/follow-ups; legacy unknown stays unknown/hidden, no historical backfill. |
| `modules/intervention/infrastructure/repositories.py` | Owning in-memory intervention lookup boundary for the preceding tenant seam, no cross-tenant aliasing or lifecycle changes. |
| `shared/infrastructure/persistence/repositories.py` | Owning DurableInterventionRepository serialization/lookup for the same new-record tenant seam and restart controls; no global document-store waiver or old-record rewrite. |
| `tests/contract/test_api_error_envelope.py` | Both job404 envelope fixtures are anonymous. Supply authorized tenant-scoped read principal; preserve exact404/detail/envelope assertions and anonymous401 separately. |
| `tests/contract/test_api_versioning.py` | Anonymous audit/events200 expectation; authenticated same-tenant alias comparison, no anonymous read restoration. |
| `tests/contract/test_api_idempotency_and_pagination.py` | PriceOps replay itself succeeds; audit GET is anonymous. Use separate same-tenant auditor, preserve single approval/event/replay counts and all pagination members. |
| `tests/contract/test_platform_api.py` | Anonymous generic forecastops.score/netplan.solve enqueue fixtures violate supported job auth/type boundary. Use actual supported authorized operation/input fixture for positive enqueue/read/replay/OpenAPI checks, keep explicit unsupported/anonymous denials. |
| `tests/integration/test_adlift_incrementality.py` | Original/b2c model/effect passes, final audit GET lacks audit role/tenant; separate same-tenant auditor, unchanged numerical/evidence/idempotency checks. |
| `tests/integration/test_avm_valuation.py` | Two original/b2c final audit reads lack auditor/tenant. Supported scoped audit fixture, unchanged real estate, durable restart, dataroom/SoD/numerical assertions. |
| `tests/integration/test_gap_ml_003_elasticity_binding.py` | Original/b2c elasticity result succeeds, audit reader lacks tenant/auditor. Scoped reader and owning plan tenant; unchanged recovered -1.5, bounds and model checks. |
| `tests/integration/test_intervention_workflow.py` | Two original/b2c lifecycle positives reach final unsupported audit GET. Separate authorized same-tenant auditor, keep every transition/conflict/approval/maturity/follow-up/label assertion and add foreign denial. |
| `tests/integration/test_production_api_composition.py` | Production dependency-isolation fixture supplies only trusted-dev headers, actual401/no_credentials. Synthetic signed service identity using real boundary; preserve production/live-data/provider/model modes and dependency-specific failures. |
| `modules/avm/tests/test_avm_production_execution.py` | BOTH original/b2c actual-PG/LightGBM/lifelines artifact-schema cases fail HTTP401 after real artifacts pass. Add supported synthetic signed auth; never substitute memory/offline/skip artifacts, PG, schemas or production mode. |
| `tests/integration/test_scoring_model_binding.py` | Anonymous audit reader after model-binding positive; authenticated own-tenant auditor, preserve missing-artifact fail-closed/model-source assertions. |
| `tests/integration/test_sitescore_decision.py` | Domain loop positive reaches audit GET without auditor; supported same-tenant audit principal, retain missing reason422/actual realization and model checks. |
| `tests/reliability/test_job_delivery_state_clear.py` | Anonymous read of custom delivery-state-clear-probe job fails. Supported authorized scoped forecast fixture with actual worker handler/state lifecycle; retain every clear/retain field/retry assertion, not generic-type permission widening. |
| `tests/integration/test_audit_evidence_export.py` | FOUR fresh2bb failures: missing verified tenant in HTTP fixture. Scope principal and newly synthesized source events consistently; preserve201/expiry422/missing-field422/raw-error500 sanitation. Offline direct-service fixtures remain explicitly offline, not HTTP evidence. |
| `tests/integration/test_audit_evidence_persistence.py` | ONE fresh2bb failure: HTTP retained export principal has no tenant. Same supported fixture repair, unchanged retention/checksum/hold/restart/governance denials. |
| `infra/docker/docker-compose.e2e.yml` | Old886 E2E now gets past seed and runs129 cases:13fail/65pass/51notrun. Failure bodies include browser403 on mutations, missing UI state transitions, and pre2bb missing export audit. Docker binds internal0.0.0.0:3000 while browser uses localhost:3100; no ODP_WEB_BASE_URL is declared. Lease ONLY throwaway E2E exact external-origin wiring after confirming failed response, retaining strict CSRF/production settings, timeouts and EVERY inventory member. This suspected fixture-origin seam is not yet a proven root cause for all13. |

This is a finite inventory-derived request, not blanket permission to modify
other APIs/tests/helpers/config. Test `_authz.py` is deliberately NOT requested:
use explicit principals per owning fixture rather than globally adding roles or
tenants to every integration test. Intervention paths are named to avoid silently
expanding into shared persistence after discovering that owning entity has no
tenant; unused leased paths should remain untouched. If any additional owning
path is truly necessary, stop and justify it rather than authoring a waiver.

No edits to these25 paths have been made. Remaining13 E2E failures have complete
failure bodies but no hosted API-tail/response-body artifacts (only performance
artifact was uploaded); all13 must be actually remeasured, not assumed resolved
by origin wiring. Old886 unit and new2bb CI are not yet terminal. The new HTTP
export fix does NOT claim to secure every retained-export listing/download/
governance surface; independent whole-composition coverage remains owed.

## Unchanged final gates and authority

Preserve original COMPLETE BLOCKED six-reopen record, failed owners/epochs,
PR1459@a129, absent-original UNKNOWN and seven human-decision files. Current
readback proves the complete original record equal to preceding allocation;
initial verifier compared it to a wrapper incorrectly, retained/corrected without
any state mutation. Pi is an author and a historically failed original owner,
NOT an independent assessor or failed-epoch clearance.

After all scoped repairs: fresh final-head canonical14, all17 separate bounded
selections, normal terminal hosted CI and configured independent Codex2 supplied-
text assessment with explicit omissions/late measurements/account-provenance
uncertainty are required. Never transfer old/local evidence. Keep1200/420s,
Vitest5000ms, Web300kB, Argon64MiB/time3, native480/540 and all transport/byte/
custody/lifetime/assertion/actual-PG/frozen-lock controls unchanged. Finish owned
work before governed task_finalize.sh submission can supersede the author.
Human/Ops/PENDING, HOLD/quarantine/bindingNULL/cleanupUNKNOWN and goalfalse persist.
No automated approval, merge arming, merge, done, recovery/deployment, live
credentials/accounts/sessions/cloud/IAM/business/data/model/F11 effect.

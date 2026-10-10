# Operator resource provisioning — decision required

Pi · reviewer Codex2 · task `ODP-UI-NETWORK-TABS-PARITY-001` · PR #1440

**Blocked prerequisite, not completion or a review submission.** On this wake,
read-only inspection confirmed the resource gap against fetched canonical
`origin/dev` **10eb6224fa31010fbf3c6f27612ca7d17ba4401e** and resumed task head
**5ef2378861dd3323713afd60dcafcd519e4d11a8**. No application code, API contract,
architecture policy, test expectation or existing screenshot was changed.
`source-receipt.json` pins the inspected blobs and existing negative evidence.

## Verified boundary (line references are at the canonical SHA above)

| Surface | Evidence | Consequence |
| --- | --- | --- |
| Operator URL submit | `modules/opsboard/application/network_listings.py:1238-1269` creates `IN-*` records; no Assignment/SLA resource tokens in this envelope. `apps/api/app/routes/operator_modules/network_listings.py:403-458` invokes this service. | A display owner/name or intake version does not identify an actionable workflow resource. |
| Operator persistence/read | `shared/infrastructure/persistence/operator_network_listings.py:25-48` stores records in `operator.assisted_intakes`; `packages/openapi-client/src/index.ts:1473` reads the Operator route. | Adding token fields to a DTO alone does not provision or join resources. |
| v1 Assignment create | `apps/api/app/routes/listings.py:2791-2810` requires `UuidString` and an existing `active.intakes` record; `:2835-2876` creates an assignment. Typed client `packages/openapi-client/src/index.ts:1529-1540` calls this separate route. | Passing an `IN-*` ID here is not a supported association. The task branch's added recipient/scope checks must also survive integration. |
| v1 read projection | `apps/api/app/routes/listings.py:2575-2577,2596-2606` reads v1 Intake then looks up linked Assignment/SLA. | Existing v1 resource projections do not make the Operator read model shared. |
| Promotion compatibility | `apps/api/app/routes/listings.py:1352-1372` has `V1IntakeRepositoryAdapter` for Operator promotion reads/writes. | This adapter is real, but does not establish UUID workflow foreign keys or SLA initialization. Promotion success cannot certify Transfer/Pause. |
| SLA action routes | `apps/api/app/routes/listings.py:4236-4264,4304-4330` exposes pause/resume on **existing** SLA instances. `modules/listing/application/assignment_sla.py:201-223` has domain creation, not an Operator submit integration. | An actionable resource must be provisioned by an authorized service before screenshots can demonstrate genuine pause/resume success. |
| Normative lifecycle | `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_STATE_CONTRACTS.md` §5 requires Intake-created UNASSIGNED assignments, policy/calendar-calculated due times, authorized recipients and versioned audit/events. `modules/listing/domain/intake_states.py:690-692` reserves SLA initialization to SVC_SLA/emergency authority. | The UI parity worker must not choose a deadline, initialize as a human manager or silently change this contract. |
| SQL constraints | `infra/db/migrations/assisted_listing_intake/001_baseline.sql:351-384` binds workflow resources to UUID Intake FKs and requires SLA policy version/start/due times. | A generated UUID or copied document is not by itself a valid migration/transaction strategy. |

The task branch contains action authorization, directory and read-token repairs,
but still has the same `IN-*` submit, UUID Assignment entry and existing-resource
SLA actions. Those repairs are preserved, not reset to the canonical baseline.

## Existing observed symptom (not a new test run)

Read the committed `sla-status/actual-unavailable/actual-intake-{1440,390}.json`:
both are actual local Operator HTTP responses for synthetic-source `IN-3001`,
READY/v1, with **absent** Assignment/SLA IDs and versions. The paired
`refused-writes-{1440,390}.json` records retain zero resource writes. Deep-link
unavailable screenshots and original execution receipts remain in
[`../sla-status/`](../sla-status/README.md). This is local synthetic-source/header-
stub evidence, not production readiness. No tests were rerun on this docs-only
wake; prior successful runs retain their original source and limitations.

## Requested unblock decision — Human/Ops to route to Product/Platform

Provide a task-scoped approved implementation plan, or an explicitly assigned
prerequisite task with these deliverables:

1. **Identity and persistence:** choose an authoritative Intake representation
   and the durable same-tenant `IN-*` ↔ UUID association/migration strategy.
   Define existing-record backfill, duplicate/idempotency behavior, rollback and
   cross-store failure recovery; do not merely relax `UuidString` or SQL FKs.
2. **Provisioning owner and trigger:** define which existing intake-created
   service/router initializes the UNASSIGNED assignment and SLA, at which
   lifecycle point, and how resources become visible in Operator detail reads.
   Keep human claim/transfer/pause separate from service initialization.
3. **Versioned SLA policy:** identify approved policy data/configuration and
   calendar/timezone, start/due calculation, pause/resume and terminal handling.
   A test-generated deadline can test the mechanism but cannot substitute for
   an approved product policy or be labeled genuine Operator provisioning.
4. **Scope/transaction contract:** retain fresh identity-directory recipient
   validation, linked Intake scope, resource-specific versions, idempotency,
   audit/outbox and fail-closed reads. Specify transaction/reconciliation
   ownership rather than dual-writing disconnected records without recovery.

This note requests decisions; it does **not** approve a new canonical policy,
create another task, waive required evidence or transfer ownership. No additional
mock-only SLA/Transfer increments should be treated as resolving this prerequisite.
Other visual negative states and independent VDC outcomes also remain open.

## Resume/acceptance sequence after the prerequisite

- Verify the approved plan and delivered dependency against freshly fetched
  `origin/dev`; implement only the authorized task-owned integration scope.
- Submit through the actual Operator URL flow, not manually seeded child IDs.
  Read authoritative Assignment/SLA IDs, separate versions and policy lineage
  through the same detail used by the browser. Check duplicate submission,
  tenant/scope denial, unavailable policy and recovery without phantom resources.
- Use the live resource-bound directory for Transfer; obtain actual acknowledged
  write receipts and durable detail rereads/reload for Transfer and Pause/Resume.
  Preserve handoff/reason, conflict drafts, permission gates and pending guards.
- Capture 1440/390 Package 10 reference/before/after pairs with geometry,
  keyboard/scoped accessibility and genuine successful plus negative states.
  Disclose synthetic provider/test accounts separately from resource seeding.
- Finish the remaining all-tab/negative-state/density and independent VDC checks,
  final-scope verification/CI and PR later-spec explanation. Only then use
  `task_finalize.sh` for atomic exact-head review resubmission; do not call
  handoff/re_review/done or certify PR #1440 as ready on this blocker note.

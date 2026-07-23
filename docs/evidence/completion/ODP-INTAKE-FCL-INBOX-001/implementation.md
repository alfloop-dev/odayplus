# ODP-INTAKE-FCL-INBOX-001 Implementation Evidence

Task-ID: ODP-INTAKE-FCL-INBOX-001
Baseline: c900e906f96cb3750274c24e1a8f2922999f9048
Branch: task/ODP-INTAKE-FCL-INBOX-001
Owner: Codex
Status: implemented

## Delivered Scope

- Added complete URL-restorable Inbox query state for saved view, search,
  selection, list/map mode, pagination/cursor, stable sort, and every required
  operational filter.
- Added a UI-local adapter contract for canonical query/page data, bootstrap
  context, saved views, HeatZones, claim results, and authoritative errors.
- Replaced the visual row layout with a semantic table containing the required
  identity, source, method, stage, outcome, issue, ownership, SLA, submitter,
  area, freshness, restriction, and direct-action columns.
- Added a MapLibre geographic view that uses only API-provided coordinates.
  Records without authoritative latitude/longitude remain in an explicit
  unlocated list; HeatZone centroids are never substituted.
- Added direct durable open, review, and correction links plus direct claim and
  retry actions. Generic row preview is retained only as a compatibility hook
  and is not used by those actions.
- Completed Add URL context and URL evidence, including original/canonical URL,
  source host, HeatZone, submitter, tenant/scope/owner, request locking, source
  policy result, and server-returned submission evidence.
- Removed hardcoded HeatZones and saved views. Missing bootstrap data is
  visible and fail closed; URL-selected saved views are sent only when present
  in the authoritative list.
- Removed browser claim policy. The UI sends only an intake ID to the
  integration command adapter and renders the server's assignment receipt;
  due time and idempotency policy are not produced by the Inbox.
- Routes exact duplicates only when the authoritative response says
  `EXACT_DUPLICATE` and supplies `matchResult.targetListingId`.
- Shows a related Listing only for `EXACT_DUPLICATE` or `REVISION` with an
  authoritative target. `POSSIBLE_MATCH` never renders as linked.
- Renders error code, correlation ID, occurred time, retryability, current
  version, current state, and next action without filling missing metadata.

## Authoritative Receipt and Error Rule

The UI does not generate intake IDs, receipt metadata, error codes, correlation
IDs, occurred times, policy results, match outcomes, or listing targets.

Success is rendered only from an `AssistedIntake` returned by the submit API or
from the same authoritative record after the integration container refreshes
the Inbox. A compatibility wait that receives neither result ends without a
success state. The durable primary action uses the returned intake ID; an exact
duplicate uses only the returned existing Listing ID.

Direct claim success and failure similarly render only the adapter's
`AssignmentReceipt` or `AuthoritativeInboxError`. Claim UI state changes occur
after the server response.

## Runtime Shared-File Boundary

FCL-RUNTIME owns canonical `/api/v1/intakes`, signed cursors, submit/claim
commands, exact-duplicate identity, mutation, lifecycle, revision, and shared
client schemas.

The independent-review correction removes this task's previous changes to:

- `apps/api/app/routes/operator_modules/network_listings.py`;
- `packages/openapi-client/src/index.ts`; and
- `tests/contract/test_operator_assisted_listing_api.py`.

The final branch diff against baseline `c900e906` is empty for all three paths.
Inbox-specific shapes live only in `inboxContracts.ts` until Runtime and
Integration provide their canonical adapters. The legacy endpoint is neither
retained nor extended by this task.

## Integration Adapter Required

`ODP-INTAKE-FCL-INTEGRATION-001` must compose this slice as follows:

1. Resolve authoritative tenant, scope, submitter, owner, permitted HeatZones,
   and saved views from bootstrap/API data and pass them through
   `bootstrapContext` and `savedViews`. Missing data must remain unavailable.
2. Send `onQueryChange` to Runtime's canonical `/api/v1/intakes` query adapter,
   then pass the mapped records and `IntakeInboxPageContract` back to the view.
3. Preserve Runtime's signed `nextCursor` and `previousCursor` exactly. Never
   decode, regenerate, or reuse a cursor after filters or sort change.
4. Make `onAddSubmit` call the canonical submit command and return its complete
   `AssistedIntake` response. Do not discard the response and do not construct
   a UI receipt from the request.
5. Implement `onClaimIntake(intakeId)` with Runtime's canonical server command.
   Server policy owns owner, due time, idempotency, and concurrency metadata.
6. Pass canonical API errors through `AuthoritativeInboxError`, including
   server code, correlation ID, occurred time, retryability, current version,
   current state, and next action. Do not fill absent values.
7. Mount `/w/expansion/listings/intake/:intakeId` as the durable intake route.
   For `EXACT_DUPLICATE`, use `matchResult.targetListingId` to restore the
   existing Listing selection instead of opening the intake as a Listing.
8. Provide the governed `NEXT_PUBLIC_ODP_MAP_TILE_URL` and attribution at build
   time for live basemap tiles. Missing coordinates remain unlocated.

## Files

- `apps/web/features/operator/network/intake/AddListingFromUrlDialog.tsx`
- `apps/web/features/operator/network/intake/IntakeInboxMap.tsx`
- `apps/web/features/operator/network/intake/ListingInboxIntakeView.tsx`
- `apps/web/features/operator/network/intake/inboxContracts.ts`
- `apps/web/features/operator/network/intake/intake.module.css`
- `apps/web/features/operator/network/intake/useIntakeInboxQuery.ts`
- `apps/web/features/operator/network/intake/__tests__/AddListingFromUrlDialog.test.tsx`
- `apps/web/features/operator/network/intake/__tests__/ListingInboxIntakeView.test.tsx`

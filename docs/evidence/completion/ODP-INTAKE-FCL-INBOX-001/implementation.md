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
- Extended the read-only Inbox API response with server-side filters, query-
  bound expiring cursors, stable ordering, count summaries, authoritative
  location data, restriction/retry indicators, and listing targets.
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
- Routes exact duplicates only when the authoritative response says
  `EXACT_DUPLICATE` and supplies `matchResult.targetListingId`.

## Authoritative Receipt and Error Rule

The UI does not generate intake IDs, receipt metadata, error codes, correlation
IDs, occurred times, policy results, match outcomes, or listing targets.

Success is rendered only from an `AssistedIntake` returned by the submit API or
from the same authoritative record after the integration container refreshes
the Inbox. A compatibility wait that receives neither result ends without a
success state. The durable primary action uses the returned intake ID; an exact
duplicate uses only the returned existing Listing ID.

Direct claim success and failure similarly render only the API's
`AssignmentReceipt` or `IntakeApiError`. Claim UI state changes occur after the
server response.

## Runtime Shared-File Boundary

FCL-RUNTIME owns mutation, lifecycle, revision, and identity behavior. This
task changes only the following read-query and response DTO hunks in shared
files:

1. `apps/api/app/routes/operator_modules/network_listings.py`
   - read-only Inbox imports and `_INTAKE_*` query/cursor/decorator helpers;
   - `GET /api/v1/operator/network-listings/intake` parameters, validation,
     filtering, sorting, cursor pagination, and response envelope;
   - no submit, correction, decision, assignment, lifecycle, revision,
     promotion, merge, split, or unmerge handler was changed.
2. `packages/openapi-client/src/index.ts`
   - `IntakeInboxLocation`;
   - read-response fields appended to `AssistedIntake`;
   - `IntakeInboxQuery` and `IntakeInboxPage`;
   - no client mutation method or mutation payload was changed.

Runtime integration must preserve these read hunks while taking authority over
the mutation/lifecycle sections of the same files.

## Integration Adapter Required

`ODP-INTAKE-FCL-INTEGRATION-001` must compose this slice as follows:

1. Call `listIntakes(buildIntakeInboxServerQuery(...))` whenever
   `onQueryChange` fires, then pass both `page.items` and the complete
   `IntakeInboxPage` back to `ListingInboxIntakeView`.
2. Preserve the opaque `nextCursor` and `previousCursor` exactly. Never decode,
   regenerate, or reuse a cursor after filters or sort change.
3. Make `onAddSubmit` call the canonical submit API and return its complete
   `AssistedIntake` response. Do not discard the response and do not construct
   a UI receipt from the request.
4. Pass canonical API errors through `IntakeApiError`, including server code,
   correlation ID, occurred time, retryability, current version, and next
   action. Do not fill absent values in the UI.
5. Mount `/w/expansion/listings/intake/:intakeId` as the durable intake route.
   For `EXACT_DUPLICATE`, use `matchResult.targetListingId` to restore the
   existing Listing selection instead of opening the intake as a Listing.
6. Provide the governed `NEXT_PUBLIC_ODP_MAP_TILE_URL` and attribution at build
   time for live basemap tiles. Missing coordinates remain unlocated.

## Files

- `apps/api/app/routes/operator_modules/network_listings.py`
- `apps/web/features/operator/network/intake/AddListingFromUrlDialog.tsx`
- `apps/web/features/operator/network/intake/IntakeInboxMap.tsx`
- `apps/web/features/operator/network/intake/ListingInboxIntakeView.tsx`
- `apps/web/features/operator/network/intake/intake.module.css`
- `apps/web/features/operator/network/intake/useIntakeInboxQuery.ts`
- `apps/web/features/operator/network/intake/__tests__/AddListingFromUrlDialog.test.tsx`
- `apps/web/features/operator/network/intake/__tests__/ListingInboxIntakeView.test.tsx`
- `packages/openapi-client/src/index.ts`
- `tests/contract/test_operator_assisted_listing_api.py`

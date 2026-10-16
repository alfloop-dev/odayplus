# Personal listing criteria and canonical Find Areas source

The Package 10 action is `建立物件搜尋條件`. The user confirmed personal
save/reopen/edit/delete on 2026-10-16. The original prototype additionally says
that manual submissions and approved feeds are matched when received. This
action therefore uses durable receipt matching; it does not search external
sites, schedule scraping, create candidates or substitute a review workflow.

The other confirmed action, `加入季度展店計畫`, starts an area planning draft
before the user selects a quarter, candidates and budget. That feature is still
outstanding. This change must not be described as its implementation.

## Ownership and delivery

`listing-search.v1` is a reserved private query on the existing native SavedView
owner. The actor and tenant come from the authenticated principal. Commands
use the native PostgreSQL owner, expected versions, actor-scoped idempotency,
transactional audit and deletion tombstones. The native saved-view list applies
the current zone policy to this reserved query too.

Search/receipt writes stage immutable revision events in the same transaction.
The registered `listing-search-receipt-match` worker handles committed events
and verifies the actual queue job, lease, fence, tenant and facts digest. It
does not lock intake parents or fetch source pages. Same-version changed facts
are refused even if they would produce the same match outcome.

The ledger is internal evidence. Result reads recheck the current private owner,
query/receipt revision, complete record scope, source/snapshot admission and
field masks. Missing or masked effective facts cannot become favorable values.
Public job enqueue/read/retry cannot expose or forge internal matching jobs or
private command receipts, including their reserved idempotency namespaces.

## Canonical zone read

The original production listing aggregate had no producer for canonical
HeatZones. A fresh tenant could have a real native result while Find Areas
remained empty. The live Operator route now reads the tenant's native
`heatzone_store.list_scores` through `OperatorLiveRepository.load_heat_zones`.
This read is independent of unrelated workspace readiness and checks the
original native/finite Operator domain read permission first.

`network_heat_zones` validates native identity, H3, metrics, times, versions and
lineage, rejects contradictory tenant/fixture metadata and projects every
reader's complete scope before counts and selection. It never saves zone rows
to the listing aggregate or uses a visible listing to invent aggregate scope.
Native HeatZone results currently lack brand/store/assigned-area ownership;
readers restricted on an absent axis receive no zone aggregate. Unknown facts
remain null, and missing geometry remains unavailable. A failed source clears
the zone collection and carries explicit unavailability; it does not erase
otherwise permitted Radar rows or report an observed zero-zone count.

## UI and verification

The existing six actions and design-system Modal are retained. The
search action needs a valid current API snapshot containing the selected zone
and the existing submission role; a fallback card cannot enable it. Accepted
commands are read back before the UI says they are verified. Late session or
permission loss clears private content. Uncertain responses keep the same
command identity and inputs.

Tests cover criteria boundaries, actual PostgreSQL transactions/worker delivery,
normal-app job attacks, native SavedViews, source revocation, zone projection,
cross-tenant and full-axis filtering, late UI invalidation and actual browser
CRUD/worker matching at 1440/1024/390 pixels. The browser fixture uses an owned
synthetic database, real routers and the registered worker, without route
interception. Its results are engineering evidence, not human UAT, real login,
production model admission or permission to deploy. Retain failed attempts and
bind reported results to the exact tested source; original timeouts, retries
and bundle limits must remain unchanged.

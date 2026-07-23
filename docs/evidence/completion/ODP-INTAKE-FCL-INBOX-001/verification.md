# ODP-INTAKE-FCL-INBOX-001 Verification Evidence

Task-ID: ODP-INTAKE-FCL-INBOX-001
Baseline: c900e906f96cb3750274c24e1a8f2922999f9048
Branch: task/ODP-INTAKE-FCL-INBOX-001
Owner: Codex

## Passed

```bash
npm run typecheck --workspace=@oday-plus/web
```

Result: passed.

```bash
npm run test --workspace=@oday-plus/web -- \
  --run features/operator/network/intake/__tests__
```

Result: 8 files passed, 85 tests passed.

```bash
npm run build --workspace=@oday-plus/web
```

Result: production build passed. Existing autoprefixer warnings came from
unmodified `designAligned.module.css`, `governance.module.css`, and
`networkFindAreas.module.css`.

```bash
git diff --check
```

Result: passed.

## Focused Assertions

- Inbox component tests assert semantic table roles/headers and `aria-sort`,
  all adapter query values, URL list/map/selection restoration, browser
  back/forward restoration, direct action routing, server claim receipt, retry,
  MapLibre coordinates, and the unlocated list.
- Ownership tests assert only authoritative saved-view and HeatZone props are
  rendered; missing bootstrap data is unavailable; unknown URL saved views are
  removed from the adapter query; claim receives only the intake ID; and no
  browser due time is sent.
- Match tests assert `POSSIBLE_MATCH` has no Listing link while
  `EXACT_DUPLICATE` and `REVISION` require an authoritative target.
- Error tests assert code, correlation ID, occurred time, retryability, current
  version, current state, and next action are all visible.
- Add URL component tests assert validation, canonical preview, submit locking,
  bootstrap-only operational context, missing-context fail-closed behavior,
  server-only receipt values, durable intake navigation, and exact-duplicate
  navigation by authoritative existing Listing ID.

## Runtime Isolation

```bash
git diff c900e906f96cb3750274c24e1a8f2922999f9048 -- \
  apps/api/app/routes/operator_modules/network_listings.py \
  packages/openapi-client/src/index.ts \
  tests/contract/test_operator_assisted_listing_api.py
```

Result: empty. This task retains no legacy API, shared client, or legacy
endpoint contract-test changes.

## Integration Proof Still Owned Elsewhere

The task contract assigns full durable-route composition and full Playwright
coverage to `ODP-INTAKE-FCL-INTEGRATION-001` after Wave 1 is terminal. This
branch therefore does not edit
`tests/e2e/operator-assisted-listing-intake.spec.ts` or mount the dynamic route.

Integration must add the final real-API browser proof for:

- list/map query and selection restoration through the mounted production
  route;
- exact-duplicate submit response followed to the existing Listing;
- durable intake reopen at
  `/w/expansion/listings/intake/:intakeId`.

Those are integration handoffs, not UI-generated substitutes. This branch has
the focused UI contract assertions needed for the adapter.

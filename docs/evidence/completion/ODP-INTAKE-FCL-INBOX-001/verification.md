# ODP-INTAKE-FCL-INBOX-001 Verification Evidence

Task-ID: ODP-INTAKE-FCL-INBOX-001
Baseline: c900e906f96cb3750274c24e1a8f2922999f9048
Branch: task/ODP-INTAKE-FCL-INBOX-001
Owner: Codex

## Passed

```bash
ruff check apps/api/app/routes/operator_modules/network_listings.py \
  tests/contract/test_operator_assisted_listing_api.py
```

Result: passed.

```bash
npm run typecheck --workspace=@oday-plus/web
npm run typecheck --workspace=@oday-plus/openapi-client
```

Result: both passed.

```bash
npm run test --workspace=@oday-plus/web -- \
  --run features/operator/network/intake/__tests__
```

Result: 8 files passed, 81 tests passed.

```bash
pytest -q tests/contract/test_operator_assisted_listing_api.py
```

Result: 54 tests passed. The only output warning is the existing
Starlette/httpx TestClient deprecation warning.

```bash
npm run build --workspace=@oday-plus/web
```

Result: production build passed. Existing autoprefixer warnings came from
unmodified `designAligned.module.css`, `governance.module.css`, and
`networkFindAreas.module.css`.

```bash
OPSBOARD_PORT=3112 ODP_API_PORT=8102 \
ODP_API_BASE_URL=http://127.0.0.1:8102 \
ODP_PLAYWRIGHT_REUSE_EXISTING=1 \
npx playwright test tests/e2e/operator-assisted-listing-intake.spec.ts \
  --grep 'canonical 1' --project=chromium
```

Result: 1 browser flow passed against the task worktree API and web server.

```bash
git diff --check
```

Result: passed.

## Focused Assertions

- API contract tests exercise each required filter, each saved view, stable
  ordering, opaque next/previous cursor use, cursor/query mismatch, malformed
  cursor, timestamp validation, field decoration, located records, and
  unlocated records.
- Inbox component tests assert semantic table roles/headers and `aria-sort`,
  all server query values, URL list/map/selection restoration, browser
  back/forward restoration, direct action routing, server claim receipt, retry,
  MapLibre coordinates, and the unlocated list.
- Add URL component tests assert validation, canonical preview, submit locking,
  operational context, server-only receipt values, durable intake navigation,
  and exact-duplicate navigation by authoritative existing Listing ID.

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
the component and API contract assertions needed for the adapter.

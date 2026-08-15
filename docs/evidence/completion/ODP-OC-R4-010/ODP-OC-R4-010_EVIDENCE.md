# ODP-OC-R4-010 Evidence

Task: Harden auth tenant isolation idempotency privacy and observability.

## Source Package

Canonical package 6:
`docs_archive/00_source_zips/operator_console/r4-20260707-package-6/Oday Plus 營運管理後台 (6).zip`

- SHA256: `db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76`
- Interactive HTML SHA256 from manifest: `65d359f4abaf82b39eb16f67da8e91e7ad1b030628bc15f8f45ce7c18c0e2f48`
- `unzip -t`: passed, no compressed data errors.
- Manifest: `docs_archive/00_source_zips/operator_console/r4-20260707-package-6/manifest.json`

Relevant package 6 `data-screen-label` values used:

- `Store Ops 門市營運`
- `Dialog Camera Purpose`
- `Growth 營收成長`
- `Network SiteScore Lab`
- `Govern 治理稽核`

## Security Coverage

- Protected Operator reads require a principal: missing headers return `401`.
- Authenticated principals without Operator scope return `403`.
- Wrong tenant receives `403` before entity lookup; same-tenant unknown IDs return `404`, proving entity existence is not leaked cross-tenant.
- Camera purpose write is idempotent and records one shared audit event for replayed requests.
- Camera purpose audit metadata includes purpose and idempotency key, but excludes submitted media canaries such as `mediaSecret`, `signedPlaybackUrl`, and `auditNote`.

## Visual Evidence

Screenshots are in `docs/evidence/completion/ODP-OC-R4-010/screenshots/`.
`manifest.json` pairs archive package 6 and local app captures by surface and viewport.

- Desktop captures use element-level screenshots of the relevant package/app surface.
- Constrained captures are true `390 x 844` viewport screenshots after scrolling to the relevant surface.
- This task did not change CSS/layout. The only visual metadata change is adding `data-screen-label="Growth 營收成長"` to the local Growth root for package traceability. No task-owned visual deltas were found in the captured surfaces.

## Verification

Commands run successfully:

```bash
sha256sum "docs_archive/00_source_zips/operator_console/r4-20260707-package-6/Oday Plus 營運管理後台 (6).zip"
unzip -t "docs_archive/00_source_zips/operator_console/r4-20260707-package-6/Oday Plus 營運管理後台 (6).zip"
uv run pytest tests/security/test_operator_security_platform.py -q
uv run pytest tests/security tests/integration -k operator -q
uv run pytest tests/contract/test_operator_api.py tests/contract/test_operator_growth_api.py tests/contract/test_operator_governance_api.py tests/contract/test_operator_network_scoring_api.py tests/contract/test_operator_network_review_api.py tests/contract/test_operator_network_listings_api.py tests/contract/test_operator_network_rebalance_api.py -q
npm run typecheck --workspace=@oday-plus/web
ODP_OPERATOR_PRODUCT_GATE=1 npx playwright test tests/e2e/e2e-operator-console.spec.ts --project=chromium --grep "productization gate"
```

Observed non-blocking warnings:

- Starlette/httpx deprecation warnings in FastAPI TestClient.
- Existing CSS autoprefixer warnings for `start`/`end` alignment values during Next/Playwright runs.

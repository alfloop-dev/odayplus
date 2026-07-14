# ODP-OC-R4-005 Verification Receipt

## Source Preflight

Canonical source: `docs_archive/00_source_zips/operator_console/r4-20260707-package-6/`

- `docs_archive/00_source_zips/operator_console/LATEST.json` resolves to package 6.
- ZIP SHA-256 verified:
  `db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76`
- Interactive HTML SHA-256 verified:
  `65d359f4abaf82b39eb16f67da8e91e7ad1b030628bc15f8f45ce7c18c0e2f48`

Commands run:

```bash
test "$(sha256sum "docs_archive/00_source_zips/operator_console/r4-20260707-package-6/Oday Plus 營運管理後台 (6).zip" | cut -d ' ' -f 1)" = "db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76"
unzip -t "docs_archive/00_source_zips/operator_console/r4-20260707-package-6/Oday Plus 營運管理後台 (6).zip"
sha256sum "docs_archive/00_source_zips/operator_console/r4-20260707-package-6/extracted/Oday Plus Operator Console.dc.html"
```

## Focused Checks

Commands run on this worktree after the audit reopen:

```bash
uv run pytest tests/contract -k 'listing or network'
npm run typecheck --workspace=@oday-plus/web
ODP_API_PORT=8114 OPSBOARD_PORT=3114 ODP_API_BASE_URL=http://127.0.0.1:8114 npx playwright test tests/e2e/operator-network-listings.spec.ts tests/e2e/e2e-map.spec.ts
ODP_API_PORT=8114 OPSBOARD_PORT=3114 ODP_API_BASE_URL=http://127.0.0.1:8114 npx playwright test tests/e2e/e2e-operator-console.spec.ts -g "Network workspace exposes" --timeout=90000
```

Results:

- `10 passed, 125 deselected` for contract tests.
- Web typecheck passed.
- Playwright Network Listing Radar + map suite: `12 passed`.
- Playwright Operator Console Network workspace focused case: `1 passed`.

## Evidence Capture

Capture servers:

- Product API: `http://127.0.0.1:8115`
- Product web: `http://127.0.0.1:3115/operator?ws=network`
- Archived package 6 HTML: `http://127.0.0.1:8126/Oday%20Plus%20Operator%20Console.dc.html`

Capture details are recorded in:

- `api-proof.json`
- `map-pixel-proof.json`
- `screenshot-manifest.json`


# ODP-OC-R4-008 Verification

## Commands

- `test $(sha256sum docs_archive/00_source_zips/operator_console/r4-20260707-package-6/Oday\ Plus\ 營運管理後台\ \(6\).zip | cut -d \  -f 1) = db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76`
- `unzip -t docs_archive/00_source_zips/operator_console/r4-20260707-package-6/Oday\ Plus\ 營運管理後台\ \(6\).zip`
- `uv run pytest tests/contract/test_operator_network_rebalance_api.py -q`
- `uv run pytest tests/contract -k 'rebalance or avm or netplan' -q`
- `npm run typecheck --workspace=@oday-plus/web`
- `ODP_API_BASE_URL=http://127.0.0.1:8299 OPSBOARD_PORT=3300 ODP_API_PORT=8299 ODP_PLAYWRIGHT_REUSE_EXISTING=1 npx playwright test tests/e2e/operator-network-rebalance.spec.ts`
- `ODP_API_BASE_URL=http://127.0.0.1:8299 OPSBOARD_PORT=3300 ODP_API_PORT=8299 ODP_PLAYWRIGHT_REUSE_EXISTING=1 npx playwright test tests/e2e/e2e-operator-console.spec.ts -g "Network workspace exposes"`

## Result

- Package 6 SHA verified and ZIP integrity passed.
- Contract tests passed.
- Web typecheck passed.
- Rebalance Playwright spec passed against dedicated local API/web servers.
- Existing Network workspace Playwright coverage passed after resetting network-listings and network-rebalance state at test start.

## Notes

- `npm ci` was required because `node_modules` was absent in this worktree.
- Local Playwright default reused a stale API server on port 8099; final e2e verification used dedicated ports 8299/3300 with reuse-existing after manually starting current worktree servers.

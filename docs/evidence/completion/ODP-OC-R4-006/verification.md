# ODP-OC-R4-006 Verification Receipt

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

Commands run on this worktree:

```bash
uv run pytest tests/contract -k 'candidate or sitescore'
uv run npx playwright test tests/e2e/operator-network-scoring.spec.ts
```

Results:

- `6 passed` for contract tests.
- Playwright Network scoring E2E suite: `4 passed`.
- Source archive SHA and `unzip -t` passed for package 6.

## Evidence Capture

Capture details are recorded in:

- `api-proof.json`
- `screenshot-manifest.json`

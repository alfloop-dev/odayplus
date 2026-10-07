# ODP-NPM-DEPLOY-AUDIT-RECOVERY-001

## Observed blocker and scope

Latest protected-dev Deploy Dev run [37499848122](https://github.com/alfloop-dev/odayplus/actions/runs/37499848122) at `ada72a9ebe8d311253ef6e22eb50d0905b98755d` failed **Run production npm audit gate** before building images or deploying. The downloaded hosted receipt reports one high finding. Local audit of the same lock identifies `sharp <0.35.5`, [GHSA-wq5f-xc86-pv6w / CVE-2026-96889](https://github.com/advisories/GHSA-wq5f-xc86-pv6w): librsvg use-after-free.

This patch changes only the root sharp override and its locked native dependency closure: sharp/native bindings `0.35.4 -> 0.35.5`, libvips packages `1.3.3 -> 1.3.4`, and the nested WASM runtime `1.11.1 -> 1.11.3` required by the new binding. No framework upgrades, advisory suppression, threshold change, authorization change, cloud operation or deployment rerun.

## Exact tested source

Dependency source commit: `587b61ce8c5ce47b8d3a885601d4b66701a6d9c7` (subsequent evidence-only commit does not modify these inputs).

| File | SHA-256 |
|---|---|
| `package.json` | `ec75ecd952f960dae794807c7a5af106f51ce1f4c7fc87cff3ccde5411eb6bc7` |
| `package-lock.json` | `bb287c16ade4a4a83abe9792499adab7ad8c38607fb6750dbd45f41e89edf9e2` |

npm's workspace refresh retained the old optional sharp despite the new override. To avoid refreshing unrelated dependencies, an isolated package with `optionalDependencies: {"sharp":"0.35.5"}` was resolved using `npm install --package-lock-only --ignore-scripts --prefer-online`; its npm-generated sharp/native records (including registry integrity values) replaced the matching 28 records in the existing lock. The remaining records and dependency declarations are unchanged. A clean full `npm ci` then validated the resulting lock and downloaded/verified the native binaries.

## Local verification (2026-10-07 UTC)

Environment: Node `v22.23.2`, npm `10.9.8`, Linux x64.

- `NODE_ENV=development npm ci --include=dev --include=optional --no-audit --no-fund`: PASS, 486 packages installed.
- `npm ls sharp`: Next `15.5.25` resolves sharp `0.35.5`.
- `ODAY_RELEASE_SHA=587b61ce8c5ce47b8d3a885601d4b66701a6d9c7 python3 delivery_toolchain/security/npm_audit_gate.py --receipt ...`: PASS, zero production findings at every severity; unchanged `high` threshold. See `local-after-receipt.json` and `local-after-audit.json`.
- Native smoke: `sharp.versions.sharp === '0.35.5'`; generate an 8x8 raster and parse an 8x8 SVG, resize both to 4x4 PNG, assert dimensions/format/nonempty buffers: PASS. Loaded `vips 8.18.7`, `rsvg 2.63.2`.
- `NODE_ENV=test npm run test --workspace=@oday-plus/web`: **63 files / 605 tests passed**. Expected connection-refused stderr from local test fixtures did not fail tests.
- `NODE_ENV=production npm run build --workspace=@oday-plus/web`: PASS, including type validation/static generation/standalone tracing.
- `npm run bundle:budget --workspace=@oday-plus/web`: PASS, Operator 291.7/300 kB, Intake 289.0/300 kB, Franchisee 116.6/130 kB.
- `git diff --check`: PASS.

## Release boundary

These are local engineering checks, not hosted-CI or live acceptance. Independent review and protected branch CI must pass before the existing standing dev deployment lane builds a fresh immutable candidate. No old failed build is reused, no alternative deploy path is created, and staging/production human gates remain unchanged. Live login/admin/E2E readback and the parent rollout remain open until actual deployment succeeds.

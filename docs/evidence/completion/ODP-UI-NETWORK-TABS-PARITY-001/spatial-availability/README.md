# Spatial list availability — bounded owner increment

Task `ODP-UI-NETWORK-TABS-PARITY-001` · Pi · reviewer Codex2 · existing PR #1440.
**Not full acceptance or a formal resubmission. Keep task in_progress.**

## Boundary / reference

Package 10 contains no Spatial/HZ-006 screen. The unchanged reference captures
`design-network-no-spatial-{1440,390}.png` and `design-absence-*.json` record that
absence, not a fictional Spatial design or pixel-parity result. This later-spec
integration uses the Network compact header, outlined secondary button, bordered
status panel, indigo focus, and responsive wrapping. No source permissions, role
grants, decision API, API fixture records or product write contracts changed.

All availability screenshots use controlled browser **list read** responses:
500, 403, missing `items`, empty, and one unit-contract-shaped proposal. Other
Network reads use the existing local fixture backend. These are not production
observations, real permission grants, generation/preview/decision receipts,
durable writes or a complete live business acceptance. The recovered proposal
is controlled data, not a fabricated backend readback.

## Repairs / anchored provenance

- Before render: `5e1461098237`; capture harness and baseline anchored in
  `a7269cf73a70`. Pending reads, HTTP failures and malformed envelopes formerly
  appeared as an enabled `全部提案 (0)` / empty success.
- Implementation: `8011d06c6201`. List client rejects HTTP failures and invalid
  list envelopes rather than returning an empty list. Workspace records actual
  pending/error/empty/ready states even in fixture mode, clears stale data on
  failure, and ignores superseded read results after tab/persona changes.
- Spatial owns its loading/empty/error UI in production too (the seed-data gate
  remains). Pending/error count is explicitly unconfirmed; filters disabled;
  stale detail and decision controls absent. Failed reads expose scoped retry.
- The supplemental harness initially retained only one pending resolver. An
  extra hydration read after mobile reload could remain blocked indefinitely.
  It now releases all pending requests and allows later reads to finish; this
  does not weaken the loading/error/geometry/AA assertions.

Before/after pairs at1440 and390 cover pending, HTTP500, HTTP403 and malformed
list envelope. After-only recovery is a newly reachable retry path, not a
claimed before pair. Empty/proposal regressions also run at1024. Pi inspected
before/after mobile failure and desktop failure/reference: the error explanation
and retry button fit the panel and remain readable; previously failed reads
claimed no proposals. The local Next dev indicator is not a product control.

## Original terminal receipts

Every command completed synchronously; adjacent `.exit` is its original exit.
No grep waits, inferred process completion or count-only reruns.

| Check | Actual result | Receipt |
|---|---|---|
| before availability diagnostic | 2 passed, exit0 | `before.log/.exit` |
| focused client/panel/production workspace units | 24 passed, exit0 | `focused.log/.exit` |
| final availability/retry geometry and scoped AA | 2 passed, exit0 | `after.log/.exit` |
| empty/proposal/reference regression | 3 passed, exit0 | `render-regression.log/.exit` |
| full web Vitest | 759 passed /72 files, exit0 | `unit.log/.exit` |
| web lint / typecheck | exit0 each | `lint.*`, `typecheck.*` |
| code boundaries | exit0; tracked inventory unchanged | `boundaries.*` |
| business E2E inventory |125 tests /18 files, exit0 | `inventory.*` |
| whitespace | exit0 | `diff.*` |

Sixteen final scoped axe reports contain no AA violations. Geometry asserts
panel containment/scrollWidth/document width, plus existing child containment
and mobile stacking. Units exercise production retry and a late failed read
from a superseded persona. This is not whole-page WCAG, modal keyboard proof or
independent VDC approval.

Retained failure: `after-initial.log/.exit` (1 passed /1 failed, exit1),
`after-initial-context.md` and `diagnostic-*` screenshots/axe/geometry retain the
mobile orphaned-read failure before the harness repair. Final evidence is
`after-*`, not the partial diagnostics. All source/artifact hashes are adjacent.

Reproduce from repo root, without concurrent production build:

```sh
OPSBOARD_PORT=3310 ODP_API_PORT=8310 ODP_API_BASE_URL=http://127.0.0.1:8310 \
NETWORK_PARITY_DESIGN=1 NETWORK_PARITY_CAPTURE_PHASE=after \
NETWORK_PARITY_EVIDENCE_DIR="$PWD/docs/evidence/completion/ODP-UI-NETWORK-TABS-PARITY-001/spatial-availability" \
npx playwright test --config tests/visual/operator-network-tabs-parity.config.ts operator-spatial-surface-parity.spec.ts
npm test --workspace=@oday-plus/web
npm run lint --workspace=@oday-plus/web
npm run typecheck --workspace=@oday-plus/web
python3 delivery_toolchain/governance/check_code_boundaries.py --write-inventory
npx playwright test --list
```

## Remaining / next owner work

Still required: genuine Spatial generation, preview, decision and reload evidence;
split/modal/permission/conflict geometry; refreshed authority while a decision
is pending (read-generation guard is NOT a decision/modal guard), post-write
readback-failure acknowledgement, invalid proposal-item validation beyond the
list envelope, and other task negative pairs. Retain real Transfer/Pause IDs and
versions, Find Areas reload-before-back/full-density follow-up, final CI/PR
later-spec explanation and independent VDC outcomes from the prior brief. Do not
run task_finalize/handoff/re_review/done merely for this bounded increment.

# Radar sources / list / detail — bounded owner increment

Task `ODP-UI-NETWORK-TABS-PARITY-001` · Pi · reviewer Codex2 · PR #1440

**Not full task acceptance or review resubmission.** Find Areas, Rebalance,
Spatial, remaining batch/error/permission/conflict states, genuine Transfer/Pause
resource authority and independent VDC outcomes remain open. Do not run
`task_finalize.sh` on this incremental evidence alone.

## Provenance

- Resumed clean task branch at `fd43efa42e7c`; fetched `origin/dev` remains
  `2fe3ef933794d0bb293449b74dffe9602d53f767`, an ancestor, rechecked after tests.
- `b152cfb5ea92`: supplemental Radar capture harness, no product changes.
- `54466d1ecdb5`: actual pre-edit 1440/390 captures, including empty-source
  diagnostic. `before-source.sha256` binds code/reference/harness used there.
- `2e83d084b66e`: product repair plus four focused unit cases. Rendering is
  unchanged by subsequent test-only TypeScript fixes, tablet/empty assertions
  and evidence notes. `source.sha256` binds final source and harness.
- Reference is unchanged archived Package 10 standalone, expansion-manager
  persona. External reference requests are blocked. Only prototype navigation
  and clipped reference source buttons use DOM events; product uses real clicks.
  Persona toast disappears naturally before capture; no content is synthesized.
- Product reads the isolated local fixture API without interception. Intake has
  zero real submissions here; reference has ten mock submissions. We do not
  fabricate Intake rows, assignment/SLA authority or extra listing sources.

## Real repairs and design-language integration

Desktop source cards already used a **six-track auto-fill grid**, not the two
stretched columns from the original audit. Intake search/select controls already
formed a compact row. This increment verifies those existing repairs rather than
claiming to have introduced them. With two actual sources, vacant tracks stay
vacant; no nine-source mock inventory is added. Source status badge no longer
shrinks and splits `已連接` across lines.

The reference's **180 / 844 / 348px** desktop source/list/detail hierarchy remains.
Source URL metadata now wraps instead of silently clipping. Detail preserves
semantic `dl/dt/dd`, the 86px photo placeholder, actual values and evidence refs.
Missing hard-rule metadata displays `未提供檢查結果`, not an invented `3/3 通過`;
source-provided summaries remain untouched. Legitimate zero frontage stays `0m`.

Reference-style outlined Watchlist, broker-contact and direct-SiteScore entries
are explicitly disabled, with visible unavailable-service explanation. The
previously clickable but unimplemented Radar map toggle is also disabled. The
separate functional Intake map and Find Areas map remain unchanged. Secondary
archive uses the existing actual handler only for its supported hard-rule case;
no general-purpose archive operation or permission grant is invented.

Row and detail now share terminal, supported-service and hard-rule gates. Detail
cannot convert a failed-rule row, mint another pending write, advertise an absent
handler or silently no-op on an already-created candidate. Candidate navigation
without a real callback is disabled with a note directing operators to its tab.
The existing bounded demo IDs and merge-role gates are preserved; backend checks
remain authoritative. There are no success toasts for these unavailable actions.

An empty source/zone intersection previously retained a different source's detail
and conversion button. Both actual **before-empty** images show this bug. It now
retires stale detail and all write controls. The reference also retains stale
detail in its zero-row source state; reproducing that contradiction would be
unsafe, so after-empty shows an explicit unavailable selection instead.

Every listing title now has a focusable native selection button with selected
state and visible focus. Supplemental tests use keyboard Enter to select a
*different* row and return to L-2024, not merely focus the already-selected row.
Mouse selection and existing row write controls remain available.

Scoped axe baseline found **24 low-contrast nodes** at each width. Radar-only
muted labels/evidence and teal chips are minimally darkened within the design
palette. Shared panels are not recolored. Final list and empty-filter states
have zero scoped violations at 1440, 1024 and 390 (WCAG 2 A/AA, 2.1 AA, 2.2 AA).
This is not a whole-application accessibility certificate.

## Images and geometry inspected by Pi

`shots/` contains design/before/after list and empty-filter **1440/390** PNGs,
plus supplemental after **1024** images. Reference empty source is `永慶 0`;
product empty state is actual broker source intersected with HZ-01. These are
structurally paired zero-row states, not identical data inventories. Full-page
images are clipped to the requested viewport width, never widened to hide overflow.

| Geometry | 1440 | 1024 | 390 |
|---|---:|---:|---:|
| panel width / document scrollWidth | 1400 / 1440 | 984 / 1024 | 366 / 390 |
| source tracks | 6 | 4 | 1 |
| source card width | 226.66 | 240 | 366 |
| filter / list / detail width | 180 / 844 / 348 | 180 / 790 / 984 | 366 / 366 / 366 |
| desktop gutter | 14 | 14 | stacked |
| search height | 35 | 35 | 35 |

Pi inspected desktop and mobile design/before/after, empty-state pairs and final
tablet images. Tablet detail spans below the two-column source/list, and mobile
stacks all three. Source chips scroll locally on mobile. Primary/outlined
secondary hierarchy and full evidence remain reachable. Shorter real lists,
two source cards and empty real Intake are data differences, not pixel-parity
claims. The Next dev indicator remains visible in implementation captures.

## Verification — original synchronous exit receipts

Every run has `logs/<name>.log` and `.exit`, captured immediately from its original
shell command; no process-liveness guesses or reruns solely to count tests.
`verification.json` records accepted and diagnostic runs; `artifacts.sha256`
binds the evidence. Final render source is `2e83d084b66e`.

| Check | Result |
|---|---|
| before-hydrated / before-empty | 2 passed each, exit 0; actual pre-edit captures |
| after-tablet-empty | **3 passed**, exit 0; geometry, keyboard, list/empty axe, disabled map, complete unchanged API snapshot |
| focused-final | **16 passed**, exit 0; four new Radar cases and existing panel tests |
| full web unit | **69 files / 730 passed**, exit 0 |
| durable Listing Radar E2E | **8 passed**, exit 0; UI/API conversion, evidence merge/archive, reason/risk, denial, conflict, focus, idempotency and live canvas |
| typecheck-final / lint / boundaries / inventory / diff-check | exit 0; inventory unchanged **125 / 18** |

Failures are retained, not counted as successes. Initial `before` exit 1 lacked
explicit local subject/tenant storage and reached fallback rows, not L-2024;
the harness now establishes the actual fixture identity. Initial `typecheck`
exit 2 used Playwright's `exact` option with Testing Library; removed only those
unsupported test options, then typecheck-final and focused-final passed. No
product gate was removed to satisfy verification. Earlier two-width `after`
also passed before final tablet/empty-source/keyboard-switch assertions.

Reproduce from task repository root (do not overlap production build/browser):

```sh
OPSBOARD_PORT=3312 ODP_API_PORT=8312 ODP_API_BASE_URL=http://127.0.0.1:8312 \
NETWORK_PARITY_DESIGN=1 \
NETWORK_PARITY_EVIDENCE_DIR="$PWD/docs/evidence/completion/ODP-UI-NETWORK-TABS-PARITY-001/radar-surface/shots" \
npx playwright test --config tests/visual/operator-network-tabs-parity.config.ts operator-radar-surface-parity.spec.ts
OPSBOARD_PORT=3312 ODP_API_PORT=8312 ODP_API_BASE_URL=http://127.0.0.1:8312 \
ODP_E2E_MODE=true ODP_PRODUCT_MODE=poc ODP_DATA_BINDING_MODE=fixture \
npx playwright test tests/e2e/operator-network-listings.spec.ts --workers=1 --retries=0 --project=chromium
npm run test --workspace=@oday-plus/web
npm run typecheck --workspace=@oday-plus/web
npm run lint --workspace=@oday-plus/web
.venv/bin/python delivery_toolchain/governance/check_code_boundaries.py
npx playwright test --list
```

## Remaining owner boundary

VDC-001 authority/conditional controls remain intact; no fabricated Transfer/Pause
resource IDs. VDC-002 Radar containment and VDC-003 scoped AA/keyboard have this
bounded proof. VDC-004 routing/restoration is unchanged, not newly certified by
local Radar selection tests. VDC-005 still requires independent discipline
outcomes against the final full-task head. Supplemental tests remain outside the
exact business inventory; required-CI integration and final PR explanation are
still due. No production build, remote CI, live product acceptance, independent
approval or full-scope resubmission is claimed here.

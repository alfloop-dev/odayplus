# Spatial / HZ-006 later-spec integration — bounded owner increment

Task `ODP-UI-NETWORK-TABS-PARITY-001` · Pi · reviewer Codex2 · PR #1440.
**Incremental only; not full acceptance, review submission or merge approval.**

## Reference boundary and evidence provenance

Package 10 standalone has **no Spatial / 空間治理 / Merge & Split screen**.
`design-network-no-spatial-{1440,390}.png` is the unchanged Network landing;
`design-absence-*.json` and the browser assertion record absent Spatial controls.
It is not a fabricated Spatial reference or a pixel-parity claim. The integration
uses the existing Network design language (compact header, outlined secondary
controls, 300px rail, 14px gutter, white bordered detail, indigo keyboard focus).
The HZ-006 functions and server-side decider authority remain present.

All empty/proposal captures explicitly use **controlled browser read responses**
for `/api/v1/heatzones/merge-split/proposals`. The proposal shape comes from the
existing unit contract, includes a zero gain and warning, and is not observed
production data. Empty means the controlled response has no items, not a claim
that production has no proposals. These are render/interaction receipts, **not
API-backed generation, approval, rejection, durable reload or live evidence**.
Other Network reads use the local fixture backend. No API grant or write contract
was modified, and no mocked successful write is passed off as durable proof.

## Anchors and actual repairs

- Baseline `cd641ae3be83`: captures old render `6ab83c8f7ba4`, supplemental harness
  and reference absence; baseline mode records diagnostics without asserting
  final geometry or accessibility.
- Integration `10e23c1eace8`: real responsive CSS, keyboard proposal buttons,
  compact single header, source warnings and safe filter/preview behavior.
- Final render `60c8687dc8ac`: fixes measured empty/selected-row contrast.

At 390, old panel scroll width was **729px in a 366px panel**, with detail at
x=348 and width393: mostly clipped even though document width misleadingly stayed
390. Final list and detail are both366px at x=12, vertically stacked with a14px
gap; detail text/actions/metrics and warnings are reachable. Desktop rail is300px,
detail begins x334; tablet rail250px, detail x284. Two-column mobile metric grid
and one-column member/reason facts preserve all values instead of truncating.

Selected detail now comes only from the filtered proposals, not an off-filter
selection. Unavailable preview/decision callbacks disable actions. Null or wrong
identity preview cannot appear as success; obsolete pending results are ignored
when the active proposal/status changes. Selection/filter are frozen while a
preview/decision/modal is pending. Failed rejection preserves input (unit proof),
zero values remain zero, and source warnings are visible. Approval acknowledgement
no longer claims application solely from a resolved callback; latest status must
confirm application. Role-denied controls remain hidden.

Pi inspected desktop/mobile before/after and the desktop reference: mobile detail
is genuinely reachable, identifier/reason text wraps, and warnings are visible.
The Next dev indicator appears in local screenshots; not a product control.

## Verification — original synchronous terminal receipts

Each command has an adjacent `.exit` containing the actual exit code. No count-only
reruns or log-grep waiting loops were used.

| Check | Result | Log |
|---|---|---|
| baseline/reference capture | 3 passed, exit0 (diagnostic mode) | `before.log` |
| focused HeatZone panel units | 12 passed, exit0 | `focused.log` |
| final supplemental geometry/keyboard/filter/scoped AA | 3 passed, exit0 | `after.log` |
| full web Vitest | 747 passed /70 files, exit0 | `unit.log` |
| web lint | exit0 | `lint.log` |
| web typecheck | exit0 | `typecheck-final.log` |
| code boundaries / inventory generation | exit0, tracked inventory unchanged | `boundaries.log` |
| business inventory | 125 tests /18 files, exit0 | `inventory.log` |
| diff whitespace | exit0 | `diff.log` |

Six final scoped axe receipts (empty/proposal ×1440/1024/390) contain zero AA
violations. Assertions cover panel and child containment/scrollWidth, mobile
stacking, keyboard selection, filtered-empty removal and source zero/warning.
This is not full keyboard/modal/whole-page WCAG or independent VDC approval.

Retained failures:
- `after-initial.log/.exit`:3 failed, exit1; empty text #64748b against #f2f4f8
  failed contrast. `diagnostic-empty-*` retains those captures/axe/boxes.
- `after-row-contrast.log/.exit`:3 failed, exit1; selected-row small text against
  #eff6ff failed contrast. `diagnostic-proposal-*` retains these diagnostics.
  Both were fixed to #475569, not suppressed or excluded from axe.
- `boundaries-uv-missing.log/.exit`:exit127 because uv unavailable. Used the
  already-declared `python3 delivery_toolchain/governance/check_code_boundaries.py`
  instead; no host/tool filesystem scans or dependency installs.

Reproduce (from repo root; do not overlap production build and dev-server tests):

```sh
OPSBOARD_PORT=3310 ODP_API_PORT=8310 ODP_API_BASE_URL=http://127.0.0.1:8310 \
NETWORK_PARITY_DESIGN=1 NETWORK_PARITY_CAPTURE_PHASE=after \
NETWORK_PARITY_EVIDENCE_DIR="$PWD/docs/evidence/completion/ODP-UI-NETWORK-TABS-PARITY-001/spatial-surface" \
npx playwright test --config tests/visual/operator-network-tabs-parity.config.ts operator-spatial-surface-parity.spec.ts
npm test --workspace=@oday-plus/web -- features/operator/__tests__/HeatZoneMergeSplitPanel.test.tsx
npm test --workspace=@oday-plus/web
npm run lint --workspace=@oday-plus/web
npm run typecheck --workspace=@oday-plus/web
python3 delivery_toolchain/governance/check_code_boundaries.py --write-inventory
npx playwright test --list
```

## Remaining scope — do not formally resubmit yet

Spatial populated/split/decision/error/permission states still require genuine
backend generation/preview/decision/readback and modal accessibility/geometry
proof. This increment does not certify the inherited decision modals. Proposal
list read failure versus empty and refreshed authority during pending decisions
also need investigation; no claim made from this controlled read harness.
Other task requirements remain: applicable negative/permission/conflict pairs,
real Transfer/Pause IDs/versions, Find Areas reload-before-back stability/full
scope density, final verification/required CI integration, PR explanation of
later-spec features and independent VDC outcomes. Keep task `in_progress`.

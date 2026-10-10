# Rebalance — bounded owner increment, not task completion

Task `ODP-UI-NETWORK-TABS-PARITY-001` · Pi · reviewer Codex2 · PR #1440.
**Not ready for full-scope review/resubmission or merge.** Spatial, remaining
negative/batch states, genuine Transfer/Pause authority and independent VDC
outcomes remain open in the parent checklist.

## Source and implementation

- Resumed clean `27a9bb32051b`; current canonical `origin/dev` `2fe3ef933794`
  re-fetched after verification and confirmed an ancestor of this branch.
- Baseline harness `5b74726103da` captures the genuine pre-repair render.
- Rendering anchor `5683d6a6a2d2`: compact name/status/source-Issue/record identity,
  revenue/utilization/eight-week signals + labeled trend on the left, real-service
  AVM + NetPlan on the right, 330px desktop/280px tablet list and 14px gutters.
  Mobile stacks analysis rather than overflowing or clipping values.
- `d1ef18fb41f3` preserves the complete source summary, adds geometry/keyboard/
  expanded-disclosure checks and source-zero/missing/pending/error/empty units.
- `dac44607e34f` repairs test-only CSS/typed fixture assertions and waits on the
  existing lazy-panel boundary in the business E2E. Final evidence uses this head;
  the render is unchanged from `d1ef18fb41f3`.

Package 10 places valuation/scenarios beside revenue rather than below a full-
width trend, and aligns light history with row metrics. Those structural repairs
are actual JSX/CSS changes, not renamed classes. Pre-repair detail heights at
1440 were 458px initial / 611px AVM / 2014px selected NetPlan. The final geometry
files record the repaired heights. Source trend zeros stay zero; absent trend is
explicitly unavailable, not a synthesized chart. Zero AVM and metrics are pinned
by units. The record ID, source Issue and full summary are retained.

## Later-spec integration and authority

Package 10's demo uses session-only values and allows submission without the
current NetPlan disclosure gate. This implementation must not copy those claims:

- AVM prices/confidence/quality disposition/model/snapshot/evidence are service
  values. Quality notices remain intact, including legacy/unverifiable cases.
- Each scenario's modelled/unmodelled/blocked badges, infeasibility diagnostics,
  recommendation and metadata remain visible. Incomplete eight-class partitions
  still block submission; no constraints are defaulted to successful validation.
- Selected owner/evidence stays visible. Only the **detailed execution chart** is
  placed in a native, keyboard-operable disclosure, absent from Package 10. Its
  complete partition/metadata/equivalent-table component is unchanged. The
  separate blocker, acknowledgement form when applicable, relocation boundary
  and primary action are **outside** the disclosure. Opening/closing it never
  grants approval or alters the selected scenario.
- Service-driven AVM request/complete/solve and scenario selection handlers are
  unchanged. Pending actions remain disabled; no toast-only write is introduced.
  Busy and empty strings use the design's Chinese language.
- This fixture's selected Move has only CAPITAL modelled. The durable receipt
  remains `netplanreview`, `selectedScenarioId=move`, no approval and relocation
  false after reload. Business E2E also proves the direct submission returns 422
  and no Govern approval exists. These are isolated fixture-service writes,
  **not live, remote acceptance or independent governance approval**.

## Captures and assertions

All images use 900px viewport height, full-page capture clipped to viewport width.
Actual app interactions use real clicks/keyboard and real service reads/writes;
no product responses, grants, resource versions or successful outputs are mocked.

- `design-{rebalance,avm,netplan}-{1440,390}.png`: untouched archived Package 10,
  expansion manager; DOM dispatch only for prototype tabs clipped on mobile.
  Reference action toasts are awaited before final captures. The prototype's
  mobile two-column layout itself overflows: its detail is off-canvas in the
  390px reference, deliberately not repaired or misrepresented as reachable.
- `before-{rebalance,avm,netplan}-{1440,390}.png`: actual baseline; 1024 supplemental.
- `after-{rebalance,avm,netplan,netplan-expanded}-{1440,390}.png`: final actual app;
  1024 supplemental. Final geometry is measured after resetting document scroll;
  baseline y-coordinates could precede that reset, so do not use baseline y values
  as a repaired layout comparison. Widths/heights remain directly measured.
- Final assertions cover document/panel/list/detail/stepper/action containment,
  exactly one main landmark, fixed list widths/gutters, half-width desktop/tablet
  analysis and mobile stacking. Keyboard Enter performs an actual AVM request
  and scenario selection; native summary opens/closes the unchanged detailed
  plan while the primary remains disabled. POST-backed selected evidence survives
  reload; `durable-selection-*.json` preserves the authoritative GET payload.
- Baseline scoped axe reported one prohibited aria label and 8/14/18 low-contrast
  nodes in initial/AVM/NetPlan. The trend now has an image role and textual source
  values; scoped muted/step/AVM/metadata/recommendation colors meet AA.
  All **12 final state/width scoped axe receipts have zero violations**, including
  expanded chart. This is not whole-shell accessibility or VDC-005 approval.

Pi inspected desktop, mobile, reference and expanded-chart captures. Identity,
metrics/light history and AVM/scenario hierarchy are restored; longer real
constraint disclosures intentionally make selected NetPlan taller than the
prototype. They must remain visible, not be hidden to manufacture pixel parity.

## Verification — original synchronous terminal exits

| Check | Result | Receipt |
|---|---|---|
| Baseline supplemental browser | 3 passed, exit 0 | `before.log` |
| Final supplemental browser | 3 passed, exit 0, retries 0 | `geometry.log` |
| Focused Rebalance units | 26 passed, exit 0 | `focused.log` |
| Full web units | 742 passed / 70 files, exit 0 | `unit.log` |
| Durable Rebalance business E2E | 1 passed, exit 0, retries 0 | `workflows.log` |
| Lint | exit 0 | `lint.log` |
| Final typecheck | exit 0 | `typecheck-final.log` |
| Boundary checker | exit 0 | `boundaries.log` |
| Root business inventory | unchanged 125 / 18, exit 0 | `inventory.log` |
| Diff whitespace | exit 0 | `verification.json` |

The supplemental config adds only this parity harness, not a fabricated business
inventory count. Required CI/full-task verification are still pending.

Diagnostics retained, not counted as passing:

1. `focused-initial.log`: 25 passed/1 failed, exit 1; happy-dom rewrites
   `import.meta.url` to a non-file URL. Test reads now use the existing web-root
   file convention.
2. `focused-css-reader.log` and `unit-css-reader.log`: 25/741 passed, one failed,
   exit 1; CSS-key assertion omitted comma-separated selectors. Test-only regex
   repaired. `typecheck-fixture.log` exit 2: added required snake-case disclosure
   fields to the test fixture; no product contract loosened.
3. `workflows-cold-load.log` exit 1 and context: the business spec asserted a lazy
   panel within 5s while its declared loading boundary was visible. Cold Next
   logs also showed incomplete generated JSON. Awaited the actual boundary with
   a bounded timeout (all durable/gate assertions preserved); removed only ignored
   `.next` after the original tools exited, then ran serially. Final original
   terminal receipt is exit 0. No grep-wait loops or inferred process receipts.

Reproduce from root, browser runs serially (no concurrent production build):

```sh
npm test --workspace=@oday-plus/web
npm run lint --workspace=@oday-plus/web
npm run typecheck --workspace=@oday-plus/web
OPSBOARD_PORT=3310 ODP_API_PORT=8310 ODP_API_BASE_URL=http://127.0.0.1:8310 \
NETWORK_PARITY_DESIGN=1 \
NETWORK_PARITY_EVIDENCE_DIR="$PWD/docs/evidence/completion/ODP-UI-NETWORK-TABS-PARITY-001/rebalance-surface" \
npx playwright test --config tests/visual/operator-network-tabs-parity.config.ts \
  tests/visual/operator-rebalance-surface-parity.spec.ts
OPSBOARD_PORT=3310 ODP_API_PORT=8310 ODP_API_BASE_URL=http://127.0.0.1:8310 \
ODP_E2E_MODE=true ODP_PRODUCT_MODE=poc ODP_DATA_BINDING_MODE=fixture \
npx playwright test tests/e2e/operator-network-rebalance.spec.ts \
  --workers=1 --retries=0 --project=chromium
python3 delivery_toolchain/governance/check_code_boundaries.py
npx playwright test --list
```

`verification.json` binds source paths, render head and log exit markers;
`artifacts.sha256` binds every other file in this evidence directory.

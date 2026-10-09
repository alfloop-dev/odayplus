# Network tabs — base composition and bounded visual increment

Task `ODP-UI-NETWORK-TABS-PARITY-001` · Pi · reviewer Codex2 · PR #1440

**Incremental evidence only. The task remains `in_progress`, not ready for full
review or merge.** This directory name is the repository evidence convention,
not a completion claim. Intake dialogs and the other Network screens still need
paired visual/geometry acceptance; see the remaining-work checklist below.

## History-preserving base advance

- Resumed clean task branch at `8e5183f47e3a`.
- Merged `origin/dev` `30b95d9e9ecc` with merge `86ebebcd8ddc`; no conflicts.
- Anchors: `547be6866051` (geometry harness), `f125a0337af2` (real detail/action
  CSS and reachable mini-map), `9e1d3a0468a2` (layout regressions).
- During verification dev advanced again: merged `1b064bb00cda` (operator read
  authorization) with `4480acc404b7`. Git composed the shared Network workspace
  and route-gate tests without textual conflicts. Active-persona authorization,
  per-tab read gates and denied aggregate behavior remain intact.
- `633b8f8a0135` repairs composition assertions: Chinese KPI labels no longer
  expose HeatZone counts, so the scoped-read unit test proves the actual visible
  zones and selected detail instead. Withheld zones still fail closed while
  authorized Radar rows remain usable. WAIT E2E waits for the actual reviewer
  snapshot before opening its dialog; no validation/permission assertions removed.
- No reset, stash, history rewrite, force push, or direct dev push.

## Actual visual repairs (not class-name-only changes)

The inherited JSX used **24 undefined CSS module keys** in Candidate detail and
an undefined `secondaryButton` across three panels. Added concrete styles for
key/value rows, photo placeholder, Gate hierarchy, action stack and audit rows;
a unit assertion now checks every CSS key referenced by these three panels.

Desktop Compare rail now matches the archived **300px**, rather than 348px.
SiteScore's mobile flex picker had compressed the map to **2px**, at x=964 in a
390px viewport. The picker choices now scroll locally in their own wrapper; the
210px-high map is a separate full-width row below them. Desktop picker remains
250px. Mobile score identity wraps without splitting `82 / 100`; Risk breakdown
uses two columns and wraps values rather than silently truncating risk semantics.
Secondary actions now have actual design-style borders, padding, radius and focus
outlines. Revenue bars restore the indigo gradient that a more-specific old rule
had overridden with teal.

No API grant, write handler, scoring value, or Intake contract was changed in this
increment. Existing added mock-only/dead actions and fixture facts are **not**
validated as real product behavior by these geometry checks.

## Paired evidence and geometry

[shots/](shots/) contains 18 PNGs: for Candidate, SiteScore and Compare at both
1440×900 and 390×900 viewports:

- `before-increment-<screen>-<width>.png`: actual browser capture at the resumed
  layout (`547be6866051` rendering source, before this wake's CSS repairs).
  These are **not** the original pre-task screenshots. The original audit's
  pre-task 1440 shots remain at the task brief's canonical audit path.
- `design-<screen>-<width>.png`: unchanged Package 10 standalone, expansion
  manager persona. Optional external requests blocked; DOM events only for
  reference tabs that the prototype clips on mobile. The prototype's own
  overflow is not corrected. Capture is explicitly clipped to the requested
  width; it does not falsely imply the reference has no overflow.
- `after-<screen>-<width>.png`: actual application, expansion manager persona,
  composed render head `4480acc404b7`. The next anchor only changes tests, not
  rendering. UI uses real clicks, no force clicks or DOM navigation shortcuts.
- `geometry-<screen>-<width>.json`: actual measured boxes and document width at
  1440, 1024 and 390; design box included at 1440/390. `before-increment-geometry-*`
  retains the diagnostic pre-repair measurements.

The nine supplemental browser checks assert contained document/panel/detail
geometry, desktop columns and 14px gutters, mobile stacking, reachable map width
and height, key/value columns and styled actions, revenue/risk structure, map
below comparison table, locally scrollable wide table, one main landmark, four
candidate KPI and retained blocked scoring controls. Full WCAG/keyboard or
per-pixel parity is **not** claimed. The supplemental config is outside the
exact business inventory; it is not yet added to required CI.

Pi inspected the captured images: desktop detail/actions now have the intended
hierarchy, and mobile map/risk values are reachable. Review still needs to address
remaining density and functional/data discrepancies below. [shots.sha256](shots.sha256)
binds each image and measurement file.

## Verification (original synchronous terminal exit receipts)

Final tests follow the second base merge; no build runs concurrently with a web
dev server. Logs are preserved here, and [verification.json](verification.json)
records source/artifact hashes and each command's observed exit code.

| Check | Result | Log |
|---|---|---|
| web lint | exit 0 | `lint.log` |
| web typecheck | exit 0 | `typecheck.log` |
| full web Vitest at `633b8f8a0135` | 67 files, **701 passed**, exit 0 | `unit.log` |
| composition-focused Vitest | 3 files, **27 passed**, exit 0 | `focused-unit.log` |
| supplemental geometry at render `4480acc404b7` | **9 passed**, exit 0 | `geometry.log` |
| four Network business specs at `633b8f8a0135` | **27 passed**, exit 0, retries 0 | `workflows.log` |
| web production build at `633b8f8a0135` | exit 0 | `build.log` |
| boundary inventory | exit 0 | `boundaries.log` |
| root Playwright inventory | **125 tests / 18 files**, exit 0 (current base inventory) | `inventory.log` |
| `git diff --check` | exit 0 | recorded in `verification.json` |

Reproduction from repository root (do not overlap build and browser runs):

```sh
npm run lint --workspace=@oday-plus/web
npm run typecheck --workspace=@oday-plus/web
npm test --workspace=@oday-plus/web
OPSBOARD_PORT=3310 ODP_API_PORT=8310 ODP_API_BASE_URL=http://127.0.0.1:8310 \
NETWORK_PARITY_DESIGN=1 NETWORK_PARITY_EVIDENCE_DIR="$PWD/docs/evidence/completion/ODP-UI-NETWORK-TABS-PARITY-001/shots" \
npx playwright test --config tests/visual/operator-network-tabs-parity.config.ts
OPSBOARD_PORT=3310 ODP_API_PORT=8310 ODP_API_BASE_URL=http://127.0.0.1:8310 \
ODP_E2E_MODE=true ODP_PRODUCT_MODE=poc ODP_DATA_BINDING_MODE=fixture \
npx playwright test tests/e2e/e2e-network-find-areas-api-binding.spec.ts \
  tests/e2e/operator-network-scoring.spec.ts tests/e2e/operator-network-listings.spec.ts \
  tests/e2e/operator-network-review.spec.ts --workers=1 --retries=0 --project=chromium
npm run build --workspace=@oday-plus/web
python3 delivery_toolchain/governance/check_code_boundaries.py
npx playwright test --list
```

Diagnostic failures are not hidden or counted as successful runs:

- First supplemental config launch exit 1: config-relative Python path; fixed
  server cwd. First reference capture exit 1: default design persona denied
  Network; selected the expansion manager before navigating.
- Pre-repair geometry **7 passed / 2 failed**, exit 1; `geometry-before.log`
  proves 348px Compare rail and the 2px mobile map.
- Initial web test exit 1: old CSS assertion expected 348px; fixed to measured
  Package 10 300px and added missing-key coverage.
- Initial business run **26 passed / 1 failed**, exit 1: lazy map still loading.
  Awaited its declared loading boundary (15s, consistent with snapshot checks),
  preserving the actual canvas visibility assertion.
- One geometry rerun collided with a concurrent Next production build:
  **1 passed / 8 failed**, exit 1, missing `.next` manifests/chunks. See
  `geometry-build-collision.log`. Not accepted as evidence. Removed only ignored
  generated `.next` output after servers exited and reran serially.
- Second-base unit **699 passed / 2 failed**, and business **20 passed / 1 failed /
  6 not run**. `composition-unit-before.log` and `composition-workflows-before.log`
  preserve those results. Chinese KPI and actual snapshot hydration repairs were
  anchored before the successful full runs above.

These are local fixture/durable-test-backend receipts, not remote CI, live writes,
release acceptance or independent reviewer approval.

## Remaining owner work before formal `task_finalize.sh` resubmission

1. Complete paired Intake detail, field correction, receipt decision, separate
   Transfer/Pause, Promotion and Review Decision audit and 1440/390 captures;
   add per-dialog geometry, focus return/trap, contrast and permission assertions.
   Preserve VDC-001 conditional controls, conflict input preservation and receipts.
2. Capture/inspect other Network tabs, batch score view and states. Reconcile the
   English `Blocked until candidate exists` stepper banner with actual selected
   flow and Package 10 density **without deleting its underlying gate**.
3. Fix inherited Candidate facts/audit presented as hardcoded truths (e.g. the
   current gate says 18坪 while the new detail says 28坪). Gate completeness must
   not imply hard-rule success for a missing-data candidate. Connect or explicitly
   disable inherited no-op edit/archive controls and do not report mock notices
   as successful writes. SiteScore risk tones and fallback/empty data semantics
   also need review before full acceptance.
4. Document the supported schematic mini-map versus actual map integration and
   any later-spec features absent from Package 10 in the final PR explanation.
5. Preserve VDC-002 responsive reachability, VDC-003 accessibility and VDC-004
   URL restoration; obtain VDC-005 independent discipline outcomes. This owner
   increment does not impersonate those approvals.
6. Finish acceptance, run final verification and required CI integration, then
   use `AI_NAME=Pi ./delivery_toolchain/git/task_finalize.sh` to atomically
   resubmit PR #1440. Do not move to review/done on this bounded checkpoint.

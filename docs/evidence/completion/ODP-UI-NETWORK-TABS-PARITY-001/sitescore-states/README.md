# SiteScore risk / batch / scoped flow — bounded owner increment

Task `ODP-UI-NETWORK-TABS-PARITY-001` · Pi · reviewer Codex2 · PR #1440

**Not full task acceptance or review resubmission.** Other Network tabs,
Transfer/Pause authority, further dialog permission/conflict states and independent
VDC outcomes remain open. No task_finalize / review / done transition is claimed.

## Durable source and baseline

- Resumed clean at `99b71dd2a663`; `origin/dev` remains `2fe3ef933794`, an ancestor.
- `796a9fc8df1a`: supplemental state capture baseline (no rendering changes).
- `5462d12f5790`: scoped flow hint, explicit risk display metadata and batch layout.
- `df06d4810a21`: measured contrast repairs and persona-scoped capture startup.
- Final rendering is `df06d4810a21`; subsequent harness changes only await the
  actual panel/flow read (30 seconds) and measure at scroll position zero.

Canonical `before-*` captures were regenerated from **git archive
`796a9fc8df1a`** in `$ORCH_SCRATCH_DIR/sitescore-baseline-796a9fc8`, using the final
capture harness in BEFORE mode, dependency symlinks and separate local ports
3311/8311. This restores only historical rendering in a scratch copy: the task
branch was never reset, modified backward, rebased or switched. No status command
was run from the scratch archive. Baseline source hashes are in
`before-source.sha256`; final sources in `source.sha256`.

The first baseline run captured before persona/flow hydration completed; its
images/measurements are retained under `shots/baseline-startup-diagnostics/` and
are **not** the final comparison evidence. Regeneration was to fix measurement
provenance, not to count tests again. All failed runs have original exit receipts.

## Real changes and later-spec integration

### Flow hint, without removing gates

The original full-width warning repeated `Blocked until candidate exists` across
unrelated tabs. The header now matches the reference's current-step hierarchy;
only the active blocked journey exposes a compact Chinese status in the existing
header chip. The duplicate bottom warning row is removed. Next-step hints are
relative to the active step, not a different tab's current step. Blocked buttons
in **both** step grid and flow chain remain disabled, with localized summaries.

The listings journey `L-2024 -> CS-1001` is distinct from the SiteScore service's
four fixture candidates: the listings read still says conversion has not occurred.
We do **not** infer conversion from a global candidate count, invent an entity,
enable those steps or change backend truth. The hint explicitly says **此流程**.
Known missing-candidate/model/read summaries are translated; unknown server
summaries remain available instead of silently substituting a permissive state.

### Risk breakdown

The old CSS rendered every risk green, including high competition, high rent and
missing data. Risks now use semantic `dl/dt/dd`, retain complete textual values,
and wrap rather than silently ellipsize. Optional `subScoreTones` are explicit
source-declared presentation metadata. Package 10 fixture expected values declare
mixed good/watch/risk tones (high demand positive, high competition risky).
`_build_scorecard` copies that metadata; actual model-report projection is
unchanged and does **not** invent tones. Missing values or absent/invalid metadata
remain neutral grey. No recommendation-to-subscore inference, prose parsing,
threshold change, scoring decision or new hard-rule success is introduced.

Scoped axe found real 4.47:1 label and 4.36:1 green-value contrast failures.
Colors were minimally darkened in the same design palette; labels and textual
risk descriptions still carry meaning independent of color.

### Batch view

390px previously inherited the desktop two-column grid **after** the mobile
media rules, compressing candidate names into near single-character vertical
columns. It now stacks selector/results, wraps rent on its own row, and uses
44px minimum selection targets. Desktop two-column density is retained.
The 680px mobile result table scrolls within a named, focusable region with a
visible focus ring; real keyboard ArrowRight proves local horizontal reachability.
Selection controls expose `aria-pressed`; decorative checkbox glyphs are hidden
from assistive technology. Gate-blocked candidates remain disabled; selecting
none disables execution. Existing authorized batch API wiring is unchanged.

## Paired images / measurements inspected by Pi

`shots/` contains design/before/after risk and batch PNGs at **1440 and 390**,
plus after no-selection states and geometry JSONs. Design source is the unchanged
Package 10 archive; expansion manager selected. Only reference mobile navigation
uses DOM events (the prototype clips tabs); implementation uses real clicks.
External reference requests are blocked and full-page captures are clipped to the
requested viewport width. Reference batch fixture is empty while the application
has real local fixture-service candidates/results; this is not equal-data parity.
Both show the same selector/results hierarchy. Implementation mobile stacking
intentionally improves on the reference's compressed columns (VDC-002).

| Measurement | Before | After |
|---|---:|---:|
| 1440 flow height | 182.98px | 140.48px |
| 390 flow height | 356.14px | 313.64px |
| 390 batch table scroll viewport | 158px | **340px** |
| 390 underlying table width | 720px | 680px (locally scrollable) |
| 390 batch panel width | 366px | 366px, no document overflow |
| 1440 risk/report widths | 1098 / 1136px | unchanged |

Pi inspected the before/after desktop risk and desktop/mobile batch images and
mobile risk image: mixed risk tones now match reference semantics, labels are
readable, selection rows no longer collapse, and result columns are keyboard
reachable. Disabled unavailable write actions remain disabled. These are local
fixture evidence, not live-data acceptance, pixel-perfect parity, a global axe
approval or independent VDC-005 sign-off. Next dev indicator remains in captures.

## Verification — original synchronous exit receipts

Every run has `logs/<name>.log` and `.exit`, not a summary-grep completion test.
`verification.json`, `source.sha256` and `artifacts.sha256` bind the receipts.

| Check | Result |
|---|---|
| historical baseline, `before-archive` | 2 passed; exit 0 |
| final measured geometry/keyboard/scoped risk+batch axe, `after-measured` | 2 passed; exit 0; zero scoped violations at both widths |
| full web, `unit` | 68 files / **722 passed**, exit 0 |
| focused Network panel unit, `focused-unit` | **12 passed**, exit 0 |
| durable scoring business E2E, `durable-e2e` | **4 passed**, exit 0; API batch persistence/gate skips and UI selection/compare readback |
| API contract, `api-tests` | **6 passed**, exit 0; tones/conditions/gates/permission/idempotency |
| `typecheck-final`, `lint`, `ruff`, `boundaries`, `inventory` | all exit 0; inventory unchanged **125 / 18** |
| `git diff --check` | exit 0 |

Diagnostics retained, not counted as success:

- `after`: 2 failures on real risk contrast; original axe reports preserved as
  `axe-risk-before-contrast-*`.
- `after-contrast`: cold 1440 panel hydration timeout and real mobile batch
  contrast failures; mobile axe report preserved as `axe-batch-before-contrast-390`.
- `after-final`: 1440 lazy panel exceeded default 5-second assertion while full
  web unit verification ran; 390 passed. Kept the gate and increased only the
  panel readiness wait to the existing snapshot wait's 30-second bound.
- `after-stable`, `after-ready`: both widths passed during intermediate captures.
- `before-stable`: archive web server lacked workspace-local dependency symlink
  (`next: not found`); dependency link added, no host filesystem search/install.

Reproduce final supplemental verification from task repository root:

```sh
OPSBOARD_PORT=3310 ODP_API_PORT=8310 ODP_API_BASE_URL=http://127.0.0.1:8310 \
NETWORK_PARITY_EVIDENCE_DIR="$PWD/docs/evidence/completion/ODP-UI-NETWORK-TABS-PARITY-001/sitescore-states/shots" \
npx playwright test --config tests/visual/operator-network-tabs-parity.config.ts operator-sitescore-states-parity.spec.ts
OPSBOARD_PORT=3310 ODP_API_PORT=8310 ODP_API_BASE_URL=http://127.0.0.1:8310 \
ODP_E2E_MODE=true ODP_PRODUCT_MODE=poc ODP_DATA_BINDING_MODE=fixture \
npx playwright test tests/e2e/operator-network-scoring.spec.ts --workers=1 --retries=0 --project=chromium
.venv/bin/python -m pytest tests/contract/test_operator_network_scoring_api.py -q
```

No production build/remote CI/full final-task run in this bounded increment.
Supplemental tests remain outside the exact business inventory; required CI
integration still belongs to full task acceptance, before formal resubmission.

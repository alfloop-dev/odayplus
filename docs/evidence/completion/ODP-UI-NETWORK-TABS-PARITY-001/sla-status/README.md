# Authoritative SLA summary / terminal and unavailable state increment

Pi · reviewer Codex2 · `ODP-UI-NETWORK-TABS-PARITY-001` · existing PR #1440

**Increment only; not full Package 10 acceptance or formal review submission.**
The state-pair harness uses explicitly mocked reads, not successful SLA creation,
Transfer/Pause writes or durable reload. The separate genuine local API probe
still demonstrates unavailable resource authority. No permission, deadline,
calendar policy, cross-store association or independent VDC approval is invented.

## Scope and source

Resumed clean `63572b369767`; probe anchor `ac8d3bfc3b85`, product/test repair
anchor `b8d6b7d3c0ae`. Canonical `origin/dev` was fetched before implementation and
after verification; remained `10eb6224fa31010fbf3c6f27612ca7d17ba4401e`.
Verified canonical summary, state/service contracts, routes and Web/Playwright
configuration against this ref. No rebase/base merge or production change.
`source.sha256` binds the baseline summary and final tested files;
`shots.sha256` binds all 34 PNGs.

- Summary now presents only the six supported authoritative `slaState` values.
  Browser wall time cannot override a server `DUE_SOON`, `OVERDUE`, `ON_TRACK`
  or terminal state. Legacy `isSlaPaused`/`isBreached` flags cannot override it.
- Preserve `COMPLETED` as **已完成 (Completed)** with a text/icon pattern, not
  an overdue timer or unavailable state. Unknown/missing state is UNAVAILABLE,
  even if a deadline or legacy flag exists. This is honest presentation of a
  potentially stale read model, not proof that server-side derivation is fresh.
- Invalid/missing due time displays UNAVAILABLE rather than `Invalid Date`.
  A deadline is displayed as data, never promoted to authority for state/actions.
- Pause is displayed only for ON_TRACK/DUE_SOON/OVERDUE when a caller supplies
  its authorized callback; paused records use Resume, completed/breached/unknown
  records offer neither. Existing container permission/resource-version guards
  remain the independent action authority. No successful operation is mocked.
- Retain the existing Package 10-derived continuous detail spacing, contained
  cards, compact authority notes and bilingual label/pattern treatment. This
  increment repairs state contradictions, **not a new layout-completion claim**.
  The design's compact ON_TRACK block is a reference; later-spec resource IDs,
  versions and unavailable notes remain integrated rather than removed.

Canonical `modules/listing/domain/intake_states.py` defines COMPLETED and makes
it terminal; `modules/listing/application/assignment_sla.py` derives DUE_SOON
within **two hours**, not the removed browser's one-hour guess. We deliberately
do not copy those thresholds into the client or certify calendar/paused-clock
policy by doing so. Existing backend semantics remain unchanged.

## Paired screen and geometry evidence

`shots/` contains **14 before + 14 after + 2 Package 10 design PNGs**, all at
1440×900 or 390×900. Each state has same-width pairs with its own fixture/geometry
JSON. The fixtures are named `IN-SLA-UI-FIXTURE`, contain no Assignment/SLA IDs or
versions, and are intercepted GET projections only. Resource writes are aborted
and both completed runs assert zero attempted resource calls. Full navigations
exercise the actual container/detail rendering but do **not** prove persistence.

| Pair suffix (both widths) | Explicit read fixture | Baseline → final |
| --- | --- | --- |
| `on_track` | ON_TRACK, historical deadline | OVERDUE → ON_TRACK |
| `due_soon` | DUE_SOON, deadline +90 minutes | ON_TRACK → DUE_SOON |
| `overdue` | OVERDUE, future deadline | ON_TRACK → OVERDUE |
| `breached` | BREACHED, future deadline | BREACHED retained |
| `paused` | PAUSED, historical deadline | PAUSED retained |
| `completed` | COMPLETED, historical deadline | OVERDUE → COMPLETED |
| `unavailable` | UNKNOWN, invalid deadline | ON_TRACK / Invalid Date → UNAVAILABLE |

Example: [before completed mobile](shots/before-completed-390.png) /
[after completed mobile](shots/after-completed-390.png),
[before unknown desktop](shots/before-unavailable-1440.png) /
[after unknown desktop](shots/after-unavailable-1440.png).
[Design desktop](shots/design-detail-1440.png) /
[design mobile](shots/design-detail-390.png) preserve the original reference:
these are not fabricated design-state variants for new terminal/error cases.

Both phases assert document/summary horizontal containment and no summary
horizontal overflow; JSON records summary dimensions and visible status text.
All **14 final summary-scoped Axe scans have zero violations**. Pi inspected the
mobile completed before/after and desktop unavailable against the mobile design:
labels/patterns remain readable and resource notes remain reachable by scrolling.
Long detail is a scrolling page, not a viewport-height modal. The local Next
indicator is not a product control. This is owner inspection, not independent
VDC-005 or whole-console AA approval.

## Genuine unavailable-resource regression

The unchanged `operator-assignment-authority-parity.spec.ts` was also executed
against the actual local API at both widths. `actual-unavailable/` retains two
real GET records, four Transfer/Pause deep-link screenshots, four geometry JSONs
and two zero-write receipts. Actual URL submission creates `IN-3001` from the
explicit synthetic source through the actual Operator API. No child resource IDs
or versions are provided. Deep links continue to render unavailable summary
rather than opening actionable Transfer/Pause dialogs. This is local header-stub /
synthetic provider evidence, not real IdP/cloud/production acceptance, nor genuine
SLA provisioning or mutation success. Earlier success/fixture evidence is not
upgraded by this regression.

## Original synchronous execution receipts

All runs completed with original terminal exits saved immediately in the same
shell (`logs/*.exit`); no output-summary waits, process-name polling or count-only
reruns. Statistics below come from completed logs/JUnit. No test result is unknown.

| Check | Original result |
| --- | --- |
| Baseline new summary unit regressions | exit 1; 20 failed / 14 passed, 34 tests |
| Baseline browser state pairs/design | exit 0; 2 cases (7 states each) |
| First repaired focused units | exit 1; 3 failed / 70 passed, 73 tests |
| Final focused units | exit 0; 73 tests / 3 files |
| Final browser state pairs / geometry / scoped AA | exit 0; 2 cases (7 states each) |
| Genuine local unavailable deep-link regression | exit 0; 2 cases |
| Full Web Vitest | exit 0; 833 tests / 73 files |
| Web typecheck / lint | exit 0 each |
| Boundary check | exit 0; unchanged 1213-file inventory |
| Business E2E inventory (list only) | exit 0; unchanged 125 tests / 18 files |
| Diff check | exit 0 |

The first repaired-unit failures were an SSR assertion that omitted React's
`<!-- -->` separator between static and dynamic text. The assertion now accepts
that separator while still requiring the exact unavailable due-time text;
original failure log/XML are retained. Full/focused Web stderr contains expected
happy-dom API connection and SSL warnings despite successful exits. Browser logs
contain existing dependency/API warnings. No full API suite, production build,
cloud check or final-scope remote CI acceptance was run/claimed here.

Reproduction (root; use fresh output directories):

```sh
OPSBOARD_PORT=3310 ODP_API_PORT=8310 ODP_API_BASE_URL=http://127.0.0.1:8310 \
ODP_PLAYWRIGHT_REUSE_EXISTING=0 NETWORK_PARITY_CAPTURE_PHASE=after \
NETWORK_PARITY_DESIGN=1 NETWORK_PARITY_EVIDENCE_DIR=/tmp/new-sla-status \
npx playwright test --config tests/visual/operator-network-tabs-parity.config.ts \
  operator-sla-status-parity.spec.ts
# Same server environment, new evidence directory:
npx playwright test --config tests/visual/operator-network-tabs-parity.config.ts \
  operator-assignment-authority-parity.spec.ts
npm test --workspace=@oday-plus/web -- --run \
  features/operator/network/intake/__tests__/AssignmentSlaSummary.test.tsx \
  features/operator/network/intake/__tests__/IntakeProcessingDetail.test.tsx \
  features/operator/network/intake/__tests__/Package10VisualP1.test.tsx
npm test --workspace=@oday-plus/web
npm run typecheck --workspace=@oday-plus/web
npm run lint --workspace=@oday-plus/web
.venv/bin/python delivery_toolchain/governance/check_code_boundaries.py
npx playwright test --list
git diff --check
```

Baseline runs used anchored original product at `ac8d3bfc3b85`, not a reconstructed
or weakened status expectation. Existing final product is `b8d6b7d3c0ae`.

## Remaining owner work

The real Operator `IN-*` repository is still not the v1 UUID Intake store. The
v1 Assignment path exists, but the v1 routes expose pause/resume without an SLA
creation entry; domain initialization is reserved to SVC_SLA/emergency authority.
This increment neither invents a cross-store mapping nor chooses a deadline.
Resolve the canonical resource association/provisioning policy and shared reads
before claiming actual Transfer/Pause UI writes/reload with paired geometry.
Do not seed child IDs or deadlines and label those screenshots genuine success.

Continue remaining negative/all-tab states/density, final-scope verification,
PR later-spec explanation and independent VDC outcomes. Prior Spatial split,
directory and action-authority receipts retain their own limits. **Task remains
in_progress; not ready for task_finalize/handoff/re_review/done.**

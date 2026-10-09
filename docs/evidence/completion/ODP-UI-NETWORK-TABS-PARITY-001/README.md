# Network tabs — base composition and bounded visual increment

Task `ODP-UI-NETWORK-TABS-PARITY-001` · Pi · reviewer Codex2 · PR #1440

**Incremental evidence only. The task remains `in_progress`, not ready for full
review or merge.** This directory name is the repository evidence convention,
not a completion claim. Intake dialogs and the other Network screens still need
paired visual/geometry acceptance; see the remaining-work checklist below.

## Owner repair after Codex2 changes requested

[review-repair/README.md](review-repair/README.md) records the bounded repair at
`cd951649f2b3`: authoritative Candidate Gate/KV values, unavailable missing facts,
disabled unsupported actions, active-persona Compare/selected batch API wiring,
zero-preserving revenues, 713 passing web tests, durable UI action proof, and new
three-panel design/before/after images plus geometry. Earlier screenshots and
receipts below are retained history, not the repaired render's final evidence.
Full Intake/all-tab/VDC acceptance remains open; **do not resubmit or merge yet**.

## Intake modal increment

[intake-dialogs/README.md](intake-dialogs/README.md) adds field-fix/receipt-decision
1440/390 design-before-after pairs, four geometry/focus/reason-gate/axe checks,
113 Intake unit tests and three durable correction/decision/reload E2E checks.
Required later-spec risk acknowledgement remains visible, with compact design
spacing. Continuous detail, Transfer/Pause, Promotion, Review Decision and
permission/conflict visual states remain pending; this is not full acceptance.

## Continuous detail / current base increment

[intake-detail/README.md](intake-detail/README.md) records merge `f3006f748`
composing dev `7869551a58e8`, continuous-detail 1440/390 design-before-after
geometry, unclipped summary values, compact explicit assignment/SLA unavailable
states, single main landmark, keyboard-scrollable stages and reload/Radar-return
repair. Full web 713, supplemental detail 2, durable Intake E2E 3, lint/typecheck/
boundaries/inventory passed. Actual read receipts lack assignment/SLA resource
IDs/versions: Transfer/Pause product modal/state pairs remain unavailable, not
mocked or falsely approved. Promotion/Review Decision/all-tab/VDC scope remains
open; **not ready for review resubmission**.

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

## Review Decision / required base advance increment

[review-decision/README.md](review-decision/README.md) records dev `2fe3ef933794`
composition through `4168e4f46b3f`, baseline `94eb6f55af81`, repair `b009ca5db2a6`,
and GO/WAIT/Return/Reject 1440/390 design-before-after geometry, keyboard, scoped
axe, actual POST200/durable GET200 and reload state pairs. Mobile is now centered
350px/14px rather than bottom-aligned 370px/12px; Reject CTA is reference red.
Required fields, pending-write guards and acknowledgement semantics are repaired
without relaxing canonical override rules. Full web 718, supplemental 8, business
Review 8, typecheck/lint/boundaries and unchanged 125/18 inventory passed. Failed
cold BFF/read and response-body diagnostics remain recorded. This is another
bounded owner checkpoint, not full acceptance or resubmission.

## Promotion confirmation / durable reviewer increment

[promotion/README.md](promotion/README.md) records baseline `670c780e9018`,
modal repair `ee101d6051b5`, and reviewer readback repair `58ad432bb249`.
1440/390 design-before-after pairs reduce initial heights from 565.5/713.4px
to 412.6/480.7px with reference hierarchy, direct reason/risk fields and a
collapsed later-spec audit/control disclosure. Both widths pass geometry,
scoped axe, keyboard and actual write-controls/POST200/GET200/reload checks.
The genuine POST-only reviewer projection is repaired and contract-tested;
no authority or commit gates were weakened. Full web 720, focused UI 37,
API promotion tests, typecheck/lint/boundaries and unchanged 125/18 inventory
passed. This is a bounded checkpoint, not full acceptance or resubmission.

## SiteScore risk / batch / scoped flow increment

[sitescore-states/README.md](sitescore-states/README.md) records baseline
`796a9fc8df1a`, repair `5462d12f5790` and access repair `df06d4810a21`.
Stable 1440/390 design-before-after pairs show the duplicate global warning row
removed without enabling the blocked listings journey, explicit fixture risk tones
with neutral missing-metadata fallback, and mobile batch stacking with a 340px
keyboard-scrollable result viewport instead of 158px. Risk values are semantic
key/value pairs, preserve full text and pass scoped contrast/axe checks. Full web
722, supplemental 2, durable scoring E2E 4, API contract 6, lint/typecheck/ruff/
boundaries and unchanged 125/18 inventory passed; failed diagnostics are retained.
Other tabs/dialog state pairs, independent VDC outcomes and full verification/CI
integration remain open. **Not ready for formal review resubmission.**

## Review full-tab increment

[review-surface/README.md](review-surface/README.md) records baseline
`62005332c0ba` and rendering `2e252b634588`: 1440/390 design-before-after Review
queue/detail pairs, 1024 supplemental geometry, semantic source-backed facts,
outlined secondary decisions, recommendation warning, explicitly disabled
unavailable field-visit action, 33 baseline low-contrast nodes repaired and zero
scoped AA axe violations. Three geometry/keyboard/focus/unchanged-state checks,
19 focused units, full web 726, durable Review E2E 8, lint/typecheck/boundaries and
unchanged 125/18 inventory passed. Other tabs/state pairs/authority/VDC outcomes
remain open; **this is not a full-scope review resubmission**.

## Radar full-tab / empty-filter increment

[radar-surface/README.md](radar-surface/README.md) records baseline `54466d1ecdb5`
and render `2e83d084b66e`: actual 1440/390 design-before-after list/detail and
empty-filter pairs, supplemental 1024 geometry, preserved six-track source cards
and compact Intake search, keyboard row selection, wrapped URLs, reference-style
explicitly unavailable secondary actions, and source-backed missing/zero values.
Off-filter stale detail is retired; row/detail share hard-rule/pending/terminal
write gates. Scoped baseline 24 contrast nodes repaired; list and empty states
have zero AA axe violations at all three widths. Full web 730, focused 16,
durable Listing Radar E2E 8, typecheck/lint/boundaries and unchanged 125/18
inventory passed. Original missing-identity/typecheck diagnostics are retained.
Other tab/negative-state/authority/VDC scope remains open; **not ready to resubmit**.

## Find Areas hierarchy / URL increment

[`findareas-surface/README.md`](findareas-surface/README.md) records actual
1440/390 design-before-after and supplemental 1024 captures, single-line lenses,
a bounded map, source-backed key/value detail and explicit primary/secondary
behavior. Unavailable writes are disabled; local tracking is labeled non-durable;
geocoder remains accessible through native disclosure; SiteScore navigation is
real and the toast-only Find Areas review CTA is retired. Validated zone/lens URL
hints restore back/forward/reload without changing scoped API authority. Final
collapsed/expanded-search scoped AA is zero at all widths. Full web 735, focused
16 and Find Areas/Radar business E2E 15 passed; diagnostics are retained, including
an uncertified reload-before-back ordering timeout requiring follow-up. This is
still a bounded checkpoint, **not ready for full-scope resubmission**.

## Rebalance hierarchy / AVM / blocked NetPlan increment

[`rebalance-surface/README.md`](rebalance-surface/README.md) records baseline
`5b74726103da`, layout `5683d6a6a2d2`, summary/assertions `d1ef18fb41f3` and
cold-load proof `dac44607e34f`. Initial/AVM/selected-NetPlan 1440/390
reference-before-after pairs, supplemental 1024 and expanded-chart captures restore
left signals/right valuation hierarchy and compact row light history. Authoritative
zeros/missing data are preserved. Only the detailed execution chart is collapsed;
constraint badges, blocking alerts, applicable acknowledgements and relocation
boundary remain visible. Twelve final scoped AA receipts are clear; keyboard real
AVM request/selection, durable GET/reload and direct-submit422/no-approval proof pass.
Full web742/focused26/visual3/business1/typecheck/lint/boundaries and unchanged125/18
inventory pass with original exits; failed test-reader/typed-fixture/cold-load
receipts retained. This is a bounded checkpoint, **not full-scope resubmission**.

## Spatial later-spec integration increment

[`spatial-surface/README.md`](spatial-surface/README.md) records baseline
`cd641ae3be83`, integration `10e23c1eace8` and contrast `60c8687dc8ac`.
Package 10 has no Spatial reference: unchanged Network landing captures and
absence assertions explicitly document this boundary, not invented parity.
Controlled read-model empty/proposal 1440/390 before-after pairs and tablet
geometry restore reachable mobile detail (729px clipped content →366px stacked
panels), keyboard rows, visible source warnings, zero preservation and safe
filter/preview identity. Six scoped AA receipts clear; full web747/focused12/
visual3/lint/typecheck/boundaries/inventory125-18 original exits0. Failed contrast
and missing-uv diagnostics remain. These are UI-only controlled-read receipts,
not durable decision proof; inherited modals/real proposal generation/permission/
error states remain open. **Not full acceptance or review resubmission.**

## Spatial decision authority / acknowledgement increment

[`spatial-decisions/README.md`](spatial-decisions/README.md) records baseline
`ed196bd00ff1`, implementation `e0b8a5dd89d1` and harness `1974b6aeaaac`:
1440/390 approve/reject modal, pending, controlled-conflict, POST-ack/GET-failure
and terminal-retry before-after pairs. Confirmation pins proposal/persona;
revoked/unavailable/replaced authority cannot submit; pending input/cancel/Escape
freeze; failed requests preserve input. Acknowledged POST and unconfirmed GET are
explicitly separate, and locally acknowledged IDs cannot be re-decided. Malformed
items/duplicate IDs/incomplete split topology fail closed. Twenty after scoped AA
scans clear; full web785/focused50/modal4/render-regression5/lint/typecheck/boundaries/
inventory125-18 original exits0. Controlled HTTP outcomes **are not backend durable
proof**. Genuine generation/preview/decision/reload, other negative pairs, Intake
resource authority and independent VDC scope remain open. **Not ready to resubmit.**

## Spatial genuine API / SQLite increment

[`spatial-durable/README.md`](spatial-durable/README.md) records before `b0b88e97d900`,
contrast repair `cfe2df48f43d` and final verified source `153d3702e528`.
Actual mounted generation/preview/approve/reject and browser reload now have
1440/390 before-after captures, independent SQLite process readbacks, exact
operator/reason/model/policy records, repeated-decision422 and audit-chain proof.
Input history and matured inventory are explicitly test-generated, not live data
readiness. Genuine rejection text exposed a missed AA contrast defect; both
baseline violations are retained and all12 final scoped scans clear. Final isolated
SQL browser4, controlled regression9, focused50/fullweb785, lint/typecheck/ruff/
boundaries/inventory125-18 pass with original exit receipts; setup failures and
missing-uv API-suite receipt remain. This does not prove PostgreSQL/live deployment,
production WORM/auth, full server restart, split readiness or independent VDC.
**Still not ready for full-scope resubmission.**

## Intake resource-token / conflict-refresh increment

[`resource-authority/README.md`](resource-authority/README.md) records anchors
`356abaeab220` and `7f16fca8c36c`: Transfer/Pause now display resource-specific
versions, reject mismatched receipts/unsafe tokens and disable unresolved-conflict
writes. Successful rereads supersede cached claim/resume receipts; failed rereads
retain conflict/refresh and drafts. Controlled mounted regressions prove v8→409→
refresh→v9 requests, not backend durability. Real local API 1440/390 deep-link
probes expose fresh Operator Intake's absent Assignment/SLA IDs/versions, with
four unavailable-state screenshots and no resource POST. They are **not** modal
repair/design pairs or successful Transfer/Pause acceptance. Full web790,
browser2, lint/typecheck/boundaries/inventory125-18 have original exit0 receipts;
early focused failures are retained. Genuine provisioning/read-model/target-scope
integration must precede modal acceptance; no ID/version/production evidence was
invented. **Not ready for full-scope resubmission.**

## Assignment / SLA contract read-token increment

[`resource-projection/README.md`](resource-projection/README.md) records anchor
`02a44df9c0fc`: UUID Intake detail now exposes tenant-bound, resource-specific
Assignment/SLA versions; normative/generated schema and explicit Operator DTO
fields agree. Actual HTTP Assignment creation/claim/409/transfer/reread tests,
fixture-resource SLA pause/resume/read tests, unavailable-token cases, full API
operations suite, runtime schema drift, focused web34, typecheck/lint/ruff and
boundaries have original exit0 receipts. The initial API timeout lacks a child
exit receipt and is preserved as UNKNOWN, not success. **This does not connect
the separate Operator Intake read model or provision its resources/targets.**
No browser/durability/visual acceptance is inferred; those remain next work.
Still not ready for full-scope review resubmission.

## Transfer target fail-closed increment

[`transfer-targets/README.md`](transfer-targets/README.md) records anchors
`945db9c95d9f` and `7a8dc49d5128`: static people/queue targets and silent target
substitution are removed; missing targets disable submission and target changes
invalidate consent while preserving handoff drafts. Explicit fixture targets
replace runtime defaults in mounted tests. 1440/390 design-before-after modal
pairs, geometry, scoped axe and keyboard use **mocked resource GETs only**, not
real provisioning/directory/write proof. Separate genuine local API probes still
show absent Operator resource IDs/versions and no resource POST. Full web797,
focused56, paired browser2+2, real-negative browser2, typecheck/lint/boundaries and
inventory125-18 have original exit0 receipts. UUID shape is not identity/scope
authorization; genuine provisioning, directory and server validation remain next.
**Still not ready for full-scope resubmission.**

## Remaining owner work before formal `task_finalize.sh` resubmission

1. Field correction/receipt decision baseline pairs, geometry/focus and scoped
   axe are recorded in `intake-dialogs/`; continuous-detail baseline pairs and
   unavailable-authority/keyboard/reload checks are now in `intake-detail/`.
   Complete separate Transfer/Pause (requires actual resource read-model authority).
   Promotion confirmation/reason/risk/self-review/committed-reload pairs are now
   in `promotion/`; further applicable saga/permission/conflict visual states remain.
   Review Decision baseline/validation/committed-reload pairs
   are in `review-decision/`; complete further applicable permission/conflict visual
   states and full-scope detail density review.
   Preserve VDC-001 conditional controls, conflict input preservation and receipts.
2. Spatial baseline/later-spec integration is now recorded in `spatial-surface/`;
   local generated-history mounted generation/preview/decision/readback now has
   SQLite persistence/audit proof in `spatial-durable/`. Complete applicable split
   and modal/permission/error state evidence; never substitute the test maturity
   seam for release-bound live readiness or PostgreSQL acceptance. Also complete
   remaining batch write/error/empty states. Rebalance initial/AVM/selected-blocked baseline,
   expanded-plan geometry/scoped AA and durable/gate/reload proof are now in
   `rebalance-surface/`; further applicable negative/permission/conflict pairs remain. Find Areas baseline hierarchy, actual scoped
   data, keyboard and back/forward-before-reload proof are in `findareas-surface/`;
   follow up reload-before-back stability and full-scope detail density. Radar full-tab/source-empty pairs, tablet/mobile
   geometry, keyboard/scoped AA and real source/write gates are in `radar-surface/`;
   remaining applicable error/permission/conflict visual states still need evidence.
   Review full-tab pairs, tablet/mobile geometry,
   scoped AA/keyboard and unavailable-action integration are in `review-surface/`.
   Batch baseline/no-selection geometry, keyboard and scoped axe plus compact
   flow hint are recorded in `sitescore-states/`. Backend flow gates are preserved;
   do not infer listings conversion from the separate scoring candidate count.
3. Candidate hardcoded facts/audit, no-op success notices, missing Compare wiring,
   unreachable batch scoring and zero-revenue defaults are repaired in the linked
   owner increment. Source-declared fixture risk tones and scoped accessibility
   are now recorded in `sitescore-states/`; inspect fallback/empty-data visual
   states and explain disabled unsupported actions before
   full acceptance. Gate completeness must never imply invented hard-rule success.
4. Document the supported schematic mini-map versus actual map integration and
   any later-spec features absent from Package 10 in the final PR explanation.
5. Preserve VDC-002 responsive reachability, VDC-003 accessibility and VDC-004
   URL restoration; obtain VDC-005 independent discipline outcomes. This owner
   increment does not impersonate those approvals.
6. Finish acceptance, run final verification and required CI integration, then
   use `AI_NAME=Pi ./delivery_toolchain/git/task_finalize.sh` to atomically
   resubmit PR #1440. Do not move to review/done on this bounded checkpoint.

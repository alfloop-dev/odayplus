# Spatial split topology / genuine permission increment

Task `ODP-UI-NETWORK-TABS-PARITY-001` · Pi · reviewer Codex2 · existing PR #1440.
**Increment only; not full Package 10 acceptance or formal review submission.**

## Sources and design boundary

Resumed clean `f6b17b27d5c2`; harness anchors `99974fd64d61` and
`2c67d4340d04`; final UI/harness source `8d475fc964da`. Fresh canonical
`origin/dev` is `10eb6224fa31010fbf3c6f27612ca7d17ba4401e`. Checked canonical
Operator routes/services, v1 Intake router and Spatial contracts before changing
this lane. No base merge/rebase, identity provisioning, product backend,
permission, policy threshold, maturity receipt or generated status edits.

Package 10 has **no Spatial screen or split modal**. Same-width unchanged Network
reference captures and absence assertions remain in
[`../spatial-availability/`](../spatial-availability/README.md). These paired
split captures are a later-spec integration with the existing compact Network
rail/detail/dialog language, not an invented prototype match. This increment adds
one compact source fact using a wrapping definition list, not a new section bar.

## Repairs and proof boundary

- Display the proposal's measured absorption-density ratio explicitly beside its
  child partitions. No computed client-side ratio, positive fallback or new
  policy threshold; zero remains zero, absent/nonfinite values say unavailable.
- A rejected split no longer says that approving it will create children. Rejected
  wording describes this decision's non-creation; approved/applied wording refers
  to authoritative topology readback rather than declaring present activation
  from proposal status alone. PROPOSED retains the all-children/parent-retirement
  consequence and existing authorized decision controls.
- Extend the existing isolated test factory with explicit synthetic parent
  membership and side-labelled outcome history from the established integration
  helpers. Genuine production evidence assembly/engine generates the split; its
  child IDs come from an independent persisted proposal GET, **not** a response
  fabricated by the browser harness. The measured fixture ratio is 4.27, not the
  helper's input multiplier 3.2. The generation can also yield a merge proposal;
  the harness selects by actual composition kind without concealing that result.
- Four separate fresh SQLite servers exercise approve/reject at 1440/390.
  Actual browser preview, modal POST, GET and full reload retain terminal status.
  A new Python process opens a new database connection, confirms the same proposal,
  exactly one decision audit event, actor/reason and intact audit chain. Approval
  creates both exact child partitions, retires both parent memberships and reads
  each active child lineage independently. Rejection creates no children and
  retains both active parent memberships. Repeat decisions return 422. No split
  cleanup or parent restoration is performed after these receipts.
- The actual PM/auditor persona has **neither heatzone VIEW nor OVERRIDE**. Its
  browser list GET403 renders unavailable, not authoritative empty or stale
  proposal detail. Independent list GET and approve/reject POST return403. Fresh
  process snapshots before/after denied writes match exactly. Returning to the
  real expansion-manager persona reloads the genuine proposal and enables the
  normal flow. There is no invented expansion-staff Operator persona, no new
  read-only grant, and no browser-mocked successful or denied response.

This uses **local header-stub authentication**, generated 224-day history,
explicit fixture parent topology and the existing test-only matured-inventory
loader seam. It is not deployed IdP, real production barrier evidence, PostgreSQL,
release-bound model maturity, a complete restart test or cloud WORM acceptance.
The audit sink is scratch-local exclusive-create storage; source receipt paths
and fixture versions remain visible. Other Network reads remain explicit local
fixtures. No cloud/IAM/source/model activation is implied.

## Paired screenshots and geometry

`before/` and `after/` each contain 16 full-page PNGs: preview, modal, terminal
reload and PM/auditor denial for both actions and both widths. Baseline product
UI is `2c67d4340d04` (unchanged inherited UI); final product is `8d475fc964da`.
Baseline AA findings were recorded, final AA absence enforced. Different proposal
UUIDs/timestamps are expected from the independent fresh databases.

Final assertions cover document/panel/control containment, dialog height and
split child/fact horizontal containment, actual source density and terminal copy.
All **16 final scoped AA scans have zero violations**. Baseline fact geometry
was not yet explicitly enumerated; final child/fact boxes extend the inherited
panel/control geometry assertions. Scans are panel/modal-scoped, not whole-console
or independent VDC-005 approval. Existing Network reference absence evidence is
not upgraded by these tests.

Pi inspected final desktop preview and mobile rejected-terminal screenshots
against the mobile baseline: density is readable without replacing raw reasons,
all child IDs/cells remain wrapped/reachable, and the rejected caption no longer
suggests a new decision. The local Next development indicator is not a product
control. This is owner inspection, not independent visual approval.

## Original synchronous verification receipts

All terminal exits were saved immediately in the same shell. No summary-grep
waiting loops or count-only reruns. Counts come from original completed logs.

| Check | Result | Receipt |
| --- | --- | --- |
| Baseline split API/SQLite browser | 4 separate cases, each exit0 | `logs/before-*.log/.exit` |
| Final split API/SQLite/browser geometry/permission | 4 separate cases, each exit0 | `logs/after-*.log/.exit` |
| Existing genuine merge regression | 4 passed, exit0 | `logs/merge-regression.*` |
| Focused panel/client/workspace Vitest | 57 passed /3 files, exit0 | `logs/focused.*` |
| Full Web Vitest | 814 passed /73 files, exit0 | `logs/unit.*` |
| Web typecheck / lint / Python harness Ruff | exit0 each | named logs/exits |
| Boundaries | exit0; unchanged 1213-file inventory | `logs/boundaries.*` |
| Business inventory | exit0; unchanged 125 tests /18 files | `logs/inventory.*` |
| Diff whitespace | exit0 | `logs/diff.*` |

Retained failed baseline attempts (all original terminal exit1, not unknown jobs):
missing required override reason for explicit synthetic parent; generation DTO
lacks derived child IDs (fixed by authoritative proposal GET); unsupported staff
persona fell back to a non-heatzone role and timed out (fixed by actual PM/auditor
persona and real GET403 assertions); browser's discarded GET403 response body
could not be consumed (fixed by independent API GET/body, retaining browser status).
No permission or assertion was weakened to turn these failures into passes.
Full Web stderr includes expected happy-dom connection refusals and dependency SSL
warnings despite completed exit0. Browser/API logs include deprecation warnings.
No production build/full API suite/final-scope CI verification was run here.

Reproduction, from root, with **one new scratch DB per split case**:

```sh
mkdir -p /tmp/new-spatial-case
OPSBOARD_PORT=3310 ODP_API_PORT=8310 ODP_API_BASE_URL=http://127.0.0.1:8310 \
NETWORK_SPATIAL_DURABLE_DIR=/tmp/new-spatial-case \
NETWORK_SPATIAL_COMPOSITION=split NETWORK_SPATIAL_CASE=approve-390 \
NETWORK_PARITY_CAPTURE_PHASE=after NETWORK_PARITY_EVIDENCE_DIR=/tmp/new-evidence \
npx playwright test --config tests/visual/operator-spatial-durable-parity.config.ts
# Repeat with fresh directories for approve-1440 / reject-1440 / reject-390.
# Merge regression omits NETWORK_SPATIAL_COMPOSITION and NETWORK_SPATIAL_CASE.
npm test --workspace=@oday-plus/web -- --run \
  features/operator/__tests__/HeatZoneMergeSplitPanel.test.tsx \
  features/operator/network/__tests__/heatZoneCompositionClient.test.ts \
  features/operator/network/__tests__/SpatialProposalAvailability.test.tsx
npm test --workspace=@oday-plus/web
npm run typecheck --workspace=@oday-plus/web
npm run lint --workspace=@oday-plus/web
.venv/bin/python -m ruff check tests/visual/spatial_durable_backend.py
python3 delivery_toolchain/governance/check_code_boundaries.py
npx playwright test --list
git diff --check
```

## Remaining task work

Genuine Operator Assignment/SLA provisioning/shared reads and actual Transfer/Pause
UI writes/reload with same-width evidence remain unresolved: canonical Operator
uses `IN-*` tenant document records, while v1 uses separate UUID Intake storage;
v1 offers SLA pause/resume but no creation entry. Do not manufacture resource IDs,
SLA deadlines/policy or success screenshots. Follow up the explicit persistence
and policy integration boundary before wiring a successful lifecycle.

Remaining negative/all-tab pairs and density, final full-scope verification/CI,
PR later-spec explanation and independent VDC outcomes are also open. The bounded
Spatial split and permission proof does not close them. **Keep in_progress; do
not task_finalize, handoff, re_review or done until the actual full scope is ready.**

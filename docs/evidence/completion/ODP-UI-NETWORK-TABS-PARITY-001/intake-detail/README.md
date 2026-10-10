# Continuous Intake detail — bounded owner increment

Pi · reviewer Codex2 · task ODP-UI-NETWORK-TABS-PARITY-001 · PR #1440

**Not full acceptance or review resubmission.** Promotion/Review Decision,
remaining Network/batch states and independent VDC outcomes remain due.
Transfer/Pause implementation modal/state capture is blocked by missing resource
read-model authority; the reference-only images here do not close that scope.

## History-preserving base composition

Resumed clean at `48703d8268f9` on the assigned branch. Canonical live status
confirmed `in_progress`, with the previous submission rejected by Codex2.
Merged `origin/dev` `7869551a58e8` with `f3006f748` (no conflicts), then pushed
normally. This composes the release-signing retry changes and updated boundary
inventory without resetting, rebasing, discarding or rewriting task history.
Re-fetch after verification confirmed the same base; `git merge-base
--is-ancestor 7869551a58e8 HEAD` exited 0.

Anchors:
- `9bb69c89aa94`: attempted assignment-modal capture, preserved history.
- `f35e0a7486dc`: replaced that unreachable capture with actual continuous detail
  and unavailable-authority coverage; original field-fix/decision spec restored.
- `01aab56e426d`: summary/assignment density, wrapping and nested-main repair.
- `0d617aa150f0`: explicit Radar return for reloaded workspace detail.
- `51d10dcaf0eb`: keyboard-reachable horizontal stages and unique live summaries.

Before rendering is the composed product source at `f35e0a7486dc` (only capture
harness readiness changed before its successful run). After rendering/verification
uses `51d10dcaf0eb`. `source.sha256` binds renderer, harness and untouched archived
design; `artifacts.sha256` binds this increment's receipts and screenshots.

## Actual UI repairs and intentional integration

- Desktop detail gaps 16→12px; outer padding returns to 14px/20px/46px.
  Header uses 14px radius, 10px gaps and 12px/18px padding.
- Submission facts have design-style 7px/10px bordered cards. Long actual URL,
  timestamp and ID values wrap within `minmax(0, 1fr)` columns rather than being
  silently clipped by the summary container. At 390 the previous summary had
  scrollWidth **418px** in a **346px** box; after it is **344px** in that same box.
  Missing facts remain explicitly unavailable, not invented reference values.
- Assignment/SLA cards and action spacing are compact; four existing explicit
  missing-authority notices remain, but use left-aligned 4px/12px rows instead
  of four centered 22px/14px empty-state panels. Assignment block height falls
  **417.5→249.4px desktop**, **595.1→417px mobile**.
- Summary height increases **120→165.7px desktop**, **239.3→381.8px mobile**:
  borders and naturally wrapped authoritative values cost height, deliberately
  preferable to clipped URLs. This is not a pixel-identical reference claim.
- Detail is a labelled section inside the shell's main, eliminating nested main
  landmarks. No section, field, stage, audit, permission or backend gate removed.
- Workspace detail return now explicitly routes to `ws=network&tab=radar`, like
  `/intake/:id`. Reload previously lost the local Radar override and returned to
  Find Areas; filters and selected record remain intact.
- Horizontal processing stages have a named focusable region and visible focus
  ring. At 390, real keyboard ArrowRight scrolls the stage strip. Live result and
  field-change summaries retain their announcements with distinct region names.

The prototype has five short summary facts, positive assignment/SLA state and
short evidence tokens. Product retains nine authoritative summary facts,
unavailable-authority explanations, masking controls, receipts and real audit
metadata boundaries. They are integrated using its card/row hierarchy rather
than hidden to imitate the prototype. Intake's source/WORM, lineage, comparison,
promotion and audit density still merit further full-scope visual review. Include
these later-spec integration boundaries in the final PR explanation.

## Paired evidence and authority gap

`shots/` contains design/before/after continuous detail at **1440×900 / 390×900**,
before/after audit-scroll viewports, measured geometry, scoped axe and raw local
synthetic API read receipts. Implementation is actual clicks through the mounted
console and isolated FastAPI/BFF: reset, URL submit, real detail load, reload,
return to the real inbox. No request interception, fabricated fields or DOM
navigation for implementation. Broad runner security headers are cleared; the
fixture session specifies manager subject/role and **tenant-a** explicitly.

The untouched archived Package 10 reference is driven with reference-only DOM
clicks because its mobile navigation is clipped. Its existing overflow stays;
viewport screenshots do not claim to fix the reference. Pi inspected design,
before/after desktop/mobile and mobile audit views. This is owner inspection,
not independent VDC-005 approval or full screen/state acceptance.

**Transfer/Pause gap:** `authority-1440.json` / `authority-390.json` contain actual
200 network Intake responses without assignment ID/status/version or SLA
instance/state/version. The canonical dev backend read model likewise has no
`assignmentId` / `slaInstanceId` / resource-version projection. Product gates
correctly hide Claim/Transfer/Pause/Resume and explain unavailable authority.
The supplemental test asserts this behavior and never adds fake IDs, relaxes
version checks, mocks responses or reuses intake version as resource version.
Reference Transfer/Pause images at both widths are explicitly **reference only**;
there are no successful product modal pairs or browser 409 receipts here.
A canonical API/read-model binding or approved durable fixture provision is
needed before those product modal/state captures can be completed. The existing
unit suites cover valid resource-version actions and conflict-preserved drafts;
they are not substitutes for mounted browser proof.

## Original synchronous completion receipts

Every run completed through its original terminal tool and saved the shell exit
code immediately; no polling for summaries or liveness inference. Final results:

| Command/check | Exit/result | `logs/` |
| --- | --- | --- |
| stabilized before detail captures | 0; 2 passed | detail-before-stable.log / .exit |
| after geometry, unavailable gates, keyboard, scoped axe, reload/inbox return | 0; 2 passed | detail-after-keyboard.log / .exit |
| full web Vitest | 0; 67 files / 713 passed | unit.log / .exit |
| typecheck | 0 | typecheck.log / .exit |
| lint | 0 | lint.log / .exit |
| durable Intake correction/reason/fresh-context E2E | 0; 3 passed | durable-e2e.log / .exit |
| boundaries | 0; 1210 files | boundaries.log / .exit |
| root business inventory | 0; 125 tests / 18 files unchanged | inventory.log / .exit |
| git diff --check / composed base ancestry | 0 | original terminal |

Final scoped axe reports have **zero violations** at both widths; this is not a
full-page/WCAG certification. Unit stderr retains existing ECONNREFUSED navigation
probes and PostgreSQL TLS warnings; the original invocation exited 0.
No production build/full business E2E was run for this bounded increment; those
remain part of final full-scope verification before review submission.

Failed diagnostics retained, not counted as passes:
- `before.log`: Transfer trigger absent because resource authority unavailable.
- `detail-before.log`, `detail-before-ready.log`: clearing broad runner headers
  exposed missing fixture session tenant. Timeout widening alone did not fix it;
  explicit tenant session restored actual permission-bound reads.
- `detail-before-tenant.log`: exposed two main landmarks.
- `detail-before-landmarks.log`: captured mobile 418px summary scrollWidth versus
  346px box. Before harness records defects; after asserts their correction.
- `detail-after.log`: geometry/axe completed, but reload return opened Find Areas.
- `detail-after-return.log`: desktop passed; mobile scoped axe caught keyboard-
  inaccessible horizontal stages. Fixed semantics/focus, no axe rule exclusion.

Reproduce serially from repo root:

```sh
OPSBOARD_PORT=3310 ODP_API_PORT=8310 ODP_API_BASE_URL=http://127.0.0.1:8310 \
NETWORK_PARITY_DESIGN=1 NETWORK_PARITY_CAPTURE_PHASE=after \
NETWORK_PARITY_EVIDENCE_DIR="$PWD/docs/evidence/completion/ODP-UI-NETWORK-TABS-PARITY-001/intake-detail/shots" \
npx playwright test --config tests/visual/operator-network-tabs-parity.config.ts \
  operator-intake-detail --workers=1 --retries=0 --project=chromium
npm test --workspace=@oday-plus/web
npm run typecheck --workspace=@oday-plus/web
npm run lint --workspace=@oday-plus/web
OPSBOARD_PORT=3310 ODP_API_PORT=8310 ODP_API_BASE_URL=http://127.0.0.1:8310 \
ODP_E2E_MODE=true ODP_PRODUCT_MODE=poc ODP_DATA_BINDING_MODE=fixture \
npx playwright test tests/e2e/operator-network-assisted-intake.spec.ts \
  --grep 'identity-field correction demands|possible match requires|decisions and corrections survive' \
  --workers=1 --retries=0 --project=chromium
python3 delivery_toolchain/governance/check_code_boundaries.py
npx playwright test --list
```

## Next, not review-ready

Continue Promotion and Review Decision baseline/state pairs, all other Network
and batch states, selected-flow banner/risk-tone reconciliation and independent
VDC outcomes. Resolve/provision actual assignment/SLA authority before claiming
Transfer/Pause modal/conflict pairs. Preserve previous three-panel and Intake
modal repairs. Only after complete acceptance/final verification, explain the
integrations in PR #1440 and atomically resubmit with `task_finalize.sh`.

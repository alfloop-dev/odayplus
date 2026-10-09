# Review queue / detail — bounded owner increment

Task `ODP-UI-NETWORK-TABS-PARITY-001` · Pi · reviewer Codex2 · PR #1440

**Not full task acceptance or review resubmission.** Find Areas, Radar, Rebalance,
Spatial, remaining batch/error/empty states, applicable dialog permission/conflict
pairs, Transfer/Pause authority and independent VDC outcomes remain open. No
`task_finalize`, review or done transition is claimed by this increment.

## Source and provenance

- Resumed clean at `de52e40656c1`; fetched `origin/dev` remains
  `2fe3ef933794d0bb293449b74dffe9602d53f767`, an ancestor (rechecked after tests).
- `62005332c0ba`: supplemental full Review tab baseline, no rendering changes.
- `2e252b634588`: Review hierarchy/contrast/semantic facts, unavailable field-visit
  action, gate copy and four new unit checks. Rendering is unchanged thereafter;
  only the supplemental harness adds 1024px/AA/landmark assertions and evidence.
- `before-*` is the actual pre-edit browser capture from this clean task branch,
  not a reconstructed image. `before-source.sha256` binds that rendering to
  `62005332c0ba`; `source.sha256` binds final code/harness/reference.
- Reference is the unchanged Package 10 standalone archive (hash
  `1aefb8068faa39666599ceeafe74ba24f1ddc8abd57ba9a6513a724abaee7d0f`).
  Reference PM/稽核 persona selected; implementation uses the authorized local
  expansion-manager fixture session. No implementation requests are intercepted,
  no review data is invented, and no production service is reset or written.
- Original reference images in the baseline commit contained a transient
  persona toast. Current `design-*` images were recaptured after it disappeared
  naturally. This changes neither prototype state nor product content. External
  reference network requests are blocked; only its clipped mobile tab navigation
  uses DOM events. Application navigation uses real clicks.

## Real repairs and later-spec integration

The existing **390 / 996px** desktop queue/detail split already matched the design;
we preserve it rather than redesigning a correct grid. The full tab lacked the
reference's recommendation warning and had filled orange Return / filled red
Reject buttons instead of outlined secondary decisions. The warning now names
actual `review.recommendation` and explains existing reason/risk confirmation and
RETURN exception. It changes no decision mapping, authorization or validation.
Return/Reject have design-style outline hierarchy, scoped to Review so shared
approval-center buttons do not change. GO remains the primary green action.

The reference's secondary **要求現勘（審核前補件）** entry is represented by a
**disabled** outlined button and visible unavailable-service explanation. No
field-visit write handler exists here. Clicking it cannot produce a fake receipt
or success toast; it must remain disabled until a real authorized service is
available. The existing confirmed RETURN flow remains available to authorized
reviewers, and unauthorized roles still see their read-only role note.

Candidate facts use `dl/dt/dd` instead of unassociated spans; missing values
remain `—`. Dates, source listing, applicant, model/snapshot, compare text, risk
summary and history are unchanged authoritative projection values. Queue buttons
expose selected state with `aria-pressed` and a visible keyboard focus outline.
Facts, chips and metadata wrap inside constrained columns rather than clipping
long values; mobile metric grids remain two columns and queue/detail stack.

Scoped axe exposed **33 low-contrast nodes at each baseline width** (one
`color-contrast` rule): IDs, applicant/time, metadata/fact/history labels, evidence
chips, and filled Return/Reject. Muted labels are minimally darkened within the
reference palette; teal chips are darkened; outlined decisions use readable text.
No shared global color or fixture values are changed. Final scoped axe has zero
violations at 1440, 1024 and 390 with WCAG 2 A/AA, 2.1 AA and 2.2 AA tags.

The real scoped flow summary `No candidate review packet yet.` now displays in
Chinese (including its tooltip). Separate listings journey `L-2024` remains
blocked despite unrelated reviews/scoring candidates. Both flow-step and chain
buttons remain disabled; no conversion or candidate/review authority is inferred.

## Paired images and geometry inspected by Pi

`shots/` contains design/before/after **1440 and 390** PNGs and geometry, plus
supplemental after **1024** PNG/geometry, before/final axe reports and unchanged
backend read receipts. Full-page screenshots are clipped to requested viewport
width, not silently widened to accommodate reference overflow.

| Measurement | Final | Meaning |
|---|---:|---|
| 1440 panel / queue / detail | 1400 / 390 / 996px | exact reference desktop column widths |
| 1440 queue → detail gap | 14px | preserved |
| 1024 panel / queue / detail | 984 / 300 / 670px | tablet two-column hierarchy |
| 390 panel / queue / detail | 366 / 366 / 366px | stacked, locally contained |
| 390 metric columns | 150 / 150px | readable two-column metrics |
| document scrollWidth | 1440 / 1024 / 390px | no page-level horizontal overflow |
| 1440 detail height before → after | 575.05 → 663.09px | adds warning + unavailable secondary entry |
| 390 detail height before → after | 832.92 → 963.06px | wrapped new content, not hidden/clipped |

Pi inspected desktop before/design/after, mobile before/design/after and final
1024 images. Desktop warning/action hierarchy now matches Package 10. The
prototype's 390px grid leaves the detail offscreen (350px panel, 606px scrollWidth,
390px + 0px columns); reproducing that defect would violate VDC-002. The
implementation stacks and exposes the whole detail instead. More queue items,
full risk strings and shorter actual history are **data differences**, not a reason
to synthesize mock detail. Next dev indicator is retained in implementation PNGs.
This is visual owner inspection, not pixel-identical parity or independent approval.

## Verification — original synchronous terminal exits

Every run has `logs/<name>.log` and `.exit`, saved immediately from the original
shell command. No grep-based waiting, inferred process completion or rerun solely
for test counting. `verification.json` records accepted and diagnostic runs;
`artifacts.sha256` binds all evidence files.

| Run | Result |
|---|---|
| `before` | 2 passed, exit 0; actual baseline images at 1440/390 |
| `after-tablet-aa` | **3 passed**, exit 0; geometry, scoped AA axe, one main, keyboard selection, Escape focus return, disabled unsupported action, unchanged decisions/audit |
| `focused-unit` | **19 passed**, exit 0; four Review surface checks plus existing panel/dialog gates |
| `unit` | **69 files / 726 passed**, exit 0 |
| `durable-e2e` | **8 passed**, exit 0; GO/WAIT/RETURN mapping, atomic five-record sync, rejection rollback, idempotency, read-only ops-lead and server 403 expansion denial |
| `typecheck`, `lint`, `boundaries`, `inventory`, `diff-check` | exit 0; root business inventory unchanged **125 / 18** |

Earlier `after` and `after-final` two-width runs passed before tablet/2.2 AA
assertions were added; final canonical images/axe are from `after-tablet-aa`.
`diagnostic` failed at both widths on actual baseline contrast; original axe
reports remain `axe-before-review-*`. An attempted nonexistent Python inventory
helper returned exit 2 (`inventory-missing-tool`); it is **not** a passed check.
The actual existing root Playwright `--list` command passed; no host search/install
or invented verification tool was used. No Python/product API behavior changed.
No production build, live-data acceptance, remote CI or final full-task check is
claimed by this bounded increment.

Reproduce from task repository root:

```sh
OPSBOARD_PORT=3312 ODP_API_PORT=8312 ODP_API_BASE_URL=http://127.0.0.1:8312 \
NETWORK_PARITY_DESIGN=1 \
NETWORK_PARITY_EVIDENCE_DIR="$PWD/docs/evidence/completion/ODP-UI-NETWORK-TABS-PARITY-001/review-surface/shots" \
npx playwright test --config tests/visual/operator-network-tabs-parity.config.ts operator-review-surface-parity.spec.ts
OPSBOARD_PORT=3312 ODP_API_PORT=8312 ODP_API_BASE_URL=http://127.0.0.1:8312 \
ODP_E2E_MODE=true ODP_PRODUCT_MODE=poc ODP_DATA_BINDING_MODE=fixture \
npx playwright test tests/e2e/operator-network-review.spec.ts --workers=1 --retries=0 --project=chromium
npm run test --workspace=@oday-plus/web
npm run typecheck --workspace=@oday-plus/web
npm run lint --workspace=@oday-plus/web
.venv/bin/python delivery_toolchain/governance/check_code_boundaries.py
npx playwright test --list
```

## VDC boundary / next owner work

- VDC-001: Transfer/Pause remains gated on actual assignment/SLA IDs+versions;
  not represented by this Review secondary action and not claimed complete.
- VDC-002: this Review tab is geometrically contained at all three widths;
  other pending tabs/states still require their own evidence.
- VDC-003: scoped AA contrast, one main and stable dialog return tested. This is
  not a whole-application accessibility certificate or manual screen-reader audit.
- VDC-004: existing Network URL routing and Intake restorable inbox remain
  unchanged. Review queue selection is not newly encoded here; do not claim
  new URL restoration acceptance from a keyboard-selection test.
- VDC-005: Product/System Design/Frontend/Accessibility/QA independent outcomes
  against exact final implemented head remain required; owner does not self-sign.

Supplemental evidence remains outside the exact business test inventory. Final
required-CI integration, full task verification and PR explanation must precede
atomic `task_finalize.sh` resubmission. **Do not resubmit this bounded increment.**

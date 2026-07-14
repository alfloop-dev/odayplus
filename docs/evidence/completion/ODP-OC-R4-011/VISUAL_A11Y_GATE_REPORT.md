# ODP-OC-R4-011 — R4 Operator Console Visual & Accessibility Gate

**Task:** Make full R4 product E2E visual and accessibility gates mandatory
**Owner:** Claude · **Reviewer:** Antigravity4 · **Phase:** Operator Console R4 Productization

This report is the final visual + accessibility receipt for the Operator
Console R4 productization wave. It pins the canonical design source, records the
mandatory-gate wiring, and links each of the **32 archived screen labels** to a
current runtime screenshot or an explicit non-runtime/dialog coverage
assertion.

## 1. Canonical Design Source (package 6)

| Field | Value |
|---|---|
| Package | `r4-20260707-package-6` |
| Interactive HTML | `docs_archive/00_source_zips/operator_console/r4-20260707-package-6/extracted/Oday Plus Operator Console.dc.html` |
| Canonical ZIP | `docs_archive/00_source_zips/operator_console/r4-20260707-package-6/Oday Plus 營運管理後台 (6).zip` |
| ZIP SHA-256 | `db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76` |
| Interactive HTML SHA-256 | `65d359f4abaf82b39eb16f67da8e91e7ad1b030628bc15f8f45ce7c18c0e2f48` |
| Demo state | `oday-plus-r4-20260707` |
| Screen labels | 32 (extracted live from the interactive HTML) |

Preflight (per `docs_archive/00_source_zips/operator_console/README.md`) was
completed in this worktree: `origin/dev` synced at/after `7eba8098`,
`LATEST.json` resolves to package 6, `unzip -t` passes, and the ZIP SHA-256
matches `LATEST.json`. Package 6 is byte-identical to package 5 in its extracted
payload (`design_delta_count: 0`); it changes only delivery provenance.

## 2. What Became Mandatory

Before this task the CI `product-e2e-gate` job ran `make product-release-gate`,
which only exercised the PV expansion / ops / AVM product specs and never armed
the Operator Console gate. This task makes the full R4 Operator Console product
E2E — visual, accessibility, six-role allow/deny, map-pixel and reload — a
required part of that job.

- **`scripts/e2e/run_product_e2e.sh`** now runs the Playwright suite with
  `ODP_OPERATOR_PRODUCT_GATE=1` (arming the **ODP-OC-PROD-014** go/no-go gate)
  and adds the operator suite, the new visual/a11y spec
  (`tests/e2e/operator-visual-a11y.spec.ts`), the operator console spec, and
  the map accessibility spec.
- **`scripts/e2e/check_operator_visual_a11y_gate.py`** is a new static checker
  (run from `scripts/e2e/check_product_release_gate.py`, i.e. the CI
  `product-e2e-gate` job) that fails release when the coverage manifest drifts
  from the 32 canonical labels, the package-6 ZIP SHA-256 changes, the runner
  drops the mandatory gate flag/specs, or this report loses its provenance.
- **ODP-OC-PROD-014** (`tests/e2e/e2e-operator-console.spec.ts`) fails when
  `/operator` still renders the design iframe, when no Operator Console read API
  proof (`GET /api/v1/operator/bootstrap|today|issues|approvals`) is observed,
  or when a workflow write lacks `Idempotency-Key` / `X-Correlation-Id`.

## 3. Viewports and Accessibility Method

Every runtime surface is asserted at two widths and screenshotted for
comparison against the archived interactive HTML:

- **Desktop** — 1440 × 900
- **Constrained** — 1024 × 768

Accessibility is scanned with `@axe-core/playwright` scoped to
`[data-testid="operator-console"]`. The gate fails on any **serious** or
**critical** (major) violation at either viewport; screenshots are attached to
the Playwright report as the desktop/constrained comparison evidence.

## 4. Six-Role Allow / Deny Matrix

`operator-visual-a11y.spec.ts` asserts the server-side bootstrap envelope
exposes exactly the permitted workspaces per role and withholds all others
(deny path). This composes with the API-layer 403 fail-closed proofs delivered
by ODP-OC-R4-010.

| Role | Allowed workspaces |
|---|---|
| `cs-lead` | today, store, govern |
| `expansion-manager` | today, network, govern |
| `field-lead` | today, store |
| `marketing-manager` | today, growth, govern |
| `ops-lead` | today, store, growth, network, govern |
| `pm-audit` | today, store, govern |

Reason gates (Govern return/reject ≥10 chars, Network review decision reason,
Store Ops camera purpose) remain covered by the merged operator suite and the
console spec.

## 5. Map & Reload

- **Map canvas** — `operator-visual-a11y.spec.ts` asserts the Network
  find-areas HeatZone canvas and the expansion flow stepper render; deep map
  keyboard/pixel/axe behaviour is covered by `e2e-map-a11y.spec.ts` and
  `e2e-map.spec.ts`, both now in the mandatory runner.
- **Reload survival** — the `ws=store&entity=ISS-1024&tab=triage` deep link is
  re-resolved from the URL after `page.reload()` (workspace, selected entity,
  and tab persist). Store / Growth / Network Review / Rebalance / Govern write
  durability is proven by the merged operator suite and the contract tests.

## 6. Screen-Label Coverage (32 / 32)

Machine-checkable source of truth:
`docs/evidence/completion/ODP-OC-R4-011/screen_label_coverage.json`
(validated by `scripts/e2e/check_operator_visual_a11y_gate.py`).

Coverage kinds: **runtime_workspace** (20) — desktop + constrained screenshot +
axe; **runtime_dialog** (9) — opened live by a named workflow spec;
**non_runtime_assertion** (3) — emitted structurally by
`StoreOpsWorkflowDialogs.tsx` but not opened by the deterministic suite, audited
by source-of-truth assertion.

| Screen label | Coverage | Proof |
|---|---|---|
| `Today 今日工作` | runtime_workspace | desktop+constrained screenshot + axe · `tests/e2e/operator-visual-a11y.spec.ts` |
| `Top Navigation` | runtime_workspace | desktop+constrained screenshot + axe · `tests/e2e/operator-visual-a11y.spec.ts` |
| `Notifications` | runtime_workspace | header panel screenshot + axe · `tests/e2e/operator-visual-a11y.spec.ts` |
| `Role Switch Menu` | runtime_workspace | header menu screenshot + axe · `tests/e2e/operator-visual-a11y.spec.ts` |
| `Store Ops 門市營運` | runtime_workspace | desktop+constrained screenshot + axe · `tests/e2e/operator-visual-a11y.spec.ts` |
| `Store Ops 全店四燈摘要` | runtime_workspace | desktop+constrained screenshot + axe · `tests/e2e/operator-visual-a11y.spec.ts` |
| `Govern 治理稽核` | runtime_workspace | desktop+constrained screenshot + axe · `tests/e2e/operator-visual-a11y.spec.ts` |
| `Growth 營收成長` | runtime_workspace | desktop+constrained screenshot + axe · `tests/e2e/operator-visual-a11y.spec.ts` |
| `Growth 建立入口` | runtime_workspace | desktop+constrained screenshot + axe · `tests/e2e/operator-visual-a11y.spec.ts` |
| `Growth 會員分群` | runtime_workspace | desktop+constrained screenshot + axe · `tests/e2e/operator-visual-a11y.spec.ts` |
| `Growth PriceOps` | runtime_workspace | desktop+constrained screenshot + axe · `tests/e2e/operator-visual-a11y.spec.ts` |
| `Network 展店與店網` | runtime_workspace | desktop+constrained screenshot + axe · `tests/e2e/operator-visual-a11y.spec.ts` |
| `Network Expansion Flow Stepper` | runtime_workspace | desktop+constrained screenshot + axe · `tests/e2e/operator-visual-a11y.spec.ts` |
| `Network 找區域` | runtime_workspace | desktop+constrained screenshot + axe · `tests/e2e/operator-visual-a11y.spec.ts` |
| `Network 物件雷達` | runtime_workspace | desktop+constrained screenshot + axe · `tests/e2e/operator-visual-a11y.spec.ts` |
| `Network 候選點工作台` | runtime_workspace | desktop+constrained screenshot + axe · `tests/e2e/operator-visual-a11y.spec.ts` |
| `Network SiteScore Lab` | runtime_workspace | desktop+constrained screenshot + axe · `tests/e2e/operator-visual-a11y.spec.ts` |
| `Network 候選點比較` | runtime_workspace | desktop+constrained screenshot + axe · `tests/e2e/operator-visual-a11y.spec.ts` |
| `Network 選址審核` | runtime_workspace | desktop+constrained screenshot + axe · `tests/e2e/operator-visual-a11y.spec.ts` |
| `Network 低效重配` | runtime_workspace | desktop+constrained screenshot + axe · `tests/e2e/operator-visual-a11y.spec.ts` |
| `Dialog Triage` | runtime_dialog | opened via 完成 Triage · `tests/e2e/operator-store-ops.spec.ts` |
| `Dialog Assign` | runtime_dialog | opened via 指派 Owner · `tests/e2e/operator-store-ops.spec.ts` |
| `Dialog Create Action` | runtime_dialog | opened via 建立 Field Action · `tests/e2e/operator-store-ops.spec.ts` |
| `Dialog Camera Purpose` | runtime_dialog | opened via 點擊填寫調閱目的 · `tests/e2e/operator-store-ops.spec.ts` |
| `Dialog Outcome Review` | runtime_dialog | opened via 檢視 Outcome · `tests/e2e/operator-store-ops.spec.ts` |
| `Drawer Field Report` | runtime_dialog | opened via 提交 Field Report · `tests/e2e/operator-store-ops.spec.ts` |
| `Dialog Review Decision` | runtime_dialog | opened via review go/return/reject · `tests/e2e/operator-network-review.spec.ts` |
| `Dialog Growth Draft Builder` | runtime_dialog | opened via growth entry builder · `tests/e2e/operator-growth.spec.ts` |
| `Dialog Growth Outcome` | runtime_dialog | opened via growth closeout outcome · `tests/e2e/operator-growth.spec.ts` |
| `Dialog Escalate` | non_runtime_assertion | `dialogScreenLabels.escalate` · `apps/web/features/operator/StoreOpsWorkflowDialogs.tsx` |
| `Dialog Reply Review` | non_runtime_assertion | `dialogScreenLabels.replyReview` · `apps/web/features/operator/StoreOpsWorkflowDialogs.tsx` |
| `Dialog Transfer` | non_runtime_assertion | `dialogScreenLabels.transfer` · `apps/web/features/operator/StoreOpsWorkflowDialogs.tsx` |

### Parity note

`Notifications` and `Role Switch Menu` did not previously emit their archived
`data-screen-label` at runtime; this task adds those two attributes (plus test
ids) to `apps/web/features/operator/OperatorConsole.tsx` so all shell labels are
both runtime-visible and gate-asserted. No other design-screen delta was found
against package 6 for the productized surfaces.

## 7. Verification Commands

```bash
# Static mandatory-gate checks (CI product-e2e-gate job):
python3 scripts/e2e/check_operator_visual_a11y_gate.py
python3 scripts/e2e/check_product_release_gate.py

# Canonical package-6 integrity:
test "$(sha256sum 'docs_archive/00_source_zips/operator_console/r4-20260707-package-6/Oday Plus 營運管理後台 (6).zip' | cut -d' ' -f1)" \
  = db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76

# Runtime gate (Docker-backed runner, boots api+web):
make product-release-gate

# Focused runtime proofs:
ODP_OPERATOR_PRODUCT_GATE=1 npx playwright test tests/e2e/e2e-operator-console.spec.ts -g ODP-OC-PROD-014
npx playwright test tests/e2e/operator-visual-a11y.spec.ts tests/e2e/operator-*.spec.ts tests/e2e/e2e-map-a11y.spec.ts
```

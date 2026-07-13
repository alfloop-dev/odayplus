# Operator Console R4 — Implementation & Page Acceptance Matrix

**Task:** ODP-OC-R4-000 · Lock R4 design baseline and page acceptance matrix
**Owner:** Claude2 · **Reviewer:** Codex2 · **Baseline captured:** 2026-07-13

This matrix freezes the *obtainable* Operator Console R4 design truth so any
reviewer can bind implementation work to the exact design snapshot, identify the
branch source, and see the release blockers **without reading chat history**.

The machine-readable companion is
[`docs/evidence/OPERATOR_CONSOLE_R4_BASELINE_RECEIPT.json`](../evidence/OPERATOR_CONSOLE_R4_BASELINE_RECEIPT.json)
(validated with `jq empty`).

---

## 1. Design source baseline

| Field | Value |
|---|---|
| Design bundle label | `oday-plus-r4-20260707` |
| Design bundle md5 | `78b65c33fac19dc33ba241e640df5cd1` |
| Snapshot date | 2026-07-07 (frozen) |
| Provenance | Off-repo design bundle used to seed the ODP-OC-R4-001…012 fleet. The bundle is **not** tracked in this git repository; this document plus the receipt record its identity. |

### Referenced source docs — presence in baseline tree

The R4 task briefs cite three source docs. As of the baseline commit they are
**not tracked** in `origin/dev`; their content is reproduced here so nothing
depends on the missing files:

| Referenced path | In baseline tree? |
|---|---|
| `docs/design/OPERATOR_CONSOLE_R4_FLEET_EXECUTION_TASKS_2026-07-13.md` | ❌ absent |
| `docs/design/OPERATOR_CONSOLE_R4_FLEET_EXECUTION_TASKS_2026-07-13.json` | ❌ absent |
| `docs/evidence/OPERATOR_CONSOLE_DESIGN_PARITY_AUDIT_2026-07-13.md` | ❌ absent |

### Unavailable artifact — AI Trading Desk zip

The **AI Trading Desk** reference design zip is **unavailable** in this
repository and in the design bundle. Per ODP-OC-R4-000 scope, its absence is
**non-blocking** to R4 implementation. **No parity to the AI Trading Desk design
is claimed anywhere in this baseline.** Any parity statement must wait until the
zip is obtained and independently verified (see release blocker RB-1).

---

## 2. Branch truth

| Field | Value |
|---|---|
| Target branch | `dev` |
| Promotion branch | `main` |
| Baseline ref | `origin/dev` |
| Baseline commit | `5fefe2b11184a14169d52c00634dbf2552e5dc5c` |
| Baseline subject | Merge pull request #267 from alfloop-dev/task/ODP-FIN-AUTH-001 |
| Baseline captured at | 2026-07-13T18:52:13+08:00 |
| `main` tip | `52fc9cd3270d8cf591ecca2ebc767cf6dc289d11` |

All `ODP-OC-R4-001…012` task branches are cut from `origin/dev` at or after the
baseline commit. Cutover of `dev → main` is gated by **ODP-OC-R4-011**
(product E2E gate) and **ODP-OC-R4-012** (staging verify + promote).

---

## 3. Page acceptance matrix (screen → one task → one E2E assertion)

Every R4 screen maps to **exactly one** implementation task and **one** E2E
assertion. The `operator-*.spec.ts` suites are **planned** deliverables of their
owning task (not yet present in `origin/dev` at baseline) and are made mandatory
in CI by ODP-OC-R4-011.

| # | R4 screen | Impl task | E2E spec | Primary E2E assertion |
|---|---|---|---|---|
| 1 | **Shell** — header, nav, search, notifications, approvals, Task Center, Ctrl/Cmd+K | ODP-OC-R4-002 | `tests/e2e/operator-shell-today.spec.ts` | Header counts match API after writes without full session reset; Ctrl/Cmd+K and keyboard nav pass accessibility tests. |
| 2 | **Today** — role-aware home (six roles) | ODP-OC-R4-002 | `tests/e2e/operator-shell-today.spec.ts` | All six roles get distinct allowed workspaces + Today content from bootstrap/today APIs; search/queue selection opens the exact entity and tab. |
| 3 | **Store Ops** — four-light summary + durable issue lifecycle | ODP-OC-R4-003 | `tests/e2e/operator-store-ops.spec.ts` | ISS-1024 completes a valid lifecycle and reload shows persisted state; invalid transitions → 409; duplicate idempotency keys don't duplicate audit rows. |
| 4 | **Growth** — three create entries + five-step Draft Builder + conflict gate | ODP-OC-R4-004 | `tests/e2e/operator-growth.spec.ts` | All three entry cards prefill/persist the correct draft type; blocked conflicts cannot submit and return actionable server reasons. |
| 5 | **Network · Find Areas / Expansion Stepper / Listing Radar** | ODP-OC-R4-005 | `tests/e2e/operator-network-listings.spec.ts` | HZ-01 → L-2024 → CS-1001 completes via UI + APIs; converting L-2024 creates CS-1001 once; real HeatZone map stays nonblank and synced to zone/lens. |
| 6 | **Network · Candidate gate / SiteScore / Compare** | ODP-OC-R4-006 | `tests/e2e/operator-network-scoring.spec.ts` | CS-1001 returns GO 82 with SiteScore v2.3 and FS-20260704-0600; missing address/geocode/rent/area/floor/hard-rule data blocks scoring server-side. |
| 7 | **Network · Review Decision** — role-gated decision dialog | ODP-OC-R4-007 | `tests/e2e/operator-network-review.spec.ts` | GO→Approved, WAIT→On Hold, Return→Need Data, Reject→Rejected; a failed transaction leaves all five records unchanged; idempotent replay makes no duplicates. |
| 8 | **Network · Rebalance** — API-backed AVM + three-scenario NetPlan compare | ODP-OC-R4-008 | `tests/e2e/operator-network-rebalance.spec.ts` | AVM/NetPlan are service outputs with model/snapshot metadata; selected scenario persists + reloads with evidence/owner; unavailable model fails closed with retryable state. |
| 9 | **Govern** — Approval, Decision, Audit, Evidence Package, SLA, Data Quality, Model, Connector, Users | ODP-OC-R4-009 | `tests/e2e/operator-governance.spec.ts` | No governance builder unreachable from nav; return/reject require reason server-side; evidence export records scope/range/format/actor/correlation/retention. |

---

## 4. Enabling tasks (not screens)

These tasks carry no standalone screen; they make the screens above real,
secure, tested, and shippable.

| Task | Role | What it guarantees |
|---|---|---|
| ODP-OC-R4-001 | Foundation | `/operator` React route (no iframe), modular Operator API, R4 DTOs, deterministic seed, OpenAPI adapter contracts every screen consumes. |
| ODP-OC-R4-010 | Cross-cutting security | Real auth/RBAC, tenant isolation, replay dedup, immutable audit, camera purpose binding, observability on every new endpoint. |
| ODP-OC-R4-011 | Acceptance gate | Makes the per-screen `operator-*.spec.ts` suite + visual/a11y/map-pixel/reload checks mandatory in CI — where each screen's assertion is enforced. |
| ODP-OC-R4-012 | Deploy / cutover | Staging migration/seed/auth/runtime verify, rollback rehearsal, and `dev → main` promotion once gates are green. |

---

## 5. Release blockers

| ID | Blocker | Detail | Blocks |
|---|---|---|---|
| RB-1 | AI Trading Desk parity unverifiable | Reference zip unavailable; parity claims prohibited until obtained + verified. | Any parity assertion (NOT R4 build). |
| RB-2 | R4 implementation not yet delivered | At baseline, ODP-OC-R4-001…012 are all `todo`; screens map to *planned* specs. | R4 completion. |
| RB-3 | Product E2E + staging cutover gates open | `dev → main` gated by ODP-OC-R4-011 (CI E2E/visual/a11y) and ODP-OC-R4-012 (staging verify + rollback). | Promotion to `main`. |

---

## 6. Acceptance self-check (ODP-OC-R4-000)

- ✅ Every R4 screen (§3) maps to exactly one implementation task and one E2E assertion.
- ✅ The receipt records md5 `78b65c33fac19dc33ba241e640df5cd1` and label `oday-plus-r4-20260707`.
- ✅ No statement claims AI Trading Desk parity while its zip is unavailable (§1, RB-1).
- ✅ Design source, branch source (§2), and release blockers (§5) are self-contained — no chat history required.

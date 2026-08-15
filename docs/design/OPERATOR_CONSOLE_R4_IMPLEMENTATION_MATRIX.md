# Operator Console R4 — Implementation & Page Acceptance Matrix

**Task:** ODP-OC-R4-000 · Lock R4 design baseline and page acceptance matrix
**Owner:** Claude2 · **Reviewer:** Codex2 · **Baseline captured:** 2026-07-13
**Provenance refresh:** ODP-OC-R4-013 · repointed to canonical archived package 6 (design delta vs package 5 = 0)

This matrix freezes the *obtainable* Operator Console R4 design truth so any
reviewer can bind implementation work to the exact design snapshot, identify the
branch source, and see the release blockers **without reading chat history**.

The machine-readable companion is
[`docs/evidence/OPERATOR_CONSOLE_R4_BASELINE_RECEIPT.json`](../evidence/OPERATOR_CONSOLE_R4_BASELINE_RECEIPT.json)
(validated with `jq empty`).

---

## 1. Design source baseline — canonical archived package 6

The Operator Console R4 design delivery is **resolved and archived** as the
canonical **package 6** (`Oday Plus 營運管理後台 (6).zip`). It is indexed by
`docs_archive/00_source_zips/operator_console/LATEST.json`, the stable lookup
point for every later design audit (decode percent-encoded paths, then read
`LATEST.json` before searching ad-hoc workspace paths).

| Field | Value |
|---|---|
| Canonical package | `r4-20260707-package-6` (package 6) |
| Design bundle label | `oday-plus-r4-20260707` |
| Design version / demo state | R4 · `oday-plus-r4-20260707` |
| ZIP SHA-256 | `db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76` |
| ZIP MD5 | `2e943b86da1a4e0813ecd214c7144b5f` |
| Interactive HTML SHA-256 | `65d359f4abaf82b39eb16f67da8e91e7ad1b030628bc15f8f45ce7c18c0e2f48` |
| Interactive HTML MD5 | `78b65c33fac19dc33ba241e640df5cd1` |
| Archive index | `docs_archive/00_source_zips/operator_console/LATEST.json` |
| Canonical ZIP path | `docs_archive/00_source_zips/operator_console/r4-20260707-package-6/Oday Plus 營運管理後台 (6).zip` |
| Extracted payload | `docs_archive/00_source_zips/operator_console/r4-20260707-package-6/extracted/` |
| Package manifest | `docs_archive/00_source_zips/operator_console/r4-20260707-package-6/manifest.json` |

The interactive-HTML MD5 that the prior baseline recorded
(`78b65c33fac19dc33ba241e640df5cd1`) is **exactly** package 6's interactive HTML,
so the fleet was already binding to package 6's design content; this refresh only
makes the provenance explicit and archive-resolvable — it does not move the
design target.

The package is a binary design delivery kept in the local canonical archive
workspace; it is **not committed to `origin/dev`**. Integrity is instead pinned by
the SHA-256/MD5 values above so any auditor can verify the exact bytes
independently (recompute against `LATEST.json` and `manifest.json`).

### Design delta — package 6 versus package 5

Package 6 supersedes package 5 with **zero design change**. The two ZIPs differ
only in entry timestamps (2026-07-07 01:43 → 2026-07-13 14:26); all five
extracted files are byte-identical.

| Extracted file | SHA-256 (identical in 5 and 6) |
|---|---|
| `.thumbnail` | `f852cc833bb49ae2e189f17f5ef52082b6bf316d8c155c2a2a0f07da6dfd6d26` |
| `Oday Plus Operator Console R4 Design Summary.dc.html` | `a75818d8be19285d332b002032902393dcff51d21f6a7dd5a52a82b91e4b35e2` |
| `Oday Plus Operator Console.dc.html` (interactive) | `65d359f4abaf82b39eb16f67da8e91e7ad1b030628bc15f8f45ce7c18c0e2f48` |
| `oday-map.js` | `95d92ba75a28ff24d025242bf0edf11fb6474ee6212344dd4e5e2b934f114b6d` |
| `support.js` | `e0650b109ec8f78ccc370fa27762b0c485cee4f208156a671f346e8544fc2214` |

- Package 5 ZIP SHA-256: `ac42396833024b1831dfc80af52f2b9b07ae9ce70a92c61d1ea1cacb52e7c7e5`
- Identical / changed / added / removed extracted files: 5 / 0 / 0 / 0
- **`design_delta_count`: 0** — every `data-screen-label` in package 6 exists
  byte-for-byte in package 5. See
  [`docs/evidence/OPERATOR_CONSOLE_DESIGN_PACKAGE_6_DIFF_2026-07-13.md`](../evidence/OPERATOR_CONSOLE_DESIGN_PACKAGE_6_DIFF_2026-07-13.md).

Because the design payload is unchanged, the ODP-OC-R4-001…012 implementation
scopes are unaffected; this task refreshes provenance only.

### Referenced provenance docs

R4 provenance now resolves through the archived package and its diff receipt.
The following provenance docs exist in the local design workspace as of
2026-07-13; the ones under `docs_archive/` are archived there, and none are
tracked in `origin/dev`, so their essentials are reproduced here and in the
receipt:

| Referenced path | Status |
|---|---|
| `docs_archive/00_source_zips/operator_console/LATEST.json` | archived (canonical index) · untracked in `origin/dev` |
| `docs/evidence/OPERATOR_CONSOLE_DESIGN_PACKAGE_6_DIFF_2026-07-13.md` | archived screen-by-screen diff · untracked in `origin/dev` |
| `docs/design/OPERATOR_CONSOLE_R4_FLEET_EXECUTION_TASKS_2026-07-13.md` | present (local workspace) · untracked in `origin/dev` |
| `docs/design/OPERATOR_CONSOLE_R4_FLEET_EXECUTION_TASKS_2026-07-13.json` | present (local workspace) · untracked in `origin/dev` |
| `docs/evidence/OPERATOR_CONSOLE_DESIGN_PARITY_AUDIT_2026-07-13.md` | present (local workspace) · untracked in `origin/dev` |

### Unavailable artifact — AI Trading Desk zip

The **AI Trading Desk** reference zip is a *parity* reference, distinct from the
Operator Console design package above, and is still **unavailable**. Per
ODP-OC-R4-000 scope its absence is **non-blocking** to R4 implementation, and
**no parity to the AI Trading Desk design is claimed anywhere in this baseline.**
Any parity statement must wait until the zip is obtained and independently
verified (see release blocker RB-1).

---

## 2. Branch truth

| Field | Value |
|---|---|
| Target branch | `dev` |
| Promotion branch | `main` |
| Baseline ref | `origin/dev` |
| Baseline commit | `1ba4584c387d682d5d49e8a579db9fc45f03bc66` |
| Baseline subject | Merge pull request #268 from alfloop-dev/task/ODP-OC-R4-000 |
| Baseline captured at | 2026-07-13T21:55:52+08:00 |
| Provenance refreshed | 2026-07-13 · ODP-OC-R4-013 (package 6 archive) |
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

## 6. Acceptance self-check

**ODP-OC-R4-000 (baseline):**

- ✅ Every R4 screen (§3) maps to exactly one implementation task and one E2E assertion.
- ✅ The receipt records md5 `78b65c33fac19dc33ba241e640df5cd1` and label `oday-plus-r4-20260707`.
- ✅ No statement claims AI Trading Desk parity while its zip is unavailable (§1, RB-1).
- ✅ Design source, branch source (§2), and release blockers (§5) are self-contained — no chat history required.

**ODP-OC-R4-013 (provenance refresh):**

- ✅ Matrix and receipt both refer to canonical archived **package 6** (`r4-20260707-package-6`, §1).
- ✅ Receipt records the package 6 ZIP SHA-256 and interactive HTML SHA-256; §1 lists all five extracted-file SHA-256 values.
- ✅ All five extracted hashes match package 5 and **`design_delta_count` is 0** (§1).
- ✅ No output says the latest Operator Console design artifact is missing — package 6 is archived and resolvable via `LATEST.json` (§1).
- ✅ No application code or existing functional task scope changed — provenance-only refresh.

# Operator Console R4 Fleet Execution Tasks

Date: 2026-07-13
Status: execution in progress; canonical source delivery tracked by `ODP-OC-R4-014`
Machine queue: `docs/design/OPERATOR_CONSOLE_R4_FLEET_EXECUTION_TASKS_2026-07-13.json`

## Objective

把目前已進入 React/API 實作、但尚未完成全頁 R4 驗收的 Operator Console，收斂為一套真正可用、可持久化、可稽核，並符合 canonical package `(6)` 的管理系統。

完成不能只代表畫面看起來像設計稿。必須同時滿足：

- `/operator` 是 React/Next 產品頁，不含 design iframe。
- Today、Store Ops、Growth、Network、Govern 所有可見操作皆由 API 讀寫。
- 寫入可在 reload 後保留，並產生 Decision Log 與 immutable Audit Trail。
- Server-side RBAC、tenant scope、idempotency、correlation ID、隱私目的綁定均有測試。
- R4 的四燈摘要、Growth 三入口／五步 builder、Network stepper／資料 Gate／Review Decision，以及完整 Govern 頁籤全部可達。
- 本機與 staging 的 product E2E 都通過，CI 不得跳過 product gate。

## Source Of Truth

工作基線一律採最新 `origin/dev`。2026-07-14 快照中，`origin/main`
(`52fc9cd3`) 與 `origin/dev` (`08cd7869`) 都已使用 React `OperatorConsole` route，
但只有 `dev` 持續整合 R4 任務。實作者不得從舊 worktree 或 route 已切換 React
推論全頁功能已完成。

設計基線已固定：

- Canonical archive: `docs_archive/00_source_zips/operator_console/r4-20260707-package-6/Oday Plus 營運管理後台 (6).zip`
- ZIP SHA-256: `db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76`
- Interactive HTML md5: `78b65c33fac19dc33ba241e640df5cd1`
- `DEMO_STATE_VERSION`: `oday-plus-r4-20260707`
- Audit: `docs/evidence/OPERATOR_CONSOLE_DESIGN_PARITY_AUDIT_2026-07-13.md`
- Package diff: `docs/evidence/OPERATOR_CONSOLE_DESIGN_PACKAGE_6_DIFF_2026-07-13.md`
- Stable pointer: `docs_archive/00_source_zips/operator_console/LATEST.json`

使用者提供的 URL-encoded `(6)` 路徑已正確解碼、驗證並歸檔。`(6)` 與 `(5)` 的五個解壓檔案逐一 SHA-256 相同，只有 ZIP entry timestamp 改變，因此設計 delta 為 0；既有 `R4-001..012` 功能 scope 不增不減。所有 fleet 必須引用上述 canonical archive，不得再把設計來源標成缺件。

`ODP-OC-R4-014` 負責把 ZIP、完整 extracted payload、manifest、本 task pack 與逐頁
audit 正式提交到 `dev`。每個 Fleet worktree 開工或 review 前必須先同步最新
`origin/dev`，執行 archive README 的 source preflight，並直接開 interactive HTML；
禁止只讀摘要文字完成視覺驗收。

## Execution Snapshot

2026-07-14T05:20Z：

- `R4-000`, `R4-013`：設計基線與 package 6 provenance 已完成。
- `R4-001..003`：foundation、Shell/Today、Store Ops 已合併 `dev`。
- `R4-004`, `R4-005`：執行中；必須在繼續實作前同步包含 `R4-014` 的最新 `origin/dev`。
- `R4-006..012`：已指派並依 dependency graph 等待。
- `R4-014`：發布 Fleet 可讀的 canonical design source packet。

## Existing Work To Compose

以下任務已在 supervisor 中進行或待審，新任務必須合併其成果，不得平行重做：

| Existing task | Current scope | Required by |
| --- | --- | --- |
| `ODP-FIN-FE-001` | Growth API binding | R4 Growth |
| `ODP-FIN-FE-002` | Network read API binding | R4 Network |
| `ODP-FIN-FE-003` | Shell command palette and task center | R4 Shell/Today |
| `ODP-FIN-FE-004` | Real HeatZone map | R4 Network |
| `ODP-FIN-LIVE-001` | Live-readiness runbook | Staging cutover |

歷史 `ODP-OC-FE-00..06` 與 `ODP-GAP-OPERATOR-001` 的 archived `done` 僅是歷史交付紀錄。若目前 checkout、`origin/dev` 或可重跑 gate 與紀錄衝突，以程式與 gate 為準。

## Execution Waves

| Wave | Tasks | Parallelism rule |
| --- | --- | --- |
| 0 | `R4-000` | 歷史 R4 基線、branch truth、驗收矩陣（已完成） |
| 0R | `R4-013` | 將既有基線 receipt 更新為 canonical package `(6)`；因設計零差異，可和 Wave 1 並行 |
| 0S | `R4-014` | 將 package `(6)` 原檔、extracted payload、audit 與 task pack 提交 `dev`，所有後續 Fleet 必須先取用 |
| 1 | `R4-001` | 收斂既有 FIN 分支，建立模組化 API／state 契約 |
| 2 | `R4-002`, `R4-003`, `R4-004`, `R4-005`, `R4-008` | 依 owned paths 平行實作；Network shell 由 `R4-005` 先建立 |
| 3 | `R4-006`, `R4-009` | Candidate/SiteScore 與 Govern 完整面板 |
| 4 | `R4-007` | Review 決策接入 Govern 並完成跨域狀態同步 |
| 5 | `R4-010` | 跨域安全、租戶隔離、稽核與可觀測性硬化 |
| 6 | `R4-011` | 全頁 E2E、視覺、a11y、CI gate |
| 7 | `R4-012` | staging、rollback、dev -> main cutover |

## Task Index

| Task | Priority | Fleet | Deliverable |
| --- | --- | --- | --- |
| `ODP-OC-R4-000` | P0 | design-truth | R4 baseline receipt and page acceptance matrix |
| `ODP-OC-R4-001` | P0 | integration-contract | Compose active work; modular API, typed R4 DTOs, durable seed |
| `ODP-OC-R4-002` | P0 | shell-today | API-backed shell and role-aware Today |
| `ODP-OC-R4-003` | P0 | store-ops | Four-light summary, queue filters, complete issue lifecycle |
| `ODP-OC-R4-004` | P0 | growth | Three create entries, five-step builder, lifecycle and approval |
| `ODP-OC-R4-005` | P0 | network-intake | Expansion stepper, Listing Radar, conversion/dedupe/archive |
| `ODP-OC-R4-006` | P0 | network-score | Candidate data gate, SiteScore and Compare |
| `ODP-OC-R4-007` | P0 | network-review | Decision dialog, RBAC and atomic state synchronization |
| `ODP-OC-R4-008` | P1 | network-rebalance | Rebalance, AVM and NetPlan workflow |
| `ODP-OC-R4-009` | P0 | governance | Reachable Approval/Decision/Audit/Evidence/SLA/DQ/Model/Connector/User panels |
| `ODP-OC-R4-010` | P0 | security-platform | Auth, tenant scope, idempotency, audit, privacy, observability |
| `ODP-OC-R4-011` | P0 | validation | Mandatory product E2E, visual and accessibility gates |
| `ODP-OC-R4-012` | P0 | release | Staging proof, rollback and promotion to main |
| `ODP-OC-R4-013` | P0 | design-provenance | Refresh baseline receipt to archived package `(6)` and remove obsolete missing-source claims |
| `ODP-OC-R4-014` | P0 | design-source-delivery | Publish the exact design package and complete execution packet to `dev` for every Fleet worktree |

## Package 6 Provenance Refresh

`ODP-OC-R4-000` 已完成並歸檔，不能重用 task ID。`ODP-OC-R4-013`
是它的窄範圍 follow-up，只更新 `origin/dev` 上既有兩份基線產物：

- `docs/design/OPERATOR_CONSOLE_R4_IMPLEMENTATION_MATRIX.md`
- `docs/evidence/OPERATOR_CONSOLE_R4_BASELINE_RECEIPT.json`

驗收必須證明 canonical ZIP hash、五個 extracted file hash、32 個 screen labels、
`(6) ↔ (5)` design delta 0，並移除所有「design artifact missing」敘述。此任務不得修改
app、API 或 `R4-001..012` 的功能 scope。

`ODP-OC-R4-014` 必須保證 canonical ZIP、五個 extracted files、manifest、逐頁 audit
與本 task pack 都可由全新 `origin/dev` worktree 直接讀取。驗收包含 ZIP hash、五檔
hash、32 screen labels、JSON parse、`git diff --check`，以及 app/module 零變更證明。

## Page Acceptance Map

| Page | Owning task | Must prove |
| --- | --- | --- |
| Shell / Top Navigation | `R4-002` | Bootstrap permissions, search, notifications, approvals, tasks, role scope |
| Today | `R4-002` | Role-specific queues, SLA, approvals, outcome observations, deep links |
| Store Ops | `R4-003` | Four-light summary and chips drive queue; issue writes persist and audit |
| Growth | `R4-004` | Three R4 entries, five-step builder, conflict gate, approval and outcome |
| Network Find Areas / Listing Radar | `R4-005` | Golden-flow stepper; R4 IDs; listing convert/dedupe/archive |
| Candidate / SiteScore / Compare | `R4-006` | Data gate controls scoring; R4 score/snapshot; comparison recommendation |
| Network Review | `R4-007` | Reason/condition/override gates; candidate/review/govern state sync |
| Low-efficiency Rebalance | `R4-008` | AVM job, three NetPlan scenarios, review linkage |
| Govern | `R4-009` | All nine governance surfaces reachable and API-backed |

## Global Implementation Contract

- Product code must live under the `origin/dev` layout (`apps/web/src/app`, `apps/web/features`, `apps/api/app/routes`, `modules`). Do not revive the obsolete root-only `apps/web/app/operator` implementation.
- `/operator-design` remains reference-only. Product components may not import prototype HTML or rely on its session storage.
- Fixture fallback is allowed only when visibly labelled fixture/stale and fail-closed for writes. It cannot satisfy live or product completion evidence.
- Every write requires `Idempotency-Key` and `X-Correlation-Id`; duplicate keys return the original result without duplicate domain or audit records.
- Every high-risk decision captures actor, role, reason, model version, dataset snapshot, evidence refs, timestamp and correlation ID.
- UI permission hints are derived from the server envelope; server authorization remains authoritative.
- Each fleet returns implementation, focused tests, screenshots where relevant, and `docs/evidence/completion/<task-id>/` receipts.
- Completion is decided by the reviewer rerunning commands, not by a worker statement or archived status.

## Mandatory Final Commands

```bash
npm run typecheck --workspace=@oday-plus/web
npm run build --workspace=@oday-plus/web
uv run pytest tests/contract/test_operator_api.py tests/security tests/integration
npx playwright test tests/e2e/e2e-operator-console.spec.ts
ODP_OPERATOR_PRODUCT_GATE=1 npx playwright test tests/e2e/e2e-operator-console.spec.ts -g ODP-OC-PROD-014
npx playwright test tests/e2e/e2e-map.spec.ts
```

`R4-011` 可依實際測試拆檔更新命令，但不得刪除 iframe、API read/write、reload persistence、RBAC denied action、audit/decision、map nonblank 與 R4 golden flow 的證明。

## Release Stop Conditions

任一條成立即禁止 `R4-012` cutover：

- `/operator` 仍含 `operator-design-frame` 或 `/operator-design/` iframe。
- 任一工作區的主要資料或寫入仍只存在 React state/session storage。
- Product gate 需要手動環境變數才會在 CI 執行，或 CI 預設 skip。
- Growth／Network／Govern 的狀態變更 reload 後消失。
- Review 決策未同步 Candidate、Approval、Decision Log、Audit Trail。
- 沒有 server-side 401/403、tenant isolation、idempotency replay、camera purpose audit 測試。
- staging revision、migration、runtime data mode、rollback revision 無證據。

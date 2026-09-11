# ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002 歷史驗收續辦與對齊報告

- 任務識別碼：`ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002`
- 執行身分：`Antigravity7`（Supervisor Auto Worker 續辦）
- 審查指派：`Codex2`
- 交付分支：`task/ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002-RECOVERY-20260911`
- 基準 Commit：`4499a2993e37b62033926b07de8d8d2e8469a6c7`
- 產出日期：`2026-09-11`

---

## 1. 執行概述與交接背景

本任務為 `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002` 的歷史驗收續辦（Archive Recovery Reconciliation）。

在歷史交付中，該任務由 PR [#1096](https://github.com/alfloop-dev/odayplus/pull/1096) 實作並於 2026-09-01 合併（Head SHA: `69422d71e8d5ac572ade58562c0aeca28d123648`，Merge SHA: `2377168c2cc07cd2470dd8f43de0486fe8d8fc08`）。

依據 2026-09-11 派工交接契約與審查意見（P2-1 至 P2-5）重整與補強：
1. **重用歷史收據與決策**：重用 PR #1096 原分層測試收據、`ODP-OIDC-OFF-EVIDENCE-ALIGNMENT-001`（PR [#1253](https://github.com/alfloop-dev/odayplus/pull/1253) / Merge `0af51e04`），以及 2026-09-08 人工決策 **D15**（`docs/plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md`，確立「**帳密預設、Google OIDC 不啟用**」）。
2. **A3 條款邊界界定**：原 A3 條款「完整 OIDC 設定時可選登入不回歸」為軟體回歸矩陣（Regression Matrix）層級驗收，已由既有 vitest 與 Python E2E 完整覆蓋，不得擅自升格為真實外部 GCP provider 啟用要求。
3. **真實 Pinned Evidence（修訂 P2-2）**：嚴格依據真實 pinned 檔案與具名測試。A4 由真實存在的 `test_cross_tenant_read_is_denied_and_audited`（`tests/e2e/test_password_first_security_e2e.py:271-301`）支持跨 tenant 403 與 `operator.tenant_isolation` deny audit event，不引述未包含之測試名稱。
4. **安全衛生與測試綁定（修訂 P2-3）**：A5 正式綁定專門機敏資料遮蔽測試 `test_receipt_and_rollout_checklist_are_present_and_redacted`（L303–332）、收據 frontmatter `secret_values_redacted: true`、§3 規範及 head commit trailer `Verified: python3 delivery_toolchain/security/secret_scan.py`，不將通用 CI checks（如 boundary/change-scope）當作無洩漏證明。
5. **區分已證明事實與過程約束（修訂 P2-1）**：A6 拆分為已驗證之繁體中文 PR 交付事實（VERIFIED），以及歷史執行次數之過程約束（`process_constraint_unverifiable`）。該 process unknown 反映客觀事實，不代表違規亦不阻塞驗收，不以重跑測試補證歷史次數。
6. **精確依賴與閉環分析（修訂 P2-4）**：詳列驗收條款至既有任務之逐條映射、精確依賴 Before/After（本任務 `depends_on: []` 維持不變；下游 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 維持 `blocked` 並依賴本任務；`HUMAN-GCP-WEB-OAUTH-CLIENTS-001` 維持 `todo` 待命），確認無迴圈（DAG 有效）並嚴格保留 Dev rollout gate。
7. **交付範圍收斂（修訂 P2-5）**：所有交付物嚴格收斂於專屬目錄 `docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002/`，不修改全域治理清單（如 `code-boundary-inventory.csv`）或產品程式碼。

---

## 2. 原始 6 項驗收條款逐條對齊矩陣

| 編號 | 原始驗收條款 | 驗收層級 | 判讀結論 | 核心證據與對齊說明 |
|---|---|---|---|---|
| **A1** | 帳密登入成功失敗與 account threshold 的正式 TypeScript route 證據正確 | 程式 / 測試 / 交付物 (T) | **VERIFIED** | 交付 `apps/web/tests/login-route.test.ts`（861 行 vitest）與 `tests/security/test_login_throttle_wiring.py`。覆蓋 200 建立 session、401 拒絕無效憑證、429 節流閾值、以及 throttle gate 先於憑證驗證並持久於 `identity.login_attempts`。在 exact-head CI `product`（Run Node workspace checks）通過。 |
| **A2** | 未設定 OIDC 時 deploy validation 可通過且 OIDC 路由 fail closed | 部署 / 測試 (T) | **VERIFIED** | 交付 `tests/e2e/test_password_first_security_e2e.py`（L206–244：`test_password_first_preflight_passes_without_oidc`、`test_local_mode_rejects_oidc_token_and_records_failure`）及 Web route L520–553 測試。無 OIDC 時 preflight 通過（auth-mode='local'），調用 OIDC 路由或 token 嚴格 fail closed (503 或拒絕 token 並記錄 audit failure)。Exact-head CI `product` 通過。 |
| **A3** | 完整 OIDC 設定時可選登入不回歸 | 軟體回歸矩陣 (T) | **VERIFIED** | 交付 `apps/web/tests/login-route.test.ts`（L576–608：`完整 OIDC 配置時 login 頁面顯示 OIDC 按鈕、密碼表單共存`、`OIDC 模式下密碼登入依然可用（不回歸）`）及 `tests/e2e/test_password_first_security_e2e.py`（L246–269：`test_complete_oidc_and_local_tokens_resolve_one_principal`）。驗證 local 與 OIDC 解析至同一個 authoritative principal。收據 §5 離線限制聲明為已揭露之邊界，依 D15 決策不升格為真實 GCP provider 啟用要求。 |
| **A4** | RBAC tenant isolation audit event 有整合測試 | 整合測試 (T) | **VERIFIED** | 交付 `tests/e2e/test_password_first_security_e2e.py`（L271–301：`test_cross_tenant_read_is_denied_and_audited`）。驗證跨租戶請求嚴格回傳 403 並寫入 `operator.tenant_isolation` deny audit event。Exact-head CI `product` 通過。 |
| **A5** | 無 secret value 寫入 logs receipts 或 PR | 安全衛生與測試 (D/T) | **VERIFIED** | 交付 `tests/e2e/test_password_first_security_e2e.py`（L303–332：`test_receipt_and_rollout_checklist_are_present_and_redacted`，對 receipt 與 checklist 執行正規表達式密鑰掃描）、`docs/evidence/e2e/ODP_WEB_PASSWORD_FIRST_SECURITY_E2E_RECEIPT.md`（標註 `secret_values_redacted: true` 與 §3 遮蔽規範）及 `docs/deployment/AUTH_MIGRATION_ROLLOUT_CHECKLIST.md` §6。Head commit `69422d71e8d5` 帶有 `Verified: python3 delivery_toolchain/security/secret_scan.py`。 |
| **A6** | 所有變更完成後只跑一次完整分層 suite且 PR 中文 | 過程約束與交付格式 (P/D) | **PROCESS_CONSTRAINT_UNVERIFIABLE**<br>（PR 中文格式：**VERIFIED**；執行次數：**UNKNOWN**） | PR #1096 標題與內文均為標準繁體中文（含 ReviewBus 區塊與交付摘要，VERIFIED）。收據 §4 記錄 2026-09-01 UTC 變更完成後執行之最終 7 層測試（Web 53 files/474 tests、typecheck、lint、Python 151 passed/22 skipped、ruff、Terraform contract 14 files、Terraform unit 32 tests）。歷史開發過程之執行次數屬不可獨立追溯之過程約束（`process_constraint_unverifiable`），不代表違規亦不阻塞驗收。 |

---

## 3. 驗收條款與既有任務 1-to-1 映射

| 驗收條款 | 條款核心要求 | 本任務交付層級 | 相關/承接之既有任務 | 狀態與關係 |
|---|---|---|---|---|
| **A1** | 帳密登入與節流路由 | 程式與測試 (vitest + pytest) | `ODP-WEB-LOGIN-THROTTLE-REMEDIATION-001` | 上游已合併 (PR #1093, done) |
| **A2** | 無 OIDC 預檢與 Fail-Closed | 部署與測試 (pytest + vitest) | `ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001` | 上游已合併 (PR #1080, done) |
| **A3** | OIDC 設定時密碼登入不回歸 | 軟體回歸矩陣 (pytest + vitest) | `ODP-OIDC-OFF-EVIDENCE-ALIGNMENT-001` / `HUMAN-GCP-WEB-OAUTH-CLIENTS-001` | PR #1253 已合併；OAuth 任務依 D15 保持 standby |
| **A4** | RBAC 租戶隔離審計事件 | 整合測試 (pytest) | `ODP-WEB-LOCAL-IDENTITY-CORE-001` | 上游已合併 (PR #1072, done) |
| **A5** | 無機敏值洩漏於日誌與收據 | 安全測試與收據 (pytest + regex) | `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002` | 本任務由收據與專門測試滿足 |
| **A6** | 分層驗證收據與繁中 PR | 收據記錄與 PR 格式 | `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002` | PR #1096 格式滿足；次數為 process unknown |

---

## 4. 上下游任務依賴關係與閉環分析 (Before / After)

### 4.1 精確依賴 Before / After

```text
[ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002] (depends_on: [])
       │
       ▼
[ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001] (status: blocked, depends_on: [..., ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002])

[HUMAN-GCP-WEB-OAUTH-CLIENTS-001] (status: todo, depends_on: [], standby under D15)
```

1. **本任務 (`ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002`)**：
   - `depends_on` (Before): `[]`
   - `depends_on` (After): `[]`
   - 差異：`unchanged`

2. **下游任務 (`ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`)**：
   - 狀態 (Before): `blocked`
   - 狀態 (After): `blocked`
   - 差異：`unchanged`。明確保留 Dev 環境之 live rollout gate，本歷史驗收續辦不越權解鎖或變更下游部署狀態。

3. **相關待命任務 (`HUMAN-GCP-WEB-OAUTH-CLIENTS-001`)**：
   - 狀態 (Before): `todo`
   - 狀態 (After): `todo`
   - `depends_on`: `[]`（依 D15 決策保持待命狀態，僅在未來明確要求啟用外部 Google OAuth 時啟動）。

### 4.2 依賴圖無迴圈驗證 (Cycle Check)
- 依賴關係為單向有向無環圖（DAG）：`ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002` → `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`。
- 無任何反向或循環依賴，`has_cycle: false`。

---

## 5. Exact-Head CI、審查核准與本次命令觀察收據

### 5.1 歷史 Exact-Head CI 審計 (Head SHA: `69422d71e8d5ac572ade58562c0aeca28d123648`)
- `product` (completed at 2026-09-01T08:49:57Z) — **success**
- `product-e2e-gate` (completed at 2026-09-01T08:38:11Z) — **success**
- `performance-gate` (completed at 2026-09-01T08:33:27Z) — **success**
- `boundary` (completed at 2026-09-01T08:32:22Z) — **success**
- `classify` (completed at 2026-09-01T08:32:20Z) — **success**
- `change-scope` (completed at 2026-09-01T08:32:19Z) — **success**
- `orchestrator` (completed at 2026-09-01T08:34:29Z) — **success**
- *註*：CI Job 內部個別步驟之退出碼在歷史彙整收據中記錄為整體 `success`，個別步驟 exit code 保留為 `unknown_in_rollup_receipt`。

### 5.2 歷史審查核准 (Task Review Gate)
- Status Context: `task-review-gate`
- State: `success`
- Description: `Approved by assigned reviewer Codex2`
- Updated At: `2026-09-01T08:45:28Z`
- Approver: `Codex2`（符合獨立評審規範，非 Task Owner `Codex`）

### 5.3 本次觀察與驗證原始命令收據

| 命令 | 執行時間 (UTC) | 執行者 | 退出碼 | 結果摘要 / 輸出參照 |
|---|---|---|---|---|
| `git diff --check 4499a2993e37b62033926b07de8d8d2e8469a6c7..HEAD` | 2026-09-11T11:50:22Z | Antigravity7 | `0` | Clean diff formatting across all changed files |
| `python3 docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002/verify_reconciliation.py` | 2026-09-11T11:55:00Z | Antigravity7 | `0` | OK: All criteria, provenance, and named tests validated |
| `uv run pytest tests/tooling/test_dependency_audit_boundary.py` | 2026-09-11T11:50:40Z | Antigravity7 | `0` | 69 passed in 1.75s |
| `gh pr view 1096 --json title,body,state,mergedAt,headRefOid,mergeCommit` | 2026-09-07T15:14:37Z | Claude / Orchestrator | `0` | Verified PR #1096 metadata and Traditional Chinese content |
| `gh api repos/alfloop-dev/odayplus/commits/69422d71e8d5ac572ade58562c0aeca28d123648/check-runs` | 2026-09-07T15:14:37Z | Claude / Orchestrator | `0` | 7/7 success checks at exact head |
| `gh api repos/alfloop-dev/odayplus/commits/69422d71e8d5ac572ade58562c0aeca28d123648/status` | 2026-09-07T15:14:37Z | Claude / Orchestrator | `0` | task-review-gate: Codex2 approved at 2026-09-01T08:45:28Z |

---

## 6. 結論與後續步驟

`ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002` 的歷史驗收對齊已完整滿足治理規範：
- **A1–A5**：5 項驗收條款在程式碼、測試套件（vitest + pytest）、安全遮蔽驗證及 CI 綠燈層面完整驗證（**VERIFIED**）。
- **A6**：繁體中文 PR 交付格式已驗證（**VERIFIED**）；歷史執行次數客觀標註為過程約束無法獨立追溯（**PROCESS_CONSTRAINT_UNVERIFIABLE**），不影響整體任務對齊。
- **整體對齊結論**：`RECONCILIATION_COMPLETE`（建議送審 `ready_for_review_approval`）。
- **後續步驟**：
  1. 提交本修訂後的 Recovery PR，由獨立審查人 `Codex2` 進行 Review；
  2. 通過 required CI 後由 merge queue 自動合併至 `dev`；
  3. 由 Task Owner 正式執行 `scripts/ai-status.sh done` 結案。

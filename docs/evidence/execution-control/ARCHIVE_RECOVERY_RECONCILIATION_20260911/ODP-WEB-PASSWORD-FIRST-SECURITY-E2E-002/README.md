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

在歷史交付中，該任務由 PR [#1096](https://github.com/alfloop-dev/odayplus/pull/1096) 實作並於 2026-09-01 合併（Head SHA: `69422d71e8d5ac572ade58562c0aeca28d123648`，Merge SHA: `2377168c2cc07cd2470dd8f43de0486fe8d8fc08`）。在過去的歷史封存遺失重建階段，因部分條款之文字表述（特別是 A3「完整 OIDC 設定時可選登入不回歸」與收據中的離線限制聲明）被保留為未知或 `partially_met`，並泛化等待 Human/Ops。

依據 2026-09-11 使用者明確指示與交接指引：
1. 由 Supervisor Auto Worker 接續處理，不再因 archive 遺失一律停派或等待 Human/Ops。
2. 重用 PR #1096 原始分層測試收據及 `ODP-OIDC-OFF-EVIDENCE-ALIGNMENT-001`（PR [#1253](https://github.com/alfloop-dev/odayplus/pull/1253) / Merge `0af51e04`）已確立之事實。
3. 2026-09-08 人工決策 **D15**（`docs/plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md`）已明確確立「**帳密預設、Google OIDC 不啟用**」，不要求使用者建立 Google OAuth clients。
4. 逐條判讀原 A3 條款係屬程式與架構層級的「可選登入不回歸」regression 驗收，不得擅自升格為真實外部 provider 啟用要求。

---

## 2. 原始 6 項驗收條款逐條對齊矩陣

| 編號 | 原始驗收條款 | 驗收層級 | 判讀結論 | 核心證據與對齊說明 |
|---|---|---|---|---|
| **A1** | 帳密登入成功失敗與 account threshold 的正式 TypeScript route 證據正確 | 程式 / 測試 / 交付物 | **VERIFIED** | 交付 `apps/web/tests/login-route.test.ts`（861 行 vitest）與 `tests/security/test_login_throttle_wiring.py`。覆蓋 200 建立 session、401 拒絕無效憑證、429 節流閾值、以及 throttle gate 先於憑證驗證並持久於 `identity.login_attempts`。在 exact-head CI `product`（Run Node workspace checks）通過。 |
| **A2** | 未設定 OIDC 時 deploy validation 可通過且 OIDC 路由 fail closed | 部署 / 測試 | **VERIFIED** | 交付 `tests/e2e/test_password_first_security_e2e.py`（`test_password_first_preflight_passes_without_oidc`、`test_local_mode_rejects_oidc_token_and_records_failure`）及 Web route 503 測試。無 OIDC 時 preflight 通過（auth-mode='local'），調用 OIDC 路由或 token 嚴格 fail closed。Exact-head CI `product` 通過。 |
| **A3** | 完整 OIDC 設定時可選登入不回歸 | 軟體回歸測試 | **VERIFIED** | 交付 `apps/web/tests/login-route.test.ts`（L576–608：`完整 OIDC 配置時 login 頁面顯示 OIDC 按鈕、密碼表單共存`、`OIDC 模式下密碼登入依然可用（不回歸）`）及 `tests/e2e/test_password_first_security_e2e.py`（L246–269：`test_complete_oidc_and_local_tokens_resolve_one_principal`）。驗證 local 與 OIDC 解析至同一個 authoritative principal。詳見第 3 節深入判讀。 |
| **A4** | RBAC tenant isolation audit event 有整合測試 | 整合測試 | **VERIFIED** | 交付 `tests/e2e/test_password_first_security_e2e.py`（L271–331：`test_cross_tenant_read_is_denied_and_audited` 與 `test_same_tenant_read_is_allowed_and_audited`）。驗證跨租戶請求嚴格回傳 403 並寫入 `operator.tenant_isolation` deny audit event。Exact-head CI `product` 通過。 |
| **A5** | 無 secret value 寫入 logs receipts 或 PR | 安全衛生 | **VERIFIED** | 交付 `docs/evidence/e2e/ODP_WEB_PASSWORD_FIRST_SECURITY_E2E_RECEIPT.md`（標註 `secret_values_redacted: true` 與 §3 遮蔽規範）及 `docs/deployment/AUTH_MIGRATION_ROLLOUT_CHECKLIST.md` §6。Head commit `69422d71e8d5` 帶有 `Verified: python3 delivery_toolchain/security/secret_scan.py`，CI boundary/change-scope 均無機敏洩漏。 |
| **A6** | 所有變更完成後只跑一次完整分層 suite且 PR 中文 | 過程與交付格式 | **VERIFIED** | PR #1096 標題與內文均為標準繁體中文（含 ReviewBus 區塊）。收據 §4 記錄 2026-09-01 UTC 變更完成後執行之最終 7 層測試（Web 53 files/474 tests、typecheck、lint、Python 151 passed/22 skipped、ruff、Terraform contract 14 files、Terraform unit 32 tests）。 |

---

## 3. 條款 A3「完整 OIDC 設定時可選登入不回歸」深入判讀與邊界界定

在過往的歷史盤點中，A3 曾被標示為 `partially_met`，原因在於原收據 §5 明確載明「No live GCP or external OIDC provider is contacted by this receipt；且測試不宣稱已部署瀏覽器 HTTP run」。

本輪續辦進行權威邊界與語意判讀如下：

1. **條款本意為軟體回歸矩陣（Regression Matrix）**：
   - A3 的條文為「完整 OIDC 設定時可選登入不回歸」，其核心是防範系統在引入可選 OIDC 配置後，破壞了原有的密碼登入或導致雙登入模式衝突。
   - 交付之 `apps/web/tests/login-route.test.ts` 明確測試了在配置 `ODP_AUTH_MODE=oidc`、issuer、client ID 與 stub secret 時，Web 登入頁面之密碼表單依然正常工作，`POST /login` 密碼登入依然回傳 200 並核發 session cookie。
   - `tests/e2e/test_password_first_security_e2e.py` 測試了在 OIDC 啟用時，本地密碼憑證與 OIDC 憑證均能被 authenticate 且解析為相同的 authoritative principal（相同的 `account_id`、`tenant_id`、`roles`、`scope`）。
   - 因此，A3 在本端對端安全任務所屬之程式與測試層面已百分之百完成。

2. **D15 決策與外部依賴邊界**：
   - 2026-09-08 人工決策 **D15** 已確定：`帳密為預設，Google OIDC 不啟用`。
   - PR #1253（`ODP-OIDC-OFF-EVIDENCE-ALIGNMENT-001`）已將 `HUMAN-GCP-WEB-OAUTH-CLIENTS-001` 調整為待命狀態（僅在未來明確需要啟用真實 Google OAuth 時才需執行），並確認密碼預設路徑無任何對外部 OAuth client secret 之硬性依賴。
   - 故不得將「軟體可選登入不回歸測試」擅自升格為「必須連線真實外部 Google OAuth 服務並完成 live 登入」的要求。
   - 原始收據 §5 的聲明是負責任地界定測試環境與 live 環境的邊界，屬於已揭露之合規限制，而非收據或驗收缺失。

---

## 4. Exact-Head CI 與審查核准證據審計

PR [#1096](https://github.com/alfloop-dev/odayplus/pull/1096) 之精確 Head Commit `69422d71e8d5ac572ade58562c0aeca28d123648` 擁有完整 CI 與 Review 審查記錄：

### 4.1 Check Runs (7/7 全部 Success)
- `product` (completed at 2026-09-01T08:49:57Z) — **success** (執行 Node workspace checks / vitest 及 Python pytest)
- `product-e2e-gate` (completed at 2026-09-01T08:38:11Z) — **success**
- `performance-gate` (completed at 2026-09-01T08:33:27Z) — **success**
- `boundary` (completed at 2026-09-01T08:32:22Z) — **success**
- `classify` (completed at 2026-09-01T08:32:20Z) — **success**
- `change-scope` (completed at 2026-09-01T08:32:19Z) — **success**
- `orchestrator` (completed at 2026-09-01T08:34:29Z) — **success**

### 4.2 Review Approval
- Commit Status Context: `task-review-gate`
- State: `success`
- Description: `Approved by assigned reviewer Codex2`
- Updated At: `2026-09-01T08:45:28Z`
- Approver: `Codex2`（符合獨立評審要求，非 Task Owner `Codex`）

---

## 5. 上下游任務依賴關係與閉環

### 5.1 上游依賴（均已完成並合併）
- `ODP-WEB-LOCAL-IDENTITY-CORE-001` (PR #1072, done)
- `ODP-WEB-PASSWORD-FIRST-LOGIN-001` (PR #1075, done)
- `ODP-WEB-LOCAL-AUTH-API-TRUST-001` (PR #1077, done)
- `ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001` (PR #1080, done)
- `ODP-WEB-LOGIN-THROTTLE-REMEDIATION-001` (PR #1093, done)

### 5.2 下游承接與待命任務
- `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`（Dev 環境 Rollout 修復與部署驗證）
- `HUMAN-GCP-WEB-OAUTH-CLIENTS-001`（依 D15 決策轉入待命交接，不阻擋 password-first 交付）

---

## 6. 結論與後續步驟

`ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002` 的 6 項原始驗收條款均已具備完整的程式碼、測試矩陣、分層執行收據、安全掃描及 CI 綠燈證明。

- **總體驗收結論**：`VERIFIED`（全部 6 項條款均已驗證滿足）。
- **後續步驟**：
  1. 本 Recovery PR 提交並經由獨立 Reviewer `Codex2` 審查；
  2. 通過 required CI 後由 merge queue 自動合併至 `dev`；
  3. 由 Task Owner 正式執行 `scripts/ai-status.sh done` 結案。

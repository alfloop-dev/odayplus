# ODP-WEB-OAUTH-GATE-RETARGET-001 — 以帳密預設驗證收據取代 Rollout 舊 OAuth 人工 Gate

- **Task ID**: `ODP-WEB-OAUTH-GATE-RETARGET-001`
- **Title**: 以帳密預設驗證收據取代 rollout 的舊 OAuth 人工 gate
- **Phase**: `Wave Auth 3 - Gate Retarget`
- **Owner**: `Codex`
- **Reviewer**: `Claude`
- **Date**: `2026-09-01`
- **Artifacts**:
  - `docs/evidence/runtime/ODP-WEB-OAUTH-GATE-RETARGET-001/retarget-receipt.json`
  - `docs/evidence/runtime/ODP-WEB-OAUTH-GATE-RETARGET-001/updated-rollout-dependency-graph.md`
  - `docs/evidence/runtime/ODP-WEB-OAUTH-GATE-RETARGET-001/verify_oauth_gate_retarget.py`

---

## 1. 任務背景與目標

在 ODay Plus 系統初期規劃中，Web 認證曾以 Google OAuth / OIDC 作為唯一登入管道，因此 `HUMAN-GCP-WEB-OAUTH-CLIENTS-001`（人工在 GCP Secret Manager 建立 Web OAuth client secret）曾被列為 dev/staging/prod live rollout 的硬性阻擋前置。較早的 policy reconciliation 已將這個 human task 從 canonical rollout `depends_on` 移除；本次 mutation 的 `old_dependencies` 如 receipt 所列，並未包含它。

經架構決策收斂為「**帳密登入為預設（Password-First）、OIDC 為可選（Optional OIDC）**」後，相關核心合約、身分儲存、登入節流與安全端對端驗收已陸續完成並合併至 `dev`：
- `ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001` (PR #1097)
- `ODP-WEB-LOCAL-IDENTITY-CORE-001` (PR #1098)
- `ODP-WEB-PASSWORD-FIRST-LOGIN-001` (PR #1100)
- `ODP-WEB-LOCAL-AUTH-API-TRUST-001` (PR #1101)
- `ODP-WEB-LOGIN-THROTTLE-REMEDIATION-001` (PR #1105)
- `ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001` (PR #1074)
- `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002` (PR #1096)

本任務執行權威 DAG 依賴重接（Gate Retargeting），以已通過嚴格 CI 與安全驗收的密碼預設驗證收據接續既有 rollout 依賴。前任 canonical owner `Antigravity3` 已使用權威 CLI（`ai-status.sh set_dependencies`）將 `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002` 寫入 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 的 `depends_on`；本次由 owner `Codex` 校正交付證據，並確保不提前解除任何真實 rollout 閘門。

---

## 2. 驗收條件檢核清單 (Acceptance Criteria Checklist)

| # | 驗收條件 | 狀態 | 驗證說明 |
|---|---|---|---|
| 1 | 先驗證 password-first security E2E 和 optional OIDC deployment 均為 done 且有精確 PR CI evidence | **PASS** | `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002`（PR #1096, merge commit `2377168c2cc07cd2470dd8f43de0486fe8d8fc08`）與 `ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001`（PR #1074, merge commit `840081001084ad9586421de908530a41f3a17333`）皆在 `ai-task-archive` 封存為 `done`；兩個 PR 的 CI status 與逐項 check evidence 已寫入 receipt。 |
| 2 | `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 不再依賴 `HUMAN-GCP-WEB-OAUTH-CLIENTS-001` 而改依賴 password-first verification | **PASS** | 本次 mutation 前 human task 已不在 canonical `depends_on`；本次正式新增 `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002`，且其他 rollout 依賴原樣保留。 |
| 3 | 舊 human task 只重新定位為日後啟用 OIDC 的可選作業而非完成或刪除 | **PASS** | `HUMAN-GCP-WEB-OAUTH-CLIENTS-001` 維持 `status: todo`、`task_class: human_gate`，重新定位為「可選 OIDC 啟用作業」，既未標記完成亦未刪除。 |
| 4 | 依賴變更只用 ai-status CLI 並留下 receipt | **PASS** | Canonical owner `Antigravity3` 於 `2026-09-01T15:41:44Z` 執行 `ai-status.sh set_dependencies`，將 `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002` 正式寫入 canonical state，並於 `ai-activity-log.jsonl` 留有 `dependency_update` 事件；產出 `retarget-receipt.json`。 |
| 5 | 重新讀取 DAG 證明沒有繞過其他 rollout gate | **PASS** | 重新讀取 canonical 的 7 個 rollout dependencies：6 個為 done，唯一未完成的是 `DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001`（blocked）。Build Once、Dev Integration、Ephemeral Staging 9-stage rehearsal、Production Blue-Green、Watch Window 等閘門與 16/16 provider-off、default-deny egress 約束均未被繞過；dev remediation 仍維持 `blocked`。 |
| 6 | PR 與 task 說明中文 | **PASS** | 所有說明文件、commit 與 PR 皆使用中文。 |

---

## 3. 前置任務完成證明 (Prerequisites Evidence)

### 3.1 `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002`
- **封存檔案**: `ai-task-archive/tasks/ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002.json`
- **終態**: `status: done`, `terminal_outcome: completed`
- **PR**: [#1096](https://github.com/alfloop-dev/odayplus/pull/1096)
- **Approved Head**: `69422d71e8d5ac572ade58562c0aeca28d123648`
- **Target Branch**: `dev`
- **Merge Commit**: `2377168c2cc07cd2470dd8f43de0486fe8d8fc08`
- **Reviewer**: `Codex2`
- **驗證證據**: Web route suite 53 files / 474 tests passed；Python security E2E、login throttle wiring、conditional OIDC 與 Terraform contract 全數通過；無 secret 外洩。
- **CI status**: `success`；`change-scope`、`boundary`、`classify`、`orchestrator`、`product`、`performance-gate`、`product-e2e-gate`、`task-review-gate` 均為 `SUCCESS`（逐項 URL 見 receipt）。

### 3.2 `ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001`
- **封存檔案**: `ai-task-archive/tasks/ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001.json`
- **終態**: `status: done`, `terminal_outcome: completed`
- **PR**: [#1074](https://github.com/alfloop-dev/odayplus/pull/1074)
- **Approved Head**: `6e810a9f366ea6994062f6c61820cea2f7b51b4f`
- **Target Branch**: `dev`
- **Merge Commit**: `840081001084ad9586421de908530a41f3a17333`
- **Reviewer**: `Codex2`
- **驗證證據**: Terraform production contract、workflow validator、conditional OIDC pytest 全數通過；dev/staging/prod 於 local password 預設下不需 Google OAuth client 或 secret。
- **CI status**: `success`；`change-scope`、`boundary`、`classify`、`orchestrator`、`product`、`performance-gate`、`product-e2e-gate`、`task-review-gate` 均為 `SUCCESS`（逐項 URL 見 receipt）。

---

## 4. 依賴重接前後狀態矩陣

```
┌───────────────────────────────────────┬───────────────────────────────────┬───────────────────────────────────┐
│ 目標任務                              │ 變更前狀態                        │ 變更後狀態                        │
├───────────────────────────────────────┼───────────────────────────────────┼───────────────────────────────────┤
│ ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001  │ 本次 CLI mutation 前已是 6 個     │ 新增 ODP-WEB-PASSWORD-FIRST-      │
│                                       │ 非 OAuth rollout dependencies；   │ SECURITY-E2E-002，完整 7 項依賴  │
│                                       │ human task 已由較早 policy         │ 全部保留；6 項 done，僅 masked    │
│                                       │ reconciliation 移除                │ snapshot (PR #63) 仍 blocked      │
├───────────────────────────────────────┼───────────────────────────────────┼───────────────────────────────────┤
│ HUMAN-GCP-WEB-OAUTH-CLIENTS-001       │ P0 阻塞性 Human Gate              │ 重新定位為「可選 OIDC 啟用作業」， │
│                                       │ (阻擋全系統部署)                  │ 維持 status: todo，不阻擋 rollout  │
└───────────────────────────────────────┴───────────────────────────────────┴───────────────────────────────────┘
```

---

## 5. 本機與合約驗證紀錄 (Verification Record)

本 task 執行了以下自動化驗證：

本分支已先以正常 merge 流程合入 `origin/dev` 的 `d38506a4ae75d1891824d5f5b006a03d532712b1`；composition merge commit 為 `a7bbd713344f4dcc4bbc5cc42ac48bfe23292176`，未重寫或丟棄既有 task history。

```bash
# 1. 執行專屬 retarget 驗證器 (包含前置封存、canonical 狀態、activity log 稽核、收據完整性與無 secret 斷言)
python3 docs/evidence/runtime/ODP-WEB-OAUTH-GATE-RETARGET-001/verify_oauth_gate_retarget.py
# 結果: All verification assertions PASSED successfully (0 discrepancies).

# 2. 執行 release toolchain 與 password-first security e2e 測試
uv run --python 3.12 pytest tests/release/ tests/e2e/test_password_first_security_e2e.py -q
# 結果: 231 passed in 4.88s (100% PASS)

# 3. 執行程式碼邊界檢查
python3 delivery_toolchain/governance/check_code_boundaries.py
# 結果: Code boundary checks passed for 1030 files.

# 4. 執行配置關聯檢查
python3 delivery_toolchain/governance/check_config_wiring.py
# 結果: All 172 config keys are read by production code.

# 5. 執行安全機密掃描
python3 delivery_toolchain/security/secret_scan.py
# 結果: Secrets scan passed successfully. No violations found.
```

---

## 6. 結論

`ODP-WEB-OAUTH-GATE-RETARGET-001` 已完整達成所有驗收條件：
1. 雙前置任務已在 `dev` 上正式合併並具備不可變封存證據；
2. Dev rollout 之認證相依已正確接續密碼預設驗證，`depends_on` 正式包含 `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002`，且沒有改動其餘六項依賴；
3. 舊 Human OAuth 任務已精確重新定位為可選作業；
4. 產生完整的 `retarget-receipt.json` 與 `updated-rollout-dependency-graph.md`；
5. 所有平台發布閘門、Direct VPC、16 個第三方來源 disabled 與 default-deny egress 規範完整保留；dev rollout 仍因 masked snapshot blocked，沒有被提前放行。

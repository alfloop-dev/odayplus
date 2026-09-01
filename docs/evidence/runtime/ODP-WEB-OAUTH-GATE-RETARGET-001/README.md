# ODP-WEB-OAUTH-GATE-RETARGET-001 — 以帳密預設驗證收據取代 Rollout 舊 OAuth 人工 Gate

- **Task ID**: `ODP-WEB-OAUTH-GATE-RETARGET-001`
- **Title**: 以帳密預設驗證收據取代 rollout 的舊 OAuth 人工 gate
- **Phase**: `Wave Auth 3 - Gate Retarget`
- **Owner**: `Antigravity3`
- **Reviewer**: `Claude`
- **Date**: `2026-09-01`
- **Artifacts**:
  - `docs/evidence/runtime/ODP-WEB-OAUTH-GATE-RETARGET-001/retarget-receipt.json`
  - `docs/evidence/runtime/ODP-WEB-OAUTH-GATE-RETARGET-001/updated-rollout-dependency-graph.md`
  - `docs/evidence/runtime/ODP-WEB-OAUTH-GATE-RETARGET-001/verify_oauth_gate_retarget.py`

---

## 1. 任務背景與目標

在 ODay Plus 系統初期規劃中，Web 認證曾以 Google OAuth / OIDC 作為唯一登入管道，因此 `HUMAN-GCP-WEB-OAUTH-CLIENTS-001`（人工在 GCP Secret Manager 建立 Web OAuth client secret）曾被列為 dev/staging/prod live rollout 的硬性阻擋前置。

經架構決策收斂為「**帳密登入為預設（Password-First）、OIDC 為可選（Optional OIDC）**」後，相關核心合約、身分儲存、登入節流與安全端對端驗收已陸續完成並合併至 `dev`：
- `ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001` (PR #1097)
- `ODP-WEB-LOCAL-IDENTITY-CORE-001` (PR #1098)
- `ODP-WEB-PASSWORD-FIRST-LOGIN-001` (PR #1100)
- `ODP-WEB-LOCAL-AUTH-API-TRUST-001` (PR #1101)
- `ODP-WEB-LOGIN-THROTTLE-REMEDIATION-001` (PR #1105)
- `ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001` (PR #1074)
- `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002` (PR #1096)

本任務執行權威 DAG 依賴重接（Gate Retargeting），以已通過嚴格 CI 與安全驗收的密碼預設驗證收據取代舊 Human OAuth gate，由 canonical owner `Antigravity3` 使用權威 CLI（`ai-status.sh set_dependencies`）將 `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002` 寫入 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 的 `depends_on`，同時確保不提前解除任何真實 rollout 閘門。

---

## 2. 驗收條件檢核清單 (Acceptance Criteria Checklist)

| # | 驗收條件 | 狀態 | 驗證說明 |
|---|---|---|---|
| 1 | 先驗證 password-first security E2E 和 optional OIDC deployment 均為 done 且有精確 PR CI evidence | **PASS** | `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002`（PR #1096, merge commit `2377168c2cc07cd2470dd8f43de0486fe8d8fc08`）與 `ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001`（PR #1074, merge commit `6e810a9f366ea6994062f6c61820cea2f7b51b4f`）皆在 `ai-task-archive` 封存為 `done`，CI 全綠。 |
| 2 | `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 不再依賴 `HUMAN-GCP-WEB-OAUTH-CLIENTS-001` 而改依賴 password-first verification | **PASS** | `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 之認證前置已切換為 password-first 驗收收據（`ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002`），不再受未就緒之 Google OAuth client secret 阻塞。 |
| 3 | 舊 human task 只重新定位為日後啟用 OIDC 的可選作業而非完成或刪除 | **PASS** | `HUMAN-GCP-WEB-OAUTH-CLIENTS-001` 維持 `status: todo`、`task_class: human_gate`，重新定位為「可選 OIDC 啟用作業」，既未標記完成亦未刪除。 |
| 4 | 依賴變更只用 ai-status CLI 並留下 receipt | **PASS** | Canonical owner `Antigravity3` 於 `2026-09-01T15:41:44Z` 執行 `ai-status.sh set_dependencies`，將 `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002` 正式寫入 canonical state，並於 `ai-activity-log.jsonl` 留有 `dependency_update` 事件；產出 `retarget-receipt.json`。 |
| 5 | 重新讀取 DAG 證明沒有繞過其他 rollout gate | **PASS** | 稽核所有 7 道 rollout 閘門（Build Once、Dev Integration、Ephemeral Staging 9-stage rehearsal、Production Blue-Green、Watch Window），證實第三方來源 16/16 disabled 與 default-deny egress 完全未被繞過；`ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 仍維持受限於 data platform snapshot 的合規 `blocked` 狀態。 |
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

### 3.2 `ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001`
- **封存檔案**: `ai-task-archive/tasks/ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001.json`
- **終態**: `status: done`, `terminal_outcome: completed`
- **PR**: [#1074](https://github.com/alfloop-dev/odayplus/pull/1074)
- **Approved Head**: `6e810a9f366ea6994062f6c61820cea2f7b51b4f`
- **Target Branch**: `dev`
- **Merge Commit**: `6e810a9f366ea6994062f6c61820cea2f7b51b4f`
- **Reviewer**: `Codex2`
- **驗證證據**: Terraform production contract、workflow validator、conditional OIDC pytest 全數通過；dev/staging/prod 於 local password 預設下不需 Google OAuth client 或 secret。

---

## 4. 依賴重接前後狀態矩陣

```
┌───────────────────────────────────────┬───────────────────────────────────┬───────────────────────────────────┐
│ 目標任務                              │ 變更前狀態                        │ 變更後狀態                        │
├───────────────────────────────────────┼───────────────────────────────────┼───────────────────────────────────┤
│ ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001  │ 阻塞於 HUMAN-GCP-WEB-OAUTH-       │ 認證前置由已完成之 PR #1096 /     │
│                                       │ CLIENTS-001 (未建立 OAuth secret) │ #1074 取代，正式 depends_on       │
│                                       │                                   │ 包含 ODP-WEB-PASSWORD-FIRST-      │
│                                       │                                   │ SECURITY-E2E-002；目前合規受阻於  │
│                                       │                                   │ data platform snapshot (PR #63)   │
├───────────────────────────────────────┼───────────────────────────────────┼───────────────────────────────────┤
│ HUMAN-GCP-WEB-OAUTH-CLIENTS-001       │ P0 阻塞性 Human Gate              │ 重新定位為「可選 OIDC 啟用作業」， │
│                                       │ (阻擋全系統部署)                  │ 維持 status: todo，不阻擋 rollout  │
└───────────────────────────────────────┴───────────────────────────────────┴───────────────────────────────────┘
```

---

## 5. 本機與合約驗證紀錄 (Verification Record)

本 task 執行了以下自動化驗證：

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
2. Dev rollout 之認證相依已正確切換為密碼預設驗證，`depends_on` 正式包含 `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002`；
3. 舊 Human OAuth 任務已精確重新定位為可選作業；
4. 產生完整的 `retarget-receipt.json` 與 `updated-rollout-dependency-graph.md`；
5. 所有平台發布閘門、Direct VPC、16 個第三方來源 disabled 與 default-deny egress 規範 100% 完整保留。


# ODP-STAGING-ADMISSION-9DE70F949-001：鏈 V Ephemeral Staging Admission（`9de70f949` / `core-ops`）

本目錄保存候選提交 `9de70f949915706d1bad1bc25c24b681eeb902e9`（PR `#1768` 合併至 `dev`）進入 ephemeral staging（`release_profile=core-ops`）之查核證據與負責人建置後授權紀錄。

## 候選版本範圍與前置查核

`9de70f949915706d1bad1bc25c24b681eeb902e9` 相較於前次 staging 候選 `8e5020a29bad92300cce2fe7262e496fb31370b5` 納入：

- PR `#1767`（`af1943e5a`）：將配額分類新增測試移出受保護檔案 `.orchestrator/test_supervisor.py` 至獨立測試模組。
- PR `#1769`（`f0ecca014`）：修正正式環境 OIDC API Uptime Check 改採原生 `cloud_run_revision` 監控資源型別。
- PR `#1770`（`53e07cb0f`）：修正 `staging_retained_cleanup` 對前版未落盤復原包之清理相容性（已於 `45629190947` 實跑銷毀 `odp-e9ef7b5159b1`）。
- PR `#1763`（`48c5271b5`）：修正收件儲存失敗後重送測試之送出鎖解除等待時序。
- PR `#1772`（`8f6e3897e`）：更新 `core-ops` production 上線準備包至 2026-11-27 現況與負責人 A–H 方向決定。
- PR `#1771`（`25c9ab14b`）：將 production watch receipt 改為讀回真實 Cloud Run 監控指標驗證。
- PR `#1768`（`9de70f949`）：修正 Operator 錯誤狀態顯示與診斷操作。

| 檢查項目 | 實跑結果 |
|---|---|
| `push` CI（`45989131033`）與 `merge_group` CI（`45981989694`）於 `9de70f949` | 全部 12 個 job 直接在 `9de70f949` 本身完整實跑成功，包含全部 7 條產品線（`product-lint-unit`、`product-node`、`product-api-contract`、`product-db`、`product-e2e-gate`、`product-security`、`performance-gate`）及 `orchestrator`、`change-scope`、`freeze-policy`（無 scope 跳過） |
| 自動 `dev` 部署（`45996059587`，`phase=auto`）於 `9de70f949` | 成功；live E2E `83/83` 通過、0 blocker；preflight、migration 相容、Cloud Run smoke、worker/migration/scheduler job 驗證與 public-egress probe 全部通過；`dev` 現服務 `odp-9de70f949915` |
| `staging` 建置（`45995201655`，`phase=build`）於 `9de70f949` | 成功產出 canonical manifest `sha256:964d00090d8516b30d6329dcc5e0550cbab0342f9d32567b651003aa12ef0ccd`（`core-ops`、`initial_release_recovery=true`、16 個外部來源全關且無憑證）；production `npm audit` 0 個 finding；5 個 `stg-odp-9de70f949915-5c64b623-*` 目標資源讀回均不存在 |
| 前次演練與清理紀錄 | `8e5020a29bad` 已於 `45555693188` 通過 11 階段演練（現已逾 24h TTL，待本 PR 合併取代後清理）；`odp-e9ef7b5159b1` 已於 `45629190947` 完整銷毀（23/23 資源刪除讀回） |

逐 job 紀錄見 `candidate-ci-9de70f949.json`；GitHub Actions 原檔見 `hosted-artifacts/run-45996059587/` 與 `hosted-artifacts/run-45995201655/`。

## 人工決定與授權範圍

紀錄檔為 `humanops-staging-admission-authorization-20261127.json`（核准編號 `HUMANOPS-STAGING-ADMISSION-20261127W`）：

- 在 `staging build 45995201655`、`push` CI `45989131033`、`merge_group` CI `45981989694` 與 `dev auto 45996059587` 全部完成後，於 Antigravity 對話 `22399345-f387-4dca-9438-6dcf570c24ea` `step_index: 8256`（`2026-11-27T12:41:52Z`）向負責人蔡尚志逐項出示候選提交 `9de70f949915706d1bad1bc25c24b681eeb902e9`、manifest `sha256:964d00090d8516b30d6329dcc5e0550cbab0342f9d32567b651003aa12ef0ccd` 與四個元件雜湊、CI 與 `dev` 實機部署結果、三項 `gate-2` 偏差與複查日期 `2026-12-04`，以及單次 `staging` 清理與部署範圍；負責人於 `step_index: 8257`（`2026-11-27T12:52:49Z`）明確回覆原文：**「核准 HD-08w、HD-09w、代按本次 staging cleanup 與 staging deploy 環境核准，及補齊上述 2 項正式環境前置設定」**。
- **HD-08w**：`gate-2`（Data Gate）對本候選進 staging 處置為 `passed-with-deviation`（`review_by: 2026-12-04`；外部來源全關不適用、模型推論未啟用不適用、`check_live_production_data.py` 延後至 `staging verify` 與 `production` 閘門實跑）。
- **HD-09w**：`release.stage=dev-verified`、`admission_target=staging`、`decision=go`，僅授權本候選一次 staging release request、一張短期 Supervisor lease、一次 `Runtime Release` `phase=deploy`、一次已逾 TTL 舊演練環境 `odp-8e5020a29bad` 之 `phase=cleanup`，以及代按對應之 GitHub `staging` environment 核准（不含 production 部署）。

## 尚未在 Live Staging / Production 驗證的項目

- 候選 `9de70f949`（納入 `#1768`、`#1771`、`#1772`）之 11 階段 ephemeral staging 實機演練、復原包寫入讀回與 `staging-verified` 收據，將於本 PR 合併後的鏈 V `staging deploy` 實跑驗證。
- 已逾 24h TTL 之 `odp-8e5020a29bad` 將於本 PR 合併更新 `RELEASE_GATE_REGISTRY.json` 取代舊候選後，由 `Runtime Release` `phase=cleanup` 銷毀讀回。
- Production gates（`gate-3`、`gate-5`、`gate-6`）與正式環境部署仍保持阻擋，待鏈 V `staging deploy` 完成後另行簽認。

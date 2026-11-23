# ODP-STAGING-ADMISSION-E9EF7B515-001：鏈 T Ephemeral Staging Admission（`e9ef7b515` / `core-ops`）

本目錄保存候選提交 `e9ef7b5159b143c11af8b6260b645df31aecf981`（PR `#1741` 合併至 `dev`）進入 ephemeral staging（`release_profile=core-ops`）之查核證據與負責人授權紀錄。

## 候選版本範圍與前置查核

`e9ef7b5159b143c11af8b6260b645df31aecf981` 相較於前次 staging 候選 `49ef699a6c086f117463be5b73d0efc66d8ebb31`（PR `#1730`）納入：

- PR `#1736`（`0dd5a3b43d44`）：Supervisor frozen-path review 邊界修正。
- PR `#1739`（`91a2a87d2b41`）：共用 `.github/workflows/frozen-review-policy.yml` 取代重複內嵌 checkout 規則。
- PR `#1737`（`2d3fa970c159`）：ephemeral staging `staging_admin_bootstrap` Cloud SQL `/cloudsql` volume mount、72 小時 bootstrap TTL 單一來源讀回，以及密碼輪替／新密碼重登 E2E 驗證。
- PR `#1741`（`e9ef7b5159b1`）：core-ops production Audit WORM 根模組輸出（`audit_worm_sink_uri`、`audit_writer_service_account`、`audit_recovery_reader_service_account`）、結構化 egress probe 收據可觀測性，以及 `deploy_cloud_run_waji.sh` `set -u` 變數防護。

| 檢查項目 | 實跑結果 |
|---|---|
| `push` CI（`45080084852`） | 全部 job 成功；7 個 product lanes（`product-lint-unit`、`product-node`、`product-api-contract`、`product-db`、`product-e2e-gate`、`product-security`、`performance-gate`）全部實跑通過 |
| `merge_group` CI（`45074825592`） | 全部執行的 job 成功（僅 PR/merge_group 不執行的自動 dev release dispatch job 為 `skipped`）；7 個 product lanes 全部實跑通過 |
| 自動 `dev` 部署（`45085553758`，`phase=auto`） | 首次即成功；live E2E `83/83` 通過、0 blocker；preflight、migration 相容、Cloud Run smoke、worker/migration/scheduler job 驗證（含 immutable image binding）與 public-egress probe 全部通過；`dev` 現服務 `odp-e9ef7b5159b1` |
| `staging` 建置（`45084842707`，`phase=build`） | 成功產出 canonical manifest `sha256:b7ab5fbb0d9cbe08d063ea8044f89ac1b165dd329313e8d8938c3ae514f2161e`（`core-ops`、16 個外部來源全關且無憑證）；production `npm audit` 0 個 finding；5 個 `stg-odp-e9ef7b5159b1-bbd9e6c0-*` 目標資源讀回均不存在 |

逐 job 紀錄見 `candidate-ci-e9ef7b515.json`；GitHub Actions 原檔見 `hosted-artifacts/run-45085553758/` 與 `hosted-artifacts/run-45084842707/`。

## 人工決定與授權範圍

紀錄檔為 `humanops-staging-admission-authorization-20261123.json`（核准編號 `HUMANOPS-STAGING-ADMISSION-20261123T`）：

- 負責人蔡尚志於 Antigravity 對話 `2026-11-22T14:52Z`（「同意」）與 `2026-11-22T23:39:16Z`（「都做一做」）明確指示執行鏈 T ephemeral staging admission、Supervisor lease 發放與 staging 部署演練（production `terraform apply` 因涉及不可逆 7 年 Audit WORM lock，單獨保留待負責人確認後才執行）。
- **HD-08t**：`gate-2`（Data Gate）對本候選進 staging 處置為 `passed-with-deviation`（`review_by: 2026-11-30`）：外部來源 16 項全關且無憑證、core-ops 不啟用模型推論、`check_live_production_data.py` 留待 staging verify 與 production gates 實跑。
- **HD-09t**：`release.stage=dev-verified`、`admission_target=staging`、`decision=go`，僅授權本候選一次 staging release request、一張短期 Supervisor lease 與一次 `Runtime Release` `phase=deploy`（仍須 GitHub `staging` environment 核准，不含 production）。

## 尚未在 Live Staging 驗證的項目

- PR `#1737` 在真實 `console-staging.oday-plus.com.tw` 上的初始密碼輪替與新密碼重複登入旅程、以及 `staging_admin_bootstrap` `/cloudsql` socket 掛載，將於本 PR 合併後的鏈 T `staging deploy` 實跑驗證。
- PR `#1741` 的結構化 egress probe 收據將於鏈 T `staging deploy` 實跑驗證。
- 前次已逾 24h TTL 的 `49ef699a6c08` verified staging 資源清理（PR `#1742`）將於鏈 T `staging deploy` 完成後再合併執行，以確保本候選部署時 `e9ef7b5159b1..dev` 僅含 `docs/evidence/` 變更（`check_candidate_ancestry` 為空）。

## Gate 狀態摘要

- `gate-0`（Code Gate）：`passed`（綁定 `e9ef7b5159b143c11af8b6260b645df31aecf981`；`49ef699a6c08` 收據移入 `historical_receipts`）。
- `gate-2`（Data Gate）：`passed-with-deviation`（依 `HD-08t` 處置；`49ef699a6c08` 收據移入 `historical_receipts`）。
- `gate-1`、`gate-4`：維持 `blocked`（待 HD-07 處置；staging admission 不要求通過）。
- `gate-3`、`gate-5`、`gate-6`：維持 `blocked`（production admission 門檻，僅更新 `release_sha`）。

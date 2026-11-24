# ODP-STAGING-ADMISSION-8E5020A29-001：鏈 U Ephemeral Staging Admission（`8e5020a29` / `core-ops`）

本目錄保存候選提交 `8e5020a29bad92300cce2fe7262e496fb31370b5`（PR `#1761` 合併至 `dev`）進入 ephemeral staging（`release_profile=core-ops`）之查核證據與負責人授權紀錄。

## 候選版本範圍與前置查核

`8e5020a29bad92300cce2fe7262e496fb31370b5` 相較於前次 staging 候選 `e9ef7b5159b143c11af8b6260b645df31aecf981` 納入：

- PR `#1745`（`634409ba6a0f`）：修正 `persist_staging_recovery` `gcloud storage cp --no-clobber` 旗標，保留 generation 條件與 SHA-256 讀回驗證。
- PR `#1742`（`78ed4f915f61`）：延伸 `staging_retained_cleanup` 支援已逾 TTL 且已被取代之 verified 演練環境銷毀（已於 `45396929900` 實跑銷毀 `odp-49ef699a6c08`）。
- PR `#1750`（`e4c82d61b`）：發版快照 generation 綁定。
- PR `#1755`（`79e210559022`）、PR `#1758`（`42ddf2064`）、PR `#1759`（`ddb641827577`）、PR `#1752`（`7f1555bbd199`）：Operator 空門市交易範圍、通知抽屜焦點穩定性、Geo 證據綁定與 `product-db` 測試並行容量。
- PR `#1761`（`8e5020a29bad`）：Supervisor 配額分類器補強（僅變更 5 個 `.orchestrator/*` 檔案，產品樹與 `7f1555bbd199` 完全一致）。

| 檢查項目 | 實跑結果 |
|---|---|
| `push` CI（`45354646865`）與 `merge_group` CI（`45353137987`）於 `8e5020a29bad` | `orchestrator`、`change-scope`、`freeze-policy`、`product` 彙總全部成功；因 `7f1555bbd..8e5020a29bad` 僅改動 `.orchestrator/*`，7 個 product lanes 依 scope 跳過 |
| 相同產品樹 `7f1555bbd199` 之 `push` CI（`45353195393`）與 `merge_group` CI（`45347387596`） | 7 個 product lanes（`product-lint-unit`、`product-node`、`product-api-contract`、`product-db`、`product-e2e-gate`、`product-security`、`performance-gate`）全部實跑通過 |
| 自動 `dev` 部署（`45356221614`，`phase=auto`）於 `8e5020a29bad` | 成功；live E2E `83/83` 通過、0 blocker；preflight、migration 相容、Cloud Run smoke、worker/migration/scheduler job 驗證與 public-egress probe 全部通過；`dev` 現服務 `odp-8e5020a29bad` |
| `staging` 建置（`45396936799`，`phase=build`）於 `8e5020a29bad` | 成功產出 canonical manifest `sha256:4ccc2c6e30497250baeab7e88cc9873bb2b057e859a9f86aae5f09c8bcdb25e9`（`core-ops`、16 個外部來源全關且無憑證）；production `npm audit` 0 個 finding；5 個 `stg-odp-8e5020a29bad-*` 目標資源讀回均不存在 |
| 舊演練環境清理（`45396929900`，`phase=cleanup`） | 已成功銷毀 `odp-49ef699a6c08`（`stg_odp_49ef699a6c08_a4eeaa0e` 全部資源刪除讀回） |

逐 job 紀錄見 `candidate-ci-8e5020a29.json`；GitHub Actions 原檔見 `hosted-artifacts/run-45356221614/` 與 `hosted-artifacts/run-45396936799/`。

## 人工決定與授權範圍

紀錄檔為 `humanops-staging-admission-authorization-20261124.json`（核准編號 `HUMANOPS-STAGING-ADMISSION-20261124U`）：

- 負責人蔡尚志於 Antigravity 對話明確指示完成舊環境清理與新一輪 ephemeral staging 全套演練（「同意」、「都做一做」、「繼續做完阿」、「你把它處理好阿」、「繼續處理」），production `terraform apply` 仍保留待確認後執行。
- **HD-08u**：`gate-2`（Data Gate）對本候選進 staging 處置為 `passed-with-deviation`（`review_by: 2026-11-30`）。
- **HD-09u**：`release.stage=dev-verified`、`admission_target=staging`、`decision=go`，僅授權本候選一次 staging release request、一張短期 Supervisor lease 與一次 `Runtime Release` `phase=deploy`（仍須 GitHub `staging` environment 核准，不含 production）。

## 尚未在 Live Staging 驗證的項目

- PR `#1745` 修正後的 `staging-recovery-create.json` 與 `staging-recovery-final.json` 寫入及讀回驗證，將於本 PR 合併後的鏈 U `staging deploy` 實跑驗證。
- `console-staging.oday-plus.com.tw` 初始密碼輪替與新密碼重登 E2E、10 階段 rehearsal verification 與結構化 egress probe 收據，將於鏈 U `staging deploy` 實跑驗證。
- 前次因 `persist-recovery` 失敗而未寫入 GCS recovery bundle 的 `odp-e9ef7b5159b1` 殘留命名空間資源不影響 `odp-8e5020a29bad` 獨立命名空間演練，後續另依 GCP 認證清理其獨立 Terraform state。

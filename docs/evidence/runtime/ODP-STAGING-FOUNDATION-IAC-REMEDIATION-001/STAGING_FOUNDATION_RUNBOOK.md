# Ephemeral Staging Foundation 營運 Runbook

本文件提供 ODay Plus Ephemeral Staging 底層基礎設施 (Foundation) 與受治理 State Backend 的部署、初始化、日常維運及生命週期對接指引。

---

## 1. 架構分層與責任界線

依據《ODay Plus 全系統部署與短生命週期 Staging 規劃》(Rollout Plan §4.2)，Staging 環境分為兩層：

### 1.1 長期共用底層 (Long-Lived Foundation)
- **GCP 專案**：`odayplus-runtime-20260825`（live readback project number `767864276141`）
- **VPC 網路與子網路**：`oday-staging-runtime` (CIDR: `10.42.0.0/24`)
- **服務連接點**：Private Service Access (`servicenetworking.googleapis.com`)
- **出口防護**：Fail-Closed Firewall (Default-Deny `0.0.0.0/0`，僅允許 RFC1918 與 Restricted Google APIs)
- **CMEK 金鑰**：`oday-staging-runtime` (KeyRing: `oday-staging-runtime`，90天自動輪換，`prevent_destroy = true`)
- **Cloud SQL 執行個體**：`oday-staging-foundation-sql` (PostgreSQL 16, Private IP Only, CMEK 加密, 啟用 PITR)。既有 `oday-staging-sql` 掛在 default VPC 且 CMEK/private network 不可 in-place 變更，保留為 legacy，不得在本任務刪除。
- **State Backend**：`oday-tfstate-staging-${PROJECT_ID}` (CMEK 加密, 版本控制, 保留政策)；只允許 Terraform state/lock 物件，不是一般 artifact 儲存區。
- **Deployer 權限**：`github-deployer@${PROJECT_ID}.iam.gserviceaccount.com` (經由 Workload Identity Federation 認證)；bucket-scoped `roles/storage.objectUser` 已由 live IAM readback 與 GitHub Actions run `33320822376` 的兩個 remote-state object readback 驗證。
- **Staging runtime identities**：`oday-staging-runtime@${PROJECT_ID}.iam.gserviceaccount.com` 與 `oday-staging-web@${PROJECT_ID}.iam.gserviceaccount.com`；兩者由 Terraform resource email 接合 subnet IAM。

### 1.2 短生命週期發布資源 (Ephemeral Staging Per-Release)
- 由 `infra/terraform/modules/ephemeral_staging` 與 `product_ops/deployment/staging_lifecycle.py` 動態建立與銷毀。
- 包含：Release 專屬資料庫與使用者、專屬 GCS 資料貯體、專屬 Cloud Run 服務、Pub/Sub Topics、Paused Cloud Scheduler。

---

## 2. 一次性 Bootstrap 與 Foundation 初始化程序

### 步驟 2.1：建立受治理 State Backend

```bash
cd infra/terraform/bootstrap

# `bootstrap.sh` 會執行兩階段流程：Phase 1 使用暫存的 backend-less copy，
# Phase 2 用 main.tf 的唯一 gcs backend 以 -migrate-state 遷移。
./infra/terraform/bootstrap/bootstrap.sh /secure/path/staging.tfvars
```

### 步驟 2.2：部署 Staging Foundation 底層資源

使用受治理的 `modules/runtime_foundation`：

```bash
# backend bucket 必須來自 bootstrap 的 live readback，不可填 placeholder。
cp /secure/path/live/staging_foundation.backend.hcl /secure/path/staging_foundation.backend.hcl

# foundation tfvars 由 release packet 產生，至少包含 exact release SHA、immutable
# images、`cloud_sql_instance_name = "oday-staging-foundation-sql"` 與 live bucket ref。
```

---

## 3. Ephemeral Staging 生命週期參數對接

當執行 `staging_lifecycle.py create` 或 GitHub Actions Runtime Release 時，直接傳入由 Foundation 產生的唯讀資源參照：

| Ephemeral Staging 參數名稱 | 來源 Foundation 輸出 / 資源名稱 | 範例值 |
|---|---|---|
| `--project-id` | `var.project_id` | `odayplus-runtime-20260825` |
| `--region` | `var.region` | `asia-east1` |
| `--network-name` | `module.runtime_foundation.network_name` | `oday-staging-runtime` |
| `--subnetwork-name` | `module.runtime_foundation.subnetwork_name` | `oday-staging-runtime` |
| `--cloud-sql-instance-name` | `module.runtime_foundation.cloud_sql_instance_name` | `oday-staging-foundation-sql` |
| `--cloud-sql-connection-name` | `module.runtime_foundation.cloud_sql_instance_connection_name` | `odayplus-runtime-20260825:asia-east1:oday-staging-foundation-sql` |
| `--kms-key-id` | `module.runtime_foundation.kms_crypto_key_id` | `projects/odayplus-runtime-20260825/locations/asia-east1/keyRings/oday-staging-runtime/cryptoKeys/oday-staging-runtime` |
| `--deployer-service-account` | `google_service_account.github_deployer.email` | `github-deployer@odayplus-runtime-20260825.iam.gserviceaccount.com` |

---

## 4. 驗證與健康檢查

1. **網路出口防護驗證**：
   - Root Terraform 的 Cloud Run API/Web contract 設定 `egress = "ALL_TRAFFIC"`；foundation live readback 目前只確認既有 MLflow 為 `PRIVATE_RANGES_ONLY`，不得把它當成 API/Web 的 ALL_TRAFFIC 證據。
   - 驗證防火牆規則 `oday-staging-deny-all-egress` 優先級為 `65534`，阻絕所有對外流量。
   - 驗證 `oday-staging-allow-private-egress` 與 `oday-staging-allow-restricted-google-apis` 限制在受保護範圍。
2. **CMEK 靜態加密驗證**：
   - 檢查 Cloud SQL 執行個體 `encryption_key_name` 指向 KMS Key。
   - 檢查 State Bucket 及 Ephemeral Storage Buckets 皆設定 `default_kms_key_name`。
3. **無憑證與無機密暴露驗證**：
   - 執行 `python3 infra/terraform/validate_contract.py`，確認無任何 plaintext secret 或 password 輸出。
   - 只有 `gcloud`/Terraform readback 已確認存在且在 remote state 中收斂之資源，才可在 receipt 標記為 live/converged；本次 receipt 對 API/Web 保留 pending，僅記錄 Terraform Direct VPC ALL_TRAFFIC contract。
4. **State Backend 安全隔離與 IAM**：
   - State bucket 只接受 Terraform state/lock 物件；binary plan、一般 release artifact 與任何 `plans/` 上傳一律禁止。Plan 的 digest、generation 與 action summary 寫入 receipt 即可，不得以 state bucket 保存 plan。
   - 若發現 plan 已誤上傳，立即將物件標記為安全隔離事件，記錄 CMEK、object generation、retention expiration 與 expiry-cleanup owner；不得提前刪除、不得解除 retention、不得把事件標成完成。
   - bucket policy 必須維持 admin 的 bucket-scoped `roles/storage.admin` 與 WIF deployer 的 `roles/storage.objectUser`；不得為了 readback 把 project-wide `roles/storage.admin` 授給 deployer。
   - 由 admin 執行 bucket metadata/IAM readback，再由 WIF deployer 讀取 bootstrap 與 foundation state object metadata；兩段都成功才可標記 least-privilege IAM `VERIFIED`。
   - 2026-08-30 驗證證據為 GitHub Actions run `33320822376`；正常 build、admission 與 deploy jobs 均 skipped。

## 5. Migration、Rollback 與 Destroy Guard

1. Root foundation 的 17 個 legacy resource address（含兩個 subnet IAM
   identity）以 `moved` blocks 映射到 module；本次既有專用 SQL 以
   `terraform import module.runtime_foundation.google_sql_database_instance.primary`
   採用進 governed state，最終 convergence plan 的 replacement、destroy 均為 `0`。
2. 若 live 有不相容的 legacy SQL，保留 `oday-staging-sql`，只對新 foundation
   instance `oday-staging-foundation-sql` 做 state adoption；不得修改或刪除
   legacy instance，也不得使用 `terraform destroy` 取代 migration。
3. Apply 前只在受控執行範圍短暫產生 binary plan，記錄其 SHA256、candidate SHA、
   backend prefix、state generation 與 action summary；apply 只接受同一 controlled
   plan file，完成後不把 binary plan 上傳 state bucket。失敗時保留 state、
   operation 與 evidence，不解除 KMS/State Bucket 的 `prevent_destroy`。
4. State bucket 與 runtime KMS 永久設定 `prevent_destroy = true`、bucket
   `force_destroy = false`；任何 destroy plan 必須停止並由 owner/reviewer 明確
   處理。Ephemeral release 的 cleanup 只能使用 release label/prefix 精確清理。

### 5.1 State Bucket 安全隔離事件（目前不得結案）

本次 readback 發現一個 binary plan 曾被誤置於 state bucket 的
`oday-plus/staging/plans/` 路徑（object generation
`1787822664931431`）。這不是合法的 state object，也不是可宣稱完成的
plan 保存物件；事件詳見 `STATE_BUCKET_SECURITY_QUARANTINE.md` 與
`live-apply-plan-receipt.json` 的 `security_quarantine_incident`。

- CMEK：`projects/odayplus-runtime-20260825/locations/asia-east1/keyRings/oday-tfstate-staging-state/cryptoKeys/oday-tfstate-staging-state`
- retention expiration：`2026-09-26T09:24:24Z`
- no early deletion：`true`；在 expiration 前不得刪除或放寬 retention。
- expiry-cleanup owner：`Staging Foundation Owner`
- receipt/runbook completion claim：`WITHHELD`；state bucket plan upload policy：`PROHIBITED`。

完整雜湊、object generation、state serial、IAM verification 與 live readback 見同目錄
的兩份 JSON receipt。

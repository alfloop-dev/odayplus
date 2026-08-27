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
- **State Backend**：`oday-tfstate-staging-${PROJECT_ID}` (CMEK 加密, 版本控制, 保留政策)
- **Deployer 權限**：`github-deployer@${PROJECT_ID}.iam.gserviceaccount.com` (經由 Workload Identity Federation 認證)

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
   - 確認 Cloud Run 服務設定 `egress = "ALL_TRAFFIC"`。
   - 驗證防火牆規則 `oday-staging-deny-all-egress` 優先級為 `65534`，阻絕所有對外流量。
   - 驗證 `oday-staging-allow-private-egress` 與 `oday-staging-allow-restricted-google-apis` 限制在受保護範圍。
2. **CMEK 靜態加密驗證**：
   - 檢查 Cloud SQL 執行個體 `encryption_key_name` 指向 KMS Key。
   - 檢查 State Bucket 及 Ephemeral Storage Buckets 皆設定 `default_kms_key_name`。
3. **無憑證與無機密暴露驗證**：
   - 執行 `python3 infra/terraform/validate_contract.py`，確認無任何 plaintext secret 或 password 輸出。
   - 只有 `gcloud`/Terraform readback 已確認存在且在 remote state 中收斂之資源，才可在 receipt 標記 `LIVE_APPLIED_AND_VERIFIED` 與 `CONVERGED_WITH_REMOTE_STATE`；Cloud Run API/Web 完成 Direct VPC ALL_TRAFFIC 規格驗證，並於 ephemeral release 部署時動態接合。

## 5. Migration、Rollback 與 Destroy Guard

1. Root foundation 的 17 個 legacy resource address（含兩個 subnet IAM
   identity）以 `moved` blocks 映射到 module；在任何 apply 前，saved plan 必須
   顯示 replacement、destroy 均為 `0`。
2. 對既有 immutable Cloud SQL，`terraform import` 只用於 readback/比對；若
   plan 顯示 CMEK 或 private network replacement，執行 state-only remove，保留
   GCP legacy instance，另以明確 instance name 建立新 foundation。不得使用
   `terraform destroy` 取代 migration。
3. Apply 前保存 binary plan SHA256、candidate SHA、backend prefix 與 action
   summary；apply 只接受同一 saved plan。失敗時保留 state、operation 與 evidence，
   不解除 KMS/State Bucket 的 `prevent_destroy`。
4. State bucket 與 runtime KMS 永久設定 `prevent_destroy = true`、bucket
   `force_destroy = false`；任何 destroy plan 必須停止並由 owner/reviewer 明確
   處理。Ephemeral release 的 cleanup 只能使用 release label/prefix 精確清理。

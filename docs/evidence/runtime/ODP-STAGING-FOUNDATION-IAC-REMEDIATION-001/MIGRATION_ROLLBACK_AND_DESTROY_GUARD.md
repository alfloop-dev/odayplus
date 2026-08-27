# Terraform Foundation 抽取與 Zero-Replacement 狀態遷移指引

本文件詳細說明將 Root Terraform 基礎設施抽取至 `modules/runtime_foundation` 的遷移機制、安全防護 (Destroy Guard) 以及緊急回滾 (Rollback) 程序。

---

## 1. 狀態遷移機制 (State Migration via Moved Blocks)

Terraform 1.1+ 支援原生 `moved` blocks。當配置結構重構時，`moved` blocks 能在 `terraform plan` 階段將既有狀態位址 (State Address) 映射至新的模組位址，而**不會產生任何摧毀 (Destroy) 或重建 (Recreation)**。

### 1.1 遷移對照表

| 既有 Root 資源位址 | 遷移後模組位址 | 資源型態 | 變更操作 |
|---|---|---|---|
| `google_compute_network.runtime` | `module.runtime_foundation.google_compute_network.runtime` | VPC 網路 | 無 (In-place Rename) |
| `google_compute_subnetwork.runtime` | `module.runtime_foundation.google_compute_subnetwork.runtime` | 子網路 | 無 (In-place Rename) |
| `google_compute_global_address.private_services` | `module.runtime_foundation.google_compute_global_address.private_services` | PSA 位址 | 無 (In-place Rename) |
| `google_service_networking_connection.private_services` | `module.runtime_foundation.google_service_networking_connection.private_services` | 對等連線 | 無 (In-place Rename) |
| `google_compute_firewall.deny_all_egress` | `module.runtime_foundation.google_compute_firewall.deny_all_egress` | 出口防火牆 | 無 (In-place Rename) |
| `google_compute_firewall.allow_private_egress` | `module.runtime_foundation.google_compute_firewall.allow_private_egress` | 出口防火牆 | 無 (In-place Rename) |
| `google_compute_firewall.allow_restricted_google_apis` | `module.runtime_foundation.google_compute_firewall.allow_restricted_google_apis` | 出口防火牆 | 無 (In-place Rename) |
| `google_kms_key_ring.runtime` | `module.runtime_foundation.google_kms_key_ring.runtime` | KMS KeyRing | 無 (In-place Rename) |
| `google_kms_crypto_key.runtime` | `module.runtime_foundation.google_kms_crypto_key.runtime` | KMS Key | 無 (In-place Rename) |
| `google_project_service_identity.cloud_sql` | `module.runtime_foundation.google_project_service_identity.cloud_sql` | 服務代理 | 無 (In-place Rename) |
| `google_project_service_identity.pubsub` | `module.runtime_foundation.google_project_service_identity.pubsub` | 服務代理 | 無 (In-place Rename) |
| `google_kms_crypto_key_iam_member.cloud_sql` | `module.runtime_foundation.google_kms_crypto_key_iam_member.cloud_sql` | KMS IAM | 無 (In-place Rename) |
| `google_kms_crypto_key_iam_member.gcs` | `module.runtime_foundation.google_kms_crypto_key_iam_member.gcs` | KMS IAM | 無 (In-place Rename) |
| `google_kms_crypto_key_iam_member.pubsub` | `module.runtime_foundation.google_kms_crypto_key_iam_member.pubsub` | KMS IAM | 無 (In-place Rename) |
| `google_sql_database_instance.primary` | `module.runtime_foundation.google_sql_database_instance.primary` | Cloud SQL | 無 (In-place Rename) |

---

## 2. 銷毀防護策略 (Destroy Guards)

為杜絕任何誤刪已部署資源的風險，本架構部署了三層安全防護：

1. **KMS 金鑰防護**：
   - `google_kms_crypto_key.runtime` 設有 `lifecycle { prevent_destroy = true }`。
   - 任何嘗試摧毀該金鑰的 Terraform 操作皆會被本機引擎直接阻斷。
2. **Cloud SQL 刪除防護**：
   - `google_sql_database_instance.primary` 設定 `deletion_protection = local.is_prod` (可在 non-prod 顯式控制)，防止資料庫被誤刪。
3. **Precondition 嚴格防護**：
   - `terraform_data.production_contract` 生命週期先驗條件檢查，不容許任何破壞性降級設定。

---

## 3. 回滾程序 (Rollback Procedures)

若在執行 Apply 前或執行中發生任何異常，依循以下指引：

1. **未 Apply 階段**：
   - 直接捨棄生成的 plan 檔案，原環境狀態未受任何變更。
2. **已 Apply 但需回退程式碼**：
   - 若需切換回舊版 Terraform 定義，可透過 `moved` block 反向聲明 (`from = module.runtime_foundation.X to = X`) 或直接以 `terraform state mv` 完成無縫狀態還原。
   - 由於資源本身未被重建，底層資料庫、網路與金鑰連線維持 100% 正常。

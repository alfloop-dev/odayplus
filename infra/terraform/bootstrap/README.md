# ODay Plus Terraform State Backend Bootstrap

此模組負責建立與維護受治理的 Google Cloud Storage (GCS) Terraform Remote State Backend，遵循 ODay Plus 部署架構規範與安全合規政策。

## 治理與安全特性

1. **客戶自管加密金鑰 (CMEK)**：專用 Cloud KMS KeyRing 與 CryptoKey (`7776000s` 自動輪換，`prevent_destroy = true`)，所有 state 物件於靜態保存時全面加密。
2. **物件版本控制 (Object Versioning)**：啟用版本控制，防止 state 意外覆寫與狀態遺失。
3. **保留政策 (Retention Policy)**：依環境設定不可篡改或法定保留期，生產環境啟用鎖定保留政策 (`is_locked = true`)。
4. **強制防止公開存取 (Public Access Prevention)**：設定為 `enforced`，阻斷所有公開存取途徑。
5. **統一貯體層級存取 (Uniform Bucket-Level Access)**：強制統一 IAM 授權，禁止物件層級 ACL 洩漏。
6. **最小權限 (Least Privilege IAM)**：僅授權專屬 CI/CD Deployer (`roles/storage.objectUser`)，禁止廣泛讀寫。
7. **發布隔離 State Prefix**：
   - Root Dev: `prefix = "oday-plus/dev"`
   - Root Prod: `prefix = "oday-plus/prod"`
   - Ephemeral Staging Release: `prefix = "oday-plus/staging/releases/{release_id}"`

## 初始化與執行指引

```bash
# 1. 建立 State Backend
terraform -chdir=infra/terraform/bootstrap init
terraform -chdir=infra/terraform/bootstrap plan -var-file=bootstrap.tfvars
terraform -chdir=infra/terraform/bootstrap apply -var-file=bootstrap.tfvars

# 2. 產生的 Backend 設定範例 (用於 root 或 staging):
# bucket = "oday-tfstate-staging-123456"
# prefix = "oday-plus/staging"
```

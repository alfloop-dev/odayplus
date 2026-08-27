# ODay Plus Terraform State Backend Bootstrap

此模組負責建立與維護受治理的 Google Cloud Storage (GCS) Terraform Remote State Backend，遵循 ODay Plus 部署架構規範與安全合規政策。

## 治理與安全特性

1. **客戶自管加密金鑰 (CMEK)**：專用 Cloud KMS KeyRing 與 CryptoKey (`7776000s` 自動輪換，`prevent_destroy = true`)，所有 state 物件於靜態保存時全面加密。
2. **物件版本控制 (Object Versioning)**：啟用版本控制，防止 state 意外覆寫與狀態遺失。
3. **保留政策 (Retention Policy)**：依環境設定不可篡改或法定保留期，生產環境啟用鎖定保留政策 (`is_locked = true`)。
4. **強制防止公開存取 (Public Access Prevention)**：設定為 `enforced`，阻斷所有公開存取途徑。
5. **統一貯體層級存取 (Uniform Bucket-Level Access)**：強制統一 IAM 授權，禁止物件層級 ACL 洩漏。
6. **銷毀防護 (Destroy Guard)**：State Bucket 屬長期控制面資源，所有環境（包含 dev 與 staging）一律設定 `force_destroy = false` 與 `lifecycle { prevent_destroy = true }`，嚴禁意外或連帶刪除歷史 state。
7. **最小權限 (Least Privilege IAM)**：僅授權專屬 CI/CD Deployer (`roles/storage.objectUser`)，禁止廣泛讀寫。
8. **發布隔離 State Prefix**：
   - Root Dev: `prefix = "oday-plus/dev"`
   - Root Prod: `prefix = "oday-plus/prod"`
   - Ephemeral Staging Release: `prefix = "oday-plus/staging/releases/{release_id}"`

## 兩階段 Bootstrap 執行指引 (解決 Chicken-and-Egg 問題)

當遠端 GCS State Bucket 尚未存在時，直接執行 `terraform init` 會因無法連線 backend 而失敗。因此採用嚴格、可重複執行的**兩階段 Bootstrap 程序**。`main.tf` 保留唯一的 `backend "gcs" {}` 宣告；腳本在 Phase 1 只建立同一份設定的暫存 backend-less copy，避免 Terraform 在 bucket 尚未存在時初始化遠端 backend。Phase 2 再以 canonical 設定執行真正的 `-migrate-state`。

### 方法 A：自動化腳本執行

```bash
chmod +x infra/terraform/bootstrap/bootstrap.sh
./infra/terraform/bootstrap/bootstrap.sh infra/terraform/bootstrap/staging.tfvars
```

腳本會在受控暫存目錄複製 `.tf` 與 provider lockfile，僅從 copy 移除
backend 宣告；Phase 1 apply 完成後，才將該 local state 交給 canonical
`main.tf` 遷移到 `oday-plus/bootstrap`。遷移成功後只刪除暫存與 local state
檔案，GCS object 才是 durable source of truth。

### 方法 B：標準 CLI 分步執行

```bash
# 若需人工拆步，請先建立只存在於受控暫存目錄的 backend-less copy，並將
# 原始 terraform.tfstate 複製到該目錄；不可直接從含 backend 宣告的目錄 plan。
PHASE1_DIR=$(mktemp -d)
cp infra/terraform/bootstrap/*.tf infra/terraform/bootstrap/.terraform.lock.hcl "$PHASE1_DIR/"
sed -i '/^[[:space:]]*backend "gcs" {}/d' "$PHASE1_DIR/main.tf"
terraform -chdir="$PHASE1_DIR" init -backend=false -reconfigure -input=false
terraform -chdir="$PHASE1_DIR" plan -input=false -var-file=/secure/path/staging.tfvars -out="$PHASE1_DIR/bootstrap.tfplan"
terraform -chdir="$PHASE1_DIR" apply -input=false "$PHASE1_DIR/bootstrap.tfplan"

# 讀取剛建立的 bucket 名稱，並把 Phase 1 state 交給 canonical config。
BUCKET_NAME=$(terraform -chdir="$PHASE1_DIR" output -raw state_bucket_name)
cp "$PHASE1_DIR/terraform.tfstate" infra/terraform/bootstrap/terraform.tfstate

# 階段 2：將本機狀態無縫遷移 (-migrate-state) 至新建立的受治理 GCS Bucket
terraform -chdir=infra/terraform/bootstrap init -input=false -migrate-state \
  -backend-config="bucket=${BUCKET_NAME}" \
  -backend-config="prefix=oday-plus/bootstrap" -force-copy
rm -rf "$PHASE1_DIR" infra/terraform/bootstrap/terraform.tfstate \
  infra/terraform/bootstrap/terraform.tfstate.backup
```

完成後，該 Bucket 即可供 Root Terraform 與 Ephemeral Staging 安全使用。

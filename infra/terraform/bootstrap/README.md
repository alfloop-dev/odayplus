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

### 失敗與取消恢復

腳本收到 `SIGINT`／`SIGTERM` 時會將訊號傳給目前 Terraform 的整個
process group（包含 provider 子程序），等待它退出並完成 state 寫入，
再以 `130`／`143` 結束。不會繼續下一個 plan、apply、output 或 migration。
一般命令失敗則保留該命令的非零 exit code。

Phase 1 失敗時，已產生的 `terraform.tfstate` 與 `.backup` 會複製回
bootstrap 目錄，原暫存目錄也保留。若複製失敗，錯誤訊息會指出原檔位置，
不能把複製失敗當成已保存。Phase 2 開始後，canonical bootstrap 目錄可能
已經有較新的 migration state；此時保留該檔及 Phase 1 原副本，不以舊副本
覆蓋它。先檢查 state、backend 初始化結果及實際資源，再依狀態恢復，
避免在沒有 state 的情況重跑 apply。這些檔案可能含敏感資料，應維持受控
存取，不上傳一般 artifacts 或 Git。

只有 remote migration 命令成功後才清除 local state 與暫存目錄。
Root Terraform 的 backend 片段取自 `backend_config_hcl_example`，
依 `var.environment` 輸出 dev／staging／prod prefix；output 失敗會停止，
不套用預設 staging 值。

離線回歸測試會執行真正的 Bash 腳本，以 Terraform stub 覆蓋 shell-only
及 process-group 取消、provider 結束、state flush、部分 apply／migration
失敗、相對路徑與三種環境，測試不呼叫 GCP：

```bash
uv run pytest -q infra/terraform/tests/test_bootstrap.py
```

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

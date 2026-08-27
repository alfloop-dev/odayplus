# ODay Plus Staging Foundation Bootstrap & Governed State Guide

本指南詳細說明 ODay Plus Staging 專案底層基礎設施（Foundation）與受治理 Remote State Backend 的兩階段初始化、安全控制與日常對接程序。

---

## 1. 核心設計原則

1. **單一權威模組架構**：所有 VPC、子網路、出向防火牆、CMEK Key 及 Private Cloud SQL 皆由 `infra/terraform/modules/runtime_foundation` 統一管理，禁止在 staging 維護分叉的 IaC 定義。
2. **兩階段 Bootstrap（解除 Chicken-and-Egg）**：
   - 階段 1：使用本機暫態 (`-backend=false`) 建立 CMEK 與 GCS State Bucket。
   - 階段 2：透過 `-migrate-state` 將 bootstrap state 遷移進新建立的遠端 GCS Bucket。
3. **長期控制面銷毀防護 (Destroy Guard)**：
   - State Bucket 強制設定 `force_destroy = false` 與 `prevent_destroy = true`。
   - KMS CryptoKey 設定 `rotation_period = "7776000s"` 與 `prevent_destroy = true`。
4. **預設拒絕出向流量 (Default-Deny Egress)**：
   - VPC 出口防火牆優先級 `65534` 阻斷全部對外連線 (`0.0.0.0/0`)。
   - 僅放行 RFC1918 私有網段與受限 Google API (`199.36.153.4/30`, `199.36.153.8/30`)。
   - Cloud Run 服務設定 `egress = "ALL_TRAFFIC"` 確保流量全數經過受控 VPC。

---

## 2. 兩階段 State Backend 初始化步驟

```bash
# 準備 staging tfvars
cat <<EOF > infra/terraform/bootstrap/staging.tfvars
project_id             = "odayplus-runtime-20260825"
region                 = "asia-east1"
environment            = "staging"
retention_period_days  = 30
deployer_member_emails = [
  "serviceAccount:github-deployer@odayplus-runtime-20260825.iam.gserviceaccount.com"
]
EOF

# 執行自動化兩階段 bootstrap
./infra/terraform/bootstrap/bootstrap.sh infra/terraform/bootstrap/staging.tfvars
```

---

## 3. Staging Foundation 部署與參數對接

```bash
# 初始化 runtime_foundation (指向剛剛建立的 GCS Bucket)
cat <<EOF > staging_foundation.backend.hcl
bucket = "oday-tfstate-staging-odayplus-runtime-20260825"
prefix = "oday-plus/staging/foundation"
EOF

terraform -chdir=infra/terraform init -backend-config=staging_foundation.backend.hcl
terraform -chdir=infra/terraform plan -var-file=infra/terraform/env/staging.tfvars
```

### 對接 Ephemeral Staging Lifecycle

產生的基礎設施 Output 直接作為 Ephemeral Staging 指令參數：
- `--network-name`: `oday-staging-runtime`
- `--subnetwork-name`: `oday-staging-runtime`
- `--cloud-sql-instance-name`: `oday-staging-sql`
- `--cloud-sql-connection-name`: `odayplus-runtime-20260825:asia-east1:oday-staging-sql`
- `--kms-key-id`: `projects/odayplus-runtime-20260825/locations/asia-east1/keyRings/oday-staging-runtime/cryptoKeys/oday-staging-runtime`
- `--deployer-service-account`: `github-deployer@odayplus-runtime-20260825.iam.gserviceaccount.com`

---

## 4. 安全與合規檢核清單

- [x] GCS State Bucket 啟用 CMEK 靜態加密
- [x] GCS State Bucket 啟用 Object Versioning
- [x] GCS State Bucket 啟用 Uniform Bucket-Level Access 與 Public Access Prevention (enforced)
- [x] State Bucket 及 KMS Key 具備 `prevent_destroy = true` 銷毀防護
- [x] Cloud Run 採 Direct VPC `ALL_TRAFFIC` 導流
- [x] VPC 無 Cloud NAT，出口防火牆為 default-deny
- [x] 絕不向外部或一般 artifact 上傳 Terraform state
- [x] 輸出變數絕無任何 plaintext 密碼或敏感金鑰

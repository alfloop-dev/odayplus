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
   - Root Cloud Run API/Web contract 設定 `egress = "ALL_TRAFFIC"` 確保發布時流量全數經過受控 VPC；foundation readback 不得把尚未部署的 API/Web 標為 live。

---

## 2. 兩階段 State Backend 初始化步驟

```bash
# 準備 staging tfvars（檔案只放在受控暫存路徑，不提交 repository）
./infra/terraform/bootstrap/bootstrap.sh /secure/path/staging.tfvars
```

`bootstrap/main.tf` 保留唯一 `backend "gcs" {}` 宣告。腳本 Phase 1 會在
暫存目錄以 `-backend=false` 執行同一份 resource graph；bucket 建立後，Phase
2 才由 canonical config 以 `-migrate-state` 遷移到 `oday-plus/bootstrap`。
成功後 local state 與暫存目錄會清除，不以 runner 暫存檔冒充 durable state。

---

## 3. Staging Foundation 部署與參數對接

```bash
# backend 設定必須由 live bootstrap readback 取得，不能填 placeholder
cat >/secure/path/staging_foundation.backend.hcl <<'EOF'
bucket = "oday-tfstate-staging-odayplus-runtime-20260825"
prefix = "oday-plus/staging/foundation"
EOF

terraform -chdir=infra/terraform init \
  -backend-config=/secure/path/staging_foundation.backend.hcl
terraform -chdir=infra/terraform plan \
  -var-file=/secure/path/staging_foundation.tfvars \
  -out=/secure/path/staging_foundation.tfplan
terraform -chdir=infra/terraform apply /secure/path/staging_foundation.tfplan
```

`staging_foundation.tfvars` 必須包含 release packet 提供的 exact SHA 與 immutable
image digest；不可使用 placeholder。若 live
已有同名但不相容的 legacy Cloud SQL，先保留該 instance，改用新的明確
`cloud_sql_instance_name`，並在 migration receipt 記錄 state-only import/remove
與 zero-replacement plan。

本次 staging live foundation 的實際 identity 由 Terraform 建立或採用：
`oday-staging-runtime@odayplus-runtime-20260825.iam.gserviceaccount.com` 與
`oday-staging-web@odayplus-runtime-20260825.iam.gserviceaccount.com` 均以
Terraform resource email 接合 subnet `roles/compute.networkUser`；不得以
手寫 email 或不存在的 smoke/operator principal 取代。

### 對接 Ephemeral Staging Lifecycle

產生的基礎設施 Output 直接作為 Ephemeral Staging 指令參數：
- `--network-name`: `oday-staging-runtime`
- `--subnetwork-name`: `oday-staging-runtime`
- `--cloud-sql-instance-name`: `oday-staging-foundation-sql`
- `--cloud-sql-connection-name`: `odayplus-runtime-20260825:asia-east1:oday-staging-foundation-sql`
- `--kms-key-id`: `projects/odayplus-runtime-20260825/locations/asia-east1/keyRings/oday-staging-runtime/cryptoKeys/oday-staging-runtime`
- `--deployer-service-account`: `github-deployer@odayplus-runtime-20260825.iam.gserviceaccount.com`

---

## 4. 安全與合規檢核清單

- [x] GCS State Bucket 啟用 CMEK 靜態加密
- [x] GCS State Bucket 啟用 Object Versioning
- [x] GCS State Bucket 啟用 Uniform Bucket-Level Access 與 Public Access Prevention (enforced)
- [x] State Bucket 及 KMS Key 具備 `prevent_destroy = true` 銷毀防護
- [ ] Cloud Run API/Web 採 Direct VPC `ALL_TRAFFIC` 導流（Terraform contract 已確認；API/Web 尚待 ephemeral release live readback；既有 MLflow connector 的 `PRIVATE_RANGES_ONLY` 不算本項證據）
- [x] VPC 無 Cloud NAT，出口防火牆為 default-deny（以 live gcloud readback 驗證）
- [x] 絕不向外部或一般 artifact 上傳 Terraform state（state prefix 與 release prefix 分離）
- [x] 輸出變數絕無任何 plaintext 密碼或敏感金鑰（contract test 通過）

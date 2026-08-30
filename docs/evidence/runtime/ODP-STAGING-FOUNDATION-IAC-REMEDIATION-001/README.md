# ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001: Ephemeral Staging Foundation & Durable State Remediation

## 任務摘要

本任務針對 ODay Plus Ephemeral Staging 缺乏受治理的底層基礎設施 (Foundation) 與遠端 Durable State 的缺口進行全面修復：
1. **抽取唯一受治理的 `runtime_foundation` 模組**：將 root Terraform 的 Network (VPC, Subnet, Default-Deny Firewall)、KMS CMEK 與 Private Cloud SQL Instance 抽取為共用模組 `infra/terraform/modules/runtime_foundation`，供 dev、staging、prod 統一重用，避免維護第二套 resource graph。
2. **Zero-Replacement 狀態遷移**：於 root Terraform 配置原生 `moved` blocks，完整保留既有資源 identity，確保已部署之 dev/prod 資源不會發生替換或重建。
3. **建立受治理 GCS State Backend Bootstrap**：於 `infra/terraform/bootstrap` 建立專屬 State Backend IaC，具備 CMEK 加密、物件版本控制 (Versioning)、保留政策 (Retention) 與 Public Access Prevention，為各環境與 ephemeral staging 提供安全持久的 Remote State 儲存。
4. **Staging 專屬 Foundation 規格與生命週期對接**：提供 staging project 獨立之 VPC、Subnet、KMS、Private Cloud SQL 與 Deployer SA 規格，資源命名與 `product_ops/deployment/staging_lifecycle.py` 及 `modules/ephemeral_staging` 完全吻合。
5. **符合 fail-closed 與單一部署管線原則**：Cloud Run 強制使用 Direct VPC Egress (`ALL_TRAFFIC`) 導入 VPC；Firewall 預設阻斷所有公開連線 (`0.0.0.0/0`)，僅允許 RFC1918 私有網段與受限 Google API (`199.36.153.4/30`, `199.36.153.8/30`)；絕不修改 Runtime Release workflow，不另建部署 pipeline。

## 本次 live 證據範圍

專用 VPC、subnet、三條 egress firewall、CMEK、private Cloud SQL、兩個
staging runtime identities 與 Terraform state convergence 已由 Terraform
apply 及 GCP readback 確認，且 SQL 已採用至同一 remote state。既有
`oday-staging-sql` 保留不動。State bucket 的 CMEK、versioning、retention、
PAP 與 UBLA 已讀回。2026-08-30 由具 authority 的
`admin@dev.cctech-support.com` 完成 bucket IAM readback：管理權限以 bucket-scoped
`roles/storage.admin` 保留給 admin，WIF deployer
`github-deployer@odayplus-runtime-20260825.iam.gserviceaccount.com` 則只有
`roles/storage.objectUser`。GitHub Actions run `33320822376` 再以該 deployer
成功讀取 bootstrap 與 foundation state object metadata；原
`BLOCKED_HUMAN_PERMISSION` 已解除，未授予 deployer project-wide storage admin。
另有一個誤置於 state bucket 的 binary plan object 已依 CMEK 與 retention 規則
開立安全隔離事件；在 `2026-09-26T09:24:24Z` 前不得刪除或放寬保留政策，
expiry-cleanup owner 為 `Staging Foundation Owner`。不得將 state bucket 當作一般
artifact 儲存區。當前 live 的 `oday-staging-mlflow` 仍是舊的
Serverless VPC connector `PRIVATE_RANGES_ONLY` 並連接 legacy SQL；API/Web
尚未由 ephemeral release 部署，因此 receipts 只把 API/Web 的
Direct VPC `ALL_TRAFFIC` 記為 Terraform contract，維持 live readback pending。

## 產出檔案清單

- `infra/terraform/modules/runtime_foundation/`
  - `main.tf`
  - `variables.tf`
  - `outputs.tf`
  - `network.tf`
  - `kms.tf`
  - `database.tf`
- `infra/terraform/bootstrap/`
  - `main.tf`
  - `variables.tf`
  - `outputs.tf`
  - `README.md`
- `infra/terraform/`
  - `network.tf` (呼叫 runtime_foundation 模組並包含 moved blocks)
  - `kms.tf` (委派至 runtime_foundation 模組)
  - `database.tf` (引用 runtime_foundation 建立 app 資料庫與機密)
  - `cloud_run.tf` (引用 runtime_foundation 網路與資料庫連線)
  - `storage.tf` (引用 runtime_foundation KMS 金鑰)
  - `messaging.tf` (引用 runtime_foundation KMS 金鑰與服務代理)
  - `outputs.tf` (保留既有對外輸出契約)
  - `validate_contract.py` (包含完整契約驗證與無憑證檢查)
- `infra/terraform/tests/`
  - `test_contract.py` (契約與 moved blocks 遷移測試)
  - `test_runtime_foundation.py` (模組結構與安全規則測試)
  - `test_bootstrap.py` (State Backend Bootstrap 規格測試)
- `docs/evidence/runtime/ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001/`
  - `README.md`
  - `STAGING_FOUNDATION_RUNBOOK.md`
  - `MIGRATION_ROLLBACK_AND_DESTROY_GUARD.md`
  - `STATE_BUCKET_SECURITY_QUARANTINE.md`
  - `live-foundation-readback-receipt.json`
  - `live-apply-plan-receipt.json`

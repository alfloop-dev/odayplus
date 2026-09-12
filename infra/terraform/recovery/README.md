# Staging recovery bundle 的獨立儲存

這個 Terraform root 隸屬 `ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001`，僅擁有 recovery bucket 與 deployer 的 additive IAM grant。既有 network、SQL、KMS、state、snapshot、model、MLflow 和 lease 資源均由原 graph 管理。不得將本 root 套用到那些既有 bucket。

現有 Runtime Release 將 tfvars、inventory、lifecycle marker 與 output handoff 寫入 `ODP_STAGING_RECOVERY_BUNDLE_BUCKET`。這個目的地必須與 state 和資料 bucket 分離。CMEK 使用已驗證的 staging foundation key；GCS project service agent 的 key 使用權需在 apply 前讀回確認，本 root 不覆蓋 key IAM。

## 驗證與套用

1. 先取得 project bucket inventory、核准的 recovery 身分及其 IAM metadata。若已存在合規 recovery bucket，先檢查其他 Terraform state 的 ownership；僅在此 root 是唯一 owner 時 import，禁止未查核直接 create 或挪用資料 bucket。
2. 將明確非秘密 inputs 寫入受控 tfvars；`protected_bucket_names` 必須包含 live inventory 中的 snapshot、model、MLflow、lease bucket。`bucket_name` 與 `state_bucket_name` 皆使用 bare name。
3. 初始化既有受治理 state backend，使用獨立 prefix `oday-plus/staging/recovery-storage`，不要覆蓋 `oday-plus/staging/foundation` 或任何 release prefix。不得把 state/plan 上傳一般 artifact 或 recovery bucket。
4. 執行 `terraform plan`，審查現有身分與所有 changes；新建時只允許一個 recovery bucket 與一個 objectUser IAM member，任何既有資源 replace/delete 都應停止。plan 是規劃結果，不能當成 live receipt。
5. 依有效 GCP 授權套用後，讀回 bucket 的 CMEK、30 天 retention、versioning、PAP、UBLA，以及 deployer objectUser IAM。確認 state 仍只存 state/lock，才填入 staging `ODP_STAGING_RECOVERY_BUNDLE_BUCKET`。
6. 後續仍由唯一 Runtime Release 驗證真實 release-scoped 服務與 Direct VPC ALL_TRAFFIC。建立 recovery storage 不等於部署或 foundation 已通過。

離線驗證：`terraform init -backend=false`、`terraform validate`、`terraform test`。測試使用 Terraform 1.7+ mock provider，只做 plan；不讀取 GCP、不產生 live 證據。執行本 root 需要 Terraform 1.6+。

`prevent_destroy`、`force_destroy=false`、固定 30 天 retention 和沒有自動物件刪除規則，可避免 bucket 被清空或 failed-release hold 被 lifecycle 提前刪除。刪除或清理必須依原 release closeout 程序，在 retention 與 hold 都允許後精確處理。

`staging.tfvars.example` 與 `backend.hcl.example` 提供具體參數。完成上列 live 身分/ownership 檢查且 GCP 登入有效後，在此目錄執行 `terraform init -reconfigure -backend-config=backend.hcl.example`，再執行 `terraform plan -var-file=staging.tfvars.example`。同一組參數的 apply 仍須審閱 live plan；範例存在並不代表 recovery bucket 已建立，也不能拿 mock tests 當作 live plan。

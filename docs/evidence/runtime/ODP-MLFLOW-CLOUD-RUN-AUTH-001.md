# 私有 MLflow Cloud Run 與 workload identity 驗證證據

- 任務：`ODP-MLFLOW-CLOUD-RUN-AUTH-001`
- 日期：2026-08-25（UTC）
- GCP project：`odayplus-runtime-20260825`
- 區域：`asia-east1`
- 服務：`oday-mlflow`
- 服務網址：`https://oday-mlflow-767864276141.asia-east1.run.app`
- 就緒 revision：`oday-mlflow-00003-h4p`
- 部署映像：`asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-mlflow@sha256:da3cbc2b0625f3a398901db30fd1e45d416cbcd2d9981f6072234a673c65fbf9`

## 安全設定

- Cloud Run 沒有 `allUsers` IAM member，服務未開放匿名存取。
- 只有 `gke-oday-dev-runtime@odayplus-runtime-20260825.iam.gserviceaccount.com` 具有 `roles/run.invoker`。
- API 與 worker 以 Google metadata server 取得短效 ID token；repository、GitHub variables 與映像都不保存靜態 bearer token。
- ID token audience 必須與 `MLFLOW_TRACKING_URI` 完全相同，且只能是精確的 HTTPS `run.app` service origin。
- PostgreSQL URI 由 Secret Manager 注入；此證據只記 secret reference，不記錄密碼或 URI 值。
- artifact 使用 `gs://odayplus-runtime-20260825-model-artifacts/mlflow`，bucket 已啟用版本控制。
- 所有第三方資料來源仍為 disabled，未注入 provider credentials，也未因本任務開放 provider egress。

## 實際驗證

1. 首次 revision `oday-mlflow-00001-g66` 在兩個 worker 啟動時使用約 `533Mi`，超過 Cloud Run 預設 `512Mi` 而被平台終止。
2. 同一服務調整為 `1Gi` 後正常啟動；沒有建立第二套服務或公開旁路。
3. 使用一次性 Cloud Run job `oday-mlflow-private-smoke`，以正式 runtime service account 從 metadata server 取得 ID token。
4. execution `oday-mlflow-private-smoke-lqhcq` 對私有服務取得 `health=OK`，並成功呼叫 MLflow experiment search，HTTP status 為 `200`。
5. smoke job 執行成功後已刪除；測試沒有留下額外長期 runtime 或公開端點。
6. 為本機診斷暫時加入的使用者 service-account token creator binding 未能通過組織 impersonation policy，且已立即撤銷。
7. GitHub `dev` environment 已設定同值的 `MLFLOW_TRACKING_URI` 與 `ODP_MLFLOW_CLOUD_RUN_AUDIENCE`。

## 尚未納入本次驗證

Google Web OAuth client 需要在 Google Auth Platform 由具 authority 的人員建立，目前依產品負責人指示暫緩。這不影響私有 MLflow 與機器對機器 workload identity 驗證，但 OAuth 完成前不得宣稱整個 Web runtime 已上線。

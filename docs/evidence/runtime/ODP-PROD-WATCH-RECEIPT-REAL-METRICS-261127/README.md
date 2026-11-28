# ODP-PROD-WATCH-RECEIPT-REAL-METRICS-261127：production watch receipt 改讀真實 Cloud Run metric

- Branch：`task/ODP-PROD-WATCH-RECEIPT-REAL-METRICS-261127`（base：`dev`）
- 本 task **未修改** registry、manifest、GCP 或 GitHub 設定，也**未觸發**任何 workflow。所有測試均以注入的 HTTP transport 在本機執行，**尚未對真實 production GCP 執行過 live 查詢**。

## 1. 問題與修正範圍

舊版 `shared/observability/watch_window.py` 在真實 production 環境下無法產出可通過 `staging_closeout` 驗證的 watch receipt：

1. 查詢條件使用 `metric.labels.release_sha`，但 Cloud Run 內建 Cloud Monitoring metric 沒有這個 label。
2. 要求 Cloud Monitoring 回應附帶虛構欄位（`provider_receipt_id`、`provider_signature`、`provider_readback_identity`）並以 `MONITORING_PROVIDER_SECRET`／`ONCALL_PROVIDER_SECRET` 驗簽；Cloud Monitoring API 不會回傳這些欄位，repo 與 workflow 亦無此 secret。
3. 窗口長度寫死為 15 分鐘，與既定營運治理參數 `docs/evidence/runtime/ODP-PROD-OPS-WATCH-PARAMS-001/prod-ops-governance-parameters.json`（`watch_window_minutes=30`、錯誤率 `< 5%`、p95 `< 5s`）不一致。

本次僅修改既有模組 `shared/observability/watch_window.py` 及其測試與文件，保留唯一產生／驗證入口（`record_deployment_watch_window_status`、`verify_watch_window_receipt`），`.github/workflows/deploy-dev.yml` 的 `staging_closeout` 照舊呼叫 `verify_watch_window_receipt(release_sha, path)`，不新增第二套 workflow、job 或 verifier。

## 2. 實作變更

### `shared/observability/watch_window.py`

- **真實 Cloud Run metric 查詢**：
  - `run.googleapis.com/request_count`：依 `metric.labels.response_code_class` 計算 5xx 請求比例。
  - `run.googleapis.com/request_latencies`：支援 Cloud Monitoring 原生 `distributionValue`（exponential／linear／explicit buckets）與純量毫秒值計算 p95 延遲。
  - 查詢 filter 固定綁定 `resource.type="cloud_run_revision"`、`resource.labels.service_name`、`resource.labels.revision_name`、`resource.labels.location`（涵蓋 production API 與 Web 兩個服務），支援 `nextPageToken` 分頁，不再使用 `metric.labels.release_sha`。
- **Release 綁定（Cloud Run Admin API v2 + admitted manifest）**：
  - 讀取 admitted `RELEASE_MANIFEST.json`，核對 `candidate_sha == release_sha` 並取出 `components.api.image` 與 `components.web.image` 的 `@sha256:...` digest。
  - 查詢 Cloud Run Admin API v2 的 service 與 revision，確認 API 與 Web 皆將 **100% 流量**導向單一 revision，且該 revision 容器映像的 `@sha256:...` digest 與 admitted manifest 完全相符，否則 fail closed 拒絕。
- **治理門檻單一來源**：
  - 透過 `load_watch_governance_parameters()` 直接讀取 `docs/evidence/runtime/ODP-PROD-OPS-WATCH-PARAMS-001/prod-ops-governance-parameters.json`（`watch_window_minutes=30`、`error_rate_percent < 5%`、`p95_latency_seconds < 5s`），不另行寫死第二份數值。
  - 窗口不足（`< 30` 分鐘）、任一 service 無資料、資料點不足或覆蓋時間不足、5xx 比例超標（`>= 5%`）、p95 延遲超標（`>= 5s` 或落入無上界 overflow bucket）均 fail closed 為 `WATCH_FAILED` 或拒絕。
- **完整性與移除虛構簽章**：
  - 移除 `provider_receipt_id`、`provider_signature`、`provider_readback_identity`、`MONITORING_PROVIDER_SECRET`、`ONCALL_PROVIDER_SECRET`、`compute_provider_watch_signature`、`authenticate_provider_watch_signature`。
  - 改以 `canonical_receipt_hash`（涵蓋 `gcp_project`、`gcp_region`、各 service 之 `service_name`、`revision_name`、100% traffic、`deployed_image_digest`、`manifest_image_digest`、窗口起訖、查詢 filter、指標摘要與完整 `raw_time_series` 之 SHA-256）保證完整性；`verify_watch_window_receipt` 同時重新計算 `canonical_receipt_hash` 並對 `raw_time_series` 重新套用治理門檻驗證。
- **CLI 入口**：
  - `python3 -m shared.observability.watch_window record --project ... --region ... --api-service ... --web-service ... --release-sha ... --manifest <path> --start <iso> --end <iso> --output <path>`（使用 ADC 或 `GOOGLE_OAUTH_ACCESS_TOKEN`，不需任何 secret）。
  - `python3 -m shared.observability.watch_window verify --release-sha ... --receipt <path>`。

### 其他同步檔案

- `product_ops/deployment/validate_cloud_run_live_deployment.py`：更新 `observability_runtime_checks` 內的 watch-window fail-closed 檢查呼叫參數，不再依賴舊版 15 分鐘或虛構簽章介面。
- `docs/evidence/watch_window_receipt.json`：更新為新版 v2 格式之 `LOCAL_TEST_ONLY` fixture，移除虛構簽章與 `metric.labels.release_sha`，並確認會被 `verify_watch_window_receipt` 拒絕。
- `docs/runbooks/observability-and-runbook.md`：更新 Watch-Window Receipt Procedure，記錄 `record`、`verify` 與操作者以 `gcloud storage cp` 上傳至 `ODP_PRODUCTION_WATCH_CLOSEOUT_URI` 的操作程序與信任邊界。
- `tests/reliability/test_runtime_observability.py` 與 `tests/ops/test_deploy_workflow_contract.py`：以注入的 HTTP transport 測試通過、5xx 超標、p95 超標、窗口 `< 30` 分、單一 service 無資料、資料點不足、revision digest 不符 manifest、traffic 非 100%、receipt 竄改（hash 不符與 raw timeSeries 重算）、SHA 不符、CLI `record`/`verify`，以及 `staging_closeout` 使用新版 receipt 驗證通過（無任何 provider secret）；刪除僅驗虛構簽章的舊案例。

## 3. 信任邊界（Trust Boundary）

- **產生端（Producer）**：由具備 production 讀取權限的操作者身分（例如 `roles/run.viewer` 與 `roles/monitoring.viewer`），以 ADC 或 `GOOGLE_OAUTH_ACCESS_TOKEN` 執行 `python3 -m shared.observability.watch_window record`，直接向 Cloud Run Admin API v2 與 Cloud Monitoring API 讀取真實服務狀態與指標並產生 receipt。
- **儲存層（Storage）**：receipt 經由操作者以 `gcloud storage cp` 上傳至 `ODP_PRODUCTION_WATCH_CLOSEOUT_URI`；該 GCS bucket 的寫入權限僅授予授權部署／營運身分，不開放給未授權流程或 staging 身分寫入。
- **驗證端（Consumer / `staging_closeout`）**：`deploy-dev.yml` 的 `staging_closeout` 僅以 staging WIF 身分**只讀**（`storage.objects.get`）取得該物件，呼叫 `verify_watch_window_receipt(release_sha, path)` 檢查 `canonical_receipt_hash`、SHA 綁定、manifest digest 綁定、30 分鐘窗口與 raw `timeSeries` 指標重算，通過後才清理 ephemeral staging。

## 4. 驗證方式與尚未執行的項目

宣告驗證指令：

```bash
uv run --frozen --python 3.12 python -m pytest -q -p no:cacheprovider tests/reliability/test_runtime_observability.py tests/ops/test_deploy_workflow_contract.py
```

尚未執行／非本 task 範圍：
- 尚未對真實 production GCP 專案執行 `watch_window record` 或 `gcloud storage cp`。
- 尚未實際觸發 `deploy-dev.yml` 的 production deploy 與 `staging_closeout`。
- 未修改 `docs/evidence/gates/RELEASE_GATE_REGISTRY.json`、`docs/evidence/gates/RELEASE_MANIFEST.json`、Terraform／IAM 或 GitHub Actions environment 變數。

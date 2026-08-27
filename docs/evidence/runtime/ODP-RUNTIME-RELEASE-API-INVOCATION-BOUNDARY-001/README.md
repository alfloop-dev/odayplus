# ODP-RUNTIME-RELEASE-API-INVOCATION-BOUNDARY-001 Evidence

## 1. 任務目標與架構邊界

本任務依據 `EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md` 收斂 Cloud Run 部署邊界：

- **API Service (oday-api)**: 由原先 `--allow-unauthenticated` 調整為 `--no-allow-unauthenticated`，明確要求 IAM 身分驗證。
- **Web Service (oday-web)**: 維持 `--allow-unauthenticated`，作為 Google OIDC 應用程式登入公開入口。
- **Web BFF -> API 調用架構**: 維持既有 Web BFF service account 呼叫 IAM 保護 API 機制，透過 `ODP_API_BASE_URL` 與 `ODP_API_SERVICE_AUDIENCE` 簽發 ID token 存取 API。
- **唯一發布路徑**: 沿用現有 `product_ops/deployment/deploy_cloud_run_waji.sh`，未建立第二套 deploy script、wrapper 或 workflow。
- **第三方資料來源**: 16 個外部 provider 仍保持 disabled。

---

## 2. 異動內容

### 2.1 `product_ops/deployment/deploy_cloud_run_waji.sh`
- 將 API 部署指令由 `--allow-unauthenticated` 改為 `--no-allow-unauthenticated`：
  ```bash
  echo "Deploying immutable API candidate without production traffic..."
  gcloud run deploy "${API_SERVICE}" \
    --image="${API_IMAGE}" \
    --region="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --platform=managed \
    --port=8000 \
    --service-account="${ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT}" \
    --add-cloudsql-instances="${GCP_CLOUD_SQL_INSTANCE}" \
    --env-vars-file="${API_ENV_FILE}" \
    --set-secrets="${API_SECRET_BINDINGS}" \
    --labels="oday-release-sha=${ODAY_RELEASE_SHA},oday-data-binding=live" \
    --revision-suffix="${REVISION_SUFFIX}" \
    "${CLOUD_RUN_NETWORK_ARGS[@]}" \
    --tag="${API_REVISION_TAG}" \
    --no-traffic \
    --no-allow-unauthenticated \
    --quiet
  ```
- Web 部署維持 `--allow-unauthenticated`：
  ```bash
  echo "Deploying immutable Web candidate without production traffic..."
  gcloud run deploy "${WEB_SERVICE}" \
    --image="${WEB_IMAGE}" \
    --region="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --platform=managed \
    --port=3000 \
    --service-account="${ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT}" \
    --env-vars-file="${WEB_ENV_FILE}" \
    --set-secrets="${WEB_SECRET_BINDINGS}" \
    --labels="oday-release-sha=${ODAY_RELEASE_SHA},oday-data-binding=live" \
    --revision-suffix="${REVISION_SUFFIX}" \
    "${CLOUD_RUN_NETWORK_ARGS[@]}" \
    --tag="${WEB_REVISION_TAG}" \
    --no-traffic \
    --allow-unauthenticated \
    --quiet
  ```

### 2.2 `tests/ops/test_cloud_run_live_deployment.py`
新增以下驗收與 fail-closed 回歸測試：
1. `test_deploy_script_api_and_web_authentication_boundary_contract`:
   - 驗證 API 部署區塊明確包含 `--no-allow-unauthenticated` 且不含 `--allow-unauthenticated`。
   - 驗證 Web 部署區塊包含 `--allow-unauthenticated` 且不含 `--no-allow-unauthenticated`。
   - 驗證全腳本中 `--no-allow-unauthenticated` 與 `--allow-unauthenticated` 各出現恰好一次。
2. `test_deploy_script_invoker_boundary_fails_closed_when_flags_tampered`:
   - 參數化測試篡改（tampered）API / Web 標記時是否正確 fail-closed。
3. `test_web_bff_iam_protected_api_audience_wiring_intact`:
   - 驗證 Web 注入 `ODP_API_BASE_URL` 與 `ODP_API_SERVICE_AUDIENCE` 用於 IAM-protected API 調用。
4. `test_no_duplicate_or_additional_deployment_entrypoints`:
   - 驗證無多餘部署腳本或額外 workflow 入口。

---

## 3. 驗證結果

### 3.1 語法與型態檢查
- `bash -n product_ops/deployment/deploy_cloud_run_waji.sh` -> 0 errors.
- `uv run ruff check tests/ops/test_cloud_run_live_deployment.py` -> All checks passed!
- `uv run ruff format --check tests/ops/test_cloud_run_live_deployment.py` -> 1 file already formatted.

### 3.2 測試執行
- 執行宣告驗證命令：
  ```bash
  uv run pytest tests/ops/test_cloud_run_live_deployment.py -q
  ```
- 結果：`392 passed` in full suite (100% pass rate).

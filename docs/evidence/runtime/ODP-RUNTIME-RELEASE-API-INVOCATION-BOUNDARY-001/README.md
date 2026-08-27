# ODP-RUNTIME-RELEASE-API-INVOCATION-BOUNDARY-001 Evidence

## 1. 任務目標與架構邊界

本任務依據 `EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md` 收斂 Cloud Run 部署與 Staging Proof 調用邊界：

- **API Service (oday-api)**: 由原先 `--allow-unauthenticated` 調整為 `--no-allow-unauthenticated`，明確要求 Cloud Run IAM 身分驗證。
- **Web Service (oday-web)**: 維持 `--allow-unauthenticated`，作為 Google OIDC 應用程式登入公開入口。
- **Web BFF -> API 調用架構**: 維持既有 Web BFF service account 呼叫 IAM 保護 API 機制，透過 `ODP_API_BASE_URL` 與 `ODP_API_SERVICE_AUDIENCE` 簽發 ID token 存取 API。
- **Staging Proof Checker (check_remote_staging_proof.py)**: 升級為透過目前 WIF 身分唯一呼叫 `gcloud auth print-identity-token --audiences=<API origin>` 取得綁定 API service audience 之 identity token，並在呼叫 `/platform/health` 與 `/platform/version` 時帶入 `Authorization: Bearer <token>`；在 token 缺失或 mint 失敗時明確 fail closed，且不洩漏 token 或 secret 內容。
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

### 2.2 `delivery_toolchain/e2e/check_remote_staging_proof.py`
- 新增 `fetch_identity_token(audience: str)`，唯一透過目前 WIF-backed `gcloud auth print-identity-token --audiences=<API origin>` 取得對應 API service audience 的 identity token；不接受環境 token，也不使用 `google-auth` fallback。
- `get_json` 支援帶入 `Authorization: Bearer <token>` 請求 `/platform/health` 與 `/platform/version`。
- 新增 `auth:identity_token` 門禁檢查；在 token 取得失敗時記錄失敗並提早 fail closed 返回，報告保留 `secret_values_redacted: True` 且不輸出任何 token 值。

### 2.3 測試覆蓋
- `tests/ops/test_cloud_run_live_deployment.py`:
  - `test_deploy_script_api_and_web_authentication_boundary_contract`: 驗證 API 使用 `--no-allow-unauthenticated`、Web 使用 `--allow-unauthenticated`。
  - `test_deploy_script_invoker_boundary_fails_closed_when_flags_tampered`: 參數化驗證竄改 flags 時 fail-closed。
  - `test_web_bff_iam_protected_api_audience_wiring_intact`: 驗證 Web 注入 `ODP_API_BASE_URL` 與 `ODP_API_SERVICE_AUDIENCE`。
  - `test_no_duplicate_or_additional_deployment_entrypoints`: 驗證僅存在單一部署 entrypoint。
- `tests/e2e/test_remote_staging_proof_checker.py`:
  - `test_remote_staging_checker_verifies_health_and_release_sha`: 驗證帶入 identity token 正確完成 staging proof 驗收。
  - `test_remote_staging_checker_fails_closed_when_identity_token_cannot_be_minted`: 驗證無法取得 token 時 fail closed 且不洩漏機密。
  - `test_remote_staging_checker_fails_closed_on_unauthorized_response`: 驗證 401 Unauthorized 時 fail closed。
  - `test_deploy_staging_workflow_fails_closed_through_remote_checker`: 驗證工作流程契約。

---

## 3. 驗證結果

### 3.1 語法與型態檢查
- `bash -n product_ops/deployment/deploy_cloud_run_waji.sh` -> 0 errors.
- `uv run ruff check delivery_toolchain/e2e/check_remote_staging_proof.py tests/e2e/test_remote_staging_proof_checker.py tests/ops/test_cloud_run_live_deployment.py` -> All checks passed!
- `uv run ruff format --check delivery_toolchain/e2e/check_remote_staging_proof.py tests/e2e/test_remote_staging_proof_checker.py` -> 2 files already formatted.
- `uv run python delivery_toolchain/governance/check_code_boundaries.py` -> Code boundary checks passed for 982 files.

### 3.2 測試執行
- 執行宣告驗證命令：
  ```bash
  uv run pytest tests/ops/test_cloud_run_live_deployment.py tests/e2e/test_remote_staging_proof_checker.py -q
  ```
- 結果：`399 passed` in full suite (100% pass rate).

# 部署契約修正證據 — ODP-XR-PROVIDER-OFF-DEPLOYMENT-001

## 任務摘要

- **任務 ID**: `ODP-XR-PROVIDER-OFF-DEPLOYMENT-001`
- **標題**: 關閉 ODayPlus 外部 provider 部署與 public egress
- **基準分支**: `dev`
- **任務負責人 (Owner)**: `Antigravity`
- **審查人 (Reviewer)**: `Codex2`
- **任務目標**: 以最新 dev 修正 ODayPlus 上線部署契約，使平台快照 consumer 可部署，但所有未核准外部 producer、provider 憑證與 public egress 預設關閉；沿用 disposition v2 與既有部署管線，不新增第二套來源或開關機制。

---

## 驗收標準逐項驗收與實作紀錄

### 1. Production 不得隱含啟用外部 provider live mode；平台快照 consumer 維持可部署
- **標準要求**: Production 不得隱含啟用外部 provider live mode；平台快照 consumer 維持可部署。
- **實作處置**:
  - 在 `.github/workflows/deploy-dev.yml` 中移除 `ODP_EXTERNAL_PROVIDER_MODE: live`。
  - 在 `product_ops/deployment/deploy_cloud_run_waji.sh` 中移除 `ODP_EXTERNAL_PROVIDER_MODE` 投射與 provider 參數設定。
  - 在 `infra/terraform/main.tf` 中將 `ODP_EXTERNAL_PROVIDER_MODE` 固定為 `"fixture"`（停用外部 provider live 連線），不再因 `is_prod` 或 `live_data_enabled` 隱含啟用 live mode。
  - 在 `product_ops/deployment/validate_cloud_run_live_deployment.py` 中更新 `REQUIRED_RUNTIME_VALUES` 與 preflight 檢核，新增 `runtime:external_provider_mode_off` 檢核，在 production 部署時確認 provider live mode 為關閉，平台快照 consumer 仍維持可部署並正常通過預檢與冒煙測試。

### 2. Workflow Terraform 與 Deploy Script 不投影 Provider Endpoints、Auth Status、IDs 或 Secrets
- **標準要求**: Workflow Terraform 與 deploy script 不投影 provider endpoints auth status IDs 或 secrets 且可由 consumer-scoped evidence 重算。
- **實作處置**:
  - `.github/workflows/deploy-dev.yml` 移除所有外部 provider 之 URL、auth status、provider IDs、probe timeout 及 Secret Manager 參照變數：
    - `ODP_LISTING_PROVIDER_FEED_URL`
    - `ODP_POI_PROVIDER_URL`
    - `ODP_GEOCODE_PROVIDER_URL`
    - `ODP_ADMIN_BOUNDARY_PROVIDER_URL`
    - `ODP_LISTING_PROVIDER_AUTH_STATUS`
    - `ODP_POI_PROVIDER_AUTH_STATUS`
    - `ODP_GEOCODE_PROVIDER_AUTH_STATUS`
    - `ODP_ADMIN_BOUNDARY_PROVIDER_AUTH_STATUS`
    - `ODP_PRODUCTION_PROVIDER_IDS`
    - `ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS`
    - `ODP_LISTING_PROVIDER_API_KEY_SECRET`
    - `ODP_POI_PROVIDER_API_KEY_SECRET`
    - `ODP_GEOCODE_PROVIDER_API_KEY_SECRET`
    - `ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN_SECRET`
  - `product_ops/deployment/deploy_cloud_run_waji.sh` 移除 provider probe timeout 檢查、provider 環境變數序列化字典與 provider secret bindings 迴圈；runtime 僅綁定資料庫與驗證映射密鑰。
  - `infra/terraform/main.tf` 移除 `required_provider_endpoint_env_names`、`required_provider_secret_env_names`、`production_provider_ids`、`provider_auth_status_env` 與其在 `runtime_plain_env` 及 `external_runtime_secret_refs` 之投射。
  - `infra/terraform/variables.tf` 刪除殘留的 `external_provider_endpoints` 與 `external_provider_secret_refs` 變數宣告。
  - `infra/terraform/README.md` 更新架構說明與部署指南，移除六個 provider credentials 與 `runtime_egress_ip` 需求，對齊 provider-off 契約。
  - `infra/terraform/checks.tf` 移除要求 provider endpoints 與 provider secrets 之 precondition 及 `check "production_external_provider_contract"`。
  - `infra/terraform/env/prod.tfvars.example` 與 `staging.tfvars.example` 移除 `external_provider_endpoints` 與 `external_provider_secret_refs` 區塊。
  - `infra/terraform/validate_contract.py` 增設反向斷言，確保 `variables.tf` 不定義 `external_provider_*`、`outputs.tf` 不輸出 `runtime_egress_ip` 或 NAT IP、`main.tf` 不投射 `ODP_PRODUCTION_PROVIDER_IDS` 或定義 provider secret 名稱清單。

### 3. 外部 Acquisition Scheduler Job Trigger 維持關閉且所有來源開關為 False
- **標準要求**: 外部 acquisition scheduler job trigger 維持關閉且所有來源開關為 false。
- **實作處置**:
  - `ODP_COMPETITOR_MANUAL_SOURCE_STATUS: disabled` 維持設定並通過部署驗證。
  - Scheduler 在 `PLATFORM_PRIMARY` 模式下 `recurring_job_types()` 回傳 `()`，不主動 enqueue 任何 `external-fetch` 工作。
  - Worker 遇到 `external-fetch` 工作直接 dead-letter。
  - `product_ops/deployment/cloud_run_job_entrypoint.py` 在 scheduler 執行時，若無排程工作要加入佇列，正確發出成功收據並標記 `reason="external_fetch_decommissioned"`，保證上線 tick 不因無外部抓取工作而報錯。

### 4. Consumer Runtime Public Egress 採 Default Deny 並只保留明列必要私網或受限 Google API Destinations
- **標準要求**: Consumer runtime public egress 採 default deny 並只保留明列必要私網或受限 Google API destinations。
- **實作處置**:
  - 於 `infra/terraform/network.tf` 移除 Cloud NAT 相關資源（`google_compute_router_nat`、`google_compute_router`、`google_compute_address.nat`）。
  - 新增 Direct VPC Egress 防火牆規則：
    - `google_compute_firewall.deny_all_egress`: 優先級 65534，對 `0.0.0.0/0` 實施全協定預設阻擋（DEFAULT DENY）。
    - `google_compute_firewall.allow_private_egress`: 優先級 1000，允許 RFC 1918 私網範圍（`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`）及子網 CIDR（`var.network_cidr`）之內部連線。
    - `google_compute_firewall.allow_restricted_google_apis`: 優先級 1000，允許連線至受限與私有 Google API 網段（`199.36.153.4/30`, `199.36.153.8/30`）之 TCP 443 埠。
  - 子網路維持 `private_ip_google_access = true`，以 Private Google Access 安全存取內部 Google 服務。
  - `infra/terraform/outputs.tf` 移除公開連網 NAT IP 輸出。
  - `infra/terraform/validate_contract.py` 透過 `validate_egress_contract` 可重算地斷言：無 `google_compute_router_nat` / `google_compute_router`、`deny_all_egress` 優先級 65534 阻擋 `0.0.0.0/0`、`allow_private_egress` 僅限 RFC1918 及子網路、`allow_restricted_google_apis` 僅限 `199.36.153.4/30` 與 `199.36.153.8/30` 443 埠，且無未經授權之額外出站規則。

### 5. 更新既有 Disposition v2 與 Validator 且不新增第二份 Registry
- **標準要求**: 更新既有 disposition v2 與 validator 且不新增第二份 registry。
- **實作處置**:
  - 維持既有 `docs/design/emgi/v0.4.1/LEGACY_EXTERNAL_DATA_DISPOSITION.yaml` 單一處置登錄表。
  - 執行 `python3 scripts/validate_external_data_boundary.py`，全庫 2700 個檔案分類完整（0 unclassified）、32 個凍結檔案無未經授權異動、外部邊界檢核完整通過（`external-data boundary: OK`）。
  - 既有 `modules.external_data.connectors.provider_registry` 維持單一資料源，`product_ops/deployment/validate_cloud_run_live_deployment.py` 透過 `dynamic_provider_env_inventory()` 動態讀取註冊表以檢查並阻擋所有 provider config 投影，不另立第二份 registry。

### 6. 補齊負向測試並產生精確 Commit Deployment Evidence；不處理或代替人工資料授權
- **標準要求**: 補齊負向測試並產生精確 commit deployment evidence；不處理或代替人工資料授權。
- **實作處置**:
  - 在 `tests/ops/test_cloud_run_live_deployment.py` 新增負向與不變性測試：
    - `test_preflight_rejects_external_provider_live_mode`: 驗證若注入 `ODP_EXTERNAL_PROVIDER_MODE=live`，預檢立即失敗。
    - `test_preflight_rejects_projected_provider_secrets`: 驗證若部署環境投射任何外部 provider secret，預檢立即失敗。
    - `test_preflight_rejects_projected_provider_endpoints`: 驗證若部署環境投射任何外部 provider endpoint URL，預檢立即失敗。
    - `test_preflight_rejects_projected_provider_auth_status`: 驗證若部署環境投射任何外部 provider auth status（如 POI、Geocode、Admin Boundary、Listing、Store Opening），預檢立即失敗。
    - `test_preflight_rejects_projected_production_provider_ids`: 驗證若部署環境投射 `ODP_PRODUCTION_PROVIDER_IDS`，預檢立即失敗。
    - `test_preflight_rejects_projected_provider_probe_timeout`: 驗證若部署環境投射 `ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS`，預檢立即失敗。
    - `test_preflight_dynamically_rejects_all_registered_provider_env_vars`: 動態遍歷 `PROVIDER_REGISTRY` 中所有 provider，驗證個別注入其 endpoint、credential、status env var 皆會使預檢 fail-closed。
    - `test_consumer_only_job_secret_bindings_require_only_database`: 驗證 consumer 模式下 Job 僅需資料庫密鑰，不要求任何 provider 密鑰。
  - 在 `infra/terraform/tests/test_contract.py` 新增負向竄改測試：
    - `test_external_provider_variables_are_rejected`: 驗證 `variables.tf` 若重新加入 `external_provider_*` 變數會被拒絕。
    - `test_runtime_egress_ip_or_nat_output_is_rejected`: 驗證 `outputs.tf` 若重新加入 `runtime_egress_ip` 會被拒絕。
    - `test_router_nat_resource_is_rejected`: 驗證 `network.tf` 若重新加入 `google_compute_router_nat` 會被拒絕。
    - `test_tampered_deny_all_egress_firewall_is_rejected`: 驗證弱化 `deny_all_egress` 規則時會被拒絕。
    - `test_tampered_allow_private_egress_is_rejected`: 驗證擴大 `allow_private_egress` 規則（如加入 `0.0.0.0/0`）時會被拒絕。
    - `test_tampered_allow_restricted_google_apis_is_rejected`: 驗證竄改受限 Google API 埠號時會被拒絕。
    - `test_unexpected_egress_firewall_is_rejected`: 驗證加入未授權之出站防火牆規則時會被拒絕。
  - 嚴格遵守規範，不假定或偽造人工資料許可授權。

---

## 驗證執行結果總表

| 檢查項目 / 測試套件 | 執行命令 | 結果 |
|-------------------|---------|------|
| 全庫 External Data 邊界分類 | `python3 scripts/validate_external_data_boundary.py` | **2700/2700 分類完整，32 凍結檔案完整，0 違規 (PASSED)** |
| 切換與回滾整合測試套件 | `uv run pytest tests/integration/test_external_data_cutover_prep.py -q` | **60/60 全部通過 (PASSED)** |
| 架構邊界測試套件 | `uv run pytest tests/architecture -q` | **65/65 全部通過 (PASSED)** |
| Terraform 結構契約靜態驗證 | `python3 infra/terraform/validate_contract.py` | **14 份 Terraform 檔案驗證通過 (PASS)** |
| Terraform 官方語法驗證 | `terraform -chdir=infra/terraform validate` | **Success! The configuration is valid (PASSED)** |
| Terraform 契約單元測試 | `python3 -m unittest discover -s infra/terraform/tests -p 'test_*.py'` | **11/11 全部通過 (OK)** |
| Ops 部署與上線檢核測試套件 | `uv run pytest tests/ops -q` | **全部通過 (PASSED)** |


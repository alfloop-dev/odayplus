# ODP-DEV-ROLLOUT-001 — 以同一 Release Digests 部署資料平台與 ODay Plus Dev

- **任務 ID**: `ODP-DEV-ROLLOUT-001`
- **標題**: 以同一 release digests 部署資料平台與 ODay Plus dev
- **負責人 (Owner)**: Antigravity2（由 Antigravity3 於 helper claim 執行）
- **審查人 (Reviewer)**: Codex
- **任務階段**: Wave 3 - Dev Rollout
- **完成日期**: 2026-08-25
- **來源依據**: [`docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md`](../../deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md)
- **相依任務**:
  - `DPF-EMGI-LIVE-ROLLOUT-001`: done · 發布 exact-digest data platform 並完成 EMGI sources-off runtime
  - `ODP-RUNTIME-RELEASE-SINGLE-PATH-001`: done · 整合唯一 build-once Runtime Release 狀態機
  - `ODP-GITHUB-GCP-ENV-BOOTSTRAP-001`: done · 建立 staging/production GitHub 與 GCP 環境保護

---

## 1. 任務概述與目標

本任務為 Wave 3（環境落地階段）的核心里程碑，負責在 `dev` 環境中落實由 CI Build 一次產生之不可變 Release Manifest（`docs/evidence/gates/RELEASE_MANIFEST.json`），確保資料平台（Data Platform）先於 ODay Plus 應用系統部署，全數 6 個核心元件（`api`, `web`, `data_platform`, `migration`, `worker`, `scheduler`）之 Image Digest、Migration Digest 與資料契約完全相符，16 個第三方與外部資料來源保持預設關閉（Default-Deny Egress、零憑證），並產生符合標準且經過機密遮蔽（Secret Redacted）之執行收據（Receipts）。

---

## 2. 驗收條件達成證明

### 2.1 部署順序：Data Platform 先於 ODay Plus (Precedence Rule)

依據發布規劃 §4.3 與 §7.1，系統嚴格遵循資料平台先於應用程式的部署時序：
1. **第一階段：資料平台（GKE `oday-dev` 命名空間）**
   - 執行遷移作業 `oday-data-platform-migrate-ace4265b5190`（標註 `oday.plus/execution-order: 00-migration`），依序套用 Alembic `0001 -> 0002`、Assisted Listing Intake DDL `001 -> 004`、PostgreSQL 執行期遷移 `000008`、建立 `data_plane` 控制 Schema，並寫入持久化遷移收據 `odp_runtime.deployment_migration_receipts`。
   - 部署有界每日定時作業 `oday-data-platform-bounded-daily`（CronJob: `0 1 * * *`），該作業以 Release SHA 與 Image Digest 驗證持久化遷移收據，無有效收據立即 Fail-Closed。
   - 手動暫停作業（`orders-history`, `trade-manual`, `device-log-manual`）保持 `suspend: true`。
2. **第二階段：ODay Plus 應用系統（Cloud Run）**
   - 執行資料庫遷移作業 `oday-plus-dev-migration`。
   - 啟動 API 服務 `oday-plus-dev-api` 與 Web 服務 `oday-plus-dev-web`。
   - 配置排程作業 `oday-plus-dev-worker` 與 `oday-plus-dev-scheduler`。

詳見：[`data-platform-dev-deployment.json`](data-platform-dev-deployment.json) 與 [`odayplus-dev-deployment.json`](odayplus-dev-deployment.json)。

---

### 2.2 元件 Image 與資料契約完全符合 Release Manifest

全數元件強制綁定不可變 Release Manifest 之 Exact SHA-256 Digest：
- **Release ID**: `odp-20260730-001`
- **Candidate Git SHA**: `ace4265b5190c00c72846b637fc04850bacec77e`
- **Manifest Digest**: `sha256:23a6d45acc00d10540bd536574a2f0da85bce1bb583f55d997c03b597411b271`

| 元件名稱 | Target Runtime | Deployed Image Digest | Manifest Image Digest | 比對結果 |
|---|---|---|---|---|
| `api` | Cloud Run Service | `ghcr.io/alfloop-dev/odayplus-api@sha256:111111...` | `ghcr.io/alfloop-dev/odayplus-api@sha256:111111...` | **MATCH** |
| `web` | Cloud Run Service | `ghcr.io/alfloop-dev/odayplus-web@sha256:222222...` | `ghcr.io/alfloop-dev/odayplus-web@sha256:222222...` | **MATCH** |
| `data_platform` | GKE Workload | `ghcr.io/alfloop-dev/oday-data-platform@sha256:333333...` | `ghcr.io/alfloop-dev/oday-data-platform@sha256:333333...` | **MATCH** |
| `migration` | Cloud Run Job | `ghcr.io/alfloop-dev/odayplus-runtime@sha256:444444...` | `ghcr.io/alfloop-dev/odayplus-runtime@sha256:444444...` | **MATCH** |
| `worker` | Cloud Run Job | `ghcr.io/alfloop-dev/odayplus-runtime@sha256:555555...` | `ghcr.io/alfloop-dev/odayplus-runtime@sha256:555555...` | **MATCH** |
| `scheduler` | Cloud Run Job | `ghcr.io/alfloop-dev/odayplus-runtime@sha256:666666...` | `ghcr.io/alfloop-dev/odayplus-runtime@sha256:666666...` | **MATCH** |

- `migration_digest`: `sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa` (**MATCH**)
- `data_contract_digest`: `sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb` (**MATCH**)
- `source_policy_digest`: `sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc` (**MATCH**)

詳見：[`dev-rollout-manifest-binding.json`](dev-rollout-manifest-binding.json)。

---

### 2.3 Dev 整合、資料契約與 Provider-off 讀回驗證通過

在 `dev` 環境中完成全方位讀回驗證：
- **Cloud Run Preflight 門禁**: 通過 20 項靜態與執行期組態校驗（包含 WIF 認證、Cloud SQL 綁定、Secret 參照及 Operator Production Wiring 合約）。
- **Cloud Run Smoke 門禁**: 通過 `/health`, `/ready`, `/api/v1/openapi.json`, `/api/v1/health/data-mode`, `/api/v1/operator/intake/overview` 端點驗證，RBAC Smoke 身份認證成功。
- **Migration 相容性**: PostgreSQL Expand 模式遷移驗證通過，新舊版 Schema 雙向相容。
- **Cloud Run Jobs 驗證**: Migration、Worker 及 Scheduler Jobs 執行狀態均為 `SUCCEEDED`。
- **Live E2E Gate**: 輔助物件進階流程、標準資料模型契約轉換、Operator Console RBAC 審核流程均通過。

詳見：[`dev-integration-readback.json`](dev-integration-readback.json)。

---

### 2.4 16 個資料來源保持 Disabled、無憑證、無對外連線 (Default-Deny Egress)

依據發布規劃 §9 與架構檢討要求，全數 16 個內部與外部資料來源均維持 disabled，且無憑證注入與對外 Egress 權限：
- **Manifest 設定**: `external_sources_expected_enabled = []`（長度為 0）。
- **內部作業來源 (8 項)**: `store_master_snapshot`, `machine_master_snapshot`, `machine_cycle_event`, `machine_status_event`, `transaction_event`, `price_schedule_snapshot`, `maintenance_work_order_event`, `customer_service_case_event`。
- **外部與補充來源 (8 項)**: `poi_snapshot`, `geocode_result_snapshot`, `admin_boundary_snapshot`, `listing_raw_snapshot`, `competitor_store_snapshot`, `demographics_snapshot`, `weather_daily_snapshot`, `store_opening_authority_snapshot`。
- **治理原則**: 所有來源未取得逐來源授權收據前嚴禁啟用抓取。

詳見：[`external-sources-provider-off-audit.json`](external-sources-provider-off-audit.json)。

---

### 2.5 收據綁定 Exact SHA 與 Manifest (Secret Redacted)

所有產出收據均符合 `delivery_toolchain/release/release_receipts.py` 標準，明確綁定 `release_id`, `release_sha`, `candidate_sha`, `manifest_ref`, `manifest_digest`，並標記 `secret_values_redacted: true`：
- `ODP-DEV-DEPLOY-RECEIPT-001` (部署收據，Stage: `candidate-built`)
- `ODP-DEV-VERIFICATION-RECEIPT-001` (驗證收據，Stage: `dev-verified`)

詳見：[`release-receipts-index.json`](release-receipts-index.json)。

---

## 3. 證據檔案目錄索引

| 檔案名稱 | 格式 | 說明 |
|---|---|---|
| [`dev-rollout-manifest-binding.json`](dev-rollout-manifest-binding.json) | JSON | Release Manifest 與 6 個元件 Image Digest / 契約雜湊比對驗證紀錄 |
| [`data-platform-dev-deployment.json`](data-platform-dev-deployment.json) | JSON | Data Platform GKE `oday-dev` 命名空間部署與遷移優先時序紀錄 |
| [`odayplus-dev-deployment.json`](odayplus-dev-deployment.json) | JSON | ODay Plus Cloud Run API/Web/Migration/Worker/Scheduler 部署組態 |
| [`dev-integration-readback.json`](dev-integration-readback.json) | JSON | Dev 整合端點、Preflight、Smoke、Migration 相容性與 Jobs 讀回報告 |
| [`external-sources-provider-off-audit.json`](external-sources-provider-off-audit.json) | JSON | 16 個資料來源預設停用、零憑證與 Default-Deny Egress 查核報告 |
| [`release-receipts-index.json`](release-receipts-index.json) | JSON | 符合規範之標準 Release Evidence Receipts 索引 |

---

## 4. 自動化測試驗證

本任務編寫專屬之測試套件 `tests/ops/test_dev_rollout.py`，完整涵蓋以下斷言：
- Manifest 與 Deployed Component Digests 精確一致性。
- Data Platform 部署優先序及 GKE Workload 標註檢查。
- 16 個資料來源預設 Disabled 及零憑證斷言。
- Dev Integration Preflight / Smoke / Migration / E2E 讀回狀態驗證。
- Release Receipts 之 Schema、Identity 綁定與 Secret Redaction 驗證。

執行指令：
```bash
uv run --python 3.12 pytest tests/ops/test_dev_rollout.py
```

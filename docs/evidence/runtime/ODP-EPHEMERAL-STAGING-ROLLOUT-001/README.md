# ODP-EPHEMERAL-STAGING-ROLLOUT-001 — 建立 Ephemeral Staging 並完成全套 Release Rehearsal

- **任務 ID**: `ODP-EPHEMERAL-STAGING-ROLLOUT-001`
- **標題**: 建立 ephemeral staging 並完成全套 release rehearsal
- **負責人 (Owner)**: Codex2（由 Antigravity3 於 helper claim 執行）
- **審查人 (Reviewer)**: Claude
- **任務階段**: Wave 3 - Staging Rollout
- **完成日期**: 2026-08-25
- **來源依據**: [`docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md`](../../deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md)
- **相依任務**:
  - `ODP-EPHEMERAL-STAGING-IAC-001`: done · 實作 ephemeral staging 建立、隔離、TTL 與安全清理
  - `ODP-DEV-ROLLOUT-001`: done · 以同一 release digests 部署資料平台與 ODay Plus dev

---

## 1. 任務概述與目標

本任務為 Wave 3（環境落地階段）的關鍵發布演練里程碑，負責在短生命週期（Ephemeral）Staging 環境中，以不可變 Release Manifest（`docs/evidence/gates/RELEASE_MANIFEST.json`）之 Exact SHA-256 Image Digests 與合約雜湊，建立完整隔離環境（隔離資料庫、Schema、Bucket、租戶 Tenant、獨立 Service Accounts、IAM 綁定、Pub/Sub Topics/Subs 與預設暫停之 Cloud Scheduler Trigger），落實資料平台優先部署時序，完整演練 Migration、Live E2E、Worker/Scheduler Jobs、Backup Checkpoint & Restore Drill、Rollback Rehearsal，確保 16 個第三方與外部資料來源保持關閉（Default-Deny Egress、零憑證），確立失敗保留至 TTL 與成功保留至 Prod Closeout 清理原則，並產出符合標準且機密遮蔽（Secret Redacted）之 `staging-verified` Release Receipts。

---

## 2. 驗收條件達成證明

### 2.1 建立隔離 Staging 並部署同一 Digests (Precedence & Isolation Rule)

依據發布規劃 §4.1、§4.2 與 §7.1，系統以精確 release 標籤建立隔離短生命週期資源，並嚴格遵循資料平台先於應用系統的部署時序：
1. **第一階段：資料平台（GKE `oday-staging-odp-20260730-001` 命名空間）**
   - 執行遷移作業 `oday-data-platform-migrate-e496be62c47c`（標註 `oday.plus/execution-order: 00-migration`），套用 Alembic `0001 -> 0002`、Assisted Listing Intake DDL `001 -> 004`、PostgreSQL 執行期遷移 `000008`、建立 `data_plane` 控制 Schema，並寫入持久化遷移收據 `odp_runtime.deployment_migration_receipts`。
   - 部署有界定時作業 `oday-data-platform-bounded-daily`（CronJob: `0 1 * * *`），強制以 Release SHA 與 Image Digest 驗證持久化遷移收據，無有效收據立即 Fail-Closed。
   - 手動作業（`orders-history`, `trade-manual`, `device-log-manual`）保持 `suspend: true`。
2. **第二階段：ODay Plus 應用系統（Cloud Run & Ephemeral Staging 模組）**
   - 依據 `staging_lifecycle.py` 與 `infra/terraform/modules/ephemeral_staging` 建立獨立資料庫 `stg_odp_20260730_001_8f54e6c3`、DB User `stg_odp_20260730_001_8f54e6c3_app`、獨立 Secret Manager 密鑰、隔離 Storage Bucket `stg-odp-20260730-8f54e6c3-data-odayplus-runtime-20260825`、租戶分區 `tenant-odp-20260730-001-8f54e6c3`、獨立 Service Accounts（Runtime, Web, Worker）與 Pub/Sub 隊列。
   - 部署 Cloud Run API 服務 `stg-odp-20260730-001-8f54e6c3-api` 與 Web 服務 `stg-odp-20260730-001-8f54e6c3-web`。
   - 配置排程作業 Migration (`stg-odp-20260730-001-8f54e6c3-migration`)、Worker (`stg-odp-20260730-001-8f54e6c3-worker`) 與 Scheduler (`stg-odp-20260730-001-8f54e6c3-scheduler`)。
   - 配置 Cloud Scheduler Trigger `stg-odp-20260730-001-8f54e6c3-worker-trigger`，起始狀態嚴格保持 **PAUSED**。

詳見：[`data-platform-staging-deployment.json`](data-platform-staging-deployment.json) 與 [`odayplus-staging-deployment.json`](odayplus-staging-deployment.json)。

---

### 2.2 元件 Image 與資料契約完全符合 Release Manifest (Build-Once Guarantees)

全數 6 個元件強制綁定不可變 Release Manifest（`docs/evidence/gates/RELEASE_MANIFEST.json`）之 Exact SHA-256 Digest：
- **Release ID**: `odp-20260730-001`
- **Candidate Git SHA**: `e496be62c47c45d758681b8a4d3abfae16f1c96d`
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

詳見：[`staging-rollout-manifest-binding.json`](staging-rollout-manifest-binding.json)。

---

### 2.3 Migration / E2E / Worker / Scheduler / Backup / Restore / Rollback 全套 Rehearsal 通過

依據發布規劃 §7.2 與 §8.4，在隔離 Staging 環境中執行了完整的 Production-like 演練：
1. **Migration 相容性演練 (Expand Rehearsal)**:
   - 執行非破壞性 Expand Migration，資料庫 Schema 擴展後驗證舊版 API 讀取模式仍可正常運作，確保零停機相容性；Contract Migration 嚴格延後至後續獨立維護階段。
2. **Data Platform 契約讀回 (Data Contract Readback)**:
   - 資料平台 Snapshot Materialization 通過，契約讀回測試通過；確認資料僅來自核准之 Masked Snapshot，嚴禁掛載正式環境可寫資料庫。
3. **API / Web Smoke 與 Live E2E 門禁**:
   - 通過 Preflight 檢查與 `/health`, `/ready`, `/api/v1/openapi.json`, `/api/v1/health/data-mode`, `/api/v1/operator/intake/overview` 端點認證 Smoke。
   - 執行 Live E2E Gate：輔助物件進階流程、標準資料模型契約轉換、Operator Console RBAC 審核流程均通過。
4. **Worker / Scheduler Jobs 單次受控執行 (One-Shot Execution)**:
   - Migration Job 執行狀態為 `SUCCEEDED`；Worker 與 Scheduler 進行受控 One-Shot 執行，驗證冪等性（Idempotency）、重試（Retry）與 Dead-Letter Queue (DLQ) 隔離路徑。
   - Cloud Scheduler Trigger 起始保持 `PAUSED`。
5. **備份檢查點與還原演練 (Backup & Restore Drill)**:
   - 建立 Staging 資料庫備份檢查點 `stg-backup-odp-20260730-001-chk01`。
   - 執行還原演練至隔離驗證實例 `stg_odp_20260730_001_8f54e6c3_restore_test`，完成資料表結構、資料筆數與 Checksum 同步校驗（Parity Verified）。
6. **回滾演練 (Rollback Rehearsal)**:
   - 演練服務指針與容器映像切回前一穩定版本（Service/Revision Reversion）；驗證舊版服務在 Expand 後 Schema 下讀取無異常；演練不執行毀滅性 Down Migration；確認失敗軌跡可完整保留供除錯。

詳見：[`staging-rehearsal-readback.json`](staging-rehearsal-readback.json)。

---

### 2.4 16 個資料來源保持 Disabled、無憑證、無對外連線 (Default-Deny Egress)

依據發布規劃 §9，全數 16 個內部與外部資料來源均維持 disabled，且無憑證注入與對外 Egress 權限：
- **Manifest 設定**: `external_sources_expected_enabled = []`（長度為 0）。
- **內部作業來源 (8 項)**: `store_master_snapshot`, `machine_master_snapshot`, `machine_cycle_event`, `machine_status_event`, `transaction_event`, `price_schedule_snapshot`, `maintenance_work_order_event`, `customer_service_case_event`。
- **外部與補充來源 (8 項)**: `poi_snapshot`, `geocode_result_snapshot`, `admin_boundary_snapshot`, `listing_raw_snapshot`, `competitor_store_snapshot`, `demographics_snapshot`, `weather_daily_snapshot`, `store_opening_authority_snapshot`。
- **治理原則**: 所有來源未取得逐來源授權收據前嚴禁啟用抓取，Staging 部署預設禁止 Public Egress。

詳見：[`external-sources-provider-off-audit.json`](external-sources-provider-off-audit.json)。

---

### 2.5 失敗保留至 TTL、成功等待 Prod Closeout 清理 (Lifecycle & TTL Governance)

依據發布規劃 §4.1、§7.3 及 `product_ops/deployment/staging_lifecycle.py`：
- **資源標籤規範**: 所有 Staging 資源具備 `release_id: odp-20260730-001-8f54e6c3`, `tenant: tenant-odp-20260730-001-8f54e6c3`, `owner_task: ODP-EPHEMERAL-STAGING-ROLLOUT-001`, `ephemeral: true`, `managed_by: terraform`, `created_at: 2026-08-25-17-00-00`, `expires_at: 2026-08-26-17-00-00`。
- **失敗保留原則**: Staging 驗證若失敗，資源預設保留至 24 小時 TTL 以供除錯；若需延長需具名負責人及原因（最長 168 小時）。
- **成功保留與清理**: Staging 驗證成功後，環境維持就緒狀態，等待後續 Production Blue-Green 部署完成與收尾任務（`ODP-POSTDEPLOY-WATCH-CLOSEOUT-001`）進行精確標籤銷毀。
- **精確清理門禁**: 清理操作嚴格限定依 Release 標籤精確匹配，嚴禁寬泛 Wildcard 或整個 Project 清理；孤兒掃描器（Orphan Scanner）每小時定期掃描逾期資源。

詳見：[`staging-lifecycle-ttl-audit.json`](staging-lifecycle-ttl-audit.json)。

---

### 2.6 產生 Staging-Verified Receipts (Secret Redacted)

所有產出收據均符合 `delivery_toolchain/release/release_receipts.py` 標準，明確綁定 `release_id`, `release_sha`, `candidate_sha`, `manifest_ref`, `manifest_digest`，環境標註為 `staging`，階段為 `staging-verified`，並標記 `secret_values_redacted: true`：
- `ODP-STAGING-DEPLOY-RECEIPT-001` (部署收據，Stage: `staging-verified`, Environment: `staging`, Result: `pass`)
- `ODP-STAGING-REHEARSAL-RECEIPT-001` (演練驗證收據，Stage: `staging-verified`, Environment: `staging`, Result: `pass`)

詳見：[`release-receipts-index.json`](release-receipts-index.json)。

---

## 3. 證據檔案目錄索引

| 檔案名稱 | 格式 | 說明 |
|---|---|---|
| [`staging-rollout-manifest-binding.json`](staging-rollout-manifest-binding.json) | JSON | Release Manifest 與 6 個元件 Image Digest / 契約雜湊比對驗證紀錄 |
| [`data-platform-staging-deployment.json`](data-platform-staging-deployment.json) | JSON | Data Platform GKE `oday-staging-odp-20260730-001` 命名空間部署與遷移優先時序紀錄 |
| [`odayplus-staging-deployment.json`](odayplus-staging-deployment.json) | JSON | ODay Plus 隔離 DB/Bucket/Secrets/PubSub/Cloud Run/Paused Scheduler 部署組態 |
| [`staging-rehearsal-readback.json`](staging-rehearsal-readback.json) | JSON | Staging 全套演練報告（Migration/E2E/Jobs/Backup/Restore/Rollback） |
| [`external-sources-provider-off-audit.json`](external-sources-provider-off-audit.json) | JSON | 16 個資料來源預設停用、零憑證與 Default-Deny Egress 查核報告 |
| [`staging-lifecycle-ttl-audit.json`](staging-lifecycle-ttl-audit.json) | JSON | Staging 生命週期、TTL、失敗保留除錯、成功等待 Prod Closeout 與孤兒掃描報告 |
| [`release-receipts-index.json`](release-receipts-index.json) | JSON | 符合規範之標準 `staging-verified` Release Evidence Receipts 索引 |

---

## 4. 自動化測試驗證

本任務編寫專屬之測試套件 `tests/ops/test_staging_rollout.py`，完整涵蓋以下斷言：
- Release Manifest 與 Deployed Component Digests 精確一致性。
- Data Platform 部署優先序及 GKE Workload 標註檢查。
- ODay Plus 隔離資源（DB, Bucket, Secrets, Service Accounts, Pub/Sub, Cloud Run, Paused Scheduler）及標籤正確性。
- 全套 Rehearsal 讀回驗證（Migration 相容性、Data Contract、API/Web Smoke、Live E2E、Jobs、Backup & Restore Drill、Rollback Rehearsal）。
- 16 個資料來源預設 Disabled 及零憑證斷言。
- Staging 生命週期、TTL 策略、失敗保留與成功等待 Closeout 清理機制。
- Release Receipts 之 Schema、Identity 綁定（`stage="staging-verified"`, `environment="staging"`）與 Secret Redaction 驗證。

執行指令：
```bash
uv run --python 3.12 pytest tests/ops/test_staging_rollout.py
```

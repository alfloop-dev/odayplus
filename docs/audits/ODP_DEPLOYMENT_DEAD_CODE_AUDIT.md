# ODay Plus 部署、Proof、Gate 與 Scheduler 重複及廢 Code 盤點報告

- **文件 ID**: `ODP-DEPLOY-DEAD-CODE-AUDIT`
- **任務 ID**: `ODP-DEPLOY-DEAD-CODE-AUDIT-001`
- **任務階段**: Wave 0 — 基線與介面凍結 (Dead Code Audit)
- **盤點基準日期**: 2026-08-24
- **負責人 (Owner)**: Antigravity
- **審查人 (Reviewer)**: Codex
- **來源依據**: [`docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md`](../deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md)
- **執行原則**: **本任務只稽核不刪 code**。所有項目均以 caller、workflow、runtime unit、cron、GitHub Actions 具體呼叫路徑逐項證明，作為後續 Wave 1 / Wave 2 實作與刪除任務（特別是 `ODP-DEPLOY-DEAD-CODE-REMOVAL-001` 與 `ODP-RUNTIME-RELEASE-SINGLE-PATH-001`）之執行依據。

---

## 1. 執行摘要

本報告針對 ODay Plus 系統中與 **部署 (Deployment)**、**證明 (Proof & Evidence)**、**門禁 (Release Gates & Admission)**、**排程與工作 (Scheduler & Worker)** 以及 **容器與基礎設施 (Docker & IaC)** 相關之全部工作流程、腳本、配置與文件進行全面性盤點。

### 1.1 核心盤點結論

1. **唯一部署入口與旁路分析 (Bypass Entrypoint)**:
   - 現有 `.github/workflows/deploy-dev.yml` (`Runtime Release`) 是主要的部署工作流程，但現況在 `deploy` job 內部現場執行 `docker build`，違反「Build Once、以 Digest 為唯一部署身分」原則。
   - `product_ops/deployment/deploy_cloud_run_waji.sh` 可在本地或 runner 直接被呼叫，若環境具備 GCP 權限則可能繞過 Supervisor 發行之 release lease。
   - `.github/workflows/promote-dev-to-main.yml` 僅為代碼由 `dev` 晉級至 `main` 之 PR 自動合併流程，不是 Production 部署管線；兩者責任界限須明確分離。
2. **舊版 External Proof Follow-up 殘留 (Legacy External Proof)**:
   - 歷史 PR #82 所衍生之 `.github/workflows/external-proof-followup.yml` 及 13 支 `check_external_proof_*.py` 腳本，在最新 `origin/dev` 上**已經完全移除（檔案已不存在）**。
   - 然而，`docs/evidence/` 下仍殘留 4 個完全孤立的 JSON/MD 佇列與狀態板檔案（如 `PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json`、`EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json` 等），且 4 份關閉文件（如 `PRODUCT_RELEASE_CLOSEOUT_MANIFEST.md` 等）仍充斥對已刪除腳本之幽靈引用。依規劃書原則，這些殘留文件應在 Wave 2 正式刪除。
3. **門禁與入場機制重複與循環依賴 (Admission & Gate Redundancy)**:
   - `delivery_toolchain/release/check_runtime_admission.py` 現況僅檢查 `task_id` 與 `release_lease` 之正規表達式（Shape-only），尚未綁定 Supervisor 簽名與 CAS 防重放狀態。
   - `check_runtime_admission.py` 與 `check_release_gate_registry.py` 目前在部署 dev/staging 前硬性要求 Gate 0–6 全部通過，造成「staging 才能產生的驗證收據反過來阻擋 staging 部署」的循環依賴。
   - `delivery_toolchain/e2e/verify_deployment_health_backup_rollback.py` 在 `deploy-dev.yml` 中啟動本地 Docker Compose (SQLite) 產生測試收據，無法代表真實 Cloud Run / PostgreSQL 部署健康；應在 Ephemeral Staging 建立後由真實環境演練取代。
4. **容器定義與 Dockerfile 漂移 (Docker Redundancy)**:
   - 倉庫內存在過期陳舊之 `infra/docker/Dockerfile.api`（暴露 8080 port、寫死 pip install 依賴）與 `infra/docker/Dockerfile.web`（暴露 8080 port），已被正規之 `infra/docker/api.Dockerfile`（暴露 8000 port、由 pyproject.toml 同步）與 `infra/docker/web.Dockerfile`（暴露 3000 port）取代。
   - `infra/docker/docker-compose.yml` 引用過期之 `Dockerfile.api`，與根目錄標準 `docker-compose.yml` 重複且行為不一致。
5. **排程與工作處理器 (Scheduler & Worker)**:
   - 本地開發使用根目錄 `docker-compose.yml` 啟動 `apps/scheduler/oday_scheduler` 與 `apps/worker/oday_worker`。
   - 雲端環境使用 GCP Cloud Scheduler 定期 HTTP 呼叫觸發 Cloud Run Jobs，並以 `product_ops/deployment/cloud_run_job_entrypoint.py` 執行 bounded worker/scheduler。兩者分工明確，雲端部署腳本之 traffic/trigger rollback 機制健全，應予以保留並延伸至 Production Blue-Green。

---

## 2. 繞過 Runtime Release 的入口與旁路盤點

依據驗收條件第 3 項，盤點所有可能繞過唯一 `Runtime Release` 發布流程之直接/間接入口：

| 入口類型 | 檔案 / 路徑 | 現況行為 | 風險與旁路機制 | 後續處置方案 |
|---|---|---|---|---|
| **本機/腳本直接發布** | `product_ops/deployment/deploy_cloud_run_waji.sh` | 腳本只依賴環境變數（`ODP_DEPLOY_ENV`, `ODAY_RELEASE_SHA`, `GCP_PROJECT` 等），只要終端具備 `gcloud` 認證即可在本地執行部署。 | 若開發者在終端手動執行，可繞過 GitHub Actions 之 `admission` job，導致未持有有效 Supervisor Release Lease 即可變更 Cloud Run 服務。 | **重構 (REPLACE / REFACTOR)**：在 `ODP-PROD-BLUEGREEN-PRIMITIVES-001` 與 `ODP-RUNTIME-RELEASE-SINGLE-PATH-001` 中，將 release lease 與 manifest digest 作為腳本強制校驗參數，無簽名 lease 時直接 fail-closed。 |
| **代碼晉級工作流** | `.github/workflows/promote-dev-to-main.yml` | 當 `dev` 分支 CI 通過時觸發，驗證 `make product-release-gate` 後建立 `dev -> main` PR 並 auto-merge。 | 容易被誤認為正式環境部署入口。實際上該 workflow 僅執行 Git branch 合併，完全不觸及 GCP 部署。 | **保留並釐清 (KEEP & CLARIFY)**：保留作為 Code Promotion 自動化，但在文件與註解中嚴格宣告：**Merge 到 main 不等於 Production 部署**；Production 部署一律由唯一 Runtime Release 觸發。 |
| **設計合約驗證入口** | `.github/workflows/assisted-intake-design-validation.yml` | 包含 `workflow_dispatch` 手動觸發入口與 PR trigger，啟動本地 PostgreSQL 驗證 OpenAPI 與 SQL schema。 | 純合約驗證工具，不包含任何 GCP 部署指令或 release authority 簽發，不構成部署旁路。 | **保留 (KEEP)**：維持作為子系統設計合約驗證 gate。 |
| **手動 Cloud Run 變更** | GCP Console / Direct gcloud | 操作者手動透過 GCP 控制台切換流量或部署 Revision。 | 人工帶外操作將導致 Git 與 live runtime 狀態漂移。 | **權限治理 (GOVERNANCE)**：依 Rollout Plan 第 8 節與第 16 節，限制 WIF Service Account 為唯一的部署者，所有正式流量切換必須透過單一發布工作流程。 |

---

## 3. 舊版 External Proof Follow-up 殘留與幽靈依賴盤點

依據驗收條件第 3 項，詳細追查歷史 PR #82 External Proof Follow-up 之現況代碼與文件殘留：

### 3.1 程式碼與 Workflow 現況（證明已在 `dev` 移除）

經全庫檔案掃描與檢索，以下舊版 External Proof 核心元件**均已不存在於 `dev` 分支**：
- ❌ `.github/workflows/external-proof-followup.yml`（已移除）
- ❌ `delivery_toolchain/e2e/check_external_proof_handback_template.py`（已移除）
- ❌ `delivery_toolchain/e2e/update_external_proof_handback_status_board.py`（已移除）
- ❌ `delivery_toolchain/e2e/check_external_proof_handback_status_board.py`（已移除）
- ❌ `delivery_toolchain/e2e/check_external_proof_acceptance_readiness.py`（已移除）
- ❌ `delivery_toolchain/e2e/check_external_proof_handback_artifact.py`（已移除）
- ❌ `delivery_toolchain/e2e/check_external_proof_handback_bundle.py`（已移除）
- ❌ `delivery_toolchain/e2e/sync_external_proof_fleet_issues.py`（已移除）
- ❌ `delivery_toolchain/e2e/check_external_proof_issue_sync.py`（已移除）
- ❌ `delivery_toolchain/e2e/check_external_proof_live_blockers.py`（已移除）
- ❌ `delivery_toolchain/e2e/check_external_proof_fleet_notifications.py`（已移除）
- ❌ `delivery_toolchain/e2e/check_external_proof_issue_handback_scan.py`（已移除）
- ❌ `delivery_toolchain/e2e/sync_external_proof_escalation_comments.py`（已移除）
- ❌ `delivery_toolchain/e2e/check_external_proof_followup_workflow.py`（已移除）
- ❌ `delivery_toolchain/e2e/check_product_go_no_go.py`（已移除）

### 3.2 殘留之孤立狀態檔案與幽靈文件引用（待 Wave 2 刪除）

雖然執行腳本已刪除，但在 `docs/evidence/` 目錄中仍殘留大量定義檔，且現存文件仍描述這些不存在的腳本：

| 殘留檔案路徑 | 類型 | 內容描述 | 幽靈引用與現況分析 | 處置建議 |
|---|---|---|---|---|
| `docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json` | 孤立 JSON | 記錄 7 個外部證明任務（#132–#138，包含供應商認證、圖資、Staging 證明等）。 | 無任何 active code 讀取或寫入此 JSON；所引用的 issue #132–#138 追蹤腳本均已刪除。 | **刪除 (DELETE)**（於 `ODP-DEPLOY-DEAD-CODE-REMOVAL-001`） |
| `docs/evidence/EXTERNAL_PROOF_HANDBACK_TEMPLATE.json` | 孤立 JSON | 定義外部證明 handback 格式之 JSON Schema 範本。 | 無 active code 引用，原對應之 `check_external_proof_handback_template.py` 已刪除。 | **刪除 (DELETE)**（於 `ODP-DEPLOY-DEAD-CODE-REMOVAL-001`） |
| `docs/evidence/EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json` | 孤立 JSON | 外部證明交付狀態追蹤看板。 | 無 active code 引用，原對應之 `check_external_proof_handback_status_board.py` 已刪除。 | **刪除 (DELETE)**（於 `ODP-DEPLOY-DEAD-CODE-REMOVAL-001`） |
| `docs/evidence/EXTERNAL_PROOF_FLEET_PICKUP_BOARD.md` | 孤立 MD | 描述 #132–#138 領取與交付說明之說明文件。 | 內容全部基於已退役之 PR #82 與已刪除之 sync 腳本。 | **刪除 (DELETE)**（於 `ODP-DEPLOY-DEAD-CODE-REMOVAL-001`） |
| `docs/evidence/PRODUCT_RELEASE_CLOSEOUT_MANIFEST.md` | 陳舊文件 | 記錄 PR #82 關閉檢查清單。 | 表格第 30–41 行列出 13 個已刪除之 `check_external_proof_*.py` 腳本與 `external-proof-followup.yml`。 | **重構/精簡 (REPLACE / UPDATE)**：清除已刪除腳本指令，轉型為歷史封存紀錄。 |
| `docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md` | 陳舊文件 | 記錄 PR #82 Human/Ops GO/NO-GO 檢核表。 | 表格第 59–68 行要求執行已刪除之 10 個外部證明檢查指令。 | **重構/精簡 (REPLACE / UPDATE)**：替換為基於新 Gate 0–6 分階段 Gate 狀態之檢核表。 |
| `docs/evidence/PRODUCT_RELEASE_CLOSEOUT_PLAYBOOK.md` | 陳舊文件 | PR #82 Closeout 操作手冊。 | 內含大量已刪除之 external proof 指令。 | **重構/精簡 (REPLACE / UPDATE)**。 |
| `docs/evidence/PRODUCT_RELEASE_CLOSEOUT_PICKUP_BOARD.md` | 陳舊文件 | PR #82 領取狀態看板。 | 內含大量已刪除之 external proof 指令。 | **重構/精簡 (REPLACE / UPDATE)**。 |

---

## 4. 全系統元件逐項盤點與使用證明 (Item-by-Item Usage Evidence)

本節針對倉庫內所有部署、Proof、Gate、Scheduler、Docker 與 IaC 相關元件，逐一列出其呼叫者、Workflow 參照、執行單元、測試涵蓋與處置判定。

### 4.1 GitHub Workflows (`.github/workflows/`)

| 元件名稱與路徑 | 觸發事件 / 職責 | 呼叫者與相依分析 (Usage Evidence) | 處置判定 | 承接 Wave 任務 |
|---|---|---|---|---|
| `.github/workflows/deploy-dev.yml` (`Runtime Release`) | `workflow_dispatch` (inputs: `environment`, `release_sha`, `task_id`, `release_lease`)<br>負責目前 dev 與 staging 之 Cloud Run 部署。 | **Caller**: 由 Supervisor / Human 觸發。<br>**Steps**: 呼叫 `check_runtime_admission.py`、`verify_deployment_health_backup_rollback.py`、`deploy_cloud_run_waji.sh`、`check_remote_staging_proof.py`。<br>**問題**: 現場 build 映像檔；缺乏 production blue-green 路徑；門禁存在循環依賴。 | **重構 (REPLACE)** | `ODP-RUNTIME-RELEASE-SINGLE-PATH-001` (Wave 2) |
| `.github/workflows/promote-dev-to-main.yml` | `workflow_run` (CI completed on `dev`)<br>自動建立/合併 `dev -> main` PR。 | **Caller**: GitHub Actions 內部事件。<br>**Steps**: 執行 `make product-release-gate`、`gh pr create`、`gh pr merge`。<br>**判定**: 純代碼合併流程，無部署副作用。 | **保留 (KEEP)** | 維持現狀 |
| `.github/workflows/ci.yml` | `push` (main, dev), `pull_request`, `merge_group`<br>全系統持續整合主工作流。 | **Caller**: GitHub PR 與 Push 事件。<br>**Jobs**: `change-scope`, `orchestrator`, `product`, `performance-gate`, `product-e2e-gate`。<br>**判定**: 核心品質門禁，不可或缺。 | **保留 (KEEP)** | 維持現狀 |
| `.github/workflows/merge-queue-review-gate.yml` | `merge_group` (checks_requested)<br>為 Merge Queue 重新驗證並蓋印 `task-review-gate`。 | **Caller**: GitHub Merge Queue。<br>**Steps**: 透過 GitHub API 讀取 PR head 之 review 狀態並蓋印至 merge ref。 | **保留 (KEEP)** | 維持現狀 |
| `.github/workflows/tooling-scope-review-gate.yml` | `pull_request` (branches: dev)<br>針對純開發工具改動自動審查。 | **Caller**: GitHub PR。<br>**Steps**: 呼叫 `classify_change_review_scope.py`，若為純 tooling 則自動 stamp `task-review-gate`。 | **保留 (KEEP)** | 維持現狀 |
| `.github/workflows/emgi-consumer-boundary.yml` | `pull_request` (branches: dev)<br>驗證 EMGI 資料平台邊界。 | **Caller**: GitHub PR。<br>**Steps**: 執行 `validate_emgi_consumer_boundary.mjs` 與 `test_emgi_consumer_boundary.mjs`。 | **保留 (KEEP)** | 維持現狀 |
| `.github/workflows/assisted-intake-design-validation.yml` | `pull_request`, `workflow_dispatch`<br>驗證 Assisted Intake 設計與 OpenAPI/SQL。 | **Caller**: GitHub PR。<br>**Steps**: 執行 `validate_assisted_listing_intake_design.py`、`build_validate_assisted_listing_intake.py`、Redocly lint、PostgreSQL schema apply。 | **保留 (KEEP)** | 維持現狀 |

---

### 4.2 部署與執行期腳本 (`product_ops/deployment/`)

| 元件名稱與路徑 | 主要責任與對外介面 | 呼叫者與相依分析 (Usage Evidence) | 處置判定 | 承接 Wave 任務 |
|---|---|---|---|---|
| `product_ops/deployment/deploy_cloud_run_waji.sh` | Cloud Run 服務 (API, Web) 與 Jobs (Migration, Worker, Scheduler) 部署主腳本。 | **Caller**: `.github/workflows/deploy-dev.yml` (line 217)。<br>**Dependencies**: 呼叫 `cloud_run_release_traffic.sh`、`validate_cloud_run_live_deployment.py`、`check_live_e2e_gate.py`、`cosign`、`docker`、`gcloud`。<br>**問題**: 內含 `docker build`；流量推進僅支援單一環境 100% 切換，尚未支援 production 0% green 驗證後再 100% blue-green 切換。 | **重構 (REPLACE / REFACTOR)**：抽離 build 步驟（改為接收 release manifest digest）；擴充支援 prod blue-green。 | `ODP-PROD-BLUEGREEN-PRIMITIVES-001` (Wave 1), `ODP-RUNTIME-RELEASE-SINGLE-PATH-001` (Wave 2) |
| `product_ops/deployment/cloud_run_job_entrypoint.py` | Cloud Run Job 容器內進入點，支援 `migrate`, `worker`, `scheduler` 子命令。 | **Caller**: 由 Cloud Run Jobs 在容器啟動時以 `python product_ops/deployment/cloud_run_job_entrypoint.py <subcommand>` 執行。<br>**Tests**: `tests/ops/test_cloud_run_job_entrypoint.py` 完整覆蓋。<br>**判定**: 結構化輸出 JSON receipt、租約管理與錯誤碼封裝健全。 | **保留 (KEEP)** | 維持現狀 |
| `product_ops/deployment/cloud_run_release_traffic.sh` | Bash 流量控制與 rollback 模組。 | **Caller**: 由 `deploy_cloud_run_waji.sh` source (line 76)。<br>**Functions**: `capture_service_traffic`, `promote_service_traffic`, `restore_service_traffic`, `rollback_release_traffic`, `capture_scheduler_trigger`, `restore_scheduler_trigger`。<br>**判定**: 提供失敗自動 rollback 核心能力。 | **保留並擴充 (KEEP & EXPAND)** | `ODP-PROD-BLUEGREEN-PRIMITIVES-001` (Wave 1) |
| `product_ops/deployment/cloud_run_traffic.py` | Python 輔助工具，解析 Cloud Run JSON 描述檔並產生流量復原參數。 | **Caller**: `cloud_run_release_traffic.sh`。<br>**Tests**: `tests/ops/test_cloud_run_live_deployment.py`。<br>**判定**: 核心流量解析工具。 | **保留 (KEEP)** | `ODP-PROD-BLUEGREEN-PRIMITIVES-001` (Wave 1) |
| `product_ops/deployment/cloud_scheduler_trigger.py` | Python 輔助工具，解析 Cloud Scheduler JSON 描述檔、產生復原參數並比對漂移。 | **Caller**: `cloud_run_release_traffic.sh`。<br>**Tests**: `tests/ops/test_cloud_run_live_deployment.py`。<br>**判定**: 核心排程設定保護工具。 | **保留 (KEEP)** | `ODP-PROD-BLUEGREEN-PRIMITIVES-001` (Wave 1) |
| `product_ops/deployment/validate_cloud_run_live_deployment.py` | Cloud Run 部署各階段驗證器 (preflight, compatibility-smoke, smoke, jobs-smoke)。 | **Caller**: `deploy-dev.yml` (line 196), `deploy_cloud_run_waji.sh` (lines 79, 266, 294, 361, 571)。<br>**Tests**: `tests/ops/test_cloud_run_live_deployment.py`。<br>**判定**: 產出無機密之標準 JSON 收據，符合零信任驗證原則。 | **保留並重用 (KEEP)** | `ODP-RELEASE-EVIDENCE-RECEIPTS-001` (Wave 1) |

---

### 4.3 門禁檢查、收據與驗收工具 (`delivery_toolchain/release/`, `delivery_toolchain/e2e/`)

| 元件名稱與路徑 | 主要責任與對外介面 | 呼叫者與相依分析 (Usage Evidence) | 處置判定 | 承接 Wave 任務 |
|---|---|---|---|---|
| `delivery_toolchain/release/check_runtime_admission.py` | 發布入場檢查，驗證 release_sha、task_id、lease 及 Gate 0–6 registry。 | **Caller**: `deploy-dev.yml` (line 61)。<br>**問題**: Lease 僅做 regex 檢查（非簽名授權）；要求 Gate 0–6 全過造成循環依賴；僅支援 dev/staging。 | **替換 (REPLACE)**：由具簽名與 CAS 狀態機之權威驗證器取代。 | `ODP-RELEASE-ADMISSION-AUTHORITY-001` (Wave 0) |
| `delivery_toolchain/e2e/check_release_gate_registry.py` | 靜態 Gate 0–6 機器可讀註冊表驗證器。 | **Caller**: `Makefile` (`make release-gate-registry`), `check_runtime_admission.py`, `check_product_release_gate.py`。<br>**Tests**: `tests/e2e/test_release_gate_registry.py`。<br>**問題**: 缺少分階段 (candidate-built, dev-verified, staging-verified, prod-admitted) 狀態機支援。 | **重構 (REPLACE / REFACTOR)**：升級為多階段 Gate Registry 驗證器。 | `ODP-RELEASE-MANIFEST-GATES-001` (Wave 0) |
| `delivery_toolchain/e2e/check_product_release_gate.py` | 產品發布門禁總成，整合 gate registry 與 deterministic E2E 收據。 | **Caller**: `Makefile` (`make product-e2e-gate`, `make product-release-gate`), `.github/workflows/promote-dev-to-main.yml` (line 50)。 | **重構 (REPLACE / REFACTOR)**：配合新 gate registry 調整參數與驗證邏輯。 | `ODP-RELEASE-MANIFEST-GATES-001` (Wave 0) |
| `delivery_toolchain/e2e/check_live_e2e_gate.py` | 部署後線上即時 E2E 驗收門禁，透過真實 HTTP 操作驅動完整業務路徑與 Worker 執行。 | **Caller**: `deploy_cloud_run_waji.sh` (line 624)。<br>**Tests**: `tests/e2e/test_live_e2e_gate.py`。<br>**判定**: 核心線上驗收工具，fail-closed 設計完善。 | **保留 (KEEP)** | `ODP-RELEASE-EVIDENCE-RECEIPTS-001` (Wave 1) |
| `delivery_toolchain/e2e/check_live_production_data.py` | 直接對接 PostgreSQL 資料庫，核對真實資料平面實體與特徵。 | **Caller**: `check_live_e2e_gate.py` (line 53)。<br>**Tests**: `tests/e2e/test_live_production_data_gate.py`。<br>**判定**: 核心資料平面證明工具。 | **保留 (KEEP)** | `ODP-RELEASE-EVIDENCE-RECEIPTS-001` (Wave 1) |
| `delivery_toolchain/e2e/check_remote_staging_proof.py` | 檢查 Staging 環境端點健康與 Release SHA 一致性。 | **Caller**: `deploy-dev.yml` (line 226)。<br>**Tests**: `tests/e2e/test_remote_staging_proof_checker.py`。<br>**判定**: 保留並重構為適用於短生命週期 Ephemeral Staging 之驗證器。 | **重構 (REPLACE / REFACTOR)** | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` (Wave 3) |
| `delivery_toolchain/e2e/verify_deployment_health_backup_rollback.py` | 啟動本地 Docker Compose (SQLite) 執行備份與回滾演練。 | **Caller**: `deploy-dev.yml` (line 87)。<br>**問題**: 於 CI runner 內部使用 SQLite 模擬，與真實 GCP Cloud SQL/Cloud Run 架構脫節。 | **廢除 / 替換 (REPLACE / RETIRE)**：由真實 Ephemeral Staging 之備份還原演練收據取代。 | `ODP-EPHEMERAL-STAGING-IAC-001` (Wave 1) |
| `delivery_toolchain/e2e/run_product_e2e.sh` | 本地 / CI 確定性 E2E 測試主執行檔 (Docker Compose + Playwright + Pytest)。 | **Caller**: `Makefile` (`make product-e2e-gate`), `ci.yml` (line 313)。<br>**判定**: CI 合併 dev 必要之確定性驗證。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/e2e/run_python_e2e_tests.py` | 執行 Python E2E 測試套件並產生結果。 | **Caller**: `run_product_e2e.sh` (line 138)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/e2e/record_playwright_results.py` | 擷取 Playwright 執行輸出並結構化寫入 JSON 證據。 | **Caller**: `run_product_e2e.sh` (line 123)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/e2e/generate_product_e2e_receipt.py` & `product_e2e_receipt.py` | 彙總產生並校驗 `PRODUCT_E2E_EXECUTION_RECEIPT.json`。 | **Caller**: `run_product_e2e.sh` (line 141), `check_product_release_gate.py` (line 56)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/e2e/seed_product_e2e_data.py` | 為 E2E 測試注入確定性測試資料。 | **Caller**: `run_product_e2e.sh` (line 77)。<br>**Tests**: `tests/e2e/test_seed_product_e2e_data.py`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/e2e/worker_heartbeat.py` | 測試 Worker 背景心跳與健康狀態。 | **Caller**: E2E 測試腳本。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/e2e/check_product_grade_ci_gates.py` | 靜態校驗 Operator Console 40 個畫面標籤合約與 HTML SHA256。 | **Caller**: CI 測試 `tests/e2e/test_package10_product_grade_ci_gate.py`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/e2e/_release_target.py` & `_support.py` | E2E 工具鏈共用函式庫。 | **Caller**: `delivery_toolchain/e2e/*.py` 多處 import。 | **保留 (KEEP)** | 維持現狀 |

---

### 4.4 安全、治理與合約工具 (`delivery_toolchain/security/`, `governance/`, `openapi/`, `git/`, `github/`)

| 元件名稱與路徑 | 主要責任與對外介面 | 呼叫者與相依分析 (Usage Evidence) | 處置判定 | 承接 Wave 任務 |
|---|---|---|---|---|
| `delivery_toolchain/security/secret_scan.py` | 原始碼機密掃描。 | **Caller**: `deploy-dev.yml` (line 176), `tests/security/test_supply_chain_security_gate.py`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/security/sast_scan.py` | Python 靜態安全性分析 (Bandit)。 | **Caller**: `deploy-dev.yml` (line 179), `tests/security/test_supply_chain_security_gate.py`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/security/generate_sbom.py` | CycloneDX SBOM 生成工具。 | **Caller**: `deploy-dev.yml` (line 182), `tests/security/test_supply_chain_security_gate.py`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/security/generate_oss_notice.py` | 第三方授權聲明生成工具。 | **Caller**: `tests/security/test_oss_notice.py`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/security/attestation.py` | In-toto / SLSA 證明產生器。 | **Caller**: `tests/security/test_supply_chain_security_gate.py`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/security/sign_images.sh` | Cosign 映像檔簽章與驗章封裝。 | **Caller**: `deploy_cloud_run_waji.sh` (lines 232, 524)。 | **保留 (KEEP)** | `ODP-RELEASE-MANIFEST-GATES-001` (Wave 0) |
| `delivery_toolchain/governance/check_code_boundaries.py` | 產品程式碼與交付工具邊界檢查。 | **Caller**: `Makefile` (`make boundary-check`), `ci.yml` (line 91)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/governance/check_config_wiring.py` | 設定檔與常數連結檢查。 | **Caller**: `ci.yml` (line 99), `tests/tooling/test_config_wiring.py`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/governance/check_orchestrator_config.py` | Supervisor 配置檢查。 | **Caller**: `ci.yml` (line 98)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/governance/classify_change_review_scope.py` | PR 變更路徑分類器 (tooling vs product)。 | **Caller**: `ci.yml` (line 61), `tooling-scope-review-gate.yml` (line 30)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/governance/validate_assisted_listing_intake_design.py` | 子系統設計合約交叉驗證器。 | **Caller**: `assisted-intake-design-validation.yml` (line 76)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/governance/validate_emgi_consumer_boundary.mjs` | EMGI Producer/Consumer 邊界檢查。 | **Caller**: `emgi-consumer-boundary.yml` (line 23)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/git/task_start.sh` | 從 `dev` 建立標準 task branch。 | **Caller**: Auto Worker / Developer 進入 task 標準工具。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/git/task_finalize.sh` | 推送 task branch、開啟 PR 並自動提交 review。 | **Caller**: Auto Worker / Developer 提交審查標準工具。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/git/worker_commit.py` | Worker 安全 commit 包裝器（隔離 index、範圍校驗）。 | **Caller**: Auto Worker commit 標準工具。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/git/check_commit_scope.py` | Git hook / 提交範圍防護。 | **Caller**: `.githooks/pre-commit`, `worker_commit.py`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/git/check_commit_trailers.py` | Commit message trailers (`Task-ID`, `LLM-Agent`, `Reviewer`) 驗證。 | **Caller**: `.githooks/commit-msg`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/git/check_task_delivery_identity.py` | 驗證 Task 與 PR delivery identity。 | **Caller**: `scripts/ai_status.py`, `task_finalize.sh`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/github/apply_branch_protection.py` | GitHub 分支保護規則套用工具。 | **Caller**: 維運腳本 / CI。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/github/check_pr_merge_eligibility.py` | PR 合併資格檢查器。 | **Caller**: `tests/tooling/test_pr_merge_eligibility.py`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/openapi/check_drift.py` | OpenAPI 合約漂移檢查器。 | **Caller**: `Makefile` (`make api-contract`), `ci.yml` (line 189)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/openapi/export_openapi.py` & `generate_client.py` | OpenAPI 匯出與 TypeScript client 產生器。 | **Caller**: `Makefile` (`make api-contract-refresh`)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/openapi/build_validate_assisted_listing_intake.py` | 子系統 OpenAPI 建置與結構驗證器。 | **Caller**: `assisted-intake-design-validation.yml` (line 143)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/load/assisted_listing_intake/run.py` | 容量與負載基準測試器。 | **Caller**: `ci.yml` (line 250 in `performance-gate`)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/chaos/assisted_listing_intake/run.py` | 混沌演練執行器。 | **Caller**: 演練腳本。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/release/assisted_listing_intake/` (`config.py`, `drills.py`, `gates.py`, `run.py`) | 子系統特定之發布演練與 Canary 判定工具。 | **Caller**: `tests/ops/test_assisted_listing_intake_release.py`。<br>**判定**: 屬模組專屬演練工具，非全系統 release entrypoint。 | **保留 (KEEP)** | 維持現狀 |

---

### 4.5 容器定義與基礎設施 (`infra/docker/`, `infra/terraform/`, `infra/k8s/`, `infra/mlflow/`, `docker-compose*.yml`)

| 元件名稱與路徑 | 主要責任與對外介面 | 呼叫者與相依分析 (Usage Evidence) | 處置判定 | 承接 Wave 任務 |
|---|---|---|---|---|
| `infra/docker/api.Dockerfile` | ODay Plus API 正規 Dockerfile (FastAPI, 8000 port, pyproject 依賴)。 | **Caller**: `deploy_cloud_run_waji.sh` (line 239), 根目錄 `docker-compose.yml` (line 27)。 | **保留 (KEEP)** | `ODP-RELEASE-MANIFEST-GATES-001` (Wave 0) |
| `infra/docker/web.Dockerfile` | ODay Plus Web 正規 Dockerfile (Next.js, 3000 port, multi-stage)。 | **Caller**: `deploy_cloud_run_waji.sh` (line 519), 根目錄 `docker-compose.yml` (line 80)。 | **保留 (KEEP)** | `ODP-RELEASE-MANIFEST-GATES-001` (Wave 0) |
| `infra/docker/worker.Dockerfile` | Worker 正規 Dockerfile (ODay worker / Cloud Run job)。 | **Caller**: `deploy_cloud_run_waji.sh` (line 240)。 | **保留 (KEEP)** | `ODP-RELEASE-MANIFEST-GATES-001` (Wave 0) |
| `infra/docker/scheduler.Dockerfile` | Scheduler 正規 Dockerfile (Cloud Run scheduler job)。 | **Caller**: `deploy_cloud_run_waji.sh` (line 241)。 | **保留 (KEEP)** | `ODP-RELEASE-MANIFEST-GATES-001` (Wave 0) |
| `infra/docker/data-platform.Dockerfile` | Data Platform / EMGI runtime Dockerfile。 | **Caller**: `DPF-EMGI-LIVE-ROLLOUT-001`。 | **保留 (KEEP)** | `DPF-EMGI-LIVE-ROLLOUT-001` (Wave 1) |
| `infra/docker/Dockerfile.api` | **過期 Dockerfile** (暴露 8080 port，寫死 pip install 套件列表，容易造成 runtime 漂移)。 | **Caller**: 僅被過期之 `infra/docker/docker-compose.yml` 引用；`deploy_cloud_run_waji.sh` 已改用 `api.Dockerfile`。 | **刪除 (DELETE)** | `ODP-DEPLOY-DEAD-CODE-REMOVAL-001` (Wave 2) |
| `infra/docker/Dockerfile.web` | **過期 Dockerfile** (暴露 8080 port)。 | **Caller**: 無 active caller；`deploy_cloud_run_waji.sh` 已改用 `web.Dockerfile`。 | **刪除 (DELETE)** | `ODP-DEPLOY-DEAD-CODE-REMOVAL-001` (Wave 2) |
| `infra/docker/docker-compose.yml` | **過期 Docker Compose** (僅包含 postgres 與引用 `Dockerfile.api` 的 api)。 | **Caller**: `infra/docker/README.md`。<br>**問題**: 與根目錄功能完整之 `docker-compose.yml` 重複且引用過期 Dockerfile。 | **刪除或更新 (DELETE / RETIRE)** | `ODP-DEPLOY-DEAD-CODE-REMOVAL-001` (Wave 2) |
| `infra/docker/docker-compose.e2e.yml` | E2E 測試用 Docker Compose (包含 api, web, postgres, source-stub)。 | **Caller**: `delivery_toolchain/e2e/run_product_e2e.sh` (line 24)。 | **保留 (KEEP)** | 維持現狀 |
| `docker-compose.yml` (根目錄) | 本地開發完整 multi-service stack (migrate, api, worker, scheduler, web)。 | **Caller**: 開發者手動 `docker compose up --build`。 | **保留 (KEEP)** | 維持現狀 |
| `infra/terraform/` (`*.tf`, `audit/`, `env/`) | Cloud Run、Cloud SQL、KMS、IAM、GCS 等長期基礎設施 Terraform 模組。 | **Caller**: IaC 佈署流程；`tests/ops/test_runtime_config_code_closeout.py`。<br>**判定**: 屬共用底層 IaC，應保留並作為 Ephemeral Staging 基礎。 | **保留 (KEEP)** | `ODP-EPHEMERAL-STAGING-IAC-001` (Wave 1) |
| `infra/cloudbuild/README.md` | Cloud Build 說明文件（空白 stub）。 | **Caller**: 無；現行發布全部走 GitHub Actions (WIF)，不使用 Cloud Build。 | **歸檔 / 刪除 (RETIRE)** | `ODP-DEPLOY-DEAD-CODE-REMOVAL-001` (Wave 2) |
| `infra/k8s_optional/README.md` | 空白 stub 目錄。 | **Caller**: 無。 | **刪除 (DELETE)** | `ODP-DEPLOY-DEAD-CODE-REMOVAL-001` (Wave 2) |
| `infra/k8s/data-platform/` (`render.py`, `workloads.yaml.tpl`, `deployment_runtime.py`, `status_mapping.prod.json`) | Data Platform GKE 部署範本與渲染腳本。 | **Caller**: Data Platform EMGI 部署。 | **保留 (KEEP)** | `DPF-EMGI-LIVE-ROLLOUT-001` (Wave 1) |
| `infra/mlflow/` (`Dockerfile`, `entrypoint.py`, `runtime.py`, `healthcheck.py`) | MLflow Tracking Server 容器定義。 | **Caller**: MLflow 部署。 | **保留 (KEEP)** | 維持現狀 |

---

### 4.6 排程、Worker 與資料管線 (`apps/scheduler/`, `apps/worker/`, `pipelines/`, `product_ops/modeling/`)

| 元件名稱與路徑 | 主要責任與對外介面 | 呼叫者與相依分析 (Usage Evidence) | 處置判定 | 承接 Wave 任務 |
|---|---|---|---|---|
| `apps/scheduler/oday_scheduler` | 本地開發定期 enqueue 排程工作迴圈。 | **Caller**: 根目錄 `docker-compose.yml` (line 66)。 | **保留 (KEEP)** | 維持現狀 |
| `apps/worker/oday_worker` | 本地開發背景工作消費者。 | **Caller**: 根目錄 `docker-compose.yml` (line 54)。 | **保留 (KEEP)** | 維持現狀 |
| `pipelines/data_quality/gates.py` | 內部資料品質檢核門禁。 | **Caller**: Ingestion pipelines。 | **保留 (KEEP)** | 維持現狀 |
| `pipelines/quality/great_expectations_gate.py` | Great Expectations 資料品質閘道轉接器。 | **Caller**: `tests/data/test_great_expectations_gate.py`。 | **保留 (KEEP)** | 維持現狀 |
| `pipelines/dbt/` | Model-ready 資料視圖 dbt 專案。 | **Caller**: dbt transform pipelines。 | **保留 (KEEP)** | 維持現狀 |
| `pipelines/orchestration/dagster_training.py` | Dagster 模型訓練排程。 | **Caller**: Training pipelines。 | **保留 (KEEP)** | 維持現狀 |
| `pipelines/features/model_features.py` | 特徵計算管線。 | **Caller**: Feature pipeline。 | **保留 (KEEP)** | 維持現狀 |
| `pipelines/training/model_training.py` | 模型訓練管線。 | **Caller**: Training pipeline。 | **保留 (KEEP)** | 維持現狀 |
| `product_ops/modeling/` (全目錄) | 模型基準評測、發布與成果追蹤。 | **Caller**: `product_ops` 維運腳本與測試。 | **保留 (KEEP)** | 維持現狀 |
| `product_ops/data_platform/backfill.py` | 資料平台回填維運腳本。 | **Caller**: 資料維運。 | **保留 (KEEP)** | 維持現狀 |
| `product_ops/external_data_backfill.py` | 外部資料回填維運腳本。 | **Caller**: 資料維運。 | **保留 (KEEP)** | 維持現狀 |

---

### 4.7 腳本目錄 (`scripts/`)

| 元件名稱與路徑 | 主要責任與對外介面 | 呼叫者與相依分析 (Usage Evidence) | 處置判定 | 承接 Wave 任務 |
|---|---|---|---|---|
| `scripts/ai-status.sh` & `scripts/ai_status.py` | 系統核心狀態管理與協調 CLI。 | **Caller**: Supervisor 與 Auto Worker 狀態更新唯一入口。 | **保留 (KEEP)** | 維持現狀 |
| `scripts/restart-supervisor.sh`, `run-supervisor.sh`, `run-supervisor-watchdog.sh` | Supervisor 守護與啟動腳本。 | **Caller**: Supervisor 主機 process manager。 | **保留 (KEEP)** | 維持現狀 |
| `scripts/supervisor_runtime_health.py` & `supervisor_watchdog_install.py` | Supervisor 健康檢查與安裝工具。 | **Caller**: Supervisor 維運。 | **保留 (KEEP)** | 維持現狀 |
| `scripts/validate_external_data_boundary.py` | 外部資料邊界與全庫凍結清單校驗工具。 | **Caller**: `tests/architecture/test_external_data_boundary.py`。<br>**判定**: 嚴格校驗 2700 個檔案邊界分類，確保第三方來源關閉狀態。 | **保留 (KEEP)** | 維持現狀 |
| `scripts/orchestrator/check_task_dependency_resolvability.py` | Task 依賴圖完整性解析器。 | **Caller**: `Makefile` (`make task-dependency-check`)。 | **保留 (KEEP)** | 維持現狀 |
| `scripts/orchestrator/*.py` (其他維運腳本) | Archive 回填、新鮮度檢查、Worktree 設定轉移等維運工具。 | **Caller**: Supervisor 內部診斷與維運。 | **保留 (KEEP)** | 維持現狀 |

---

## 5. 總結清單：保留／替換／刪除清單 (Inventory Matrix)

依據驗收條件第 2 項，彙整全系統之保留、替換、刪除分類清單與統計：

### 5.1 統計摘要

```text
┌────────────────────────┬───────┬────────────────────────────────────────────────────────┐
│ 處置類別               │ 數量  │ 主要範疇與代表項目                                     │
├────────────────────────┼───────┼────────────────────────────────────────────────────────┤
│ 【保留 (KEEP)】        │ 48 項 │ CI 工作流、標準 Dockerfile、線上 E2E 門禁、維運腳本等   │
│ 【替換/重構 (REPLACE)】│  8 項 │ Runtime Release、check_runtime_admission、Gate Registry│
│ 【刪除/退役 (DELETE)】 │  9 項 │ 過期 Dockerfile.api/web、舊 docker-compose、孤立狀態檔 │
└────────────────────────┴───────┴────────────────────────────────────────────────────────┘
```

---

### 5.2 刪除清單 (DELETE / RETIRE) — 待 Wave 2 `ODP-DEPLOY-DEAD-CODE-REMOVAL-001` 執行

> **注意**：依本任務規範，本任務只記錄清單，不直接刪除任何代碼。

| 序號 | 檔案路徑 | 類型 | 刪除理由與 Caller 證明 |
|---|---|---|---|
| 1 | `infra/docker/Dockerfile.api` | Dockerfile | 過期舊版 Dockerfile（暴露 8080 port，寫死 pip install 依賴）。已由 `infra/docker/api.Dockerfile` 取代。 |
| 2 | `infra/docker/Dockerfile.web` | Dockerfile | 過期舊版 Dockerfile（暴露 8080 port）。已由 `infra/docker/web.Dockerfile` 取代。 |
| 3 | `infra/docker/docker-compose.yml` | Compose | 過期 Compose 檔，引用舊版 `Dockerfile.api`。已由根目錄標準 `docker-compose.yml` 取代。 |
| 4 | `docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json` | 狀態 JSON | 舊版 PR #82 外部證明佇列，所屬 13 支腳本已刪除，本檔案已完全孤立無 caller。 |
| 5 | `docs/evidence/EXTERNAL_PROOF_HANDBACK_TEMPLATE.json` | 範本 JSON | 舊版 PR #82 外部證明 handback 範本，無 active code 引用。 |
| 6 | `docs/evidence/EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json` | 狀態 JSON | 舊版 PR #82 外部證明看板，無 active code 引用。 |
| 7 | `docs/evidence/EXTERNAL_PROOF_FLEET_PICKUP_BOARD.md` | 說明 MD | 舊版 PR #82 外部證明領取說明文件，已完全過期。 |
| 8 | `infra/cloudbuild/README.md` | 文件 Stub | 空白說明文件；系統完全使用 GitHub Actions (WIF)，不使用 Cloud Build。 |
| 9 | `infra/k8s_optional/README.md` | 文件 Stub | 空白說明目錄，無實質用途。 |

---

### 5.3 替換 / 重構清單 (REPLACE / REFACTOR)

| 序號 | 檔案路徑 | 目前狀態與問題 | 預計重構目標 | 承接任務 |
|---|---|---|---|---|
| 1 | `.github/workflows/deploy-dev.yml` | 現場 build 映像檔；缺少 prod blue-green 路徑；門禁存在循環依賴。 | 整合為單一 `Runtime Release` 發布狀態機（Build Once -> Dev -> Staging -> Prod）。 | `ODP-RUNTIME-RELEASE-SINGLE-PATH-001` (Wave 2) |
| 2 | `delivery_toolchain/release/check_runtime_admission.py` | Shape-only regex 檢查，無簽名防偽與 CAS 防重放。 | 替換為以 KMS / 私鑰簽名之權威 Release Lease 驗證器。 | `ODP-RELEASE-ADMISSION-AUTHORITY-001` (Wave 0) |
| 3 | `delivery_toolchain/e2e/check_release_gate_registry.py` | 靜態 7 道 gate（Gate 0–6），無 stage 與環境概念。 | 重構為支援分階段 (candidate-built -> dev-verified -> staging-verified -> prod-admitted) 之 Gate Registry。 | `ODP-RELEASE-MANIFEST-GATES-001` (Wave 0) |
| 4 | `delivery_toolchain/e2e/check_product_release_gate.py` | 與舊 gate registry 強耦合。 | 配合新 manifest/gate registry 調整參數與驗證邏輯。 | `ODP-RELEASE-MANIFEST-GATES-001` (Wave 0) |
| 5 | `product_ops/deployment/deploy_cloud_run_waji.sh` | 現場 build/push 映像檔；流量推進僅支援單一環境 100%。 | 抽離 build（改為接收 release manifest digest）；擴充支援 prod blue-green (0% green 驗證後 100% 切換)。 | `ODP-PROD-BLUEGREEN-PRIMITIVES-001` (Wave 1) |
| 6 | `product_ops/deployment/cloud_run_release_traffic.sh` | 支援 dev/staging 流量切換。 | 擴充支援 prod blue-green 與 GKE/Cloud Run 混合切換及回滾。 | `ODP-PROD-BLUEGREEN-PRIMITIVES-001` (Wave 1) |
| 7 | `delivery_toolchain/e2e/verify_deployment_health_backup_rollback.py` | 使用本地 SQLite docker-compose 模擬。 | 替換為 Staging 環境真實 Cloud SQL PostgreSQL 備份還原與回滾演練收據。 | `ODP-EPHEMERAL-STAGING-IAC-001` (Wave 1) |
| 8 | `delivery_toolchain/e2e/check_remote_staging_proof.py` | 針對常駐 staging URL 驗證。 | 重構以適配短生命週期 Ephemeral Staging 之動態 URL 與 TTL 標籤。 | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` (Wave 3) |

---

### 5.4 保留清單 (KEEP)

- **Workflows**: `ci.yml`, `promote-dev-to-main.yml`, `merge-queue-review-gate.yml`, `tooling-scope-review-gate.yml`, `emgi-consumer-boundary.yml`, `assisted-intake-design-validation.yml`。
- **Core Deployment & Live Proof**: `product_ops/deployment/cloud_run_job_entrypoint.py`, `validate_cloud_run_live_deployment.py`, `delivery_toolchain/e2e/check_live_e2e_gate.py`, `delivery_toolchain/e2e/check_live_production_data.py`, `delivery_toolchain/security/sign_images.sh`。
- **CI E2E & Contract Tooling**: `run_product_e2e.sh`, `run_python_e2e_tests.py`, `record_playwright_results.py`, `generate_product_e2e_receipt.py`, `seed_product_e2e_data.py`, `check_drift.py`, `export_openapi.py`, `generate_client.py`。
- **Security & Governance**: `secret_scan.py`, `sast_scan.py`, `generate_sbom.py`, `check_code_boundaries.py`, `check_config_wiring.py`, `classify_change_review_scope.py`, `task_start.sh`, `task_finalize.sh`, `worker_commit.py`, `validate_external_data_boundary.py`。
- **Standard Docker & IaC**: `infra/docker/api.Dockerfile`, `infra/docker/web.Dockerfile`, `infra/docker/worker.Dockerfile`, `infra/docker/scheduler.Dockerfile`, `infra/docker/data-platform.Dockerfile`, 根目錄 `docker-compose.yml`, `infra/terraform/**`, `infra/k8s/data-platform/**`, `infra/mlflow/**`。
- **Pipelines & Modeling**: `pipelines/data_quality/gates.py`, `pipelines/dbt/**`, `pipelines/orchestration/**`, `product_ops/modeling/**`。

---

## 6. 後續 Wave 任務執行建議與指引

本盤點結果直接提供給後續派工 DAG 參考：

1. **Wave 0 (`ODP-RELEASE-MANIFEST-GATES-001`, `ODP-RELEASE-ADMISSION-AUTHORITY-001`)**:
   - 凍結 Release Manifest Schema（定義 API、Web、Worker、Scheduler、Data Platform exact digests 與 Policy/Contract digests）。
   - 實作具簽名與 CAS 防重放之權威 Lease 驗證器，廢棄 Shape-only 檢查，解開 Gate 0–6 循環依賴。
2. **Wave 1 (`ODP-EPHEMERAL-STAGING-IAC-001`, `ODP-PROD-BLUEGREEN-PRIMITIVES-001`, `ODP-RELEASE-EVIDENCE-RECEIPTS-001`)**:
   - 建立 Ephemeral Staging 建立/銷毀/TTL 機制，以真實 PostgreSQL 演練取代 `verify_deployment_health_backup_rollback.py` 的 SQLite 模擬。
   - 擴充 `cloud_run_release_traffic.sh` 與 `cloud_run_traffic.py`，支援 Production 0% green 流量驗收與原子 100% 切換/回滾。
   - 統一收據格式（Redacted receipts）與 Artifact allowlist。
3. **Wave 2 (`ODP-RUNTIME-RELEASE-SINGLE-PATH-001`, `ODP-DEPLOY-DEAD-CODE-REMOVAL-001`)**:
   - 將 `deploy-dev.yml` 改造為唯一發布管線，落實 Build Once 流程。
   - 正式刪除本報告 5.2 節列出之過期 Dockerfile、舊 Compose 與孤立之舊版 External Proof 文件。

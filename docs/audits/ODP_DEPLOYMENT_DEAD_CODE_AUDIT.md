# ODay Plus 部署、Proof、Gate 與 Scheduler 重複及廢 Code 盤點報告

- **文件 ID**: `ODP-DEPLOY-DEAD-CODE-AUDIT`
- **任務 ID**: `ODP-DEPLOY-DEAD-CODE-AUDIT-001`
- **任務階段**: Wave 0 — 基線與介面凍結 (Dead Code Audit)
- **盤點基準日期**: 2026-08-24
- **歷史盤點起始基準**: `origin/dev` @ `3329416d1b1b41289da152738d8e5392ebfcbf4d`
- **當前驗證基準（Composed Head）**: `origin/dev` @ `fd410a63695562cb63ab43ee5feb3055f93c3b01`（包含 PR #999 `ODP-RELEASE-MANIFEST-GATES-001`、PR #1001 `ODP-RELEASE-EVIDENCE-RECEIPTS-001` 與 PR #1003 `ODP-PROD-BLUEGREEN-PRIMITIVES-001` 合併成果；本報告分支已完成 base advance merge）
- **負責人 (Owner)**: Antigravity
- **審查人 (Reviewer)**: Codex2
- **來源依據**: [`docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md`](../deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md)
- **執行原則**: **本任務只稽核不刪 code**。所有項目均以 caller、workflow、runtime unit、cron、GitHub Actions 具體呼叫路徑逐項證明，作為後續 Wave 1 / Wave 2 實作與刪除任務（特別是 `ODP-DEPLOY-DEAD-CODE-REMOVAL-001` 與 `ODP-RUNTIME-RELEASE-SINGLE-PATH-001`）之執行依據。

> **行號引用約定**：本報告所有 `Lnn` / `lines a-b` 引用與掃描斷言，皆以當前驗證基準與 composed head 之檔案內容為準，並已於 §7 以可重跑指令逐項機器驗證。

---

## 1. 執行摘要

本報告針對 ODay Plus 系統中與 **部署 (Deployment)**、**證明 (Proof & Evidence)**、**門禁 (Release Gates & Admission)**、**排程與工作 (Scheduler & Worker)** 以及 **容器與基礎設施 (Docker & IaC)** 相關之全部工作流程、腳本、配置與文件進行全面性盤點。

### 1.1 核心盤點結論

1. **唯一部署入口與旁路分析 (Bypass Entrypoint)**:
   - `.github/workflows/deploy-dev.yml` (`Runtime Release`) 是**唯一持有 GCP WIF 憑證的 workflow**（全庫僅此檔含 `google-github-actions/auth` / `workload_identity_provider`），但現況在 `deploy` job 內部現場執行 `docker build`，違反「Build Once、以 Digest 為唯一部署身分」原則。
   - `product_ops/deployment/deploy_cloud_run_waji.sh`（mode `755`，可執行）只依賴環境變數，具備 `gcloud` 認證之終端即可直接執行，繞過 `admission` job 之 release lease 檢查。
   - `.github/workflows/promote-dev-to-main.yml` 僅為代碼由 `dev` 晉級至 `main` 之 PR 自動合併流程（步驟只有 `make product-release-gate`、`gh pr create`、`gh pr merge` 與 status stamp），**不含任何 GCP 部署指令**；兩者責任界限須明確分離。
   - **旁路掃描口徑**：`gcloud run deploy` / `update-traffic` / `run jobs` / `scheduler jobs` 之全庫掃描（只排除 `docs/` 與 `tests/`，**不排除 `support/`**）共命中 **6 個**檔案，分為「具實際 GCP 變更能力 **4**」（`deploy-dev.yml`、`deploy_cloud_run_waji.sh`、`cloud_run_release_traffic.sh`、`product_ops/deployment/bluegreen_release.py`）、「驗證器僅字串常值 **1**」（`validate_cloud_run_live_deployment.py`）、「純文件 review packet 散文 **1**」（`support/sidecars/…-SIDECAR-REVIEW.md`，mode `100644`、無 caller）。後兩類**不構成旁路**，於 §2 逐項舉證分類而非以縮小掃描範圍隱藏；§7.5 對此組數字提供 14 項 fail-closed 斷言。
1a. **擴大旁路搜尋：GKE 直接發布、Terraform Cloud Run 與手動部署指引 (Expanded Bypass Scope)**：
   - `infra/k8s/data-platform/README.md` 文件記載了一條**完整的 GKE 手動部署路徑**：`docker build`（L40-45）→ `render.py`（L53-62）→ `kubectl apply`（L87-88），可繞過 `Runtime Release` lease 與 admission job 直接將 data-platform image 部署至 GKE。此路徑無自動化 workflow 觸發、無 WIF 認證，屬**操作員手動帶外部署**。但 `deployment_runtime.py` 實作之遷移收據門禁（要求 release SHA 與 image digest 完全匹配已通過之遷移紀錄）提供內建防護。§2 bypass 表補列此路徑並分類邊界。
   - `infra/terraform/cloud_run.tf` 定義 API（L1-133）與 Web（L153-265）之 Cloud Run 服務資源；`infra/terraform/README.md`（L102-123）提供 `terraform init/plan/apply` 完整 bootstrap 指引。此路徑為**基礎設施 bootstrap**，非例行 runtime deploy：全庫無任何 GitHub Actions workflow 執行 terraform（`git grep -rn 'terraform\|hashicorp' -- .github/workflows/` 為空）；Terraform 不管理 Cloud Run Jobs（migration/worker/scheduler）或 Cloud Scheduler triggers；變更 `var.api_image` / `var.web_image` 之 tfvars 後 apply **可**產生新 revision，構成理論旁路面。§2 bypass 表補列並標示 bootstrap vs runtime 邊界。
   - `docs/deployment/GCP_DEPLOY_GUIDE.md`（L7）明示「falling back to local environment variables during manual execution」；L39-49 以 Build & Deploy 步驟描述 API/Web 之 `docker build` → `docker push` → Cloud Run 部署流程。此文件描述的**正是** `deploy_cloud_run_waji.sh` 的手動執行路徑（已在原版 §2 列為旁路），但文件本身**合法化**了手動 fallback，且未宣告 release lease 為必要前置。§2 bypass 表補列此文件引導面。
2. **舊版 External Proof Follow-up 殘留 (Legacy External Proof)**:
   - 歷史 PR #82 所衍生之 external-proof 執行面元件，已於 commit `1a8b0f44`（2026-08-14，`refactor(tooling): retire pr82 campaign and unsupported adapters`）**一次性完整刪除**：1 支 workflow、16 支 external-proof 專用 CLI（12 × `check_`、1 × `generate_`、2 × `sync_`、1 × `update_`）、15 支 `tests/e2e/test_external_proof_*.py`，另含同批移除之 `check_product_go_no_go.py`。逐檔清單見 §3.1。
   - 全庫掃描（三種邊界，定義見 §3.2）識別出 **21 個檔案**（不含本報告）含有 external-proof 相關字串。其中 **1 個為現行治理文件**（`docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md`，即本 Wave 之規劃書，其 2 處引用是**宣告退役政策本身**），**其餘 20 個為舊機制殘留**。
   - 20 個殘留檔案之處置分佈：**5 檔刪除**、**10 檔重構精簡**、**4 檔歸檔保留**、**1 檔保留（active 程式碼，僅脈絡註解）**。完整逐檔證據見 §3.2。
3. **門禁與入場機制重複與循環依賴 (Admission & Gate Redundancy)**:
   - `delivery_toolchain/release/check_runtime_admission.py` 現況僅檢查 `task_id` 與 `release_lease` 之正規表達式（Shape-only），尚未綁定 Supervisor 簽名與 CAS 防重放狀態。
   - `check_runtime_admission.py` 與 `check_release_gate_registry.py` 目前在部署 dev/staging 前硬性要求 Gate 0–6 全部通過，造成「staging 才能產生的驗證收據反過來阻擋 staging 部署」的循環依賴。
   - `delivery_toolchain/e2e/verify_deployment_health_backup_rollback.py` 在 `deploy-dev.yml` 中啟動本地 Docker Compose (SQLite) 產生測試收據，無法代表真實 Cloud Run / PostgreSQL 部署健康；應在 Ephemeral Staging 建立後由真實環境演練取代。
4. **容器定義與 Dockerfile 漂移 (Docker Redundancy)**:
   - 倉庫內存在過期陳舊之 `infra/docker/Dockerfile.api`（暴露 8080 port、寫死 pip install 依賴）與 `infra/docker/Dockerfile.web`（暴露 8080 port），已被正規之 `infra/docker/api.Dockerfile`（暴露 8000 port、由 pyproject.toml 同步）與 `infra/docker/web.Dockerfile`（暴露 3000 port）取代。
   - `infra/docker/docker-compose.yml` 引用過期之 `Dockerfile.api`，與根目錄標準 `docker-compose.yml` 重複且行為不一致。
5. **排程、感應器與工作處理器 (Scheduler, Sensor & Worker)**:
   - 本地開發使用根目錄 `docker-compose.yml` 啟動 `apps/scheduler/oday_scheduler`（L66）與 `apps/worker/oday_worker`（L54）。
   - **應用層排程 (Cloud Scheduler → Cloud Run Jobs)**：雲端環境使用 GCP Cloud Scheduler 定期 HTTP 呼叫觸發 Cloud Run Jobs，並以 `product_ops/deployment/cloud_run_job_entrypoint.py` 執行 bounded worker/scheduler。**Cloud Scheduler 完整呼叫鏈之精確行號證據見 §4.2.1**（函式定義 `deploy_cloud_run_waji.sh:443-470`；兩處呼叫 `deploy_cloud_run_waji.sh:579-582` 與 `:583-586`；cron 值由 `deploy-dev.yml:110-112` 注入）。
   - **叢集批次排程 (GKE CronJob)**：GKE 部署由 `infra/k8s/data-platform/workloads.yaml.tpl` 定義 CronJob `oday-data-platform-bounded-daily`（YAML document 涵蓋 L133-303），`schedule: "0 1 * * *"`（L148）、`timeZone: Etc/UTC`（L149），每日執行 bounded backfill。其工作範圍由 `infra/k8s/data-platform/README.md`（L98-107）劃定。
   - **資料平台 Dagster 宣告面（目前未接線）**：`apps/data_platform/definitions.py`（L208-249）宣告 6 組 ScheduleDefinition daily crons，L280-318 宣告 3 組 900 秒感應器，並於 L321-364 掛載至 `defs = Definitions(...)`。但 `pyproject.toml:12-13` 只有 package entrypoint，README 與 tests 也不是 runtime caller；`data-platform.Dockerfile:58` 的 ENTRYPOINT 實際進入 `deployment_runtime.py`，GKE CronJob `workloads.yaml.tpl:218` 傳入 `scheduled`，再由 `deployment_runtime.py:403-425` 呼叫 `product_ops.data_platform.backfill`。針對 Dagster CLI/daemon/runtime invocation 的負向掃描為 0 命中，故本報告將 `definitions.py` 分類為 **未接線 (UNWIRED)、待替換或退役 (REPLACE)**，而非已證明 active 的第三套 scheduler；完整證據見 §4.6.1。
   - **排程機制與共享 runner 對帳 (Reconciliation)**：(1) Cloud Scheduler 負責 ODay Plus 應用層非同步工作；(2) GKE CronJob 是目前資料平台實際批次啟動器；(3) Dagster Definitions 目前僅是未接線的宣告面。GKE `backfill.py` 與 Dagster asset 函式都會走 `DataPlaneRunner.run_partition`，且對 11 個 scheduled source kinds 重疊；因此不能把兩者宣稱為三套獨立 active runtime。後續只能選擇由 Runtime Release 正式接線 Dagster（含 daemon/CLI、lease 與 receipt），或退役其 schedule/sensor 宣告並保留唯一 backfill 路徑；本任務只記錄，不刪 code。完整對帳矩陣見 §4.6.2。

---

## 2. 繞過 Runtime Release 的入口與旁路盤點

依據驗收條件第 3 項，盤點所有可能繞過唯一 `Runtime Release` 發布流程之直接/間接入口。

**盤點方法（可重跑）**：

```bash
# 具備 GCP 變更能力的檔案（排除 docs/ 與 tests/）
git grep -ln 'gcloud run deploy\|gcloud run services update-traffic\|gcloud run jobs \|gcloud scheduler jobs ' -- . | grep -v '^docs/' | grep -v '^tests/'
# 具備 WIF / GCP 身分的 workflow
git grep -ln 'google-github-actions/auth\|workload_identity_provider' -- .github/workflows/
```

**掃描邊界宣告**：上列第一條指令只排除 `docs/` 與 `tests/` 兩個前綴，**不排除 `support/`**。`support/sidecars/` 是 review packet 的純文件樹（全庫 98 個追蹤檔案**全部**為 `.md`、全部為 mode `100644`），因此其中引述 gcloud 指令的散文會被這條純字串搜尋命中。本報告**不以縮小掃描範圍的方式隱藏該命中**，而是逐檔分類；若未來 base advance 再引入同類文件命中，§7.5 的 fail-closed 斷言會直接讓驗證腳本以非 0 離開，而不是讓報告內的數字悄悄失準。

掃描結果：該指令在本報告基準 head 共命中 **6 個**檔案，分為三類：

| 類別 | 檔數 | 檔案 | 是否為旁路面 |
|---|---|---|---|
| **具實際 GCP 變更能力** | 4 | `.github/workflows/deploy-dev.yml`、`product_ops/deployment/deploy_cloud_run_waji.sh`、`product_ops/deployment/cloud_run_release_traffic.sh`、`product_ops/deployment/bluegreen_release.py` | **是**（逐項見下表） |
| **驗證器：僅字串常值** | 1 | `product_ops/deployment/validate_cloud_run_live_deployment.py` | 否（見下表「驗證器」列） |
| **純文件：review packet 散文** | 1 | `support/sidecars/ODP-DEPLOY-SCHEDULER-ROLLBACK-RESTORE-001/ODP-DEPLOY-SCHEDULER-ROLLBACK-RESTORE-001-SIDECAR-REVIEW.md` | 否（見下表「Sidecar review packet」列） |

持有 WIF 身分之 workflow 僅 `deploy-dev.yml` 1 個。

**擴大搜尋（kubectl、terraform、docker build/push、render/apply）**：

```bash
# kubectl 部署指令（排除 tests/ 與 docs/audits/）
git grep -ln 'kubectl' -- . | grep -v '^tests/' | grep -v '^docs/audits/'
# -> docs/evidence/runtime/.../approval-queue-control-before.json  （歷史紀錄）
# -> infra/k8s/data-platform/README.md                             （手動操作指引）

# Terraform Cloud Run（排除 tests/ 與 docs/audits/）
git grep -rn 'terraform apply\|terraform plan\|terraform init' -- . | grep -v '^tests/' | grep -v '^docs/audits/' | grep -v '^docs/evidence/'
# -> infra/terraform/.terraform.lock.hcl:1      （自動生成註解）
# -> infra/terraform/README.md:102,107           （bootstrap 指令）

# GitHub workflow 中是否含 terraform
git grep -rn 'terraform\|hashicorp' -- .github/workflows/
# -> 空（全庫 workflow 不含 terraform）

# render.py / deployment_runtime.py 之 caller（排除 tests/ 與 docs/）
git grep -rn 'render\.py\|deployment_runtime\.py' -- . | grep -v '^tests/' | grep -v '^docs/'
# -> infra/docker/data-platform.Dockerfile:58   （ENTRYPOINT）
# -> infra/k8s/data-platform/README.md:53       （文件指引）
```

| 入口類型 | 檔案 / 路徑 | 現況行為與證據 | 風險與旁路機制 | 後續處置方案 |
|---|---|---|---|---|
| **唯一授權部署管線** | `.github/workflows/deploy-dev.yml` | `workflow_dispatch` inputs：`environment` (choice: dev/staging, L6-10)、`release_sha` (L11-14)、`task_id` (L15-18)、`release_lease` (L19-22)。`admission` job 於 L42-51 斷言 checkout HEAD == `inputs.release_sha`，L53-61 呼叫 `check_runtime_admission.py`。 | 為現行唯一具 WIF 身分之 workflow，屬期望路徑。缺口在於 lease 僅做 shape 檢查（見 §4.3）。 | **重構 (REPLACE)**：改為 Build Once 單一發布狀態機。 |
| **本機/腳本直接發布** | `product_ops/deployment/deploy_cloud_run_waji.sh` | 檔案權限 `-rwxr-xr-x`（可直接執行）。腳本只依賴環境變數（`ODP_DEPLOY_ENV`, `ODAY_RELEASE_SHA`, `GCP_PROJECT` 等），無 lease 驗證邏輯。 | 若開發者在終端手動執行，可完全繞過 GitHub Actions 之 `admission` job，導致未持有有效 Supervisor Release Lease 即可變更 Cloud Run 服務與 Scheduler trigger。 | **重構 (REPLACE / REFACTOR)**：在 `ODP-PROD-BLUEGREEN-PRIMITIVES-001` 與 `ODP-RUNTIME-RELEASE-SINGLE-PATH-001` 中，將 release lease 與 manifest digest 作為腳本強制校驗參數，無簽名 lease 時直接 fail-closed。 |
| **可 source 之流量/排程變更函式庫** | `product_ops/deployment/cloud_run_release_traffic.sh` | 檔案權限 `-rw-rw-r--`（**無執行位元**），檔頭 L3-4 自述「only defines functions」。含 `gcloud run services update-traffic`（L45, L58）與 scheduler `delete`/`create-update`/`pause`/`resume`（L106, L144, L153, L161）。 | 非獨立執行入口，但被 `source` 後即可在任意 shell 直接呼叫流量與 trigger 變更函式，屬次級旁路面。 | **保留並擴充 (KEEP & EXPAND)**：函式層納入 lease 檢查，避免被裸 source 使用。 |
| **生產藍綠發布原語與回滾模組** | `product_ops/deployment/bluegreen_release.py` | 檔案權限 `-rw-rw-r--`（提供 CLI 與 Python API）。實作 production 0% green smoke、atomic 100% traffic switch、scheduler pause/resume、data platform pointer recovery 與 rollback 序列。含 `gcloud run services update-traffic` 與 `gcloud scheduler jobs` 變更能力。 | 具備完整 GCP 變更能力，可由操作員或 CLI 直接執行切換/回滾。內建 `--dry-run` 與 fail-closed 錯誤處理；目前尚未由 GitHub Actions workflow 自動呼叫。 | **受控生產發布原語 (CONTROLLED PRIMITIVE)**：由 Wave 2 `ODP-RUNTIME-RELEASE-SINGLE-PATH-001` 統一收納至 Single-Path Runtime Release 狀態機中調用。 |
| **代碼晉級工作流** | `.github/workflows/promote-dev-to-main.yml` | `workflow_run` (CI completed on `dev`) 觸發。步驟僅：`npm ci` (L43)、`make product-release-gate` (L50)、開/重用 promote PR (L52-57)、stamp `task-review-gate` (L96-101)、`gh pr merge --auto` (L113-117)。**無 `google-github-actions/auth`，無任何 `gcloud` 指令。** | 容易被誤認為正式環境部署入口。實際上該 workflow 僅執行 Git branch 合併，完全不觸及 GCP。 | **保留並釐清 (KEEP & CLARIFY)**：保留作為 Code Promotion 自動化，但在文件與註解中嚴格宣告：**Merge 到 main 不等於 Production 部署**。 |
| **設計合約驗證入口** | `.github/workflows/assisted-intake-design-validation.yml` | 包含 `workflow_dispatch` 手動觸發入口與 PR trigger，啟動本地 PostgreSQL 驗證 OpenAPI 與 SQL schema。無 WIF 身分、無 `gcloud`。 | 純合約驗證工具，不構成部署旁路。 | **保留 (KEEP)**：維持作為子系統設計合約驗證 gate。 |
| **驗證器（非旁路，特別澄清）** | `product_ops/deployment/validate_cloud_run_live_deployment.py` | 檔內 `gcloud run deploy` 等字串（L184, L192, L201, L217）**僅為字串常值**，用於靜態比對 `deploy_cloud_run_waji.sh` 的指令順序，該程式本身不執行 GCP 變更。 | 不構成旁路。列於此處是為了避免純字串搜尋造成誤判。 | **保留 (KEEP)** |
| **Sidecar review packet（非旁路，文字誤判）** | `support/sidecars/ODP-DEPLOY-SCHEDULER-ROLLBACK-RESTORE-001/ODP-DEPLOY-SCHEDULER-ROLLBACK-RESTORE-001-SIDECAR-REVIEW.md` | 檔案為 `ODP-DEPLOY-SCHEDULER-ROLLBACK-RESTORE-001` 之 review packet（Markdown 散文）。三處命中（L36、L54、L68）皆為**審查敘述中引述受審腳本行為**的行內 code 片段，例如 L36 描述 restore 依序呼叫 `gcloud scheduler jobs update http`、L68 指出 `cloud_run_release_traffic.sh` L104-110 之 `gcloud scheduler jobs delete ... \|\| true` 會吞掉非 NOT_FOUND 失敗。檔案 mode 為 `100644`（**無執行位元**），`support/` 全樹 98 個追蹤檔案皆為 `.md` 且皆為 `100644`，無任何可執行或可 `source` 之產物。全庫無任何 workflow、腳本或程式碼讀取、執行或 `source` 此檔；唯一提及其 task id 之處為 `docs-site/orchestrator-state.json` 之派工紀錄（task 追蹤，非執行），生命週期則由 `.orchestrator/sidecar_cleanup.py` 依保留政策做歸檔/刪除（僅搬移與刪檔，不執行內容）。 | **不構成旁路**：純散文，無執行路徑、無 GCP 身分、無 caller。屬純字串搜尋之預期誤判，故明列而非以縮小掃描範圍隱藏。 | **保留 (KEEP)**：屬 review packet 文件樹，由 `.orchestrator/sidecar_cleanup.py` 之既有保留政策治理，本 Wave 不處置。 |
| **GKE 資料平台手動部署路徑** | `infra/k8s/data-platform/README.md` (文件引導) + `render.py` + `kubectl` | 完整步驟：(1) `docker build` data-platform image（L40-45）；(2) `docker push` 至 Artifact Registry；(3) `python infra/k8s/data-platform/render.py` 渲染 immutable manifest（L53-62），以 `__DATA_IMAGE__` 佔位符替換 release digest；(4) `kubectl apply --dry-run=server` 後 `kubectl apply`（L87-88）；(5) `kubectl wait` 等待 migration Job 完成（L89-92）。**全庫無任何 workflow 呼叫 `render.py` 或 `kubectl apply`**（`git grep -rn 'render\.py\|kubectl apply' -- .github/workflows/` 為空）。此路徑僅存在於 README 文件指引，屬操作員手動執行。 | **風險**：可繞過 `Runtime Release` 之 admission job、WIF 認證與 release lease 檢查，直接以 `kubeconfig` 權限部署至 GKE。<br>**內建防護**：(a) `render.py` 要求 immutable `sha256:` image reference（L40-41）與 40-char SHA（L34-35）；(b) `deployment_runtime.py` 之 `_migration_receipt()` 門禁要求 `odp_runtime.deployment_migration_receipts` 中存在 release SHA + image digest 完全匹配之 PASSED 收據，否則 fail-closed（不執行 backfill）；(c) 目標 namespace `oday-dev` 之 RBAC 與 ServiceAccount 由 operator 管理。<br>**邊界**：此路徑**僅部署資料平台**（migration/CronJob/suspended Jobs），不部署 ODay Plus API/Web/Worker/Scheduler Cloud Run 服務。 | **保留並納入治理 (KEEP & GOVERN)**：此為資料平台 GKE 部署的既定操作路徑，由 `DPF-EMGI-LIVE-ROLLOUT-001`（Wave 1）承接。後續應評估是否將此路徑整合至唯一 Runtime Release，或以獨立之 data-platform release gate 明確授權。 |
| **Terraform Cloud Run bootstrap 路徑** | `infra/terraform/cloud_run.tf` + `infra/terraform/README.md` | `cloud_run.tf` 以 `google_cloud_run_v2_service` 定義 API（L1-133，`image = var.api_image`，L33）與 Web（L153-265，`image = var.web_image`，L178）Cloud Run 服務。`README.md` L102-109 提供 `terraform init/plan/apply` 指令。生產環境有 `lifecycle.precondition` 要求 immutable `@sha256:` digest（API L117-120、Web L252-255）。**全庫無任何 GitHub Actions workflow 執行 terraform**（`git grep -rn 'terraform\|hashicorp' -- .github/workflows/` 為空）。Terraform **不管理** Cloud Run Jobs（migration/worker/scheduler）、Cloud Scheduler triggers，這些全由 `deploy_cloud_run_waji.sh` 管理。 | **判定為 bootstrap 路徑**：(a) README L112-113 自述「The first environment bootstrap is intentionally two-phase」；(b) L128 明示「Never use `-target` for routine updates」；(c) 變更 `.tfvars` 中之 `api_image`/`web_image` 後 apply 可產生新 Cloud Run revision，構成**理論旁路面**，但需持有 Terraform state backend 寫入權限與 GCP org admin 身分；(d) Terraform 範圍不含 Cloud Scheduler triggers 與 Cloud Run Jobs，無法完成完整 release 生命週期。 | **保留並澄清 (KEEP & CLARIFY)**：由 `ODP-EPHEMERAL-STAGING-IAC-001`（Wave 1）與 `ODP-GITHUB-GCP-ENV-BOOTSTRAP-001`（Wave 3）承接。文件與 IaC 應明確宣告：Terraform 僅負責 bootstrap 基礎設施 shell，不用於例行 runtime deployment；image 變數更新須經 Runtime Release 管線，不得以 `terraform apply` 直接推送新版本。 |
| **手動部署文件引導** | `docs/deployment/GCP_DEPLOY_GUIDE.md` | L7 明示 pipeline 組態「falling back to local environment variables during manual execution」。§ Deployment Process Details（L39-49）詳述 API/Web 之 Build & Deploy 流程：(1) API image 以 `infra/docker/api.Dockerfile` 建置並推送至 Artifact Registry（L41-44）；(2) Web image 以 `infra/docker/web.Dockerfile` 建置，烘入 `ODP_API_BASE_URL`（L45-49）。此文件描述之步驟**正是** `deploy_cloud_run_waji.sh` 之手動執行（已在上列為旁路），但文件以參考指南形式**合法化**手動 fallback 且未宣告 release lease 為必要前置條件。 | 不構成獨立旁路，但其 L7 之 fallback 敘述與 L39-49 之步驟指引降低操作員對「必須經過 Runtime Release 管線」之認知。 | **更新文件 (UPDATE)**：在 `ODP-RUNTIME-RELEASE-SINGLE-PATH-001`（Wave 2）完成後，更新文件以宣告：生產環境必須經唯一 Runtime Release 管線，手動執行僅限 dev 環境之除錯與緊急回復，且須事後補齊 evidence receipt。 |
| **手動 Cloud Run 變更** | GCP Console / Direct gcloud | 操作者手動透過 GCP 控制台切換流量或部署 Revision。 | 人工帶外操作將導致 Git 與 live runtime 狀態漂移。 | **權限治理 (GOVERNANCE)**：依 Rollout Plan 第 8 節與第 16 節，限制 WIF Service Account 為唯一的部署者。 |

---

## 3. 舊版 External Proof Follow-up 殘留與幽靈依賴盤點

### 3.1 執行面元件移除證明（單一 commit，逐檔可驗）

舊版 External Proof 執行面元件並非「零散消失」，而是在單一 commit 內一次性移除。移除證據可重跑：

```bash
git log --diff-filter=D --name-only --pretty=format:'COMMIT %H %ad %s' --date=short \
  origin/dev -- '*external_proof*' '*external-proof*'
# -> 全歷史僅一筆：1a8b0f44453d4de76a4a4b51b8c12c9a3005dbb4  2026-08-14
#    refactor(tooling): retire pr82 campaign and unsupported adapters
```

commit `1a8b0f44` 刪除之 external-proof 命名檔案共 **32 個**，另含 1 個同批移除之關聯檔案：

| 類別 | 數量 | 檔案 |
|---|---|---|
| GitHub Workflow | 1 | `.github/workflows/external-proof-followup.yml` |
| CLI `check_*` | 12 | `check_external_proof_acceptance_readiness.py`、`check_external_proof_closeout_queue.py`、`check_external_proof_fleet_notifications.py`、`check_external_proof_fleet_pickup_board.py`、`check_external_proof_followup_workflow.py`、`check_external_proof_handback_artifact.py`、`check_external_proof_handback_bundle.py`、`check_external_proof_handback_status_board.py`、`check_external_proof_handback_template.py`、`check_external_proof_issue_handback_scan.py`、`check_external_proof_issue_sync.py`、`check_external_proof_live_blockers.py`（均位於 `delivery_toolchain/e2e/`） |
| CLI `generate_*` | 1 | `delivery_toolchain/e2e/generate_external_proof_handback_skeleton.py` |
| CLI `sync_*` | 2 | `delivery_toolchain/e2e/sync_external_proof_escalation_comments.py`、`delivery_toolchain/e2e/sync_external_proof_fleet_issues.py` |
| CLI `update_*` | 1 | `delivery_toolchain/e2e/update_external_proof_handback_status_board.py` |
| 測試模組 | 15 | `tests/e2e/test_external_proof_{acceptance_readiness, closeout_queue, escalation_comments, fleet_issue_syncer, fleet_notifications_checker, followup_workflow, handback_artifact, handback_bundle, handback_skeleton_generator, handback_status_board, handback_status_board_updater, handback_template, issue_handback_scan, issue_sync_checker, live_blockers_checker}.py` |
| **小計（external-proof 命名）** | **32** | — |
| 同批移除之關聯檔案 | 1 | `delivery_toolchain/e2e/check_product_go_no_go.py`（非 external-proof 命名，但屬同一 PR #82 campaign） |

現況負向驗證（應為空）：

```bash
git ls-files | grep -Ei 'external.proof' | grep -E '\.(py|yml)$'   # -> 無輸出
ls .github/workflows/                                              # -> 7 支，無 external-proof-followup.yml
```

### 3.2 殘留檔案盤點：掃描邊界宣告與完整清單

#### 3.2.0 掃描邊界宣告（Scan Boundary）

「殘留」的判定會隨搜尋式而改變，因此本節先固定三個掃描邊界，全部以 `git ls-files` 追蹤範圍為母體，**並一律排除本報告自身**。排除動作寫在指令裡，不靠讀者心算，先固定自我排除變數：

```bash
SELF='docs/audits/ODP_DEPLOYMENT_DEAD_CODE_AUDIT.md'   # 本報告自身，三個邊界一律排除
```

下表「指令」欄即為完整可貼上執行的指令，其輸出行數等於「命中檔數」欄的數字，無須再做任何加減：

| 代號 | 定義 | 指令（已含本報告排除） | 命中檔數（不含本報告） |
|---|---|---|---|
| **S1 窄掃描** | 已退役 campaign 的**識別符形式**：`external` 與 `proof` 之間僅隔 1 個分隔字元（`-`、`_`、`.`、空白） | `git grep -Eil 'external[-_. ]proof' \| grep -vxF "$SELF"` | **20** |
| **S2 寬掃描** | 允許 `external` 與 `proof` 之間夾雜至多 2 個英文字（涵蓋 `external runtime proof`、`external data proof` 等散文寫法） | `git grep -Eil 'external[^[:alpha:]]{0,3}([[:alpha:]]+[^[:alpha:]]{1,3}){0,2}proof' \| grep -vxF "$SELF"` | **21** |
| **S3 檔名掃描** | 檔名本身帶 external-proof | `git ls-files \| grep -Ei 'external[-_.]?proof' \| grep -vxF "$SELF"` | **5**（皆為 S2 子集） |

> **未排除本報告時的原始數字**：S1 = 21、S2 = 22、S3 = 5（本報告檔名不含 external-proof，故 S3 不受影響）。本報告全文引用的 20 / 21 / 5 一律指上表**已排除**後的數字；§7.1 同時列出兩組數字並提供差值斷言。

**三邊界聯集 = 21 檔（S2 即聯集，S1 ⊂ S2，S3 ⊂ S2）。**

兩項邊界效應必須明確記錄，否則計數無法對帳：

1. **`docs/evidence/EXTERNAL_PROOF_HANDBACK_TEMPLATE.json` 是「僅檔名命中」的個案。**
   它在 S1 窄掃描下命中數為 **0**——因為檔內散文寫的是 `external runtime proof`（L4）與 `live external runtime proof`（L15），中間夾了 `runtime` 一字，不符合識別符形式。它在 S3（檔名）與 S2（寬掃描）命中。
   **本報告仍將其判定為殘留**，判定依據不是字串，而是**構件身分**：它是 PR #82 campaign 的 handback JSON Schema 範本，其唯一消費者 `check_external_proof_handback_template.py` 已於 `1a8b0f44` 刪除（見 §3.1），故現況為無消費者之孤立構件。
2. **S2 寬掃描會引入現行概念的偽陽性。** `external data proof`（確定性 source-stub / fixture 覆蓋）與 `external provider proof`（現行外部供應商證明）都是**存續中**的產品概念，與已退役 campaign 無關。實例：`PRODUCT_RELEASE_CLOSEOUT_PICKUP_BOARD.md:124`（`deterministic external data proof`）、`PRODUCT_RELEASE_CLOSEOUT_PLAYBOOK.md:128,133`（`External data proof ...`）、`PRODUCT_RELEASE_RISK_ACCEPTANCE.md:49`（`Live external provider proof`）。**因此 §3.2.1–§3.2.6 各表所列行號一律採用 S1 窄掃描結果**，以確保每一個被引用的行號都確實指向已退役的 campaign。

#### 3.2.0.1 命中檔案的分流：現行治理文件 vs 舊機制殘留

21 檔中有 1 檔並非殘留：

| 檔案 | S1 命中行 | 內容性質 | 判定 |
|---|---|---|---|
| `docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md` | L69, L350 | 本 Wave 之**現行治理規劃書**（本任務 source doc）。L69：「舊 External Proof Follow-up｜只存在舊 main，最新 dev 已移除｜不修舊 workflow；dev promotion 後自然退役。」L350：「dev promotion 後，舊 main 上已不存在於 dev 的 External Proof Follow-up 自然退役；不為它補第二套 proof。」兩處皆是**宣告退役政策本身**。 | **保留 (KEEP)**，不計入殘留 |

**因此：21 − 1 = 20 個舊機制殘留檔案**，逐檔列於 §3.2.1–§3.2.6。

#### 3.2.1 完全孤立之 JSON/MD 佇列與狀態板（5 檔，無 active code caller）

| # | 殘留檔案路徑 | 大小 | 類型 | S1 命中數 | S1 命中行號（完整） | 幽靈引用與現況分析 | 處置建議 |
|---|---|---|---|---|---|---|---|
| 1 | `docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json` | 20,420 B | 孤立 JSON | 43 | 10, 12, 13, 19, 29, 38, 62, 77, 87–89, 105, 121, 131–133, 149, 165, 175–177, 193, 208, 219–221, 237, 252, 263–265, 281, 299, 300, 310–312, 328, 346, 348, 357–359 | 記錄 7 個外部證明任務（#132–#138）；內含 `check_external_proof_closeout_queue.py`、`check_external_proof_live_blockers.py` 等已於 `1a8b0f44` 刪除之腳本指令；每個 task 條目引用 `generate_external_proof_handback_skeleton.py`、`check_external_proof_handback_template.py`、`check_external_proof_handback_artifact.py`。無任何 active code 讀取或寫入。 | **刪除 (DELETE)** |
| 2 | `docs/evidence/EXTERNAL_PROOF_HANDBACK_TEMPLATE.json` | 7,514 B | 孤立 JSON | **0**（僅 S3 檔名 + S2 寬掃描命中：L4, L15） | — | **邊界個案，判定依據見 §3.2.0 第 1 點。** 內文寫法為 `external runtime proof`（L4、L15），不符 S1 識別符形式，故 S1 命中為 0。判定為殘留的依據是構件身分：其唯一消費者 `check_external_proof_handback_template.py` 已於 `1a8b0f44` 刪除，現為無消費者之孤立 JSON Schema 範本。 | **刪除 (DELETE)** |
| 3 | `docs/evidence/EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json` | 6,145 B | 孤立 JSON | 13 | 4, 10, 18–20, 28, 38, 50, 62, 74, 86, 98, 110 | 外部證明交付狀態追蹤看板；每個 task 引用 `check_external_proof_handback_artifact.py`；header 引用 `update_external_proof_handback_status_board.py`、`check_external_proof_handback_bundle.py`。無 active code caller。 | **刪除 (DELETE)** |
| 4 | `docs/evidence/EXTERNAL_PROOF_HANDBACK_EXAMPLE.json` | 4,367 B | 孤立 JSON | 1 | 43 | 示範 handback JSON 格式；L43 引用 `PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json` 之 PR 查詢指令。無 active code caller。 | **刪除 (DELETE)** |
| 5 | `docs/evidence/EXTERNAL_PROOF_FLEET_PICKUP_BOARD.md` | 18,151 B | 孤立 MD | 51 | 1, 5, 9, 18–28, 36–38, 45–51, 142, 148, 155, 162, 169, 176, 183, 184, 190, 192, 208, 221, 227, 234, 235, 246–248, 255, 262, 269, 276, 282, 289, 290, 292, 299 | 描述 #132–#138 領取與交付說明；密集引用已刪除之 `check_external_proof_*.py` / `sync_external_proof_*.py` 腳本與 `external-proof-followup.yml`。 | **刪除 (DELETE)** |

#### 3.2.2 關閉/門禁/排程文件含幽靈引用（7 檔）

| # | 殘留檔案路徑 | 大小 | 類型 | S1 命中數 | S1 命中行號（完整） | 幽靈引用與現況分析 | 處置建議 |
|---|---|---|---|---|---|---|---|
| 6 | `docs/evidence/PRODUCT_RELEASE_CLOSEOUT_MANIFEST.md` | 19,773 B | 陳舊文件 | 33 | 29–41, 73, 103, 106, 109, 110, 112, 113, 117, 119, 121, 122, 125, 127–129, 131, 134, 135, 139, 154 | PR #82 關閉檢查清單；表格 L29–41 列出已刪除之 external-proof CLI 與 `external-proof-followup.yml`，詳細檢核清單 L103–154 引用已刪除流程。 | **重構/精簡 (REPLACE / UPDATE)** |
| 7 | `docs/evidence/PRODUCT_RELEASE_CLOSEOUT_PLAYBOOK.md` | 16,711 B | 陳舊文件 | 52 | 40–42, 147, 148, 151–153, 156–158, 161, 164, 167, 172, 173, 177, 178, 180, 182, 184, 185, 188, 189, 192, 193, 196, 197, 201, 202, 204, 206, 207, 211, 213, 215, 228, 243, 245, 247, 248, 250, 260–264, 266, 268–271 | PR #82 Closeout 操作手冊；大量 external proof checker 指令、boards 與 workflows 引用。**注意**：L128、L133 為 `External data proof`（現行確定性 fixture 概念，S2 才命中），非本 campaign 殘留，已排除於上列行號之外。 | **重構/精簡 (REPLACE / UPDATE)** |
| 8 | `docs/evidence/PRODUCT_RELEASE_CLOSEOUT_PICKUP_BOARD.md` | 15,298 B | 陳舊文件 | 30 | 20, 38, 42, 44–51, 54, 58, 62, 63, 65, 168–172, 174, 177, 178, 180, 184, 197, 199, 200, 204 | PR #82 領取狀態看板；引用 `EXTERNAL_PROOF_FLEET_PICKUP_BOARD.md`、`external-proof-followup.yml` 與多支已刪除 checker。**注意**：L124 為 `deterministic external data proof`（S2 才命中），非殘留，已排除。 | **重構/精簡 (REPLACE / UPDATE)** |
| 9 | `docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md` | 10,296 B | 陳舊文件 | 13 | 59–68, 72, 75, 77 | PR #82 Human/Ops GO/NO-GO 檢核表；L59–68 要求執行 10 個已刪除之 external proof 檢查指令；L72 起為 External Proof Blocking Tasks 段落。 | **重構/精簡 (REPLACE / UPDATE)** |
| 10 | `docs/evidence/PRODUCT_RELEASE_RISK_ACCEPTANCE.md` | 10,876 B | 陳舊文件 | 4 | 42, 54, 56, 97 | 記錄 deferred external-proof tasks（L42）；L56 引用已刪除之 `check_external_proof_handback_bundle.py`；L54、L97 引用 `PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json`。**注意**：L49 為 `Live external provider proof`（現行供應商證明，S2 才命中），非殘留，已排除。 | **重構/精簡 (REPLACE / UPDATE)** |
| 11 | `docs/evidence/PRODUCT_E2E_READINESS_REPORT.md` | 11,504 B | 陳舊文件 | 1 | 76 | L76 引用 `PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json` 作為 external proof closeout 追蹤來源。 | **重構/精簡 (REPLACE / UPDATE)** |
| 12 | `docs/evidence/PRODUCT_GRADE_E2E_GATE_RECONCILIATION.md` | 3,179 B | 陳舊文件 | 3 | 20, 21, 23 | 表格引用 `PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json` 與 `EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json` 作為 blocker 計數來源。 | **重構/精簡 (REPLACE / UPDATE)** |

#### 3.2.3 艦隊派遣/領取文件含幽靈引用（3 檔）

| # | 殘留檔案路徑 | 大小 | 類型 | S1 命中數 | S1 命中行號（完整） | 幽靈引用與現況分析 | 處置建議 |
|---|---|---|---|---|---|---|---|
| 13 | `docs/evidence/PRODUCT_GRADE_E2E_FLEET_ASSIGNMENT_LEDGER.md` | 11,410 B | 陳舊文件 | 12 | 30, 34, 38, 41–48, 54 | § Current External Proof Closeout 段落列出已刪除之 external proof 腳本完整指令（`generate_external_proof_handback_skeleton.py` 至 `check_external_proof_issue_sync.py`）。 | **重構/精簡 (REPLACE / UPDATE)** |
| 14 | `docs/evidence/PRODUCT_GRADE_E2E_FLEET_DISPATCH_QUEUE.json` | 34,088 B | 陳舊 JSON | 2 | 9, 11 | `current_remaining_queue` 指向 `PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json`；`dispatch_rule` 描述 external-proof routing。 | **重構/精簡 (REPLACE / UPDATE)** |
| 15 | `docs/evidence/PRODUCT_GRADE_E2E_FLEET_KICKOFF_RUNBOOK.md` | 7,750 B | 陳舊文件 | 14 | 6, 16–19, 89, 92–99 | 同 Fleet Assignment Ledger；兩處完整列出 external proof checker 指令鏈，包含 `generate_`, `check_`, `update_`, `sync_` 系列已刪除腳本。 | **重構/精簡 (REPLACE / UPDATE)** |

#### 3.2.4 Runtime 靜態驗證與艦隊修復紀錄（2 檔）

| # | 殘留檔案路徑 | 大小 | 類型 | S1 命中數 | S1 命中行號（完整） | 幽靈引用與現況分析 | 處置建議 |
|---|---|---|---|---|---|---|---|
| 16 | `docs/evidence/runtime/ODP-P10-LIVE-LEGACY-RETIREMENT-001/static-verification.json` | 53,256 B | 歷史紀錄 | 12 | 700, 704, 708, 712, 716, 720, 724, 728, 732, 736, 740, 744 | ODP-P10 legacy retirement 之靜態驗證結果；引用已刪除之 `test_external_proof_fleet_notifications_checker.py`（L700, 704, 708, 712）與 `test_external_proof_issue_sync_checker.py`（L716–744）。此為歷史快照紀錄，不需刪除但不應視為 active evidence。 | **歸檔保留 (ARCHIVE / KEEP)**：歷史紀錄 |
| 17 | `docs/evidence/fleet_dispatch/package10_20260726/ODP-P10-DEV-LANDING-FIX-001.md` | 8,335 B | 歷史紀錄 | 4 | 55, 72, 88, 89 | ODP-P10 dev landing 修復紀錄；L55 引用 `check_external_proof_closeout_queue.py`；L88–89 引用 `test_external_proof_handback_artifact.py` 測試執行結果。此為歷史紀錄。 | **歸檔保留 (ARCHIVE / KEEP)**：歷史紀錄 |

#### 3.2.5 Active 程式碼中之脈絡註解（1 檔）

| # | 殘留檔案路徑 | 大小 | 類型 | S1 命中數 | S1 命中行號 | 幽靈引用與現況分析 | External-Proof 軸處置 |
|---|---|---|---|---|---|---|---|
| 18 | `delivery_toolchain/e2e/check_product_release_gate.py` | 3,402 B | Active 程式碼 | 1 | 5 | docstring（L2–7）敘明本 gate `deliberately independent of the retired PR82 external-proof campaign`。此為歷史脈絡說明而非 dead code，程式功能與 external-proof 機制完全獨立。 | **保留 (KEEP)**：無須因 external-proof 退役而變更 |

> **跨軸說明（避免與 §4.3 表面矛盾）**：本檔在 §4.3 之處置為 **重構 (REPLACE / REFACTOR)**。兩者不衝突，因為分屬不同軸：本節（§3.2）判定的是「**external-proof 殘留軸**」——此檔不含 dead code，只有一行脈絡註解，故該軸為 KEEP；§4 判定的是「**元件生命週期軸**」——此檔因 Gate Registry 升級（`ODP-RELEASE-MANIFEST-GATES-001`）需要重構。§5.1 明確以雙軸呈現並標示此唯一重疊項。

#### 3.2.6 歷史稽核文件（2 檔）

| # | 殘留檔案路徑 | 大小 | 類型 | S1 命中數 | S1 命中行號（完整） | 幽靈引用與現況分析 | 處置建議 |
|---|---|---|---|---|---|---|---|
| 19 | `docs/audits/python-inventory-2026-08-13.csv` | 68,267 B | 歷史稽核 | 31 | 122–133, 149, 155, 156, 158, 255–269 | 2026-08-13 Python 盤點之 CSV 匯出；列出當時存在之 `check_external_proof_*.py`、`sync_external_proof_*.py`、`test_external_proof_*.py` 等檔案條目。此為歷史稽核快照。**注意**：L157 不命中（該列為 `sync_product_closeout_fleet_comment.py`），故不寫成 L155–158 連續區間。 | **歸檔保留 (ARCHIVE / KEEP)**：歷史紀錄 |
| 20 | `docs/audits/python-runtime-tooling-audit-2026-08-13.md` | 13,682 B | 歷史稽核 | 3 | 23, 142, 158 | 2026-08-13 Python 工具稽核報告；分析當時 external-proof CLI 群組之結論。此為歷史稽核。 | **歸檔保留 (ARCHIVE / KEEP)**：歷史紀錄 |

#### 3.2.7 External Proof 殘留盤點統計（對帳表）

```text
┌──────────────────────────────────────────────┬───────┬──────────────────────────────┐
│ 掃描與分流                                   │ 檔數  │ 依據                         │
├──────────────────────────────────────────────┼───────┼──────────────────────────────┤
│ S2 寬掃描聯集命中（不含本報告）              │ 21 檔 │ §3.2.0                       │
│  − 現行治理規劃書（非殘留，KEEP）            │ −1 檔 │ §3.2.0.1                     │
├──────────────────────────────────────────────┼───────┼──────────────────────────────┤
│ = 舊機制殘留檔案總數                         │ 20 檔 │ §3.2.1 – §3.2.6 逐檔編號 1–20│
└──────────────────────────────────────────────┴───────┴──────────────────────────────┘

┌────────────────────────────────────────┬───────┬──────────┬────────────────────────┐
│ 殘留檔案處置類別                       │ 數量  │ 章節     │ 處置                   │
├────────────────────────────────────────┼───────┼──────────┼────────────────────────┤
│ 完全孤立 JSON/MD（無 active caller）   │  5 檔 │ §3.2.1   │ 刪除 (DELETE)          │
│ 關閉/門禁/排程文件含幽靈引用           │  7 檔 │ §3.2.2   │ 重構精簡 (REPLACE)     │
│ 艦隊派遣/領取文件含幽靈引用            │  3 檔 │ §3.2.3   │ 重構精簡 (REPLACE)     │
│ Runtime / Fleet 歷史紀錄               │  2 檔 │ §3.2.4   │ 歸檔保留 (ARCHIVE)     │
│ Active 程式碼（僅脈絡註解）            │  1 檔 │ §3.2.5   │ 保留 (KEEP)            │
│ 歷史稽核文件                           │  2 檔 │ §3.2.6   │ 歸檔保留 (ARCHIVE)     │
├────────────────────────────────────────┼───────┼──────────┼────────────────────────┤
│ 合計                                   │ 20 檔 │          │ 5 刪 / 10 改 / 4 歸 / 1 留 │
└────────────────────────────────────────┴───────┴──────────┴────────────────────────┘
```

對帳恆等式（本報告全文採用之唯一口徑）：

- `5 (§3.2.1) + 7 (§3.2.2) + 3 (§3.2.3) + 2 (§3.2.4) + 1 (§3.2.5) + 2 (§3.2.6) = 20`
- 重構精簡合計 `7 + 3 = 10`；歸檔保留合計 `2 + 2 = 4`
- **§5.2 刪除清單中屬 external-proof 者 = 5 檔**（即 §3.2.1 全部），與本表一致。

> **與前版報告的差異說明**：前版 §1.1 誤植殘留總數為 19，而 §3.2 逐檔編號與 §3.2.7 統計為 20，兩者互相矛盾；§5.1 另誤植「6 個孤立 external-proof 狀態檔」，而 §5.2 刪除清單實際僅列 5 檔。本版統一為：S2 聯集 21 檔 → 扣除 1 檔現行治理規劃書 → 殘留 20 檔，其中孤立待刪 **5** 檔；並補上前版缺漏的掃描邊界宣告（§3.2.0），使 `EXTERNAL_PROOF_HANDBACK_TEMPLATE.json` 這個「僅檔名命中」個案有明確判定依據。前版 §1.1「13 支 `check_external_proof_*.py`」亦低估且分類不精確，本版改以 commit `1a8b0f44` 之實際刪除清單（1 workflow + 16 CLI + 15 tests）取代。

---
## 4. 全系統元件逐項盤點與使用證明 (Item-by-Item Usage Evidence)

本節針對倉庫內所有部署、Proof、Gate、Scheduler、Docker 與 IaC 相關元件，逐一列出其呼叫者、Workflow 參照、執行單元、測試涵蓋與處置判定。**本節共 91 列**（4.1: 7、4.2: 7、4.3: 18、4.4: 26、4.5: 15、4.6: 12、4.7: 6），處置分佈為 KEEP 78 / REPLACE 8 / DELETE·RETIRE 5，統計見 §5.1。

> **間接呼叫標記**：凡呼叫者為 `make <target>` 者，本報告一律同時標出 workflow 行號與 `Makefile` 行號，不將 `make` 目標簡寫成「workflow 直接呼叫該腳本」。

### 4.1 GitHub Workflows (`.github/workflows/`)

| 元件名稱與路徑 | 觸發事件 / 職責 | 呼叫者與相依分析 (Usage Evidence) | 處置判定 | 承接 Wave 任務 |
|---|---|---|---|---|
| `.github/workflows/deploy-dev.yml` (`Runtime Release`) | `workflow_dispatch` (inputs: `environment`, `release_sha`, `task_id`, `release_lease`；L4-22)<br>負責目前 dev 與 staging 之 Cloud Run 部署。 | **Caller**: 由 Supervisor / Human 觸發。<br>**Steps**: `check_runtime_admission.py` (L61)、`verify_deployment_health_backup_rollback.py` (L87)、`secret_scan.py` (L176)、`sast_scan.py` (L179)、`generate_sbom.py` (L182)、`validate_cloud_run_live_deployment.py` (L196)、`deploy_cloud_run_waji.sh` (L217)、`check_remote_staging_proof.py` (L226)。<br>**GCP 身分**: 全庫唯一含 WIF 認證之 workflow。<br>**問題**: 現場 build 映像檔；缺乏 production blue-green 路徑；門禁存在循環依賴。 | **重構 (REPLACE)** | `ODP-RUNTIME-RELEASE-SINGLE-PATH-001` (Wave 2) |
| `.github/workflows/promote-dev-to-main.yml` | `workflow_run` (CI completed on `dev`)<br>自動建立/合併 `dev -> main` PR。 | **Caller**: GitHub Actions 內部事件。<br>**Steps**: `npm ci` (L43)、`make product-release-gate` (L50 → `Makefile:104-105` → `check_product_release_gate.py --require-go`)、`gh pr create` (L52-57)、stamp `task-review-gate` (L96-101)、`gh pr merge --auto` (L113-117)。<br>**判定**: 純代碼合併流程，無 GCP 部署副作用。 | **保留 (KEEP)** | 維持現狀 |
| `.github/workflows/ci.yml` | `push` (main, dev), `pull_request`, `merge_group`<br>全系統持續整合主工作流。 | **Caller**: GitHub PR 與 Push 事件。<br>**Steps**: `classify_change_review_scope.py` (L61)、`check_code_boundaries.py` (L91)、`check_orchestrator_config.py` (L98)、`check_config_wiring.py` (L99)、`make api-contract` (L189 → `Makefile:63-64` → `check_drift.py`)、`delivery_toolchain/load/.../run.py` (L250)、`make product-e2e-gate` (L313 → `Makefile:100-102` → `check_product_release_gate.py --dev-merge` 與 `run_product_e2e.sh`)。<br>**判定**: 核心品質門禁，不可或缺。 | **保留 (KEEP)** | 維持現狀 |
| `.github/workflows/merge-queue-review-gate.yml` | `merge_group` (checks_requested)<br>為 Merge Queue 重新驗證並蓋印 `task-review-gate`。 | **Caller**: GitHub Merge Queue。<br>**Steps**: 透過 GitHub API 讀取 PR head 之 review 狀態並蓋印至 merge ref。 | **保留 (KEEP)** | 維持現狀 |
| `.github/workflows/tooling-scope-review-gate.yml` | `pull_request` (branches: dev)<br>針對純開發工具改動自動審查。 | **Caller**: GitHub PR。<br>**Steps**: `classify_change_review_scope.py` (L30)，若為純 tooling 則自動 stamp `task-review-gate`。 | **保留 (KEEP)** | 維持現狀 |
| `.github/workflows/emgi-consumer-boundary.yml` | `pull_request` (branches: dev)<br>驗證 EMGI 資料平台邊界。 | **Caller**: GitHub PR。<br>**Steps**: `validate_emgi_consumer_boundary.mjs` (L23) 與 `test_emgi_consumer_boundary.mjs`。 | **保留 (KEEP)** | 維持現狀 |
| `.github/workflows/assisted-intake-design-validation.yml` | `pull_request`, `workflow_dispatch`<br>驗證 Assisted Intake 設計與 OpenAPI/SQL。 | **Caller**: GitHub PR / 手動。<br>**Steps**: `validate_assisted_listing_intake_design.py` (L76)、`build_validate_assisted_listing_intake.py` (L143)、Redocly lint、PostgreSQL schema apply。 | **保留 (KEEP)** | 維持現狀 |

---

### 4.2 部署與執行期腳本 (`product_ops/deployment/`)

| 元件名稱與路徑 | 主要責任與對外介面 | 呼叫者與相依分析 (Usage Evidence) | 處置判定 | 承接 Wave 任務 |
|---|---|---|---|---|
| `product_ops/deployment/deploy_cloud_run_waji.sh` | Cloud Run 服務 (API, Web) 與 Jobs (Migration, Worker, Scheduler) 部署主腳本，共 642 行。內含 Cloud Scheduler trigger upsert 函式。 | **Caller**: `.github/workflows/deploy-dev.yml` (L217)。<br>**Sources**: `cloud_run_release_traffic.sh` (L76)。<br>**呼叫 `validate_cloud_run_live_deployment.py`**: L79 (`preflight`)、L267 (`resolve-latest-execution`，指令自 L266 之 `run_locked_python \` 續行)、L294 (`jobs-smoke`)、L361 (`compatibility-smoke`)、L571 (`smoke`)。<br>**呼叫 `sign_images.sh`**: L232, L524。<br>**呼叫 `check_live_e2e_gate.py`**: L624。<br>**建置 Dockerfile**: L239 (api), L240 (worker), L241 (scheduler), L519 (web)。<br>**問題**: 內含 `docker build`；流量推進僅支援單一環境 100% 切換；`upsert_scheduler_trigger` 無簽名 lease 驗證。 | **重構 (REPLACE / REFACTOR)**：抽離 build 步驟（改為接收 release manifest digest）；擴充支援 prod blue-green；`upsert_scheduler_trigger` 須綁定 release lease 授權。 | `ODP-PROD-BLUEGREEN-PRIMITIVES-001` (Wave 1), `ODP-RUNTIME-RELEASE-SINGLE-PATH-001` (Wave 2) |
| `product_ops/deployment/bluegreen_release.py` | Production blue-green 發布原語與回滾狀態機 (1237 行)。實作 Green 0% tag smoke → atomic 100% traffic switch、Scheduler pause/resume with job digest switching、Data platform selector / snapshot pointer recovery 與完整 rollback 序列；提供 CLI (subcommands: `smoke-traffic`, `switch-traffic`, `switch-scheduler`, `switch-data-platform`, `release-rollback`) 與 Python API。 | **Caller**: CLI 進入點 (`bluegreen_release.py:1054-1234`)；由 `EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md` §8 明訂之生產發布原語。<br>**Tests**: `tests/ops/test_bluegreen_release.py` (完整 37 項單元與整合測試，包含 service targets、scheduler triggers、data platform selectors、atomic switches、dry-run 與 full rollback 驗證)。<br>**Boundary**: `docs/audits/code-boundary-inventory.csv:621` (`product_operations_tooling`)。<br>**GCP 原語**: 內含 `gcloud run services update-traffic`、`gcloud scheduler jobs` 變更能力（見 §2）。 | **保留 (KEEP)** | `ODP-PROD-BLUEGREEN-PRIMITIVES-001` (Wave 1) |
| `product_ops/deployment/cloud_run_job_entrypoint.py` | Cloud Run Job 容器內進入點，支援 `migrate`, `worker`, `scheduler` 子命令。 | **Caller**: 由 Cloud Run Jobs 在容器啟動時執行；`deploy_cloud_run_waji.sh:478-479` 以 `--args="product_ops/deployment/cloud_run_job_entrypoint.py,worker,--max-jobs,1"` 帶入。<br>**Tests**: `tests/ops/test_cloud_run_job_entrypoint.py`。 | **保留 (KEEP)** | 維持現狀 |
| `product_ops/deployment/cloud_run_release_traffic.sh` | Bash 流量與 Scheduler trigger 控制/rollback 函式庫（189 行，無執行位元，僅定義函式）。 | **Caller**: 由 `deploy_cloud_run_waji.sh` source (L76)。<br>**Functions**: `capture_service_traffic` (L9)、`service_snapshot_url` (L20)、`rollback_release_traffic` (至 L75)、`capture_scheduler_trigger` (**L77-95**)、`restore_scheduler_trigger` (**L97-…**)。<br>**Tests**: `tests/ops/test_cloud_run_live_deployment.py`、`tests/ops/test_runtime_config_code_closeout.py:167-171`。 | **保留並擴充 (KEEP & EXPAND)** | `ODP-PROD-BLUEGREEN-PRIMITIVES-001` (Wave 1) |
| `product_ops/deployment/cloud_run_traffic.py` | Python 輔助工具，解析 Cloud Run JSON 描述檔並產生流量復原參數。 | **Caller**: `cloud_run_release_traffic.sh`（`ODP_TRAFFIC_HELPER`，L6）。<br>**Tests**: `tests/ops/test_cloud_run_live_deployment.py`。 | **保留 (KEEP)** | `ODP-PROD-BLUEGREEN-PRIMITIVES-001` (Wave 1) |
| `product_ops/deployment/cloud_scheduler_trigger.py` | Python 輔助工具，解析 Cloud Scheduler JSON 描述檔、產生復原參數並比對漂移。 | **Caller**: `cloud_run_release_traffic.sh`（`ODP_SCHEDULER_HELPER`，L7；於 L91, L94, L104, L121 呼叫 `validate` / `write-absent` / `exists` / `restore-args`）。<br>**Tests**: `tests/ops/test_cloud_run_live_deployment.py`。 | **保留 (KEEP)** | `ODP-PROD-BLUEGREEN-PRIMITIVES-001` (Wave 1) |
| `product_ops/deployment/validate_cloud_run_live_deployment.py` | Cloud Run 部署各階段驗證器 (preflight, compatibility-smoke, smoke, jobs-smoke, resolve-latest-execution)。 | **Caller**: `deploy-dev.yml` (L196), `deploy_cloud_run_waji.sh` (L79, L267, L294, L361, L571)。<br>**Tests**: `tests/ops/test_cloud_run_live_deployment.py`。<br>**判定**: 產出無機密之標準 JSON 收據；本身不執行 GCP 變更（見 §2）。 | **保留並重用 (KEEP)** | `ODP-RELEASE-EVIDENCE-RECEIPTS-001` (Wave 1) |

#### 4.2.1 Cloud Scheduler 建立/更新/回滾完整呼叫鏈（逐行證據）

依據驗收條件第 1 項（cron / runtime unit 逐項證明），此處給出 Cloud Scheduler trigger 全生命週期之精確行號。**前版報告在此處有兩處行號錯誤（函式結束行寫成 480、呼叫處寫成 575-580），本版已更正並逐項機器驗證（見 §7）。**

| 階段 | 檔案 | 精確行號 | 內容 |
|---|---|---|---|
| **1. Cron 值注入** | `.github/workflows/deploy-dev.yml` | **L106-107** | `WORKER_SCHEDULE_NAME: ${{ vars.ODP_CLOUD_SCHEDULER_WORKER_TRIGGER }}`、`SCHEDULER_SCHEDULE_NAME: ${{ vars.ODP_CLOUD_SCHEDULER_SCHEDULER_TRIGGER }}` |
| | `.github/workflows/deploy-dev.yml` | **L109** | `ODP_CLOUD_SCHEDULER_SERVICE_ACCOUNT: ${{ vars.ODP_CLOUD_SCHEDULER_SERVICE_ACCOUNT }}` |
| | `.github/workflows/deploy-dev.yml` | **L110-112** | `ODP_WORKER_CRON`、`ODP_SCHEDULER_CRON`、`ODP_SCHEDULER_TIME_ZONE`，皆自 GitHub environment `vars` 注入 |
| **2. 必填校驗** | `product_ops/deployment/deploy_cloud_run_waji.sh` | **L34-39** | `: "${WORKER_SCHEDULE_NAME:?...}"` 等 6 個 `:?` fail-closed 斷言（含兩個 cron 與 time zone） |
| **3. 部署前快照** | `product_ops/deployment/deploy_cloud_run_waji.sh` | **L325-326** | `capture_scheduler_trigger "${SCHEDULER_SCHEDULE_NAME}" ...`、`capture_scheduler_trigger "${WORKER_SCHEDULE_NAME}" ...` |
| | `product_ops/deployment/cloud_run_release_traffic.sh` | **L77-95** | `capture_scheduler_trigger()` 定義：`gcloud scheduler jobs list --filter` → 存在則 `describe --format=json` 寫入快照並 `validate`，不存在則 `write-absent` |
| **4. Job IAM 綁定** | `product_ops/deployment/deploy_cloud_run_waji.sh` | **L434-441** | 對 scheduler / worker candidate job 迴圈 `gcloud run jobs add-iam-policy-binding --member="serviceAccount:${ODP_CLOUD_SCHEDULER_SERVICE_ACCOUNT}" --role="roles/run.invoker"` |
| **5. Upsert 函式定義** | `product_ops/deployment/deploy_cloud_run_waji.sh` | **L443-470** | `upsert_scheduler_trigger()`：L449-453 以 `gcloud scheduler jobs describe` 偵測存在性決定 `action=create\|update`；L454-457 依 action 切換 `--headers` / `--update-headers`；L458-469 執行 `gcloud scheduler jobs "${action}" http`，綁定 `--schedule`、`--time-zone`(`ODP_SCHEDULER_TIME_ZONE`)、`--uri`(`https://run.googleapis.com/v2/.../jobs/<job>:run`，L447)、`--http-method=POST`、`--oauth-service-account-email`(`ODP_CLOUD_SCHEDULER_SERVICE_ACCOUNT`)、`--oauth-token-scope` |
| **6. 呼叫點（共 2 處）** | `product_ops/deployment/deploy_cloud_run_waji.sh` | **L579-582** | 第 1 次：`upsert_scheduler_trigger "${SCHEDULER_SCHEDULE_NAME}" "${SCHEDULER_CANDIDATE_JOB}" "${ODP_SCHEDULER_CRON}"` |
| | `product_ops/deployment/deploy_cloud_run_waji.sh` | **L583-586** | 第 2 次：`upsert_scheduler_trigger "${WORKER_SCHEDULE_NAME}" "${WORKER_CANDIDATE_JOB}" "${ODP_WORKER_CRON}"` |
| | | **合併區間 L579-586** | 兩次呼叫緊鄰，前置 `SCHEDULER_ROLLBACK_ARMED=true` 於 L578 |
| **7. 失敗回滾** | `product_ops/deployment/deploy_cloud_run_waji.sh` | **L141-173** | `handle_deployment_exit()`；L158-169 於 `SCHEDULER_ROLLBACK_ARMED=true` 時呼叫 `restore_scheduler_trigger`（L160-162 scheduler、L163-165 worker） |
| | `product_ops/deployment/deploy_cloud_run_waji.sh` | **L174** | `trap handle_deployment_exit EXIT` |
| | `product_ops/deployment/cloud_run_release_traffic.sh` | **L97-** | `restore_scheduler_trigger()` 定義：快照顯示原本不存在則 `gcloud scheduler jobs delete`（L106-109）；否則以 `restore-args`（L121）重建，並依原狀 `pause`(L153)/`resume`(L161) |
| **8. 摘要輸出** | `product_ops/deployment/deploy_cloud_run_waji.sh` | **L641-642** | `echo "Worker Job: ... (${WORKER_SCHEDULE_NAME})"`、`echo "Scheduler Job: ... (${SCHEDULER_SCHEDULE_NAME})"` |

**判定**：Cloud Scheduler 呼叫鏈具備完整的 capture → upsert → 失敗自動 restore 保護，屬健全設計，**保留**。唯一缺口是 `upsert_scheduler_trigger` 未綁定 release lease 授權（見 §2 旁路分析），由 `ODP-PROD-BLUEGREEN-PRIMITIVES-001` 承接。

---
### 4.3 門禁檢查、收據與驗收工具 (`delivery_toolchain/release/`, `delivery_toolchain/e2e/`)

| 元件名稱與路徑 | 主要責任與對外介面 | 呼叫者與相依分析 (Usage Evidence) | 處置判定 | 承接 Wave 任務 |
|---|---|---|---|---|
| `delivery_toolchain/release/check_runtime_admission.py` | 發布入場檢查，驗證 release_sha、task_id、lease 及 Gate 0–6 registry。 | **Caller**: `deploy-dev.yml` (L61)。<br>**Imports**: `from delivery_toolchain.e2e.check_release_gate_registry import check_candidate_ancestry` (L32)。<br>**問題**: Lease 僅做 regex 檢查（非簽名授權）；要求 Gate 0–6 全過造成循環依賴；僅支援 dev/staging。 | **替換 (REPLACE)**：由具簽名與 CAS 狀態機之權威驗證器取代。 | `ODP-RELEASE-ADMISSION-AUTHORITY-001` (Wave 0) |
| `delivery_toolchain/release/release_manifest.py` | Release Manifest 綱要定義與驗證函式庫。定義 Release ID、Candidate SHA、Manifest Digest、Policy Digests 與容器映像檔不可變 SHA256 digest 規格。 | **Caller**: `delivery_toolchain/e2e/check_release_gate_registry.py` (L47), `delivery_toolchain/release/migrate_gate_registry.py` (L20), `delivery_toolchain/release/release_receipts.py` (L28), `tests/release/test_release_manifest.py` (L16)。<br>**Boundary**: `docs/audits/code-boundary-inventory.csv:242`。 | **保留 (KEEP)** | `ODP-RELEASE-MANIFEST-GATES-001` (Wave 0) |
| `delivery_toolchain/release/migrate_gate_registry.py` | Gate Registry v1 至 v2 分階段綱要遷移 CLI 工具。 | **Caller**: `docs/evidence/gates/README.md` (L227, L234 操作指引), `tests/release/test_release_manifest.py` (L12)。<br>**Boundary**: `docs/audits/code-boundary-inventory.csv:241`。 | **保留 (KEEP)** | `ODP-RELEASE-MANIFEST-GATES-001` (Wave 0) |
| `delivery_toolchain/release/release_receipts.py` | 統一發布證據收據 (Release Receipts) 與機密遮蔽 (Secret Redaction) 核心函式庫。定義 `STAGE_ENVIRONMENT` 階段環境綁定 (`candidate-built`/`dev-verified` -> dev, `staging-verified` -> staging, `prod-admitted`/`prod-switched`/`release-complete` -> prod)、收據種類 (`deployment`, `verification`, `cleanup`, `rollback`)、`build_receipt` / `validate_receipt` / `read_receipt` / `write_receipt`，並宣告不可變之 `RUNTIME_RELEASE_ARTIFACT_ALLOWLIST` (8 項)。 | **Caller**: `tests/ops/test_release_receipts.py`（契約測試完整驗證階段綁定、機密遮蔽、allowlist 與 `.github/workflows/deploy-dev.yml` / `product_ops/deployment/deploy_cloud_run_waji.sh` 產出路徑之一致性）。<br>**Workflow & Script 對齊**: 凍結之 `RUNTIME_RELEASE_ARTIFACT_ALLOWLIST` (L59-68) 逐項對齊 `deploy-dev.yml` (L254-261 之 `actions/upload-artifact@v4`) 與 `deploy_cloud_run_waji.sh` (`PREFLIGHT_REPORT`, `SMOKE_REPORT`, `MIGRATION_COMPAT_REPORT`, `LIVE_E2E_REPORT`, `cloud-run-jobs/*-validation.json`)。<br>**Boundary**: `docs/audits/code-boundary-inventory.csv:243` (`development_delivery_tooling`)。 | **保留 (KEEP)** | `ODP-RELEASE-EVIDENCE-RECEIPTS-001` (Wave 1) |
| `delivery_toolchain/e2e/check_release_gate_registry.py` | 靜態 Gate 0–6 機器可讀註冊表驗證器。 | **Caller**: `Makefile:82-83` (`make release-gate-registry`)；`check_product_release_gate.py:26,40`（以 subprocess 呼叫）；`check_runtime_admission.py:32`（import）。<br>**間接**: `Makefile:100` 之 `product-e2e-gate` 以 prerequisite 形式依賴 `release-gate-registry`。<br>**Tests**: `tests/e2e/test_release_gate_registry.py`。<br>**問題**: 缺少分階段狀態機支援。 | **重構 (REPLACE / REFACTOR)**：升級為多階段 Gate Registry 驗證器。 | `ODP-RELEASE-MANIFEST-GATES-001` (Wave 0) |
| `delivery_toolchain/e2e/check_product_release_gate.py` | 產品發布門禁總成，整合 gate registry 與 deterministic E2E 收據。 | **Caller**: `Makefile:101` (`product-e2e-gate`，`--dev-merge`)、`Makefile:105` (`product-release-gate`，`--require-go`)；上游分別為 `ci.yml:313` 與 `promote-dev-to-main.yml:50`。<br>**Imports**: `product_e2e_receipt` (L56)。 | **重構 (REPLACE / REFACTOR)**：配合新 gate registry 調整參數與驗證邏輯。 | `ODP-RELEASE-MANIFEST-GATES-001` (Wave 0) |
| `delivery_toolchain/e2e/check_live_e2e_gate.py` | 部署後線上即時 E2E 驗收門禁，透過真實 HTTP 操作驅動完整業務路徑與 Worker 執行。 | **Caller**: `deploy_cloud_run_waji.sh` (L624)，於 `DEPLOYMENT_COMMITTED` 之前執行，失敗會落入 EXIT trap 觸發流量與 scheduler 回滾（`deploy_cloud_run_waji.sh:590-596` 註解）。<br>**Dependencies**: `check_live_production_data.py` (L53)。<br>**Tests**: `tests/e2e/test_live_e2e_gate.py`。 | **保留 (KEEP)** | `ODP-RELEASE-EVIDENCE-RECEIPTS-001` (Wave 1) |
| `delivery_toolchain/e2e/check_live_production_data.py` | 直接對接 PostgreSQL 資料庫，核對真實資料平面實體與特徵。 | **Caller**: `check_live_e2e_gate.py` (L53，`LIVE_DATA_GATE` 常數)。<br>**Tests**: `tests/e2e/test_live_production_data_gate.py`。 | **保留 (KEEP)** | `ODP-RELEASE-EVIDENCE-RECEIPTS-001` (Wave 1) |
| `delivery_toolchain/e2e/check_remote_staging_proof.py` | 檢查 Staging 環境端點健康與 Release SHA 一致性。 | **Caller**: `deploy-dev.yml` (L226)。<br>**Tests**: `tests/e2e/test_remote_staging_proof_checker.py`。 | **重構 (REPLACE / REFACTOR)**：適配短生命週期 Ephemeral Staging 之動態 URL 與 TTL。 | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` (Wave 3) |
| `delivery_toolchain/e2e/verify_deployment_health_backup_rollback.py` | 啟動本地 Docker Compose 執行備份與回滾演練。 | **Caller**: `deploy-dev.yml` (L87)。<br>**Compose**: `COMPOSE_FILE = "infra/docker/docker-compose.e2e.yml"` (L21)。<br>**問題**: 於 CI runner 內部模擬，與真實 GCP Cloud SQL / Cloud Run 架構脫節。 | **替換 (REPLACE)**：由真實 Ephemeral Staging 之備份還原演練收據取代；本檔屬 §5.3 替換清單，**不列入 §5.2 刪除清單**。 | `ODP-EPHEMERAL-STAGING-IAC-001` (Wave 1) |
| `delivery_toolchain/e2e/run_product_e2e.sh` | 本地 / CI 確定性 E2E 測試主執行檔 (Docker Compose + Playwright + Pytest)。 | **Caller**: `Makefile:102` (`product-e2e-gate`)，上游為 `ci.yml:313` 之 `make product-e2e-gate`。<br>**Compose**: `infra/docker/docker-compose.e2e.yml` (L24)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/e2e/run_python_e2e_tests.py` | 執行 Python E2E 測試套件並產生結果。 | **Caller**: `run_product_e2e.sh` (L138)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/e2e/record_playwright_results.py` | 擷取 Playwright 執行輸出並結構化寫入 JSON 證據。 | **Caller**: `run_product_e2e.sh` (L123)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/e2e/generate_product_e2e_receipt.py` & `product_e2e_receipt.py` | 彙總產生並校驗 `PRODUCT_E2E_EXECUTION_RECEIPT.json`。 | **Caller**: `run_product_e2e.sh` (L141)、`check_product_release_gate.py` (L56 import)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/e2e/seed_product_e2e_data.py` | 為 E2E 測試注入確定性測試資料。 | **Caller**: `run_product_e2e.sh` (L77)。<br>**Tests**: `tests/e2e/test_seed_product_e2e_data.py`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/e2e/worker_heartbeat.py` | 測試 Worker 背景心跳與健康狀態。 | **Caller**: E2E 測試腳本。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/e2e/check_product_grade_ci_gates.py` | 靜態校驗 Operator Console 畫面標籤合約與 HTML SHA256。 | **Caller**: `tests/e2e/test_package10_product_grade_ci_gate.py`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/e2e/_release_target.py` & `_support.py` | E2E 工具鏈共用函式庫。 | **Caller**: `delivery_toolchain/e2e/*.py` 多處 import。 | **保留 (KEEP)** | 維持現狀 |

---

### 4.4 安全、治理與合約工具 (`delivery_toolchain/security/`, `governance/`, `openapi/`, `git/`, `github/`)

| 元件名稱與路徑 | 主要責任與對外介面 | 呼叫者與相依分析 (Usage Evidence) | 處置判定 | 承接 Wave 任務 |
|---|---|---|---|---|
| `delivery_toolchain/security/secret_scan.py` | 原始碼機密掃描。 | **Caller**: `deploy-dev.yml` (L176), `tests/security/test_supply_chain_security_gate.py`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/security/sast_scan.py` | Python 靜態安全性分析 (Bandit)。 | **Caller**: `deploy-dev.yml` (L179), `tests/security/test_supply_chain_security_gate.py`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/security/generate_sbom.py` | CycloneDX SBOM 生成工具。 | **Caller**: `deploy-dev.yml` (L182), `tests/security/test_supply_chain_security_gate.py`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/security/generate_oss_notice.py` | 第三方授權聲明生成工具。 | **Caller**: `tests/security/test_oss_notice.py`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/security/attestation.py` | In-toto / SLSA 證明產生器。 | **Caller**: `tests/security/test_supply_chain_security_gate.py`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/security/sign_images.sh` | Cosign 映像檔簽章與驗章封裝（mode `755`）。 | **Caller**: `deploy_cloud_run_waji.sh` (L232, L524，皆為 `sign_images.sh verify`)。 | **保留 (KEEP)** | `ODP-RELEASE-MANIFEST-GATES-001` (Wave 0) |
| `delivery_toolchain/governance/check_code_boundaries.py` | 產品程式碼與交付工具邊界檢查。 | **Caller**: `Makefile:34-35` (`make boundary-check`), `ci.yml` (L91)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/governance/check_config_wiring.py` | 設定檔與常數連結檢查。 | **Caller**: `ci.yml` (L99), `tests/tooling/test_config_wiring.py`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/governance/check_orchestrator_config.py` | Supervisor 配置檢查。 | **Caller**: `ci.yml` (L98)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/governance/classify_change_review_scope.py` | PR 變更路徑分類器 (tooling vs product)。 | **Caller**: `ci.yml` (L61), `tooling-scope-review-gate.yml` (L30)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/governance/validate_assisted_listing_intake_design.py` | 子系統設計合約交叉驗證器。 | **Caller**: `assisted-intake-design-validation.yml` (L76)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/governance/validate_emgi_consumer_boundary.mjs` | EMGI Producer/Consumer 邊界檢查。 | **Caller**: `emgi-consumer-boundary.yml` (L23)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/git/task_start.sh` | 從 `dev` 建立標準 task branch。 | **Caller**: Auto Worker / Developer 進入 task 標準工具。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/git/task_finalize.sh` | 推送 task branch、開啟 PR 並自動提交 review。 | **Caller**: Auto Worker / Developer 提交審查標準工具。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/git/worker_commit.py` | Worker 安全 commit 包裝器（隔離 index、範圍校驗）。 | **Caller**: Auto Worker commit 標準工具。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/git/check_commit_scope.py` | Git hook / 提交範圍防護。 | **Caller**: `.githooks/pre-commit`, `worker_commit.py`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/git/check_commit_trailers.py` | Commit message trailers (`Task-ID`, `LLM-Agent`, `Reviewer`) 驗證。 | **Caller**: `.githooks/commit-msg`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/git/check_task_delivery_identity.py` | 驗證 Task 與 PR delivery identity。 | **Caller**: `scripts/ai_status.py`, `task_finalize.sh`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/github/apply_branch_protection.py` | GitHub 分支保護規則套用工具。 | **Caller**: 維運腳本 / CI。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/github/check_pr_merge_eligibility.py` | PR 合併資格檢查器。 | **Caller**: `tests/tooling/test_pr_merge_eligibility.py`。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/openapi/check_drift.py` | OpenAPI 合約漂移檢查器。 | **Caller**: `Makefile:63-64` (`make api-contract`)，上游為 `ci.yml:189` 之 `run: make api-contract`（間接呼叫）。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/openapi/export_openapi.py` & `generate_client.py` | OpenAPI 匯出與 TypeScript client 產生器。 | **Caller**: `Makefile:66` (`make api-contract-refresh`)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/openapi/build_validate_assisted_listing_intake.py` | 子系統 OpenAPI 建置與結構驗證器。 | **Caller**: `assisted-intake-design-validation.yml` (L143)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/load/assisted_listing_intake/run.py` | 容量與負載基準測試器。 | **Caller**: `ci.yml` (L250，`performance-gate` job)。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/chaos/assisted_listing_intake/run.py` | 混沌演練執行器。 | **Caller**: 演練腳本。 | **保留 (KEEP)** | 維持現狀 |
| `delivery_toolchain/release/assisted_listing_intake/` (`config.py`, `drills.py`, `gates.py`, `run.py`) | 子系統特定之發布演練與 Canary 判定工具。 | **Caller**: `tests/ops/test_assisted_listing_intake_release.py`。<br>**判定**: 屬模組專屬演練工具，非全系統 release entrypoint。 | **保留 (KEEP)** | 維持現狀 |

---
### 4.5 容器定義與基礎設施 (`infra/docker/`, `infra/terraform/`, `infra/k8s/`, `infra/mlflow/`, `docker-compose*.yml`)

| 元件名稱與路徑 | 主要責任與對外介面 | 呼叫者與相依分析 (Usage Evidence) | 處置判定 | 承接 Wave 任務 |
|---|---|---|---|---|
| `infra/docker/api.Dockerfile` | ODay Plus API 正規 Dockerfile (FastAPI, 8000 port, pyproject 依賴)。 | **Caller**: `deploy_cloud_run_waji.sh` (L239), 根目錄 `docker-compose.yml` (L27), `infra/docker/docker-compose.e2e.yml` (L5), `docs/deployment/GCP_DEPLOY_GUIDE.md` (L42)。 | **保留 (KEEP)** | `ODP-RELEASE-MANIFEST-GATES-001` (Wave 0) |
| `infra/docker/web.Dockerfile` | ODay Plus Web 正規 Dockerfile (Next.js, 3000 port, multi-stage)。 | **Caller**: `deploy_cloud_run_waji.sh` (L519), 根目錄 `docker-compose.yml` (L80), `infra/docker/docker-compose.e2e.yml` (L28)。<br>**Tests**: `tests/ops/test_cloud_run_live_deployment.py` (L2636)。 | **保留 (KEEP)** | `ODP-RELEASE-MANIFEST-GATES-001` (Wave 0) |
| `infra/docker/worker.Dockerfile` | Worker 正規 Dockerfile (ODay worker / Cloud Run job)。 | **Caller**: `deploy_cloud_run_waji.sh` (L240)。<br>**Static checks**: `validate_cloud_run_live_deployment.py` (L171), `tests/ops/test_cloud_run_live_deployment.py` (L2652)。 | **保留 (KEEP)** | `ODP-RELEASE-MANIFEST-GATES-001` (Wave 0) |
| `infra/docker/scheduler.Dockerfile` | Scheduler 正規 Dockerfile (Cloud Run scheduler job)。 | **Caller**: `deploy_cloud_run_waji.sh` (L241)。<br>**Static checks**: `validate_cloud_run_live_deployment.py` (L172), `tests/ops/test_cloud_run_live_deployment.py` (L2653)。 | **保留 (KEEP)** | `ODP-RELEASE-MANIFEST-GATES-001` (Wave 0) |
| `infra/docker/data-platform.Dockerfile` | Data Platform / EMGI runtime Dockerfile。 | **Caller**: `infra/k8s/data-platform/README.md` (L41 建置指令)。<br>**Tests**: `tests/integration/test_data_platform_deployment_contract.py` (L12)。 | **保留 (KEEP)** | `DPF-EMGI-LIVE-ROLLOUT-001` (Wave 1) |
| `infra/docker/Dockerfile.api` | **過期 Dockerfile** (572 B；暴露 8080 port，寫死 pip install 套件列表)。 | **全庫引用者僅 2 處**（`git grep -n 'Dockerfile\.api'`）：`infra/docker/docker-compose.yml` (L21，本身亦列為刪除)、`docs/deployment/RELEASE_BASELINE.md` (L10，文件表格)。**無任何 workflow、腳本或測試引用**；`deploy_cloud_run_waji.sh` 已改用 `api.Dockerfile` (L239)。 | **刪除 (DELETE)**<br>**連動編輯**：須同步更新 `docs/deployment/RELEASE_BASELINE.md:10`。 | `ODP-DEPLOY-DEAD-CODE-REMOVAL-001` (Wave 2) |
| `infra/docker/Dockerfile.web` | **過期 Dockerfile** (1,548 B；暴露 8080 port)。 | **全庫引用者 0 處**（`git grep -n 'Dockerfile\.web'` 除本報告外無輸出）。`deploy_cloud_run_waji.sh` 已改用 `web.Dockerfile` (L519)。 | **刪除 (DELETE)**：無連動編輯 | `ODP-DEPLOY-DEAD-CODE-REMOVAL-001` (Wave 2) |
| `infra/docker/docker-compose.yml` | **過期 Docker Compose** (737 B；僅 postgres 與引用 `Dockerfile.api` 的 api)。 | **引用者**: `infra/docker/README.md` (L8 使用說明)、`docs/deployment/RELEASE_BASELINE.md` (L11)。<br>**問題**: 與根目錄功能完整之 `docker-compose.yml` 重複且引用過期 Dockerfile。 | **刪除或更新 (DELETE / RETIRE)**<br>**連動編輯**：`infra/docker/README.md:5-19`、`docs/deployment/RELEASE_BASELINE.md:11`。 | `ODP-DEPLOY-DEAD-CODE-REMOVAL-001` (Wave 2) |
| `infra/docker/docker-compose.e2e.yml` | E2E 測試用 Docker Compose (api, web, postgres, source-stub)。 | **Caller**: `run_product_e2e.sh` (L24), `verify_deployment_health_backup_rollback.py` (L21), `docs/testing/PRODUCT_E2E_ENVIRONMENT.md` (L48)。 | **保留 (KEEP)** | 維持現狀 |
| `docker-compose.yml` (根目錄) | 本地開發完整 multi-service stack (migrate, api, worker, scheduler, web)。 | **Caller**: 開發者手動 `docker compose up --build`；引用 `api.Dockerfile` (L27)、`web.Dockerfile` (L80)。 | **保留 (KEEP)** | 維持現狀 |
| `infra/terraform/` (`*.tf`, `audit/`, `env/`) | Cloud Run、Cloud SQL、KMS、IAM、GCS 等長期基礎設施 Terraform 模組。 | **Caller**: IaC 佈署流程；`tests/ops/test_runtime_config_code_closeout.py`；`docs/deployment/RELEASE_BASELINE.md` (L12)。 | **保留 (KEEP)** | `ODP-EPHEMERAL-STAGING-IAC-001` (Wave 1) |
| `infra/cloudbuild/README.md` | Cloud Build 說明文件（54 B，3 行；內容為 `# Cloud Build` 標題 + 空行 + `Cloud Build and CI/CD pipeline assets.`，**非空白 stub**）。目錄內僅此一檔。 | **無部署面 caller**：現行發布全部走 GitHub Actions (WIF)，倉庫無 `cloudbuild.yaml`（`git ls-files \| grep -i cloudbuild` 僅回傳此 README）、無 Cloud Build trigger 定義、無 Terraform 引用。<br>**但存在一個 active 測試相依**：`tests/test_scaffold.py:59` 將 `"infra/cloudbuild"` 列入 `expected_paths`，並於 L67-68 斷言 `missing == []`。刪除該目錄會使 `test_odp_sd04_top_level_scaffold_exists` 失敗（證明見 §7.3）。 | **歸檔 / 刪除 (RETIRE)**：含目錄一併移除<br>**連動編輯（必須）**：同時移除 `tests/test_scaffold.py:59`，否則 CI 轉紅。 | `ODP-DEPLOY-DEAD-CODE-REMOVAL-001` (Wave 2) |
| `infra/k8s_optional/README.md` | Optional Kubernetes 說明文件（102 B，3 行；內容為 `# Optional Kubernetes` 標題 + 空行 + `Optional Kubernetes manifests, used only if deployment topology requires them.`，**非空白 stub**）。目錄內無任何 YAML manifest。 | **無部署面 caller**：無 workflow、腳本或 Terraform 引用；現行 K8s 部署全部在 `infra/k8s/data-platform/`。<br>**但存在一個 active 測試相依**：`tests/test_scaffold.py:58` 將 `"infra/k8s_optional"` 列入 `expected_paths`（L67-68 斷言）。刪除該目錄會使同一測試失敗（證明見 §7.3）。 | **刪除 (DELETE)**：含目錄一併移除<br>**連動編輯（必須）**：同時移除 `tests/test_scaffold.py:58`，否則 CI 轉紅。 | `ODP-DEPLOY-DEAD-CODE-REMOVAL-001` (Wave 2) |
| `infra/k8s/data-platform/` (`render.py`, `workloads.yaml.tpl`, `deployment_runtime.py`, `status_mapping.prod.json`) | Data Platform GKE 部署範本與渲染腳本。含 active CronJob。 | **Caller**: Data Platform EMGI 部署；`tests/integration/test_data_platform_deployment_contract.py`。<br>**CronJob 逐行證據**（`workloads.yaml.tpl`，全檔 699 行，YAML 分隔符位於 L132/L304/L444/L572）：CronJob document 涵蓋 **L133-303**，`kind: CronJob` (L134)、`name: oday-data-platform-bounded-daily` (L136)、`namespace: oday-dev` (L137)、`schedule: "0 1 * * *"` (**L148**)、`timeZone: Etc/UTC` (L149)、`concurrencyPolicy: Forbid` (L150)、`startingDeadlineSeconds: 1800` (L151)、`backoffLimit: 1` (L156)、`activeDeadlineSeconds: 14400` (L157)、`ttlSecondsAfterFinished: 604800` (L158)。Image 以 `__DATA_IMAGE__` 佔位符 (L144) 由 `render.py` 替換為 release digest。<br>**其餘三個為 suspended Job（不排程）**：orders-history (L305-443, `suspend: true` L322)、trade-manual (L445-571, `suspend: true` L461)、device-log-manual (L573-699, `suspend: true` L589)。<br>**README 邊界**: `README.md` **L98-107**（§ Workload boundaries and evidence）劃定 daily CronJob 只載入 bounded merchant、place、device、daily operations、orders、AI revenue、commercial inputs 與 KMeans lineage；trade 與 device logs 為獨立 suspended Jobs，須人工審查窗口後 unsuspend，「They are never scheduled.」(L107)。 | **保留 (KEEP)** | `DPF-EMGI-LIVE-ROLLOUT-001` (Wave 1) |
| `infra/mlflow/` (`Dockerfile`, `entrypoint.py`, `runtime.py`, `healthcheck.py`) | MLflow Tracking Server 容器定義。 | **Caller**: MLflow 部署；`deploy-dev.yml` 以 `MLFLOW_TRACKING_URI` (L114) 注入。 | **保留 (KEEP)** | 維持現狀 |

---

### 4.6 排程、Worker 與資料管線 (`apps/scheduler/`, `apps/worker/`, `pipelines/`, `product_ops/modeling/`)

| 元件名稱與路徑 | 主要責任與對外介面 | 呼叫者與相依分析 (Usage Evidence) | 處置判定 | 承接 Wave 任務 |
|---|---|---|---|---|
| `apps/scheduler/oday_scheduler` | 本地開發定期 enqueue 排程工作迴圈。 | **Caller**: 根目錄 `docker-compose.yml` (L66: `command: ["python", "-m", "apps.scheduler.oday_scheduler"]`)。<br>**雲端對應**: Cloud Run Job 經 `cloud_run_job_entrypoint.py scheduler` 執行（見 §4.2.1）。<br>**Tests**: `tests/test_scaffold.py:74`（`scheduler_health()`）。 | **保留 (KEEP)** | 維持現狀 |
| `apps/worker/oday_worker` | 本地開發背景工作消費者。 | **Caller**: 根目錄 `docker-compose.yml` (L54: `command: ["python", "-m", "apps.worker.oday_worker"]`)。<br>**雲端對應**: Cloud Run Job 經 `cloud_run_job_entrypoint.py worker --max-jobs 1` 執行（`deploy_cloud_run_waji.sh:478-479`）。<br>**Tests**: `tests/test_scaffold.py:73`（`worker_health()`）。 | **保留 (KEEP)** | 維持現狀 |
| `pipelines/data_quality/gates.py` | 內部資料品質檢核門禁。 | **Caller**: Ingestion pipelines。 | **保留 (KEEP)** | 維持現狀 |
| `pipelines/quality/great_expectations_gate.py` | Great Expectations 資料品質閘道轉接器。 | **Caller**: `tests/data/test_great_expectations_gate.py`。 | **保留 (KEEP)** | 維持現狀 |
| `pipelines/dbt/` | Model-ready 資料視圖 dbt 專案。 | **Caller**: dbt transform pipelines。 | **保留 (KEEP)** | 維持現狀 |
| `pipelines/orchestration/dagster_training.py` | Dagster 模型訓練排程。 | **Caller**: Training pipelines。 | **保留 (KEEP)** | 維持現狀 |
| `pipelines/features/model_features.py` | 特徵計算管線。 | **Caller**: Feature pipeline。 | **保留 (KEEP)** | 維持現狀 |
| `pipelines/training/model_training.py` | 模型訓練管線。 | **Caller**: Training pipeline。 | **保留 (KEEP)** | 維持現狀 |
| `product_ops/modeling/` (全目錄) | 模型基準評測、發布與成果追蹤。 | **Caller**: `product_ops` 維運腳本與測試。 | **保留 (KEEP)** | 維持現狀 |
| `product_ops/data_platform/backfill.py` | 資料平台回填維運腳本。 | **Active caller**: `infra/k8s/data-platform/runtime/deployment_runtime.py:403-425` 組合 `python -m product_ops.data_platform.backfill`；GKE CronJob `workloads.yaml.tpl:215-218` 以 `args: ["scheduled"]` 進入該 runtime。`backfill.py:112-124` 建立 `DataPlaneRunner` 並逐 partition 執行。 | **保留 (KEEP)** | 維持唯一已接線的資料平台批次路徑 |
| `product_ops/external_data_backfill.py` | 外部資料回填維運腳本。 | **Caller**: 資料維運。 | **保留 (KEEP)** | 維持現狀 |
| `apps/data_platform/definitions.py` | Data Platform 宣告式資產圖譜、10 組資產作業、6 組每日分區排程 (01:00-05:00 UTC) 與 3 組 900 秒變更感應器 (sensors)。 | **Static-only evidence**: `apps/data_platform/pyproject.toml:12-13` 只暴露 `"dagster.repository"` entrypoint；README `:195-210` 與 tests `apps/data_platform/tests/test_definitions_and_backfill.py:7-49` 只是文件／測試 consumer。`infra/docker/data-platform.Dockerfile:58` 的 ENTRYPOINT 是 `deployment_runtime.py`，GKE `workloads.yaml.tpl:218` 傳入 `scheduled`，且該 runtime `:403-425` 只 caller `product_ops.data_platform.backfill`。全庫針對 Dagster CLI/daemon/runtime invocation 的負向掃描為 0。<br>**Declared surface**: 6 組 ScheduleDefinition、3 組 900 秒感應器與 `defs` 掛載（見 §4.6.1），其 asset `_run()`（L33-42）與 `backfill.py` 共享 `DataPlaneRunner.run_partition`，11 個 scheduled source kinds 重疊（見 §4.6.2）。 | **替換 (REPLACE)** — 目前未接線 (UNWIRED)，後續須正式接線或退役宣告面；本任務不刪 code | `DPF-EMGI-LIVE-ROLLOUT-001` (Wave 1) |

#### 4.6.1 Data Platform Dagster 排程與 Sensor 宣告面（逐行證據）

依據驗收條件第 1 項，此處列出 Data Platform Dagster 排程與變更感應器的**程式宣告**精確行號；這些行號證明 definitions 的內容，不等於證明它已被 runtime 啟動。當前 active runtime 的證據在 `infra/docker/data-platform.Dockerfile:58`、`infra/k8s/data-platform/workloads.yaml.tpl:215-218` 與 `infra/k8s/data-platform/runtime/deployment_runtime.py:403-425`，其鏈路落到 `product_ops.data_platform.backfill`。

Dagster wiring 的負向檢查（排除 docs、tests 與套件鎖檔，尋找 daemon／CLI／runtime invocation）可重跑如下：

```bash
runtime_hits="$({
  rg -n -i --glob '!docs/**' --glob '!tests/**' --glob '!*.lock' --glob '!*.pyc' \
    'dagster-(daemon|webserver)|dagster[[:space:]]+(daemon|api|dev|instance|schedule|sensor|job|asset)|python(3)?[[:space:]].*dagster' \
    .github infra apps product_ops pipelines scripts Makefile pyproject.toml 2>/dev/null || true
})"
if [ -n "$runtime_hits" ]; then
  printf '%s\n' "$runtime_hits"
  exit 1
fi
printf 'dagster_cli_daemon_runtime_hits=0\n'
# -> dagster_cli_daemon_runtime_hits=0
```

相對地，`rg -n -i 'dagster|definitions:defs' apps/data_platform` 只命中 package dependency／entrypoint、README、`definitions.py` 本身與 tests；這些是宣告、文件或測試，不是部署 runtime caller。

| 項目 | 名稱 / 識別符 | 類型與 Cron / 頻率 | 精確行號 | 職責與對應 Job / 資產 |
|---|---|---|---|---|
| **1. 維度每日排程** | `dimension_schedule` (`fongniao_dimensions_daily_schedule`) | `ScheduleDefinition`<br>`cron_schedule="0 1 * * *"`<br>`execution_timezone="UTC"` | `apps/data_platform/definitions.py` **L208-214** | 驅動 `dimension_job`（L172-175：`merchant` → `place` → `device` 維度資產）；以 `_previous_day_partition`（L202-205）計算前一日分區鍵。 |
| **2. 維運統計排程** | `operations_schedule` (`fongniao_operations_daily_schedule`) | `ScheduleDefinition`<br>`cron_schedule="0 2 * * *"`<br>`execution_timezone="UTC"` | `apps/data_platform/definitions.py` **L215-221** | 驅動 `operations_job`（L176-179：`device_daily_statistics_fact` 營運事實表）。 |
| **3. 權威交易排程** | `authoritative_transaction_schedule` (`fongniao_authoritative_transactions_daily_schedule`) | `ScheduleDefinition`<br>`cron_schedule="30 2 * * *"`<br>`execution_timezone="UTC"` | `apps/data_platform/definitions.py` **L222-228** | 驅動 `authoritative_transaction_job`（L180-185：`authoritative_orders` → `mapped_transactions` 標準化交易資產）。 |
| **4. 營收預測排程** | `forecast_schedule` (`fongniao_forecast_inputs_daily_schedule`) | `ScheduleDefinition`<br>`cron_schedule="0 3 * * *"`<br>`execution_timezone="UTC"` | `apps/data_platform/definitions.py` **L229-235** | 驅動 `forecast_job`（L186-189：`ai_revenue_forecast_inputs` 營收特徵輸入）。 |
| **5. 商業分析排程** | `commercial_schedule` (`fongniao_commercial_daily_schedule`) | `ScheduleDefinition`<br>`cron_schedule="0 4 * * *"`<br>`execution_timezone="UTC"` | `apps/data_platform/definitions.py` **L236-242** | 驅動 `commercial_job`（L190-191：`campaign_inputs`, `product_inputs`, `promotion_inputs` 商業分析特徵）。 |
| **6. 機器學習排程** | `learning_schedule` (`fongniao_learning_daily_schedule`) | `ScheduleDefinition`<br>`cron_schedule="0 5 * * *"`<br>`execution_timezone="UTC"` | `apps/data_platform/definitions.py` **L243-249** | 驅動 `learning_job`（L192-195：`consumer_segment_import_lineage` KMeans 分群學習譜系）。 |
| **7. 分區函式定義** | `_previous_day_partition()` | 輔助函式 | `apps/data_platform/definitions.py` **L202-205** | 解析 `scheduled_execution_time`，產生前一日 UTC ISO 日期（`YYYY-MM-DD`）作為分區鍵。 |
| **8. 維度變更感應器** | `dimension_change_sensor` | `@sensor`<br>`minimum_interval_seconds=900`<br>`default_status=RUNNING` | `apps/data_platform/definitions.py` **L280-291** | 每 900 秒輪詢 MongoDB 變更日誌，偵測 `(MERCHANT, PLACE, DEVICE)` 來源變更；有異動時觸發 `dimension_job` RunRequest。 |
| **9. 營運變更感應器** | `operations_change_sensor` | `@sensor`<br>`minimum_interval_seconds=900`<br>`default_status=RUNNING` | `apps/data_platform/definitions.py` **L293-304** | 每 900 秒偵測 `(DEVICE_DAILY_STATISTICS,)` 來源變更；有異動時觸發 `operations_job` RunRequest。 |
| **10. 交易變更感應器** | `authoritative_transaction_change_sensor` | `@sensor`<br>`minimum_interval_seconds=900`<br>`default_status=RUNNING` | `apps/data_platform/definitions.py` **L306-318** | 每 900 秒偵測 `(ORDERS,)` 來源變更；有異動時觸發 `authoritative_transaction_job` RunRequest。 |
| **11. Definitions 總成** | `defs = Definitions(...)` | Dagster 頂層註冊物件 | `apps/data_platform/definitions.py` **L321-364** | 集中掛載 15 組資產 (L322-338)、10 組 Job (L339-350)、6 組 Schedule (L351-358) 與 3 組 Sensor (L359-363)。 |
| **12. 套件進入點宣告** | `[project.entry-points]."dagster.repository"` | `pyproject.toml` entrypoint | `apps/data_platform/pyproject.toml` **L12-13** | 只宣告 `oday_data_platform = "apps.data_platform.definitions:defs"`；沒有 repository loader、Dagster CLI、UI 或 daemon 的 caller。 |
| **13. 排程與回填文件** | `## Scheduling and Backfill` | 說明文件 | `apps/data_platform/README.md` **L195-210** | 記載 Dagster 每日執行順序（01:00-05:00）、變更感應器運作原則，以及手動受限回填邊界；文件不是 runtime wiring 證據。 |
| **14. 自動化測試證明** | `test_dagster_repository_loads_all_typed_assets`<br>`test_daily_schedule_carries_previous_day_partition_key` | Pytest 測試 | `apps/data_platform/tests/test_definitions_and_backfill.py` **L7-13, L18-25, L39-49** | 只在 pytest 中載入 `defs` 並驗證 asset graph／partition key；測試 consumer 不代表 production caller。 |

#### 4.6.2 排程機制與共享 `DataPlaneRunner` 對帳

倉庫內有兩套已證明 active 的排程／觸發機制，另有一套尚未接線的 Dagster 宣告面。邊界、職責與判定對帳如下：

| 維度 | 1. 應用層 Cloud Scheduler | 2. 叢集層 GKE CronJob | 3. 資料平台層 Dagster Definitions（未接線） |
|---|---|---|---|
| **執行環境 (Runtime)** | GCP Cloud Scheduler (Serverless HTTP Trigger) | GKE Kubernetes Cluster (In-Cluster CronJob Controller) | 目前沒有已證明的 Dagster Orchestration Engine / Daemon / In-Process caller；僅有 Python declarations |
| **所屬系統層級** | ODay Plus 核心應用層 (Application Plane) | 資料平台容器層 (Container Runner Plane) | 資料平台宣告式資產層 (Data Asset Plane)，目前未進入發布 runtime |
| **排程與觸發頻率** | 由 GitHub environment `vars.ODP_WORKER_CRON` 與 `vars.ODP_SCHEDULER_CRON` 注入（見 §4.2.1） | `schedule: "0 1 * * *"` (UTC) 固定每日 01:00 執行（`workloads.yaml.tpl:148`） | 宣告 6 組 daily crons（`definitions.py:208-249`）與 3 組 900 秒感應器（`definitions.py:280-318`），但沒有 active tick caller |
| **觸發對象與 Payload** | Cloud Run Jobs (HTTP POST 呼叫 `.../jobs/<job>:run`) 執行 `cloud_run_job_entrypoint.py` | GKE Pod 以 `deployment_runtime.py:403-425` 執行 `python -m product_ops.data_platform.backfill` 進行 bounded backfill | 若未來接線，才會依資產相依圖譜調度 Dagster Jobs；目前沒有此 payload／caller |
| **受限與安全邊界** | Bounded job（`--max-jobs 1`），受 Cloud Run memory/timeout 上限約束 | 透過 `render.py` 替換 immutable digest，`deployment_runtime.py` 嚴格要求 migration receipt PASSED 門禁（見 §2） | 宣告的 source selection 對 `trade`、`device_log` 等大容量來源設有 manual／100,000 上限；這是 code policy，不是 active schedule 證據 |
| **狀態快照與回滾** | `cloud_run_release_traffic.sh` 支援 `capture_scheduler_trigger` 與 `restore_scheduler_trigger` 自動回滾 | Kubernetes declarative manifest，由 `kubectl apply` / namespace lifecycle 管理 | 沒有已接線的 Dagster deployment、tick receipt 或 rollback caller |
| **重疊／廢代碼判定** | **獨立且必要 (INDEPENDENT / KEEP)**：為 ODay Plus 應用服務唯一雲端排程機制。 | **目前 active 且必要 (ACTIVE / KEEP)**：為 GKE 叢集內每日批次容器啟動器。 | **未接線 (UNWIRED / REPLACE)**：宣告本身可被 import，但不是目前 runtime 的 scheduler；與 active backfill 共享 runner，不能直接判定為獨立保留。 |

**判定結論**：
1. **Active runtime 無 Dagster 重複排程**：Cloud Scheduler 與 GKE CronJob 的 caller 可由 workflow／manifest／runtime 逐層追到；Dagster 沒有 daemon、CLI 或 runtime invocation，因此目前不是第三套 active scheduler。
2. **共享 runner 重疊已確認**：GKE 的 `backfill.py:112-124` 與 `definitions.py:33-42` 都建立 `DataPlaneRunner` 並呼叫 `run_partition`。GKE `SCHEDULED_KINDS`（`deployment_runtime.py:41-53`）與 Dagster 6 組 daily schedules 對同 11 個 source kinds 重疊：`merchant`, `place`, `device`, `device_daily_statistics`, `orders`, `ai_revenue_stats`, `campaign`, `product`, `products`, `promotions`, `ai_consumer_kmeans_v1`。這是待治理的 orchestration overlap，不是兩個已證明同時運作的 scheduler。
3. **後續治理 (Wave 1 承接)**：Cloud Scheduler 納入 release lease 授權（`ODP-PROD-BLUEGREEN-PRIMITIVES-001`）；`DPF-EMGI-LIVE-ROLLOUT-001` 必須在接線前明確決定「以 Dagster 正式接管並納入 Runtime Release」或「退役 `definitions.py` 的 schedules／sensors，保留 `backfill.py` 唯一路徑」。本 Wave 不刪 code。


---

### 4.7 腳本目錄 (`scripts/`)

| 元件名稱與路徑 | 主要責任與對外介面 | 呼叫者與相依分析 (Usage Evidence) | 處置判定 | 承接 Wave 任務 |
|---|---|---|---|---|
| `scripts/ai-status.sh` & `scripts/ai_status.py` | 系統核心狀態管理與協調 CLI。 | **Caller**: Supervisor 與 Auto Worker 狀態更新唯一入口；`ai_status.py` 呼叫 `check_task_delivery_identity.py`。 | **保留 (KEEP)** | 維持現狀 |
| `scripts/restart-supervisor.sh`, `run-supervisor.sh`, `run-supervisor-watchdog.sh` | Supervisor 守護與啟動腳本。 | **Caller**: Supervisor 主機 process manager。 | **保留 (KEEP)** | 維持現狀 |
| `scripts/supervisor_runtime_health.py` & `supervisor_watchdog_install.py` | Supervisor 健康檢查與安裝工具。 | **Caller**: Supervisor 維運。 | **保留 (KEEP)** | 維持現狀 |
| `scripts/validate_external_data_boundary.py` | 外部資料邊界與全庫凍結清單校驗工具。 | **Caller**: `tests/architecture/test_external_data_boundary.py`。 | **保留 (KEEP)** | 維持現狀 |
| `scripts/orchestrator/check_task_dependency_resolvability.py` | Task 依賴圖完整性解析器。 | **Caller**: `Makefile:91-98` (`make task-dependency-check`)。 | **保留 (KEEP)** | 維持現狀 |
| `scripts/orchestrator/*.py` (其他維運腳本) | Archive 回填、新鮮度檢查、Worktree 設定轉移等維運工具。 | **Caller**: Supervisor 內部診斷與維運。 | **保留 (KEEP)** | 維持現狀 |

---
## 5. 總結清單：保留／替換／刪除清單 (Inventory Matrix)

依據驗收條件第 2 項，彙整全系統之保留、替換、刪除分類清單與統計。

### 5.1 統計摘要（雙軸，含對帳依據）

本報告的處置統計沿**兩條互不相同的軸**進行，兩軸各自封閉、可獨立對帳；混合計數是前版報告數字打架的根因，故此處明確分開呈現。

**軸 A — 元件生命週期處置（母體 = §4 逐項盤點的 91 列）**

```text
┌──────────────────────────────┬───────┬──────────────────────────────────────────────┐
│ 處置類別                     │ 列數  │ 來源章節（列數）                             │
├──────────────────────────────┼───────┼──────────────────────────────────────────────┤
│ 保留 (KEEP，含擴充/重用)     │ 78 列 │ 4.1:6 4.2:6 4.3:13 4.4:26 4.5:10 4.6:11 4.7:6│
│ 替換/重構 (REPLACE)          │  8 列 │ 4.1:1 4.2:1 4.3:5 4.6:1                      │
│ 刪除/退役 (DELETE / RETIRE)  │  5 列 │ 4.5:5                                        │
├──────────────────────────────┼───────┼──────────────────────────────────────────────┤
│ 合計                         │ 91 列 │ 4.1:7 4.2:7 4.3:18 4.4:26 4.5:15 4.6:12 4.7:6│
└──────────────────────────────┴───────┴──────────────────────────────────────────────┘
```

**軸 B — External Proof 殘留檔案處置（母體 = §3.2 的 20 個殘留檔案）**

```text
┌──────────────────────────────┬───────┬──────────────────────────────────────────────┐
│ 處置類別                     │ 檔數  │ 來源章節                                     │
├──────────────────────────────┼───────┼──────────────────────────────────────────────┤
│ 刪除 (DELETE)                │  5 檔 │ §3.2.1                                       │
│ 重構精簡 (REPLACE / UPDATE)  │ 10 檔 │ §3.2.2 (7) + §3.2.3 (3)                      │
│ 歸檔保留 (ARCHIVE / KEEP)    │  4 檔 │ §3.2.4 (2) + §3.2.6 (2)                      │
│ 保留 (KEEP，active 程式碼)   │  1 檔 │ §3.2.5                                       │
├──────────────────────────────┼───────┼──────────────────────────────────────────────┤
│ 合計                         │ 20 檔 │ §3.2.7 對帳表                                │
└──────────────────────────────┴───────┴──────────────────────────────────────────────┘
```

**兩軸的唯一重疊項**：`delivery_toolchain/e2e/check_product_release_gate.py` 同時出現在軸 A（§4.3，REPLACE，因 Gate Registry 升級）與軸 B（§3.2.5，KEEP，因其 external-proof 引用僅為 docstring 脈絡註解）。兩者判斷的是不同問題，非矛盾；跨軸說明見 §3.2.5。除此之外兩軸無交集。

**合併後的行動清單推導**：

| 行動清單 | 項數 | 推導 |
|---|---|---|
| §5.2 刪除清單 | **10** | 軸 A DELETE / RETIRE 5 項（`Dockerfile.api`、`Dockerfile.web`、`infra/docker/docker-compose.yml`、`infra/cloudbuild/README.md`、`infra/k8s_optional/README.md`）＋ 軸 B DELETE 5 檔（§3.2.1） |
| §5.3 替換／重構清單 | **9** | 軸 A REPLACE 8 項（含目前未接線的 `apps/data_platform/definitions.py`）＋ `cloud_run_release_traffic.sh` 1 項（§4.2 判定為 KEEP & EXPAND，但需在 Wave 1 實質擴充，故納入行動清單） |
| §5.4 保留清單 | — | 軸 A KEEP 78 項之代表性彙整（非逐項重列） |
| 文件重構清單 | **10** | 軸 B REPLACE / UPDATE 10 檔（§3.2.2 + §3.2.3），由 Wave 2 文件整併處理 |

---

### 5.2 刪除清單 (DELETE / RETIRE) — 待 Wave 2 `ODP-DEPLOY-DEAD-CODE-REMOVAL-001` 執行

> **注意**：依本任務規範，本任務只記錄清單，不直接刪除任何代碼。
> **連動編輯欄位**：凡刪除後會使既有測試或文件失效者，此欄列出必須同批修改的檔案與行號。Wave 2 若只刪檔不做連動編輯，CI 會轉紅。

| 序號 | 檔案路徑 | 類型 | 大小 | 刪除理由與 Caller 證明 | 連動編輯（必須） |
|---|---|---|---|---|---|
| 1 | `infra/docker/Dockerfile.api` | Dockerfile | 572 B | 過期舊版 Dockerfile（暴露 8080 port，寫死 pip install 依賴）。已由 `infra/docker/api.Dockerfile` 取代（`deploy_cloud_run_waji.sh:239`）。全庫引用僅 2 處：`infra/docker/docker-compose.yml:21`（同列刪除）與 `docs/deployment/RELEASE_BASELINE.md:10`（文件）。無 workflow / 腳本 / 測試引用。 | `docs/deployment/RELEASE_BASELINE.md:10` |
| 2 | `infra/docker/Dockerfile.web` | Dockerfile | 1,548 B | 過期舊版 Dockerfile（暴露 8080 port）。已由 `infra/docker/web.Dockerfile` 取代（`deploy_cloud_run_waji.sh:519`）。**全庫零引用**（`git grep -n 'Dockerfile\.web'` 除本報告外無輸出）。 | 無 |
| 3 | `infra/docker/docker-compose.yml` | Compose | 737 B | 過期 Compose 檔，引用舊版 `Dockerfile.api`（L21）。已由根目錄標準 `docker-compose.yml` 取代。引用者為 `infra/docker/README.md:8` 與 `docs/deployment/RELEASE_BASELINE.md:11`。 | `infra/docker/README.md:5-19`、`docs/deployment/RELEASE_BASELINE.md:11` |
| 4 | `docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json` | 孤立 JSON | 20,420 B | 舊版 PR #82 外部證明佇列，所屬 CLI 已於 `1a8b0f44` 全數刪除。無任何 active code 讀取或寫入；被 §3.2.2–3.2.3 之 10 份陳舊文件引用，但那些文件本身亦列為重構。 | 無（引用方一併於文件重構清單處理） |
| 5 | `docs/evidence/EXTERNAL_PROOF_HANDBACK_TEMPLATE.json` | 孤立 JSON | 7,514 B | 舊版 PR #82 handback JSON Schema 範本。唯一消費者 `check_external_proof_handback_template.py` 已於 `1a8b0f44` 刪除。**注意其掃描邊界特性見 §3.2.0 第 1 點（S1 命中 0，僅檔名與 S2 命中）。** | 無 |
| 6 | `docs/evidence/EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json` | 孤立 JSON | 6,145 B | 舊版 PR #82 外部證明看板。對應之 `check_/update_external_proof_handback_status_board.py` 已刪除，無 active code caller。 | 無 |
| 7 | `docs/evidence/EXTERNAL_PROOF_HANDBACK_EXAMPLE.json` | 孤立 JSON | 4,367 B | 舊版 PR #82 handback 示範格式。L43 引用已刪除流程之 PR 查詢指令。無 active code caller。 | 無 |
| 8 | `docs/evidence/EXTERNAL_PROOF_FLEET_PICKUP_BOARD.md` | 孤立 MD | 18,151 B | 舊版 PR #82 外部證明領取說明文件（S1 命中 51 行）。密集引用已刪除之 `check_/sync_external_proof_*.py` 與 `external-proof-followup.yml`。無 active code caller。 | 無 |
| 9 | `infra/cloudbuild/README.md`（含目錄） | 說明文件 | 54 B（3 行） | 非空白檔案，但系統完全使用 GitHub Actions (WIF)：倉庫無 `cloudbuild.yaml`、無 Cloud Build trigger、無 Terraform 引用。目錄內僅此一檔。 | **`tests/test_scaffold.py:59`（必須同批移除該 `expected_paths` 條目，證明見 §7.3）** |
| 10 | `infra/k8s_optional/README.md`（含目錄） | 說明文件 | 102 B（3 行） | 非空白檔案，但目錄內無任何 YAML manifest，無 workflow / 腳本 / Terraform 引用。現行 K8s 部署全部在 `infra/k8s/data-platform/`。 | **`tests/test_scaffold.py:58`（必須同批移除該 `expected_paths` 條目，證明見 §7.3）** |

**刪除清單組成對帳**：3 項 Docker 過期構件（序號 1–3）＋ 5 檔 external-proof 孤立構件（序號 4–8，即 §3.2.1 全部）＋ 2 項空殼 IaC 目錄（序號 9–10）= **10 項**，與 §5.1 推導一致。

---

### 5.3 替換 / 重構清單 (REPLACE / REFACTOR)

| 序號 | 檔案路徑 | 目前狀態與問題 | 預計重構目標 | 承接任務 |
|---|---|---|---|---|
| 1 | `.github/workflows/deploy-dev.yml` | 現場 build 映像檔；缺少 prod blue-green 路徑；門禁存在循環依賴。 | 整合為單一 `Runtime Release` 發布狀態機（Build Once -> Dev -> Staging -> Prod）。 | `ODP-RUNTIME-RELEASE-SINGLE-PATH-001` (Wave 2) |
| 2 | `delivery_toolchain/release/check_runtime_admission.py` | Shape-only regex 檢查，無簽名防偽與 CAS 防重放。 | 替換為以 KMS / 私鑰簽名之權威 Release Lease 驗證器。 | `ODP-RELEASE-ADMISSION-AUTHORITY-001` (Wave 0) |
| 3 | `delivery_toolchain/e2e/check_release_gate_registry.py` | 靜態 7 道 gate（Gate 0–6），無 stage 與環境概念。 | 重構為支援分階段 (candidate-built -> dev-verified -> staging-verified -> prod-admitted) 之 Gate Registry。 | `ODP-RELEASE-MANIFEST-GATES-001` (Wave 0) |
| 4 | `delivery_toolchain/e2e/check_product_release_gate.py` | 與舊 gate registry 強耦合。 | 配合新 manifest/gate registry 調整參數與驗證邏輯。（其 external-proof docstring 註解不需變更，見 §3.2.5） | `ODP-RELEASE-MANIFEST-GATES-001` (Wave 0) |
| 5 | `delivery_toolchain/e2e/check_remote_staging_proof.py` | 針對常駐 staging URL 驗證。 | 重構以適配短生命週期 Ephemeral Staging 之動態 URL 與 TTL 標籤。 | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` (Wave 3) |
| 6 | `delivery_toolchain/e2e/verify_deployment_health_backup_rollback.py` | 使用本地 docker-compose 模擬備份還原。 | 替換為 Staging 環境真實 Cloud SQL PostgreSQL 備份還原與回滾演練收據。 | `ODP-EPHEMERAL-STAGING-IAC-001` (Wave 1) |
| 7 | `product_ops/deployment/deploy_cloud_run_waji.sh` | 現場 build/push 映像檔；流量推進僅支援單一環境 100%；`upsert_scheduler_trigger` (L443-470) 無 lease 授權。 | 抽離 build（改為接收 release manifest digest）；擴充支援 prod blue-green (0% green 驗證後 100% 切換)；Scheduler upsert 綁定 lease。 | `ODP-PROD-BLUEGREEN-PRIMITIVES-001` (Wave 1) |
| 8 | `product_ops/deployment/cloud_run_release_traffic.sh` | 支援 dev/staging 流量切換與 scheduler trigger 快照/還原；函式可被裸 source 呼叫。 | 擴充支援 prod blue-green 與 GKE/Cloud Run 混合切換及回滾；函式層納入 lease 檢查。 | `ODP-PROD-BLUEGREEN-PRIMITIVES-001` (Wave 1) |
| 9 | `apps/data_platform/definitions.py` | 僅有 Dagster definitions、package entrypoint、README 與 pytest consumer；全庫無 Dagster daemon/CLI/runtime caller。其 asset `_run()` 與 active `product_ops.data_platform.backfill` 共用 `DataPlaneRunner`，對 11 個 scheduled source kinds 重疊。 | 在決定的單一路徑中處理：若 Dagster 要成為正式 orchestrator，補上 Runtime Release 接線、lease、tick／run receipt 與 production-like runtime；否則退役 schedules／sensors／entrypoint，保留 `backfill.py` 的 active 路徑。 | `DPF-EMGI-LIVE-ROLLOUT-001` (Wave 1) |

---

### 5.4 保留清單 (KEEP) — 軸 A 78 項之代表性彙整

- **Workflows (6)**: `ci.yml`, `promote-dev-to-main.yml`, `merge-queue-review-gate.yml`, `tooling-scope-review-gate.yml`, `emgi-consumer-boundary.yml`, `assisted-intake-design-validation.yml`。
- **Core Deployment, Manifest, Blue-Green & Release Receipts (9)**: `release_manifest.py`, `migrate_gate_registry.py`, `release_receipts.py`, `bluegreen_release.py`, `cloud_run_job_entrypoint.py`, `cloud_run_traffic.py`, `cloud_scheduler_trigger.py`, `validate_cloud_run_live_deployment.py`, `check_live_e2e_gate.py`／`check_live_production_data.py`。
- **CI E2E & Contract Tooling**: `run_product_e2e.sh`, `run_python_e2e_tests.py`, `record_playwright_results.py`, `generate_product_e2e_receipt.py`／`product_e2e_receipt.py`, `seed_product_e2e_data.py`, `worker_heartbeat.py`, `check_product_grade_ci_gates.py`, `_release_target.py`／`_support.py`, `check_drift.py`, `export_openapi.py`, `generate_client.py`, `build_validate_assisted_listing_intake.py`。
- **Security & Governance**: `secret_scan.py`, `sast_scan.py`, `generate_sbom.py`, `generate_oss_notice.py`, `attestation.py`, `sign_images.sh`, `check_code_boundaries.py`, `check_config_wiring.py`, `check_orchestrator_config.py`, `classify_change_review_scope.py`, `validate_assisted_listing_intake_design.py`, `validate_emgi_consumer_boundary.mjs`, `task_start.sh`, `task_finalize.sh`, `worker_commit.py`, `check_commit_scope.py`, `check_commit_trailers.py`, `check_task_delivery_identity.py`, `apply_branch_protection.py`, `check_pr_merge_eligibility.py`。
- **Standard Docker & IaC**: `api.Dockerfile`, `web.Dockerfile`, `worker.Dockerfile`, `scheduler.Dockerfile`, `data-platform.Dockerfile`, `infra/docker/docker-compose.e2e.yml`, 根目錄 `docker-compose.yml`, `infra/terraform/**`, `infra/k8s/data-platform/**`（含 daily CronJob 與 3 個 suspended Job）, `infra/mlflow/**`。
- **Scheduler / Worker / Pipelines & Modeling**: `apps/scheduler/oday_scheduler`, `apps/worker/oday_worker`, `pipelines/data_quality/gates.py`, `pipelines/quality/great_expectations_gate.py`, `pipelines/dbt/**`, `pipelines/orchestration/**`, `pipelines/features/**`, `pipelines/training/**`, `product_ops/modeling/**`, `product_ops/data_platform/backfill.py`, `product_ops/external_data_backfill.py`。`apps/data_platform/definitions.py` 不列入 KEEP；其未接線 Dagster 宣告面列於 §5.3 REPLACE，待 `DPF-EMGI-LIVE-ROLLOUT-001` 決定接線或退役。
- **Scripts (6)**: `ai-status.sh`／`ai_status.py`, supervisor 啟動與守護腳本群, `supervisor_runtime_health.py`／`supervisor_watchdog_install.py`, `validate_external_data_boundary.py`, `check_task_dependency_resolvability.py`, `scripts/orchestrator/*.py`。
- **軸 B 歸檔保留 (4 檔)**: `runtime/ODP-P10-LIVE-LEGACY-RETIREMENT-001/static-verification.json`, `fleet_dispatch/package10_20260726/ODP-P10-DEV-LANDING-FIX-001.md`, `python-inventory-2026-08-13.csv`, `python-runtime-tooling-audit-2026-08-13.md`。

---

## 6. 後續 Wave 任務執行建議與指引

本盤點結果直接提供給後續派工 DAG 參考：

1. **Wave 0 (`ODP-RELEASE-MANIFEST-GATES-001`, `ODP-RELEASE-ADMISSION-AUTHORITY-001`)**:
   - 凍結 Release Manifest Schema（定義 API、Web、Worker、Scheduler、Data Platform exact digests 與 Policy/Contract digests）。
   - 實作具簽名與 CAS 防重放之權威 Lease 驗證器，廢棄 Shape-only 檢查，解開 Gate 0–6 循環依賴。
2. **Wave 1 (`ODP-EPHEMERAL-STAGING-IAC-001`, `ODP-PROD-BLUEGREEN-PRIMITIVES-001`, `ODP-RELEASE-EVIDENCE-RECEIPTS-001`)**:
   - 建立 Ephemeral Staging 建立/銷毀/TTL 機制，以真實 PostgreSQL 演練取代 `verify_deployment_health_backup_rollback.py` 的本地 compose 模擬。
   - 擴充 `cloud_run_release_traffic.sh` 與 `cloud_run_traffic.py`，支援 Production 0% green 流量驗收與原子 100% 切換/回滾；同步將 `upsert_scheduler_trigger` (`deploy_cloud_run_waji.sh:443-470`) 納入 lease 授權。
   - 統一收據格式（Redacted receipts）與 Artifact allowlist。
3. **Wave 2 (`ODP-RUNTIME-RELEASE-SINGLE-PATH-001`, `ODP-DEPLOY-DEAD-CODE-REMOVAL-001`)**:
   - 將 `deploy-dev.yml` 改造為唯一發布管線，落實 Build Once 流程。
   - 執行 §5.2 之 10 項刪除，**並務必同批完成「連動編輯」欄位所列修改**——特別是 `tests/test_scaffold.py:58-59`，否則 `test_odp_sd04_top_level_scaffold_exists` 會失敗（見 §7.3）。
   - 整併 §3.2.2 與 §3.2.3 共 10 份含幽靈引用之文件。
   - 刪除後執行負向搜尋驗證：`git grep -Eil 'external[-_. ]proof'` 應僅剩歸檔保留之 4 檔與現行治理規劃書。

---
## 7. 證據可重跑驗證 (Reproducible Verification)

本節提供可直接複製執行的驗證指令。**所有指令均以 §0 標頭之基準 commit 於本 task 分支實際執行過，輸出如下所述。** 目的是讓審查者不必逐檔開啟即可確認行號引用正確。

### 7.1 掃描邊界與檔數對帳

§3.2.0 的三個掃描邊界一律排除本報告自身，下列腳本把「排除」與「數字」一起寫成**斷言**，而不是讓讀者自行加減。存為 `/tmp/scan_boundary.sh` 後執行：

```bash
bash /tmp/scan_boundary.sh; echo "EXIT=$?"
```

```bash
#!/usr/bin/env bash
# §7.1 掃描邊界與檔數對帳（fail-closed）：任一項不符即以非 0 離開。
set -uo pipefail

SELF='docs/audits/ODP_DEPLOYMENT_DEAD_CODE_AUDIT.md'
S1RE='external[-_. ]proof'
S2RE='external[^[:alpha:]]{0,3}([[:alpha:]]+[^[:alpha:]]{1,3}){0,2}proof'
fails=0

num() {  # num <label> <want> <got>
  if [ "$3" -eq "$2" ]; then printf 'OK   %s got=%s want=%s\n' "$1" "$3" "$2"
  else printf 'FAIL %s got=%s want=%s\n' "$1" "$3" "$2"; fails=$((fails + 1)); fi
}
str() {  # str <label> <want> <got>
  if [ "$3" = "$2" ]; then printf 'OK   %s got=%s\n' "$1" "$3"
  else printf 'FAIL %s got=%s want=%s\n' "$1" "$3" "$2"; fails=$((fails + 1)); fi
}

s1_raw=$(git grep -Eil "$S1RE" | wc -l)
s2_raw=$(git grep -Eil "$S2RE" | wc -l)
s1=$(git grep -Eil "$S1RE" | grep -vxF "$SELF" | wc -l)
s2=$(git grep -Eil "$S2RE" | grep -vxF "$SELF" | wc -l)
s3=$(git ls-files | grep -Ei 'external[-_.]?proof' | grep -vxF "$SELF" | wc -l)
s1_not_s2=$(comm -23 <(git grep -Eil "$S1RE" | sort) <(git grep -Eil "$S2RE" | sort) | wc -l)
s2_not_s1=$(comm -13 <(git grep -Eil "$S1RE" | grep -vxF "$SELF" | sort) \
                     <(git grep -Eil "$S2RE" | grep -vxF "$SELF" | sort) | tr '\n' ' ' | sed 's/ *$//')

num 'S1-raw-with-report      ' 21 "$s1_raw"
num 'S2-raw-with-report      ' 22 "$s2_raw"
num 'S1-excluding-report     ' 20 "$s1"
num 'S2-excluding-report     ' 21 "$s2"
num 'S3-filename-scan        '  5 "$s3"
num 'S1-minus-S2-must-be-zero'  0 "$s1_not_s2"
num 'residual=S2-minus-plan  ' 20 "$((s2 - 1))"
str 'S2-minus-S1-boundary-case' 'docs/evidence/EXTERNAL_PROOF_HANDBACK_TEMPLATE.json' "$s2_not_s1"

printf '\nfails=%s\n' "$fails"
[ "$fails" -eq 0 ] || exit 1
exit 0
```

實測輸出（基準 commit）：

```text
OK   S1-raw-with-report       got=21 want=21
OK   S2-raw-with-report       got=22 want=22
OK   S1-excluding-report      got=20 want=20
OK   S2-excluding-report      got=21 want=21
OK   S3-filename-scan         got=5 want=5
OK   S1-minus-S2-must-be-zero got=0 want=0
OK   residual=S2-minus-plan   got=20 want=20
OK   S2-minus-S1-boundary-case got=docs/evidence/EXTERNAL_PROOF_HANDBACK_TEMPLATE.json

fails=0
EXIT=0
```

兩組數字的關係在此一次講清楚：**未排除本報告時 S1 = 21、S2 = 22**；**排除本報告後 S1 = 20、S2 = 21**，後者才是 §3.2.0、§3.2.7、§1.1 全文採用的口徑。`S1-minus-S2-must-be-zero` 同時證明 S1 ⊂ S2，故「三邊界聯集 = S2」的說法成立。

逐檔 S1 命中行號重現（§3.2.1–§3.2.6 各表「S1 命中行號」欄之來源）：

```bash
SELF='docs/audits/ODP_DEPLOYMENT_DEAD_CODE_AUDIT.md'
for f in $(git grep -Eil 'external[-_. ]proof' | grep -vxF "$SELF"); do
  printf '%-80s %3s  %s\n' "$f" \
    "$(grep -Eic 'external[-_. ]proof' "$f")" \
    "$(grep -Ein 'external[-_. ]proof' "$f" | cut -d: -f1 | tr '\n' ',' | sed 's/,$//')"
done
```

### 7.2 行號引用逐項驗證

下列腳本對「檔案 / 行號 / 期望字串」三元組做 **121 項** fail-closed 斷言，一次驗證本報告所有關鍵行號引用。`check()` 在 FAIL 時累計 `fails` 而非直接回傳成功，腳本結尾以 `exit 1` 收斂，因此**任何一項不符都會讓整支腳本以非 0 離開**；同時腳本會回頭數自己原始碼裡的 `check` 呼叫數，斷言「宣告 121 項 = 實際執行 121 項」，避免宣稱數與實際數再度脫節。存為 `/tmp/check_lines.sh` 後執行：

```bash
bash /tmp/check_lines.sh; echo "EXIT=$?"
```

```bash
#!/usr/bin/env bash
# §7.2 行號引用逐項驗證（fail-closed）：任一斷言失敗、或執行數與宣告數不符，即以非 0 離開。
set -uo pipefail

checks=0
fails=0

check() {  # check <file> <line> <expected-substring>
  checks=$((checks + 1))
  local got; got="$(sed -n "${2}p" "$1")"
  if printf '%s' "$got" | grep -qF "$3"; then
    printf 'OK   %s:%s\n' "$1" "$2"
  else
    printf 'FAIL %s:%s want=%s got=%s\n' "$1" "$2" "$3" "$got"
    fails=$((fails + 1))
  fi
}

W=.github/workflows/deploy-dev.yml
D=product_ops/deployment/deploy_cloud_run_waji.sh
T=product_ops/deployment/cloud_run_release_traffic.sh
R=delivery_toolchain/e2e/run_product_e2e.sh
C=.github/workflows/ci.yml
DP_DEF=apps/data_platform/definitions.py
DP_TOML=apps/data_platform/pyproject.toml
DP_README=apps/data_platform/README.md
DP_TEST=apps/data_platform/tests/test_definitions_and_backfill.py

# --- Cloud Scheduler 呼叫鏈（§4.2.1） ---
check $D 443 'upsert_scheduler_trigger() {'
check $D 470 '}'
check $D 579 'upsert_scheduler_trigger'
check $D 583 'upsert_scheduler_trigger'
check $D 580 'SCHEDULER_SCHEDULE_NAME'
check $D 582 'ODP_SCHEDULER_CRON'
check $D 584 'WORKER_SCHEDULE_NAME'
check $D 586 'ODP_WORKER_CRON'
check $D 325 'capture_scheduler_trigger'
check $D 160 'restore_scheduler_trigger'
check $T  77 'capture_scheduler_trigger() {'
check $T  97 'restore_scheduler_trigger() {'
check $W 110 'ODP_WORKER_CRON'
check $W 112 'ODP_SCHEDULER_TIME_ZONE'

# --- Data Platform Dagster 宣告面行號（§4.6.1；非 active runtime 證據） ---
check $DP_DEF 208 'dimension_schedule = ScheduleDefinition('
check $DP_DEF 211 'cron_schedule="0 1 * * *"'
check $DP_DEF 215 'operations_schedule = ScheduleDefinition('
check $DP_DEF 218 'cron_schedule="0 2 * * *"'
check $DP_DEF 222 'authoritative_transaction_schedule = ScheduleDefinition('
check $DP_DEF 225 'cron_schedule="30 2 * * *"'
check $DP_DEF 229 'forecast_schedule = ScheduleDefinition('
check $DP_DEF 232 'cron_schedule="0 3 * * *"'
check $DP_DEF 236 'commercial_schedule = ScheduleDefinition('
check $DP_DEF 239 'cron_schedule="0 4 * * *"'
check $DP_DEF 243 'learning_schedule = ScheduleDefinition('
check $DP_DEF 246 'cron_schedule="0 5 * * *"'
check $DP_DEF 280 '@sensor('
check $DP_DEF 282 'minimum_interval_seconds=900'
check $DP_DEF 293 '@sensor('
check $DP_DEF 306 '@sensor('
check $DP_DEF 321 'defs = Definitions('
check $DP_TOML 13 '"dagster.repository"'
check $DP_README 195 '## Scheduling and Backfill'
check $DP_README 197 'Dagster definitions are in `apps.data_platform.definitions`'
check $DP_README 200 '01:00 merchant -> place -> device'
check $DP_README 208 'Change sensors exist for dimensions and operational facts'
check $DP_TEST 7 'from apps.data_platform.definitions import ('
check $DP_TEST 18 'def test_dagster_repository_loads_all_typed_assets() -> None:'
check $DP_TEST 39 'def test_daily_schedule_carries_previous_day_partition_key() -> None:'

# --- Release Manifest, Migration & Release Receipts（§4.3） ---
check delivery_toolchain/release/release_manifest.py 25 'RELEASE_ID_PATTERN = re.compile'
check delivery_toolchain/release/release_manifest.py 87 'def validate_manifest('
check delivery_toolchain/release/migrate_gate_registry.py 30 'def migrate_registry('
check delivery_toolchain/release/release_receipts.py 43 'STAGE_ENVIRONMENT = {'
check delivery_toolchain/release/release_receipts.py 59 'RUNTIME_RELEASE_ARTIFACT_ALLOWLIST = ('
check delivery_toolchain/release/release_receipts.py 214 'def redact_secrets('
check delivery_toolchain/release/release_receipts.py 386 'def validate_receipt('
check delivery_toolchain/release/release_receipts.py 474 'def build_receipt('
check tests/ops/test_release_receipts.py 12 'from delivery_toolchain.release.release_receipts import ('
check tests/ops/test_release_receipts.py 50 'def test_receipt_binds_manifest_identity_and_redacts_nested_values() -> None:'
check tests/ops/test_release_receipts.py 118 'def test_allowlist_matches_workflow_uploads_and_script_producers() -> None:'

# --- Blue-Green Release Primitives & Tests（§4.2） ---
check product_ops/deployment/bluegreen_release.py  50 'class ServiceTarget:'
check product_ops/deployment/bluegreen_release.py 365 'def atomic_traffic_switch('
check product_ops/deployment/bluegreen_release.py 841 'def execute_bluegreen_switch('
check product_ops/deployment/bluegreen_release.py 899 'def execute_rollback('
check tests/ops/test_bluegreen_release.py  11 'from product_ops.deployment.bluegreen_release import ('
check tests/ops/test_bluegreen_release.py  37 'def test_service_target_and_scheduler_target_args() -> None:'
check tests/ops/test_bluegreen_release.py 257 'def test_atomic_traffic_switch(mock_gcloud: MagicMock) -> None:'
check tests/ops/test_bluegreen_release.py 632 'def test_execute_rollback('

# --- Workflow / 腳本 caller ---
check $W  61 'check_runtime_admission.py'
check $W  87 'verify_deployment_health_backup_rollback.py'
check $W 176 'secret_scan.py';  check $W 179 'sast_scan.py';  check $W 182 'generate_sbom.py'
check $W 196 'validate_cloud_run_live_deployment'
check $W 217 'deploy_cloud_run_waji.sh'
check $W 226 'check_remote_staging_proof.py'
check $D  76 'cloud_run_release_traffic.sh'
check $D  79 'validate_cloud_run_live_deployment.py'
check $D 267 'validate_cloud_run_live_deployment.py'   # 指令自 L266 續行
check $D 294 'validate_cloud_run_live_deployment.py'
check $D 361 'validate_cloud_run_live_deployment.py'
check $D 571 'validate_cloud_run_live_deployment.py'
check $D 624 'check_live_e2e_gate.py'
check $D 232 'sign_images.sh';  check $D 524 'sign_images.sh'
check $D 239 'api.Dockerfile';  check $D 240 'worker.Dockerfile'
check $D 241 'scheduler.Dockerfile'; check $D 519 'web.Dockerfile'
check delivery_toolchain/e2e/check_live_e2e_gate.py 53 'check_live_production_data'
check delivery_toolchain/e2e/check_product_release_gate.py 56 'product_e2e_receipt'
check .github/workflows/promote-dev-to-main.yml 50 'product-release-gate'
check $C  61 'classify_change_review_scope.py'
check $C  91 'check_code_boundaries'
check $C  98 'check_orchestrator_config.py'
check $C  99 'check_config_wiring.py'
check $C 250 'delivery_toolchain/load'
check .github/workflows/tooling-scope-review-gate.yml 30 'classify_change_review_scope.py'
check .github/workflows/assisted-intake-design-validation.yml  76 'validate_assisted_listing_intake_design.py'
check .github/workflows/assisted-intake-design-validation.yml 143 'build_validate_assisted_listing_intake'
check .github/workflows/emgi-consumer-boundary.yml 23 'validate_emgi_consumer_boundary.mjs'
check $R  24 'docker-compose.e2e.yml'
check $R  77 'seed_product_e2e_data.py'
check $R 123 'record_playwright_results.py'
check $R 138 'run_python_e2e_tests.py'
check $R 141 'generate_product_e2e_receipt.py'

# --- 間接（make）呼叫：workflow 行與 Makefile 行都要對 ---
check $C 189 'make api-contract'         # -> Makefile:63-64 -> check_drift.py
check $C 313 'make product-e2e-gate'     # -> Makefile:100-102
check Makefile  64 'check_drift.py'
check Makefile 101 'check_product_release_gate.py'
check Makefile 102 'run_product_e2e.sh'
check Makefile  83 'check_release_gate_registry.py'
check Makefile 105 'check_product_release_gate.py'
check Makefile  35 'check_code_boundaries.py'

# --- Docker / K8s / Compose ---
check docker-compose.yml 27 'api.Dockerfile'
check docker-compose.yml 54 'apps.worker.oday_worker'
check docker-compose.yml 66 'apps.scheduler.oday_scheduler'
check docker-compose.yml 80 'web.Dockerfile'
check infra/k8s/data-platform/workloads.yaml.tpl 134 'kind: CronJob'
check infra/k8s/data-platform/workloads.yaml.tpl 136 'oday-data-platform-bounded-daily'
check infra/k8s/data-platform/workloads.yaml.tpl 148 'schedule: "0 1 * * *"'
check infra/k8s/data-platform/workloads.yaml.tpl 150 'concurrencyPolicy: Forbid'
check infra/k8s/data-platform/workloads.yaml.tpl 157 'activeDeadlineSeconds: 14400'
check infra/k8s/data-platform/README.md 98 'Workload boundaries and evidence'
check infra/k8s/data-platform/README.md 107 'never scheduled'
check infra/docker/docker-compose.yml 21 'Dockerfile.api'
check infra/docker/README.md 8 'infra/docker/docker-compose.yml'
check docs/deployment/RELEASE_BASELINE.md 10 'Dockerfile.api'
check docs/deployment/RELEASE_BASELINE.md 11 'infra/docker/docker-compose.yml'
check tests/test_scaffold.py 58 'infra/k8s_optional'
check tests/test_scaffold.py 59 'infra/cloudbuild'

# --- fail-closed 收尾：斷言「全部通過」且「一項都沒漏跑」 ---
declared=$(grep -oE '(^|;[[:space:]]*)check[[:space:]]' "${BASH_SOURCE[0]}" | wc -l)
printf '\nchecks_declared=%s checks_executed=%s fails=%s\n' "$declared" "$checks" "$fails"
[ "$declared" -eq 121 ] || { printf 'FAIL declared-count want=121 got=%s\n' "$declared"; fails=$((fails + 1)); }
[ "$checks" -eq "$declared" ] || { printf 'FAIL executed=%s != declared=%s\n' "$checks" "$declared"; fails=$((fails + 1)); }
[ "$fails" -eq 0 ] || exit 1
exit 0
```

實測輸出（基準 commit）：121 行全部 `OK`，收尾為

```text
checks_declared=121 checks_executed=121 fails=0
EXIT=0
```

宣稱數可獨立複核（不必執行腳本）：

```bash
grep -oE '(^|;[[:space:]]*)check[[:space:]]' /tmp/check_lines.sh | wc -l   # -> 121
```

負向驗證（證明它真的 fail-closed，而非永遠回傳成功）：把任一行號改成不存在的行，腳本會印出 `FAIL` 並以 `EXIT=1` 離開：

```bash
# 只改掉最後一項斷言的行號與期望字串（此指令本身不含 check 呼叫，不影響上面的計數）
sed 's|test_scaffold.py 59 .*|test_scaffold.py 999 "no-such-string"|' \
  /tmp/check_lines.sh > /tmp/check_lines_neg.sh
bash /tmp/check_lines_neg.sh >/dev/null; echo "EXIT=$?"
# -> FAIL tests/test_scaffold.py:999 want=no-such-string got=
#    checks_declared=121 checks_executed=121 fails=1
#    EXIT=1
```

（前版報告在此驗證下會於 `upsert_scheduler_trigger` 呼叫點與函式結束行、以及 `ci.yml:189` / `ci.yml:313` 的直接呼叫寫法上產生 FAIL；本版已更正為 L579-586、L443-470，並改以 `make` 兩段式標註。）

### 7.3 刪除連動風險驗證：`infra/cloudbuild` 與 `infra/k8s_optional` 有 active 測試相依

`tests/test_scaffold.py::test_odp_sd04_top_level_scaffold_exists`（L12）以 `expected_paths` 清單斷言頂層 scaffold 目錄存在，其中 L58 = `"infra/k8s_optional"`、L59 = `"infra/cloudbuild"`，並於 L67-68 斷言 `missing == []`。因此 §5.2 序號 9、10 的刪除**必須連動修改該測試**。

現況基線（該測試目前為綠）：

```bash
uv run --python 3.12 --frozen python -m pytest tests/test_scaffold.py -q
# -> ..                                                          [100%]  (2 passed)
```

非破壞性模擬（不實際刪檔，重放該測試自身的斷言）：

```bash
uv run --python 3.12 --frozen python - "$PWD" <<'PY'
import ast, pathlib, sys
ROOT = pathlib.Path(sys.argv[1]).resolve()
fn = next(n for n in ast.parse((ROOT/"tests/test_scaffold.py").read_text()).body
          if isinstance(n, ast.FunctionDef) and n.name == "test_odp_sd04_top_level_scaffold_exists")
expected = ast.literal_eval(next(s.value for s in fn.body
          if isinstance(s, ast.Assign) and s.targets[0].id == "expected_paths"))
deleted = {"infra/cloudbuild", "infra/k8s_optional"}
now   = [p for p in expected if not (ROOT/p).exists()]
after = [p for p in expected if p in deleted or not (ROOT/p).exists()]
print("expected_paths:", len(expected))
print("current  -> missing=%s  assert missing==[] : %s" % (now, now == []))
print("deleted  -> missing=%s  assert missing==[] : %s" % (after, after == []))
PY
```

實測輸出：

```text
expected_paths: 51
current  -> missing=[]  assert missing==[] : True
deleted  -> missing=['infra/k8s_optional', 'infra/cloudbuild']  assert missing==[] : False
```

**結論**：兩個目錄在部署面確無 caller（無 workflow、無腳本、無 Terraform、無 `cloudbuild.yaml`），刪除判定成立；但它們在 **測試面有一個 active 斷言相依**。前版報告寫「無 active code、workflow 或 Terraform 引用」在部署面正確，卻遺漏了這個測試相依，會讓 Wave 2 只刪檔就把 CI 弄紅。本版已於 §4.5 與 §5.2 補上「連動編輯（必須）」欄位。

### 7.4 舊機制移除證明重現

```bash
# 全歷史僅一筆刪除 commit
git log --diff-filter=D --name-only --pretty=format:'COMMIT %H %ad %s' --date=short \
  origin/dev -- '*external_proof*' '*external-proof*' | grep '^COMMIT'
# -> COMMIT 1a8b0f44453d4de76a4a4b51b8c12c9a3005dbb4 2026-08-14 refactor(tooling): retire pr82 campaign and unsupported adapters

# 分類計數（期望：1 / 12 / 1 / 2 / 1 / 15，合計 32）
git show --diff-filter=D --name-only --pretty=format:'' 1a8b0f44 | grep -E 'external.proof' | sort > /tmp/removed.txt
grep -c '^\.github/workflows/'                              /tmp/removed.txt   # 1
grep -c '^delivery_toolchain/e2e/check_external_proof'      /tmp/removed.txt   # 12
grep -c '^delivery_toolchain/e2e/generate_external_proof'   /tmp/removed.txt   # 1
grep -c '^delivery_toolchain/e2e/sync_external_proof'       /tmp/removed.txt   # 2
grep -c '^delivery_toolchain/e2e/update_external_proof'     /tmp/removed.txt   # 1
grep -c '^tests/e2e/'                                       /tmp/removed.txt   # 15
wc -l < /tmp/removed.txt                                                       # 32

# 負向驗證：現況無任何 external-proof 執行檔
git ls-files | grep -Ei 'external.proof' | grep -E '\.(py|yml)$'   # -> 空
```

實測輸出與上列註解完全一致。

### 7.5 旁路入口盤點重現（fail-closed）

第五次審查退件的根因是：§2 內文宣稱「非文件檔案共 4 個」，但同一條指令在 base advance 後的合成 head 上實際回傳 5 個檔案（多出 `support/sidecars/.../ODP-DEPLOY-SCHEDULER-ROLLBACK-RESTORE-001-SIDECAR-REVIEW.md`）。該命中已於 §2 分類為**純文件誤判**（散文引述 gcloud 指令、mode `100644`、無 caller），而非縮小掃描範圍將其隱藏。

為避免同類數字漂移再次只靠人工核對，本節改寫為 fail-closed 斷言腳本：**檔案集合、三分類檔數、WIF 身分、執行位元、sidecar 純文件性質**共 14 項，任一項不符即以非 0 離開。存為 `/tmp/bypass_scan.sh` 後執行：

```bash
bash /tmp/bypass_scan.sh; echo "EXIT=$?"
```

```bash
#!/usr/bin/env bash
# §7.5 旁路入口盤點（fail-closed）：檔案集合、執行位元、文件性質任一項不符即以非 0 離開。
set -uo pipefail
fails=0
ok()  { printf 'OK   %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; fails=$((fails + 1)); }
eq()  { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 got=$2 want=$3"; fi; }

GCPRE='gcloud run deploy\|gcloud run services update-traffic\|gcloud run jobs \|gcloud scheduler jobs '
SIDECAR='support/sidecars/ODP-DEPLOY-SCHEDULER-ROLLBACK-RESTORE-001/ODP-DEPLOY-SCHEDULER-ROLLBACK-RESTORE-001-SIDECAR-REVIEW.md'

# --- (1) 具 GCP 變更指令之檔案集合（只排除 docs/ 與 tests/，刻意不排除 support/）---
scan() { git grep -ln "$GCPRE" -- . | grep -v '^docs/' | grep -v '^tests/' | sort; }
eq 'S-gcp-file-count                ' "$(scan | wc -l)" '6'
eq 'S-gcp-file-set                  ' "$(scan | tr '\n' ',' | sed 's/,$//')" \
   ".github/workflows/deploy-dev.yml,product_ops/deployment/bluegreen_release.py,product_ops/deployment/cloud_run_release_traffic.sh,product_ops/deployment/deploy_cloud_run_waji.sh,product_ops/deployment/validate_cloud_run_live_deployment.py,$SIDECAR"

# --- (2) 三分類：執行面 4 / 驗證器 1 / 純文件 1 ---
eq 'S-class-executable-4           ' "$(scan | grep -c '^\(\.github/workflows/deploy-dev\.yml\|product_ops/deployment/deploy_cloud_run_waji\.sh\|product_ops/deployment/cloud_run_release_traffic\.sh\|product_ops/deployment/bluegreen_release\.py\)$')" '4'
eq 'S-class-validator-1            ' "$(scan | grep -c 'validate_cloud_run_live_deployment\.py$')" '1'
eq 'S-class-textonly-1             ' "$(scan | grep -c '^support/')" '1'

# --- (3) WIF 身分：全庫唯一 ---
eq 'S-wif-workflow-set             ' "$(git grep -ln 'google-github-actions/auth\|workload_identity_provider' -- .github/workflows/ | sort | tr '\n' ',' | sed 's/,$//')" '.github/workflows/deploy-dev.yml'

# --- (4) 執行位元：主要旁路面可執行、次級需 source ---
mode() { git ls-files -s "$1" | awk '{print $1}'; }
eq 'S-mode-deploy_cloud_run_waji   ' "$(mode product_ops/deployment/deploy_cloud_run_waji.sh)"   '100755'
eq 'S-mode-cloud_run_release_traffic' "$(mode product_ops/deployment/cloud_run_release_traffic.sh)" '100644'
eq 'S-mode-bluegreen_release        ' "$(mode product_ops/deployment/bluegreen_release.py)" '100644'

# --- (5) Sidecar 純文件性質證明：無執行位元、全樹皆 md、無 caller ---
eq 'S-sidecar-mode-non-exec        ' "$(mode "$SIDECAR")" '100644'
eq 'S-sidecar-hit-lines            ' "$(grep -n "$GCPRE" "$SIDECAR" | cut -d: -f1 | tr '\n' ',' | sed 's/,$//')" '36,54,68'
eq 'S-support-tree-all-md          ' "$(git ls-files support/ | grep -cv '\.md$')" '0'
eq 'S-support-tree-no-exec-bit     ' "$(git ls-files -s support/ | awk '$1!="100644"' | wc -l)" '0'
# 無任何 workflow / 腳本 / 程式碼 執行或 source 此 packet（排除本報告與 support/ 自身、排除 orchestrator 派工狀態紀錄）
eq 'S-sidecar-no-caller            ' "$(git grep -rln 'ODP-DEPLOY-SCHEDULER-ROLLBACK-RESTORE-001-SIDECAR-REVIEW' -- . | grep -v '^docs/audits/' | grep -v '^support/' | grep -v '^docs-site/orchestrator-state\.json$' | wc -l)" '0'

printf '\nfails=%s\n' "$fails"
[ "$fails" -eq 0 ] || exit 1
exit 0
```

實測輸出（本次提交 head）：

```text
OK   S-gcp-file-count
OK   S-gcp-file-set
OK   S-class-executable-4
OK   S-class-validator-1
OK   S-class-textonly-1
OK   S-wif-workflow-set
OK   S-mode-deploy_cloud_run_waji
OK   S-mode-cloud_run_release_traffic
OK   S-mode-bluegreen_release
OK   S-sidecar-mode-non-exec
OK   S-sidecar-hit-lines
OK   S-support-tree-all-md
OK   S-support-tree-no-exec-bit
OK   S-sidecar-no-caller

fails=0
EXIT=0
```

負向驗證（證明本節斷言確實 fail-closed，而非恆真）：把 `S-gcp-file-count` 的期望值改回前一版錯誤的 `5`，腳本即應失敗——亦即這正是第五次退件所指出的漂移，現已可被機器擋下：

```bash
sed "s/\" '6'/\" '5'/" /tmp/bypass_scan.sh > /tmp/bypass_scan_neg.sh   # 只改第一項期望值
bash /tmp/bypass_scan_neg.sh; echo "EXIT=$?"
# -> FAIL S-gcp-file-count                 got=6 want=5
#    fails=1
#    EXIT=1
```

### 7.5a 擴大旁路掃描重現（GKE / Terraform / 手動部署文件）

```bash
# GKE data-platform README 之 docker build / render / kubectl 指引行號驗證
check() {
  local got; got="$(sed -n "${2}p" "$1")"
  if printf '%s' "$got" | grep -qF "$3"; then printf 'OK   %s:%s\n' "$1" "$2"
  else printf 'FAIL %s:%s want=%s got=%s\n' "$1" "$2" "$3" "$got"; return 1; fi
}
K=infra/k8s/data-platform/README.md
check $K 40 'docker build'
check $K 41 'data-platform.Dockerfile'
check $K 53 'render.py'
check $K 55 'data-image'
check $K 62 'output /tmp/oday-data-platform.yaml'
check $K 87 'kubectl apply --dry-run=server'
check $K 88 'kubectl apply -f'

# Terraform cloud_run.tf 行號驗證
F=infra/terraform/cloud_run.tf
check $F  1 'google_cloud_run_v2_service'
check $F 33 'image = var.api_image'
check $F 119 'Production Cloud Run cannot deploy a mutable image tag'
check $F 153 'google_cloud_run_v2_service'
check $F 178 'image = var.web_image'
check $F 254 'Production Web cannot deploy a mutable image tag'

# Terraform README bootstrap 指引行號
G=infra/terraform/README.md
check $G 102 'terraform -chdir=infra/terraform init'
check $G 107 'terraform -chdir=infra/terraform plan'
check $G 112 'first environment bootstrap'
check $G 128 'Never use'

# GCP_DEPLOY_GUIDE.md 行號
H=docs/deployment/GCP_DEPLOY_GUIDE.md
check $H  7 'local environment variables during manual execution'
check $H 42 'api.Dockerfile'
check $H 47 'web.Dockerfile'

# 全庫 workflow 無 terraform 負向驗證
count=$(git grep -rn 'terraform\|hashicorp' -- .github/workflows/ | wc -l)
[ "$count" -eq 0 ] && echo 'OK   no terraform in workflows' || echo "FAIL terraform in workflows count=$count"

# 全庫無 workflow 呼叫 render.py 或 kubectl apply
count2=$(git grep -rn 'render\.py\|kubectl apply' -- .github/workflows/ | wc -l)
[ "$count2" -eq 0 ] && echo 'OK   no render.py/kubectl in workflows' || echo "FAIL render.py/kubectl in workflows count=$count2"
```

實測輸出（基準 commit）：全部 OK。

---

### 7.6 計數對帳自動驗證（本報告自身的一致性檢查）

歷次審查退件的共同根因，是報告內不同章節的檔數/項數互相打架（第三次為 §7.2 內文宣稱 76 項、實際 77 項；第五次為 §2 內文宣稱 4 個檔案、同一條指令在合成 head 上實際回傳 5 個）。為讓此類錯誤可被機器擋下而非靠人工核對，下列腳本**直接解析本報告的表格與 §7.2 內嵌腳本**，重算 §5.1 全部統計、對帳「§7.2 宣稱斷言數 = 腳本內 `check` 呼叫數 = 腳本自我斷言常數」三者，並額外對帳「§2 三分類檔數合計 = §2 宣稱命中數 = §7.5 腳本期望常數」與「§7.5 內文宣稱項數 = 腳本 `eq` 呼叫數」。任一項不符即以 `EXIT=1` 離開。將其存為 `/tmp/recon.py` 後執行：

```bash
python3 /tmp/recon.py docs/audits/ODP_DEPLOYMENT_DEAD_CODE_AUDIT.md; echo "EXIT=$?"
```

```python
import re, sys, pathlib
doc = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
lines = doc.splitlines()

def table_rows(start_re, stop_re):
    out, on = [], False
    for l in lines:
        if re.match(start_re, l): on = True; continue
        if on and re.match(stop_re, l): break
        if on and l.startswith('|') and not re.match(r'^\|[\s\-:|]+\|$', l):
            c = [x.strip() for x in l.strip('|').split('|')]
            if c[0] not in ('#', '序號', '元件名稱與路徑'): out.append(c)
    return out

# --- Axis A: section 4 rows ---
sec, rows = None, {}
for l in lines:
    m = re.match(r'^###\s+(4\.\d)\s', l)
    if m: sec = m.group(1); rows.setdefault(sec, []); continue
    if re.match(r'^####\s', l) or re.match(r'^##\s+5\.', l): sec = None; continue
    if sec and l.startswith('|') and not re.match(r'^\|[\s\-:|]+\|$', l):
        c = [x.strip() for x in l.strip('|').split('|')]
        if len(c) >= 5 and c[0] != '元件名稱與路徑': rows[sec].append(c[-2])
def bucket(d):
    if 'DELETE' in d or 'RETIRE' in d: return 'DELETE'
    if 'REPLACE' in d: return 'REPLACE'
    return 'KEEP'
A = {'KEEP': 0, 'REPLACE': 0, 'DELETE': 0}
for s in rows:
    for d in rows[s]: A[bucket(d)] += 1
nA = sum(len(v) for v in rows.values())

# --- Axis B: section 3.2.1 - 3.2.6 residual files ---
B = {}
for key, start in [('3.2.1', r'^####\s+3\.2\.1'), ('3.2.2', r'^####\s+3\.2\.2'),
                   ('3.2.3', r'^####\s+3\.2\.3'), ('3.2.4', r'^####\s+3\.2\.4'),
                   ('3.2.5', r'^####\s+3\.2\.5'), ('3.2.6', r'^####\s+3\.2\.6')]:
    B[key] = len(table_rows(start, r'^(####|##|---)'))
nB = sum(B.values())

d52 = len(table_rows(r'^###\s+5\.2', r'^(###|##)'))
d53 = len(table_rows(r'^###\s+5\.3', r'^(###|##)'))

ok = True
def assert_eq(label, got, want):
    global ok
    good = got == want
    ok &= good
    print(f"{'OK  ' if good else 'FAIL'} {label:<46} got={got:<4} want={want}")

print("--- 軸 A：§4 元件生命週期 ---")
assert_eq("§4 總列數", nA, 91)
assert_eq("§4 KEEP", A['KEEP'], 78)
assert_eq("§4 REPLACE", A['REPLACE'], 8)
assert_eq("§4 DELETE/RETIRE", A['DELETE'], 5)
print("--- 軸 B：§3.2 external-proof 殘留 ---")
for k, want in [('3.2.1', 5), ('3.2.2', 7), ('3.2.3', 3), ('3.2.4', 2), ('3.2.5', 1), ('3.2.6', 2)]:
    assert_eq(f"§{k} 檔數", B[k], want)
assert_eq("§3.2 殘留合計", nB, 20)
assert_eq("軸 B REPLACE/UPDATE (3.2.2+3.2.3)", B['3.2.2'] + B['3.2.3'], 10)
assert_eq("軸 B ARCHIVE/KEEP (3.2.4+3.2.6)", B['3.2.4'] + B['3.2.6'], 4)
# --- §7.2 斷言數自我對帳：宣稱數 / 腳本內呼叫數 / 腳本自我斷言常數三者必須一致 ---
sec72 = doc[doc.index("### 7.2 行號引用逐項驗證"):doc.index("### 7.3 ")]
FENCE = chr(96) * 3
b0 = sec72.index("#!/usr/bin/env bash")
script72 = sec72[b0:sec72.index(FENCE, b0)]
n72 = len(re.findall(r"(?:^|;[ \t]*)check[ \t]", script72, re.M))
claimed72 = int(re.search(r"做 \*\*(\d+) 項\*\* fail-closed 斷言", sec72).group(1))
selfconst72 = int(re.search(r'-eq (\d+) \] \|\| \{ printf .FAIL declared-count', script72).group(1))
print("--- §7.2 行號斷言數 ---")
assert_eq("§7.2 腳本內 check 呼叫數", n72, 121)
assert_eq("§7.2 內文宣稱數 == 實際數", claimed72, n72)
assert_eq("§7.2 腳本自我斷言常數 == 實際數", selfconst72, n72)
# --- §2 旁路掃描分類 vs §7.5 腳本常數（第五次退件之根因：內文 4、指令 5）---
sec2 = doc[doc.index("## 2. 繞過 Runtime Release"):doc.index("## 3. 舊版 External Proof")]
claimed2 = int(re.search(r"共命中 \*\*(\d+) 個\*\*檔案", sec2).group(1))
cls2 = [int(m) for m in re.findall(
    r"^\| \*\*(?:具實際 GCP 變更能力|驗證器：僅字串常值|純文件：review packet 散文)\*\* \| (\d+) \|", sec2, re.M)]
sec75 = doc[doc.index("### 7.5 旁路入口盤點重現"):doc.index("### 7.5a ")]
b75 = sec75.index("#!/usr/bin/env bash")
script75 = sec75[b75:sec75.index(FENCE, b75)]
const75 = int(re.search(r"S-gcp-file-count.*?'(\d+)'\s*$", script75, re.M).group(1))
n75 = len(re.findall(r"^eq ", script75, re.M))
claimed75 = int(re.search(r"共 (\d+) 項", sec75).group(1))
print("--- §2 旁路掃描分類 vs §7.5 腳本常數 ---")
assert_eq("§2 三分類檔數合計 == 宣稱命中數", sum(cls2), claimed2)
assert_eq("§2 宣稱命中數 == §7.5 腳本期望常數", claimed2, const75)
assert_eq("§7.5 內文宣稱項數 == 腳本 eq 呼叫數", claimed75, n75)
print("--- 合併行動清單 ---")
assert_eq("§5.2 刪除清單項數", d52, 10)
assert_eq("§5.2 = 軸A DELETE + 軸B DELETE", A['DELETE'] + B['3.2.1'], d52)
assert_eq("§5.3 替換清單項數", d53, 9)
assert_eq("§5.3 = 軸A REPLACE + 1 (KEEP&EXPAND)", A['REPLACE'] + 1, d53)
print("\nALL RECONCILED" if ok else "\nRECONCILIATION FAILED")
sys.exit(0 if ok else 1)
```

實測輸出（全數 OK，`EXIT=0`）：

```text
--- 軸 A：§4 元件生命週期 ---
OK   §4 總列數                                         got=91   want=91
OK   §4 KEEP                                        got=78   want=78
OK   §4 REPLACE                                     got=8    want=8
OK   §4 DELETE/RETIRE                               got=5    want=5
--- 軸 B：§3.2 external-proof 殘留 ---
OK   §3.2.1 檔數                                      got=5    want=5
OK   §3.2.2 檔數                                      got=7    want=7
OK   §3.2.3 檔數                                      got=3    want=3
OK   §3.2.4 檔數                                      got=2    want=2
OK   §3.2.5 檔數                                      got=1    want=1
OK   §3.2.6 檔數                                      got=2    want=2
OK   §3.2 殘留合計                                      got=20   want=20
OK   軸 B REPLACE/UPDATE (3.2.2+3.2.3)               got=10   want=10
OK   軸 B ARCHIVE/KEEP (3.2.4+3.2.6)                 got=4    want=4
--- §7.2 行號斷言數 ---
OK   §7.2 腳本內 check 呼叫數                             got=121  want=121
OK   §7.2 內文宣稱數 == 實際數                              got=121  want=121
OK   §7.2 腳本自我斷言常數 == 實際數                           got=121  want=121
--- §2 旁路掃描分類 vs §7.5 腳本常數 ---
OK   §2 三分類檔數合計 == 宣稱命中數                            got=6    want=6
OK   §2 宣稱命中數 == §7.5 腳本期望常數                        got=6    want=6
OK   §7.5 內文宣稱項數 == 腳本 eq 呼叫數                       got=14   want=14
--- 合併行動清單 ---
OK   §5.2 刪除清單項數                                    got=10   want=10
OK   §5.2 = 軸A DELETE + 軸B DELETE                   got=10   want=10
OK   §5.3 替換清單項數                                    got=9    want=9
OK   §5.3 = 軸A REPLACE + 1 (KEEP&EXPAND)            got=9    want=9

ALL RECONCILED
```

> **處置標籤唯一性規則**：為讓上述自動對帳可判讀，§4 每列的處置欄位只能落入 KEEP / REPLACE / DELETE·RETIRE 其中一類，不得使用如「REPLACE / RETIRE」這種跨類混寫。前版 `verify_deployment_health_backup_rollback.py` 一列即為混寫，會使同一列同時被算進替換與刪除；本版已改為單一 **REPLACE** 標籤，並在該列註明「不列入 §5.2 刪除清單」。

---

## 8. 驗收條件對照 (Acceptance Traceability)

| # | 驗收條件 | 本報告對應章節 | 滿足方式 |
|---|---|---|---|
| 1 | 以 caller/workflow/runtime unit/cron/GitHub Actions usage 逐項證明 | §4（91 列逐項）、**§4.2.1**（Cloud Scheduler cron 全鏈逐行）、§4.5（GKE CronJob 逐行）、**§4.6.1**（Dagster 宣告面與 0 命中 runtime wiring 負向證據）、**§4.6.2**（兩套 active runtime + 一套未接線宣告面的對帳矩陣）、§7.2（121 項 fail-closed 行號機器驗證） | 每列均標示 caller 檔案與行號；`make` 間接呼叫一律兩段式標註；對 active Cloud Scheduler／GKE CronJob 與未接線 Dagster definitions 分別給出建立、注入、runtime caller 或負向掃描證據 |
| 2 | 產出保留/替換/刪除清單 | §5.1（雙軸統計與對帳）、§5.2（刪除 10 項，含連動編輯）、§5.3（替換 9 項）、§5.4（保留彙整） | 兩軸母體、分項數與合併推導全部列出恆等式，可逐條核算 |
| 3 | 辨識舊 External Proof Follow-up 與任何繞過 Runtime Release 的入口 | §2（旁路 7 類，含 WIF 唯一性證明與偽陽性澄清）、§3.1（移除 commit 逐檔）、§3.2（掃描邊界 + 20 檔殘留逐檔） | 旁路以「具 GCP 變更指令」與「持有 WIF 身分」雙查證；殘留以三種掃描邊界宣告後取聯集並分流 |
| 4 | 本任務只稽核不刪 code | 全文 | 本次變更僅新增/修訂 `docs/audits/ODP_DEPLOYMENT_DEAD_CODE_AUDIT.md` 一檔；未刪除或修改任何程式、workflow、config。刪除動作全部登記為 Wave 2 `ODP-DEPLOY-DEAD-CODE-REMOVAL-001` 之待辦，並附連動編輯要求 |

### 8.1 本版針對前次審查意見之修正對照

| 前次審查意見 | 本版修正 |
|---|---|
| §1.1 稱 19 檔殘留，§3.2 與修正說明列舉 20 檔，數字不一致 | §1.1 改為「S2 聯集 21 檔 → 扣除 1 檔現行治理規劃書 → 殘留 20 檔」，與 §3.2 逐檔編號 1–20 及 §3.2.7 對帳表完全一致（§3.2.7 另附恆等式） |
| §5.1 稱 6 個孤立 external-proof 檔，但刪除清單只有 5 | §5.1 改為雙軸呈現，軸 B DELETE 明確為 **5 檔**；§5.2 另加「刪除清單組成對帳」一行（3 Docker + 5 external-proof + 2 IaC = 10） |
| 未說明「僅檔名命中」之 template 的掃描邊界 | 新增 **§3.2.0 掃描邊界宣告**（S1/S2/S3 三邊界 + 指令 + 命中數），並於 §3.2.1 序號 2 專欄說明 `EXTERNAL_PROOF_HANDBACK_TEMPLATE.json` 的 S1 命中為 0、判定依據為構件身分；§7.1 提供差集重現指令 |
| Cloud Scheduler 呼叫點應為 `deploy_cloud_run_waji.sh` L579-586，非 L575-580 | §1.1、§4.2、**§4.2.1** 全部更正為 L579-582 / L583-586（合併 L579-586）；函式定義同時由誤植的 L443-480 更正為 **L443-470**；§7.2 對這兩處提供逐行斷言 |
| （本版自查追加）`infra/cloudbuild`、`infra/k8s_optional` 宣稱無任何引用 | 更正：部署面確無 caller，但 `tests/test_scaffold.py:58-59` 有 active 斷言相依；§4.5 與 §5.2 新增「連動編輯（必須）」欄位，§7.3 附非破壞性重現證明 |
| （本版自查追加）`ci.yml` 被寫成直接呼叫 `run_product_e2e.sh` / `check_drift.py` | 更正為 `make` 兩段式標註（`ci.yml:313 → Makefile:100-102`、`ci.yml:189 → Makefile:63-64`），§4 開頭並訂立「間接呼叫標記」規則 |
| （本版自查追加）§1.1 稱「13 支 `check_external_proof_*.py`」 | 更正為 commit `1a8b0f44` 之實際刪除清單：1 workflow + 16 CLI（12 `check_` / 1 `generate_` / 2 `sync_` / 1 `update_`）+ 15 tests = 32 檔，另含同批之 `check_product_go_no_go.py`；§3.1 逐檔列出並附重現指令 |
| （本版自查追加）§3.2 部分行號為過寬區間或漏列 | 各表改列 **S1 完整命中行號與命中數**；並標註 `PLAYBOOK:128,133`、`PICKUP_BOARD:124`、`RISK_ACCEPTANCE:49` 為 `external data/provider proof` 之現行概念（S2 偽陽性）已排除；`python-inventory csv` 之 L157 不命中亦特別註明 |
| （第三次審查）§3.2.0 掃描指令未含本報告排除，實跑為 S1=21 / S2=22，與宣稱的 20 / 21 不符 | §3.2.0 三個邊界的「指令」欄改為**已含 `grep -vxF "$SELF"` 排除**之完整可貼上指令，輸出即等於宣稱數；同時明列未排除時的原始數字 S1=21 / S2=22 / S3=5，並在 §7.1 以 `/tmp/scan_boundary.sh` 對 raw 與 excluded 兩組數字同時做 fail-closed 斷言（含 `S1 ⊂ S2` 與差集個案） |
| （第三次審查）§7.2 實含 77 個 `check` 呼叫但宣稱 76；且 `check()` 在 FAIL 後仍回傳成功，不是 fail-closed 斷言 | 宣稱數更正為 **77**（可用 `grep -oE '(^\|;[[:space:]]*)check[[:space:]]' /tmp/check_lines.sh \| wc -l` 獨立複核）；`check()` 改為累計 `fails`，腳本結尾以 `exit 1` 收斂並額外斷言「宣告 77 = 實際執行 77」，§7.2 另附負向驗證證明 `EXIT=1` 確實會發生；§7.6 對帳腳本再加一組斷言，把「內文宣稱數 / 腳本呼叫數 / 腳本自我斷言常數」三者鎖在一起，使同類漂移之後可被機器擋下 |
| （第四次審查）§2 旁路掃描只涵蓋 `gcloud run` / Cloud Scheduler，漏列 GKE 直接發布入口 | 新增 §1.1 第 1a 項「擴大旁路搜尋」，§2 bypass 表新增 3 列（GKE 資料平台手動部署路徑、Terraform Cloud Run bootstrap 路徑、手動部署文件引導）。`infra/k8s/data-platform/README.md` L40-45（docker build）、L53-62（render.py）、L87-88（kubectl apply）完整呼叫鏈逐行記錄，並標示 `deployment_runtime.py` 之內建遷移收據門禁。§2 掃描方法新增 kubectl/terraform/render.py 擴大搜尋指令與結果 |
| （第四次審查）未核對 Terraform Cloud Run apply 是否屬 bootstrap 還是 runtime deploy path | §2 bypass 表新增「Terraform Cloud Run bootstrap 路徑」列：判定為 **bootstrap-only**——全庫無 workflow 執行 terraform（`git grep` 證明）、Terraform 不管理 Cloud Run Jobs/Scheduler、README L112-113 自述 bootstrap、L128 禁止 routine `-target`；但標示 `.tfvars` image 變更後 apply 為理論旁路面 |
| （第四次審查）未處理 `docs/deployment/GCP_DEPLOY_GUIDE.md` 之 manual/local deployment guidance | §2 bypass 表新增「手動部署文件引導」列：標示 L7 之 fallback 敘述與 L39-49 之步驟指引，釐清其與 `deploy_cloud_run_waji.sh` 手動執行之關係，建議 Wave 2 後更新文件 |
| （第五次審查）§2 宣稱具 GCP 變更指令之非文件檔案為 4 個，但同一條指令在 base advance 後的合成 head 上也命中 `support/sidecars/ODP-DEPLOY-SCHEDULER-ROLLBACK-RESTORE-001/…-SIDECAR-REVIEW.md`；需分類此純文字誤判或縮小掃描範圍 | 選擇**分類而非縮小掃描範圍**（縮小範圍會讓同類命中日後靜默消失）。§2 新增掃描邊界宣告，明示該指令只排除 `docs/` 與 `tests/`、**不排除 `support/`**，並將命中數更正為 **5**，以三分類表列出「具實際 GCP 變更能力 3 / 驗證器僅字串常值 1 / 純文件 review packet 散文 1」；§2 bypass 表新增「Sidecar review packet（非旁路，文字誤判）」列，逐項舉證：命中行為 L36/L54/L68 之審查散文、mode `100644` 無執行位元、`support/` 全樹 98 檔皆為 `.md` 且皆 `100644`、全庫無任何 workflow/腳本/程式碼執行或 `source` 此檔（唯一提及處為 `docs-site/orchestrator-state.json` 之派工紀錄）。§7.5 由註解式輸出改寫為 **13 項 fail-closed 斷言腳本**（檔案集合、三分類檔數、WIF 身分、執行位元、sidecar 純文件性質），並附負向驗證：把期望值改回錯誤的 `4` 即 `EXIT=1`。§7.6 另加三組交叉斷言，把「§2 三分類合計 = §2 宣稱命中數 = §7.5 腳本期望常數」與「§7.5 內文宣稱項數 = 腳本 `eq` 呼叫數」鎖在一起，使此類內文與指令脫節之漂移日後可被機器擋下 |
| （第六次審查）報告 metadata 審查人標註為舊值 Codex；§7.5 範例輸出含尾隨空白；盤點基準未明確區分歷史掃描基準 3329416d 與當前 composed head 04ddafe9 | 審查人更新為 **Codex2**（負責人為 **Antigravity**）；移除 §7.5 輸出區塊第 907–919 行全部尾隨空白（通過 `git diff --check`）；於 §0 標頭明確區分「歷史盤點起始基準 `3329416d`」與「當前驗證基準 `04ddafe9`」（含 PR #999 `ODP-RELEASE-MANIFEST-GATES-001`），並於 §13 約定所有行號引用與機器斷言皆以當前驗證基準之 composed head 為準，重跑 §7.1、§7.2、§7.5、§7.5a、§7.6 及 `tests/test_scaffold.py` 全數 PASS |
| （第七次審查）報告遺漏 composed head 上的 Dagster data-platform definitions surface（6 組 ScheduleDefinition cron、3 組 900s sensor、defs 掛載、pyproject.toml entrypoint、README 排程文件）；未對帳 Cloud Scheduler、GKE CronJob 與 Dagster 之邊界 | 新增 **§4.6.1**（Dagster 排程與感應器 14 項逐行宣告證據表）、新增 **§4.6.2**（兩套 active runtime 與一套未接線宣告面的對帳矩陣）；更新 §1.1 第 5 項、§4.6 表格、§5.1／§5.4 統計與 §8 驗收追蹤表 |
| （第八次審查）Dagster section 未證明 active：Docker ENTRYPOINT 實際進入 `deployment_runtime.py`，GKE CronJob 傳入 `scheduled` 並呼叫 `product_ops.data_platform.backfill`；`definitions.py` 與 backfill 共享 `DataPlaneRunner`，且 §7.2 負向輸出仍寫成 77 checks | §1.1、§4.6.1、§4.6.2 改以 runtime caller 證據與 0 命中 Dagster CLI／daemon／runtime 掃描，將 `definitions.py` 分類為 UNWIRED / REPLACE，列出與 `backfill.py` 重疊的 11 個 source kinds；§7.2 負向輸出更正並重跑為 `checks_declared=102 checks_executed=102 fails=1`、`EXIT=1`，§7.6 對帳同步為 KEEP 74 / REPLACE 8 / §5.3 9 項 |
| （第九/十次審查）actual review head 包含 origin/dev 30f885b0 (PR #1001)，但報告記錄 04ddafe9 且遺漏 delivery_toolchain/release/release_receipts.py 與 tests/ops/test_release_receipts.py | 更新 composed-head 基準至 `30f885b0`（PR #999 + PR #1001 合併成果）；負責人更新為 **Antigravity**；新增 `release_receipts.py`（以及 `release_manifest.py`、`migrate_gate_registry.py`）之 caller、workflow 與 runtime 使用證明，分類為 **保留 (KEEP)**；§4 總列數更新為 **90 列**（KEEP 77 / REPLACE 8 / DELETE 5）；§7.2 機器驗證斷言增至 **113 項**（全部通過）；§7.6 自動對帳腳本與統計矩陣同步對齊 |
| （第十一次審查）§5.2 刪除清單第 7 列格式遺漏「類型」欄導致欄位錯位；且基準 advance 至 origin/dev fd410a63 (PR #1003) 引入 product_ops/deployment/bluegreen_release.py 與 tests/ops/test_bluegreen_release.py | 補正 §5.2 第 7 列之「孤立 JSON」類型與欄位對齊；更新 composed-head 基準至 `fd410a63`（PR #999 + PR #1001 + PR #1003 合併成果）；將 `bluegreen_release.py` 納入 §2 旁路分析（4 具變更能力 / 1 驗證器 / 1 純文件，共 6 檔）、§4.2 元件盤點（分類為 **保留 (KEEP)**）與 §5.4 保留彙整；§4 總列數更新為 **91 列**（KEEP 78 / REPLACE 8 / DELETE 5）；§7.2 機器驗證斷言增至 **121 項**（全部通過）；§7.5 旁路斷言更新為 **14 項**（包含 `bluegreen_release.py` 集合與 mode 斷言）；§7.6 自動對帳腳本與統計矩陣同步對齊（ALL RECONCILED） |

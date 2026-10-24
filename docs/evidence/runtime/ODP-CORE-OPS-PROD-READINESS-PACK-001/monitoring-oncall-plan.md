# core-ops production 監控、watch window 與值班計畫（DRAFT · 未簽署）

行號以 `origin/dev` @ `5d761526` 為準。

**結論先講：**

- repo 內**沒有任何已部署的告警基礎設施**：Terraform 沒有 Cloud Monitoring 的 alert policy、uptime check、notification channel、log metric、log sink、dashboard，也沒有 SLO 資源。
- 告警只以 JSON 設定加上應用程式內的路由程式碼存在。路由的接收者是角色字串，不是真人或真實的通道。
- watch window 是純人工作業，沒有 receipt 產生者。
- gate-6 的「monitoring watch window and on-call route assigned and live」（`docs/evidence/gates/RELEASE_GATE_REGISTRY.json:458`）目前是**缺**。

## 1. repo 內實際存在的監控

### 1.1 基礎設施（Terraform）

| 項目 | 現況 | 依據 |
|---|---|---|
| Cloud Monitoring／Logging 資源 | 不存在。`infra/terraform` 底下所有子目錄都沒有 `google_monitoring_*`、`google_logging_metric`、`google_logging_project_sink` | 已搜尋整個 `infra/terraform`，查無結果 |
| 啟用的 API | `required_apis` 共 10 個，不含 `monitoring.googleapis.com` 與 `logging.googleapis.com` | `infra/terraform/main.tf:59-70` |
| Cloud Run probe | API：startup probe 打 `/readiness`，liveness probe 打 `/liveness`。Web：TCP 3000 | `infra/terraform/cloud_run.tf:102-123`、`:276-295` |
| 監控相關 IAM | 只有 `roles/logging.viewer` 給 deployer SA，用途是讀 egress receipt；沒有 `roles/monitoring.*` | `infra/terraform/iam.tf:194-198` |

Runtime Release 的 production 部署是用 `gcloud` 直接部署，不執行 Terraform root stack（`docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md:135`）。所以就算 Terraform 裡有 probe，production 實際的 probe 設定也要看 `deploy_cloud_run_waji.sh` 的 `gcloud run deploy` 參數。本包**未驗證**這一點。

### 1.2 監控設定檔（JSON，沒有被渲染成雲端資源）

- `infra/monitoring/README.md:35-36` 寫明這些 JSON 要由 Terraform／`gcloud` exporter 渲染成 Cloud Monitoring 資源。repo 內沒有針對告警與 SLO 的 exporter。
- **`infra/monitoring/alerts.json`**：12 條告警，條件都是文字描述，沒有數值門檻（例如 `:17`）。
  - 路由在 `:124-139`：預設接收者 `admin`；P1 給 `ops-lead`；P2、P3 給 `oncall-engineer`。
  - 和 core-ops 無關的告警：`forecast-daily-failed`（`:23`）與 `model-drift-high`（`:78`）屬於模型功能，core-ops 下這些功能本來就是拒絕的。
- **`infra/monitoring/slo.json`**：
  - 可用率 `0.995`，30 天（`:5-12`）。
  - `latency_p95_ms` 800（`:14-18`）。
  - RPO／RTO 目標（`:29-38`）。
- **`infra/monitoring/dashboards.json`**：`slo_owner` 是「SRE Lead / Platform Operations」，`watch_window_minutes` 是 15（`:9-10`）。

### 1.3 應用程式內

| 項目 | 現況 | 依據 |
|---|---|---|
| health／readiness | `/liveness`、`/healthz`、`/readiness`、`/health`、`/platform/health` | `apps/api/oday_api/main.py:773-895` |
| release identity | `/platform/version`、`/platform/release-identity` | `main.py:897-912` |
| metrics 匯出 | `/platform/observability`、`/platform/metrics/export` 會呼叫 `ProductionMetricsExporter`，POST 到 `monitoring.googleapis.com/v3` 的 custom metric。只有被呼叫時才匯出，沒有排程 | `main.py:914-915`；`shared/observability/metrics.py:679`、`:707`、`:820` |
| 告警路由 | `AlertRouter` 讀 `alerts.json`，把接收者角色字串當 `user_id` 送到 notification service。某個 severity 沒有接收者時會 fail closed | `shared/observability/alerts.py:43`、`:154-168`、`:292-293` |
| 實際觸發告警的程式 | 只有一處：`dlq-spike`，位於 assisted listing intake worker | `apps/worker/assisted_listing_intake/worker.py:218-222` |
| 告警需要的 release SHA | `AlertRouter` 讀 `RELEASE_SHA` 或 `TRUSTED_DEPLOYED_RELEASE_SHA`（`alerts.py:45-49`）。部署腳本與 workflow 設定的是 `ODAY_RELEASE_SHA`，前兩者沒有任何部署路徑會設定 | `alerts.py:45-49`；`deploy-dev.yml:1258` |
| on-call 傳送 | 需要 `NOTIFICATION_ADAPTER_TYPE`、`ONCALL_ENDPOINT_URL`、`ONCALL_PROVIDER_SECRET`；production 一定要有 on-call route。這三個在 Terraform 與 workflow 裡都沒有設定 | `modules/notifications/infrastructure/adapters.py:757-760` |
| 唯一的 on-call 傳送測試 | 本機 loopback，結果 `status: FAILED`、`http_status: 0` | `support/sidecars/ODP-PLAN-OBSERVABILITY-LIVE-001/ODP-PLAN-OBSERVABILITY-LIVE-001-SIDECAR-REVIEW.md:18-22`、`:34` |
| 結構化 log | JSON 寫到 stderr，Cloud Run 會收進 Cloud Logging（平台預設行為，本包**未驗證**） | `shared/observability/logging.py:135`、`:144` |

### 1.4 watch window 的程式

- `bluegreen_release.py` 寫明「Start watch window」是呼叫者的責任，沒有 `watch` 子命令（`product_ops/deployment/bluegreen_release.py:852-863`、`:989-1047`）。
- `staging_closeout` 只會**驗證** watch receipt：它讀 `vars.ODP_PRODUCTION_WATCH_CLOSEOUT_URI`，呼叫 `verify_watch_window_receipt`（`.github/workflows/deploy-dev.yml:1695`、`:1793-1798`）。驗證內容是：
  - `release_sha` 必須相符；
  - 狀態必須是 `WATCH_PASSED`；
  - 至少 15 分鐘（`shared/observability/watch_window.py:694-734`）。
- repo 內只有 fail-closed 的負向測試會呼叫 `record_deployment_watch_window_status`（`product_ops/deployment/validate_cloud_run_live_deployment.py:617-634`）。**沒有任何真實的產生者。**
- 已 commit 的 `docs/evidence/watch_window_receipt.json` 是 `LOCAL_TEST_ONLY`，用的也是已淘汰的專案（`docs/deployment/GCP_DEPLOY_GUIDE.md:196`）。

## 2. 已記錄的 watch 與觀察義務

### 2.1 切換後的 watch window（上線當下）

| 參數 | 值 | 性質 | 依據 |
|---|---|---|---|
| Watch window | 30 分鐘 | 寬鬆初始值，沒有實測依據，**不是自動化閘門** | `docs/evidence/runtime/ODP-PROD-OPS-WATCH-PARAMS-001/README.md:22`、`:32-37`；`docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:288-296` |
| SLO 門檻 | 錯誤率 < 5% 且 p95 < 5 秒 | 同上 | 同上 README `:23` |
| Rollback 觸發 | 錯誤率 > 10% 持續 5 分鐘；p95 > 10 秒持續 5 分鐘；health check 連續 3 次失敗；auth failure、資料品質、job、queue lag、audit 缺失、operator 判定 | 人工判斷 | 同上 README `:24`；`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:301-309` |
| Rollback／On-call owner | Human/Ops（文件中記載了一個具名帳號） | 只寫在文件裡，沒有接到任何告警路由或設定 | 同上 README `:25`；`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:266`、`:307` |

**文件之間互相矛盾，需人工決定：**

- watch window：15 分鐘（`infra/monitoring/dashboards.json:10`、`docs/runbooks/observability-and-runbook.md:168`、`watch_window.py` 的下限）對 30 分鐘（PROD-OPS-05）。
- p95：800 ms（`slo.json:17`）對 < 5 秒（PROD-OPS-05）對 ≤ 3.0 秒（NFR）。

### 2.2 上線後的觀察義務（`ODP-POSTDEPLOY-WATCH-CLOSEOUT-001`）

來源：`docs/evidence/completion/ODP-RELEASE-ACCEPTANCE-CARRYFORWARD-001/obligation_matrix.json`

| Obligation | 要求 | 觀察期 | Owner | 行號 |
|---|---|---|---|---|
| `ODP-NFR-PERF-001-PROD` | API P95 ≤ 3.0 秒 | ≥ 24 小時的 production 實際流量；receipt 取自 `run.googleapis.com/request_latencies` | Human/Ops | `:111-127` |
| `ODP-NFR-BATCH-002` | 每日 batch 在營業日 cutoff 前完成 | 連續 7 個營業日。**cutoff 時間在 repo 內沒有定義，需要 Human/Ops 提供** | Human/Ops | `:140-167` |
| `ODP-NFR-AVAIL-003` | OpsBoard 月可用率 ≥ 99.5% | 一個完整日曆月；以 uptime check 的 `check_passed` 比率 ≥ 0.995 為準 | Human/Ops | `:170-186` |

- 三項都是 `BLOCKED_BY_EVIDENCE`。它們屬於 post-production 觀察，**不擋** production admission（`:160-167`）。
- 注意：`ODP-NFR-AVAIL-003` 需要 production uptime check，但 Terraform 裡沒有 uptime check 資源（見 §1.1）。

**task owner 記載不一致：**

- registry 寫 `ODP-POSTDEPLOY-WATCH-CLOSEOUT-001` 的 owner 是 Antigravity5（`RELEASE_GATE_REGISTRY.json:495`）。
- `owner_inventory.json:86-88` 寫的是 Antigravity3，`status: todo`。
- 這個 task 的定義不在 repo 內，本包**未驗證**。

### 2.3 事故等級與回應（既有 runbook）

- 等級 P0 到 P4（`docs/runbooks/incident-management.md:9-15`）。
- 角色：Incident Commander、Tech Lead、Data Lead、ML Lead、Security Lead、Communications Lead、Scribe、Business Owner、Support Lead。P0 與 P1 必須指定 Incident Commander（`:19-21`）。
- 回應 SLA：P0 15 分鐘、P1 30 分鐘、P2 4 小時（`:50-54`）。
- `alerts.json` 只定義到 P1–P3，沒有 P0 路由（`:124-139`）。

## 3. core-ops 上線需要補的監控（缺口清單，需人工決定）

1. 決定 production 的告警是否走 Cloud Monitoring。若是：
   - 要啟用 Monitoring API；
   - 要建立 alert policy、uptime check、notification channel；
   - 要給 runtime 身分 metric writer 權限。

   這些都是 IaC 或 IAM 變更，不在本 task 範圍。
2. 至少涵蓋 core-ops 相關的訊號：
   - `api-availability-drop`（P1）
   - `audit-write-failure`（P1）
   - `unauthorized-spike`（P2）
   - `dlq-spike`（P2）
   - `alert-delivery-failure`（P1）
   - `/readiness` 的 uptime

   模型類告警可以標為 not-applicable，因為 core-ops 下這些功能是拒絕的。
3. 決定告警通道並設定 `ONCALL_ENDPOINT_URL` 與 `ONCALL_PROVIDER_SECRET`。設定前，production 的 notification adapter 會 fail closed。
4. 設定 runtime 的 `RELEASE_SHA` 或 `TRUSTED_DEPLOYED_RELEASE_SHA`，否則應用程式內的告警路由會在缺 SHA 時 raise。這是程式或部署層變更，需另開 task。
5. 決定 watch receipt 由誰、用什麼工具產生、上傳到哪個 `ODP_PRODUCTION_WATCH_CLOSEOUT_URI`。
6. 統一 watch window 長度與 p95 門檻（見 §2.1）。
7. 定義營業日 cutoff 時間（`ODP-NFR-BATCH-002`）。

## 4. 值班與角色表（**姓名留白，由 Human/Ops 填寫**）

| 角色 | 職責（依 repo 文件） | 上線當下必須在線 | 姓名 | 聯絡管道 | 代理人 | 依據 |
|---|---|---|---|---|---|---|
| Watch owner | 切換後監看 watch window，依 SLO 門檻判斷 | 是 | | | | `EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:266`、`:294` |
| Rollback owner | 判定並執行回滾 | 是 | | | | 同上 `:204`、`:307` |
| Incident Commander | P0／P1 事故指揮 | 是（待命） | | | | `incident-management.md:19-21` |
| `ops-lead`（P1 告警接收者） | P1 告警 | 是 | | | | `alerts.json:127-130` |
| `oncall-engineer`（P2／P3 告警接收者） | P2／P3 告警 | 是 | | | | `alerts.json:131-138` |
| `admin`（預設接收者） | 無路由的告警 | 視需要 | | | | `alerts.json:125` |
| SRE Lead／Platform Operations（SLO owner） | dashboard 與 SLO | 是 | | | | `dashboards.json:9` |
| Security Lead | auth 或權限異常 | 待命 | | | | `incident-management.md:19-21` |
| Communications Lead | 對使用者公告 | 待命 | | | | 同上 |
| Scribe | 事故紀錄 | 待命 | | | | 同上 |
| Support Lead | 使用者回報窗口 | 是 | | | | 同上；`observability-and-runbook.md:4` |
| GitHub `production` environment reviewer | 核准 deploy job | dispatch 當下 | | | | `GCP_DEPLOY_GUIDE.md:15` |

**watch window 時段（留白）：**

- 開始時間：＿＿＿＿
- 結束時間：＿＿＿＿
- 時區：＿＿＿＿

**簽核（留白）：**

| 項目 | 簽署人 | 角色 | 日期 | 決定 |
|---|---|---|---|---|
| 值班表與告警路由已指派並生效 | | Human/Ops | | □ 核准 □ 附條件 □ 退回 |
| watch window 長度與門檻 | | Human/Ops | | |
| watch receipt 產生方式 | | Human/Ops | | |

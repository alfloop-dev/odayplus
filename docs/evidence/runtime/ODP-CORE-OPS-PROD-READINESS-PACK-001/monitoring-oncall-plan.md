# core-ops production 監控、watch window 與值班計畫（DRAFT · 未簽署 · 2026-11-27 版）

- 行號以 `origin/dev` @ `48c5271b5d882d824d432f3483ab4415030bb8e6` 為準。
- GCP／GitHub 現況引用 `live-readback-20261127.md`（`L-…`）；負責人決定 A–H 見同檔 §2。

**結論先講：**

- **2026-10-24 版說「沒有任何告警基礎設施」，已過時。** `infra/terraform/monitoring.tf` 已存在（ODP-PROD-MONITORING-ALERTING-001，PR #1593，2026-10-24 合併）。live 上 production 已有 email 通道、6 條啟用中的告警政策與 Web uptime check（`L-3`）。
- **告警路由已由負責人決定（決定 C）**：Cloud Monitoring 以 email 寄給負責人，負責人擔任 on-call。
- **watch window** 依 PROD-OPS-05 為 30 分鐘，人工判斷。另外由 Claude 在上線後 24 小時內每小時讀回健康狀態（決定 C）。
- **仍缺**：watch receipt 無法以真實資料產生（`L-6`，修正 pending）；`ODP_PRODUCTION_WATCH_CLOSEOUT_URI` 未設（`L-4.3`）；應用程式內的告警路由（`alerts.json`）仍沒有接到真實通道。
- gate-6 的「monitoring watch window and on-call route assigned and live」（`docs/evidence/gates/RELEASE_GATE_REGISTRY.json:760`）：從「缺」改為「**部分**」。

## 1. 監控現況

### 1.1 Terraform（`infra/terraform/monitoring.tf`）

| 資源 | 內容 | 依據 |
|---|---|---|
| 通知通道 | email channel，來自 `monitoring_notification_emails`，或沿用既有 channel ID（`monitoring_notification_channel_ids`）。production 兩者都沒有時，plan 會失敗 | `monitoring.tf:94-105`、`:32-35`；`infra/terraform/checks.tf:175-182` |
| Web uptime | `<prefix> web /login`，每 60 秒，逾時 10 秒 | `monitoring.tf:107-142`、`:20` |
| API uptime | `<prefix> api /platform/health`，以 Monitoring service agent 的 OIDC token 驗證；production 必須設定 `monitoring_api_uptime_host`，而且要等於 `api_public_host` | `monitoring.tf:144-192`；`checks.tf:175-194`；`infra/terraform/env/prod.tfvars.example:72` |
| uptime 失敗告警 | 多於一個區域失敗、持續 180 秒（約 3 次） | `monitoring.tf:204-239`、`:21` |
| 5xx 比率 | API 與 Web 各一條，門檻 5%、持續 300 秒 | `monitoring.tf:241-284`、`:15-19` |
| API p95 | 3000 ms、持續 300 秒（依核准的 NFR ≤ 3.0 秒） | `monitoring.tf:286-319`、`:9-13` |
| Cloud Run job 失敗 | 任一 execution 失敗即觸發 | `monitoring.tf:321-357` |
| Cloud SQL 飽和 | CPU 80%、disk 85%、連線數 80%，持續 600 秒；這三個門檻是工程預設值，沒有核准來源 | `monitoring.tf:359-435`、`:23-30` |
| Monitoring API | `required_apis` 已包含 `monitoring.googleapis.com` | `infra/terraform/main.tf:87` |
| API uptime 的 invoker | production 的 API 由部署流程建立，部署腳本授予 Monitoring 通知 service agent `roles/run.invoker` | `deploy_cloud_run_waji.sh:1028-1039`、`:1163` |

### 1.2 live（`L-3`）

- email 通道「ODay Plus prod alert email」存在（`L-3.1`）。依決定 C，收件人是負責人；本包不記錄 email 地址。
- 6 條啟用中的告警政策：web uptime、Cloud SQL saturation、api 5xx > 5%、api p95 > 3000 ms、web 5xx > 5%、Cloud Run job failed（`L-3.2`）。與 §1.1 的 Terraform 定義一一對應。
- uptime check「oday-prod web /login」存在（`L-3.3`）。
- **`L-3` 沒有列出 API uptime check。** production 的 API 要等首次部署才會存在（`L-2.8`），所以在那之前這個 check 不可能通過。live 上是否已建立，**未讀回**。首次部署後要再讀一次。

### 1.3 應用程式內（與 2026-10-24 版相同，仍未接線）

| 項目 | 現況 | 依據 |
|---|---|---|
| 告警設定檔 | `infra/monitoring/alerts.json` 的 12 條告警只有文字條件；接收者是角色字串（`admin`、`ops-lead`、`oncall-engineer`），不是真人 | `alerts.json:124-139` |
| `AlertRouter` | 把接收者角色字串當 `user_id` 送到 notification service；讀 `RELEASE_SHA` 或 `TRUSTED_DEPLOYED_RELEASE_SHA`，部署路徑只設 `ODAY_RELEASE_SHA` | `shared/observability/alerts.py:43-49`、`:154-168`；`deploy-dev.yml:1733` |
| 實際觸發告警的程式 | 只有 `dlq-spike`，在 assisted listing intake worker | `apps/worker/assisted_listing_intake/worker.py:221` |
| on-call 傳送 | 需要 `ONCALL_ENDPOINT_URL`、`ONCALL_PROVIDER_SECRET`；Terraform 與 workflow 都沒設 | `modules/notifications/infrastructure/adapters.py:757-760` |

結論：production 的告警以 Cloud Monitoring 為準（§1.1、§1.2）。`audit-write-failure`、`unauthorized-spike`、`dlq-spike` 這類應用層訊號，**沒有**對應的 Cloud Monitoring 告警，也沒有經 `AlertRouter` 送到真實通道。是否要補（例如以 log-based metric 建告警），**仍待決**（HD-22）。

### 1.4 watch receipt

- `staging_closeout` 驗證 watch receipt 時呼叫 `verify_watch_window_receipt`（`deploy-dev.yml:2706`），要求 provider 簽章與 `MONITORING_PROVIDER_SECRET`（`shared/observability/watch_window.py:357-400`、`:825-844`）。以真實資料**不可能**產生通過的 receipt（`L-6.1`）。
- receipt 不存在（404）時 `staging_closeout` 改為 defer，staging 保留（`deploy-dev.yml:2674-2707`）。
- 修正 task `ODP-PROD-WATCH-RECEIPT-REAL-METRICS-261127`：**pending**（`L-6.2`）。
- receipt 的位置 `ODP_PRODUCTION_WATCH_CLOSEOUT_URI`：**未設**（`L-4.3`）。`staging_closeout` 在讀 receipt 之前就要求它（`deploy-dev.yml:2647`）。

## 2. watch window 與上線後讀回

### 2.1 切換後的 watch window（決定 C）

| 參數 | 值 | 性質 | 依據 |
|---|---|---|---|
| Watch window | **30 分鐘** | 寬鬆初始值，人工判斷，不是自動閘門 | `docs/evidence/runtime/ODP-PROD-OPS-WATCH-PARAMS-001/README.md:22`、`:31-36`；決定 C |
| SLO 門檻 | 錯誤率 < 5% 且 p95 < 5 秒 | 同上 | 同 README `:23` |
| Rollback 觸發 | 錯誤率 > 10% 持續 5 分鐘；p95 > 10 秒持續 5 分鐘；health check 連續 3 次失敗；auth failure、資料品質、job、queue lag、audit 缺失、operator 判定 | 人工判斷 | 同 README `:24` |
| Watch／rollback／on-call owner | **負責人**（決定 C） | — | 決定 C；同 README `:25` |

**2026-10-24 版列的門檻矛盾，現況：**

- watch window：PROD-OPS-05 的 30 分鐘已由決定 C 採用。`watch_window.py` 的下限 15 分鐘（`:441-448`）是 receipt 的最低要求，與 30 分鐘不衝突。`infra/monitoring/dashboards.json:10` 的 15 分鐘是未核准設定檔，不採用。
- p95：Cloud Monitoring 告警採核准的 NFR 3000 ms（`monitoring.tf:9-13`，註解寫明不採 `slo.json` 的 800 ms）。watch window 的 < 5 秒與回滾的 > 10 秒是人工判斷用的門檻。三者用途不同，已不衝突。

### 2.2 Claude 的每小時讀回（決定 C）

- **期間**：production 切換完成（`DEPLOYMENT_COMMITTED=true`）後 24 小時，每小時一次，共 24 次。
- **讀什麼（提議，待負責人確認）**：

  | 項目 | 讀法 | 不符時 |
  |---|---|---|
  | 告警事件 | Cloud Monitoring 的 incident 清單與 6 條政策的狀態 | 立即通知負責人 |
  | uptime | Web 與 API uptime check 的最近結果 | 同上 |
  | Cloud Run | `oday-prod-api`、`oday-prod-web` 的 serving revision 與流量；revision 應等於這次 release | 同上 |
  | 錯誤 log | 兩個 service 的 `severity>=ERROR` 筆數 | 明顯增加時通知負責人 |
  | Cloud Run job | worker、scheduler 最近 execution 的結果 | 失敗時通知負責人 |
  | Cloud SQL | `oday-prod-foundation-sql` 的狀態（`RUNNABLE`） | 同上 |
  | release identity | `runbook-core-ops.md` §2.3 | 見下 |

- **身分**：Claude 以唯讀身分讀回（與 `L-…` 相同的 `deborah.lu`）。release identity 需要 Web session 或 API invoker（`runbook-core-ops.md` §2.3）；Claude 是否另有 production 帳號，**仍待決**。在決定之前，release identity 由負責人在 watch window 內讀一次。
- **紀錄**：每次讀回記下時間（UTC）、各項結果與異常。記錄放在哪裡（例如另開 evidence task），**仍待決**。
- **Claude 不會自行撤銷或回滾**；異常只通知負責人，由負責人判定（`runbook-core-ops.md` §6.2）。

### 2.3 上線後的觀察義務（`ODP-POSTDEPLOY-WATCH-CLOSEOUT-001`）

來源：`docs/evidence/completion/ODP-RELEASE-ACCEPTANCE-CARRYFORWARD-001/obligation_matrix.json`

| Obligation | 要求 | 觀察期 | 現況 | 行號 |
|---|---|---|---|---|
| `ODP-NFR-PERF-001-PROD` | API P95 ≤ 3.0 秒 | ≥ 24 小時 production 實際流量；receipt 取自 `run.googleapis.com/request_latencies` | Cloud Monitoring 已有 p95 > 3000 ms 告警；量測 receipt 仍要另外產生 | `:111-127` |
| `ODP-NFR-BATCH-002` | 每日 batch 在營業日 cutoff 前完成 | 連續 7 個營業日 | **對 core-ops 記為 not-applicable**：core-ops 沒有每日 batch；上模型時再定（決定 F） | `:140-167` |
| `ODP-NFR-AVAIL-003` | OpsBoard 月可用率 ≥ 99.5% | 一個完整日曆月；以 uptime check 的 `check_passed` 比率 ≥ 0.995 為準 | Web uptime check 已存在（`L-3.3`），可作為資料來源 | `:170-186` |

- 三項都是 `BLOCKED_BY_EVIDENCE`，屬於上線後的觀察，**不擋** production admission（`:160-167`）。
- task owner 記載仍不一致：registry 寫 Antigravity5（`RELEASE_GATE_REGISTRY.json:797`），`owner_inventory.json:86-88` 寫 Antigravity3。本包**未驗證**，見 HD-29。

### 2.4 事故等級與回應（既有 runbook，未變）

- 等級 P0 到 P4（`docs/runbooks/incident-management.md:9-15`）；P0、P1 必須指定 Incident Commander（`:19-21`）。
- 回應 SLA：P0 15 分鐘、P1 30 分鐘、P2 4 小時（`:50-54`）。
- core-ops 上線初期，Incident Commander、Rollback owner、Watch owner 都由負責人擔任（決定 C）。

## 3. 仍需補的項目（需人工決定或另開 task）

1. **watch receipt 的產生**：等 `ODP-PROD-WATCH-RECEIPT-REAL-METRICS-261127` 合併（pending）。它若改到 evidence 以外的路徑，必須在凍結、選定 candidate 之前合併（決定 A；`deployment-plan.md` §5 第 1 點）。
2. **設定 `ODP_PRODUCTION_WATCH_CLOSEOUT_URI`**（staging 與 production environment，`L-4.3`）。
3. **API uptime check**：首次部署後讀回是否存在並通過（§1.2）。
4. **應用層訊號的告警**（`audit-write-failure`、`unauthorized-spike`、`dlq-spike`）：要不要以 log-based metric 補，或明確接受不補（§1.3）。
5. **值班人員直接讀 API 的身分**：是否設定 `ODP_PROD_API_INVOKER_GROUP`（`L-4.4`；`runbook-core-ops.md` §2.3）。要在首次 production 部署前決定，因為 binding 在部署時才套用。
6. **Claude 每小時讀回的項目、身分與紀錄位置**（§2.2）。

## 4. 值班與角色表

依決定 C，上線當下的 on-call、watch owner 與 rollback owner 都是負責人。下表的「方向」欄記錄這個決定；**具名欄仍留白**，由負責人簽核時填寫。

| 角色 | 職責 | 方向（決定 C） | 姓名（留白） | 聯絡管道（留白） | 代理人（留白） | 依據 |
|---|---|---|---|---|---|---|
| On-call／Watch owner | 收告警 email；監看 30 分鐘 watch window | 負責人 | | | | `ODP-PROD-OPS-WATCH-PARAMS-001/README.md:25` |
| Rollback owner | 判定撤銷（首次 release）或回滾（後續 release） | 負責人 | | | | `runbook-core-ops.md` §6；`rollback-plan.md` §3 |
| Incident Commander | P0／P1 事故指揮 | 負責人 | | | | `incident-management.md:19-21` |
| 上線後 24 小時讀回 | 每小時讀回，異常通知負責人 | Claude | — | — | — | §2.2 |
| Support Lead | 使用者回報窗口 | 待決 | | | | `observability-and-runbook.md:4` |
| GitHub `production` environment reviewer | 核准 deploy job | `Alien-alfaloop`、`ajoe734`（`L-4.5`） | | | | `GCP_DEPLOY_GUIDE.md:15` |

**watch window 時段（留白）：**

- 開始時間：＿＿＿＿
- 結束時間：＿＿＿＿
- 時區：＿＿＿＿

**簽核（留白）：**

| 項目 | 簽署人 | 角色 | 日期 | 決定 |
|---|---|---|---|---|
| 告警路由與值班（方向已由決定 C 決定）已生效 | | Human/Ops | | □ 核准 □ 附條件 □ 退回 |
| §3 的未決項目處置 | | Human/Ops | | |
| watch receipt 產生方式（等修正 task 合併後） | | Human/Ops | | |

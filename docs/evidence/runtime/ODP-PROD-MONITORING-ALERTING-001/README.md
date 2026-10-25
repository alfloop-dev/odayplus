# ODP-PROD-MONITORING-ALERTING-001：Terraform Cloud Monitoring 與告警

本文件說明本 task 在 `infra/terraform` root module 新增的 Cloud Monitoring 資源、門檻值與來源，以及上線前仍須由人提供的項目。

本 task 只改 IaC 與文件：

- **沒有**對任何真實專案執行 `terraform plan` 或 `terraform apply`；
- **沒有**呼叫 gcloud 或任何 GCP API；
- 監控資源**尚未上線**。

對應的缺口來自 readiness 稽核：`task/ODP-CORE-OPS-PROD-READINESS-PACK-001` 的 `monitoring-oncall-plan.md` §1.1、§3，以及 `gate-evidence-map.md` gate-6。

## 1. 新增內容

| 檔案 | 內容 |
|---|---|
| `infra/terraform/main.tf` | `required_apis` 加入 `monitoring.googleapis.com`。監控相關輸入也加入 `production_contract_values`，所以 prod 會拒絕 placeholder 或 `example` 之類的值 |
| `infra/terraform/monitoring.tf` | 新檔，列出所有監控資源，見下表 |
| `infra/terraform/variables.tf` | 新增四個變數：`monitoring_notification_emails`、`monitoring_notification_channel_ids`、`monitoring_api_uptime_host`、`monitoring_cloud_sql_max_connections` |
| `infra/terraform/checks.tf` | 在 `terraform_data.production_contract` 新增一條阻斷式 precondition；另新增 `check "production_monitoring_contract"`，提供可讀的診斷訊息 |
| `infra/terraform/validate_contract.py`、`tests/test_contract.py` | 斷言新資源、門檻與 prod fail-closed 規則存在，並禁止在 `monitoring.tf` 寫死 email |
| `infra/terraform/README.md`、`env/prod.tfvars.example` | 補充說明，並加入 `REPLACE_WITH` 範本值 |

`monitoring.tf` 內的資源：

| 資源 | 監控對象 |
|---|---|
| `google_monitoring_uptime_check_config.web_login` | Web 公開登入頁 `GET /login`，port 443，接受 2xx，每 60 秒一次。host 取自 `web_base_url`；沒有設定時改用 Web run.app host。路由來源是 `apps/web/src/app/login/route.ts`，middleware 也排除了這個路徑 |
| `google_monitoring_uptime_check_config.api_health`（選用，prod 必填） | API `GET /platform/health`，以 Monitoring service agent 的 OIDC token 驗證。這個端點在 DB、queue 或 live gate 不健康時回 503（`apps/api/oday_api/main.py:829`）。只有設定 `monitoring_api_uptime_host` 時才建立 |
| `google_cloud_run_v2_service_iam_member.api_uptime_invoker` | 讓 `service-<project_number>@gcp-sa-monitoring-notification.iam.gserviceaccount.com` 取得 Terraform 管理之 API 的 `roles/run.invoker`，與上一項一起建立 |
| `google_monitoring_notification_channel.email` | 每個 `monitoring_notification_emails` 地址建立一個 email channel |
| `google_monitoring_alert_policy.uptime_failure` | 每個 uptime check 一條（web，以及有設定時的 api） |
| `google_monitoring_alert_policy.cloud_run_5xx_ratio` | API 與 Web 各一條，算 5xx 佔全部 request 的比例 |
| `google_monitoring_alert_policy.api_latency_p95` | API 的 `run.googleapis.com/request_latencies` p95 |
| `google_monitoring_alert_policy.cloud_run_job_failed` | 專案內**所有** Cloud Run job 的失敗 execution。原因見 §3 |
| `google_monitoring_alert_policy.cloud_sql_saturation` | Cloud SQL 的 CPU、disk 使用率與 backend 連線數 |

所有 alert policy 都把 `notification_channels` 指到同一個 local：

- 它由 email channel 加上 `monitoring_notification_channel_ids` 組成；
- validator 會檢查每條 policy 都有這樣接線。

5xx、latency 與 uptime 三種 policy 帶有 user label `odp_alert_id = "api-availability-drop"`，對應 `infra/monitoring/alerts.json` 中的同名告警。

## 2. 門檻與來源

| 告警 | 門檻 | 來源 |
|---|---|---|
| API p95 延遲 | 每 5 分鐘視窗的 p95 > 3000 ms，持續 5 分鐘 | **canonical：** `docs/design/ODP-PGAP-RELIABILITY-001_SLO_AND_DR_TARGETS_RATIFICATION.md` §2「p95 latency budget <= 3.0 s」（`status: ratified`，Human/Ops 2026-07-16）。生產義務 `ODP-NFR-PERF-001-PROD` 也指定量 `run.googleapis.com/request_latencies` p95 ≤ 3.0 s（`docs/evidence/completion/ODP-RELEASE-ACCEPTANCE-CARRYFORWARD-001/obligation_matrix.json`） |
| 5xx 比例（API、Web） | > 5%，持續 5 分鐘 | `docs/evidence/runtime/ODP-PROD-OPS-WATCH-PARAMS-001/README.md` §2：watch window 的 SLO 是「錯誤率 < 5%」；rollback 觸發條件的時間窗是「持續 5 分鐘」 |
| uptime 失敗 | 超過一個 checker region 失敗，持續 180 秒 | 同上 README §2：「Health check 連續失敗 3 次」，check 每 60 秒一次 |
| Cloud Run job 失敗 | 5 分鐘內失敗的 execution 數 > 0 | 同上 README §2 的 rollback 觸發條件「job failure」 |
| Cloud SQL CPU | > 80%，持續 10 分鐘 | **工程預設值，沒有已核准的來源**，上線後依實測流量調整 |
| Cloud SQL disk | > 85%，持續 10 分鐘 | **工程預設值**。instance 已開啟 disk autoresize，這條是提早預警 |
| Cloud SQL 連線數 | `num_backends` 總和 > `floor(0.8 × monitoring_cloud_sql_max_connections)`，持續 5 分鐘 | **工程預設比例**。`max_connections` 沒有以 database flag 固定，實際值隨 tier 而定，所以由人提供 |

### p95 門檻衝突的處理

`monitoring-oncall-plan.md` §2.1 指出三個互相矛盾的 p95 值：

- **800 ms**（`infra/monitoring/slo.json`）：該檔沒有經過核准，也從未被渲染成雲端資源。
- **< 5 秒**（PROD-OPS-05 watch 參數）：README 自己標明是「寬鬆初始值」，只用於人工 rollback 判斷。
- **≤ 3.0 秒**（NFR）：唯一經 Human/Ops ratify 的門檻，生產觀察義務也以它為準。

所以本 task 採用 3.0 秒，沒有把門檻做成必填變數。

範圍限制：ratify 文件 §2 的範圍是「API + queue + DB path」，**沒有**涵蓋 Web 或瀏覽器的延遲。因此只有 API 有 latency 告警；Web 只有 5xx 與 uptime 告警。

## 3. 設計限制（如實記錄）

1. **production 實際部署不經過這個 root stack。** Runtime Release 用 `product_ops/deployment/deploy_cloud_run_waji.sh` 以 gcloud 部署（`docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md` §6）。影響如下：
   - 本 task 的 5xx 與 latency policy 依 Terraform 資源名稱篩選 service（例如 `oday-prod-api`）。
   - gcloud 部署的 service 名稱來自 `vars.ODP_CLOUD_RUN_API_SERVICE` 與 `vars.ODP_CLOUD_RUN_WEB_SERVICE`。
   - 兩者不一致時，這些 policy 看不到 production 流量。
   - 本 task 沒有另開一套名稱覆寫機制。
   - 另外，`terraform_data.production_contract` 的模型 precondition 目前會擋下 core-ops 的 prod plan（readiness pack 的後續項 F2），所以這些資源在 F2 解決前無法以 prod tfvars 套用。
2. **API uptime check 的可達性。**
   - Terraform 把 API 設為 `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER`，外部 uptime checker 打不到 run.app host。因此 API uptime check 要等人提供一個 checker 能到達的 host 才會建立，prod 則強制要求提供。
   - gcloud 部署的 API 沒有指定 `--ingress`，實際 ingress 要以部署結果為準。
   - 如果走 gcloud 部署的 service，Monitoring service agent 的 `roles/run.invoker` 也要在那個 service 上授予。Terraform 只會授予在自己管理的 API 上。
3. **Cloud Run job 以整個專案為範圍。**
   - migration、worker、scheduler job 由部署腳本建立，名稱是每個 release 一個的 `<base>-r-<sha12>`，root stack 並沒有建模這些 job。
   - 所以告警比對專案內所有 `cloud_run_job`，不寫死 job 名稱。
   - 這是靠「每個環境一個專案」的前提成立。
4. **Web uptime check 要求 Web 對外公開。** prod 範本中的 `web_invoker_members = ["allUsers"]` 滿足這點。dev 若沒有開放，這個 check 會失敗，告警會送到 dev 設定的 channel；dev 沒有設定 channel 時則只會在 console 中觸發，不會通知任何人。
5. **不在本 task 範圍、仍然缺的項目：**
   - watch window receipt 的產生者；
   - `ODP-NFR-BATCH-002` 的營業日 cutoff（cutoff 未定義，所以沒有做 batch-window 告警，只有 job 失敗告警）；
   - 應用層 `AlertRouter` 的 `ONCALL_ENDPOINT_URL` 與 `ONCALL_PROVIDER_SECRET`；
   - dashboard 與 SLO 資源；
   - `infra/monitoring/alerts.json` 的其餘應用層告警（`audit-write-failure`、`unauthorized-spike`、`dlq-spike` 等），它們需要 log-based 或 custom metric。

## 4. prod fail-closed 規則

`environment = "prod"` 時，`terraform_data.production_contract` 的 precondition 要求以下三項都成立，否則在任何資源建立前就讓 plan 失敗：

- `monitoring_notification_emails` 與 `monitoring_notification_channel_ids` 合計至少一個；
- `monitoring_api_uptime_host` 非空；
- `monitoring_cloud_sql_max_connections` 非 null。

dev 和 staging 的 channel 可以留空：policy 照樣建立且 plan 有效，只是不會通知任何人。

## 5. 仍需人工提供

| 項目 | 對應變數或位置 | 說明 |
|---|---|---|
| 告警通知目標 | `monitoring_notification_emails`，或 `monitoring_notification_channel_ids`（例如 PagerDuty、Slack 等 channel，在 Terraform 以外建立） | 不得寫入 repo，放在受管的 prod tfvars |
| on-call 名單 | `monitoring-oncall-plan.md` §4 的值班表（Watch owner、Rollback owner、Incident Commander、`ops-lead`、`oncall-engineer` 等） | 姓名、聯絡管道、代理人都還是空白 |
| API uptime host | `monitoring_api_uptime_host` | 必須是外部 checker 能到達的 host，並決定 API ingress 或 load balancer 的路徑（§3-2） |
| Cloud SQL `max_connections` | `monitoring_cloud_sql_max_connections` | 以 production instance 的實際值為準 |
| service 名稱一致性 | gcloud 部署的 `ODP_CLOUD_RUN_API_SERVICE` 與 `ODP_CLOUD_RUN_WEB_SERVICE` | 要和 Terraform 的 `oday-<env>-api`／`-web` 一致，或另開 task 處理（§3-1） |
| F2 模型契約 | readiness pack 的 F2 | 解決後才能以 prod tfvars 執行 plan 或 apply |
| 套用與驗證 | `terraform plan`／`apply`，由具權限的 Human/Ops 執行 | 套用後要確認：uptime check 為綠燈、測試告警送達、gate-6 receipt 已記錄 |
| Cloud SQL 門檻校準 | `monitoring.tf` 的 locals | CPU、disk、連線比例都是工程預設值 |

## 6. 驗證紀錄（離線）

- 用 releases.hashicorp.com 下載 Terraform 1.9.8 與 provider（google／google-beta 5.45.2、random 3.9.0），放在 scratchpad 的 filesystem mirror。
  - 原因：`registry.terraform.io` 被 sandbox proxy 擋下。
  - 版本與 `.terraform.lock.hcl` 相同，`init` 時以 lock 檔的 hash 驗證。
- `terraform init -backend=false` 與 `terraform validate` 都在副本中執行，沒有連線任何 GCP 專案。
- 用 `mock_provider` 寫了一份臨時的 `terraform test`，**沒有 commit**，以 plan 驗證以下五個情境：
  1. dev 不提供監控輸入時，plan 有效；
  2. 監控輸入會正確接線（web host、兩條 uptime 告警、channel、320 的連線門檻、service agent 的 member）；
  3. 不合法的 email 會被拒絕；
  4. prod 缺監控輸入時 fail closed；
  5. prod 補齊監控輸入後，plan 通過。

各命令與結束碼見 commit 訊息與 task 最終報告。

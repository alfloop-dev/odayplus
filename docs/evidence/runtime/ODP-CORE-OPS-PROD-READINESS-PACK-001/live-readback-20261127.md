# live 讀回紀錄（2026-11-27）與負責人決定（DRAFT · 不是簽署）

> **這份文件只轉錄，不是本 task 的查證結果。**
>
> - §1 的 live 事實，是 Claude 在 2026-11-27 另一個對話 session 中，以 `deborah.lu` 帳號**唯讀**讀回 GCP 與 GitHub 後，寫進 task `ODP-CORE-OPS-PROD-GO-PACK-REFRESH-261127` 的 brief。
> - 本 task（auto worker）**沒有**呼叫 GCP 或 GitHub API，也沒有重新查證任何一項。本檔只把 brief 的內容逐項轉錄，並註明讀取方式。
> - brief 只寫了讀到什麼，**沒有附上**確切的命令與原始輸出。下表「讀取方式」欄只寫讀取的對象與類型；需要原始輸出時，請由負責人或 Claude 重新讀回並另存收據。
> - 本包其他文件凡是提到 GCP 或 GitHub 的現況，一律引用本檔的項目編號（例如 `L-2.3`），不另外宣稱查證過。

## 1. live 事實（2026-11-27，由 Claude 以 `deborah.lu` 唯讀讀回）

### L-1 billing

| # | 事實 | 讀取方式 |
|---|---|---|
| L-1.1 | `odayplus-runtime-20260825`（staging）與 `odayplus-prod-20260826`（production）的 billing 在 2026-11-26 07:18Z 停用。原因是 billing 帳戶 016498 關閉 | Claude 唯讀讀回兩個專案的 billing 狀態 |
| L-1.2 | 2026-11-27 05:3xZ 改綁 billing 帳戶 `016A0C-BB2832-369D8E` 後恢復 | 同上 |
| L-1.3 | Cloud SQL 在 2026-11-27 06:04Z 全部回到 `RUNNABLE` | Claude 唯讀讀回 Cloud SQL 執行個體狀態 |

### L-2 production 基礎設施（`odayplus-prod-20260826`）

| # | 事實 | 讀取方式 |
|---|---|---|
| L-2.1 | VPC `oday-prod-runtime` 存在，建立時間 2026-11-25 06:48Z | Claude 唯讀讀回 VPC network |
| L-2.2 | KMS keyring `oday-prod-runtime` 存在 | Claude 唯讀讀回 KMS keyring |
| L-2.3 | Cloud SQL `oday-prod-foundation-sql` 存在：`REGIONAL`、沒有 public IPv4、CMEK 使用 keyring `oday-prod-runtime` | Claude 唯讀讀回 Cloud SQL 執行個體設定 |
| L-2.4 | 舊的 `oday-prod-sql`（`ZONAL`）仍存在。依負責人決定不動它（見 §2 H） | 同上 |
| L-2.5 | bucket `oday-prod-audit-worm-odayplus-prod-20260826` 的 retention 是 `220924800` 秒（7 年），而且**已鎖定** | Claude 唯讀讀回 bucket retention policy |
| L-2.6 | global LB `oday-prod-web-lb`，IP `34.110.254.149` | Claude 唯讀讀回 load balancer 與位址 |
| L-2.7 | Google 受管憑證 `ACTIVE`，涵蓋 `console.oday-plus.com.tw` 與 `api.oday-plus.com.tw` | Claude 唯讀讀回 SSL certificate |
| L-2.8 | production Cloud Run 目前只有 `oday-prod-mlflow`。API 與 Web（`oday-prod-api`、`oday-prod-web`）由部署流程建立，目前還不存在 | Claude 唯讀讀回 Cloud Run service 清單 |

### L-3 production 監控

| # | 事實 | 讀取方式 |
|---|---|---|
| L-3.1 | email notification channel「ODay Plus prod alert email」存在 | Claude 唯讀讀回 Cloud Monitoring notification channel |
| L-3.2 | 6 條**啟用中**的告警政策：web uptime、Cloud SQL saturation、api 5xx > 5%、api p95 > 3000 ms、web 5xx > 5%、Cloud Run job failed | Claude 唯讀讀回 Cloud Monitoring alert policy |
| L-3.3 | uptime check「oday-prod web /login」存在 | Claude 唯讀讀回 uptime check config |

### L-4 GitHub environment

| # | 事實 | 讀取方式 |
|---|---|---|
| L-4.1 | `production` environment 已設定：`ODP_CLOUD_RUN_VPC_EGRESS=all-traffic`、`ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT`、`ODP_RELEASE_LEASE_PUBLIC_KEY`、`ODP_RELEASE_LEASE_STATE_URI=gs://odayplus-prod-20260826-release-leases/leases`、`ODP_PROD_DEPLOY_URL=https://console.oday-plus.com.tw`、`ODP_PROD_API_URL=https://api.oday-plus.com.tw`、`ODP_CLOUD_RUN_API_SERVICE=oday-prod-api`、`ODP_CLOUD_RUN_WEB_SERVICE=oday-prod-web`、`ODP_CLOUD_RUN_MIN_INSTANCES=2`、`ODP_AUTH_AUDIENCES` | Claude 唯讀讀回 GitHub environment variables |
| L-4.2 | `staging` environment 已設定 `ODP_PRODUCTION_RELEASE_LEASE_STATE_URI` | 同上 |
| L-4.3 | `staging` 與 `production` **都沒有**設定 `ODP_PRODUCTION_WATCH_CLOSEOUT_URI`。`staging_closeout` 需要它，**待設定** | 同上 |
| L-4.4 | `ODP_PROD_API_INVOKER_GROUP` 沒有設定（選填） | 同上 |
| L-4.5 | GitHub `staging` 與 `production` environment 的 required reviewers 是 `Alien-alfaloop`、`ajoe734` | Claude 唯讀讀回 GitHub environment protection rules |

注意：GitHub environment variable 的讀取有分頁限制，預設只回 30 筆。brief 沒有說明讀取時是否已處理分頁。L-4.3、L-4.4 的「未設定」若要當作 GO 前的依據，建議重讀一次並確認已讀完全部頁面。

### L-5 鏈 U（candidate `8e5020a29`）

| # | 事實 | 讀取方式 |
|---|---|---|
| L-5.1 | `8e5020a29` 的 staging deploy 成功：Runtime Release run `45555693188`，2026-11-25 | Claude 唯讀讀回 GitHub Actions run |
| L-5.2 | 但 dev 之後又合併了含程式碼的 #1763、#1769、#1770。ancestry 規則只允許 evidence-only descendant，所以 `8e5020a29` 無法上 production，**不採用** | Claude 依 dev 歷史判斷；ancestry 規則見 `delivery_toolchain/e2e/check_release_gate_registry.py:708`、`:731` |
| L-5.3 | staging release `odp-8e5020a29bad` 的 TTL 已在 2026-11-26 05:48Z 到期 | Claude 唯讀讀回 staging release 標籤 |

repo 內的 registry 仍綁定 `8e5020a29`（`docs/evidence/gates/RELEASE_GATE_REGISTRY.json:15-31`）。run `45555693188` 沒有收進 repo 的 evidence 目錄。

### L-6 watch receipt

| # | 事實 | 讀取方式 |
|---|---|---|
| L-6.1 | `shared/observability/watch_window.py` 要求 receipt 帶 `provider_signature`，並以 `MONITORING_PROVIDER_SECRET` 驗證（`:357-400`、`:825-844`）。這個 provider 簽章不存在，所以以真實監控資料**不可能**產生通過驗證的 receipt | Claude 閱讀程式。本 task 也以 `origin/dev` 核對過行號 |
| L-6.2 | 修正 task 是 `ODP-PROD-WATCH-RECEIPT-REAL-METRICS-261127`，**尚未合併**。本包一律標為 **pending** | Claude 依 task 狀態記錄 |

## 2. 負責人決定（對話，2026-11-27）

負責人蔡尚志在 2026-11-27 與 Claude 的對話中做了以下決定。本包記為「**負責人決定（對話，2026-11-27）**」。

**這些是方向決定，不是 gate 簽署。** gate-3、gate-5、gate-6、release decision、lease 與 GitHub environment 核准，都要在選定 candidate 之後另行具名；本包所有簽名欄都保持空白。

| 代號 | 決定 | 本包落在哪裡 |
|---|---|---|
| A | **candidate**：先在不凍結的情況下把文件備妥，再凍結 `ready_dispatcher`，以當時的 dev tip 當 candidate。staging 通過後直接接 production，完成後再解凍 | `deployment-plan.md` §1；`human-decisions-checklist.md` HD-05 |
| B | **UAT（HD-13）**：由負責人本人在 ephemeral staging 操作並簽核。帳號由 Claude 以該 release 的 bootstrap `platform_admin`，經既有的邀請 API 建立。staging 自 PR #1737 起，每個 release 都有自己的 bootstrap admin 與密碼 secret（見 `product_ops/deployment/staging_lifecycle.py`）。密碼不寫入 repo。PriceOps、AdLift、Growth、Franchisee 是否納入**仍待決** | `uat-plan.md` §2、§3 |
| C | **告警與值班（HD-22）**：告警以 email 寄給負責人，負責人擔任 on-call。自動 watch window 依 `ODP-PROD-OPS-WATCH-PARAMS-001` 的 30 分鐘；另外由 Claude 在上線後 24 小時內，每小時讀回一次健康狀態 | `monitoring-oncall-plan.md` |
| D | **首次 release 已 commit 後的下線（HD-21 第 1 點）**：採選項 (a)，撤銷 Web 的 `allUsers` invoker，保留資料與服務。修正後以下一個 release 前滾，再重新開放。這個程序要寫成 runbook 步驟（含撤銷、恢復的 gcloud 命令與讀回驗證），並在 staging 演練 | `rollback-plan.md` §2.4、§5；`runbook-core-ops.md` §6 |
| E | **DR（HD-20）**：GO 前先做 production-representative DR 演練：對 `oday-prod-foundation-sql` 建備份，還原到臨時執行個體，量 RPO／RTO（目標 RPO ≤ 60 分、RTO ≤ 240 分），留收據後刪除臨時執行個體。**建立與刪除之前，Claude 會再向負責人確認** | `backup-dr-impact.md` §6 |
| F | **HD-14**：`ODP-NFR-BATCH-002` 的營業日 cutoff，對 core-ops 記為 not-applicable（core-ops 沒有每日 batch）。上模型時再定 | `human-decisions-checklist.md` HD-14 |
| G | **gate-3**：2026-11-20 已決定 core-ops 記為不適用／passed-with-deviation。**簽署**仍在 candidate 選定之後 | `gate-evidence-map.md` gate-3；HD-15 |
| H | **2026-11-20 的決定**：production Cloud Run 由部署流程建立，名稱 `oday-prod-*`；Cloud SQL 照規格新建，舊的 `oday-prod-sql` 不動；audit WORM 鎖 7 年；API／Web min instances 各 2；網域 `console.oday-plus.com.tw`／`api.oday-plus.com.tw` 走固定 HTTPS LB | `deployment-plan.md` §4；`backup-dr-impact.md` §3 |

決定 H 在 repo 程式中的落點（本 task 以 `origin/dev` @ `48c5271b5` 核對）：

- production 的 API／Web 由部署流程擁有：`infra/terraform/cloud_run.tf:1-7`；`infra/terraform/env/prod.tfvars.example:4-9`、`:20-24`。
- 新的 Cloud SQL `oday-prod-foundation-sql`，舊的不納管：`infra/terraform/env/prod.tfvars.example:26-31`；`infra/terraform/checks.tf:70-76`。
- min instances ≥ 2：`product_ops/deployment/deploy_cloud_run_waji.sh:44-68`。
- 固定 HTTPS LB：`infra/terraform/load_balancer.tf:1-10`；`infra/terraform/env/prod.tfvars.example:38-43`。

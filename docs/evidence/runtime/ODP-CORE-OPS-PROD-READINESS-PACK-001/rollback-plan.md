# core-ops production 回滾計畫（DRAFT · 未簽署 · 2026-11-27 版）

- 行號以 `origin/dev` @ `48c5271b5d882d824d432f3483ab4415030bb8e6` 為準。
- GCP 與 GitHub 的現況引用 `live-readback-20261127.md`（`L-…`）；負責人決定 A–H 見同檔 §2。
- 2026-10-24 版標為「待合併」的 PR #1565，已在 2026-10-24 合併（merge commit `9a346570a`）。本版改以 `origin/dev` 上的 `docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md` §5 為準。

## 1. 總覽：哪些自動、哪些手動

| 情境 | 機制 | 自動或手動 | 依據 |
|---|---|---|---|
| 部署腳本在 live E2E gate 通過**之前**失敗（migration、smoke、切換或 live gate） | `deploy_cloud_run_waji.sh` 的 EXIT trap `handle_deployment_exit`：依 deploy 前的 snapshot 判斷 recovery 模式，回復 API／Web 流量與 scheduler trigger；首次 release 則刪除 candidate | **自動** | `deploy_cloud_run_waji.sh:498-559`；`cloud_run_release_traffic.sh:320-443` |
| promotion、切換前狀態擷取或 migration 前備份失敗 | job 停在任何 Cloud Run 變更之前，沒有東西需要回復 | **自動**（fail closed） | `deploy-dev.yml:1989-2175` |
| live E2E gate 通過之後（`DEPLOYMENT_COMMITTED=true`），watch window 或之後發現異常：**後續 release** | 人工判定後，以 workflow 上傳的切換前狀態執行 `bluegreen_release.py rollback` | **手動** | §3；`deploy_cloud_run_waji.sh:1533` |
| 同上：**首次 release**（沒有 blue） | 決定 D：撤銷 Web 的 `allUsers` invoker，保留資料與服務，以下一個 release 前滾 | **手動** | §2.4 |
| staging 演練失敗 | `staging_lifecycle.py hold` 保留 release-scoped 資源 24 小時；之後以 `phase=cleanup` 清理 | 自動 hold；cleanup 由人工 dispatch | `deploy-dev.yml:2352-2370`、`:2752-3399` |
| 資料庫 schema | migration 在切換前就執行，任何回滾都**不會**回復 schema；規劃明文禁止緊急回滾時做 destructive down migration | 不回復。資料回復點是 migration 前備份 | `deploy_cloud_run_waji.sh:1055-1118`；`deploy-dev.yml:2086-2175`；`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:329` |

**gate-6 的「proven」：** 以上任何一條路徑都還沒有在 production 執行過的 receipt。staging 上鏈 U 曾成功部署一次（`L-5.1`），但那次的演練 receipt 沒有收進 repo，而且 candidate 不採用。見 §5。

## 2. 首次 production release（production 從未有過 release）

依 `L-2.8`，production 目前只有 `oday-prod-mlflow`，`oday-prod-api` 與 `oday-prod-web` 都不存在。所以 core-ops 的第一次 production 部署就是首次 release。

### 2.1 規則（已合併，PR #1565）

依 `docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md` §5：

- **採方案 A：擴充 `initial_release_recovery`**（design `:144-150`）。可封存首次 release 的建置目標只有 dev 與 staging（`delivery_toolchain/release/release_manifest.py:357`）。staging 封存的紀錄可以 admission 到 staging 與 production（design `:157`）。
- **各環境的 recovery 語意**（design `:164`）：
  - **production 與 dev**：`delete-candidate-zero-traffic`，刪除本次建立的 service、job 與 scheduler trigger，維持零流量。
  - **ephemeral staging**：`forensic-hold-release-scoped-namespace`。失敗時只 `hold`，不刪 candidate、不動流量，等之後 cleanup。
- **manifest 的 `rollback_release` 只是宣告。** runtime 回復一律以 target 當下的狀態為準（design `:135`）。
- **lineage 只能用一次。** 只要 production 的 lease store 曾為**其他** manifest 消耗過 lease，首次 release 分支就關閉（design `:160`）。staging admission 必須跨環境讀 production store，讀不到就拒絕。
- **後續項：**
  - **F1-a**（design `:201`、`:223`）：首次 production release 在 lease 消耗後失敗，而下一個候選是不同 manifest 時，lineage 會拒絕它，它也沒有可綁的 rollback 目標。這是刻意的 fail closed，解除方式需要具名決策（HD-03）。
  - **F1-b**（design `:198`、`:224`）：staging environment 要設 `ODP_PRODUCTION_RELEASE_LEASE_STATE_URI`，並讓 staging admission 身分能唯讀 production lease bucket。變數**已設**（`L-4.2`）；IAM **未讀回**（HD-04）。

### 2.2 commit 之前失敗：自動 initial-release-cleanup

- deploy 前捕捉 API／Web snapshot。兩者都不存在時，`release_recovery_mode` 回傳 `initial-release-cleanup`（`cloud_run_release_traffic.sh:378-397`）。
- `restore_service_traffic` 刪除本次建立的 service，並讀回確認已刪（`cloud_run_release_traffic.sh:320-350`）。
- `cleanup_initial_release_candidates` 刪除 migration、worker、scheduler candidate job（`cloud_run_release_traffic.sh:399-431`）。
- scheduler trigger 還原成 snapshot 的狀態；部署前不存在的就刪除（`deploy_cloud_run_waji.sh:536-547`）。
- log 會寫明「沒有前一版可回滾；recovery 是刪除 candidate 並維持零流量」（`deploy_cloud_run_waji.sh:512-515`）。
- **不會回復的部分：** migration 已經對 `oday-prod-foundation-sql` 執行，schema 不會回復。資料回復點是步驟 12 的 migration 前備份（`deployment-plan.md` §2）。

### 2.3 首次 release 不可以用 `bluegreen_release.py rollback`

- 首次 release 的切換前狀態把兩個 service 記為 absent（`deploy-dev.yml:2034-2083`；`bluegreen_release.py:331-369`）。
- `rollback` 會對每個 service 呼叫 `restore_service_traffic`（`bluegreen_release.py:718-744`、`:469-475`）。snapshot 是 absent 時，這個函式會**刪除** service（`cloud_run_release_traffic.sh:324-350`）。
- 結果等同 2026-10-24 版的選項 (b)，而且之後會碰到 F1-a。**這不是決定 D 的程序。**

### 2.4 首次 release 已 commit 之後的下線（負責人決定 D，2026-11-27）

**決定：** 採選項 (a)。撤銷 Web 的 `allUsers` invoker，讓服務不再對外公開；**保留**資料、API、Web、job 與 scheduler；修正後以下一個 release 前滾，再重新開放。

為什麼這樣可行：首次 release 已 commit，表示它的 lease 已在 production 消耗，而且 release 確實存在於 production。下一個 release 依規則綁定 `rollback_release`（design `:160`），指向這個已上線的 release；不會碰到 F1-a（F1-a 只在首次 release 失敗、被清除之後發生）。

#### 2.4.1 何時觸發

依 `ODP-PROD-OPS-WATCH-PARAMS-001`（`docs/evidence/runtime/ODP-PROD-OPS-WATCH-PARAMS-001/README.md:24`；`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:308-317`），以下任一條件成立：

| 觸發條件（PROD-OPS-05，寬鬆初始值） | 對應的 production 告警（`L-3.2`；門檻在 `infra/terraform/monitoring.tf`） |
|---|---|
| 錯誤率 > 10% 持續 5 分鐘 | 「web 5xx > 5%」與「api 5xx > 5%」會先觸發（門檻 5%、持續 300 秒，`monitoring.tf:15-19`）。收到告警後要看實際比率，> 10% 且持續 5 分鐘才達回滾門檻 |
| p95 > 10 秒持續 5 分鐘 | 「api p95 > 3000 ms」會先觸發（`monitoring.tf:9-13`）。同樣要看實際值 |
| health check 連續失敗 3 次 | web uptime 告警：每 60 秒檢查、持續 180 秒（`monitoring.tf:20-21`） |
| auth failure、資料品質異常、job failure、queue lag、audit 缺失，或 operator 判定異常 | 「Cloud Run job failed」告警；其他項目沒有對應告警，靠人工判斷與 Claude 的每小時讀回（決定 C） |

另外，以下 P0 情境不必等門檻，應立即撤銷：跨租戶資料、`platform_admin` 讀到業務資料、模型功能回傳替代預測、部署的 SHA 或 profile 不符（`runbook-core-ops.md` §4）。

watch window 之後仍適用同一程序，直到有下一個 release 為止。

#### 2.4.2 誰判定、誰執行

| 角色 | 誰 | 依據 |
|---|---|---|
| 判定是否撤銷 | 負責人（on-call） | 決定 C |
| 執行撤銷與恢復 | **提議（待負責人確認）**：負責人本人；或負責人在該次事件中明確指示後，由 Claude 執行。Claude 不會自行判定撤銷 | 決定 C、D |
| 執行身分 | 需要對 `oday-prod-web` 有 `run.services.getIamPolicy` 與 `run.services.setIamPolicy` 的身分。`deborah.lu` 是唯讀讀回用的身分，不能用來撤銷。實際使用哪個身分**仍待決** | — |
| 事件紀錄 | 執行者在 incident 中留下 §2.4.5 的收據 | — |

#### 2.4.3 撤銷程序

以下 `<REGION>` 是 production 的 `GCP_REGION`（範本是 `asia-east1`，`infra/terraform/env/prod.tfvars.example:12`）。

```bash
PROJECT=odayplus-prod-20260826
REGION=<REGION>
WEB=oday-prod-web

# 0. 撤銷前讀回（存成收據）
gcloud run services get-iam-policy "$WEB" --project="$PROJECT" --region="$REGION" --format=json \
  > web-iam-before.json
gcloud run services describe "$WEB" --project="$PROJECT" --region="$REGION" \
  --format='value(status.traffic)' > web-traffic-before.txt

# 1. 撤銷 allUsers 的 roles/run.invoker
gcloud run services remove-iam-policy-binding "$WEB" \
  --project="$PROJECT" --region="$REGION" \
  --member=allUsers --role=roles/run.invoker

# 2. 讀回驗證
gcloud run services get-iam-policy "$WEB" --project="$PROJECT" --region="$REGION" --format=json \
  > web-iam-after.json
#    web-iam-after.json 不得再有 allUsers。其他成員（例如 smoke 身分）維持不變。
curl -sS -o /dev/null -w '%{http_code}\n' https://console.oday-plus.com.tw/login
#    預期 401 或 403。IAM 生效有延遲，請間隔 5 秒重試，最多 2 分鐘
#    （與部署腳本等待公開生效的方式相同：cloud_run_release_traffic.sh:234-253）。
```

- Web 的 ingress 是 `internal-and-cloud-load-balancing`（`deploy_cloud_run_waji.sh:271`、`:1320`），所以只需要從 `console.oday-plus.com.tw` 驗證。
- 撤銷之後，「web uptime」告警會在約 3 分鐘後觸發（`L-3.2`、`L-3.3`）。這是**預期**的，也可以當作撤銷生效的第二個證據。
- API 本來就不公開（`deploy_cloud_run_waji.sh:1148`），不需要動。worker 與 scheduler 會繼續跑；決定 D 沒有包含暫停排程，若要暫停需要另外決定。

#### 2.4.4 恢復程序

**主要路徑：以下一個 release 前滾（決定 D）。**

- Web service 已存在時，部署腳本以 `--allow-unauthenticated` 部署 Web candidate（`deploy_cloud_run_waji.sh:1302-1306`、`:1323`），這會把 `allUsers` 加回來。
- **注意時間差**：`allUsers` 在 Web candidate **部署**時就加回，這時流量仍在舊的、有問題的 revision 上，要到切換（`deploy_cloud_run_waji.sh:1414`）才換成新版。這段期間舊版是公開的。
- **前滾失敗時**：若前滾 release 在 commit 前失敗，EXIT trap 只回復流量，不回復 IAM（`deploy_cloud_run_waji.sh:498-559`）。Web 會維持公開，而且跑在舊的 revision 上。**on-call 必須立刻再執行一次 §2.4.3。**
- 前滾 release 成功後，讀回確認：

```bash
gcloud run services get-iam-policy "$WEB" --project="$PROJECT" --region="$REGION" --format=json \
  > web-iam-restored.json            # 必須含 allUsers / roles/run.invoker
curl -sS -o /dev/null -w '%{http_code}\n' https://console.oday-plus.com.tw/login   # 預期 200
```

再以 `runbook-core-ops.md` §2 讀 `/platform/release-identity`，確認 `release_sha` 是前滾 release 的 SHA。

**例外路徑：不經新 release 直接重開。** 只有負責人判定原因已排除、舊 release 本身沒有問題時才使用（例如外部依賴暫時故障）：

```bash
gcloud run services add-iam-policy-binding "$WEB" \
  --project="$PROJECT" --region="$REGION" \
  --member=allUsers --role=roles/run.invoker
```

讀回方式同上。

#### 2.4.5 每次執行要留的收據

| 欄位 | 內容 |
|---|---|
| 觸發條件 | 哪一條（§2.4.1），附告警名稱或讀回內容與時間 |
| 判定者與執行者 | 姓名或帳號；執行身分 |
| 撤銷前 | `web-iam-before.json`、`web-traffic-before.txt` |
| 撤銷時間 | UTC，命令開始與結束 |
| 撤銷後讀回 | `web-iam-after.json`（沒有 `allUsers`）；`curl` 的 HTTP 狀態與時間 |
| 恢復 | 前滾 release 的 SHA 與 run ID，或例外路徑的核准；`web-iam-restored.json`；`curl` 結果；release identity 讀回 |
| 公開時間差 | 前滾時 Web 重新公開到切換完成的時間（從 workflow log 讀） |

## 3. 後續 release（production 已有前一版）

- **build 時綁定前一版。** 新 release 以 `rollback_release` 綁定前一個已核准的 manifest（`deploy-dev.yml:140-144`）。首次 release 分支因 lineage 規則關閉，所以第二個 release 起一律走這條路（design `:160`）。
- **commit 前失敗：自動回復。**
  - `release_recovery_mode` 回傳 `rollback`（`cloud_run_release_traffic.sh:389-392`）。
  - `restore_service_traffic` 依 snapshot 把流量還原成原本的 revision 配置，並讀回驗證（`cloud_run_release_traffic.sh:351-360`）。
  - 兩個 scheduler trigger 還原成 snapshot 的設定（`deploy_cloud_run_waji.sh:536-547`）。
- **commit 後異常：手動。** 依 `EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:306-329`：
  - 觸發條件同 §2.4.1，只是人工判斷依據。
  - 判定者是負責人（on-call，決定 C）。
  - 順序：暫停 scheduler → Web 與 API 切回 blue → trigger 依 snapshot 還原 → 驗證舊版能讀新 schema → rollback smoke → 保留證據並開 incident（`:319-326`）。
- **`--state-file` 的來源（2026-10-24 版的缺口，已修）。**
  - workflow 在任何 Cloud Run 變更**之前**擷取切換前狀態，並上傳 artifact `production-bluegreen-prerelease-state-<sha>-<attempt>`，保存 90 天（`deploy-dev.yml:2034-2083`）。
  - `release-state.json` 裡的 snapshot 路徑是相對路徑，下載到別處也能用（`deploy-dev.yml:2073`；`docs/evidence/runtime/ODP-PROD-DEPLOY-SAFETY-001/README.md` §2.1）。
- **rollback 命令。** 先下載上一段的 artifact 到 `<STATE_DIR>`。`--dry-run`、`--project`、`--region` 是主 parser 的全域參數，必須放在子命令**之前**（`bluegreen_release.py:757-759`、`:792-796`）：

```bash
# 先 dry run
python3 product_ops/deployment/bluegreen_release.py --dry-run \
  --project odayplus-prod-20260826 --region <REGION> \
  rollback --api-service oday-prod-api --web-service oday-prod-web \
  --state-file <STATE_DIR>/release-state.json

# 確認後執行（去掉 --dry-run）
python3 product_ops/deployment/bluegreen_release.py \
  --project odayplus-prod-20260826 --region <REGION> \
  rollback --api-service oday-prod-api --web-service oday-prod-web \
  --state-file <STATE_DIR>/release-state.json
```

  `rollback` 會先暫停 scheduler，再依 snapshot 還原 Web、API 與兩個 trigger（`bluegreen_release.py:718-744`）。這條命令**從未在 staging 或 production 演練過**。

## 4. 資料與 schema

- migration 是 expand-only：規劃要求不在緊急回滾中執行 destructive down migration，contract migration 延後到相容觀察期後的獨立任務（`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:50`、`:328`）。
- 部署腳本在切換前跑 `compatibility-smoke`，以舊 revision 驗新 schema；全新 target 跑 `bootstrap-compatibility`（`deploy_cloud_run_waji.sh:1084-1117`）。
- 要回到某個資料時間點，只能走 Cloud SQL 備份還原或 PITR。每次 production 部署前都有一份 on-demand 備份（`deploy-dev.yml:2086-2175`）。還原程序與演練見 `backup-dr-impact.md` §6（決定 E）。

## 5. 演練：做過什麼、要做什麼

| 項目 | 現況 | 依據 |
|---|---|---|
| staging `rollback_rehearsal` stage | 程式存在，是 11 個 stage 中的第 8 個 | `staging_lifecycle.py:2868-2886` |
| 這個 stage 演練什麼 | 把 candidate 自己已釘選的 revision 切到同 image 的 probe revision，再還原。驗證的是「流量指標切換與還原」機制，**不是**「回到前一版」 | design `:132` |
| staging 演練是否跑過 | 鏈 U 的 `8e5020a29` staging deploy 成功（`L-5.1`）。這次演練的 receipt 沒有收進 repo；candidate 也不採用 | `L-5` |
| production 回滾、首次 release cleanup | 從未執行。production 還沒有 API／Web service | `L-2.8` |
| 本機 E2E 回滾證明 | build 時執行，對象是 docker compose 加 SQLite，不是雲端 | `deploy-dev.yml:652-656`；`delivery_toolchain/e2e/verify_deployment_health_backup_rollback.py:37-38` |

### 5.1 決定 D 的 staging 演練（必做）

在 candidate C 的 ephemeral staging 存活期間（verify 全部通過之後、TTL 到期或 closeout 之前）執行。staging 專案是 `odayplus-runtime-20260825`；`<STAGING_WEB>` 取 Terraform output `staging_web_service_name`（`infra/terraform/modules/ephemeral_staging/outputs.tf:16`）。staging Web 的 `allUsers` 由 Terraform 管理（`infra/terraform/modules/ephemeral_staging/main.tf:455-461`）。

| # | 步驟 | 收據 |
|---|---|---|
| 1 | 讀回基準：`gcloud run services get-iam-policy <STAGING_WEB> --project=odayplus-runtime-20260825 --region=<REGION> --format=json`；`curl` `https://console-staging.oday-plus.com.tw/login` | `stg-web-iam-before.json`；HTTP 狀態（預期 200） |
| 2 | 撤銷：`gcloud run services remove-iam-policy-binding <STAGING_WEB> --member=allUsers --role=roles/run.invoker …` | 命令、開始與結束時間 |
| 3 | 讀回驗證：IAM policy 沒有 `allUsers`；`curl` 回 401 或 403；量從撤銷到 `curl` 被拒的時間 | `stg-web-iam-after.json`；HTTP 狀態；生效秒數 |
| 4 | 確認 API、worker、資料不受影響：以 `runbook-core-ops.md` §2 讀 staging 的 `/platform/release-identity`（走 API 的直接路徑） | 讀回內容 |
| 5 | 恢復（例外路徑的命令）：`gcloud run services add-iam-policy-binding <STAGING_WEB> --member=allUsers --role=roles/run.invoker …` | 命令與時間 |
| 6 | 讀回驗證：IAM policy 有 `allUsers`；`curl` 回 200；以 staging 的 `staging-admin` 登入一次 | `stg-web-iam-restored.json`；HTTP 狀態 |
| 7 | 確認 staging cleanup 前 IAM 已和 Terraform 一致（第 6 步之後即一致），避免 destroy 時與實際狀態不符 | 讀回內容 |

**staging 演練無法涵蓋的部分：**

- 「以下一個 release 前滾」的重新開放。staging 每個 release 都是新的 namespace（`staging_lifecycle.py:68`），沒有「既有 Web service」可以前滾，所以 §2.4.4 主要路徑的時間差與失敗情境，只能在 production 第一次前滾時觀察。
- production 的告警。staging 沒有 production 的 uptime check。

### 5.2 其他建議的演練（DRAFT，範圍需負責人核准）

1. 在 staging 以 core-ops candidate C 跑完 11 個 stage，取得 `rollback_rehearsal` 的實際 receipt，並收進 repo（evidence-only）。
2. 下載 production 第一次部署的 `production-bluegreen-prerelease-state-*` artifact，確認內容可被 `bluegreen_release.py --dry-run … rollback` 讀取（不實際執行）。首次 release 時兩個 service 都是 absent，dry run 的輸出應顯示會刪除 service，這正好用來確認 §2.3 的警告。

## 6. 簽核（留白）

| 項目 | 簽署人 | 角色 | 日期 | 決定 |
|---|---|---|---|---|
| 本回滾計畫已審閱 | | Release owner | | □ 核准 □ 附條件 □ 退回 |
| 首次 release commit 後的下線程序（§2.4） | 方向已由負責人決定（決定 D，2026-11-27）；程序本身**待簽** | Human/Ops | | □ 核准程序 □ 退回 |
| §2.4.2 的執行者與執行身分 | | Human/Ops | | |
| §5.1 staging 演練結果 | | Human/Ops | | □ 通過 □ 未通過 |
| rollback owner | 方向：負責人（決定 C）；具名**待簽** | Human/Ops | | |

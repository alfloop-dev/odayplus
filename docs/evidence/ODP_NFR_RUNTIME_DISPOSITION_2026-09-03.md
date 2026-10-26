# ODP-NFR-RUNTIME-EVIDENCE-001：SHARED-008 與四條 NFR 的執行期處置

- **任務**：`ODP-NFR-RUNTIME-EVIDENCE-001`（owner Claude，reviewer Codex2）
- **基準**：`origin/dev` @ `0d5699401d3c70d2a55f23c12e9e2ee19668a77a`
- **評估時間**：2026-09-29T07:40:55Z（readback 窗口 `07:39:56Z`–`07:40:55Z`，實際時鐘讀值）
- **依據**：[修正計畫](../plans/ODP_REMEDIATION_PLAN_2026-09-03.md)「目前不進入實作的方向」、[待裁決事項](../plans/ODP_OPEN_DECISIONS_2026-09-03.md) 第 20 項、[需求處置政策](../governance/ODP_REQUIREMENT_DISPOSITIONS.md) §2
- **機讀處置**：[`runtime/ODP-NFR-RUNTIME-EVIDENCE-001/disposition.json`](runtime/ODP-NFR-RUNTIME-EVIDENCE-001/disposition.json)
- **原始收據**：[`runtime/ODP-NFR-RUNTIME-EVIDENCE-001/rollout-readback.json`](runtime/ODP-NFR-RUNTIME-EVIDENCE-001/rollout-readback.json)，sha256 `9e13d07c9224544a790fe6d5495f36e8e301b1b92e11b1b6295abf6cbe61f760`，由同目錄 `capture_rollout_readback.py` 產生

## 結論

五項全部維持 **`BLOCKED_BY_EVIDENCE`**。

本 task 的前提是「immutable dev/staging/prod rollout receipt 可用」。依賴 task `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 已 done，但它交付的是 build 與准入證據，本身明寫「blocked, not deployed」。本次對 GitHub 讀回的結果也一致：三個環境的**完整** GitHub deployment 歷史裡，沒有任何一個 Runtime Release run 的 `Deploy the admitted artifact by immutable digest` job 成功。

**讀回範圍**：不是抽樣。collector 以 GraphQL 分頁讀完每個環境的全部 deployment（筆數與 `totalCount` 對帳），每筆 deployment 的全部 status，再對每一筆曾進入 `success` 或 `inactive`（被後一次成功部署取代的舊 success）的 deployment，讀回其 workflow run 的完整 job 清單（含所有 attempt）。**不涵蓋**：不經 GitHub Actions 的部署（例如手動 `gcloud`）不會留下 GitHub deployment 紀錄，這裡看不到；runtime 端唯一的檢查是 `prior_receipts[0]` 的 hosted 缺席 probe。

| 環境 | deployment 總數／曾成功 | 讀回結果 | 收據位置 |
|---|---|---|---|
| dev | 982／639（246 個 run） | 3 個 Runtime Release run（`36250018645`、`36252020646`、`36278150009`，都是 `c8d26f02`）讓 deployment 進入 success，但 deploy job 都是 `failure`（migration 失敗，見 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001/deploy-run-36278150009-migration-failure.md`）。最新 run `36509055237`（candidate `ee06d1d8`）只跑了 build，lease／deploy／watch 都是 `skipped`。其餘 243 個 run 是整改前的舊 workflow `Deploy Dev`（2026-06-26～07-27），部署的不是經准入的 immutable-digest artifact。run `36509055237` 的 hosted probe（2026-09-29T01:41Z 起）讀到五個 dev target 全部 absent | `calls.deployment_history_dev`、`calls.deployed_run_jobs`、`calls.latest_run_jobs`；`prior_receipts[0]` |
| staging | 82／28（28 個 run） | 唯一的 Runtime Release run（`33320822376`，2026-08-30）只跑了 `Verify staging deployer state backend access`，release 相關 job 全部 `skipped`。其餘 27 個 run 是整改前的舊 workflow `Deploy Staging`（2026-06-26～06-28），同樣不是准入 artifact | `calls.deployment_history_staging`、`calls.deployed_run_jobs` |
| production | 0／0 | GitHub 上 0 筆 deployment | `calls.deployment_history_production` |

舊 workflow 的成功部署是揭露事項：它們證明 dev／staging 曾經有東西在跑，但那正是這次整改要取代的 false-done 前提，不能當 immutable rollout receipt。

沒有部署就沒有可量的 runtime window。所以 PERF／BATCH／AVAIL／RPO 沒有量到任何數字，SHARED-008 也不能只憑 repo 或 GitHub 設定就判定通過。以下各項列出「環境恢復後要跑什麼、門檻多少、誰能跑」，不是空白結案。

## 各項處置

| 需求 | 處置 | 環境 | 門檻 | 命令／查詢 | 時間窗 | 證據 owner | 下次檢查 |
|---|---|---|---|---|---|---|---|
| `ODP-FR-SHARED-008` | `BLOCKED_BY_EVIDENCE` | dev、staging、production | dev／staging 的任何 secret version 值 digest 都不等於 production 的；dev／staging 的 Cloud Run 資源都不以 production SA 執行；收據不含任何 secret 值 | 在 WIF job 內 `gcloud secrets versions access … \| sha256sum`（只把 digest 帶出 job）；`gcloud run services/jobs describe` 讀 serviceAccountName 與 secretKeyRef | 各環境 deploy 成功後讀一次，每次 release 重讀 | Human/Ops | `2026-10-06` |
| `ODP-NFR-PERF-001` | `BLOCKED_BY_EVIDENCE` | staging、production | p95 ≤ 3.0 s、0 failure，併發 10／20／50、每輪 150 requests（已批准的門檻）；真實流量 p95 ≤ 3.0 s | 對已部署 `oday-api` 跑負載，保留每筆 latency；Cloud Monitoring `run.googleapis.com/request_latencies` p95 | 同一 release 的負載輪次；有流量後監控至少 24 h | Human/Ops（負載可由 task owner 跑） | `2026-10-06` |
| `ODP-NFR-BATCH-002` | `BLOCKED_BY_EVIDENCE` | production | 每日批次在營運日開始前成功完成。**截止時刻在 repo 裡沒有定義**，要由 Human/Ops 提供後才判得了 | `gcloud run jobs executions list --job=oday-worker-r-<release>`（scheduler 同）取 completionTime 與 succeeded／failed | 同一 release 連續 7 個營運日 | Human/Ops | `2026-10-06` |
| `ODP-NFR-AVAIL-003` | `BLOCKED_BY_EVIDENCE` | production | OpsBoard 月可用率 ≥ 99.5% | Cloud Monitoring uptime check 打 production `oday-web` OpsBoard 路由，取 `uptime_check/check_passed` 比例 | 一個完整日曆月 | Human/Ops | `2026-10-06` |
| `ODP-NFR-RPO-004` | `BLOCKED_BY_EVIDENCE` | staging、production | RPO ≤ 60 min、RTO ≤ 240 min（已批准的門檻）；備份能在 staging 還原並通過核心查詢（`ODP-AC-NFR-007`） | `gcloud sql instances describe` 讀 backupConfiguration／PITR；對已部署 DB 做一次計時還原演練 | 一次計時演練，同時讀備份設定 | Human/Ops | `2026-10-06` |

各項的 `evidence_needed` 與 `reopen_trigger` 在 `disposition.json`。共通的 reopen trigger 是：**對應環境第一次有 Runtime Release run 的 deploy job 結論為 success**。AVAIL 從 production 部署起還要再等一個日曆月才量得到。

門檻來源：需求原文在 `oday_plus_batch_02_sa_documents.zip` 裡（SA-06 第 140 行，SA-08 第 44–47、148 行）；PERF 與 RPO/RTO 的數字依據 [`ODP-PGAP-RELIABILITY-001` 的 SLO／DR 批准文件](../design/ODP-PGAP-RELIABILITY-001_SLO_AND_DR_TARGETS_RATIFICATION.md)。該文件 §4 明說：現有的 `dr_drill_records.json` 是本機 `shutil.copy` 演練，`load_soak_performance_report.json` 是本機的確定性測試，兩者都**不能**當成 runtime 證據。本處置沿用這個界線。

## SHARED-008 已經看得到的部分（設定層，不是 runtime）

收據的 `identity_reference_comparison` 比對三個 GitHub environment 的 10 個識別與 secret 參照變數：project、deployer SA、WIF provider、runtime SA、scheduler SA、四個 secret ref、snapshot bucket。收據只存各值的 sha256 指紋，不存原值。三個環境的 10 個 key 全部有值；任何一個環境缺 key 或讀取失敗，collector 會 exit 1 且不寫收據，verifier 也會拒絕。

- **dev 與 staging 都沒有任何一個參照和 production 相同。**
- dev 與 staging **共用** `GCP_PROJECT_ID`、`GCP_SERVICE_ACCOUNT`、`GCP_WORKLOAD_IDENTITY_PROVIDER`。SHARED-008 原文只要求不共用 Production Secret，所以這裡列為揭露事項，不算違規；但它代表 dev／staging 之間沒有 project 層級的隔離。

這只證明「設定指向不同的東西」。兩個不同名稱的 secret 可能存著相同的值，部署後綁到資源上的實際 SA 也要讀回才算數。所以這一項仍然是 `BLOCKED_BY_EVIDENCE`。

## 本 worker 做不到的事

auto worker 的 gcloud 無法非互動重新認證（`ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 的 README 有紀錄）。本次沒有對 GCP 發出任何請求；所有讀回都來自 GitHub API 與既有的 hosted 收據。

## 驗證

```
python3 docs/evidence/runtime/ODP-NFR-RUNTIME-EVIDENCE-001/verify_nfr_runtime_disposition.py
```

驗證器只讀已 commit 的檔案，不連網。它檢查以下幾件事：

- 收據的 hash 與記錄值一致，且收據不含原始 SA／secret 值；
- 收據內任何巢狀層級的呼叫都 exit 0，且必要呼叫都在；
- deployment 歷史完整：筆數等於 `totalCount`、status 沒有被截斷、每個曾成功的 deployment 都解析到完整 job 清單；
- 每一項都具備環境、命令、門檻、時間窗、owner 與下次檢查日期；
- 狀態不是 `BLOCKED_BY_EVIDENCE` 的項目，必須附上存在的 runtime 收據；
- `rollout_precondition.observed` 的每個數字與 run 清單，都等於從完整歷史重算的結果；
- 三個環境的 10 個 key 都有指紋；是否與 production 相同由 verifier 從指紋自行推導，不採信收據裡記錄的結論；dev／staging 相同的 key 都已揭露；
- 本文件表格的狀態與日期與 JSON 一致；
- 文字沒有觸發治理閘的豁免或移交宣稱。

若要重新讀回，執行 `python3 docs/evidence/runtime/ODP-NFR-RUNTIME-EVIDENCE-001/capture_rollout_readback.py`（需要 `gh` 已登入，約 1 分鐘）。只有全部呼叫成功且歷史完整時才會覆寫收據，否則 exit 1 並保留原檔。覆寫後要同步更新 `disposition.json` 的 digest 與 `rollout_precondition.observed`。

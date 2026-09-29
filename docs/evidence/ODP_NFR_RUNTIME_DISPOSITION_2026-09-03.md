# ODP-NFR-RUNTIME-EVIDENCE-001：SHARED-008 與四條 NFR 的執行期處置

- **任務**：`ODP-NFR-RUNTIME-EVIDENCE-001`（owner Claude，reviewer Codex2）
- **基準**：`origin/dev` @ `0d5699401d3c70d2a55f23c12e9e2ee19668a77a`
- **評估時間**：2026-09-29T07:03:14Z（readback 窗口 `07:02:50Z`–`07:03:14Z`，實際時鐘讀值）
- **依據**：[修正計畫](../plans/ODP_REMEDIATION_PLAN_2026-09-03.md)「目前不進入實作的方向」、[待裁決事項](../plans/ODP_OPEN_DECISIONS_2026-09-03.md) 第 20 項、[需求處置政策](../governance/ODP_REQUIREMENT_DISPOSITIONS.md) §2
- **機讀處置**：[`runtime/ODP-NFR-RUNTIME-EVIDENCE-001/disposition.json`](runtime/ODP-NFR-RUNTIME-EVIDENCE-001/disposition.json)
- **原始收據**：[`runtime/ODP-NFR-RUNTIME-EVIDENCE-001/rollout-readback.json`](runtime/ODP-NFR-RUNTIME-EVIDENCE-001/rollout-readback.json)，sha256 `27ba98016cb11cd320cf5d3a1f7a895000be8aa17e6465cebdb53cc5726284fd`，由同目錄 `capture_rollout_readback.py` 產生

## 結論

五項全部維持 **`BLOCKED_BY_EVIDENCE`**。

本 task 的前提是「immutable dev/staging/prod rollout receipt 可用」。依賴 task `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 已 done，但它交付的是 build 與准入證據，本身明寫「blocked, not deployed」。本次對 GitHub 讀回的結果也一致：三個環境都**沒有任何一次** `Deploy the admitted artifact by immutable digest` job 成功。

| 環境 | 讀回結果 | 收據位置 |
|---|---|---|
| dev | 最新 Runtime Release run `36509055237`（candidate `ee06d1d8`）只跑了 build，lease／deploy／watch 三個 job 都是 `skipped`。deployment status 有出現 `success` 的三個 run（`36250018645`、`36252020646`、`36278150009`，都是 `c8d26f02`），deploy job 都是 `failure`（migration 失敗，見 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001/deploy-run-36278150009-migration-failure.md`）。hosted probe 在 `01:44Z` 讀到五個 dev target 全部 absent | `calls.latest_run_jobs`、`calls.success_deployment_runs_dev`；`prior_receipts[0]` |
| staging | 唯一的 `success` deployment（run `33320822376`，2026-08-30）只跑了 `Verify staging deployer state backend access`，release 相關 job 全部 `skipped`。其餘 81 筆都是 failure 或更早的紀錄 | `calls.success_deployment_runs_staging` |
| production | GitHub 上 0 筆 deployment | `calls.deployments_production` |

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

收據的 `identity_reference_comparison` 比對三個 GitHub environment 的 10 個識別與 secret 參照變數：project、deployer SA、WIF provider、runtime SA、scheduler SA、四個 secret ref、snapshot bucket。收據只存各值的 sha256 指紋，不存原值。

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
- 每一項都具備環境、命令、門檻、時間窗、owner 與下次檢查日期；
- 狀態不是 `BLOCKED_BY_EVIDENCE` 的項目，必須附上存在的 runtime 收據；
- `rollout_precondition` 與收據內的 deploy job 結論一致；
- SHARED-008 的揭露與指紋比對一致；
- 本文件表格的狀態與日期與 JSON 一致；
- 文字沒有觸發治理閘的豁免或移交宣稱。

若要重新讀回，執行 `python3 docs/evidence/runtime/ODP-NFR-RUNTIME-EVIDENCE-001/capture_rollout_readback.py`（需要 `gh` 已登入）。它會覆寫收據，之後要同步更新 `disposition.json` 的 digest。

# ODP-FR-SHARED-001：`PARTIAL` production producer 查證

- 查證日期：2026-09-03
- Repo evidence baseline：`75d25f653aa12c21a3f9627f29af2ed4def73153`
- `origin/dev` merge-base：`6b893fd347e8452f34c2e0abdca5c4f67c8ae900`
- Task：`ODP-JOB-PARTIAL-PRODUCER-EVIDENCE-001`
- Evidence status：`BLOCKED_BY_EVIDENCE`（repo 內沒有 producer；尚無 live production queue／scheduler receipt 可證明部署中的實際 producer 狀態）

## 結論

截至上述 repo baseline，**沒有任何 production job implementation 會回報 canonical `JobStatus.PARTIAL`**。`PARTIAL` 仍是可查詢、可序列化的詞彙成員，但目前沒有可達的 job producer、item-level job receipt，或把一個 job 的成功／失敗成員聚合成 `PARTIAL` 的狀態轉移。

因此 `delivery_toolchain/governance/set_valued_requirements.json` 中 `ODP-FR-SHARED-001` 的 `PARTIAL` member 必須維持 `status: "absent"`；這個 manifest 是集合索引，不能用它把原始 requirement 改寫成「已決定不做」。若產品要求目前一定要有 `PARTIAL` producer，須另立正式 requirement amendment 或期限明確的 waiver／risk acceptance，並指定 owner、期限與 reopen trigger。

Manifest entry：[`delivery_toolchain/governance/set_valued_requirements.json:114-146`](../../delivery_toolchain/governance/set_valued_requirements.json:114)。本文件補的是 producer 查證與 disposition，不直接改動 manifest schema 或其既有 `absent` member。

查證也找到三種容易被誤判為 producer 的 partial-shaped outcome：

1. `POST /api/v1/intake-batches` 的 207 per-row accepted/rejected receipt 是真實的業務部分成功，但它是同步 API command receipt，不是 queue job。
2. XLSX commit 與外部資料 ingestion 都保留 accepted／rejected 或 accepted／quarantined 成員，但各自沒有把 aggregate 寫成 `JobStatus.PARTIAL`。
3. queue 的 `RETRYING`／`DEAD_LETTER` 是 delivery state，不是 business outcome；重試或死信不可算成 `PARTIAL`。

## 判定規則與查證範圍

本次把「適用的 `PARTIAL` producer」定義為同時滿足以下條件的 production path：

- job-level 狀態使用 canonical `JobStatus`，而不是另一個 domain 的 `run_status`、`data_status` 或 HTTP status；
- 一次 job 包含可辨識的多個 work item／member，完成時可以指出哪些成功、哪些失敗或被隔離；
- 對外 receipt／查詢可以讀到該 aggregate 與成員結果；以及
- retry contract 說明是重試整個 job、失敗成員，還是不可重試的成員。單純「某次執行拋錯後 queue 重試」不構成 `PARTIAL`。

查證的是 repo 所表達的 production implementation surface：durable queue、worker registry、API／scheduler enqueue point、module worker entry points、資料 ingestion records 及 contracts。未把測試中手工改 state、歷史文件中的 UI 詞彙、或沒有 runtime wiring 的 batch helper 當成 production producer。這不是 live Cloud Run queue inventory；因此「tree 沒有 producer」與「目前 production deployment 永遠沒有 producer」分開記錄。

## Canonical vocabulary 與 queue state machine

`JobStatus` 的唯一來源是 `packages/schemas/canonical/vocabularies.json`，由 `shared/governance/vocabularies.py` 生成。`PARTIAL` 的定義是「some work completed and some did not」，而 `JobDeliveryState` 另外承載 `RETRYING` 與 `DEAD_LETTER`。對應證據：[`packages/schemas/canonical/vocabularies.json`](../../packages/schemas/canonical/vocabularies.json)、[`shared/governance/vocabularies.py:38-72`](../../shared/governance/vocabularies.py:38)。

實際 queue path 的轉移如下：

| 事件 | canonical job status | delivery state | 證據 |
|---|---|---|---|
| enqueue／claim | `QUEUED` → `RUNNING` | 保留既有 delivery state | [`shared/jobs/queue.py:145-208`](../../shared/jobs/queue.py:145)；durable twin [`shared/infrastructure/persistence/job_queue.py:120-173`](../../shared/infrastructure/persistence/job_queue.py:120) |
| handler 正常返回 | `SUCCEEDED` | 清除 | [`apps/worker/oday_worker/main.py:133-146`](../../apps/worker/oday_worker/main.py:133) |
| 可重試例外 | `QUEUED` | `RETRYING` | [`apps/worker/oday_worker/main.py:192-226`](../../apps/worker/oday_worker/main.py:192) |
| retry budget 用盡／不可重試 | `FAILED` | `DEAD_LETTER` | [`apps/worker/oday_worker/main.py:203-239`](../../apps/worker/oday_worker/main.py:203) |
| 顯式 replay | `QUEUED` | 清除後重新執行 | [`shared/jobs/queue.py:273-307`](../../shared/jobs/queue.py:273) |

兩個 queue implementation 都沒有 `JobStatus.PARTIAL` 的轉移分支。`update_status` 能接受 enum 成員是「schema 可表達」，不是 producer evidence。

公共 intake receipt adapter 也明確把舊的 delivery-shaped status 正規化為 outcome + delivery pair：`RETRYING` → `QUEUED`／`RETRYING`，`DEAD_LETTER` → `FAILED`／`DEAD_LETTER`，不會把 delivery state 當成 `PARTIAL`。見 [`apps/api/app/routes/listings.py:638-668`](../../apps/api/app/routes/listings.py:638)。

## Production job producer inventory

default worker registry 的可執行 job 只有三個：[`apps/worker/oday_worker/handlers.py:247-258`](../../apps/worker/oday_worker/handlers.py:247)。API 的 generic enqueue route 可以收任意字串，但 registry 對未註冊 job type 會拋 `UnknownJobTypeError`，最後走 retry／dead-letter，不能因此新增一個 producer。見 [`shared/jobs/registry.py:79-84`](../../shared/jobs/registry.py:79) 與 [`apps/worker/oday_worker/main.py:263-267`](../../apps/worker/oday_worker/main.py:263)。

| candidate job | enqueue／entry point | 成功、失敗、成員 receipt | `PARTIAL` 判定 |
|---|---|---|---|
| `forecast` | API `/platform/jobs` 於 [`apps/api/oday_api/main.py:977-992`](../../apps/api/oday_api/main.py:977)；handler registry 同上 | handler 驗證一個 store 的 timeseries，正常返回由 worker 統一寫 `SUCCEEDED`；例外由 shared worker retry／fail。單一 store job 沒有成功／失敗 member aggregate。 | **不適用**：無 `PARTIAL` write 或 item receipt。 |
| `external-fetch` | scheduler 每 tick 只 enqueue 一個 provider/window 的 [`JobRequest`](../../apps/scheduler/oday_scheduler/main.py:165)；API 也可 enqueue 同一 job type，見 [`apps/api/oday_api/main.py:993-1024`](../../apps/api/oday_api/main.py:993) | `ExternalFetchScheduler` 對一個 provider/window 產生 `SUCCEEDED` 或 `FAILED` run；worker 對非 `FAILED` 的 run 正常返回，故 queue job 由外層寫 `SUCCEEDED`。provider failure 走 backoff／circuit；沒有 job-level member aggregate。 | **不適用於 canonical `PARTIAL`**；有 data-level partial candidate，詳見下節。 |
| `assisted-listing-intake` | `NetworkListingService` 的 approved retrieval path enqueue [`JobRequest`](../../modules/opsboard/application/network_listings.py:1195)；handler registry 同上 | 一個 URL／intake job 依序更新 stage 到 `RUNNING`；stage 失敗在本地 retry，超限拋 `NonRetryableJobError`；外層只會 `SUCCEEDED`、retry／`FAILED`。見 [`apps/worker/assisted_listing_intake/worker.py:80-221`](../../apps/worker/assisted_listing_intake/worker.py:80)。 | **不適用**：一個 intake，不是可聚合的 item batch；沒有 `PARTIAL` transition。 |
| generic／unknown `job_type` | `/platform/jobs` 的 `JobCreatePayload.job_type` 是字串，見 [`apps/api/oday_api/main.py:101-104`](../../apps/api/oday_api/main.py:101) | 沒有 handler 時 `UnknownJobTypeError`；shared loop retry 後 dead-letter。 | **不適用**：未知 job 不是 partial producer。 |

`JobRequest` 的其他 production call sites 是 receipt persistence，不是額外的 worker job：`TenantScopedJobReceiptStore` 使用 `{service}.receipt`，`TenantScopedCommandReceiptStore` 使用 `{service}.command-receipt`；queue lease／claim 會排除這兩種 suffix。見 [`shared/infrastructure/persistence/job_receipts.py:61-110`](../../shared/infrastructure/persistence/job_receipts.py:61) 與 [`shared/jobs/queue.py:10-18`](../../shared/jobs/queue.py:10)。

`apps/worker/consumers/assisted_listing_intake.py` 是 event consumer／dedup／DLQ 邊界，不是 shared `JobRegistry` handler；它處理的是 event delivery failure，沒有 `JobStatus` business outcome 或 member aggregate。`modules/external_data/providers/weather_demographics.py` 的 `registry.register` 命中則是 provider registry，不是 job registry。兩者均不增加本表的 production job candidate。

## Partial-shaped business outcomes（有成員，但不是 `JobStatus.PARTIAL`）

### 1. 批次 intake：最接近的真實部分成功 operation

`POST /api/v1/intake-batches` 對每一列執行 validation；成功列寫入 intake 並回傳 `ACCEPTED`／`intake_id`，缺 address 的列回傳 `REJECTED`、`retryable: false`、`next_action: CORRECT_INPUT`。只要混合成功與拒絕，API 回 207，並回傳 `accepted_count`、`rejected_count` 與逐列 receipt。見 [`apps/api/app/routes/listings.py:350-363`](../../apps/api/app/routes/listings.py:350) 與 [`apps/api/app/routes/listings.py:1730-1799`](../../apps/api/app/routes/listings.py:1730)。有效的 OpenAPI 說明也明確寫出「per-row partial-success receipt」及不 rollback 其他列：[`packages/schemas/assisted_listing_intake/openapi-effective.json:269-289`](../../packages/schemas/assisted_listing_intake/openapi-effective.json:269)。

這是**真的 partial business outcome**，但 `BatchIntakeReceipt` 沒有 `JobStatus` 欄位，也沒有 enqueue durable job；它由 API command receipt 的 idempotency wrapper 執行。故不能把 HTTP 207 或 `accepted_count < total` 自動映射為全域 `JobStatus.PARTIAL`。若未來要把它納入 `ODP-FR-SHARED-001`，需另訂 command-to-job mapping、job id、member receipt schema 與 member retry contract。

現有 contract test 也只證明這個 API operation 的 207 語意：一列有效、一列無 address 時 asserted `(accepted_count, rejected_count) == (1, 1)` 及兩列 status；見 [`tests/contract/test_assisted_listing_v1_runtime.py:41-50`](../../tests/contract/test_assisted_listing_v1_runtime.py:41)。

### 2. XLSX import：partial commit，不是 queue job

XLSX preview 會產生 `valid_rows` 與 `row_errors`；commit 重新執行相同 validation，只把 valid rows 寫入，receipt 帶 `accepted_count`、`rejected_count` 與可解析的 `intake_ids`。相同 tenant／actor／idempotency key 會 replay receipt。見 [`modules/external_data/application/xlsx_import.py:816-850`](../../modules/external_data/application/xlsx_import.py:816) 與 [`modules/external_data/application/xlsx_import.py:853-933`](../../modules/external_data/application/xlsx_import.py:853)。

這也是可辨識的部分成功，但 `XlsxCommitReceipt` 沒有 `JobStatus`，commit 在 API command 中同步完成，沒有 queue claim、worker retry 或 `PARTIAL` producer。它的 partial 語意應維持在 import contract，不應為了填滿 JobStatus member 而硬接。

### 3. External data ingestion：provider data-level partial，job-level 仍是 success/fail

外部 connector 對每筆 record 保留 `accepted`、lineage 與 quarantine issues；`IngestionRunRecord` 另保存 `accepted_count`、`quarantined_count`、`total_count`、`lineage` 與 `quarantine`。見 [`modules/external_data/application/ingestion_store.py:90-123`](../../modules/external_data/application/ingestion_store.py:90) 與 [`modules/external_data/application/ingestion_store.py:207-290`](../../modules/external_data/application/ingestion_store.py:207)。因此例如 provider 回傳 2 筆、1 筆 accepted、1 筆 quarantined 時，資料面確實可以描述為部分攝取；測試收據也查證了這組成員結果，見 [`tests/integration/test_external_ingestion_persistence.py:187-212`](../../tests/integration/test_external_ingestion_persistence.py:187)。

但 scheduler 的 `ExternalFetchRun` 只對一個 `ExternalFetchJobSpec(provider_id, schedule_id, window)` 產生 `status: "SUCCEEDED"` 或 `status: "FAILED"`，成功時另標 `data_status: FRESH|STALE`，阻擋時標 `FAILED`／`BLOCKED`。見 [`modules/external_data/workers/scheduled_fetch.py:116-186`](../../modules/external_data/workers/scheduled_fetch.py:116) 與 [`modules/external_data/workers/scheduled_fetch.py:512-628`](../../modules/external_data/workers/scheduled_fetch.py:512)。`ExternalIngestionService` 將 counts 寫進 ingestion record／audit，但不把 accepted/quarantined 混合改寫為 `JobStatus.PARTIAL`，見 [`modules/external_data/application/ingestion_service.py:307-335`](../../modules/external_data/application/ingestion_service.py:307)。

retry contract 也是整個 provider/window 的 scheduler contract：window idempotency、failure backoff、circuit breaker；沒有「只重試 quarantined member」的 job receipt。見 [`modules/external_data/workers/scheduled_fetch.py:424-573`](../../modules/external_data/workers/scheduled_fetch.py:424)。所以它是應在產品語意上明確命名的 data-quality partial candidate，但截至 baseline **不是 canonical JobStatus.PARTIAL producer**。

此外，雖然 default factory 可建立 listing、POI、admin-boundary 三種 provider，scheduler 的 recurring path 每 tick 只 enqueue `listing.partner_feed`，見 [`apps/scheduler/oday_scheduler/main.py:125-187`](../../apps/scheduler/oday_scheduler/main.py:125)。多來源測試逐一呼叫 service 並產生三筆 run，不是一個 multi-source aggregate job，見 [`tests/integration/test_external_ingestion_multisource.py:118-154`](../../tests/integration/test_external_ingestion_multisource.py:118)。

## 其他 module worker 排除表

以下 entry points 有「batch」或 worker 命名，故列入候選掃描；它們不是 `oday-worker` default registry 的 durable job producer，且其 aggregate status 沒有 `PARTIAL`：

| module entry point | 現有 aggregate 行為 | 排除理由 |
|---|---|---|
| AVM | 逐 item 建 case／report；正常結束固定 `status="succeeded"`，任一例外中止呼叫 | 沒有 member failure receipt 或 `PARTIAL`，見 [`modules/avm/workers/valuation_worker.py:47-73`](../../modules/avm/workers/valuation_worker.py:47)。 |
| SiteScore | 逐 candidate 產出 reports；固定 `status="succeeded"`，warnings 不是 failure member | warnings／report 不等於 job outcome，見 [`modules/sitescore/workers/scoring_worker.py:50-77`](../../modules/sitescore/workers/scoring_worker.py:50)。 |
| ForecastOps | domain service 回傳單一 forecast result；固定 `status="succeeded"` | 不是 queue producer，也沒有 mixed member receipt，見 [`modules/forecastops/workers/forecast_worker.py:62-98`](../../modules/forecastops/workers/forecast_worker.py:62)。 |
| HeatZone | 多個 score + warnings；固定 `status="succeeded"` | warning 不會轉成 job partial，見 [`modules/heatzone/workers/scoring_worker.py:62-114`](../../modules/heatzone/workers/scoring_worker.py:62)。 |
| AdLift | 多 campaign evaluate；固定 `status="succeeded"` | 無 item-level success/failure aggregate，見 [`modules/adlift/workers/incrementality_worker.py:42-59`](../../modules/adlift/workers/incrementality_worker.py:42)。 |
| NetPlan | 多 scenario；`succeeded` 或 `completed_with_infeasible` | `completed_with_infeasible` 是 domain result，不是 canonical `PARTIAL`，見 [`modules/netplan/workers/solver_worker.py:35-71`](../../modules/netplan/workers/solver_worker.py:35)。 |
| PriceOps | 聚合 hard-constraint violations；全數零則 `succeeded`，否則 `failed` | fail-fast／aggregate failure，沒有 partial transition，見 [`modules/priceops/workers/optimizer_worker.py:67-109`](../../modules/priceops/workers/optimizer_worker.py:67)。 |
| Geocode | 回傳 geocoded tuple + warnings，固定 `succeeded` | warnings 不含 retry/member failure contract，見 [`modules/integration/workers/geocode_worker.py:30-63`](../../modules/integration/workers/geocode_worker.py:30)。 |
| Intervention observation sweep | `matured_ids`、`pending_ids`、`evaluated_ids`；預設 `SUCCEEDED` | 這是 sweep partition，不是部分成功／失敗的 job receipt，見 [`modules/intervention/workers/observation_worker.py:20-81`](../../modules/intervention/workers/observation_worker.py:20)。 |
| Market survey expiry sweep | `expired_count` + ids | 沒有 job status 或 failure members，見 [`modules/market_survey/workers/expiry_worker.py:19-67`](../../modules/market_survey/workers/expiry_worker.py:19)。 |
| LearningHub release／monitor／recovery | 回傳 release decision、assessment 或 recovery tuple | 是 release saga/application entry point，README 只有 payload guard，不接 `JobStatus` registry，見 [`modules/learninghub/workers/release_worker.py:15-112`](../../modules/learninghub/workers/release_worker.py:15)。 |

`PredictionRun.run_status` 在 canonical model 另有 lowercase `partial` member，但這是 prediction-run domain field，不是 shared `JobStatus`；目前 forecast／sitescore application path 寫 `run_status="succeeded"`。見 [`packages/schemas/canonical/index.ts:228-237`](../../packages/schemas/canonical/index.ts:228) 與 [`modules/forecastops/application/forecasting.py:209`](../../modules/forecastops/application/forecasting.py:209)。同理，transaction `partial`、data coverage `partial`、heat-zone `PARTIALLY_ABSORBED` 都不可當作 shared job producer evidence。

## Test-only fixture 與負向證據

public job receipt schema 確實允許 `PARTIAL`：[`apps/api/app/routes/listings.py:204-214`](../../apps/api/app/routes/listings.py:204)，OpenAPI 也列出該 enum：[`packages/schemas/assisted_listing_intake/openapi-effective.json:2880-2889`](../../packages/schemas/assisted_listing_intake/openapi-effective.json:2880)。但是唯一找到的 partial job receipt test 是先建立 intake，再直接執行：

```python
store.jobs[job_id]["status"] = "PARTIAL"
store.jobs[job_id]["delivery_state"] = None
```

見 [`tests/contract/test_assisted_listing_operations.py:1919-1946`](../../tests/contract/test_assisted_listing_operations.py:1919)。這證明 read boundary 能呈現 `PARTIAL` 且不帶 delivery state；它沒有證明任何 handler／scheduler／API producer 會產生該狀態。這個區分正是本 task 要保留的 evidence boundary。

## Disposition、reopen trigger 與下一步

| 項目 | disposition |
|---|---|
| `ODP-FR-SHARED-001 / PARTIAL` member | `absent` in manifest；repo implementation evidence：沒有 producer。 |
| 「目前 production deployment 也沒有 producer」 | `BLOCKED_BY_EVIDENCE`：需 live queue／scheduler／worker receipt inventory 才能把 repo 結論提升為 deployment fact。 |
| 批次 intake／XLSX | 保留各自的 207／accepted-rejected command contract；不改寫成 shared job status。若要納入 shared job，先補正式 mapping、member receipt、retry／replay policy。 |
| external ingestion | 保留 `status` 與 `accepted/quarantined`、lineage、quarantine 的分層語意；若產品要 job-level `PARTIAL`，先決定 provider/window aggregate 與 quarantined-member retry。 |

重新開啟查證的 trigger：

- default registry 新增 handler，或任一 handler 出現 reachable `JobStatus.PARTIAL` write；
- scheduler/API 新增一個 `JobRequest` 同時承載多個 work members，且 receipt 暴露 member-level success/failure；
- external ingestion 增加跨 provider/window aggregate job；或
- live production receipt 出現 `PARTIAL`，但 repo trace 找不到對應 producer。

在以上 trigger 之一發生前，不應為了讓 manifest 的六個 member 全部變成 satisfied 而硬接任意 producer。

## 可重現查證收據

本 task brief 的 Verification 為 `none`，因此未執行測試 suite；下列是只讀 static trace 收據：

```text
git rev-parse HEAD
75d25f653aa12c21a3f9627f29af2ed4def73153

rg -n --glob '*.py' --glob '*.ts' --glob '*.tsx' \
  'JobStatus\.PARTIAL|JobStatus\s*\.\s*PARTIAL' \
  apps modules shared packages
<no matches>

rg -n --glob '*.py' 'JobRequest\(|registry\.register\(' \
  apps modules shared
<enqueue sites: API generic jobs, scheduler external-fetch,
  assisted-listing-intake; default registry: forecast, external-fetch,
  assisted-listing-intake; receipt stores are separately identified>
```

這些收據只支持「截至 repo baseline，沒有可達 `JobStatus.PARTIAL` producer」；不支持「live production 永遠不會產生」的更強宣稱。

## 來源文件

- [`ODP_REMEDIATION_PLAN_2026-09-03.md`](../plans/ODP_REMEDIATION_PLAN_2026-09-03.md)：第 5b 項要求先查證真實 producer，不得硬接。
- [`ODP_OPEN_DECISIONS_2026-09-03.md`](../plans/ODP_OPEN_DECISIONS_2026-09-03.md)：第 10 項將 `PARTIAL` 記為詞彙存在但 producer 未證明。
- [`ODP_STRUCTURAL_REMEDIATION_2026-09-01.md`](ODP_STRUCTURAL_REMEDIATION_2026-09-01.md)：合併後的既有結論與可重現查證限制。
- [`ODP_FR_VERIFICATION_112_AND_ROOT_CAUSES_2026-09-01.md`](ODP_FR_VERIFICATION_112_AND_ROOT_CAUSES_2026-09-01.md)：`SHARED-001` 的原始缺口與兩套 job vocabulary 查證。

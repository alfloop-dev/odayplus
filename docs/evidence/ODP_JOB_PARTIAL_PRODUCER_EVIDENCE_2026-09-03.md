# ODP-FR-SHARED-001：`PARTIAL` production producer 查證

- 查證日期：2026-09-03
- Repo evidence baseline：`3be12280e8e5e6d52633aa1a404b427c2416a423`（`origin/dev`；本文所有行號引用都以這棵樹為準）
- 初次查證 baseline：`75d25f653aa12c21a3f9627f29af2ed4def73153`（base advance 前，結論已於新 base 重驗，見〈base advance 後的重新查證〉）
- Task：`ODP-JOB-PARTIAL-PRODUCER-EVIDENCE-001`
- Evidence status：`BLOCKED_BY_EVIDENCE`（repo 內沒有 producer；尚無 live production queue／scheduler receipt 可證明部署中的實際 producer 狀態）
- Manifest disposition：`ODP-FR-SHARED-001 / PARTIAL` 隨本 task 由 `OPEN` 轉入 `BLOCKED_BY_EVIDENCE`（`status` 仍為 `absent`）

## 結論

截至上述 repo baseline，**沒有任何 production job implementation 會回報 canonical `JobStatus.PARTIAL`**。`PARTIAL` 仍是可查詢、可序列化的詞彙成員，但目前沒有可達的 job producer、item-level job receipt，或把一個 job 的成功／失敗成員聚合成 `PARTIAL` 的狀態轉移。

因此 `delivery_toolchain/governance/set_valued_requirements.json` 中 `ODP-FR-SHARED-001` 的 `PARTIAL` member 必須維持 `status: "absent"`；這個 manifest 是集合索引，不能用它把原始 requirement 改寫成「已決定不做」。若產品要求目前一定要有 `PARTIAL` producer，須另立正式 requirement amendment 或期限明確的 waiver／risk acceptance，並指定 owner、期限與 reopen trigger。

Manifest entry：[`delivery_toolchain/governance/set_valued_requirements.json:246-318`](../../delivery_toolchain/governance/set_valued_requirements.json:246)。本文件補的是 producer 查證，並據此把該 member 的 `disposition.state` 從 `OPEN` 推進到 `BLOCKED_BY_EVIDENCE`；`status` 維持 `absent`，manifest schema 與其他 member 不動。兩個 tracking state 都不是裁決，差別在於 `BLOCKED_BY_EVIDENCE` 必須具名 `evidence_needed`／`evidence_owner`／`next_review_date`，因此把「還缺什麼才能推進」寫成機器可讀，而不是留在文件裡。轉出 `BLOCKED_BY_EVIDENCE` 若要走向 `DECIDED`，仍必須由具名人類治理角色簽署，AI 不得自簽，見 [`docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md`](../governance/ODP_REQUIREMENT_DISPOSITIONS.md) §3.2。

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
| `external-fetch` | scheduler 每 tick 只 enqueue 一個 provider/window 的 [`JobRequest`](../../apps/scheduler/oday_scheduler/main.py:181)；API 也可 enqueue 同一 job type，見 [`apps/api/oday_api/main.py:993-1024`](../../apps/api/oday_api/main.py:993) | `ExternalFetchScheduler` 對一個 provider/window 產生 `SUCCEEDED` 或 `FAILED` run；worker 對非 `FAILED` 的 run 正常返回，故 queue job 由外層寫 `SUCCEEDED`。provider failure 走 backoff／circuit；沒有 job-level member aggregate。 | **不適用於 canonical `PARTIAL`**；有 data-level partial candidate，詳見下節。 |
| `assisted-listing-intake` | `NetworkListingService` 的 approved retrieval path enqueue [`JobRequest`](../../modules/opsboard/application/network_listings.py:1210)；handler registry 同上 | 一個 URL／intake job 依序更新 stage 到 `RUNNING`；stage 失敗在本地 retry，超限拋 `NonRetryableJobError`；外層只會 `SUCCEEDED`、retry／`FAILED`。見 [`apps/worker/assisted_listing_intake/worker.py:80-221`](../../apps/worker/assisted_listing_intake/worker.py:80)。 | **不適用**：一個 intake，不是可聚合的 item batch；沒有 `PARTIAL` transition。 |
| generic／unknown `job_type` | `/platform/jobs` 的 `JobCreatePayload.job_type` 是字串，見 [`apps/api/oday_api/main.py:101-104`](../../apps/api/oday_api/main.py:101) | 沒有 handler 時 `UnknownJobTypeError`；shared loop retry 後 dead-letter。 | **不適用**：未知 job 不是 partial producer。 |

`JobRequest` 的其他 production call sites 是 receipt persistence，不是額外的 worker job：`TenantScopedJobReceiptStore` 使用 `{service}.receipt`（[`shared/infrastructure/persistence/job_receipts.py:61-110`](../../shared/infrastructure/persistence/job_receipts.py:61)），`TenantScopedCommandReceiptStore` 使用 `{service}.command-receipt`（[`shared/infrastructure/persistence/command_receipts.py:32-145`](../../shared/infrastructure/persistence/command_receipts.py:32)）。queue lease／claim 以 suffix 排除這兩種 job type，見 [`shared/jobs/queue.py:10-18`](../../shared/jobs/queue.py:10)。兩者都只寫 `SUCCEEDED`，且寫的是 receipt envelope，不是 business outcome。

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
| LearningHub release／monitor／recovery／prediction-drift | 回傳 release decision、assessment、recovery tuple 或 `MonitoringEvaluation` | 是 release saga/application entry point，README 只有 payload guard，不接 `JobStatus` registry，見 [`modules/learninghub/workers/release_worker.py:16-153`](../../modules/learninghub/workers/release_worker.py:16)。base advance 帶入的第四個 entry point `run_prediction_drift` 同樣不註冊到 job registry，詳見下段。 |

base advance 帶入的 `ODP-FR-LH-005` 預測漂移監控是本次新增的 partial-shaped 訊號：`MonitoringEvaluation` 對一次評估保留 `drift_detected` 與逐欄的 `drifted_columns`，所以「部分欄位漂移」在資料面是可辨識的成員結果。見 [`modules/learninghub/domain/monitoring.py:40-66`](../../modules/learninghub/domain/monitoring.py:40) 與 [`modules/learninghub/workers/release_worker.py:97-120`](../../modules/learninghub/workers/release_worker.py:97)。但它回傳 domain evaluation、不經 `JobRegistry`、不寫任何 `JobStatus`，因此與批次 intake、XLSX、ingestion 同類：partial-shaped 但不是 job outcome。

`PredictionRun.run_status` 在 canonical model 另有 lowercase `partial` member，但這是 prediction-run domain field，不是 shared `JobStatus`；目前 forecast／sitescore application path 寫 `run_status="succeeded"`。見 [`packages/schemas/canonical/index.ts:228-237`](../../packages/schemas/canonical/index.ts:228) 與 [`modules/forecastops/application/forecasting.py:209`](../../modules/forecastops/application/forecasting.py:209)。同理，transaction `partial`、data coverage `partial`、heat-zone `PARTIALLY_ABSORBED` 都不可當作 shared job producer evidence。

## Test-only fixture 與負向證據

public job receipt schema 確實允許 `PARTIAL`：[`apps/api/app/routes/listings.py:204-214`](../../apps/api/app/routes/listings.py:204)，OpenAPI 也列出該 enum：[`packages/schemas/assisted_listing_intake/openapi-effective.json:2880-2889`](../../packages/schemas/assisted_listing_intake/openapi-effective.json:2880)。但是唯一找到的 partial job receipt test 是先建立 intake，再直接執行：

```python
store.jobs[job_id]["status"] = "PARTIAL"
store.jobs[job_id]["delivery_state"] = None
```

見 [`tests/contract/test_assisted_listing_operations.py:1919-1946`](../../tests/contract/test_assisted_listing_operations.py:1919)。這證明 read boundary 能呈現 `PARTIAL` 且不帶 delivery state；它沒有證明任何 handler／scheduler／API producer 會產生該狀態。這個區分正是本 task 要保留的 evidence boundary。

詞彙已一路發佈到 typed client：`export type JobStatus = "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED" | "PARTIAL";`，見 [`packages/openapi-client/src/generated/types.ts:840`](../../packages/openapi-client/src/generated/types.ts:840) 與 [`packages/openapi-client/openapi.json:4115`](../../packages/openapi-client/openapi.json:4115)。這使負向結論更明確而非更弱：從 canonical vocabulary、server schema、OpenAPI 到 generated client 全都宣告了 `PARTIAL`，唯獨沒有任何一段程式會寫出它。生成物有這個成員不能回頭當成 producer evidence。

## Base advance 後的重新查證

本 task 的初次查證做在 `75d25f65`。送審前 base advance 把 `origin/dev` 的 48 個 commit（`75d25f65..3be12280`，57 個檔案）併入本分支，因此結論必須在新 base 上重驗，而不是沿用舊 baseline 的宣稱。

本文目前引用 40 個檔案，其中 35 個未被這 48 個 commit 更動，行號引用仍指向原本的程式碼。被 base advance 改到的有 5 個，皆已逐一重讀並更新引用：`delivery_toolchain/governance/set_valued_requirements.json`（`ODP-FR-SHARED-001` 條目移到 246-318 行）、`modules/learninghub/workers/release_worker.py`（新增第四個 entry point）、`modules/learninghub/domain/monitoring.py`、`packages/openapi-client/openapi.json` 與 `packages/openapi-client/src/generated/types.ts`（後三者是重新查證期間新增的引用）。

重新查證也順手修掉初次查證留下的三處行號誤植：`network_listings.py` 的 `JobRequest` 在 1210 而非 1195、`oday_scheduler/main.py` 的 `JobRequest` 在 181 而非 165，以及 `TenantScopedCommandReceiptStore` 其實住在 `command_receipts.py` 而不是 `job_receipts.py`。三處都不影響原結論，但引用要指得到才算收據。

在新 base 上重跑的結果：

| 重驗項目 | 新 base 結果 |
|---|---|
| `JobStatus.PARTIAL` 的可達 write | 仍為零；`apps`、`modules`、`shared`、`packages` 皆無命中 |
| default registry job type | 仍是 `forecast`、`external-fetch`、`assisted-listing-intake` 三個，未增減 |
| production `JobRequest` enqueue 站點 | 仍是 scheduler external-fetch、API generic jobs、assisted-listing-intake，加上兩個 receipt store |
| 新進 job producer | 無。48 個 commit 新增的是 operator comments、prediction-drift 監控與治理閘，都不註冊 job handler |

base advance 唯一實質改變本文結論表達方式的，是 `ODP-REQUIREMENT-DISPOSITIONS` 引進的五態 disposition 生命週期與 `check_requirement_members.py` 閘門。它把 `PARTIAL` 登錄為 `OPEN`（尚在調查中）。本 task 就是那次調查：tree 層的問題已經有答案，剩下的缺口是 runtime 證據與產品裁決，對應的具名狀態是 `BLOCKED_BY_EVIDENCE`。因此本 task 把該 member 推進到 `BLOCKED_BY_EVIDENCE` 並補齊 `evidence_needed`／`evidence_owner`／`next_review_date`，同步更新 [`ODP_REQUIREMENT_DISPOSITIONS.md`](../governance/ODP_REQUIREMENT_DISPOSITIONS.md) §4.5，使 manifest、治理登錄表與本證據文件三者說同一件事。`status` 仍是 `absent`，且沒有動到任何 waiver 欄位。

## Disposition、reopen trigger 與下一步

| 項目 | disposition |
|---|---|
| `ODP-FR-SHARED-001 / PARTIAL` member | manifest `status` 維持 `absent`；`disposition.state` 由 `OPEN` 推進為 `BLOCKED_BY_EVIDENCE`，`evidence_owner` 為 `Platform Infrastructure Lead`，`next_review_date` 為 `2026-10-01`。 |
| 「目前 production deployment 也沒有 producer」 | 仍是缺口：需 live queue／scheduler／worker receipt inventory 才能把 repo 結論提升為 deployment fact。這是 `evidence_needed` 的第一項。 |
| 「是否應該要有 producer」 | 產品裁決，不是查證結果。要走 `DECIDED` 必須由具名人類治理角色簽署 amendment 或具期限 waiver；AI 不得自簽。這是 `evidence_needed` 的第二項。 |
| 批次 intake／XLSX | 保留各自的 207／accepted-rejected command contract；不改寫成 shared job status。若要納入 shared job，先補正式 mapping、member receipt、retry／replay policy。 |
| external ingestion | 保留 `status` 與 `accepted/quarantined`、lineage、quarantine 的分層語意；若產品要 job-level `PARTIAL`，先決定 provider/window aggregate 與 quarantined-member retry。 |

重新開啟查證的 trigger：

- default registry 新增 handler，或任一 handler 出現 reachable `JobStatus.PARTIAL` write；
- scheduler/API 新增一個 `JobRequest` 同時承載多個 work members，且 receipt 暴露 member-level success/failure；
- external ingestion 增加跨 provider/window aggregate job；或
- live production receipt 出現 `PARTIAL`，但 repo trace 找不到對應 producer。

在以上 trigger 之一發生前，不應為了讓 manifest 的六個 member 全部變成 satisfied 而硬接任意 producer。

## 可重現查證收據

本 task brief 的 Verification 欄為 `none`，故未跑產品測試 suite。下列 static trace 收據皆在 base advance 之後的工作樹上重跑，逐字可重現；`rg` 找不到命中時 exit code 為 1。

```text
$ git rev-parse HEAD
13ed645a2e4d44dde52c17ac4b3ea1ee5da66984      # base advance merge; 併入的 base 是 3be12280

$ rg -n --glob '*.py' --glob '*.ts' --glob '*.tsx' \
    'JobStatus\s*\.\s*PARTIAL' apps modules shared packages
; exit=1（無命中）

$ rg -n --glob '*.py' 'registry\.register\(' apps/worker
apps/worker/oday_worker/handlers.py:255:    registry.register(FORECAST_JOB_TYPE, handle_forecast)
apps/worker/oday_worker/handlers.py:256:    registry.register(EXTERNAL_FETCH_JOB_TYPE, handle_external_fetch)
apps/worker/oday_worker/handlers.py:257:    registry.register(INTAKE_JOB_TYPE, handle_assisted_listing_intake)

$ rg -n --glob '*.py' 'JobRequest\(' apps modules shared | rg -v '/tests?/|test_'
modules/opsboard/application/network_listings.py:1210    # assisted-listing-intake enqueue
shared/infrastructure/persistence/command_receipts.py:74 # {service}.command-receipt，非 worker job
shared/infrastructure/persistence/job_receipts.py:75     # {service}.receipt，非 worker job
apps/api/oday_api/main.py:1018                           # generic /platform/jobs enqueue
apps/scheduler/oday_scheduler/main.py:181                # 每 tick 一個 external-fetch provider/window
```

因本 task 變更了 requirement manifest 的 disposition，額外跑治理閘（此為變更後的必跑檢查，不是產品測試 suite）：

```text
$ UV_PYTHON=/usr/bin/python3.12 uv run --frozen python \
    delivery_toolchain/governance/check_requirement_members.py
Requirement member checks passed: 6 set-valued requirements, 32 members
(24 satisfied, 8 absent and noted; dispositions: BLOCKED_BY_EVIDENCE=4,
 DECIDED=1, IMPLEMENTATION_READY=1, OPEN=2, VERIFIED=24).
; exit=0
```

這些收據只支持「截至上述 repo baseline，沒有可達 `JobStatus.PARTIAL` producer」；不支持「live production 永遠不會產生」的更強宣稱。兩者的差別就是 `evidence_needed` 第一項存在的理由。

## 來源文件

- [`ODP_REMEDIATION_PLAN_2026-09-03.md`](../plans/ODP_REMEDIATION_PLAN_2026-09-03.md)：第 5b 項要求先查證真實 producer，不得硬接。
- [`ODP_OPEN_DECISIONS_2026-09-03.md`](../plans/ODP_OPEN_DECISIONS_2026-09-03.md)：第 10 項將 `PARTIAL` 記為詞彙存在但 producer 未證明。
- [`ODP_STRUCTURAL_REMEDIATION_2026-09-01.md`](ODP_STRUCTURAL_REMEDIATION_2026-09-01.md)：合併後的既有結論與可重現查證限制。
- [`ODP_FR_VERIFICATION_112_AND_ROOT_CAUSES_2026-09-01.md`](ODP_FR_VERIFICATION_112_AND_ROOT_CAUSES_2026-09-01.md)：`SHARED-001` 的原始缺口與兩套 job vocabulary 查證。
- [`ODP_REQUIREMENT_DISPOSITIONS.md`](../governance/ODP_REQUIREMENT_DISPOSITIONS.md)：base advance 帶入的五態 disposition 生命週期；§3.2 禁止 AI 自簽豁免，§4.5 為本 member 的登錄條目。

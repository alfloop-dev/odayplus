# INT-001 CDC 來源系統查證

- Task: `ODP-INT001-CDC-SOURCE-EVIDENCE-001`
- 需求: `ODP-RTM-INT-001`（`docs/rtm/ODAY_PLUS_EXECUTION_RTM.md:65`，`MUST`，`baselined`，owner 記為 `Data Platform Owner`）
- 日期: 2026-09-03
- Base: `75d25f653aa12c21a3f9627f29af2ed4def73153`
- 上游查證來源: `docs/plans/ODP_REMEDIATION_PLAN_2026-09-03.md`（第 6 批）、`docs/plans/ODP_OPEN_DECISIONS_2026-09-03.md`（第三層 #15）、`docs/evidence/ODP_FR_VERIFICATION_112_AND_ROOT_CAUSES_2026-09-01.md`（`INT-001`，成因 02）

---

## 判定

**CDC 對目前的來源系統不適用（N/A）。** 沒有任何一個生產上游需要 CDC 才能滿足其 change log、latency、ordering 或 delete semantics；現行的有界批次讀取（全量快照 + `updatedAt` 水位線）加上外部 API 已經覆蓋全部四項。

查證過程另外發現**一個與 CDC 無關的獨立缺陷**：下游落地層完全沒有刪除傳播路徑。這個缺陷**接上 CDC 也不會被修好**（見 §4.4），必須另立追蹤，不能併入 INT-001 的處置一起結案。

同時要記錄一項**次要落差**：`event_stream` 這個已宣告的 mode 在生產同樣沒有生產者（見 §3.4）。它不影響 CDC 的判定，但會影響 INT-001 五個成員的誠實狀態。

---

## 1. 需求原文與兩套不相容的 mode 詞彙

RTM 原文（`docs/rtm/ODAY_PLUS_EXECUTION_RTM.md:65`）：

> Integration Layer must support **batch, CDC, API, file, and event** source ingestion modes.

實作側的 taxonomy（`packages/schemas/source_contracts/index.json` 的 `integration_modes`）：

```
batch_snapshot, incremental_batch, event_stream, backfill, api_lookup
```

兩邊都是五項，但**不是同一組五項**，而且是不同的分類軸：RTM 混用了「傳輸型態」（batch / CDC / event）與「取得方式」（API / file），實作側則把傳輸型態切成三級（全量快照 / 增量 / 事件串流）另加兩個作業模式（回填 / 逐筆查詢），並把 API/file 移到另一個獨立欄位 `acquisition_methods`（`api, file, manual, feed, public_dataset, generated, internal`）。

對照：

| RTM mode | 實作對應 | 狀態 |
|---|---|---|
| batch | `batch_snapshot` + `incremental_batch` + `backfill` | 有，且比需求細 |
| API | `acquisition_method: api` / `api_lookup` | 有，位於另一個欄位 |
| file | `acquisition_method: file` / `feed` / `public_dataset` | 有，位於另一個欄位 |
| event | `event_stream` | 契約有，**生產無生產者**（§3.4） |
| **CDC** | — | **兩邊都沒有** |

CDC 不只是「還沒實作」，它**不在實作的詞彙表裡**，而且這件事被測試釘住：`tests/contract/test_ingestion_contracts.py:127-131` 對 `index["integration_modes"]` 做的是集合**相等**斷言，不是包含斷言。任何人把 `cdc` 加進 registry 而不同時改測試，測試會紅。這是一個好性質——它代表 taxonomy 的變動必須是刻意的——但也代表 §6 的處置不能只改 JSON。

`ODP_FR_VERIFICATION_112_AND_ROOT_CAUSES_2026-09-01.md:250` 記的「CDC 缺席，全樹唯一命中是一段 docstring 註解」在本次複查時仍然成立，只是命中數從 1 變成 3（見 §8 收據 R3），三處都不是實作：

- `shared/domain/models.py:119` — `"""Machine execution run cycle (IoT/CDC)."""`，一行 docstring。
- `tests/contract/test_ingestion_contracts.py:10` 與 `:119` — 測試檔的 docstring 與註解標題，其斷言主體如上所述**不含** `cdc`。

依 acceptance「不得以 docstring 或 fixture 當 production source」，這三處**都不計為來源系統證據**。

---

## 2. 什麼算「生產來源」

本次查證嚴格區分三類，只有第一、二類進入 CDC 判定：

1. **生產內部來源** — 有實際連線設定、有排程或觸發器、有落地資料表。
2. **生產外部來源** — 在 provider registry 內、有 endpoint/credential 環境變數、且在生產環境不會退回 fixture。
3. **僅存在於契約與 fixture 的宣告來源** — 只出現在 `packages/schemas/source_contracts/` 與 `tests/fixtures/`，全樹沒有生產連線。**排除**（§3.5）。

---

## 3. 逐 upstream 清點

### 3.1 唯一的生產內部來源：`fongniao_prod` MongoDB

全部內部生產資料都來自**單一個** MongoDB，經 `apps/data_platform` 落地到 PostgreSQL。

- 讀取器：`apps/data_platform/source.py::MongoSource`（docstring 自述為 "Bounded, projection-complete reader for the approved production database"）
- 連線邊界（`apps/data_platform/config.py::DataPlaneConfig.validate`，fail-closed，違反即 `DataPlaneConfigurationError`）：
  - `ODP_DATA_ENV` 必須為 `production`（`config.py:71-74`）
  - `ODP_DATA_MONGO_DATABASE` 必須恰為 `fongniao_prod`（`config.py:75-78`）
  - Mongo host 不得為 `localhost`/`127.0.0.1`/`::1`（`config.py:82-84`）
  - 每次執行筆數上界 `ODP_DATA_MAX_RECORDS_PER_RUN` ≤ 5,000,000，`batch_size` ∈ [100, 20000]
  - 讀取一律帶投影（`SOURCE_PROJECTIONS`），非全欄位讀取
- 憑證：`ODP_DATA_MONGO_URI`（單一連線字串）

**讀取語意只有兩種**（`source.py::MongoSource._window_query`，`source.py:371-388`）：

- `SNAPSHOT_SOURCE_KINDS`（8 個）→ 查詢條件為 `{}`，即**每次全量掃描**，僅用 `_id` 游標分頁續讀。
- 其餘（7 個）→ 依 `updatedAt` 加該 kind 的 `SOURCE_TIME_FIELDS` 做 `$or` 時間窗 `[window.start, window.end)`，即**水位線增量**。

兩者都以 `sort("_id", 1)` 排序、以 `{"_id": {"$gt": cursor}}` 續讀。

15 個 `SourceKind`（`apps/data_platform/contracts.py::SourceKind`）逐一如下：

| # | SourceKind（collection） | 讀取語意 | 觸發 cadence | 上游變更／刪除語意 |
|---:|---|---|---|---|
| 1 | `merchant` | 全量快照 | 排程 01:00 UTC + `dimension_change_sensor`（≥15 分） | `operation` → tenant/brand status（`mapping.py:374`） |
| 2 | `place` | 全量快照 | 同上 | `operation` → store status，需 approved `status_contract`，否則 quarantine `OPERATION_MAPPING_UNAPPROVED`（`mapping.py:390-400`） |
| 3 | `device` | 全量快照 | 同上 | `operation` → machine status |
| 4 | `campaign` | 全量快照 | 排程 04:00 UTC（無 sensor） | 無 |
| 5 | `product` | 全量快照 | 排程 04:00 UTC | 無 |
| 6 | `products` | 全量快照 | 排程 04:00 UTC | 無 |
| 7 | `promotions` | 全量快照 | 排程 04:00 UTC | 無 |
| 8 | `member` | 全量快照 | **僅手動** job | 無 |
| 9 | `device_daily_statistics` | 水位線窗（`updatedAt`\|`startDatetime`\|`createdAt`） | 排程 02:00 UTC + `operations_change_sensor` | 無 |
| 10 | `orders` | 水位線窗（`updatedAt`\|`createdAt`） | 排程 02:30 UTC + `authoritative_transaction_change_sensor` | `operation` ∈ {delete, deleted, void, voided} → status `voided`（`mapping.py:977-981`） |
| 11 | `transaction` | 水位線窗 | **僅手動** job | 同上 |
| 12 | `trade` | 水位線窗 | **僅手動** job | 同上 |
| 13 | `ai_revenue_stats` | 水位線窗（`updatedAt`\|`date`\|`createdAt`） | 排程 03:00 UTC | 無 |
| 14 | `ai_consumer_kmeans_v1` | 水位線窗（`updatedAt`\|`runDate`\|`createdAt`） | 排程 05:00 UTC | 無 |
| 15 | `device_log` | 水位線窗 | **僅手動** job | 無；為 `core.machine_status_events` 的唯一生產來源（§3.4） |

排程與觸發器的權威清單在 `apps/data_platform/definitions.py:350-364`：6 個 `ScheduleDefinition`、3 個 `sensor`。全部資產為 `DailyPartitionsDefinition`，排程一律以**前一日** partition 執行（`_previous_day_partition`）。

**`source owner` 的誠實狀態**：repo 內**沒有**逐 collection 記載的來源系統 owner。可查到的只有兩個層級的宣告——RTM 的 `Data Platform Owner`（需求層級），與 `ProviderMetadata` 的欄位預設值 `owner="Data Architect"` / `integration_owner="Connector Owner"`（`modules/external_data/providers/weather_demographics.py:167-168`，且僅作用於外部 provider）。**這是一個未填的空格，不是一個已知答案**，列入 §7。

**`latency SLA` 的誠實狀態**：repo 內**沒有**任何一個內部上游宣告 latency SLA。可查到的只有 cadence（上表）。實際的變更偵測下限是 3 個 sensor 的 `minimum_interval_seconds=900`（15 分鐘）；上限是 4 個 manual-only kinds（`member`、`transaction`、`trade`、`device_log`），它們既無排程也無 sensor，延遲取決於人。同樣列入 §7。

### 3.2 change sensor 的實際機制

`apps/data_platform/definitions.py:252-256` → `source.py:433-434`：

```python
def has_changes_since(self, kind: SourceKind, since: datetime) -> bool:
    return bool(self._database[kind.value].count_documents({"updatedAt": {"$gt": since}}))
```

也就是說，平台**已經有一條輪詢式的變更偵測路徑**，游標持久化在 Dagster sensor cursor，偵測到變更才排 run。這是理解 §4.2 的關鍵：CDC 要贏的不是「有沒有變更偵測」，而是「15 分鐘輪詢不夠快」——而後者目前沒有任何需求支撐。

### 3.3 生產外部來源（6 個 provider）

`modules/external_data/connectors/provider_registry.py::PROVIDER_REGISTRY`：

| provider_id | acquisition | 契約 | endpoint / credential 環境變數 | 生產可用 |
|---|---|---|---|---|
| `listing.partner_feed` | feed | `listing_raw_snapshot` | `ODP_LISTING_PROVIDER_FEED_URL` / `ODP_LISTING_PROVIDER_API_KEY` | 是 |
| `poi.commercial_api` | api | `poi_snapshot` | `ODP_POI_PROVIDER_URL` / `ODP_POI_PROVIDER_API_KEY` | 是 |
| `geocode.primary_api` | api | `geocode_result_snapshot` | `ODP_GEOCODE_PROVIDER_URL` / `ODP_GEOCODE_PROVIDER_API_KEY` | 是 |
| `admin_boundary.official_dataset` | public_dataset | `admin_boundary_snapshot` | `ODP_ADMIN_BOUNDARY_PROVIDER_URL` / `ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN`（bearer） | 是 |
| `competitor.manual_source` | manual | `competitor_store_snapshot` | `ODP_COMPETITOR_MANUAL_SOURCE_ATTESTATION`（人工具結） | **否**（`allowed_in_production=False`） |
| `store_opening_authority` | manual / official_registry | `store_opening_authority_snapshot` | `ODP_STORE_OPENING_AUTHORITY_ATTESTATION` | 是 |

另有兩個走獨立 registry 的 provider（`modules/external_data/providers/weather_demographics.py`）：`weather.live_api`（`ODP_WEATHER_PROVIDER_URL`）與 `demographics.live_api`（`ODP_DEMOGRAPHICS_PROVIDER_URL`）。這兩者**有** fixture 實作（`FixtureWeatherProvider` / `FixtureDemographicsProvider`，從 `tests/fixtures/source_data/external/*.valid.json` 載入），但 registry 在生產環境**fail-closed 拒絕註冊**它們：

```python
if self.deploy_env in PRODUCTION_ENVIRONMENTS and getattr(provider, "fixture_only", False):
    raise ProviderConfigurationError(..., code="fixture_forbidden")
```
（`weather_demographics.py:528-536`）

所以 weather/demographics **不構成**「以 fixture 當 production source」。這一點是刻意複查的，因為它正好落在本 task 的 acceptance 紅線上。

**外部側的 change semantics 全部是快照替換**：每次抓取取得一份完整觀測，沒有任何 provider 提供變更日誌或增量端點。延遲面：`ExternalFetchJobSpec` 的預設值為 `interval=1h`、`freshness_sla=24h`，並據此標記 `FRESH`/`STALE`（`modules/external_data/workers/scheduled_fetch.py:118-120, 522`）。**per-provider 的實際值在註冊時才提供，repo 內沒有宣告**——同樣是 §7 的空格，不是答案。

### 3.4 `event_stream`：已宣告但生產無生產者

`machine_status_event` 契約宣告 `integration_mode: event_stream` + `envelope: event`，是 registry 裡唯一的事件串流契約。但在生產，`core.machine_status_events` 的資料**來自 `SourceKind.DEVICE_LOG`，走的是同一條批次落地路徑**：

```python
elif source_kind is SourceKind.DEVICE_LOG:
    projection = project_machine_status_event(envelope, lookup, self._status_contract)
    self._upsert_machine_status_event(connection, envelope, projection)
```
（`apps/data_platform/store.py:394-401`）

而 `device_log` 是 `_window_query` 的水位線窗讀取（§3.1 第 15 列）。**生產環境沒有任何事件串流消費者**：全樹沒有 broker client、沒有 topic 訂閱、沒有 consumer group。

這不改變 CDC 的判定（CDC 與 event stream 是不同的東西），但它讓「五個 mode 有四個滿足、只差 CDC」這個說法**不成立**。誠實的說法是：**兩個缺席（CDC 與 event），成因不同**。CDC 缺席是因為沒有需求；event 缺席是因為需求存在（契約已寫）但生產路徑走了批次。

### 3.5 僅存在於契約與 fixture 的宣告來源（排除）

`packages/schemas/source_contracts/internal/` 的 8 份契約各自宣告了一個 `source_system`。其中 5 個代碼在全樹**只出現在契約檔與 fixture 檔**，沒有任何生產連線、排程或資料表：

| `source_system` | 契約 | 全樹出現位置 |
|---|---|---|
| `iot_core` | `machine_master_snapshot`, `machine_cycle_event`, `machine_status_event` | 契約 3 份 + fixture 2 份 |
| `crm` | `customer_service_case_event` | 契約 1 份 |
| `store_master` | `store_master_snapshot` | 契約 1 份 |
| `pricing` | `price_schedule_snapshot` | 契約 1 份（`services/` 下的 `pricing` 命中為不同語意） |
| `ops` | `maintenance_work_order_event` | 契約 1 份（其餘 `ops` 命中為 opsboard / DecisionPolicy 等不同語意） |

（第 6 個代碼 `payment` 確實出現在 `apps/data_platform/`，但那是 orders 文件裡的 `payment` 欄位，不是一個獨立的來源系統。）

**這 5 個都不是生產來源系統**，依 acceptance 不得作為「有 CDC 需求」的依據。它們代表的是「架構保留了位置」——`ODAY_PLUS_EXECUTION_RTM.md:65` 的備註欄自述為 "Foundation architecture reserves all ingestion modes"——而不是「有這些系統在跑」。

同時記錄一項 registry 不一致：`store_opening_authority` provider 引用 `source_contract_id="store_opening_authority_snapshot"`，但該契約檔**不存在**於 `packages/schemas/source_contracts/external/`，也不在 `index.json` 的 15 筆 contracts 內。這與 CDC 無關，列入 §7。

---

## 4. 依四個判準逐項判定

### 4.1 Change log — 不需要

CDC 的必要條件是上游提供變更日誌（MongoDB oplog / change stream、PostgreSQL WAL 等）。

- 唯一的生產上游 `fongniao_prod` 目前**沒有任何程式讀取 oplog 或 change stream**。全樹對 `change stream` / `changeStream` / `oplog` / `debezium` / `replica` 在生產程式碼的命中為 0（§8 收據 R4）。
- 變更**型別**本身並不缺載體：batch envelope 已經帶 `source_event_type`（enum 含 `snapshot, create, update, delete, void, refund, correction`）、`is_deleted`（邏輯刪除旗標）、`source_updated_at`（水位線）、`idempotency_key`（重放去重）、`payload_hash`（內容變更偵測）——`packages/schemas/source_contracts/envelopes/batch_envelope.json`。

也就是說，**change-log 語意的表達能力已經存在，而且是走 batch envelope 表達的**。缺的不是格式，是一個提供變更日誌的上游需求。**判定：不需要。**

### 4.2 Latency — 不需要

- 目前最快的變更偵測是 3 個 sensor 的 15 分鐘輪詢下限（§3.2）。
- 最慢的是 4 個 manual-only kinds，由人觸發。
- 下游消費端是**日粒度**資產：所有 asset 都是 `DailyPartitionsDefinition`，排程一律跑前一日 partition。

換句話說，**決策消費端的時間解析度是「日」，而供給端已經能做到 15 分鐘**。供給比需求快了兩個數量級。repo 內沒有任何一條寫下來的 sub-15-minute 需求。在有人寫下這樣的需求之前，CDC 的延遲優勢**沒有需求可對應**。**判定：不需要。**

（若之後真的出現近即時需求，正確的第一步是把 4 個 manual-only kinds 排上程、把 sensor 覆蓋到它們——那比引入 CDC 便宜得多，而且會先暴露真正的瓶頸在哪。）

### 4.3 Ordering — 不需要

現行讀取以 `_id` 升冪排序（`sort("_id", 1)`）並以 `_id` 游標續讀，冪等鍵是 `source_snapshot_id`（由 `(kind, source_id, content_sha256)` 決定，`identifiers.py::snapshot_id_for_content`），落地全部是 upsert。

因此**順序不是正確性的前提**：同一批資料重放兩次得到同一個結果，亂序到達也不會產生不同終態。CDC 提供的全域變更順序**沒有對應的正確性需求**。**判定：不需要。**

### 4.4 Delete semantics — 需要修，但 CDC 不是解法

這是四項中唯一需要仔細看的，結論也最反直覺。

**上游的邏輯刪除已經傳達得到。** 交易類的刪除／作廢是以欄位值送達的，不是以記錄消失的方式：

```python
def _transaction_status(document: dict[str, Any]) -> str:
    operation = str(document.get("operation") or "").strip().lower()
    if operation in {"refund", "refunded"}:
        return "refunded"
    if operation in {"delete", "deleted", "void", "voided"}:
        return "voided"
```
（`apps/data_platform/mapping.py:977-981`）

`merchant` / `place` / `device` 的 `operation` 同理，映射為 lifecycle status。這些變更會抬升 `updatedAt`，因此**水位線讀取看得到**。

**上游的實體刪除，偵測能力分兩半。** 8 個 snapshot kinds 每次全量讀，原理上可由快照差集推出消失的記錄；7 個水位線 kinds 則偵測不到——實體刪除不會抬升 `updatedAt`，被刪掉的文件不會出現在任何時間窗裡。這是水位線增量的經典盲點，**也是唯一一個 CDC 在理論上有話可說的地方**。

**但這個盲點目前不構成 CDC 的理由，因為下游根本沒有刪除路徑。** 落地層對 `delete`（不分大小寫）的命中數是 **0**：

- `apps/data_platform/store.py` — 0（§8 收據 R2）
- `apps/data_platform/pipeline.py` — 0
- `apps/data_platform/sql/control_schema.sql` — 無 `DELETE`、無 tombstone、無 `is_deleted` 欄位

所有寫入都是 `INSERT ... ON CONFLICT ... DO UPDATE` 或 `DO NOTHING`；`reconcile()`（`store.py:1074`）做的是**筆數與校驗和對帳**，不是刪除。

因此：**即使今天接上 CDC，也不會修好任何事。** 一條帶 `delete` op 的 CDC 訊息送進一個只會 upsert 的 sink，終態和現在完全一樣——那筆記錄留在下游。缺的是 sink 的墓碑／失效路徑，不是來源的變更日誌。

順序也很重要：**必須先有 sink 的刪除語意，CDC 才可能有意義**。反過來做，是花一筆可觀的成本買一個下游接不住的訊號。

**判定：CDC 不需要；但下游刪除傳播是一個獨立缺陷，另立追蹤（§7）。**

---

## 5. 條件式：若未來確實需要 CDC，真實的 contract 與 credential boundary 是什麼

本節不是提案，是把「將來有人要做時必須先回答什麼」寫下來，避免下一輪又從零查一次。

**真實的 log/stream contract**（唯一生產上游為 MongoDB，因此只有一個選項族）：

- 介面：MongoDB Change Streams（`db.collection.watch()`）或直讀 oplog（`local.oplog.rs`）。
- 前提條件：來源必須是 **replica set 或 sharded cluster**——單機 mongod 沒有 oplog，change stream 不可用。此事實**不在 repo 內**（`ODP_DATA_MONGO_URI` 是執行時設定），必須先向資料庫擁有者確認，不能從程式碼推論。
- 續讀契約：resume token（`_data`）必須持久化。合理位置是 `data_plane` control schema，與現行 `checkpoints` 表並列。
- **失效模式必須先設計**：resume token 超出 oplog 保留窗即永久失效，此時唯一出路是退回全量重讀。也就是說，**CDC 不能取代批次路徑，只能疊在它上面**——現行的全量快照讀取必須保留為 fallback。任何宣稱「改用 CDC 之後就不需要批次」的方案是錯的。
- Sink 前提：§4.4 的刪除路徑必須先存在。

**production credential boundary**（這是最關鍵、也最容易被略過的一項）：

現行憑證的邊界是**窄的**，而且是多重強制的：

| 現行 | 邊界 |
|---|---|
| 讀取範圍 | 每次 `find` 都帶投影（`SOURCE_PROJECTIONS`），非全欄位 |
| 資料量 | `max_records_per_run` ≤ 5,000,000，`batch_size` ∈ [100, 20000] |
| 資料庫 | 強制恰為 `fongniao_prod` |
| 環境 | 強制 `production`，且 host 不得為本機 |
| 權限 | 具名 collection 的 `find` |

Change stream **無法沿用這個邊界**：

- 需要 `changeStream` 權限（`readAnyDatabase` 或等價的 cluster-level 授權），這比具名 collection 的 `find` **更大**。
- change stream 傳回的是**完整的變更文件**，投影只能在客戶端事後套用——也就是說，`_minimize_device_log`（`source.py:243`）這類在**讀取邊界上**做的欄位最小化會退化成在**應用層**做。對含個資的 `member` 與 `device_log` 兩個 kind，這是實質的隱私邊界退步，不只是設定變更。
- 串流沒有 `max_records_per_run` 這種天然的筆數上界，現行的量體防護需要換一套設計。

**因此：任何 CDC 提案必須先取得憑證擁有者對「擴大生產憑證權限面」的明示核准，不能沿用現行的 `ODP_DATA_MONGO_URI`。** 這是一個安全決策，不是一個工程決策，而且應該在寫任何程式碼之前完成。

---

## 6. 連到 formal disposition 流程

**現有機制**：`delivery_toolchain/governance/set_valued_requirements.json` + `delivery_toolchain/governance/check_requirement_members.py`。這是 repo 內處置「列了 N 項但只做到 M 項」需求的唯一治理面，`ODP-FR-NET-002` 的 `SEQUENCING`／`DILUTION` 成員已經示範了完整寫法（`status: absent` + `DECIDED <日期>` + 理由 + reopen 條件）。

**現況**：`ODP-FR-INT-001` **不在**該 manifest 內（§8 收據 R5，0 命中）。目前 manifest 收錄 32 個成員（24 satisfied / 8 absent）。

**處置所需的條目**（本文件是決策輸入，條目本身由 requirement owner 定案）：

- `id`: `ODP-FR-INT-001`
- `member_count`: 5（BATCH / API / FILE / EVENT / CDC）
- 依本次查證，誠實的成員狀態是 **3 satisfied、2 absent**：

| 成員 | 建議狀態 | 依據 |
|---|---|---|
| BATCH | satisfied | `apps/data_platform/source.py::SNAPSHOT_SOURCE_KINDS`（全量快照）與 `MongoSource._window_query`（增量） |
| API | satisfied | `modules/external_data/connectors/provider_registry.py::PROVIDER_REGISTRY` |
| FILE | satisfied | `modules/external_data/application/xlsx_import.py::XlsxCommitReceipt`；另有 `feed`／`public_dataset` 取得方式 |
| EVENT | **absent** | 契約存在但生產無生產者；`core.machine_status_events` 實由 `device_log` 批次落地（`store.py:394-401`）。§3.4 |
| CDC | **absent** | 本文件 §4。note 應記 `DECIDED 2026-09-03` |

**兩個執行上的限制，先寫下來免得處置時才發現**：

1. `check_requirement_members.py:109-125` 規定 `evidence` 只接受 `relative/path.py::Symbol` 或 `path.py::Class.member`，**且 `satisfied` 的符號必須真的解析得到**。這代表 `packages/schemas/source_contracts/index.json` **不能**直接當證據——registry 是 JSON，不是 Python 符號。上表的三個 satisfied 成員都已改指向可解析的 Python 符號。
2. 該 checker 只有 `satisfied` / `absent` 兩個狀態，**沒有 `decided-not-doing`**（`VALID_STATUSES` 於 `check_requirement_members.py:53`）。`ODP_REMEDIATION_PLAN_2026-09-03.md` 已經點名這個缺口。因此 CDC 的處置只能寫成 `absent` + note，**與「還沒做」在機器上無法區分**，差別只在 note 的文字。若要讓「決定不做」成為可稽核的狀態而非一段散文，需要獨立的 requirement amendment／waiver schema——那超出本 task 範圍，屬 `ODP_REMEDIATION_PLAN` 第 6 批的「仍需補的控制」。

**本 task 沒有修改該 manifest。** 新增或變更受治理需求的成員，需要 requirement owner（RTM 記為 `Data Platform Owner`）與 reviewer 走 amendment 流程；由查證者單方面寫入，等於用證據蒐集的身分做了需求變更的決定。本文件提供的是那個決定所需的輸入。

---

## 7. 併同發現、需另立追蹤的項目

依嚴重度排序。前兩項有實質後果，後三項是未填的空格。

1. **下游刪除傳播缺席**（實質缺陷）。落地層無任何刪除或墓碑路徑（§4.4）。後果：上游實體刪除的記錄會**永久留在** PostgreSQL，且無告警——`reconcile()` 對帳的是本批筆數與校驗和，不會發現「上游少了一筆、下游還在」。對 8 個 snapshot kinds 尤其明確：全量讀已經拿得到「這筆不在了」的事實，卻沒有任何程式使用它。**這個缺陷不是 CDC 能解的，修它也不需要 CDC。**
2. **`event_stream` 宣告與生產路徑不一致**（§3.4）。契約宣告事件串流，生產走批次。需要的是二選一：補生產者，或把契約改成批次並走 §6 的處置。維持現狀的風險與 `root_cause`／`PARTIAL` 同型——讀契約的人以為有這個能力。
3. **來源 owner 未逐 upstream 記載**（§3.1）。15 個內部 collection 與 8 個外部 provider 都沒有具名的來源系統 owner，可查到的只有需求層級的 `Data Platform Owner` 與兩個欄位預設值。上游 schema 變更時沒有人可以通知。
4. **latency SLA 未宣告**（§3.1、§3.3）。內部完全沒有；外部只有 `freshness_sla` 的**預設值** 24 小時，per-provider 實際值在註冊時才提供、repo 內查不到。目前 4 個 manual-only kinds 的實際延遲取決於人，沒有上界。
5. **`store_opening_authority_snapshot` 契約缺漏**（§3.5）。provider registry 引用了一個不在 source-contract registry 內的契約 id。

---

## 8. 可重現的驗證收據

於 base `75d25f653aa12c21a3f9627f29af2ed4def73153`、repo 根目錄執行。**下列六條全部實際執行過**，記載的是實測輸出而非預期輸出。

**R1 — registry 的 mode taxonomy 不含 CDC，共 15 份契約**

```bash
python3 -c "import json; d=json.load(open('packages/schemas/source_contracts/index.json')); print(len(d['contracts']), d['integration_modes'])"
```
實測：`15 ['batch_snapshot', 'incremental_batch', 'event_stream', 'backfill', 'api_lookup']`

**R2 — 落地層沒有任何刪除路徑**

```bash
grep -c -i delete apps/data_platform/store.py apps/data_platform/pipeline.py
```
實測：兩者皆為 `0`。

**R3 — 全樹的 CDC 命中皆為 docstring／註解，非實作**

```bash
grep -rn -i "cdc" --include=*.py --include=*.sql --include=*.ts --include=*.yaml --include=*.yml . | grep -v node_modules | grep -v cdc5e5b
```
實測 3 筆，皆非實作：`shared/domain/models.py:119`（docstring）、`tests/contract/test_ingestion_contracts.py:10` 與 `:119`（docstring／註解標題）。
（`scripts/test_ai_status.py:5479` 的 `cdc5e5b…` 是一個 git SHA 的子字串假陽性，已於上式排除。此類子字串假陽性正是 `ODP_FR_VERIFICATION_112` 附錄 B 點名的主要風險。）

**R4 — 生產程式碼無 change stream／oplog 消費者**

```bash
grep -rn -i "change stream\|changeStream\|oplog\|debezium" --include=*.py apps/ modules/ shared/ services/
```
實測：無輸出。

**R5 — `ODP-FR-INT-001` 尚未進入治理 manifest**

```bash
grep -c "INT-001" delivery_toolchain/governance/set_valued_requirements.json
```
實測：`0`。

**R6 — mode taxonomy 被相等斷言釘住**

```bash
uv run --frozen --python 3.12 pytest tests/contract/test_ingestion_contracts.py -q
```
實測結果：**91 passed**，exit code `0`。

`--python 3.12` 是必要的，不是風格選擇：預設解譯器為 CPython 3.14 時，`uv` 會因 `pgserver==0.1.4` 沒有 `cp314` wheel 而在建立環境階段就失敗（`error: Distribution 'pgserver==0.1.4' ... only has wheels with the following Python ABI tag: cp312`），根本跑不到測試。

斷言主體見 `tests/contract/test_ingestion_contracts.py:127-131`：`assert {...} == set(index["integration_modes"])`。因為是集合相等而非包含，把 `cdc` 加進 registry 而不改測試會使此測試失敗。

---

## 附註：本次查證的限制

- **`fongniao_prod` 的實際狀態不在 repo 內。** 本文件對上游的所有判斷都來自讀取器程式碼與設定驗證，不是來自對生產資料庫的觀測。具體而言，「該 MongoDB 是否為 replica set」（決定 change stream 是否可用）、以及各 collection 的實際刪除頻率，**repo 無法回答**，需要資料庫擁有者提供。§4.4 的判定不依賴這兩者——它依賴的是 sink 端的缺席，那是 repo 內的事實。
- **子字串假陽性已逐條回退到符號。** 依 `ODP_FR_VERIFICATION_112` 附錄 B 的教訓，本文件每一條「存在／不存在」的判定都回到實際的 dataclass 欄位、函式呼叫點或 SQL 敘述，不採計命中數；R3 的 SHA 假陽性即為一例。
- **未截斷輸出。** 判定「不存在」的四條（CDC 實作、change stream 消費者、落地層刪除、`INT-001` 治理條目）都以計數或完整輸出確認，未使用 `head` 截斷。

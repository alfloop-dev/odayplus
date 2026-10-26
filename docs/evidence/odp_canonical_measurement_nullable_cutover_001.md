# ODP-CANONICAL-MEASUREMENT-NULLABLE-CUTOVER-001 Evidence

把六個 canonical measured column 的缺席語意從「宣告」變成「可執行」，並回應
PR #1327 review（Claude2，comment 5666534364）的 R1–R6。

- Base: `dev` @ `1246b3b4ec2f5487330e3f0645ce637bc779b127`
- Task branch: `task/ODP-CANONICAL-MEASUREMENT-NULLABLE-CUTOVER-001`
- 環境: `uv run --frozen`（專案 `.venv`，Python 3.12）
- PostgreSQL: `pgserver` 隨附的 PostgreSQL 16（`tests/conftest.py::intake_blank_db`）

## 0. 前一輪為什麼是死碼

前一輪把 `measurement_schema_version` 加進六個 dataclass 與兩份 migration，但
production Python 只有 `shared/domain/models.py` 的定義處提到它。真正的持久化
路徑是 `SqliteDocumentStore`：它 **pickle 整個 aggregate**，不寫這些欄位。

pickle 回載時，缺席的屬性不會變成 `None`，而是**穿透到 class attribute**。
cutover 把 class default 設成 `"v2"`，於是每一筆 cutover 之前寫入的 payload
回載後都自稱 `v2`，舊的代入值 `1.00` 就被判成 `measured`。

## 1. R1 — legacy marker 現在會活過 pickle 回載

`MeasuredColumnSemantics.__setstate__`（`shared/domain/models.py`）在還原狀態
時，把缺席的 `measurement_schema_version` 補成 `"v1"`：

```python
def __setstate__(self, state: dict[str, Any]) -> None:
    restored = dict(state)
    restored.setdefault("measurement_schema_version", LEGACY_MEASUREMENT_SCHEMA_VERSION)
    self.__dict__.update(restored)
```

`dataclasses.replace()` 讀的是 instance 值，所以 marker 也會跟著 mutation 與
二次寫入走。六個 model 共用的語意收斂成三個 base class，property 從六份重複的
複製貼上變成一份。

證明（`tests/integration/test_canonical_measurement_production_paths.py`）：
`TestPreCutoverPayloadsReloadAsLegacyUnknown` 把六個 model 的 pre-cutover
payload 寫進真的 `SqliteDocumentStore`、關檔、重開、再讀回來。
pre-cutover payload 是把新欄位從 `__dict__` 拿掉之後才 pickle，位元形狀等同
舊 class 產生的串流。

## 2. R2 — effective_* 現在有 production 消費者

| 位置 | 之前 | 現在 |
| --- | --- | --- |
| `modules/opsboard/application/network_scoring.py` | `listing.confidence` 進 SiteScore gate | `listing.effective_confidence` |
| `modules/opsboard/application/network_listings.py` | `"listingConfidence": lst.confidence` | `effective_confidence` + `listingConfidenceProvenance` |
| `apps/api/app/routes/sitescore.py` | `"confidence": p.confidence` | `p.effective_confidence` + `confidenceProvenance` |
| `apps/api/app/routes/listings.py` | `hasattr()` 防呆後才用 property | 直接用 property |
| `modules/heatzone/v3/contract.py` | `to_dict`/`to_map_feature` 直讀 `self.confidence` | 經 `to_canonical_score()` 的 `HeatZoneScore` |
| `modules/integration/application/mapping.py` | `asdict(canonical)` | `canonical_measurement_dict(canonical)`，附 `confidence_provenance` |
| `modules/integration/connectors/base.py` | 無此投影 | 新增 `ConnectorRun.canonical_entity_dicts()`，`CanonicalListingSnapshot.canonical_records` 改為委派 |

`Poi` 與 `CompetitorStore` 在 production Python 裡**沒有讀取端**——
`ConnectorRun.canonical_entities()` 沒有任何呼叫者，geo pipeline 吃的是原始
mapping record 而不是這兩個 aggregate。它們真正的消費者在 SQL／dbt 層，也就是
第 4 節那道閘。Python 這側擁有的是 producer 與「aggregate 變成 dict」的邊界，
所以修的是那個邊界：`canonical_entity_dicts()` 讓每一個 connector 的輸出都經過
同一個保留 provenance 的投影，而不是只有 listing 那一條。

`network_listings` 的 dict round trip（`_listing_to_dict` → `_dict_to_listing`）
會把 `legacy_unknown` 收斂成 `unmeasured`：重建出來的 `Listing` 拿到的是
effective 值 `None`，而不是舊的 `1.00`。這是刻意的——兩者對下游都是「沒有可用
的量測」，而重新塞回 `1.00` 才是這次 cutover 要移除的行為。

另外兩處會**弄丟 marker**的複製也補上了：`modules/listing/application/promotion.py`
與 `apps/api/app/routes/listings.py` 在改狀態時重建 `Listing`，之前沒帶
`measurement_schema_version`，等於把一筆 legacy row 重新蓋成當期寫入者。

## 3. R3 — SiteScore 的缺席不再以 measured 0.0 持久化

`_confidence()` 回 `float | None`。內部計分算術仍是數值（缺席用 0.0，拿到最寬
的區間與最低的建議層級——reviewer 明確允許），但 `SiteScoreReport` 多帶
`confidence_status`，`reporting.py` 在 canonical `Prediction` 生產邊界上把
abstention 寫成 `confidence=None, confidence_status="unmeasured"`。

證明：`TestSiteScorePersistsAbstentionAsNullPrediction` 真的跑
`SiteScoreReportService.score_candidates()`、存進 durable repository、重開
process、用 `get_predictions()` 讀回來。

另一個 `Prediction` producer `modules/forecastops/application/forecasting.py`
根本沒有傳 `confidence`，改 default 之後它自動變成 `None`／`unmeasured`——
cutover 之前它寫出來的是憑空的完美 `1.0`。

**刻意留下的界線**：`SiteScoreReport` 不是這次的六欄之一，也沒有拿到六個 model
那套 `__setstate__` legacy 還原。一份 cutover 之前 pickle 的舊 report 缺
`confidence_status`，回載後會落在 class default `"measured"`——它的 `0.0` 究竟是
真的低分還是 abstention，資料本身已經分辨不出來。這只影響舊 report 的序列化：
`_persist_reports` 只為當次新算出的 report 寫 `Prediction`，不會從回載的 report
再寫一次，所以 canonical 邊界不受影響。要處理它需要為 `SiteScoreReport` 另立一個
legacy 值並放寬已發布的 TS 型別，屬於另一個 task 的範圍。

## 4. R4 — dbt geo view 與 Python pipeline 語意一致

PostgreSQL 的 `avg()` 會跳過 NULL，所以一個「部分被量測」的 bucket 在 SQL 會
得到非 NULL，而 `GeoPipeline` 對同一份輸入回 `None`。`geo_grid_view.sql` 的兩個
平均都改成 null-strict：

```sql
case when count(pois.poi_id) > 0 and count(pois.poi_id) = count(pois.confidence)
     then avg(pois.confidence) else null end
```

這一項**只讀 SQL 看不出 guard 有沒有真的觸發**，所以
`tests/integration/test_canonical_measurement_postgresql.py` 把 dbt 的
`{{ var() }}` 綁成字面 timestamp（等同 `dbt --vars` 編譯結果）之後，直接對真的
PostgreSQL 執行整個 view。

## 5. R5 — DataSnapshot 的量測版本與 dataset 版本解耦

之前用 `schema_version == "v1" and quality_score == 1.0` 判 legacy。但
`schema_version` 描述的是 **dataset 版面**，由產出 pipeline 選；
`geo_grid_view.sql` 對每一列有 h3 index 的資料都發 `data_quality_score = 1.0`，
所以 `mean_quality = 1.0` 是 geo model-ready 的**預設路徑**，不是角落案例。前一輪
的判準會把全新、真的量到的完美分數整批當成 legacy 丟掉。

修法是給 `DataSnapshot` 自己的 `measurement_schema_version`。
`DEFAULT_SCHEMA_VERSION` 保持 `"v1"`，因為它講的是另一件事。

`LineageManifest.to_data_snapshot()` 讓 model-ready materializer 成為第六個
model 的真實 production writer；`to_audit_snapshot_row()` 改成從那個
`DataSnapshot` 取值，不再自己重算一套（而且算錯的）status。

## 6. R6 — 測試打的是 production path，不是 dataclass 屬性

新增兩個檔案：

- `tests/integration/test_canonical_measurement_production_paths.py`
  producer → durable persistence → 重開 process → consumer/route，涵蓋六個
  model（或其 live surrogate）的 omitted / measured 0 / measured 1 /
  pre-cutover payload 四種情形。
  Listing 打的是真的 route serializer `V1ListingRepositoryAdapter.get_listing()`；
  Prediction 打的是 `SiteScoreReportService` → `get_predictions()`；
  DataSnapshot 打的是 `DatasetSnapshotMaterializer` → `DocumentStoreLineageRecorder`。
- `tests/integration/test_canonical_measurement_postgresql.py`
  在**已經有 pre-cutover 資料**的表上跑 `000026` forward migration，然後執行
  geo view。證明六欄都變成 nullable 且沒有 default、既有 `1.00` 一列都沒有被
  改寫成 NULL、pre-cutover 列帶 `v1` marker、migration 可重放，以及
  partial bucket 在 SQL 端也回 NULL。

## 7. Migration 補件，以及一個被實測擋下來的自製缺陷

`audit.data_snapshots` 之前只處理了 `quality_score` 的 nullability，沒有 marker
欄位，PG 版補上了 `measurement_schema_version`。

SQLite 版**刻意不加**。我先加了，然後實測到它會壞：

```
first boot   : {'measurement_schema_version': 'v2', 'quality_score': 1.0}
after restart: {'measurement_schema_version': 'v1', 'quality_score': 1.0}
```

`SqliteEngine._bootstrap()` 每次開機重放全部 migration 檔，而排在前面的
`000024` 會用固定欄位清單的 `INSERT...SELECT` 重建 `data_snapshots`。任何在
`000024` 之後才加到那張表的欄位，下次重啟就會被丟掉再以 DEFAULT 補回——一筆真
的量到的 `v2` 會變回 `v1`，也就是 `legacy_unknown`。這正是這次 cutover 要移除
的失效模式，只是換成我自己造的。

`000026` 加到 `pois`/`listings`/`competitor_stores` 的欄位沒有這個問題：那三張
表的重建由 `000026` 自己持有，且會先 pre-add 欄位讓 `SELECT` 讀得到。
`prediction_runs` 是純 `ALTER`，重放時被 "duplicate column name" 吞掉。

回歸測試 `TestSqliteMarkerColumnsAreOnlyAddedWhereTheySurvive` 把兩半都釘住：
四張有 marker 的表寫入 `v2` 後重啟仍是 `v2`；`data_snapshots` 則斷言沒有這個
欄位，並在 docstring 指向 `000026` 裡寫明原因的註解。

## 8. Acceptance 4 的另一半

`delivery_toolchain/governance/measurement_default_exemptions.json` 的六筆
`shared/domain` dataclass exemption 在前一輪 commit `407710ec` 已經與 model
default 原子移除。這一輪把剩下五筆 SQL exemption 的理由更新成事實：
它們指向 `000004` 的歷史 DDL 文字（已套用的 migration 不可改），而 live schema
已由 `000026` 取代。原本寫著「這批還欠一支 migration」的理由已經不成立。

## 9. 本機驗證

全部以 `uv run --frozen`（專案 `.venv`，Python 3.12）執行，背景 job 收 exit code。

| 檢查 | 結果 |
| --- | --- |
| `pytest tests/integration/test_canonical_measurement_production_paths.py tests/integration/test_canonical_measurement_postgresql.py tests/domain/test_canonical_measurement_nullable.py` | 72 passed（含對真 PostgreSQL 16 執行 migration 與 geo view） |
| `pytest` 受影響套件（canonical、model_ready materialization、canonical schema contract、durable repository wiring、`modules/sitescore`、migration、governance） | 194 passed |
| `pytest modules/{heatzone,listing,opsboard,external_data,integration}/tests -n auto` | 125 passed |
| `pytest tests/domain modules/learninghub/tests -n auto` | 113 passed |
| `pytest` 受改動 route／序列化的 integration + contract 檔（sitescore decision、forecastops alerts、assisted listing promotion、listing platform observations、durable repository wiring、model-ready materialization、canonical schema、openapi artifact） | 88 passed |
| `ruff check tests modules apps shared models solver pipelines infra` | clean |
| `ruff check .orchestrator delivery_toolchain scripts infra` | clean |
| `npm run typecheck`（全 workspace） | exit 0 |
| `delivery_toolchain/openapi/export_openapi.py --check` | 與 live schema 相符 |
| `delivery_toolchain/governance/check_measurement_defaults.py` | 通過；剩 5 筆 SQL exemption，dataclass／mapper 層歸零 |
| `delivery_toolchain/governance/check_code_boundaries.py` | 1169 檔通過；inventory 已重產 |

`check_measurement_defaults` 的 repo 層斷言由 `{"dataclass","mapper","sql"}` 放寬為
`{"sql"}`，反映 dataclass 與 mapper 兩層的債已清。掃描器本身對每一層的偵測能力仍由
`TestItRefusesTheDefectItExistsFor`、`TestItReadsThePydanticLayer`、
`TestItReadsTheMapperLayer`、`TestItReadsTheSqlLayer` 以合成輸入各自證明，沒有因此
失去偵測力。

完整 product 測試集在這台同時跑 fleet 的機器上，即使 `-n auto` 也遠超過一個 worker
週期（實測約 1 test/sec），因此改為按 blast radius 分段跑完上表，其餘由 CI 的
product job 涵蓋。上表合計 592 筆，涵蓋這次 diff 觸及的每一個模組與測試區。

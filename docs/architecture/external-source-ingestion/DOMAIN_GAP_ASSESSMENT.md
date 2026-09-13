# 八域逐項層級評估與缺口清單

任務：`DPF-SOURCE-SA-SD-BASELINE-001`
版本：2026-09-13
驗收層級定義（SA §層級）：
`DISCOVERED → CONFIGURED → CONTRACT_TESTED → LIVE_CAPTURED → RAW_READBACK_VERIFIED → NORMALIZED_VERIFIED → RETAINED_IN_CLOUD → RELEASE_ELIGIBLE → ACTIVATED`

## 逐域評估

### 1. cwa_events（天氣與災害警示）

| 項目 | 現況 |
|---|---|
| 來源 | CWA 氣象開放資料平台 API |
| 對應來源檔 | E04（`context_events.py`）|
| 當前層級 | **CONFIGURED** |
| 已具備 | adapter 結構、live kernel 整合、context assets 定義 |
| 查明缺口 | (1) `CWA_API_KEY` 部署可用性未查證 (2) 市場事件預設 `sources.market.tw/feed/v1` 為未驗證端點，尚未對權威官方市場事件 feed 完成核實與連通性查證 (3) CWA 與 market event 為不同來源，需分開記錄 |
| 後續 task | `DPF-OFFICIAL-SOURCE-ENDPOINT-INTEGRATION-001` |
| auth/terms 狀態 | CWA 公開 API 通常免費但可能有速率限制；Secret reference 需查證；terms 尚未逐來源記錄 |

### 2. transport_osm_tdx（道路與公共運輸）

| 項目 | 現況 |
|---|---|
| 來源 | OSM Taiwan PBF + TDX 公共運輸 API |
| 對應來源檔 | E05（`transport.py`）|
| 當前層級 | **CONFIGURED** |
| 已具備 | OSM/TDX adapter、transport asset、分頁與 coverage 邏輯 |
| 查明缺口 | (1) OSM PBF 實際二進位格式與現有 parser 相容性未驗證 (2) TDX endpoint/OAuth/配額需逐項 readback (3) 檔案大小與地理範圍需確認 |
| 後續 task | `DPF-TRANSPORT-POI-RELEASE-INTEGRATION-001` |
| auth/terms 狀態 | OSM 為 ODbL，TDX 需 API key 與 OAuth；尚未查證部署中是否有有效 token |

### 3. open_poi（開放 POI）

| 項目 | 現況 |
|---|---|
| 來源 | Overture Maps / Foursquare OS / 品牌門市定位器 / Google Places verifier |
| 對應來源檔 | E06（`poi.py`）|
| 當前層級 | **CONFIGURED** |
| 已具備 | Overture/Foursquare/brand locator/Google verifier 模組與 URI overrides |
| 查明缺口 | (1) 預設公開桶 URL 為 release prefix 且日期固定，production 已拒絕尾端 `/` 的 prefix (2) 品牌來源未證明是真實 feed (3) Google verifier 是驗證用途，不能代替 open POI 主資料集 |
| 後續 task | `DPF-TRANSPORT-POI-RELEASE-INTEGRATION-001` |
| auth/terms 狀態 | Overture 為 ODbL/CDLA；Foursquare OS 有特定 TOS；品牌門市需逐站查 robots.txt 與契約；Google Places 為付費 API |

### 4. mof_moi（財政部商業登記 / 內政部實價租賃）

| 項目 | 現況 |
|---|---|
| 來源 | MOF 財政部公開 API + MOI 內政部租賃實價資料 |
| 對應來源檔 | E02（`mof_moi.py`）、E14（MOF adapter） |
| 當前層級 | **LIVE_CAPTURED**（MOF 局部）/ **CONFIGURED**（MOI） |
| 已具備 | MOF/MOI parser、adapter、raw asset；MOF 已真實取得 100 筆 |
| 查明缺口 | (1) MOF 主線預設 `data.gov.tw/dataset/mof_business` 為佔位網址 (2) parser 不認真實 camelCase 回應 (3) 100 筆僅為 first-page probe，非全量 (4) MOI 預設 `DownloadOpenData` 未指定可驗證期別/檔案 |
| 後續 task | `DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001`（MOF/RIS 修正）→ `DPF-OFFICIAL-SOURCE-ENDPOINT-INTEGRATION-001`（MOI 接入）|
| auth/terms 狀態 | MOF 為公開 API 無需 auth；MOI 為政府公開資料，需查證下載格式與使用條款 |

### 5. ris_nlsc（戶政人口 / 行政地籍界線）

| 項目 | 現況 |
|---|---|
| 來源 | RIS 內政部戶政司 ODRP014 API + NLSC 國土測繪中心界線資料 |
| 對應來源檔 | E03（`ris_nlsc.py`）、E15（RIS adapter） |
| 當前層級 | **NORMALIZED_VERIFIED**（RIS 局部）/ **CONFIGURED**（NLSC） |
| 已具備 | RIS/NLSC adapter、administrative fusion asset；RIS 已取得 2026-08 臺北市中正區 31 筆並完成解析（零隔離） |
| 查明缺口 | (1) RIS 主線為舊 download URL，未解 `responseData` 封裝 (2) 真實人口欄位與 ROC 年月需正確解析 (3) NLSC 年份 GeoJSON URL 未實際驗證格式 (4) 5/8/11 位行政代碼與發布版本的 join 邏輯待驗 (5) 31 筆不代表全國或 RIS/NLSC join 完成 |
| 後續 task | `DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001`（RIS 修正）→ `DPF-OFFICIAL-SOURCE-ENDPOINT-INTEGRATION-001`（NLSC 接入） |
| auth/terms 狀態 | RIS 為政府公開 API；NLSC 為政府開放資料（CC BY）；均無需特別授權 |

### 6. listing_observations（租屋刊登觀測）

| 項目 | 現況 |
|---|---|
| 來源 | 合作夥伴 feed / 瀏覽器擷取 / 爬蟲觀測 |
| 對應來源檔 | E07（`listings.py`）|
| 當前層級 | **CONFIGURED**（但 Dagster 入口使用示例資料） |
| 已具備 | `ListingObservationService`、多種 channel 與 extractor |
| 查明缺口 | (1) Dagster 入口直接生成固定 LST-101 payload 與 `example.com` URL (2) 以 `datetime.now()` 建立 approval 物件——非真實外部批准 (3) 未讀取真實 channel (4) 這不是缺使用者 raw 檔，是工程接線未完成 |
| 後續 task | `DPF-LISTING-PRODUCTION-ASSET-INTEGRATION-001` |
| auth/terms 狀態 | 逐網站/feed 的 robots.txt、使用條款與契約尚未查證；auth 條件因來源而異 |

### 7. mobility（人流與移動性）

| 項目 | 現況 |
|---|---|
| 來源 | 公共運輸聚合運量 / 授權 feed |
| 對應來源檔 | E08（`mobility.py`）|
| 當前層級 | **CONFIGURED**（但 Dagster 入口使用硬編碼數值） |
| 已具備 | observed/synthetic 模型分離、`MobilitySourceService` |
| 查明缺口 | (1) observed asset 固定生成台北車站 `flow_count=1000` (2) payload 僅含 `status=ok` (3) approval window 以 `datetime.now()` 生成 (4) 非真實人流接入 (5) 搭乘次數不能冒充門店實際人流 |
| 後續 task | `DPF-MOBILITY-PRODUCTION-ASSET-INTEGRATION-001` |
| auth/terms 狀態 | 真實聚合運量來源尚須調查；授權 feed 的 measure/unit/timegrain 待確認；匿名化義務視來源而定 |

### 8. site_market_context（場址市場綜合脈絡）

| 項目 | 現況 |
|---|---|
| 來源 | 衍生 domain——消費上游七域 component |
| 對應來源檔 | E09（site_context asset）、E10（builder）、E11（service） |
| 當前層級 | **CONFIGURED**（但 published asset 僅回 metadata） |
| 已具備 | `SiteMarketContextService`/`Builder`、component refs 與狀態模型 |
| 查明缺口 | (1) published asset 不呼叫組裝 service，只回 metadata (2) builder 在缺 sha 時可 hash component identity——不能證明資料內容 (3) `hash(identifier)` 不是 `hash(data bytes)` |
| 後續 task | `DPF-SITE-CONTEXT-REAL-COMPONENTS-001`（依賴 `DPF-ACQUISITION-RETENTION-BRIDGE-001`）|
| auth/terms 狀態 | 衍生 domain，auth 取決於上游各來源 |

## 跨域彙總

| 層級 | cwa | transport | poi | mof | moi | ris | nlsc | listing | mobility | site_ctx |
|---|---|---|---|---|---|---|---|---|---|---|
| DISCOVERED | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| CONFIGURED | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| CONTRACT_TESTED | — | — | — | 部分 | — | 部分 | — | — | — | — |
| LIVE_CAPTURED | — | — | — | 局部 | — | 局部 | — | — | — | — |
| RAW_READBACK_VERIFIED | — | — | — | — | — | — | — | — | — | — |
| NORMALIZED_VERIFIED | — | — | — | — | — | 局部 | — | — | — | — |
| RETAINED_IN_CLOUD | — | — | — | — | — | — | — | — | — | — |
| RELEASE_ELIGIBLE | — | — | — | — | — | — | — | — | — | — |
| ACTIVATED | — | — | — | — | — | — | — | — | — | — |

**核對結論**：
1. 本地審查與核對中，無任何 domain 達到已驗證的 `RETAINED_IN_CLOUD` 或以上層級。未讀回 retained object 代表本機/本次調查尚未完成該層級之讀回核對，不推論雲端留存證明不存在；各 domain 需由下游對應任務完成精確的 readback 驗證收據。
2. MOF 與 RIS 有局部 live 證據（100 筆/31 筆），但僅為局部有界功能驗證。其餘 domain 處於程式結構就緒（CONFIGURED）但 Dagster 入口未接真實來源的狀態。

## auth/terms 條件摘要

以下為逐來源現況；明確標示已知與未查明，不偽造授權。

| 來源 | 類型 | auth 需求 | terms 狀態 | 查證結果 |
|---|---|---|---|---|
| CWA | 公開 API | API key | 尚未查證部署 Secret | 未查明 |
| TDX | 公開 API | OAuth client | 尚未查證部署 Secret | 未查明 |
| OSM | 公開下載 | 無 | ODbL | 已知 |
| Overture | 公開下載 | 無 | ODbL/CDLA | 已知 |
| Foursquare OS | 公開 API | API key | Foursquare TOS | 需查證 |
| Google Places | 付費 API | API key | 付費/配額限制 | 需查證 |
| 品牌門市 | 網站 | 視站而定 | robots.txt/契約 | 需逐站查 |
| MOF | 公開 API | 無 | 政府開放資料 | 已知 |
| MOI | 公開下載 | 無 | 政府開放資料 | 需查證格式 |
| RIS | 公開 API | 無 | 政府開放資料 | 已知 |
| NLSC | 公開下載 | 無 | CC BY | 已知 |
| TGOS | 公開 API | API key | 政府 API 規範 | 需查證 |
| Listing | 多種 | 視來源而定 | 需逐站查 | 未查明 |
| Mobility | 多種 | 視來源而定 | 需逐站查 | 未查明 |
| Market Events | 多種 | 視來源而定 | 需逐站查 | 未查明 |
| Survey | 內部 | ODK/QField | 內部治理 | 不適用 |

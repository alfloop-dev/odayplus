# ODP 外部來源接入與八域快照調查報告

日期：2026-09-13 UTC。用途：交由 Supervisor 編排、autoworker 實作；根對話負責調查、SA／SD、任務去重與驗收追蹤。

## 結論與責任

目前不能宣稱八類資料完成串接或完成 release snapshot。也不能把整件事歸因於使用者沒有交 raw snapshot。主線已有 adapter、擷取核心、資料模型與快照 primitive，但部分 Dagster 入口仍使用示例資料、部分端點尚未正確配置，且擷取證據到受治理快照的銜接未完成。這些首先是工程交付缺口。

根對話先前直接修改程式，違反使用者指定的分工。使用者更正後已停止程式修改；修改未提交、未推送、未合併、未部署，只封存給 worker 審查及選擇性接手。不得將封存 patch 當成已通過審查的答案。

## 調查邊界與證據等級

- 主線依據：`alfloop-dev/oday-data-platform@b690a8dfd82fc6d47e2f6d7fdfa9ab05430e41cb`。`source-index.json` 的 E01–E16 列出精確檔案、commit 與內容 SHA256。
- 任務依據：canonical `ai-status.json` 當次讀回；既有 masked PR #63、XR 審查 PR #1312 均保留，不另建替代任務。
- 真實網路證據：MOF 公開 API 100 筆、RIS 2026-08 臺北市中正區 31 筆。原始 bytes 已落地與讀回驗 hash；修改後的 Dagster 入口也各取得成功收據。
- 修改後聚焦測試：150 passed。這證明該工作樹測試通過，不等於主線、完整八域、雲端或部署已完成。
- 未驗證：其他来源本次未做 live acquisition；目前部署的全部 Secret、來源啟用與帳號狀態未全面讀回；不得據此宣稱憑證不存在或任何網址確定不可用。
- GCP：本日早先使用 admin@dev.cctech-support.com 的驗證遇到 token 需重新驗證；本報告未重做 login，也沒有把它當成全部工程不能做的原因。

## 八類資料逐項分析

| Domain | 已存在的能力 | 查明缺口／待驗證內容 | 工程推進方式 | 完成的實質證據 |
|---|---|---|---|---|
| cwa_events | CWA weather/hazard adapter、live kernel、context assets（E04） | weather/hazard 有明確 CWA API 路徑；程式要求 CWA_API_KEY，實際部署是否有有效 key 本次未查。market event 預設 sources.market.tw/feed/v1 尚未對官方／業務來源查證，不能當成已串接。 | worker 盤點有效來源與 Secret reference；驗證各 feed 格式、事件時間及範圍；選用真實公開資料。不同來源分開記錄，不以市場事件缺口擋住 CWA 工程。 | 真實 response、query／dataset version、時間、SHA、有效／隔離筆數；取得必要來源使用憑據後完成來源專屬 live gate。 |
| transport_osm_tdx | OSM/TDX adapter、transport asset、分頁與 coverage 邏輯（E05） | OSM 預設 Taiwan PBF；須驗實際二進位格式與現有 parser、檔案大小及範圍。TDX endpoint／OAuth／配額須逐項 readback，本次未 live。 | 沿用 transport 入口，先以有界地域／release object 驗證 OSM，逐項驗 TDX parking、traffic、bus；修復不能消化真實回應的接點。 | 原始 PBF／JSON、實際 release identity、地理與時間 coverage、分頁完整性、parser output。 |
| open_poi | Overture/Foursquare/brand locator/Google verifier 模組與 URI overrides（E06） | 預設公開桶 URL 是 release prefix，且日期固定；production 已拒絕尾端 / 的 prefix。品牌來源也未證明是真實 feed。Google verifier 不能代替 open POI 主資料集。 | worker 從供應者正式 release catalog 找出真實 object/partition，指定來源版本及台灣範圍；沿用既有 parser 與空間分割。品牌網站逐站調查。 | exact object URI／版本／hash、實際格式解析、地理範圍、去重與來源歸屬；API 授權與各來源義務分別記錄。 |
| mof_moi | MOF/MOI parser、adapter、raw asset（E02、E14） | MOF 主線預設 data.gov.tw/dataset/mof_business 是佔位網址，parser 不認真實 camelCase。MOI 預設 DownloadOpenData 尚未指定可驗證資料檔／period。 | MOF 封存 patch 可供 worker 接手；完整稅籍需分頁規劃，100筆僅為整合證明。MOI 由 worker 查證官方租賃下載檔、期別、CSV/ZIP及schema。 | MOF 已有局部真實證據；仍需 reviewer／主線整合、需求範圍完整性。MOI 必須真實檔案讀回與解析；不能把登記等同實體營業店面。 |
| ris_nlsc | RIS/NLSC adapter、administrative fusion（E03、E15） | RIS 主線是舊 download URL，未解 responseData、真實人口欄位與ROC年月。NLSC 年份 GeoJSON URL 未實際驗證。 | worker 接手 RIS 修正與資料範圍驗證；查證 NLSC 正式邊界發布格式，校對5/8/11位行政代碼與發布版本後join。 | RIS 已真實解析31村里，零隔離；不代表全國或RIS/NLSC join完成。仍需全量／指定範圍、分頁、邊界、獨立總數與join證據。 |
| listing_observations | ListingObservationService、多種channel與extractor（E07） | 正式 Dagster 入口直接建立 LST-101 payload、example.com URL、以當下時間建立 approval，未讀取真實 channel。這不是缺使用者 raw檔。 | worker 把 asset 接到既有真實 acquisition/capture service；來源登錄由調查確定逐網站／feed。讀回真實 approval，移除 production 動態造 approval及示例資料。 | 真實網站/API raw、原URL及timestamp、版本化 extractor、欄位分類、publication/lifecycle 語意、逐來源使用條件。沒有來源時明確 unavailable。 |
| mobility | observed/synthetic 不同模型、MobilitySourceService（E08） | observed asset 固定生成 Taipei Main flow_count=1000，payload只含 status=ok，approval window以當下時間生成。不是實際人流接入。 | worker 尋找可用官方聚合運量／已授權feed，明示 measure、station/unit/timegrain；串入 observed lane。不能把搭乘次數、synthetic OD宣稱為門店實際人流。 | 真實聚合資料、時間窗／business_date、單位和數量對照、匿名化／最小群體義務、缺頁與缺時段檢驗。 |
| site_market_context | SiteMarketContextService/Builder，component refs與狀態模型（E09–E11） | published asset只回metadata，不呼叫組裝service；builder在缺sha時可hash component identity，不能證明資料內容。 | worker 使用實際上游component manifests組裝；live發布禁止以identity hash替代content hash；缺少domain必須明示 unavailable及原因。 | 同一site／as-of下可讀回的每個component object、內容SHA、衍生output、 lineage及版本；不以隨機一筆或契約登錄代替產出。 |

## 跨域根因

1. **測試證據層級混淆。** adapter/mock transport測試、real HTTP、local retained、GCS retained、masked release、runtime rollout是不同交付層，之前的「完成」沒有逐層對應。
2. **部署入口未接線。** library有live能力不代表Dagster入口使用它；listing、mobility、site context有直接證據。
3. **版本與範圍未成為執行輸入。** 固定日期、prefix URL、monthly capture ID、全國總數與局部分頁等需分開治理。
4. **快照 primitive固定舊數量。** E12保留8域／54,443總筆數，以及482/49,484/340/2,530/736/560/310/1。這些不能充當新來源真實資料的自然筆數要求。應由本次manifest宣告、再以原始檔與查詢完整性驗證；不能刪掉資料校驗來讓任意輸入通過。
5. **擷取到retention缺連接。** masked task禁止自己fetch是合理的隔離；不能將這個限制擴張為整個平台不得建立外部擷取與匯入通道。工程負責產生可追溯的retained input。
6. **所有來源的一個總門檻造成排程誤導。** 工程實作與source activation分開；只讓真實特定來源的權限/帳號阻擋該live步驟，不阻擋其他來源或sources-off基礎部署。

## 已封存的直接實作（待worker採納，不作為完成）

`implementation-handoff/manifest.json` 固定 base、tracked patch SHA及三個untracked檔SHA；patch不修改任何正式來源開關、審批收據或deployment。

內容包括MOF endpoint/真實欄位/單頁scope/unique capture ID、RIS官方格式/月份/行政代碼/分頁、kernel response envelope失敗處理、local verifier與測試。worker需先做乾淨工作樹匯入、diff review、測試、anchor commit及PR；不得直接將原工作樹廣泛stage，亦不得把patch名稱當成review approval。

仍需worker特別審查：多頁衍生input與raw頁分別bind、錯誤HTTP200留存失敗execution、不同執行間保存索引、CLI失敗留下可用失敗報告、全量MOF迭代和停止條件。這些不能因150個測試通過而略過。

## 尚未解除的具體限制

- MOF100筆與RIS31筆是局部live integration證據；沒有GCS generation，也沒有八域合格artifact。
- CWA/TDX/Google等所需帳號或Secret的「實際可用性」尚未查證，不表述成已證實缺帳號。
- listing/observed mobility真實逐來源尚須調查與選定；來源清單、API/下載網址、terms由工程調查補齊，不能回頭要求使用者生出raw snapshot。
- 外部source activation真正需要具名權威receipt的步驟沿用既有任務，不能虛構簽名；既有D01–D14決策不得反覆詢問。
- 歷史比較資料若只有同一份fixture不能完成dual-run；XR task須使用真正可比的歷史版本，或明確記錄不可重建的期間。

後續執行依 `SA.md`、`SD.md` 與 `EXECUTION_TASKS.md`；本文件是調查交付，不宣稱實作完成。

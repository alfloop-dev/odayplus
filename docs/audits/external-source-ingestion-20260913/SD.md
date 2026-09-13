# SD：沿用既有模組的接入、留存與發布設計

版本：2026-09-13。對應需求：SA FR01–FR12。本文定義worker落地邊界；根對話不直接實作。

## 現有系統與資料流

外部provider → 來源registry/version/policy → 既有LiveAcquisitionKernel + provider lease → 每頁原始EvidenceStore → source adapter與quarantine → domain產品／component manifest → 受治理匯入／retention primitive → sources-off masked materializer（#63）→ ODP release admission → 按来源activation與runtime audit。

控制面保持在既有source update policy／Dagster／Supervisor。source adapter只處理擷取與解讀；retention／masked lane讀取已取得的bytes，不自己對provider外連。

## D01：來源描述與端點

沿用SourceRegistryDocument/SourceDatasetVersion，補足或映射以下資訊：provider_id、dataset_id、release_key、exact source_uri／partition manifest、query scope、media/encoding、parser_version、published_at（未觀測不得填本機時間）、observed_at、terms版本與引用、required Secret reference、request budget、更新與留存規則。

- MOF：官方API `/OAI/api/businessRegistration?limit=…&offset=…`；主線佔位網址改正；同一日／月不同capture使用不同execution/blob identity，100筆僅是first-page probe。全量有明確offset、停止與重跑策略。
- RIS：官方 `/rs-opendata/api/v1/datastore/ODRP014/{ROC yyymm}`；明確資料月份，COUNTY/TOWN/VILLAGE/PAGE為query範圍；不能自動把未發布月份label到舊資料。
- MOI/NLSC/OSM/POI：worker從正式catalog核對實際檔案、格式、版本、壓縮/分區，不以prefix或landing page當資料檔。
- Listing/Mobility：依真實channel/feed登錄，不在asset內生成approval物件冒充外部批准。

## D02：擷取、分頁與失敗

所有來源沿用kernel evidence API。原始response在parser前存檔；對HTTP200錯誤envelope也保留失敗execution。HTTP retries不等於多次独立擷取，依現有lease契約使用。

RIS用responseCode/responseData/pageDataSize/page/totalPage驗證頁面。`totalDataSize`實抓顯示可仍為全國7781，即使query只回中正區31筆，因此不能直接當filtered total。所有頁依序取得、每頁request context與hash留存；拒絕頁號重複、總頁變動、超出budget、empty/malformed與跨頁admin重複。

MOF full page不能證明complete；指定cursor/offset與byte/request限制，留存每頁，對資料更新導致offset不穩定給出snapshot completeness限制；正式全量優先評估官方immutable發布檔。

TLS需沿用已有provider相容驗證設定，保持CA／hostname驗證；不能為成功抓取使用verify=False。重試與lease預算要一致。

## D03：可讀回的證據結構

以下是既有模型的欄位需求，worker先對映，只有既有模型不足才增修版本化schema：

| 層 | 必要資料 | 不得混用 |
|---|---|---|
| Raw capture | provider/dataset/release、request scope、HTTP/application status、observed time、exact raw URI、byte length、SHA、page/cursor、parser與code版本 | URI欄位不能只寫不存在的邏輯地址而無可解引用的backing object。 |
| Normalized | raw parent refs、contract/parser版本、有效/隔離筆數、time/geography/units、content URI/hash、classification | 合併array的hash不能填最後一頁raw hash。 |
| Retained cloud | raw/normalized provenance、immutable URI、actual GCS generation、download/readback hash、分類/mask policy digests | 本機檔沒有GCS generation；不能生成相似數字填入。 |
| Domain/component | 真實上游object refs、內容hash/count/coverage、as-of、unavailable reasons | hash(identifier)不是hash(data bytes)。 |
| Release | exact candidate/image/contracts/domain set/source snapshots/masking、artifact generation/hash、consumer binding | 歷史receipt不代表本次候選版本。 |

去重key應為provider+dataset+version+request partition+content SHA；capture ID與content identity分開。重跑可重用同bytes但必須留下新execution並保留parent lineage。持久化descriptor/index要跨process可讀回，不能只有process內memory metadata。

## D04：逐來源接線

1. Official：修復MOF/RIS後，序列化處理同檔案中的MOI/NLSC；CWA是獨立source驗證。credentials只使用配置reference，不打印值。
2. Transport/POI：沿用真實PBF/Parquet/JSON parser；視實際格式調整下載與partition mapping，禁止拿JSON fixture滿足binary來源驗收。
3. Listing：既有ListingObservationService接真實capture；sources-off/未登錄時回報不可用；production刪除固定LST-101與動態approval生成。Extractor version、欄位分類與channel使用条件綁定同一capture。
4. Mobility：ObservedMobilityFeed接真實聚合運量/授權feed，移除固定flow_count；metadata包含measure/unit/timegrain/business timezone。保留observed與synthetic互斥，缺值不可製造零。
5. Site context：asset呼叫已有SiteMarketContextService，消費真實component refs；發布分支要求可讀回content digest，舊builder便利預設不得供live claim使用。

## D05：快照前置與相容策略

沿用raw_snapshot_retention.py與既有one-shot lane，建立acquisition receipts→governed import/export→retained object連接；不要直接把raw網路頁面包成舊masked schema就宣稱合格。

- 每個domain的schema/欄位分類/輸入count/來源版本來自本次受治理manifest；驗證本次實際資料與declared count一致。
- 54,443與舊逐域固定count只保留歷史證據，不作新release通用規則。更換需回歸測試證明少頁、漏domain與篡改仍失敗。
- 原八域release完整性仍為#63的驗收要求。可先完成各domain的擷取與local/cloud留存；完整artifact必須八域齊備。工程task不得因一域未到位而完全停工。
- Provenance含原始provider URI與captured version、引入此平台的身份與時間、bytes hash及所有中間衍生關係；不能製造sealed_by/批准人。
- GCS使用現有project與runtime identity、create-only generation precondition，下載exact generation驗證；local乾跑明示非release。
- #63修materializer讀新receipt mapping，繼續sources-off/default-deny執行。變更source fetching不屬#63範圍。

## D06：任務依賴、合併與回滾

`EXECUTION_TASKS.md`定義唯一owner/owned_paths。公用kernel與official文件先由MOF/RIS修復task完成；MOI/NLSC後繼task才碰同檔案。Transport/POI、listing、mobility可在共用底座完成後並行，禁止各自複製kernel。

來源接線與retention primitive工程可分階段提交；來源不足寫精確live blocker，不把已能做的程式工作列成Human input。Site context在上游component ready後驗證；#63在retained八域齊備後送live acceptance；source activation與XR audit沿用既有task。

回滾採關閉單一來源、回到上一組已驗證的manifest／generation；不覆寫已保留object，不降低bucket retention，不用fixture補不足資料。驗收分offline tests、real local、cloud readback、release、runtime逐層標示。

## D07：worker驗證矩陣

- Unit／contract：官方真實格式的脫敏fixture、missing required fields、無意義200、quarantine、行政／時間／單位校驗。
- Integration：同一Dagster asset經kernel，跨頁、scope、重跑、原始及derived bytes逐一hash；失敗也有retained evidence。
- Live：明確來源、scope、budget與time；保留實際bytes，報告sample與全量的差別。
- Security／policy：source disabled、expired permission、manual stop、lease expiry、憑證不外洩；不得用mock成功取代live。
- Retention／release：actual generation、create-only、跨process下載、hash/count/contract/candidate/image篡改。
- Runtime：實際pod/image/Secret projection/egress/restart/latency；只在具備真實權限環境驗證，不填SIMULATED。

## D08：已封存patch的採納程序

worker讀 `implementation-handoff/manifest.json`，驗tracked.patch與untracked hashes，對固定base確認diff；在worker-owned乾淨工作樹匯入並自行審查。這是繼承材料，非既成設計或批准。完成anchor、中文PR、獨立review、CI後才交Supervisor合併。根對話不再改這份patch。

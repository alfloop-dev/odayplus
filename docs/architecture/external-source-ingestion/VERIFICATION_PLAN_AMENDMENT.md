# 驗證計畫修正：條件依賴循環、分階段方案與下游驗證命令對照

任務：`DPF-SOURCE-SA-SD-BASELINE-001`
版本：2026-09-13（修訂版 2）

## 識別的條件循環與依賴本質區分

### 結構依賴與推論區分

在原有規劃推論中，曾出現將所有 runtime 證據、受治理資料 readback 與 PR #63 artifact 誤解為必須等待 Production Legal Activation 的推論循環。本修正方案依據 SA FR09/FR10、SD D05、現行 runbook 與 canonical `ai-status.json` 的真實結構邊，明確區分**結構依賴**與**過度推論**：

1. **工程有界擷取（Bounded Engineering Capture）≠ Production Activation**：
   - 開發與修復階段的有界 probe（例如 MOF 100 筆、RIS 31 筆、公開開放資料下載解析）屬於工程單元／整合測試與 schema 校驗範疇。
   - 這些有界測試不需要正式的 production activation 授權，可在 sources-off / default-deny 的受控測試環境下執行並產出本地讀回驗證收據。

2. **PR #63（`DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001`）的獨立 Materializer 定位**：
   - PR #63 為獨立 masked release materializer，在 sources-off / default-deny 環境下讀取受治理之 retained raw data，生成八域 masked release artifact。
   - PR #63 **不需亦不包含**外部 provider fetch；其執行僅依賴上游 domain 資料到位與 retained raw snapshot，**不依賴 Legal Activation**。

3. **PR #1312（`XR-EXT-OSS-FINAL-AUDIT-001`）的技術稽核邊界**：
   - PR #1312 執行 OSS 技術稽核，包含 Pod container digest、default-deny 下的 VPC egress flow log 驗證、以及 DB snapshot readback。
   - 依據 canonical 驗收規則，技術稽核完成後方能產出完整證據供法務審查；**不能拿缺法律批准阻塞技術稽核**。

4. **真正的 Legal 與 Activation 結構依賴**：
   - `HUMAN-OSS-LEGAL-APPROVAL-001` depends_on `XR-EXT-OSS-FINAL-AUDIT-001`（法務依據技術稽核證據進行核准）。
   - `XR-SOURCE-APPROVAL-ACTIVATION-001` depends_on `HUMAN-OSS-LEGAL-APPROVAL-001`（取得具名法務核准後，方可開啟 production 來源開關）。

5. **現有決策保護**：
   - D01–D14 已決定政策方向，不得反覆詢問或自行變更。
   - 來源開關預設保持 `false`（sources-off / default-deny），需綁定 SHA-256 receipt 才可開啟。

## 修正方案：分階段驗收架構

```
階段 A：靜態技術合規、有界工程驗證與 sources-off 部署/讀回（不受 Legal Gate 阻擋）
  ├── DPF-SOURCE-SA-SD-BASELINE-001（本 task：基線納管與下游驗證規劃）
  ├── DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001（MOF/RIS 有界修復與 probe readback）
  ├── DPF-OFFICIAL-SOURCE-ENDPOINT-INTEGRATION-001（CWA/MOI/NLSC 端點整合與 parser 驗證）
  ├── DPF-TRANSPORT-POI-RELEASE-INTEGRATION-001（OSM/TDX/Overture 二進位與 release 整合）
  ├── DPF-LISTING-PRODUCTION-ASSET-INTEGRATION-001（租屋刊登觀測接真實擷取服務）
  ├── DPF-MOBILITY-PRODUCTION-ASSET-INTEGRATION-001（人流資產接真實聚合運量 feed）
  ├── DPF-ACQUISITION-RETENTION-BRIDGE-001（擷取至受治理 raw snapshot 橋接）
  ├── DPF-SITE-CONTEXT-REAL-COMPONENTS-001（真實 component manifests 組裝與 digest 驗證）
  ├── DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001 / PR #63（sources-off 下生成八域 masked artifact）
  ├── XR-EXT-OSS-FINAL-AUDIT-001 / PR #1312（Pod digest、VPC egress flow log、DB readback）
  └── HUMAN-OSS-LEGAL-APPROVAL-001 準備（case-matrix, decision-cards, receipt template）
        │
        ▼
Legal Gate 決策點：
  └── HUMAN-OSS-LEGAL-APPROVAL-001（具名 Legal/Security/Risk 人員核准）
        │
        ▼
階段 B：Production 來源啟用與 Live 運作驗收（Legal Receipt 到位後）
  ├── XR-SOURCE-APPROVAL-ACTIVATION-001（逐來源 production 啟用與 configmap 更新）
  └── Production 排程 live acquisition 與 dual-run 歷史比對驗收
```

## 下游任務 Verification 中文要求逐筆對照與可執行驗證計畫

針對下游 7 個實作任務，保留完整的中文驗收條件，制定具體可執行的驗證計畫，並對尚未具備工具或輸入之項目明確標示 `NOT_EXECUTED` 與缺口責任步驟：

### 1. DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001
- **任務名稱**：修復 MOF／RIS 官方來源 live 擷取與 parser
- **負責角色**：Owner: `Antigravity`；Reviewer: `Codex2`
- **依賴**：`DPF-SOURCE-SA-SD-BASELINE-001`
- **中文驗收標準（完整保留）**：
  1. 先驗 implementation-handoff/manifest.json：解壓 tracked.patch.gz 後核對 tracked_patch_sha256，untracked 依 archive_paths 讀 .py.txt 並驗 SHA；在自己的乾淨 worker 工作樹審查和選擇性匯入。不得直接 stage 根對話隔離工作樹。
  2. 修正 MOF 官方 API/camelCase/limit-offset 與 RIS 官方年月/responseData/行政代碼/分頁；可沿用封存 patch 但需自行審查。
  3. 多頁原始 response 與衍生 parser input 各自 hash/URI/parent refs，HTTP200 錯誤也有失敗 execution；跨 process 讀回。
  4. MOF/RIS 經既有 Dagster 真實有界擷取與 normalized 讀回，局部 100/31 不得宣稱全國或 cloud release。
  5. 增補尚缺的錯誤 CLI 證據、descriptor 持久化、真實 schema drift 與 MOF 正式覆蓋範圍策略；禁止 verify=False。
  6. 每條完成宣稱對應固定 commit 與真實 command receipt；mock、local live、GCS、release、runtime 層級分開。
  7. 沿用既有模組與控制面，按 scope 產生 anchor 及中文 PR、獨立審查與合併；遇 specific live blocker 仍先完成可獨立推進部分。
- **可執行驗證命令**：
  ```bash
  uv run --frozen pytest -q tests/sources/test_public_source_ingestion.py tests/sources/test_official_live_acquisition.py tests/external/test_live_acquisition_kernel.py tests/sources/test_mof_moi.py tests/sources/test_ris_nlsc.py
  uv run python scripts/capture_public_source_evidence.py --sources mof,ris --bounded
  git diff --check
  ```
- **工具／輸入準備與缺口說明**：
  - 由 worker 自 `docs/audits/external-source-ingestion-20260913/implementation-handoff/` 匯入 patch 與測試檔案，以 `scripts/capture_public_source_evidence.py` 產出有界 100/31 筆 live capture 與 normalized readback 證據收據。

---

### 2. DPF-OFFICIAL-SOURCE-ENDPOINT-INTEGRATION-001
- **任務名稱**：官方來源端點與合約整合（CWA／MOI／NLSC）
- **負責角色**：Owner: `Antigravity3`；Reviewer: `Codex2`
- **依賴**：`DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001`
- **中文驗收標準（完整保留）**：
  1. 官方端點替換佔位 URL：CWA weather/hazard、MOI 租賃實價、NLSC 行政邊界；禁止使用假端點。
  2. CWA 整合 weather/hazard API 與 Secret 查證；MOI 指定公開下載期別與檔案格式；NLSC 驗證年份邊界 GeoJSON。
  3. 實作真實 response parser 與資料模型轉換，處理政府資料常見之編碼、欄位命名及結構差異。
  4. 建立官方端點整合測試與合約測試，驗證端點連通性與資料解析正確性。
  5. 每條完成宣稱對應固定 commit 與真實 command receipt；mock、local live、GCS、release、runtime 層級分開。
  6. 沿用既有模組與控制面，按 scope 產生 anchor 及中文 PR、獨立審查與合併；遇 specific live blocker 仍先完成可獨立推進部分。
- **可執行驗證命令**：
  ```bash
  uv run --frozen pytest -q tests/sources/test_official_live_acquisition.py tests/sources/test_mof_moi.py tests/sources/test_ris_nlsc.py tests/sources/test_official_source_endpoints.py
  git diff --check
  ```
- **工具／輸入準備與缺口說明**：
  - `NOT_EXECUTED`：需由本 task 於實作時建立 `tests/sources/test_official_source_endpoints.py`，並查證 `CWA_API_KEY` Secret 部署可用性、MOI 租賃期別公開下載檔格式（CSV/ZIP）及 NLSC GeoJSON 邊界格式後執行官方端點讀回驗證。

---

### 3. DPF-TRANSPORT-POI-RELEASE-INTEGRATION-001
- **任務名稱**：交通與 POI 外部釋出資料集整合
- **負責角色**：Owner: `Antigravity4`；Reviewer: `Codex2`
- **依賴**：`DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001`
- **中文驗收標準（完整保留）**：
  1. OSM Taiwan PBF 實際二進位檔案解析驗證，確保 parser 相容真實 PBF 結構與地理範圍。
  2. TDX 公共運輸 API 整合，驗證 OAuth token、分頁與配額讀回。
  3. Overture Maps / Foursquare OS 正式 release catalog 物件定位與分區過濾，修正 release prefix 結尾 `/` 問題。
  4. 品牌門市定位器與 Google Places verifier 邊界隔離，明確區分主資料集與驗證工具。
  5. 建立交通與 POI 整合測試，產出實際物件解析與覆蓋率報告。
  6. 每條完成宣稱對應固定 commit 與真實 command receipt；mock、local live、GCS、release、runtime 層級分開。
  7. 沿用既有模組與控制面，按 scope 產生 anchor 及中文 PR、獨立審查與合併；遇 specific live blocker 仍先完成可獨立推進部分。
- **可執行驗證命令**：
  ```bash
  uv run --frozen pytest -q tests/sources/test_transport_poi_live_acquisition.py tests/sources/test_real_transport_poi_releases.py
  git diff --check
  ```
- **工具／輸入準備與缺口說明**：
  - `NOT_EXECUTED`：需由本 task 於實作時建立 `tests/sources/test_real_transport_poi_releases.py`，並取得 OSM Taiwan PBF 二進位檔與 TDX/Overture release 物件後執行二進位/地理 coverage 讀回驗證。

---

### 4. DPF-LISTING-PRODUCTION-ASSET-INTEGRATION-001
- **任務名稱**：將租屋刊登觀測接到真實擷取服務
- **負責角色**：Owner: `Antigravity5`；Reviewer: `Codex2`
- **依賴**：`DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001`
- **中文驗收標準（完整保留）**：
  1. 移除 Dagster 入口硬編碼之 LST-101 payload、example.com URL 與 `datetime.now()` 動態偽造 approval。
  2. 將 listing asset 對接至既有真實 `ListingObservationService` 與 capture/extractor 通道。
  3. 建立逐來源登記與授權條款查證，無合法來源時明確標示 unavailable。
  4. 實作版本化 extractor 與 raw response 落地，保留原始 URL、時間戳與欄位分類。
  5. 建立刊登觀測整合測試，驗證真實 channel 讀取或受控 fixture 讀回。
  6. 每條完成宣稱對應固定 commit 與真實 command receipt；mock、local live、GCS、release、runtime 層級分開。
  7. 沿用既有模組與控制面，按 scope 產生 anchor 及中文 PR、獨立審查與合併；遇 specific live blocker 仍先完成可獨立推進部分。
- **可執行驗證命令**：
  ```bash
  uv run --frozen pytest -q tests/sources/test_listing_production_asset.py
  git diff --check
  ```
- **工具／輸入準備與缺口說明**：
  - `NOT_EXECUTED`：需由本 task 於實作時建立 `tests/sources/test_listing_production_asset.py`，移除 LST-101 示例資料並對接 `ListingObservationService`，查證來源許可與取得真實 channel 輸入後執行 capture/readback。

---

### 5. DPF-MOBILITY-PRODUCTION-ASSET-INTEGRATION-001
- **任務名稱**：將人流移動性資產接到聚合運量資料
- **負責角色**：Owner: `Antigravity6`；Reviewer: `Codex2`
- **依賴**：`DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001`
- **中文驗收標準（完整保留）**：
  1. 移除 observed asset 固定生成台北車站 `flow_count=1000` 與 `datetime.now()` 動態 approval。
  2. 嚴格分離 observed（觀測聚合運量）與 synthetic（模擬模型）資料流程，禁止將搭乘次數偽稱為門店實際人流。
  3. 對接官方聚合運量或授權 feed，明示 measure、station/unit/timegrain。
  4. 實作匿名化與最小群體統計保護機制，落實隱私與授權義務。
  5. 建立人流資產整合測試，驗證真實聚合資料或授權 feed 讀回。
  6. 每條完成宣稱對應固定 commit 與真實 command receipt；mock、local live、GCS、release、runtime 層級分開。
  7. 沿用既有模組與控制面，按 scope 產生 anchor 及中文 PR、獨立審查與合併；遇 specific live blocker 仍先完成可獨立推進部分。
- **可執行驗證命令**：
  ```bash
  uv run --frozen pytest -q tests/sources/test_mobility_production_asset.py tests/sources/test_listing_mobility_live_acquisition.py
  git diff --check
  ```
- **工具／輸入準備與缺口說明**：
  - `NOT_EXECUTED`：需由本 task 於實作時建立 `tests/sources/test_mobility_production_asset.py`，移除 flow_count=1000 示例數值，選定官方聚合運量/授權 feed 輸入後執行單位/時間/數量核對。

---

### 6. DPF-ACQUISITION-RETENTION-BRIDGE-001
- **任務名稱**：建立外部擷取至受治理 raw snapshot 的橋接機制
- **負責角色**：Owner: `Antigravity7`；Reviewer: `Codex2`
- **依賴**：`DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001`
- **中文驗收標準（完整保留）**：
  1. 沿用既有 retention primitive 建立 capture/normalized receipts 到受治理匯入的唯一連接，不新增 scheduler/registry，不讓 retention lane 外連 provider。
  2. 移除固定歷史 54,443 及逐域數量作為新 release 通用標準，改由本次 manifest 與實際內容/完整性驗證；缺 domain／改 count／漏頁仍 fail closed。
  3. 支持各 domain 先行準備與驗證原始物件，full masked release 仍要求八域齊備；工程驗收不等待其他來源的業務帳號。
  4. 以目前真實可用 MOF/RIS 證據驗證 provenance/分類/讀回；本機結果不得編造 GCS generation。雲端由既有 runtime identity/lane create-only 並 exact generation download。
  5. 具名 sealed provenance 須真實來源，不能自造 approved identity；將具體 cloud auth/live 限制記錄在對應步驟。
  6. 每條完成宣稱對應固定 commit 與真實 command receipt；mock、local live、GCS、release、runtime 層級分開。
  7. 沿用既有模組與控制面，按 scope 產生 anchor 及中文 PR、獨立審查與合併；遇 specific live blocker 仍先完成可獨立推進部分。
- **可執行驗證命令**：
  ```bash
  uv run --frozen pytest -q tests/deploy/test_retained_raw_snapshots.py tests/external/test_acquisition_retained_import.py
  git diff --check
  ```
- **工具／輸入準備與缺口說明**：
  - `NOT_EXECUTED`：需由本 task 於實作時建立 `tests/external/test_acquisition_retained_import.py`，移除固定 54,443 count，改以動態 manifest 驗證已擷取 raw 資料匯入與篡改負例。

---

### 7. DPF-SITE-CONTEXT-REAL-COMPONENTS-001
- **任務名稱**：將 site market context 接到真實 component manifests
- **負責角色**：Owner: `Antigravity2`；Reviewer: `Codex2`
- **依賴**：`DPF-ACQUISITION-RETENTION-BRIDGE-001`
- **中文驗收標準（完整保留）**：
  1. published_site_market_contexts 改調用既有 `SiteMarketContextService`/`Builder`；單純 metadata 不算生成資料產品。
  2. live 發布只採可讀回的 component content SHA，禁止 `hash(identifier)` 替代內容 hash。
  3. 依 site/as-of/query scope 組裝真實上游；尚缺域明示 unavailable reason，不補造值。
  4. 用已準備的真實 components 驗證產物和 parents lineage；全八域 release 完整性由 #63 與 XR 最終驗收，不能阻擋本 task 可先完成的接線。
  5. 每條完成宣稱對應固定 commit 與真實 command receipt；mock、local live、GCS、release、runtime 層級分開。
  6. 沿用既有模組與控制面，按 scope 產生 anchor 及中文 PR、獨立審查與合併；遇 specific live blocker 仍先完成可獨立推進部分。
- **可執行驗證命令**：
  ```bash
  uv run --frozen pytest -q tests/products/test_site_context_real_components.py tests/products/test_site_context.py
  git diff --check
  ```
- **工具／輸入準備與缺口說明**：
  - `NOT_EXECUTED`：需由本 task 於實作時建立 `tests/products/test_site_context_real_components.py`，依賴 BRIDGE-001 提供真實 component manifests，呼叫組裝服務並驗證 component content digest。

---

## Canonical Metadata 同步與操作收據

本任務已透過 live canonical `ai-status.sh assign` 將上述可執行驗證計畫與 `NOT_EXECUTED` 缺口標示同步至 canonical `ai-status.json`：

| 任務 ID | Owner | Reviewer | Canonical 操作命令 | 退出碼 | 更新前 last_update | 更新後 last_update |
|---|---|---|---|---|---|---|
| `DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001` | Antigravity | Codex2 | `TASK_METADATA_JSON=... ai-status.sh assign ...` | `0` | 14:56:50Z | 15:42:15Z |
| `DPF-OFFICIAL-SOURCE-ENDPOINT-INTEGRATION-001` | Antigravity3 | Codex2 | `TASK_METADATA_JSON=... ai-status.sh assign ...` | `0` | 14:56:55Z | 15:42:27Z |
| `DPF-TRANSPORT-POI-RELEASE-INTEGRATION-001` | Antigravity4 | Codex2 | `TASK_METADATA_JSON=... ai-status.sh assign ...` | `0` | 14:57:05Z | 15:42:29Z |
| `DPF-LISTING-PRODUCTION-ASSET-INTEGRATION-001` | Antigravity5 | Codex2 | `TASK_METADATA_JSON=... ai-status.sh assign ...` | `0` | 14:57:11Z | 15:42:31Z |
| `DPF-MOBILITY-PRODUCTION-ASSET-INTEGRATION-001` | Antigravity6 | Codex2 | `TASK_METADATA_JSON=... ai-status.sh assign ...` | `0` | 14:57:16Z | 15:42:33Z |
| `DPF-ACQUISITION-RETENTION-BRIDGE-001` | Antigravity7 | Codex2 | `TASK_METADATA_JSON=... ai-status.sh assign ...` | `0` | 14:57:21Z | 15:42:35Z |
| `DPF-SITE-CONTEXT-REAL-COMPONENTS-001` | Antigravity2 | Codex2 | `TASK_METADATA_JSON=... ai-status.sh assign ...` | `0` | 14:57:26Z | 15:42:38Z |

本基線任務 `DPF-SOURCE-SA-SD-BASELINE-001` 僅負責設計規劃與 canonical metadata 對齊驗收，不越權執行下游實作或下游 live 擷取。

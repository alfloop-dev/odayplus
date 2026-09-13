# 驗證計畫修正：條件依賴循環、分階段方案與下游驗證命令對照

任務：`DPF-SOURCE-SA-SD-BASELINE-001`
版本：2026-09-13（修訂版 3）

## 識別的條件循環與依賴本質區分

### 結構依賴與推論區分

在原有規劃推論中，曾出現將所有 runtime 證據、受治理資料 readback 與 PR #63 artifact 誤解為必須等待 Production Legal Activation 的推論循環。本修正方案依據 SA FR09/FR10、SD D05、現行 runbook 與 canonical `ai-status.json` 的真實結構邊界，明確區分**結構依賴**與**過度推論**：

1. **工程有界擷取（Bounded Engineering Capture）≠ Production Activation**：
   - 開發與修復階段的有界 probe（例如 MOF 100 筆、RIS 31 筆、公開開放資料下載解析）屬於工程單元／整合測試與 schema 校驗範疇。
   - 這些有界測試不需要正式的 production activation 授權，可在 sources-off / default-deny 的受控測試環境下執行並產出本地讀回驗證收據。

2. **歷史快照比對之分層**：
   - 離線／受治理歷史快照之讀回比對：在已有受治理歷史 snapshot 且資料與部署權限就緒時即可推進，不需等待 Production Legal Activation。
   - 真實 production live dual-run 比對：確實需要外部 live 資料連線者，才在 Legal Gate 核准後之啟用階段進行。
   - 兩者不可一概視為需 Legal Gate 阻擋，亦不影響 canonical 依賴結構。

3. **PR #63（`DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001`）的獨立 Materializer 定位**：
   - PR #63 為獨立 masked release materializer，在 sources-off / default-deny 環境下讀取受治理之 retained raw data，生成八域 masked release artifact。
   - PR #63 **不需亦不包含**外部 provider fetch；其執行僅依賴上游 domain 資料到位與 retained raw snapshot，**不依賴 Legal Activation**。

4. **PR #1312（`XR-EXT-OSS-FINAL-AUDIT-001`）的技術稽核邊界**：
   - PR #1312 執行 OSS 技術稽核，包含 Pod container digest、default-deny 下的 VPC egress flow log 驗證、以及 DB snapshot readback。
   - 依據 canonical 驗收規則，技術稽核完成後方能產出完整證據供法務審查；**不能拿缺法律批准阻塞技術稽核**。

5. **真正的 Legal 與 Activation 結構依賴（Canonical 無循環）**：
   - `HUMAN-OSS-LEGAL-APPROVAL-001` depends_on `XR-EXT-OSS-FINAL-AUDIT-001`（法務依據技術稽核證據進行核准）。
   - `XR-SOURCE-APPROVAL-ACTIVATION-001` depends_on `HUMAN-OSS-LEGAL-APPROVAL-001`（取得具名法務核准後，方可開啟 production 來源開關）。
   - Canonical 結構依賴保持完全不變，結構圖本身無循環。

6. **現有決策保護**：
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
  ├── 離線/受治理歷史快照讀回比對（已有歷史 snapshot 且權限就緒即可推進）
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
  └── Production 排程 live acquisition 與需要外部連線之 live dual-run 歷史比對驗收
```

## 下游任務 Verification 中文要求逐筆對照與可執行驗證計畫

針對下游 7 個實作任務，逐條對照 canonical acceptance 與固定 `execution-tasks.json` 之中文驗收與驗證標準，制定具體可執行的驗證計畫，並對尚未具備工具或輸入之項目明確標示 `NOT_EXECUTED` 與缺口責任步驟：

### 1. DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001
- **任務名稱**：接手MOF／RIS局部修正並完成公開來源實測
- **負責角色**：Owner: `Antigravity`；Reviewer: `Codex2`
- **依賴**：`DPF-SOURCE-SA-SD-BASELINE-001`
- **中文驗收標準（完整逐條保留）**：
  1. 先驗 `implementation-handoff/manifest.json`、`tracked.patch` 及 `untracked` SHA，於乾淨 worker 工作樹審查和選擇性匯入；不直接 stage 根對話隔離工作樹。
  2. 修正 MOF 官方 API/camelCase/limit-offset 與 RIS 官方年月/responseData/行政代碼/分頁；可沿用封存 patch 但需自行審查。
  3. 多頁原始 response 與衍生 parser input 各自 hash/URI/parent refs，HTTP200 錯誤也有失敗 execution；跨 process 讀回。
  4. MOF/RIS 經既有 Dagster 真實有界擷取與 normalized 讀回，局部 100/31 不得宣稱全國或 cloud release。
  5. 增補尚缺的錯誤 CLI 證據、descriptor 持久化、真實 schema drift 與 MOF 正式覆蓋範圍策略；禁止 `verify=False`。
  6. 每條完成宣稱對應固定 commit 與真實 command receipt；mock、local live、GCS、release、runtime 層級分開。
  7. 沿用既有模組與控制面，按 scope 產生 anchor 及中文 PR、獨立審查與合併；遇 specific live blocker 仍先完成可獨立推進部分。
- **中文驗證項目對照**：
  1. `uv run --frozen pytest -q tests/sources/test_public_source_ingestion.py tests/sources/test_official_live_acquisition.py tests/external/test_live_acquisition_kernel.py tests/sources/test_mof_moi.py tests/sources/test_ris_nlsc.py`
  2. 以既有 Dagster 入口重跑 MOF/RIS 有界 live capture 並 readback
  3. `git diff --check`
- **可執行驗證命令**：
  ```bash
  uv run --frozen pytest -q tests/sources/test_public_source_ingestion.py tests/sources/test_official_live_acquisition.py tests/external/test_live_acquisition_kernel.py tests/sources/test_mof_moi.py tests/sources/test_ris_nlsc.py
  uv run python scripts/capture_public_source_evidence.py --source mof --output /tmp/mof-capture-evidence-$(date +%s)
  uv run python scripts/capture_public_source_evidence.py --source ris --output /tmp/ris-capture-evidence-$(date +%s)
  git diff --check
  ```
- **CLI 參數與執行規範**：
  - 封存 CLI `scripts/capture_public_source_evidence.py` 定義必填參數 `--source {mof,ris}` 與 `--output <Path>`。
  - `--output` 必須指定全新且不存在的本機目錄（內部以 `output.mkdir(parents=True, exist_ok=False)` 強制保護）。
  - 有界前置：MOF 預設 request scope `{'limit': 100, 'offset': 0}`（第 1 頁有界 probe）；RIS 預設 2026-08 臺北市中正區 ODRP014（31 筆人口資料）。
- **工具／輸入準備與 NOT_EXECUTED 缺口說明**：
  - `NOT_EXECUTED`：需由本 task worker 於實作時自 `docs/audits/external-source-ingestion-20260913/implementation-handoff/` 匯入 patch 與測試檔（`test_public_source_ingestion.py`、`official.py`、`capture_public_source_evidence.py`），執行分源 CLI 產出有界 100/31 筆 live capture 與 normalized readback 證據收據。

---

### 2. DPF-OFFICIAL-SOURCE-ENDPOINT-INTEGRATION-001
- **任務名稱**：完成CWA／MOI租賃／NLSC真實來源接入
- **負責角色**：Owner: `Antigravity3`；Reviewer: `Codex2`
- **依賴**：`DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001`
- **中文驗收標準（完整逐條保留）**：
  1. 逐來源核對官方資料 catalog 與 real download object/API；MOI 指定實際租賃檔及 period，NLSC 驗格式與版本，market event 來源未核實不得稱已接入。
  2. 查證既有 Secret reference 是否可用，只記錄名稱／狀態；CWA 等真正需 interactive account 才提出精確缺口。
  3. 串接真實 bytes 到現有 adapter/quarantine，NLSC 與 RIS 行政代碼/版本 join 有證據。
  4. 按來源分開提供可用／不可用、live raw/readback/count/coverage；不以 MOI/NLSC 一源缺口阻停其他可實作部分。
  5. 每條完成宣稱對應固定 commit 與真實 command receipt；mock、local live、GCS、release、runtime 層級分開。
  6. 沿用既有模組與控制面，按 scope 產生 anchor 及中文 PR、獨立審查與合併；遇 specific live blocker 仍先完成可獨立推進部分。
- **中文驗證項目對照**：
  1. `uv run --frozen pytest -q tests/sources/test_official_live_acquisition.py tests/sources/test_mof_moi.py tests/sources/test_ris_nlsc.py tests/sources/test_official_source_endpoints.py`
  2. 逐來源官方 live response／原始檔讀回與 join 驗證
  3. `git diff --check`
- **可執行驗證命令**：
  ```bash
  uv run --frozen pytest -q tests/sources/test_official_live_acquisition.py tests/sources/test_mof_moi.py tests/sources/test_ris_nlsc.py tests/sources/test_official_source_endpoints.py
  git diff --check
  ```
- **工具／輸入準備與 NOT_EXECUTED 缺口說明**：
  - `NOT_EXECUTED`：需由本 task 於實作時建立 `tests/sources/test_official_source_endpoints.py`。
  - 具體缺口責任步驟：
    1. CWA: 查證 `CWA_API_KEY` Secret 部署可用性，區分 weather/hazard 端點；market event 在未取得權威官方 feed 核實前不得宣稱已接入。
    2. MOI: 指定公開下載實際租賃檔案（CSV/ZIP）與 period，驗證 response 解析與 adapter 接線。
    3. NLSC: 驗證年份邊界 GeoJSON 格式與版本，並提供 5/8/11 位行政代碼與 RIS 人口資料之 join 驗證證據。
    4. 逐來源提供 live response / 原始檔讀回與 join 驗證收據。

---

### 3. DPF-TRANSPORT-POI-RELEASE-INTEGRATION-001
- **任務名稱**：完成OSM／TDX與開放POI真實release接入
- **負責角色**：Owner: `Antigravity4`；Reviewer: `Codex2`
- **依賴**：`DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001`
- **中文驗收標準（完整逐條保留）**：
  1. 核對 OSM 實際 PBF 及 POI 實際 Parquet/object/partition 格式；prefix URL 與固定日期不得充當可用資料來源。
  2. 逐來源找出 official release catalog／真實版本及台灣 scope，沿用現有 adapter、partition 與 quarantine。
  3. TDX auth/quota 與 Google verifier 角色分離，不能將 Google verifier 代替 open POI，也不能宣稱已測未執行的來源。
  4. 至少先完成可用開放來源真實有界 raw+parser+readback，再按各 source activation 條件推進 TDX 等；每 source 保存精確狀態。
  5. 每條完成宣稱對應固定 commit 與真實 command receipt；mock、local live、GCS、release、runtime 層級分開。
  6. 沿用既有模組與控制面，按 scope 產生 anchor 及中文 PR、獨立審查與合併；遇 specific live blocker 仍先完成可獨立推進部分。
- **中文驗證項目對照**：
  1. `uv run --frozen pytest -q tests/sources/test_transport_poi_live_acquisition.py tests/sources/test_real_transport_poi_releases.py`
  2. 真實 binary/object 資料解析及地理 coverage/readback
  3. `git diff --check`
- **可執行驗證命令**：
  ```bash
  uv run --frozen pytest -q tests/sources/test_transport_poi_live_acquisition.py tests/sources/test_real_transport_poi_releases.py
  git diff --check
  ```
- **工具／輸入準備與 NOT_EXECUTED 缺口說明**：
  - `NOT_EXECUTED`：需由本 task 於實作時建立 `tests/sources/test_real_transport_poi_releases.py`。
  - 具體缺口責任步驟：
    1. OSM: 取得 OSM Taiwan PBF 實際二進位檔案，驗證 parser 相容性與台灣地理範圍。
    2. TDX: 查證 TDX OAuth token 與配額，與 Google verifier 角色嚴格分離（Google verifier 是驗證工具，不可代替 open POI 主資料集）。
    3. Open POI: 對接 Overture Maps / Foursquare OS 正式 release catalog 物件（修復結尾 `/` prefix 問題），執行真實二進位/Parquet 解析與地理 coverage 讀回。
    4. 產出真實 binary/object 資料解析及地理 coverage/readback 收據。

---

### 4. DPF-LISTING-PRODUCTION-ASSET-INTEGRATION-001
- **任務名稱**：將刊登production asset接到真實來源channel
- **負責角色**：Owner: `Antigravity5`；Reviewer: `Codex2`
- **依賴**：`DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001`
- **中文驗收標準（完整逐條保留）**：
  1. production asset 移除固定 LST-101、example.com 與依 now 產生 approval；接既有 `ListingObservationService` 及 capture pipeline。
  2. 工程調查逐網站/feed 正式入口、現有使用權和可用擷取模式；來源未知明示，不要求使用者提供 raw 檔。
  3. approved API/feed 或合規公開頁經既有 kernel 取得原始 bytes 與版本化 extractor；未具備實際權限時保持 unavailable，不造 receipt。
  4. 保留觀測/lifecycle 語意、敏感欄位分類、原 URL/time/hash 與重跑讀回；正反例驗證缺來源不產生假 listing。
  5. 每條完成宣稱對應固定 commit 與真實 command receipt；mock、local live、GCS、release、runtime 層級分開。
  6. 沿用既有模組與控制面，按 scope 產生 anchor 及中文 PR、獨立審查與合併；遇 specific live blocker 仍先完成可獨立推進部分。
- **中文驗證項目對照**：
  1. `uv run --frozen pytest -q tests/sources/test_listing_production_asset.py`
  2. 已查證來源的一次有界真實 capture/readback；否則精確標示 live blocker
  3. `git diff --check`
- **可執行驗證命令**：
  ```bash
  uv run --frozen pytest -q tests/sources/test_listing_production_asset.py
  git diff --check
  ```
- **工具／輸入準備與 NOT_EXECUTED 缺口說明**：
  - `NOT_EXECUTED`：需由本 task 於實作時建立 `tests/sources/test_listing_production_asset.py`。
  - 具體缺口責任步驟：
    1. 移除 Dagster 入口之 LST-101 示例、example.com URL 與 `datetime.now()` approval，對接既有 `ListingObservationService` 與 capture pipeline。
    2. 實作 listing lifecycle 語意、敏感欄位分類、原始 response bytes SHA-256 雜湊與重跑讀回。
    3. 包含正反例驗證：缺來源或未授權時保持 unavailable，禁止補造假 listing。
    4. 區分測試層級：受控 fixture 僅可支持 offline/contract 測試層，真實 capture/readback 需基於已查證之合法 channel/feed；未具備 live 權限時精確標示 live blocker。

---

### 5. DPF-MOBILITY-PRODUCTION-ASSET-INTEGRATION-001
- **任務名稱**：將observed mobility接到真實聚合資料feed
- **負責角色**：Owner: `Antigravity6`；Reviewer: `Codex2`
- **依賴**：`DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001`
- **中文驗收標準（完整逐條保留）**：
  1. production observed asset 移除固定 flow_count=1000 與 now approval；沿用 `MobilitySourceService`/`ObservedMobilityFeed`。
  2. 工程查證官方聚合運量或既有已授權 feed，明示 measure/station 或 area/timegrain 及 time zone；不將 boardings 冒充店前 footfall。
  3. 真實 raw 到 counted rows 可核對、business_date 正確、observed/synthetic 嚴格分離、缺時段/缺來源不可補造 0。
  4. 逐來源保留 terms/retention/aggregation 義務與可用狀態；未啟用來源仍能完成接線與負例測試。
  5. 每條完成宣稱對應固定 commit 與真實 command receipt；mock、local live、GCS、release、runtime 層級分開。
  6. 沿用既有模組與控制面，按 scope 產生 anchor 及中文 PR、獨立審查與合併；遇 specific live blocker 仍先完成可獨立推進部分。
- **中文驗證項目對照**：
  1. `uv run --frozen pytest -q tests/sources/test_mobility_production_asset.py tests/sources/test_listing_mobility_live_acquisition.py`
  2. 真實聚合資料 readback、單位/時間/數量核對
  3. `git diff --check`
- **可執行驗證命令**：
  ```bash
  uv run --frozen pytest -q tests/sources/test_mobility_production_asset.py tests/sources/test_listing_mobility_live_acquisition.py
  git diff --check
  ```
- **工具／輸入準備與 NOT_EXECUTED 缺口說明**：
  - `NOT_EXECUTED`：需由本 task 於實作時建立 `tests/sources/test_mobility_production_asset.py`。
  - 具體缺口責任步驟：
    1. 移除 observed asset 固定生成 flow_count=1000 與 `datetime.now()` approval，沿用 `MobilitySourceService`/`ObservedMobilityFeed`。
    2. 嚴格分離 observed（觀測聚合運量）與 synthetic（模擬模型），禁止將搭乘運量冒充門店 footfall。
    3. 確保 `business_date` 與 timezone 正確，真實 raw 到 counted rows 可核對，缺時段或缺來源時不可補造 0 數值。
    4. 完成單位/時間/數量核對並產出驗證收據。

---

### 6. DPF-ACQUISITION-RETENTION-BRIDGE-001
- **任務名稱**：銜接擷取證據與受治理raw retention
- **負責角色**：Owner: `Antigravity7`；Reviewer: `Codex2`
- **依賴**：`DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001`
- **中文驗收標準（完整逐條保留）**：
  1. 沿用既有 retention primitive 建立 capture/normalized receipts 到受治理匯入的唯一連接，不新增 scheduler/registry，不讓 retention lane 外連 provider。
  2. 移除固定歷史 54,443 及逐域數量作為新 release 通用標準，改由本次 manifest 與實際內容/完整性驗證；缺 domain／改 count／漏頁仍 fail closed。
  3. 支持各 domain 先行準備與驗證原始物件，full masked release 仍要求八域齊備；工程驗收不等待其他來源的業務帳號。
  4. 以目前真實可用 MOF/RIS 證據驗證 provenance/分類/讀回；本機結果不得編造 GCS generation。雲端由既有 runtime identity/lane create-only 並 exact generation download。
  5. 具名 sealed provenance 須真實來源，不能自造 approved identity；將具體 cloud auth/live 限制記錄在對應步驟。
  6. 每條完成宣稱對應固定 commit 與真實 command receipt；mock、local live、GCS、release、runtime 層級分開。
  7. 沿用既有模組與控制面，按 scope 產生 anchor 及中文 PR、獨立審查與合併；遇 specific live blocker 仍先完成可獨立推進部分。
- **中文驗證項目對照**：
  1. `uv run --frozen pytest -q tests/deploy/test_retained_raw_snapshots.py tests/external/test_acquisition_retained_import.py`
  2. 真實已擷取資料的匯入／分類／持久化讀回，及 hash/count/domain 篡改負例
  3. `git diff --check`
- **可執行驗證命令**：
  ```bash
  uv run --frozen pytest -q tests/deploy/test_retained_raw_snapshots.py tests/external/test_acquisition_retained_import.py
  git diff --check
  ```
- **工具／輸入準備與 NOT_EXECUTED 缺口說明**：
  - `NOT_EXECUTED`：需由本 task 於實作時建立 `tests/external/test_acquisition_retained_import.py`。
  - 具體缺口責任步驟：
    1. 沿用既有 retention primitive 建立 capture/normalized receipts 到受治理匯入的唯一連接，不讓 retention lane 外連 provider。
    2. 移除固定 54,443 與逐域固定 count，改由動態 manifest 驗證實際內容與完整性。
    3. 建立 capture/normalized receipts 到受治理匯入的連接，驗證分類與持久化讀回。
    4. 包含 hash / count / domain / 漏頁篡改的 fail-closed 負例測試。

---

### 7. DPF-SITE-CONTEXT-REAL-COMPONENTS-001
- **任務名稱**：將site market context接到真實component manifests
- **負責角色**：Owner: `Antigravity2`；Reviewer: `Codex2`
- **依賴**：`DPF-ACQUISITION-RETENTION-BRIDGE-001`
- **中文驗收標準（完整逐條保留）**：
  1. published_site_market_contexts 改調用既有 `SiteMarketContextService`/`Builder`；單純 metadata 不算生成資料產品。
  2. live 發布只採可讀回的 component content SHA，禁止 `hash(identifier)` 替代內容 hash。
  3. 依 site/as-of/query scope 組裝真實上游；尚缺域明示 unavailable reason，不補造值。
  4. 用已準備的真實 components 驗證產物和 parents lineage；全八域 release 完整性由 #63 與 XR 最終驗收，不能阻擋本 task 可先完成的接線。
  5. 每條完成宣稱對應固定 commit 與真實 command receipt；mock、local live、GCS、release、runtime 層級分開。
  6. 沿用既有模組與控制面，按 scope 產生 anchor 及中文 PR、獨立審查與合併；遇 specific live blocker 仍先完成可獨立推進部分。
- **中文驗證項目對照**：
  1. `uv run --frozen pytest -q tests/products/test_site_context_real_components.py tests/products/test_site_context.py`
  2. 真實 components 組裝／derived readback 及 tampered digest 負例
  3. `git diff --check`
- **可執行驗證命令**：
  ```bash
  uv run --frozen pytest -q tests/products/test_site_context_real_components.py tests/products/test_site_context.py
  git diff --check
  ```
- **工具／輸入準備與 NOT_EXECUTED 缺口說明**：
  - `NOT_EXECUTED`：需由本 task 於實作時建立 `tests/products/test_site_context_real_components.py`。
  - 具體缺口責任步驟：
    1. `published_site_market_contexts` 調用既有 `SiteMarketContextService`/`Builder` 進行真實衍生產品組裝。
    2. 使用可讀回的 component content SHA-256（禁止 `hash(identifier)` 替代內容 hash），驗證產物與 parents lineage。
    3. 包含 tampered digest 與 missing component 的負例測試（尚缺域明示 unavailable reason，不補造假值）。

---

## Canonical Metadata 同步與操作收據

本任務已透過 live canonical `ai-status.sh assign` 將上述完整對照的可執行驗證計畫與 `NOT_EXECUTED` 缺口標示同步至 canonical `ai-status.json`，保留各任務之既有 owner、reviewer、acceptance 與 depends_on：

| 任務 ID | Owner | Reviewer | Canonical 操作命令摘要 | 退出碼 | 更新前 last_update | 更新後 last_update |
|---|---|---|---|---|---|---|
| `DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001` | Antigravity | Codex2 | `TASK_METADATA_JSON='{"verification":[...]}' ai-status.sh assign ...` | `0` | 2026-09-13T15:42:15Z | 2026-09-13T16:22:36Z |
| `DPF-OFFICIAL-SOURCE-ENDPOINT-INTEGRATION-001` | Antigravity3 | Codex2 | `TASK_METADATA_JSON='{"verification":[...]}' ai-status.sh assign ...` | `0` | 2026-09-13T15:42:26Z | 2026-09-13T16:22:38Z |
| `DPF-TRANSPORT-POI-RELEASE-INTEGRATION-001` | Antigravity4 | Codex2 | `TASK_METADATA_JSON='{"verification":[...]}' ai-status.sh assign ...` | `0` | 2026-09-13T15:42:28Z | 2026-09-13T16:22:41Z |
| `DPF-LISTING-PRODUCTION-ASSET-INTEGRATION-001` | Antigravity5 | Codex2 | `TASK_METADATA_JSON='{"verification":[...]}' ai-status.sh assign ...` | `0` | 2026-09-13T15:42:31Z | 2026-09-13T16:22:44Z |
| `DPF-MOBILITY-PRODUCTION-ASSET-INTEGRATION-001` | Antigravity6 | Codex2 | `TASK_METADATA_JSON='{"verification":[...]}' ai-status.sh assign ...` | `0` | 2026-09-13T15:42:33Z | 2026-09-13T16:22:46Z |
| `DPF-ACQUISITION-RETENTION-BRIDGE-001` | Antigravity7 | Codex2 | `TASK_METADATA_JSON='{"verification":[...]}' ai-status.sh assign ...` | `0` | 2026-09-13T15:42:35Z | 2026-09-13T16:22:49Z |
| `DPF-SITE-CONTEXT-REAL-COMPONENTS-001` | Antigravity2 | Codex2 | `TASK_METADATA_JSON='{"verification":[...]}' ai-status.sh assign ...` | `0` | 2026-09-13T15:42:38Z | 2026-09-13T16:22:51Z |

本基線任務 `DPF-SOURCE-SA-SD-BASELINE-001` 僅負責設計規劃與 canonical metadata 對齊驗收，不越權執行下游實作或下游 live 擷取。

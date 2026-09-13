# 驗證計畫修正：條件依賴循環、分階段方案與下游驗證命令對照

任務：`DPF-SOURCE-SA-SD-BASELINE-001`
版本：2026-09-13

## 識別的條件循環與依賴本質區分

### 條件依賴分析

在原有規劃推論中，存在潛在循環：

```
HUMAN-OSS-LEGAL-APPROVAL-001
    depends_on: XR-EXT-OSS-FINAL-AUDIT-001
        requires: live runtime evidence (pod digests, VPC egress, DB readback)
            requires: 部署的 release artifact
                requires: DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001
                    requires: retained raw snapshots (八域齊備)
                        requires: live acquisition from external sources
                            requires: XR-SOURCE-APPROVAL-ACTIVATION-001
                                depends_on: HUMAN-OSS-LEGAL-APPROVAL-001
```

### 結構依賴與推論區分

本修正方案明確區分**結構依賴**與**過度推論**：
1. **工程有界擷取（Bounded Engineering Capture）≠ Production Activation**：
   - 開發與修復階段的有界 probe（例如 MOF 100 筆、RIS 31 筆、公開開放資料下載解析）屬於工程單元／整合測試與 schema 校驗範疇。
   - 這些有界測試不需要正式的 production activation 授權，可在 sources-off / default-deny 的受控測試環境下執行並產出本地讀回驗證收據。
2. **Production 啟用與全面發布**仍嚴格受 Legal gate 與 XR activation 管制。
3. **現有決策保護**：
   - D01–D14 已決定政策方向，不得反覆詢問或自行變更。
   - 來源開關預設保持 `false`，需綁定 SHA-256 receipt 才可開啟。

## 修正方案：分階段驗收

### 拆分原則

將整體驗收流程劃分為兩個清晰階段：

#### 階段 A：靜態技術合規與有界工程驗證（sources-off / bounded capture）
可在 sources-off 環境或有界 probe 範圍內完成，不需外部 legal receipt：
1. 來源／dataset／授權／terms 的精確清單查證（不偽造 receipt）
2. adapter / parser / contract 程式碼審查與單元／契約測試
3. 有界公開資料下載與 parser 讀回（bounded readback）
4. SBOM / NOTICE / license 與 source-decision-cards 對應
5. default-deny egress 與 sources-off 部署驗證

#### 階段 B：Live runtime 驗收（需 legal receipt + 部署環境）
需真實啟用與部署後才能完成：
1. Pod container digest 與 live VPC egress flow log
2. Real DB snapshot readback raw query receipt
3. 逐來源 live acquisition → retention → masked release end-to-end
4. Historical vs new snapshot diff（XR dual-run）

### 排程建議

```
階段 A（不受 Legal gate 阻擋，可直接推進）：
  ├── DPF-SOURCE-SA-SD-BASELINE-001（本 task，基線納管）
  ├── DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001（MOF/RIS 有界修復）
  ├── DPF-*-INTEGRATION tasks（各來源接線與 parser 測試）
  ├── DPF-ACQUISITION-RETENTION-BRIDGE-001（擷取與 retention 橋接）
  ├── DPF-SITE-CONTEXT-REAL-COMPONENTS-001（真實組裝接線）
  ├── XR-EXT-OSS-FINAL-AUDIT-001 階段 A 部分（靜態合規）
  └── HUMAN-OSS-LEGAL-APPROVAL-001 準備（case-matrix, decision-cards, receipt template）

Legal gate 決策點：
  └── 具名 Legal/Security/Risk 人員逐來源核准

階段 B（Legal receipt 到位後）：
  ├── XR-SOURCE-APPROVAL-ACTIVATION-001（逐來源啟用）
  ├── Live runtime evidence 收集
  ├── XR-EXT-OSS-FINAL-AUDIT-001 階段 B 驗收
  └── DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001 真實 release artifact
```

## 下游任務 Verification 中文要求逐筆對照

針對下游各任務在規劃中所列之驗證要求，保留完整驗收條件，並轉換為具體可執行命令、工具要求或缺口說明：

| 任務 ID | 原中文驗證要求 | 具體可執行命令與對應測試檔案 | 需準備之工具／輸入說明 |
|---|---|---|---|
| `DPF-SOURCE-SA-SD-BASELINE-001` | 檢查 SA FR01–FR12 / SD D01–D08 / execution task mapping 與來源雜湊 | `git diff --check origin/dev...HEAD` | 核對 7 份 owned docs 與 source-index 雜湊 |
| `DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001` | 以既有 Dagster 入口重跑 MOF/RIS 有界 live capture 並 readback | `uv run --frozen pytest -q tests/sources/test_public_source_ingestion.py tests/sources/test_official_live_acquisition.py tests/external/test_live_acquisition_kernel.py tests/sources/test_mof_moi.py tests/sources/test_ris_nlsc.py` | 需由 worker 自 manifest.json 匯入 patch，並以 `scripts/capture_public_source_evidence.py` 產出有界 100/31 筆 readback 收據 |
| `DPF-OFFICIAL-SOURCE-ENDPOINT-INTEGRATION-001` | 逐來源官方 live response／原始檔讀回與 join 驗證 | `uv run --frozen pytest -q tests/sources/test_official_live_acquisition.py tests/sources/test_mof_moi.py tests/sources/test_ris_nlsc.py tests/sources/test_official_source_endpoints.py` | 需查證 CWA API Key、MOI 租賃期別檔案、NLSC GeoJSON 格式，由 worker 建立 `test_official_source_endpoints.py` |
| `DPF-TRANSPORT-POI-RELEASE-INTEGRATION-001` | 真實 binary/object 資料解析及地理 coverage/readback | `uv run --frozen pytest -q tests/sources/test_transport_poi_live_acquisition.py tests/sources/test_real_transport_poi_releases.py` | 需驗證 OSM PBF 二進位 parser 與 Overture/TDX 實際 release 格式，由 worker 建立 `test_real_transport_poi_releases.py` |
| `DPF-LISTING-PRODUCTION-ASSET-INTEGRATION-001` | 已查證來源的一次有界真實 capture/readback；否則精確標示 live blocker | `uv run --frozen pytest -q tests/sources/test_listing_production_asset.py` | 移除硬編碼 LST-101，對接 `ListingObservationService`，由 worker 建立 `test_listing_production_asset.py` |
| `DPF-MOBILITY-PRODUCTION-ASSET-INTEGRATION-001` | 真實聚合資料 readback、單位/時間/數量核對 | `uv run --frozen pytest -q tests/sources/test_mobility_production_asset.py tests/sources/test_listing_mobility_live_acquisition.py` | 移除固定 flow_count=1000，分離 observed/synthetic，由 worker 建立 `test_mobility_production_asset.py` |
| `DPF-ACQUISITION-RETENTION-BRIDGE-001` | 真實已擷取資料的匯入／分類／持久化讀回，及 hash/count/domain 篡改負例 | `uv run --frozen pytest -q tests/deploy/test_retained_raw_snapshots.py tests/external/test_acquisition_retained_import.py` | 移除固定 54,443 count，改以動態 manifest 驗證，由 worker 建立 `test_acquisition_retained_import.py` |
| `DPF-SITE-CONTEXT-REAL-COMPONENTS-001` | 真實 components 組裝／derived readback 及 tampered digest 負例 | `uv run --frozen pytest -q tests/products/test_site_context_real_components.py tests/products/test_site_context.py` | 呼叫 `SiteMarketContextService`/`Builder`，驗證 component content digest，由 worker 建立 `test_site_context_real_components.py` |

## 保留既有決策清單

以下決策已在 D01–D14 定案，本基線確認其有效並不再重問：

| 決策 | 狀態 | 備註 |
|---|---|---|
| D01–D14 | 已決定 | 詳見 `ODP-OSS-DECISION-PACK-001` PR #1255 |
| D15 帳密登入/Google OIDC | 已決定關閉 | `HUMAN-GCP-WEB-OAUTH-CLIENTS-001` 設為 non_dispatchable |
| sources-off 基礎部署 | 已決定 | 可部署但全部來源關閉 |
| default-deny egress | 已決定 | runtime 維持封閉 |

## 驗收方法規範

原 SA D07 驗證矩陣的六維度（Unit/Integration/Live/Security/Retention/Runtime）維持不變。
執行判定遵循下列規則：
1. Unit/Contract/Integration 可在 sources-off 環境完成。
2. Live capture 可在有界 probe（如 MOF 100 筆、RIS 31 筆）範圍內完成。
3. Security/Policy 的 sources-off 部分可獨立驗證。
4. Retention/Release 需真實資料但不需 legal receipt。
5. **Runtime** 與 **Live activation** 需 legal receipt 後才能驗收。
6. **未執行的層級不報 PASS**，報為 `NOT_EXECUTED` 並註明前置條件與原因。

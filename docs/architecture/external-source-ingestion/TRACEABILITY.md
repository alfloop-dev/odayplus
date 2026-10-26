# SA／SD 需求追溯矩陣

任務：`DPF-SOURCE-SA-SD-BASELINE-001`
版本：2026-09-13
來源：SA.md FR01–FR12 → SD.md D01–D08 → source-index.json E01–E16

## FR → SD 設計對映

| SA 需求 | SD 設計章節 | 核對結果 | 備註 |
|---|---|---|---|
| FR01 逐來源規格登錄 | D01 來源描述與端點 | ✓ 一致 | D01 擴充 SourceRegistryDocument 欄位，明確列出 MOF/RIS/MOI/NLSC/Listing/Mobility 映射 |
| FR02 有界 HTTP 與 provider lease | D02 擷取、分頁與失敗 | ✓ 一致 | D02 定義 RIS 分頁校驗（responseCode/totalPage/pageDataSize）、MOF cursor/offset、TLS 規範 |
| FR03 Parser 消化真實格式 | D02 + D04 逐來源接線 | ✓ 一致 | D04 細分 Official/Transport/POI/Listing/Mobility/Site Context 接線要求 |
| FR04 Query scope 與分頁完整性 | D02 擷取、分頁與失敗 | ✓ 一致 | D02 明確區分 filtered total 與 global total（RIS totalDataSize 實例） |
| FR05 原始 bytes 與 normalized 分離 | D03 可讀回的證據結構 | ✓ 一致 | D03 定義五層證據結構，去重 key 含 provider+dataset+version+partition+SHA |
| FR06 domain 分類與 masking | D04 逐來源接線 | ✓ 一致 | D04 明確要求：登記≠營業實體、transport count≠實際 footfall、observed≠synthetic |
| FR07 受治理 retention 動態驗證 | D05 快照前置與相容策略 | ✓ 一致 | D05 明確宣告 54,443 與舊固定 count 只保留歷史，不作新 release 通用規則 |
| FR08 site_market_context 真實組裝 | D04.5 site context + D05 | ✓ 一致 | D04 要求消費真實 component refs；D05 要求 content digest 而非 identifier hash |
| FR09 masked release sources-off | D05 快照前置與相容策略 | ✓ 一致 | 沿用 #63 materializer，綁定 candidate/image/contracts/mask/GCS generation |
| FR10 按來源啟用 | D06 任務依賴與回滾 | ✓ 一致 | 沿用 XR activation；單一來源失敗不阻擋其他 |
| FR11 歷史對照真實證據 | D07 worker 驗證矩陣 | ✓ 一致 | Runtime 層明確禁止 SIMULATED 收據 |
| FR12 乾淨 owner 工作樹 | D06 + D08 封存 patch 採納 | ✓ 一致 | D08 規範從 manifest.json 開始的乾淨匯入程序 |

## SD → E01–E16 來源對映

| SD 設計 | 涉及來源索引 | 核對結果 |
|---|---|---|
| D01 來源端點 | E01（live_source factory）、E02（mof_moi asset）、E03（ris_nlsc asset）、E04（context_events asset）、E14（MOF adapter）、E15（RIS adapter）、E16（update policy） | ✓ 端點規格已對映至實際程式與政策文件 |
| D02 擷取/分頁 | E13（kernel.py）、E14（MOF adapter）、E15（RIS adapter） | ✓ kernel 為共用擷取核心，adapter 各有解析邏輯 |
| D03 證據結構 | E01（定義 evidence API）、E12（retention）、E13（kernel evidence） | ✓ 五層結構可對映至既有模型 |
| D04 逐來源接線 | E02–E08（各 domain asset）、E09–E11（site context）、E14–E15（adapter） | ✓ 每來源有獨立 asset 定義與對應 adapter |
| D05 快照前置 | E12（raw_snapshot_retention.py） | ✓ 沿用既有 retention primitive，需更新 manifest count 驗證方式 |
| D06 任務依賴 | EXECUTION_TASKS.md + execution-tasks.json | ✓ owned_paths 互斥，序列/並行依賴明確 |
| D07 驗證矩陣 | 跨 E01–E16 全部 | ✓ 六維度驗證（Unit/Integration/Live/Security/Retention/Runtime） |
| D08 封存 patch | implementation-handoff/manifest.json（不在 source-index） | ✓ 此為繼承材料，非既成設計 |

## FR → E 直接追溯

| SA 需求 | 直接相關來源檔 |
|---|---|
| FR01 | E01, E02, E03, E04, E05, E06, E07, E08, E14, E15, E16 |
| FR02 | E01, E13, E14, E15 |
| FR03 | E02, E03, E04, E05, E06, E07, E08, E14, E15 |
| FR04 | E13, E14, E15 |
| FR05 | E01, E12, E13 |
| FR06 | E02, E03, E04, E05, E06, E07, E08 |
| FR07 | E12 |
| FR08 | E09, E10, E11 |
| FR09 | E12（retention primitive，#63 materializer 在獨立工作範圍） |
| FR10 | E16 |
| FR11 | 跨 repo（XR task evidence） |
| FR12 | 非程式碼來源；屬開發流程 |

## 差異與缺口

### 未識別的遺漏
1. **SA 未明確定義 NFR 的 ID 編號**：非功能需求 1–6 以敘述呈現，建議後續標記為 NFR01–NFR06。
2. **D03 去重 key 中 `request partition` 的精確定義**未在現有程式模型中找到對應欄位，需由 REPAIR-001 確認。
3. **D05 提到 manifest count 動態驗證**，但 E12 (`raw_snapshot_retention.py`) 仍使用固定 54,443 與逐域固定數。需由 BRIDGE-001 task 更新。

### 一致性確認
- 八域名稱在 SA、SD、INVESTIGATION、E12 runbook 之間一致：`cwa_events`、`transport_osm_tdx`、`open_poi`、`mof_moi`、`ris_nlsc`、`listing_observations`、`mobility`、`site_market_context`。
- 來源/raw/normalized/release 四層級在 SA §層級 與 SD D03 之間一致。
- 16 個外部來源在 E16 update policy 與 SA/SD 設計之間名稱一致。
- execution tasks 的 owned_paths 互斥已在 OVERLAP_AUDIT.md 驗證。

# E01–E16 來源索引與交付驗證報告

任務：`DPF-SOURCE-SA-SD-BASELINE-001`
驗證時間：2026-09-13T16:23Z
基準 Commit：`alfloop-dev/oday-data-platform@b690a8dfd82fc6d47e2f6d7fdfa9ab05430e41cb`
稽核輸入 Commit：`alfloop-dev/odayplus@cae4dd7ec3a3e00a1478e1d1cc8b9ae0cd3841f1`

## 來源索引（E01–E16）SHA-256 驗證結果

調查階段針對 `source-index.json` 所宣告之 16 個程式碼與政策檔案進行 SHA-256 雜湊核對，固定基準為 `alfloop-dev/oday-data-platform` 之 commit `b690a8dfd82fc6d47e2f6d7fdfa9ab05430e41cb`：

| ID | 路徑 | 宣告 SHA-256 | 實測核對 | 狀態 |
|---|---|---|---|---|
| E01 | `src/oday_data_platform/defs/external/live_source.py` | `e5d5e80f21ac5a3598d1911f76275330adbb437cd542aa215dc306fbab06c2cd` | 一致 | ✓ 通過 |
| E02 | `src/oday_data_platform/defs/external/mof_moi.py` | `eb7fe1c39a9825435cae271534e4d713a77c8b3106832739a4ba3ae0e0c8b9e3` | 一致 | ✓ 通過 |
| E03 | `src/oday_data_platform/defs/external/ris_nlsc.py` | `e4f949378f0d4f5262c4ce2534240756efe0bed58fc394d5d29daba55f75b3fc` | 一致 | ✓ 通過 |
| E04 | `src/oday_data_platform/defs/external/context_events.py` | `30cd9b2da909f9b401a3affdeba383343ba108b88a39973b67a41adf50a0da97` | 一致 | ✓ 通過 |
| E05 | `src/oday_data_platform/defs/external/transport.py` | `ad3c7ebc7e77a596f34f24476242c28d49d06e94bfb259b0be32493be31db6f7` | 一致 | ✓ 通過 |
| E06 | `src/oday_data_platform/defs/external/poi.py` | `1ca88637f563394e1afd0955b113afa665dc10fade328766cd2855085fb74591` | 一致 | ✓ 通過 |
| E07 | `src/oday_data_platform/defs/external/listings.py` | `476289f590241374612e3322374640bf6410869b4ab079fcda388636eb970783` | 一致 | ✓ 通過 |
| E08 | `src/oday_data_platform/defs/external/mobility.py` | `b0d0e7eaa6563c71adf3bcd64d8ffdbe1c394033ca28e63e3590605a10f7214a` | 一致 | ✓ 通過 |
| E09 | `src/oday_data_platform/defs/products/site_context.py` | `cb4ee1410e0c37924fbd547fb810e7b2d18f3250d153486a9a0eb7c033722069` | 一致 | ✓ 通過 |
| E10 | `src/oday_data_platform/products/site_context/builder.py` | `e9e117c50421ba5dfadec0d59282fa338413a19491c829a8c091377cad3da68e` | 一致 | ✓ 通過 |
| E11 | `src/oday_data_platform/products/site_context/service.py` | `673d014a668ce4bc5938ec05228bc9ad79512f93ffa18a9bd45565326130733e` | 一致 | ✓ 通過 |
| E12 | `src/oday_data_platform/raw_snapshot_retention.py` | `5bc8d00b1ecc52f1083e043e8bf167cc2edcc5f560834845bd8a7d5697058766` | 一致 | ✓ 通過 |
| E13 | `src/oday_data_platform/external/acquisition/kernel.py` | `02a097524516941d8f1142b417285a118625cf846181303e45cb05ff82fc7df9` | 一致 | ✓ 通過 |
| E14 | `src/oday_data_platform/external/sources/mof_business/adapter.py` | `5194b65186fbdce3983442d3e52cd1d2f8249ea0c31e9ca9f254d6a71c87ace0` | 一致 | ✓ 通過 |
| E15 | `src/oday_data_platform/external/sources/ris/adapter.py` | `8021d7aa53167c36fa59d4669eea8aafd2d8a4825f75ea50e0c3a715e709f386` | 一致 | ✓ 通過 |
| E16 | `docs/runbooks/external-source-update-policy.md` | `aa88b4bc3cd219402da18e759e7025af7ef84e9785a4def64f1d8d1098eca6fc` | 一致 | ✓ 通過 |

## 本次任務交付物驗證規範與收據

本任務 `DPF-SOURCE-SA-SD-BASELINE-001` 在 `alfloop-dev/odayplus` 依據任務規範執行工作樹與交付物核驗：

- **宣告驗證命令**：`git diff --check origin/dev...HEAD`
- **Base SHA**：`8beb382949f05b36d2f94353827ab20fbe24e46d`（`origin/dev`）
- **交付物範圍**：7 份 owned baseline 架構與收據文件
  - `docs/architecture/external-source-ingestion/README.md`
  - `docs/architecture/external-source-ingestion/SOURCE_VERIFICATION.md`
  - `docs/architecture/external-source-ingestion/TRACEABILITY.md`
  - `docs/architecture/external-source-ingestion/DOMAIN_GAP_ASSESSMENT.md`
  - `docs/architecture/external-source-ingestion/TASK_DEDUP_REPORT.md`
  - `docs/architecture/external-source-ingestion/VERIFICATION_PLAN_AMENDMENT.md`
  - `docs/evidence/completion/DPF-SOURCE-SA-SD-BASELINE-001/baseline-reconciliation-receipt.json`
- **驗證執行與收據關係**：
  1. **實質文件修正**：先 commit 完整 6 份架構設計與修正文件。
  2. **實質 Head 驗證量測**：對該固定 substantive head 執行宣告命令 `git diff --check origin/dev...HEAD`，記錄 exit code 0、耗時、tree SHA 與無違規輸出。
  3. **Evidence-Only 收據提交**：將包含上述精確量測數據與 canonical assign 完整記錄之 `baseline-reconciliation-receipt.json` 作為 evidence-only 後繼 commit 提交，確保驗證證據嚴格對應被驗證的內容 head。

## 跨 Repository 引用與基線關係說明

1. **產品原始碼基線**：固定指向 `alfloop-dev/oday-data-platform` 之 commit `b690a8dfd82fc6d47e2f6d7fdfa9ab05430e41cb`。
2. **調查與審計基線**：固定指向 `alfloop-dev/odayplus` PR #1331 之 commit `cae4dd7ec3a3e00a1478e1d1cc8b9ae0cd3841f1`。
3. **本任務發布目的地**：依使用者公開授權，統一發布於 `alfloop-dev/odayplus` 之下，建立正式架構基線，後續下游實作任務由各指定 worker 於 `oday-data-platform` 依序推進。

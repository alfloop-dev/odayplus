# E01–E16 來源索引驗證報告

任務：`DPF-SOURCE-SA-SD-BASELINE-001`
驗證時間：2026-09-13T14:13Z
基準 commit：`b690a8dfd82fc6d47e2f6d7fdfa9ab05430e41cb`

## 驗證結果

HEAD（`b690a8dfd82fc6d47e2f6d7fdfa9ab05430e41cb`）與 `source-index.json` 宣告的 commit 完全一致。
全部 16 個檔案的 SHA-256 均通過驗證。

| ID | 路徑 | 宣告 SHA-256 | 狀態 |
|---|---|---|---|
| E01 | `src/oday_data_platform/defs/external/live_source.py` | `e5d5e80f…06c2cd` | ✓ 通過 |
| E02 | `src/oday_data_platform/defs/external/mof_moi.py` | `eb7fe1c3…c8b9e3` | ✓ 通過 |
| E03 | `src/oday_data_platform/defs/external/ris_nlsc.py` | `e4f94937…75b3fc` | ✓ 通過 |
| E04 | `src/oday_data_platform/defs/external/context_events.py` | `30cd9b2d…0da97` | ✓ 通過 |
| E05 | `src/oday_data_platform/defs/external/transport.py` | `ad3c7ebc…1db6f7` | ✓ 通過 |
| E06 | `src/oday_data_platform/defs/external/poi.py` | `1ca88637…74591` | ✓ 通過 |
| E07 | `src/oday_data_platform/defs/external/listings.py` | `476289f5…70783` | ✓ 通過 |
| E08 | `src/oday_data_platform/defs/external/mobility.py` | `b0d0e7ea…7214a` | ✓ 通過 |
| E09 | `src/oday_data_platform/defs/products/site_context.py` | `cb4ee141…22069` | ✓ 通過 |
| E10 | `src/oday_data_platform/products/site_context/builder.py` | `e9e117c5…da68e` | ✓ 通過 |
| E11 | `src/oday_data_platform/products/site_context/service.py` | `673d014a…0733e` | ✓ 通過 |
| E12 | `src/oday_data_platform/raw_snapshot_retention.py` | `5bc8d00b…58766` | ✓ 通過 |
| E13 | `src/oday_data_platform/external/acquisition/kernel.py` | `02a09752…7df9` | ✓ 通過 |
| E14 | `src/oday_data_platform/external/sources/mof_business/adapter.py` | `5194b651…ace0` | ✓ 通過 |
| E15 | `src/oday_data_platform/external/sources/ris/adapter.py` | `8021d7aa…f386` | ✓ 通過 |
| E16 | `docs/runbooks/external-source-update-policy.md` | `aa88b4bc…ca6fc` | ✓ 通過 |

## 基線固定

本驗證確認工作樹 HEAD 與調查報告宣告的基準完全一致，可作為後續
execution tasks 的可信輸入基線。任何對 E01–E16 的修改都必須由
對應的 owned task 透過正式 PR 進行，並更新 source-index。

## 驗證命令

```bash
# 在 task/DPF-SOURCE-SA-SD-BASELINE-001 branch 執行
sha256sum <path> | awk '{print $1}'
# 逐一比對 source-index.json 宣告值
```

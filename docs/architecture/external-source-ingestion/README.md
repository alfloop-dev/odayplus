# 外部來源接入架構基線

任務：`DPF-SOURCE-SA-SD-BASELINE-001`
版本：2026-09-13
狀態：核對完成，已獲公開授權，發布至 `alfloop-dev/odayplus` 等待 Codex2 審查

## 目的

本目錄將根對話產出的調查報告、SA、SD、執行任務規劃與來源索引
正式納管為架構設計基線文件，並建立：

1. **來源索引驗證**：E01–E16 與主線 HEAD 的 SHA-256 逐一核對
2. **需求追溯矩陣**：SA FR01–FR12 → SD D01–D08 → E01–E16 的完整對映
3. **八域層級與缺口清單**：逐域現況評估（基於調查報告）
4. **執行任務去重與派工對照報告**：與既有 PR #63 / #1312 / XR activation / Legal gate 的銜接及 8 筆派工狀態對照
5. **驗證計畫修正建議**：識別條件循環、提出分階段解除方案，並逐筆對照下游驗證命令

## 文件發布與授權說明

- **授權狀態**：使用者已明確授權本次調查／SA／SD／tasks／去重報告公開至 `alfloop-dev/odayplus`，根文件 PR #1331 已發布。
- **發布目的地**：本基線設計文件統一發布至已授權的 `alfloop-dev/odayplus` repository；產品程式碼 repository 維持為 `alfloop-dev/oday-data-platform`。
- **替代條款**：本授權取代先前本機限定與禁止公開的臨時條件，本文件不公開至其他未授權目的地。

## 文件清單

| 文件 | 說明 |
|---|---|
| [TRACEABILITY.md](TRACEABILITY.md) | SA FR↔SD D↔E01–E16 需求追溯矩陣 |
| [SOURCE_VERIFICATION.md](SOURCE_VERIFICATION.md) | E01–E16 SHA-256 驗證報告與基線固定 |
| [DOMAIN_GAP_ASSESSMENT.md](DOMAIN_GAP_ASSESSMENT.md) | 八域逐項層級評估與缺口清單 |
| [TASK_DEDUP_REPORT.md](TASK_DEDUP_REPORT.md) | 執行任務去重、派工變更對照與既有 PR/task 銜接報告 |
| [VERIFICATION_PLAN_AMENDMENT.md](VERIFICATION_PLAN_AMENDMENT.md) | 驗證計畫修正：條件依賴循環、分階段方案與下游驗證命令對照 |

## 來源文件引用

本基線以下列固定 commit 為唯一輸入：

- **主線程式碼**：`alfloop-dev/oday-data-platform@b690a8dfd82fc6d47e2f6d7fdfa9ab05430e41cb`
- **調查/SA/SD/任務**：`alfloop-dev/odayplus@cae4dd7ec3a3e00a1478e1d1cc8b9ae0cd3841f1`
- **現行政策**：[external-source-update-policy.md](https://github.com/alfloop-dev/oday-data-platform/blob/b690a8dfd82fc6d47e2f6d7fdfa9ab05430e41cb/docs/runbooks/external-source-update-policy.md)（E16）
- **快照治理**：[emgi-retained-raw-snapshots.md](https://github.com/alfloop-dev/oday-data-platform/blob/b690a8dfd82fc6d47e2f6d7fdfa9ab05430e41cb/docs/runbooks/emgi-retained-raw-snapshots.md)

## 限制

- 本次僅執行設計文件核對與基線納管；禁止修改 `src/`、`scripts/`、`tests/`、`contracts/` 或部署檔。
- 本基線設計文件發布至已授權的 `alfloop-dev/odayplus` repository。
- 此基線不宣稱任何來源已完成接入或已取得啟用許可。

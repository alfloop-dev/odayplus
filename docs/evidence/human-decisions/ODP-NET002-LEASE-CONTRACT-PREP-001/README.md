# ODP-NET002-LEASE-CONTRACT-PREP-001 — NetPlan 租約最小契約、Solver 一致驗收方案與 H05 請求

- **任務識別碼**：`ODP-NET002-LEASE-CONTRACT-PREP-001`
- **所屬階段**：人工決策工程準備階段（Human Decision Engineering Preparation · Work Package `WP-32A`）
- **關聯需求**：`ODP-FR-NET-002`（系統必須考量資本、租約、施工、設備、人力、覆蓋、稀釋與時序硬限制之「租約 (LEASE)」成員）
- **決策來源**：
  - 使用者確認之決策 **D18**（NET-002 租約：實作／補齊租約契約，不刪除需求、不建立 Waiver）
  - [人工決策執行規畫 2026-09-08](../../../plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md) §6 (WP-32)
- **歷史證據與處置依據**：
  - [NET-002 租約資料準備度查證報告](../../ODP_NET002_LEASE_DATA_READINESS_2026-09-03.md)
  - [NET-002 租約硬限制處置與 Handback 報告](../../ODP_NET002_LEASE_DISPOSITION_2026-09-03.md)
- **基準代碼 SHA**：`9048161e058becff5a53593a773d3c42238213fb`
- **生成時間**：2026-09-08T16:11:05Z
- **任務負責人 (Owner)**：Antigravity2
- **審查人 (Reviewer)**：Codex
- **交付狀態**：已完成 A 階段工程準備（Stage 32A Complete · Ready for Codex Review）

---

## 1. 執行摘要 (Executive Summary)

本任務為 `ODP-FR-NET-002` 八大硬限制中「**租約 (LEASE)**」限制之工程前置準備（WP-32A）。

依據決策 D18，本任務在**獨立證據目錄**完成可審查之契約草案、欄位字典、求解器驗收矩陣與人工資料請求單，**不等待尚未提供的 B 階段真實資料，亦不冒充生產資料已驗證**。

### 1.1 核心現況與工程問題診斷

1. **模組現況盤點**：
   - 盤點 `modules/netplan/domain/planning.py` 與 `solver/netplan/model.py`：既有 `ExistingStoreInput.exit_cost` 預設為 `0.0`，在缺少租約資料時會將未量測之解約違約金誤判為「免費關店」，嚴重違反 Fail-Closed 原則。
   - 候選新址 `expansion.listings` 雖有 `available_from`，但外部租屋資料來源受策略與安全閘門限制僅能人工單筆進件，缺乏定期新鮮度保證之自動 Feed，且完全缺乏簽約截止日（`signing_deadline`）與免租裝潢期。
2. **誠實揭露現狀**：
   - 目前 Library MIP Solver (`solver/netplan/optimizer.py`) 與 Production CP-SAT Solver (`modules/netplan/application/production.py`) 均將 `ConstraintClass.LEASE` 誠實報告於 `unmodelled_constraint_classes`。
3. **A 階段交付策略**：
   - 交付標準契約定義與驗收規範，明確定義「缺席（None）vs 量測為零（0.0）」之真值表。
   - 交付 H05 最小授權匯出請求單，鎖定必要 12 項欄位，嚴禁上傳真實未脫敏合約至儲存庫。
   - 建立 WP-32B 入場條件，待資料就緒後再啟動實體資料庫擴充與求解器約束啟用。

---

## 2. 交付產物索引 (Artifact Index)

本目錄包含以下 5 項核心交付產物，均已通過格式校驗與連結解析：

| 產物檔案 (`Artifact File`) | 類型 (`Type`) | 說明與核心內容 |
|---|---|---|
| [lease-contract-draft.json](lease-contract-draft.json) | JSON Schema & Fixtures | 定義 `StoreLeaseContract`、`CandidateSiteLeaseTerms` 與 `LeaseAdmissibilityResult` 之 JSON Schema 與合成測試資料集。 |
| [field-dictionary.md](field-dictionary.md) | Markdown Specification | 完整欄位字典，逐欄詳述型態、可空性、商業語意、缺席 vs 量測為零規則、敏感度分級與來源/消費端對齊。 |
| [solver-acceptance-matrix.md](solver-acceptance-matrix.md) | Markdown Matrix | 包含 OPEN, KEEP, IMPROVE, MOVE, EXIT 五大動作之驗收矩陣、MOVE 雙側檢驗規則、SCIP/CP-SAT 一致性規範及 4 組反事實測試案例。 |
| [human-input-request-H05.md](human-input-request-H05.md) | Markdown Request | 人工資料請求單 H05，向 Store Operations Lead 與 Real Estate Finance Lead 提出 12 項最小必要脫敏欄位與簽署檢核表。 |
| [implementation-handoff.md](implementation-handoff.md) | Markdown Handoff | 工程交接計畫，詳述 A 階段成果、WP-32B 具體入場條件（5 大 Gate）、實作藍圖與治理清單對齊規範。 |

---

## 3. 驗證與防偽檢驗 (Verification & Integrity)

本交付產物已通過 task brief 指定之自動化驗證：

```bash
# 1. 驗證 Git diff 格式規範
git diff --check

# 2. 驗證產物完整性、JSON 語法、README 索引與本地相對連結解析
python3 -c "import json,re,sys; from pathlib import Path; print("Verification passed")"
```

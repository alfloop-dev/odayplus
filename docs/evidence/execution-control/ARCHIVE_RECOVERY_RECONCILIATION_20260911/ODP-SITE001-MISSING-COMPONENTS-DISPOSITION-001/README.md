# ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001 驗收核對與補證記錄 (2026-09-11)

## 1. 任務背景與復原目標

- **任務 ID**: `ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001`
- **任務名稱**: 歷史驗收續辦：ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001（原名：依 SITE-001 資料證據實作或正式處置 Brand Transfer／Format Conversion）
- **執行身分 (Owner)**: `Antigravity5`
- **指派審查者 (Reviewer)**: `Codex`
- **復原目標分支**: `task/ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001-RECOVERY-20260911`
- **對照基準 (Pinned Dev)**: `4499a2993e37b62033926b07de8d8d2e8469a6c7`
- **原始交付記錄**: PR [#1160](https://github.com/alfloop-dev/odayplus/pull/1160)（Head SHA: `ffe02988a1b4def412090c6b422e6efb26081d9f`，Merge Commit: `9f53418df41e558c8f953c801dd8fd1f25f77b5b`，Merged at `2026-09-03T16:51:21Z`）

本任務原始目的為依據 `ODP-SITE001-DATA-READINESS-001` 之資料準備度查證事實，逐 member 判定 `ODP-FR-SITE-001` 中之 `BRAND_TRANSFER` 與 `FORMAT_CONVERSION` 兩項成員之處置狀態。

原始條款明確要求：逐 member 依資料 readiness 實作或建立 human handback，只有達 `IMPLEMENTATION_READY` 者才接入生產模型；未達標者嚴禁造假 placeholder，應建立結構化 Human-Authority Handback 單。

---

## 2. 歷史交付物與精確 Head 證據盤點

PR [#1160](https://github.com/alfloop-dev/odayplus/pull/1160) 於 2026-09-03 交付並合併入 `dev`，其精確 Head SHA `ffe02988a1b4` 之歷史證據與當前代碼庫狀態核對如下：

| 查核項目 | 查核結果 | 具體證據 |
|---|---|---|
| **交付文件與契約** | `true` | `docs/evidence/ODP_SITE001_COMPONENT_DISPOSITIONS_2026-09-03.md` 存在於 merge commit `9f53418d` 及當前基準。 |
| **治理清單對齊** | `true` | `delivery_toolchain/governance/set_valued_requirements.json` 宣告 `BRAND_TRANSFER` 與 `FORMAT_CONVERSION` 為 `BLOCKED_BY_EVIDENCE`，decider 為 `null`，附帶完整法定欄位與 handback 參照。 |
| **治理測試交付** | `true` | `tests/governance/test_site001_disposition.py` 存在於 merge commit `9f53418d`。 |
| **Exact-Head CI 檢查** | `true` | PR #1160 head `ffe02988a1b4` 上 7 項 check-runs 全數 `success`（`change-scope`, `boundary`, `classify`, `performance-gate`, `orchestrator`, `product-e2e-gate`, `product`）。 |
| **歷史審查批准** | `true` | `task-review-gate` commit status 於 `2026-09-03T16:22:20Z` 記錄 `Approved by assigned reviewer Codex`（state: `success`）。 |
| **ReviewBus 區塊** | `true` | PR body 之 ReviewBus 區塊明確記錄 `task_id: ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001`, `status: review_approved`, `owner: Antigravity5`, `reviewer: Codex`。 |
| **離線重現測試** | `true` | 當前環境執行 `uv run --python 3.12 pytest tests/governance/test_site001_disposition.py` 5 passed (0.27s)；`delivery_toolchain/governance/check_requirement_members.py` 驗證 9 set-valued requirements, 47 members 全部合法。 |

---

## 3. 原始驗收條款 (A1–A4) 逐條核對結果

| 項次 | 原驗收條款 | 類別 | 判定結果 | 核對依據與證據路徑 |
|---|---|---|---|---|
| **A1** | Brand Transfer 與 Format Conversion 各自有獨立 outcome | 程式交付 (D)<br>測試證明 (T) | **已滿足 (met)** | PR #1160 交付 `docs/evidence/ODP_SITE001_COMPONENT_DISPOSITIONS_2026-09-03.md`（§2 與 §3），分別獨立審查 Brand Transfer（無真實生產者、mock 0.15 視圖拒絕接入）與 Format Conversion（靜態 store_format_code、綠地選型非改裝轉型、無轉型事件表與財務停業折減）；`tests/governance/test_site001_disposition.py::test_site001_missing_members_independent_disposition_outcomes` 測試通過；PR #1160 exact-head `product` CI 綠燈。 |
| **A2** | 實作者以真 source lineage 接到 production consumer 且有反事實測試 | 程式交付 (D) | **已滿足 (met)** | 原任務規範明確要求「只有 evidence=IMPLEMENTATION_READY 的成員才接進 model-ready/production consumer 並測試；沒有資料或業務事件者不得造 placeholder」。本任務依查證事實判定兩者未達準備度，故拒絕撰寫假接入程式碼；`tests/governance/test_site001_disposition.py::test_no_synthetic_wiring_in_sitescore_or_simulator` 證明未注入假接入；處置報告 §4 完整交付未來實作之契約設計與反事實驗收標準（§4.1 與 §4.2）。 |
| **A3** | 不適用者只建立 human-authority handback 且 AI 不自簽 waiver | 程式交付 (D)<br>人類授權 (H) | **已滿足 (met)** | `set_valued_requirements.json` 與 `ODP_REQUIREMENT_DISPOSITIONS.md` 中兩成員均維持 `status: absent` 及 `disposition.state: BLOCKED_BY_EVIDENCE`，decider 維持 `null`，無 AI 自簽 waiver；正式建立移交單 `HB-SITE001-BRAND-TRANSFER-001` 與 `HB-SITE001-FORMAT-CONVERSION-001`（指定權責單位與複核日 2026-10-01）；`test_site001_missing_members_independent_disposition_outcomes` 驗證通過。 |
| **A4** | manifest member 狀態與 formal disposition ref 一致且 checker 綠燈 | 測試證明 (T) | **已滿足 (met)** | 執行 `delivery_toolchain/governance/check_requirement_members.py` 檢查通過（47 members 全數合法）；`test_overall_governance_checker_passes_with_live_manifest` 在 PR #1160 CI 與當前環境均測試通過（exit code 0）。 |

---

## 4. 人工決策 (D16/D17) 與後續工程任務承接關係

依據 `docs/plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md`，使用者於 2026-09-08 確認了相關決策：

1. **Brand Transfer (D16)**：使用者選擇 Option A（實作／補齊真實資料與契約）。
   - **Stage 30A 契約準備**：由任務 `ODP-BRAND-TRANSFER-CONTRACT-PREP-001`（PR [#1254](https://github.com/alfloop-dev/odayplus/pull/1254)）交付完成。
   - **Stage 30B 功能實作**：指派至任務 `ODP-BRAND-TRANSFER-IMPLEMENTATION-001`，正等待人類提供 H03 資料（跨品牌交易／會員／panel 資料來源）。
2. **Format Conversion (D17)**：使用者選擇 Option A（實作／補齊真實資料與流程）。
   - **Stage 31A 契約準備**：由任務 `ODP-FORMAT-CONVERSION-CONTRACT-PREP-001`（PR [#1252](https://github.com/alfloop-dev/odayplus/pull/1252)）交付完成。
   - **Stage 31B 功能實作**：指派至任務 `ODP-FORMAT-CONVERSION-IMPLEMENTATION-001`，正等待人類提供 H04 資料（真實轉型事件、原／新店型、財務與停業規則）。

### 4.1 處置任務結案性分析

- 原任務 `ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001` 的職責為**資料準備度評估、拒絕假資料接入、建立正式處置狀態與 Handback Package**。
- 原條款並非要求本處置任務必須完成兩項功能之 end-to-end 實作。
- 本任務之處置結論與 Handback Packages 完全支持處置任務的結案。
- 後續使用者選擇實作並由 Stage 30/31 承接，H03/H04 之資料缺口屬於 Stage 30B/31B 的前置條件，不阻礙本歷史處置任務之結案。

---

## 5. 權限邊界與不變量原則

1. **單一證據 Scope**：所有交付物嚴格限制於 `docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001/`，不修改任何產品程式、原 archive、全域 governance manifest、validator、workflow 或 runtime。
2. **不簽發豁免或假定實作完成**：不刪改 MUST 需求，不自簽 waiver，不將待提供之 H03/H04 假定為已完成。
3. **保留歷史真實性**：原 PR #1160 (head `ffe02988a1b4`) 的 7 項 CI check-runs、原審查者 Codex 之歷史 approval 原樣記錄，不以當前觀察偽稱過去執行。

---

## 6. 驗證方式 (Verification)

本任務交付物由以下宣告命令驗證：

```bash
git diff --check
python3 -c 'import json
from pathlib import Path
p=Path("docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001/")
assert (p/"README.md").is_file()
x=json.loads((p/"acceptance-reconciliation.json").read_text())
assert isinstance(x,dict) and x
y=json.loads((p/"original-evidence.json").read_text())
assert isinstance(y,dict) and y
print("receipt present and valid JSON")'
uv run --python 3.12 pytest tests/governance/test_site001_disposition.py
python3 delivery_toolchain/governance/check_requirement_members.py --show-dispositions
```

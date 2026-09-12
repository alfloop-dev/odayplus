# ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001 驗收核對與補證記錄 (2026-09-12)

## 1. 任務背景與復原目標

- **任務 ID**: `ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001`
- **任務名稱**: 歷史驗收續辦：ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001（原名：依 SITE-001 資料證據實作或正式處置 Brand Transfer／Format Conversion）
- **執行身分 (Owner)**: `Antigravity5`
- **指派審查者 (Reviewer)**: `Codex2`
- **復原目標分支**: `task/ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001-RECOVERY-20260911`
- **對照基準 (Dev Baseline)**: `4b35121031d0738ff7c529810cf1b2e267161083`（經 base advance 整合 PR #1313 合併之 `origin/dev` 最新狀態；歷史曾對照 `2889b55fb1febe95c9f8650f24ead18e86015cca`）
- **原始交付記錄**: PR [#1160](https://github.com/alfloop-dev/odayplus/pull/1160)（Head SHA: `ffe02988a1b4def412090c6b422e6efb26081d9f`，Merge Commit: `9f53418df41e558c8f953c801dd8fd1f25f77b5b`，Merged at `2026-09-03T16:51:21Z`）

本任務原始目的為依據 `ODP-SITE001-DATA-READINESS-001` 之資料準備度查證事實，逐 member 判定 `ODP-FR-SITE-001` 中之 `BRAND_TRANSFER` 與 `FORMAT_CONVERSION` 兩項成員之處置狀態。

原始條款明確要求：逐 member 依資料 readiness 實作或建立 human handback，只有達 `IMPLEMENTATION_READY` 者才接入生產模型；未達標者嚴禁造假 placeholder，應建立結構化 Human-Authority Handback 單。

---

## 2. 歷史交付物與精確 Head 證據盤點 (Historical Delivery & CI Receipts)

PR [#1160](https://github.com/alfloop-dev/odayplus/pull/1160) 於 2026-09-03 交付並合併入 `dev`，其精確 Head SHA `ffe02988a1b4` 之歷史證據與當前代碼庫狀態核對如下：

| 查核項目 | 查核結果 | 具體證據與收據參照 |
|---|---|---|
| **交付文件與契約** | `true` | `docs/evidence/ODP_SITE001_COMPONENT_DISPOSITIONS_2026-09-03.md` 存在於 merge commit `9f53418d` 及當前基準。 |
| **治理清單對齊** | `true` | `delivery_toolchain/governance/set_valued_requirements.json` 宣告 `BRAND_TRANSFER` 與 `FORMAT_CONVERSION` 為 `BLOCKED_BY_EVIDENCE`，decider 為 `null`，附帶完整法定欄位與 handback 參照。 |
| **治理測試交付** | `true` | `tests/governance/test_site001_disposition.py` 存在於 merge commit `9f53418d`。 |
| **Exact-Head CI 檢查** | `true` | PR #1160 head `ffe02988a1b4` 上 7 項 check-runs 全數 `success`：`change-scope` (16:00:20Z), `boundary` (16:00:20Z), `classify` (16:00:21Z), `performance-gate` (16:01:30Z), `orchestrator` (16:03:02Z), `product-e2e-gate` (16:06:21Z), `product` (16:18:24Z, 收集路徑涵蓋 `tests/`)。 |
| **歷史審查批准** | `true` | `task-review-gate` commit status 於 `2026-09-03T16:22:20Z` 記錄 `Approved by assigned reviewer Codex`（state: `success`）。 |
| **ReviewBus 區塊** | `true` | PR body 之 ReviewBus 區塊明確記錄 `task_id: ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001`, `status: review_approved`, `owner: Antigravity5`, `reviewer: Codex`。 |
| **歷史終端記錄狀態** | `unknown (non-blocking)` | 依據驗收盤點規範，PR #1160 exact-head 歷史 check-runs 全數 success；個別具名 assertion 之原始 terminal stdout / exit codes 於事故後未保存於 job log 層，依規定顯式標記為 unknown，直接引用已驗證之 exact-head CI，不憑空捏造數字，亦不為統計 count 重跑已成功測試。 |

---

## 3. 原始驗收條款 (A1–A4) 逐條核對結果

| 項次 | 原驗收條款 | 類別 | 判定結果 | 核對依據與證據路徑 |
|---|---|---|---|---|
| **A1** | Brand Transfer 與 Format Conversion 各自有獨立 outcome | 程式交付 (D)<br>測試證明 (T) | **已滿足 (met)** | PR #1160 交付 `docs/evidence/ODP_SITE001_COMPONENT_DISPOSITIONS_2026-09-03.md`（§2 與 §3），分別獨立審查 Brand Transfer（無真實生產者、mock 0.15 視圖拒絕接入）與 Format Conversion（靜態 store_format_code、綠地選型非改裝轉型、無轉型事件表與財務停業折減）；`tests/governance/test_site001_disposition.py::test_site001_missing_members_independent_disposition_outcomes` 測試通過；PR #1160 exact-head `product` CI 綠燈（completed at 2026-09-03T16:18:24Z）。 |
| **A2** | 實作者以真 source lineage 接到 production consumer 且有反事實測試 | 程式交付 (D) | **已滿足 (met)** | 原任務規範明確要求「只有 evidence=IMPLEMENTATION_READY 的成員才接進 model-ready/production consumer 並測試；沒有資料或業務事件者不得造 placeholder」。本任務依查證事實判定兩者未達準備度，故拒絕撰寫假接入程式碼；`tests/governance/test_site001_disposition.py::test_no_synthetic_wiring_in_sitescore_or_simulator` 證明未注入假接入；處置報告 §4 完整交付未來實作之契約設計與反事實驗收標準（§4.1 與 §4.2）。生產接入條件僅對判定為實作者成立，本處置任務拒絕假接入即為正確履約。 |
| **A3** | 不適用者只建立 human-authority handback 且 AI 不自簽 waiver | 程式交付 (D)<br>人類授權 (H) | **已滿足 (met)** | `set_valued_requirements.json` 與 `ODP_REQUIREMENT_DISPOSITIONS.md` 中兩成員均維持 `status: absent` 及 `disposition.state: BLOCKED_BY_EVIDENCE`，decider 維持 `null`，無 AI 自簽 waiver；正式建立移交單 `HB-SITE001-BRAND-TRANSFER-001` 與 `HB-SITE001-FORMAT-CONVERSION-001`（指定權責單位與複核日 2026-10-01）；`test_site001_missing_members_independent_disposition_outcomes` 與 `test_site001_handback_document_and_governance_files_exist` 驗證通過。 |
| **A4** | manifest member 狀態與 formal disposition ref 一致且 checker 綠燈 | 測試證明 (T) | **已滿足 (met)** | 集合型需求清單 `delivery_toolchain/governance/set_valued_requirements.json` 中各 member 狀態與 formal disposition 參照完全一致；`tests/governance/test_site001_disposition.py::test_overall_governance_checker_passes_with_live_manifest` 於 PR #1160 exact-head `product` CI 執行通過（conclusion=success）。 |

---

## 4. 原始條款／成員與後續任務映射及依賴 DAG 核對 (Successor Tasks Mapping & DAG Reconcile)

依據 `docs/plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md` 及 canonical 看板現狀，原始需求條款、成員與後續任務之完整映射如下：

### 4.1 原始條款／成員到責任任務映射

| 需求代碼 | 成員名稱 | 對應條款 | 歷史處置狀態 | 人工決策 (2026-09-08) | 契約準備任務 (Stage A) | 功能實作任務 (Stage B) | 入場依據與前置條件 |
|---|---|---|---|---|---|---|---|
| `ODP-FR-SITE-001` | `BRAND_TRANSFER` | A1, A2, A3 | `BLOCKED_BY_EVIDENCE` (移交單: `HB-SITE001-BRAND-TRANSFER-001`) | **D16: Option A**（實作／補齊真實資料與契約） | `ODP-BRAND-TRANSFER-CONTRACT-PREP-001` (PR #1254, Stage 30A, 已合併) | `ODP-BRAND-TRANSFER-IMPLEMENTATION-001` (Stage 30B, 看板狀態: `blocked`) | **H03**: 公司已有的跨品牌交易／會員／panel 資料位置、資料負責人與授權範圍確認；未提供前維持 blocked。 |
| `ODP-FR-SITE-001` | `FORMAT_CONVERSION` | A1, A2, A3 | `BLOCKED_BY_EVIDENCE` (移交單: `HB-SITE001-FORMAT-CONVERSION-001`) | **D17: Option A**（實作／補齊真實資料與流程） | `ODP-FORMAT-CONVERSION-CONTRACT-PREP-001` (PR #1252, Stage 31A, 已合併) | `ODP-FORMAT-CONVERSION-IMPLEMENTATION-001` (Stage 31B, 看板狀態: `blocked`) | **H04**: 真實轉型事件與來源、停業/Capex/殘值/ramp 定義確認；未提供前不可用草案數值冒充業務批准。 |
| `ODP-FR-SITE-001` | （治理清單與檢查器） | A4 | `BLOCKED_BY_EVIDENCE` (decider: `null`) | 納入持續治理閘門 | 治理清單已合規 | `ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001` (看板狀態: `todo`) | 20 項結構性缺陷於結案時逐一具備 VERIFIED 或有效 formal nonimplementation disposition，且無 OPEN／BLOCKED_BY_EVIDENCE。 |

### 4.2 歷史依賴快照與精確依賴關係 Before / After 核對 (Explicitly Unchanged)

依據 Canonical 歸檔 `/home/lupin/odayplus/ai-task-archive/tasks/` 及看板現狀（正規化 archive envelope 讀取 `task.depends_on`）：
- `ODP-BRAND-TRANSFER-CONTRACT-PREP-001.json` (SHA256: `572eb22da48f7d367a15fe82a191dcb1379aaca91db3d31fe632c01bac885975`, archived_at: `2026-09-08T16:42:34Z`) 依正規化 task envelope 記錄 `task.depends_on: ["ODP-HUMAN-DECISIONS-EXECUTION-PLAN-001"]`。
- `ODP-FORMAT-CONVERSION-CONTRACT-PREP-001.json` (SHA256: `9af75cde28b5507aec4229d74674b3f2abf652d71ababc6dacccae7055324185`, archived_at: `2026-09-08T19:02:57Z`) 依正規化 task envelope 記錄 `task.depends_on: ["ODP-HUMAN-DECISIONS-EXECUTION-PLAN-001"]`。
- `ODP-HUMAN-DECISIONS-EXECUTION-PLAN-001.json` (SHA256: `9dfeedca3ca9301a167b8c153e946aee5f08c6c0e5d5b60538a5e02fadf1fcbf`, archived_at: `2026-09-08T14:54:58Z`) 依正規化 task envelope 記錄 `task.depends_on: []`。

本次補證嚴格遵守唯讀治理原則，不手改看板狀態，不變更任何 DAG 依賴邊（Before / After 保持一致）：

1. **本任務 (`ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001`)**:
   - `depends_on_before`: `[]`
   - `depends_on_after`: `[]`
   - **變更**: 無變更 (`dependency_unchanged: true`)。
2. **品牌轉移契約準備任務 (`ODP-BRAND-TRANSFER-CONTRACT-PREP-001`)**:
   - `depends_on_before`: `["ODP-HUMAN-DECISIONS-EXECUTION-PLAN-001"]`
   - `depends_on_after`: `["ODP-HUMAN-DECISIONS-EXECUTION-PLAN-001"]`
   - **狀態**: `done` (PR #1254 merged).
   - **變更**: 無變更 (`dependency_unchanged: true`)。
3. **品牌轉移實作任務 (`ODP-BRAND-TRANSFER-IMPLEMENTATION-001`)**:
   - `depends_on_before`: `["ODP-BRAND-TRANSFER-CONTRACT-PREP-001"]`
   - `depends_on_after`: `["ODP-BRAND-TRANSFER-CONTRACT-PREP-001"]`
   - **狀態**: `blocked` (等待 H03，由 canonical writer 於資料到位後解除)。
   - **變更**: 無變更 (`dependency_unchanged: true`)。
4. **店型轉換契約準備任務 (`ODP-FORMAT-CONVERSION-CONTRACT-PREP-001`)**:
   - `depends_on_before`: `["ODP-HUMAN-DECISIONS-EXECUTION-PLAN-001"]`
   - `depends_on_after`: `["ODP-HUMAN-DECISIONS-EXECUTION-PLAN-001"]`
   - **狀態**: `done` (PR #1252 merged).
   - **變更**: 無變更 (`dependency_unchanged: true`)。
5. **店型轉換實作任務 (`ODP-FORMAT-CONVERSION-IMPLEMENTATION-001`)**:
   - `depends_on_before`: `["ODP-FORMAT-CONVERSION-CONTRACT-PREP-001"]`
   - `depends_on_after`: `["ODP-FORMAT-CONVERSION-CONTRACT-PREP-001"]`
   - **狀態**: `blocked` (等待 H04，由 canonical writer 於資料到位後解除)。
   - **變更**: 無變更 (`dependency_unchanged: true`)。
6. **全案結構修復結案任務 (`ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001`)**:
   - `depends_on_before`: 包含 `ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001` 等 17 項任務。
   - `depends_on_after`: 包含 `ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001` 等 17 項任務。
   - **狀態**: `todo`。
   - **變更**: 無變更 (`dependency_unchanged: true`)。

### 4.3 依賴閉包無環拓撲計算 (Computed Transitive DAG Cycle Check)

針對本任務及全案結構修復結案任務 (`ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001`) 之完整依賴傳遞閉包（正規化載入看板任務與歸檔任務之 `task.depends_on`，共 49 個頂點、51 條導向邊）進行 Kahn 演算法拓撲排序與 DFS 循環偵測計算：
- **閉包節點規模**: 49 個任務節點（含 `ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001`、`ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001` 及其全部 17 項前置任務與其完整上游依賴任務、Stage 30/31 任務群、Archive 復原執行群等）。
- **未解析頂點 (Unresolved Vertices)**: 0 個（`unresolved_vertices: []`，全數節點均於 Canonical 看板或歸檔中精確解析）。
- **評估導向邊數 (Evaluated Edges)**: 51 條有向依賴邊。
- **循環檢測結果**: `cycle_detected: false`（無任何環狀依賴）。
- **結構合法性**: `is_valid_dag: true`（完全符合有向無環圖定義）。
- **拓撲排序序列 (Topological Order)**:
  1. `DPF-EMGI-LIVE-ROLLOUT-001`
  2. `ODP-AVM-DEPRECIATION-CONTRACT-001`
  3. `ODP-CANONICAL-LEGACY-LINEAGE-001`
  4. `ODP-DEV-STAGED-GATE-RECONCILIATION-001`
  5. `ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001`
  6. `ODP-GITHUB-GCP-ENV-BOOTSTRAP-001`
  7. `ODP-HUMAN-DECISIONS-EXECUTION-PLAN-001`
  8. `ODP-BRAND-TRANSFER-CONTRACT-PREP-001`
  9. `ODP-BRAND-TRANSFER-IMPLEMENTATION-001`
  10. `ODP-FORMAT-CONVERSION-CONTRACT-PREP-001`
  11. `ODP-FORMAT-CONVERSION-IMPLEMENTATION-001`
  12. `ODP-HZ006-MERGE-SPLIT-IMPLEMENTATION-001`
  13. `ODP-AVM-QUALITY-NULLABLE-001`
  14. `ODP-AVM-DEPRECIATION-INTEGRATION-001`
  15. `ODP-INT-MANUAL-CORRECTION-AUDIT-001`
  16. `ODP-INT001-CDC-DISPOSITION-001`
  17. `ODP-INTV006-ADJUST-WORKFLOW-001`
  18. `ODP-JOB-PARTIAL-DISPOSITION-001`
  19. `ODP-LH-PREDICTION-DRIFT-001`
  20. `ODP-LH003-BACKTEST-RELEASE-GATE-001`
  21. `ODP-MEASUREMENT-CROSSLAYER-GATE-001`
  22. `ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001`
  23. `ODP-MODELREADY-A4-REMEDIATION-001`
  24. `ODP-NET002-LEASE-DISPOSITION-001`
  25. `ODP-NETPLAN-DISCLOSURE-UI-E2E-001`
  26. `ODP-OPS002-DECISION-COMMENTS-001`
  27. `ODP-PRICE006-BANDIT-GATED-001`
  28. `ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001`
  29. `ODP-RELEASE-GATE-FIXTURE-STAGING-002`
  30. `ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001`
  31. `ODP-REQ-DISPOSITION-GOVERNANCE-001`
  32. `ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001`
  33. `ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002`
  34. `ODP-RUNTIME-RELEASE-SINGLE-PATH-001`
  35. `ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001`
  36. `ODP-SITESCORE-QUALITY-NULLABLE-001`
  37. `ODP-SPEC-SOURCE-PROVENANCE-001`
  38. `ODP-TENANT-PLATFORM-ADMIN-FAILCLOSED-001`
  39. `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002`
  40. `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`
  41. `ODP-NFR-RUNTIME-EVIDENCE-001`
  42. `ORCH-ARCHIVE-HISTORY-RECOVERY-001`
  43. `ORCH-ARCHIVE-RECOVERY-EVIDENCE-001`
  44. `ORCH-ARCHIVE-HISTORY-APPLY-001`
  45. `ORCH-ARCHIVE-HISTORY-RESTORE-002`
  46. `ORCH-ARCHIVE-RECOVERY-INVALIDATION-001`
  47. `ORCH-ARCHIVE-HISTORY-EXECUTE-003`
  48. `ODP-CANONICAL-MEASUREMENT-NULLABLE-CUTOVER-001`
  49. `ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001`

### 4.4 處置任務結案性分析

1. **職責範圍界定**：原任務 `ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001` 的履約範圍為**資料準備度評估、拒絕假資料接入、建立正式處置狀態與 Handback Package**。
2. **原條款滿足性**：原條款並非要求本處置任務必須完成兩項功能之 end-to-end 實作；PR #1160 已完全滿足 A1–A4 條款要求。
3. **後續承接與治理防線**：使用者選擇 Option A 啟動之 Stage 30 與 Stage 31 承接了功能實作，D16/D17 並非豁免 (waiver)。H03/H04 之資料缺口屬於 Stage 30B/31B 的入場前置條件，不阻礙本歷史處置任務之結案；所有 release gates 與 requirement gates 均被完整追蹤。

---

## 5. 權限邊界與不變量原則 (Invariants & Governance Declarations)

1. **單一證據 Scope**：所有交付物嚴格限制於 `docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001/`，不修改任何產品程式、原 archive、全域 governance manifest、validator、workflow 或 runtime。所有程式碼邊界（`docs/audits/code-boundary-inventory.csv`）維持原樣無變更。
2. **不簽發豁免或假定實作完成**：不刪改 MUST 需求，不自簽 waiver，不將待提供之 H03/H04 假定為已完成。
3. **保留歷史真實性**：原 PR #1160 (head `ffe02988a1b4`) 的 7 項 CI check-runs、原審查者 Codex 之歷史 approval 原樣記錄，不以當前觀察偽稱過去執行。
4. **無依賴異動與無重複任務**：不新增重複盤點任務，不修改看板依賴與狀態。

---

## 6. 當前驗證收據 (Current Verification Receipts)

本次復原補證交付物經由以下實測命令完成驗證：

```bash
git diff --check 4b35121031d0738ff7c529810cf1b2e267161083 HEAD
cmp -s docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001/original-evidence.json /home/lupin/odayplus/support/handoffs/archive-recovery-dispatch-20260911/ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001/original-evidence.json
python3 -c "import json, os, glob, heapq; ..."
```

### 驗證執行收據 (Verification Execution Receipts)

| 收據項目 | 執行命令 (Argv) | 測量基準 / 範圍 | 執行時間與耗時 | 終端退出碼 | 執行結果與終端輸出摘要 |
|---|---|---|---|---|---|
| **Git Diff Check** | `git diff --check 4b35121031d0738ff7c529810cf1b2e267161083 HEAD` | Base `4b35121031d0` / HEAD `513d39086e80` | `2026-09-12T09:17:44Z` (~0.009s) | `0` | **PASS**: 無空白行錯誤、無非預期變更。 |
| **原始證據完整性** | `cmp -s docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001/original-evidence.json /home/lupin/odayplus/support/handoffs/archive-recovery-dispatch-20260911/ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001/original-evidence.json` | Worktree copy vs Canonical dispatch copy | `2026-09-12T09:17:44Z` (~0.005s) | `0` | **PASS**: 檔案內容完全一致 (byte-for-byte match)。 |
| **Canonical 看板與傳遞依賴閉包 DAG 拓撲無環計算** | `python3 -c "import json, os, glob, heapq; ..."` | Canonical Board `/home/lupin/odayplus/ai-status.json` and task archives (`task.depends_on`) | `2026-09-12T09:17:44Z` (~0.100s) | `0` | **PASS**: `Dependency closure verified: 49 vertices, 51 edges, 0 cycles, valid DAG.` |

### 歷史治理測試套件驗證政策說明 (Historical Test Suite Policy)
- 原 PR [#1160](https://github.com/alfloop-dev/odayplus/pull/1160) exact-head `ffe02988a1b4` 之 7 項 GitHub CI check-runs 全數綠燈（`product` check-run completed 2026-09-03T16:18:24Z 收集執行了 `tests/governance/test_site001_disposition.py` 及治理成員檢查）。
- 依據證據盤點與復原驗收規範，歷史個別 assertion 終端輸出及退出碼於事故後未保存，顯式記錄為 `historical_per_test_terminal_exit_code_unknown`，直接引用已驗證之 exact-head CI，不憑空捏造執行時間或通過數量，亦不為統計計數重跑已成功之歷史測試套件。

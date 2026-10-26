# ODP-JOB-PARTIAL-DISPOSITION-001 歷史驗收續辦與補證報告 (2026-09-11)

## 1. 任務背景與復原目標

- **任務 ID**：`ODP-JOB-PARTIAL-DISPOSITION-001`
- **任務名稱**：依 producer 證據補 PARTIAL 狀態轉移或正式處置 SHARED-001
- **階段 (Phase)**：History Recovery — executable acceptance reconciliation
- **執行身分 (Owner)**：`Antigravity7`
- **審查人 (Reviewer)**：`Codex`
- **交付分支**：`task/ODP-JOB-PARTIAL-DISPOSITION-001-RECOVERY-20260911`
- **目標基準 (Target)**：`origin/dev` (`cbbd686d632656bc4098cd8ef4491def1eecbd55`)
- **關聯需求**：`ODP-FR-SHARED-001`（所有長時間任務都能查詢 QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED/PARTIAL）

### 1.1 歷史交付與條件分支背景

本任務原始設計為**條件式處置任務 (Conditional Disposition Task)**：
> 「若證據找到真實部分成功 job，僅為那些 job 實作 PARTIAL business outcome、itemized receipt、retry 與 API query；若沒有則保持 absent 並產出正式 amendment/waiver handback。禁止拿 queue delivery state 或任意 warning 冒充 PARTIAL。」

在歷史執行時（2026-09-03～2026-09-04），經前置任務 `ODP-JOB-PARTIAL-PRODUCER-EVIDENCE-001` 查證，專案代碼庫中無任何生產者寫入 `JobStatus.PARTIAL`。因此，任務由當時負責人（`Antigravity6`）執行第二條條件分支：
1. 將 `PARTIAL` 狀態保持為 `absent`，於集合型需求清單 `delivery_toolchain/governance/set_valued_requirements.json` 中登錄 `disposition.state: "BLOCKED_BY_EVIDENCE"`。
2. 產出正式 Human-Authority Handback Package `HB-SHARED001-PARTIAL-001`（文件：`docs/evidence/ODP_JOB_PARTIAL_DISPOSITION_2026-09-03.md`），詳述未來若啟用實作（Pathway A）之設計契約（含狀態轉移、明細收據、不重做成功項之重試契約、型別分離）。
3. 交付治理與型別分離測試 `tests/governance/test_job_partial_disposition.py`。
4. 歷史 PR [#1172](https://github.com/alfloop-dev/odayplus/pull/1172) 經 7 項 CI 檢查全綠（exact head `f8caf62e11643f9cbe59ea6b958faeab746fcac2`），並由審查人 `Claude` 批准後於 2026-09-04T13:14:12Z 合併至 `dev`（merge commit `9647d673ccf2c0f11ef565e78511099821d85c19`）。

---

## 2. 後續演進與職責劃分 (PR #1172 → PR #1285 → PR #1302)

2026-09-08 使用者確認決策 D19（授權實作 Durable PARTIAL）後，後續任務依序推進：
1. **Stage 33A (`ODP-DURABLE-PARTIAL-CONTRACT-PREP-001` / PR #1257)**：盤點生產者、草擬重試契約，並產出 `human-input-request-H06.md`。
2. **Stage 33B (`ODP-DURABLE-PARTIAL-IMPL-001` / PR #1285)**：於 `apps/worker` 實作真實批次處理器 `handle_batch_listing_intake`、三階段 checkpoint、`derive_batch_status_and_summary` 及差異化重試，經 `tests/reliability/test_durable_partial_batch.py` 完整測試後合併。
3. **治理對齊 (`ODP-JOB-PARTIAL-PRODUCER-RECONCILIATION-001` / PR #1302)**：將 `set_valued_requirements.json` 成員狀態對齊為 `satisfied`（指向 `handle_batch_listing_intake`），同時嚴格保留 `disposition.state: "BLOCKED_BY_EVIDENCE"`，保留 H06 人類簽署與 Live production queue 審計收據之邊界。

**職責邊界與狀態分列**：
- **歷史時點（PR #1172）**：`PARTIAL` 狀態為 `absent` / `BLOCKED_BY_EVIDENCE`。本任務之職責是**歷史條件式處置與 Handback 契約交付**。其在無生產者之歷史時點已完整履約。
- **目前時點（PR #1302 後基準）**：`PARTIAL` 狀態為 `satisfied` / `BLOCKED_BY_EVIDENCE`（程式碼生產者已交付）。後續新增之具體批次生產者實作與 H06 人類簽署，屬於後續演進任務之能力交付與治理追蹤，不應逆向擴張為本歷史處置任務之缺陷。

---

## 3. A1–A4 逐條驗收核對結果

| 項次 | 原驗收條款 | 類別 | 判定結果 | 核對依據與證據路徑 |
|---|---|---|---|---|
| **A1** | **只有證據列名的 job 能產生 PARTIAL** | 程式交付 (D)<br>測試證明 (T) | **已滿足 (met)** | 歷史 PR #1172 經前置任務 `ODP-JOB-PARTIAL-PRODUCER-EVIDENCE-001` 查證確認無在樹生產者證據後，依條件處置將 PARTIAL 保持 `absent` 並於 `set_valued_requirements.json` 登錄 `BLOCKED_BY_EVIDENCE`。交付之 `tests/governance/test_job_partial_disposition.py`（merge `9647d673ccf2`）斷言 status == `absent` 與 disposition.state == `BLOCKED_BY_EVIDENCE`，並測試列名 5 個 enum members；CI 於 exact head `f8caf62e1164` 7 項全綠（`product` check 等全數 success）。查無 producer 之事實來自前置調查證據，測試驗證治理狀態一致。後續 PR #1285/1302 交付具名 producer `handle_batch_listing_intake`，完全符合條款。 |
| **A2** | **PARTIAL receipt 可區分 succeeded／failed items 且 retry 不重做成功項** | 程式交付 (D) | **已滿足 (met)** | 依條件處置規則（無適用 producer 時保持 absent 並交付 formal handback），PR #1172 交付之 Handback 文件 `docs/evidence/ODP_JOB_PARTIAL_DISPOSITION_2026-09-03.md` §4.2 確立明細收據架構契約（total/succeeded/failed 計數與 items 逐項狀態），§4.3 確立差異化重試契約（`retry_scope="FAILED_ONLY"`，跳過已成功項）；`tests/governance/test_job_partial_disposition.py` 驗證 Handback 契約完整性。按條件分支，此項之 runtime 實作行為在當時無 producer 時不適用，形式為正式 Handback 設計契約（`HB-SHARED001-PARTIAL-001`）；後續 PR #1285 亦依此契約完整實作與測試。 |
| **A3** | **business outcome 與 delivery state 型別分離** | 測試證明 (T) | **已滿足 (met)** | 業務結果 `JobStatus`（`QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED`, `PARTIAL`）與隊列傳遞機制 `JobDeliveryState`（`RETRYING`, `DEAD_LETTER`）於 `shared/governance/vocabularies.py` 徹底分離；`tests/governance/test_job_partial_disposition.py::test_job_status_and_delivery_state_type_separation` 斷言兩者互斥；PR #1172 exact head CI `product` 通過。 |
| **A4** | **無適用 producer 時不造功能且 formal disposition 缺人類簽署仍保持未結案** | 程式交付 (D)<br>人類授權 (H) | **已滿足 (met)** | PR #1172 嚴守防偽與治理防線，在查無 producer 時不造假功能，將 PARTIAL 保持 `absent` 並登錄 `BLOCKED_BY_EVIDENCE`，交付移交單 `HB-SHARED001-PARTIAL-001`（載明法定風險負責人 Platform Infrastructure Lead、下次檢視日 2026-10-01、reopen_trigger）；測試斷言缺少人類簽署時不得標記為 `DECIDED`/`VERIFIED`/`IMPLEMENTATION_READY`。治理狀態維持未結案，完全符合原條款要求。 |

---

## 4. 依賴關係與 DAG 拓撲 (Dependencies & DAG Reconciliation)

1. **歷史前置任務 (Historical Predecessors)**：
   - `ODP-JOB-PARTIAL-PRODUCER-EVIDENCE-001`：已於歷史 W6 完成，查證確認無在樹生產者。
   - `ODP-REQ-DISPOSITION-GOVERNANCE-001`：已於歷史 W6 完成並封存（`done`），建立可機讀處置門禁。
   - `ODP-SUPPLY-CHAIN-LOCKFILE-CONSISTENCY-001`：已於歷史 W6 完成並封存（`done`），建立乾淨 CI 基底。

2. **目前看板任務狀態 (Live Canonical Board)**：
   - 本任務 `ODP-JOB-PARTIAL-DISPOSITION-001` 在 live canonical board（`/home/lupin/odayplus/ai-status.json`，sha256 `b6df6e594144b663538c9a93c4cab101e43cefd0074faab1b5036a629c83b658`）上之 `depends_on: []`（前置任務均已封存為 `done`，入度為 0，維持不變，`dependency_mutation: false`）。

3. **下游承接與結案總帳 (Downstream Dependents)**：
   - `ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001`：目前處於 `todo`，其 `depends_on` 共有 17 項任務（包含本任務 `ODP-JOB-PARTIAL-DISPOSITION-001` 與 `ODP-REQ-DISPOSITION-GOVERNANCE-001` 等）。該任務負責最終 20 項 findings 之結案總核驗。
   - DAG 有向邊核對：全看板 30 個活躍任務節點經 DFS 遞迴檢查無環（`cycles_detected: false`），完整圖投影快照記錄於 `command-receipts.json#dag_snapshot_and_cycle_verification.graph_snapshot`。

4. **後續實作與治理門禁 (Followup Lanes & Gates)**：
   - Stage 33A/33B 已由 PR #1257 / PR #1285 交付批次生產者與測試。
   - 治理成員對齊已由 PR #1302 交付，維持 `BLOCKED_BY_EVIDENCE`。
   - 人類簽署門禁 `human-input-request-H06.md` 由 Platform Infrastructure Lead / Human/Ops 持續追蹤，不自簽人類豁免。

---

## 5. 治理邊界、執行收據降格與不變量原則

1. **不偽造審批與簽署**：
   - 治理清單中 `ODP-FR-SHARED-001` 之 `PARTIAL` 成員維持 `BLOCKED_BY_EVIDENCE`。
   - 移交單 `HB-SHARED001-PARTIAL-001` 保持有效。
   - 不偽造人類簽名、不自簽豁免。
2. **歷史真實性與收據降格揭露 (Receipts & Provenance Policy)**：
   - PR #1172 merge commit `9647d673ccf2c0f11ef565e78511099821d85c19`、head commit `f8caf62e11643f9cbe59ea6b958faeab746fcac2`、7 項 CI 成功記錄與原審查人 Claude 之核准原樣保留。
   - 依 2026-09-06 archive 事故客觀事實，歷史執行終端輸出與 log bytes 未留存，標記為 `unknown (non-blocking)`；相應代碼與測試成果直接復用歷史 PR #1172 精確 head 7/7 綠燈 CI check-runs（`product` check 等）。
   - 前輪中未具備原始 worker terminal log 的本地執行數據，依 Codex 審查意見逐筆降格為 `unknown_unretained_local_log`（`exit_code=null`, `started_at=null`, `duration_seconds=null`, `stdout_snippet=null`, `stderr_snippet=null`），撤除「完整實測／成功」聲稱，使用中性「原始收據未保存」，不自填未經審計之退出碼、耗時或 snippets。
   - DAG 無環由保存之 30-node `graph_snapshot` 經離線 DFS 遍歷驗證支持。
3. **單一寫入範圍 (Strict Scope Isolation)**：
   - 本次補證僅寫入 `docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-JOB-PARTIAL-DISPOSITION-001/`，不更動任何產品程式碼、工作流、控制平面工具（`delivery_toolchain/`）或全域治理清單。

---

## 6. 本地收據降格與離線拓撲核對 (Receipts Degradation & Offline Topology Verification)

依 Codex 審查意見與驗證證據政策，前輪未留存原始 terminal handle 與 log bytes 之本地執行記錄逐筆降格為 `unknown_unretained_local_log`，不宣稱未經審計之退出碼、時間或耗時（完整記錄見 `command-receipts.json` 與 `acceptance-reconciliation.json`）：

### 1. Git Diff Check
- **Command**: `git diff --check cbbd686d632656bc4098cd8ef4491def1eecbd55 HEAD`
- **Status**: `unknown_unretained_local_log`
- **Exit Code**: `null`
- **Note**: 原始執行收據未保存；依驗證證據政策降格。

### 2. Canonical 看板與依賴圖拓撲無環檢驗 (30 nodes DFS check)
- **Command / Method**: 針對已保存之 30-node `graph_snapshot` 執行離線 DFS 拓撲遍歷
- **Status**: `unknown_unretained_local_log` (歷史 run 收據降格)；無環結論由保存之 30-node `graph_snapshot` 離線 DFS 遍歷支持
- **Cycles Detected**: `false` (0 cycles across 30 tasks)
- **Note**: 舊命令 handle 未保存，標記為 `unknown_unretained_local_log`；拓撲無環由靜態圖快照支持。

### 3. 集合型需求成員檢查器 (Governance Requirements Check)
- **Command**: `python3 delivery_toolchain/governance/check_requirement_members.py`
- **Status**: `unknown_unretained_local_log`
- **Exit Code**: `null`
- **Note**: 原始執行收據未保存；依驗證證據政策降格。

### 4. 本目錄下證據文件完整性與 JSON Schema 一致性
- **Command**: `python3 -c "import json, os; ..."`
- **Status**: `unknown_unretained_local_log`
- **Exit Code**: `null`
- **Note**: 原始執行收據未保存；依驗證證據政策降格。

# ODP-INT001-CDC-DISPOSITION-001 歷史驗收核對與處置續辦記錄 (2026-09-11)

## 1. 任務背景與復原目標

- **任務 ID**: `ODP-INT001-CDC-DISPOSITION-001`
- **任務名稱**: 歷史驗收續辦：ODP-INT001-CDC-DISPOSITION-001
- **原始任務名稱**: 依 upstream 證據實作 CDC connector 或正式處置 INT-001
- **執行身分 (Owner)**: `Antigravity6`
- **指派審查者 (Reviewer)**: `Codex2`
- **復原交付分支**: `task/ODP-INT001-CDC-DISPOSITION-001-RECOVERY-20260911`
- **基準 SHA**: `4499a2993e37b62033926b07de8d8d2e8469a6c7`
- **歷史 PR 交付**: PR [#1166](https://github.com/alfloop-dev/odayplus/pull/1166)（PR head: `39289e4207b29d2c7adea071f42901f9f0b5ae20`，merge commit: `0f35515ed15aad36c496f30973b2e2ce9fa2b026`，合併時間: 2026-09-03T20:50:26Z，合併者: `ajoe734`）

### 歷史事實與審查依據 (Historical Facts)
1. **條件型處置定位 (Conditional Disposition Task)**：
   本任務原始契約明確規定：「只有 source evidence 證明 change log、ordering、delete 與 credential boundary 可用時才實作 governed CDC；否則維持 absent 並產出需人類授權的 amendment/waiver handback。不可建立沒有真 upstream 的空 connector。」
2. **歷史 CI 與審查核准**：
   PR #1166 在精確 head `39289e4207b2` 經審查者 Antigravity7 批准（`task-review-gate` context，2026-09-03T20:07:47Z），通過 7 項 required CI check-runs（`product`, `product-e2e-gate`, `performance-gate`, `boundary`, `orchestrator`, `change-scope`, `classify` 均為 `success`）。
3. **歷史執行收據揭露 (Historical Execution Receipts Disclosure)**：
   2026-09-06 archive 事故導致終端原始 stdout/stderr log bytes 未能留存於 archive。歷史事實完全依賴 GitHub PR #1166 精確 head CI 綠燈記錄與 merge commit trailers（`Verified: check_requirement_members.py; pytest governance, INT-001, ingestion, SiteScore, and root-cause contract tests`）。缺失的歷史終端輸出依規記錄為未知 (unknown)，不為衝 count 虛構或重跑舊套件。

---

## 2. 原始條款 (A1–A4) 逐條驗收核對與邊界界定

| 項次 | 原始驗收條款 | 類別 | 判定結果 | 核對依據、精確 SHA 與邊界揭露 |
|---|---|---|---|---|
| **A1** | 實作時使用真 upstream contract 並保存 offset／ordering／delete／replay／idempotency semantics | 程式或文件交付 (D) | **已滿足 (met_by_delivered_artifact)** | 依 conditional 契約，因當時內部唯一生產來源為 MongoDB 全量快照與 `updatedAt` 水位線、無 CDC 串流必要性且下游無刪除傳播路徑，本任務嚴禁建立假 connector，交付了正式處置報告 `docs/evidence/ODP_INT001_CDC_DISPOSITION_2026-09-03.md` 及 `docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md`（merge commit `0f35515ed15a` 完整保存；CI `change-scope` 通過）。<br>*邊界*：處置交付已落定當時事實；不宣稱已實作生產 CDC connector 或完成實際資料串流。 |
| **A2** | credential 與 tenant boundary fail closed 且有 production-entry 測試 | 可由測試證明 (T)<br>程式或文件交付 (D) | **已滿足 (met_conditionally_by_test_and_handback)** | PR #1166 交付了 `tests/integration/test_int001_cdc_disposition.py:120-152`（`TestDataPlaneFailClosedBoundaries::test_data_plane_config_rejects_non_production_or_wrong_db`），於 exact-head `39289e4207b2` 的 `product` check-run（2026-09-03T20:09:20Z）通過。<br>**精確斷言涵蓋**：驗證生產入場組態 fail-closed，拒絕 staging 環境、拒絕非 `fongniao_prod` DB、拒絕本機 Mongo 連線。<br>**未涵蓋範圍揭露 (Non-coverage Disclosure)**：該單元測試未斷言運行時叢集級憑證擴大（`readAnyDatabase`）或串流跨租戶隔離。該項安全風險已於處置報告 §2 正式載明並作為拒絕未授權串流的退回依據。即時串流之叢集憑證與租戶隔離等實際能力，轉由後續實作任務 `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001` (持有 H07) 負責追蹤驗證。<br>*邊界*：僅在條件型處置範圍內滿足（組態級 fail-closed 與拒絕未授權憑證擴大之治理退回）；未在 live production 進行憑證連線。 |
| **A3** | 無適用 upstream 時不新增 connector 且 AI 不自簽 waiver | 程式或文件交付 (D)<br>人類授權 (H) | **已滿足 (met_by_delivered_artifact)** | 交付物 `delivery_toolchain/governance/set_valued_requirements.json` 與處置報告中，`ODP-FR-INT-001::CDC` 維持 `status: "absent"`、`disposition.state: "OPEN"`，指派予 `Data Platform Lead` 並附 `formal_handback_ref`，decider 欄位為 null，未出現任何 AI 自簽之 decider 或 waiver。<br>*邊界*：需求保持 OPEN 待後續實作/裁決，未建立 AI waiver，符合架構治理邊界。 |
| **A4** | requirement member 與 formal disposition ref 維持一致 | 可由測試證明 (T) | **已滿足 (met_by_test_in_green_ci)** | `delivery_toolchain/governance/test_check_requirement_members.py` 由 `orchestrator` check-run（2026-09-03T19:50:46Z）收集，`tests/integration/test_int001_cdc_disposition.py` 由 `product` check-run（2026-09-03T20:09:20Z）收集；於 exact-head `39289e4207b2` 兩項 check-run 均為 `success`。<br>*邊界*：機械式驗證治理清單與處置參照一致；不代表全部需求 member 均已實作完成。 |

---

## 3. 治理演進、責任映射與依賴無環驗證 (Evolution, Responsibility & Dependency DAG)

### A1–A4 條款與後續責任任務映射表 (Criteria to Responsibility Task Mapping)

| 原始條款 | 處置任務完成範疇 (Disposition Scope) | 後續責任任務與演進邊界 (Follow-up Task & Evolution) |
|---|---|---|
| **A1** (真上游與串流語意) | 產出處置報告，不建假 connector。 | 由 `ODP-CDC-SOURCE-CONTRACT-PREP-001` (PR #1258 產出 23 來源矩陣) -> 轉由 `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001` (持有 H07，待入場後實作真串流與順序語意)。 |
| **A2** (Fail-closed 與憑證邊界) | 交付組態級 fail-closed 測試；報告拒絕叢集憑證擴大。 | 串流運行時叢集憑證與跨租戶隔離由 `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001` (H07 要求最小讀取權限) 於實作階段驗收。 |
| **A3** (無 upstream 不加 connector 且無 AI waiver) | 需求維持 absent / OPEN，交付人類治理包。 | 使用者於 D20 選擇實作方向；由 canonical 看板與 `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001` 持續追蹤，不轉為 waiver。 |
| **A4** (清單與處置參照一致) | 機讀清單與處置文檔機械對齊，CI 驗證通過。 | CI 閘門持續監控；待實作任務完工後經正常 review 將成員改為 satisfied / VERIFIED。 |

### 依賴關係對照與無環驗證 (Dependency Graph & DAG Cycle Verification)

1. **依賴關係前後對照 (Explicitly Unchanged)**：
   - **本任務依賴 (`depends_on`)**：
     - 變更前：`["ODP-INT001-CDC-SOURCE-EVIDENCE-001", "ODP-REQ-DISPOSITION-GOVERNANCE-001"]`（均為 `done`）
     - 變更後：`["ODP-INT001-CDC-SOURCE-EVIDENCE-001", "ODP-REQ-DISPOSITION-GOVERNANCE-001"]`（**完全未變更**，`dependency_mutation: false`）
   - **下游依賴本任務者 (`dependents`)**：
     - `ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001`（status: `todo`，整體 20 項結構性修復結案台帳任務，依賴本處置任務）。
2. **工程接續任務 (D20 Implementation Follow-up Lane)**：
   - `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001`（WP-34B，status: `blocked`，`waiting_for: Human/Ops`，持有入場條件 `H07`）。
   - 前置依賴項（均已合併入 dev）：
     - `ODP-CDC-SOURCE-CONTRACT-PREP-001`（PR #1258, merge commit `414b5c17927e`）
     - `ODP-DATA-PLANE-DELETE-PROPAGATION-001`（PR #1282）
     - `ODP-DATA-CATALOG-METADATA-ALIGNMENT-001`（PR #1286）
3. **無環驗證 (Cycle Verification)**：
   - 評估之有向圖節點：`ODP-INT001-CDC-SOURCE-EVIDENCE-001`, `ODP-REQ-DISPOSITION-GOVERNANCE-001`, `ODP-INT001-CDC-DISPOSITION-001`, `ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001`, `ODP-CDC-SOURCE-CONTRACT-PREP-001`, `ODP-DATA-PLANE-DELETE-PROPAGATION-001`, `ODP-DATA-CATALOG-METADATA-ALIGNMENT-001`, `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001`。
   - 拓撲結構為嚴格有向無環圖 (DAG)，無任何閉環（`cycle_detected: false`）。

### 決策與看板觀察來源 (Observation Provenance)
- **決策 D20 依據**：`docs/plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md:68, 202`（基準 SHA `4499a2993e37b62033926b07de8d8d2e8469a6c7`），確認方向為「選項 A：實作／補齊 CDC 適用性與契約」。
- **看板狀態依據**：`/home/lupin/odayplus/ai-status.json`（觀察時間 `2026-09-11T11:30:38Z`）。

---

## 4. 權限邊界與責任不變量宣告

1. **分清處置交付與實際能力**：
   `ODP-INT001-CDC-DISPOSITION-001` 的職責是「依 upstream 證據實作或建立正式 handback」。當時依據無上游需求的查證結果交付了 formal handback 與 fail-closed 治理保護，其處置任務條款已完整達成。
2. **嚴禁誤標 VERIFIED**：
   本處置任務之驗收核對通過，**絕不代表將原始需求 `ODP-FR-INT-001` 或 CDC adapter 標記為 `VERIFIED`**。原始需求仍處於 `OPEN` 待實作狀態，實際 CDC 串流與刪除傳播能力由 `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001` 持續追蹤。
3. **不變更產品與權限**：
   本次補證工作僅寫入 `docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-INT001-CDC-DISPOSITION-001/`，不修改產品程式、不自簽 waiver、不開啟任何 live 憑證或資料庫連線。

---

## 5. 當前聚焦驗證收據 (Current Measured Verification Receipts)

於本任務工作區 `/tmp/pantheon-worker-worktrees/pantheon/odp-int001-cdc-disposition-001` 實測收據：

### 收據 1：Git Diff Check
- **Command**: `git diff --check 4499a2993e37b62033926b07de8d8d2e8469a6c7 HEAD`
- **Worktree**: `/tmp/pantheon-worker-worktrees/pantheon/odp-int001-cdc-disposition-001`
- **Measured Head SHA**: `9623d9bfd445043a92a42b342c51f5a312e5d831`
- **Timestamp**: `2026-09-11T11:31:49.651263+00:00`
- **Duration**: `0.009s`
- **Exit Code**: `0`
- **Output**: *(Clean, no whitespace errors)*

### 收據 2：集合型需求成員檢查器
- **Command**: `python3 delivery_toolchain/governance/check_requirement_members.py`
- **Worktree**: `/tmp/pantheon-worker-worktrees/pantheon/odp-int001-cdc-disposition-001`
- **Measured Head SHA**: `9623d9bfd445043a92a42b342c51f5a312e5d831`
- **Timestamp**: `2026-09-11T11:31:49.660495+00:00`
- **Duration**: `0.319s`
- **Exit Code**: `0`
- **Output Summary**: `Requirement member checks passed: 9 set-valued requirements, 47 members (37 satisfied, 10 absent and noted; dispositions: BLOCKED_BY_EVIDENCE=4, DECIDED=1, IMPLEMENTATION_READY=3, OPEN=3, VERIFIED=36).`

### 收據 3：INT-001 與治理聚焦測試
- **Command**: `uv run pytest delivery_toolchain/governance/test_check_requirement_members.py tests/integration/test_int001_cdc_disposition.py -q`
- **Worktree**: `/tmp/pantheon-worker-worktrees/pantheon/odp-int001-cdc-disposition-001`
- **Measured Head SHA**: `9623d9bfd445043a92a42b342c51f5a312e5d831`
- **Timestamp**: `2026-09-11T11:31:49.979794+00:00`
- **Duration**: `7.211s`
- **Exit Code**: `0`
- **Output Summary**: `75 passed (68 in test_check_requirement_members.py, 7 in test_int001_cdc_disposition.py)`

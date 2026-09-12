# ODP-NET002-LEASE-DISPOSITION-001 歷史驗收核對與處置續辦記錄 (2026-09-11 / 2026-09-12)

## 1. 任務背景與復原目標

- **任務 ID**: `ODP-NET002-LEASE-DISPOSITION-001`
- **任務名稱**: 歷史驗收續辦：ODP-NET002-LEASE-DISPOSITION-001
- **原始任務名稱**: 依租約資料證據實作 per-option feasibility 或正式處置 NET-002 LEASE
- **執行身分 (Owner)**: `Antigravity2`
- **指派審查者 (Reviewer)**: `Codex`
- **復原交付分支**: `task/ODP-NET002-LEASE-DISPOSITION-001-RECOVERY-20260911`
- **基準 SHA**: `3828c5ada2a1baab33d7dbe734c7ec70152d3d77`（已完成 `origin/dev` base advance merge）
- **歷史 PR 交付**: PR [#1187](https://github.com/alfloop-dev/odayplus/pull/1187)（PR head: `3bd82e8a401d463ef8301671620d45fc93b6127b`，merge commit: `c65bb54dd1171c8c878d37282470ada6a18f9ed6`，合併時間: 2026-09-04T12:35:05Z，合併者: `ajoe734`）

### 歷史事實與審查依據 (Historical Facts)
1. **條件型處置定位 (Conditional Disposition Task)**：
   本任務原始契約明確規定：「只有可追溯 per-option lease window/termination cost source 存在時，才在兩個 solver production paths 共用的 validation 加 feasibility 與 cost semantics，並更新 ConstraintClass disclosure/approval。若資料不存在則建立 human-authority amendment/waiver handback；None 絕不等於 0 或可行。」
2. **歷史 CI 與審查核准**：
   PR #1187 在精確 head `3bd82e8a401d463ef8301671620d45fc93b6127b` 經審查者 Antigravity6 批准（`task-review-gate` context，2026-09-04T12:12:18Z），通過 7 項 required CI check-runs（`product`, `product-e2e-gate`, `performance-gate`, `boundary`, `orchestrator`, `change-scope`, `classify` 均為 `success`）。
3. **歷史執行收據揭露 (Historical Execution Receipts Disclosure)**：
   2026-09-06 archive 事故導致終端原始 stdout/stderr log bytes 未能留存於 archive。歷史事實完全依賴 GitHub PR #1187 精確 head CI 綠燈記錄與 merge commit。缺失的歷史終端輸出依規記錄為未知 (unknown)，不為衝 count 虛構或重跑舊套件。

---

## 2. 原始條款 (A1–A4) 逐條驗收核對與邊界界定

| 項次 | 原始驗收條款 | 類別 | 判定結果 | 核對依據、精確 SHA 與邊界揭露 |
|---|---|---|---|---|
| **A1** | 資料 ready 時 MIP 與 CP-SAT 對相同 lease inputs 有一致 allow/deny 結果 | 可由測試證明 (T)<br>程式或文件交付 (D) | **已滿足 (met_conditionally_by_handback_and_unmodelled_consistency_test)** | PR #1187 交付了 `tests/integration/test_net002_lease_disposition.py:104-189`（`TestNet002SolverLeaseClassificationAndFailClosed::test_mip_and_cpsat_solvers_consistently_classify_lease_as_unmodelled`），於 exact-head `3bd82e8a401d463ef8301671620d45fc93b6127b` 的 `product` check-run（2026-09-04T12:05:42Z）通過。<br>**精確斷言涵蓋**：驗證 MIP (`solve_network_plan`) 與 CP-SAT (`NetPlanProductionExecutor`) 對相同門市/候選輸入，均一致將 `ConstraintClass.LEASE` 歸類於 `unmodelled_constraint_classes`（且 `unmodelled_constraint_classes == {LEASE, SEQUENCING}`）。<br>**邊界與非覆蓋揭露**：在條件型處置與無真租約來源下，本測試證明雙求解器在缺席狀態下均一致報告 LEASE 為未建模約束；**此測試不可升格為已驗證 ready lease inputs 下的 allow/deny 判定**。真實租約資料到位後的主動硬限制計算與 allow/deny 一致性，轉由後續實作任務 `ODP-NET002-LEASE-IMPLEMENTATION-001`（持有 H05 入場條件）承接驗收。 |
| **A2** | None availability／cost fail closed 而 explicit zero 有不同語意 | 程式或文件交付 (D)<br>人類授權 (H) | **已滿足 (met_by_specification_and_handback)** | PR #1187 交付了處置報告 `docs/evidence/ODP_NET002_LEASE_DISPOSITION_2026-09-03.md` §4、§5 介面協議及 `docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md`。<br>**精確語意規範**：規範 Measured Zero（量測為零，如自然期滿免罰金，`exit_cost = 0.0`，合法納入資本預算）與 Missing / Unmeasured（資料缺席，`exit_cost = None`，Fail-Closed 拒絕當作 0.0 免費解約）之語意差異。<br>**邊界與既存缺口揭露**：`tests/integration/test_net002_lease_disposition.py` 之 7 個測試**並未包含**針對 None availability/cost 與 explicit zero 數值行為的運行時測試斷言。原處置報告 `ODP_NET002_LEASE_DISPOSITION_2026-09-03.md:117` 明確揭露了既存程式碼缺口：`modules/netplan/domain/planning.py:97` 中 `ExistingStoreInput.exit_cost` 預設為 `0.0`，將未量測之解約成本視為零。處置任務拒絕注入裝飾性假防護，如實以正式 Handback Package `HB-NET002-LEASE-001` 將需求退回 Human/Ops，維持 `ODP-FR-NET-002::LEASE` 為 `BLOCKED_BY_EVIDENCE`。真實資料模型與數值 Fail-Closed 執行防護由後續任務 `ODP-NET002-LEASE-IMPLEMENTATION-001` (持有 H05) 實作。 |
| **A3** | LEASE class 的 UI／policy／receipt 與 solver classification 同步 | 可由測試證明 (T)<br>程式或文件交付 (D) | **已滿足 (met_by_test_in_green_ci)** | 歷史 exact-head `3bd82e8a401d463ef8301671620d45fc93b6127b` 交付並由 CI check-runs（`product`, `product-e2e-gate`）執行通過三項互補之測試證據：<br>1. `tests/integration/test_net002_lease_disposition.py:190-195`（`test_shared_governance_disclosure_includes_lease`）：驗證 `shared/governance/netplan_disclosure.py` 中 `NETPLAN_REQUIRED_CONSTRAINT_CLASSES`（8 大必要類別包含 LEASE）與 `NETPLAN_ACKNOWLEDGEABLE_CONSTRAINT_CLASSES`（`{"LEASE", "SEQUENCING"}`）之常數集合定義。<br>2. `tests/integration/test_netplan_disclosure_ui_e2e.py::test_e2e_production_solve_to_operator_projection_and_durable_approval_receipt`（281-476 行）：完整驗證 CP-SAT 求解分割（343 行：`unmodelled_constraint_classes == {LEASE, SEQUENCING}`）-> OpsBoard `NetworkRebalanceService` 投影（362 行：`unmodelledConstraintClasses == {"LEASE", "SEQUENCING"}`，379 行：`acknowledgeableConstraintClasses == {"LEASE", "SEQUENCING"}`）-> Operator UI 審批與持久化簽核收據（429 行：`acknowledgedConstraintClasses == {"LEASE", "SEQUENCING"}`，451 行：帶有密碼學 hash 之持久收據 `disclosureAcknowledgementId`）-> 最終 `ApprovalRecord` 綁定驗證（473 行：`acknowledged_constraint_classes == (LEASE, SEQUENCING)`）。<br>3. `tests/integration/test_netplan_disclosure_ui_e2e.py::test_e2e_http_disclosure_payload_matches_the_console_test_fixture`（1048-1065 行）與 `PlanGanttChart.test.tsx`（Node workspace UI 渲染與審批簽核測試）：驗證 HTTP 揭露 payload 與 console 測試 fixture 一致，由 `product` 的 Node workspace step 收集執行。<br>**邊界**：機械式驗證跨層揭露契約與求解器未建模輸出、OpsBoard 投影、UI 審批與簽核收據同步；不代表 LEASE 已由求解器建模或已具備主動硬限制。 |
| **A4** | 資料不 ready 時不寫裝飾性限制且只留下待人類簽署的 formal disposition | 程式或文件交付 (D)<br>人類授權 (H) | **已滿足 (met_by_delivered_artifact)** | PR #1187 交付了正式處置報告 `docs/evidence/ODP_NET002_LEASE_DISPOSITION_2026-09-03.md`（含人類授權移交單 `HB-NET002-LEASE-001`）、`docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md#odp-fr-net-002-lease`，以及 `delivery_toolchain/governance/set_valued_requirements.json`（`ODP-FR-NET-002::LEASE` 維持 `status: "absent"`、`disposition.state: "BLOCKED_BY_EVIDENCE"`，無 AI 自簽 decider/waiver）。<br>`tests/integration/test_net002_lease_disposition.py` 之 `test_lease_disposition_forbids_ai_self_signed_waiver` 與 `test_no_decorative_lease_constraints_in_domain_or_solver` 驗證無裝飾性假約束且無 AI 自簽。<br>**同需求無關成員澄清**：同需求 `SEQUENCING` member 為獨立治理成員，其歷史 decider 泛稱問題不干擾 `LEASE` 處置。<br>**邊界**：LEASE 成員維持 `BLOCKED_BY_EVIDENCE` 狀態等待真資料或正式裁決；無 AI waiver。 |

---

## 3. 治理演進、責任映射與依賴無環驗證 (Evolution, Responsibility & Dependency DAG)

### A1–A4 條款與後續責任任務映射表 (Criteria to Responsibility Task Mapping)

| 原始條款 | 處置任務完成範疇 (Disposition Scope) | 後續責任任務與演進邊界 (Follow-up Task & Evolution) |
|---|---|---|
| **A1** (雙求解器一致性) | 驗證未建模狀態下 MIP 與 CP-SAT 一致將 LEASE 列入 unmodelled classes (PR #1187)。 | 由 `ODP-NET002-LEASE-CONTRACT-PREP-001` (WP-32A, PR [#1256](https://github.com/alfloop-dev/odayplus/pull/1256), head `d70563166e344e2d560ea7cac2eb236ddd118892`, merge commit `95646a5c2bd598b7e219ee51efa7e410cb9fe0f4`, 合併時間 `2026-09-08T17:05:57Z`, 已歸檔 done) 產出雙求解器一致驗收方案 -> 轉由 `ODP-NET002-LEASE-IMPLEMENTATION-001` (WP-32B, 待 H05 滿足後實作雙求解器真實硬限制)。 |
| **A2** (Fail-closed 與 None/Zero 語意區分) | 於處置報告中定義 Fail-Closed 原則，拒絕將 None 當作 0.0 免費解約，如實揭露 ExistingStoreInput 預設 0.0 缺口並退回 Handback (PR #1187)。 | 由 `ODP-NET002-LEASE-CONTRACT-PREP-001` (WP-32A, PR #1256, merge commit `95646a5c2bd598b7e219ee51efa7e410cb9fe0f4`, 合併時間 `2026-09-08T17:05:57Z`) 規格化 `StoreLeaseContract` 與 `CandidateSiteLeaseTerms` 欄位契約 -> 轉由 `ODP-NET002-LEASE-IMPLEMENTATION-001` (WP-32B, 持有 H05) 於資料到位後驗證真實數值之 fail-closed 行為與修復預設值缺口。 |
| **A3** (UI/Policy/Receipt 跨層同步揭露) | 跨 shared/governance、OpsBoard 與 NetPlan approval 同步宣告 LEASE (PR #1187)。 | 由 CI 閘門持續監控；待 `ODP-NET002-LEASE-IMPLEMENTATION-001` 完工後，將 LEASE 從 unmodelled 遷移至 modelled constraint classes 並更新 UI 揭露。 |
| **A4** (無裝飾性限制與 Formal Handback) | 交付 `HB-NET002-LEASE-001`，清單登記為 `BLOCKED_BY_EVIDENCE`，無 AI 自簽 (PR #1187)。 | 使用者於 D18 選擇實作方向；由 canonical 看板與 `ODP-NET002-LEASE-IMPLEMENTATION-001` 持續追蹤，待 H05 滿足並經正常 review 後方得將成員改為 satisfied / VERIFIED。 |

### 依賴關係對照與無環驗證 (Dependency Graph & DAG Cycle Verification)

1. **歷史快照與 Canonical 依賴真實對照**：
   - **歷史任務快照依賴 (Historical Task Brief Snapshot)**：
     - 來源：`original-evidence.json entry.original_task_definition.dependencies`（PR #1187 當時快照）。
     - 歷史前置依賴：`["ODP-NET002-LEASE-DATA-READINESS-001", "ODP-REQ-DISPOSITION-GOVERNANCE-001", "ODP-NETPLAN-DISCLOSURE-UI-E2E-001"]`（當時均已 done，後續已歸檔）。
   - **當前 Canonical 看板與任務依賴 (Current Canonical Board & Task Brief)**：
     - 變更前 canonical 依賴 (`depends_on`): `[]`（無未完成之動態前置依賴）。
     - 變更後建議依賴 (`depends_on`): `[]`（**維持無依賴變更**，`dependency_mutation: false`）。
   - **真實下游依賴本任務者 (`canonical_dependents`)**：
     - `ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001`（status: `todo`，整體 20 項結構性修復結案台帳任務，在 canonical 看板明確依賴 `ODP-NET002-LEASE-DISPOSITION-001`、已歸檔之 `ODP-REQ-DISPOSITION-GOVERNANCE-001` 等 17 項前置修復任務）。
2. **工程接續任務 (D18 Implementation Follow-up Lane)**：
   - `ODP-NET002-LEASE-IMPLEMENTATION-001`（WP-32B，status: `blocked`，`waiting_for: Human/Ops`，持有入場條件 `H05`）。
   - 當前前置依賴（已於 dev 合併並於看板歸檔為 `done`）：
     - `ODP-NET002-LEASE-CONTRACT-PREP-001`（WP-32A, PR [#1256](https://github.com/alfloop-dev/odayplus/pull/1256), head `d70563166e344e2d560ea7cac2eb236ddd118892`, merge commit `95646a5c2bd598b7e219ee51efa7e410cb9fe0f4`, 合併時間 `2026-09-08T17:05:57Z`，來源依據 `gh pr view 1256` 及 canonical archive `tasks/ODP-NET002-LEASE-CONTRACT-PREP-001.json:253`, archived `done`）。
3. **精確範疇無環圖驗證 (Canonical DAG Cycle Verification)**：
   - **評估節點與看板位置**：
     - Live 看板節點：`ODP-NET002-LEASE-DISPOSITION-001` (`in_progress`, `depends_on: []`), `ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001` (`todo`), `ODP-NET002-LEASE-IMPLEMENTATION-001` (`blocked`)。
     - 歸檔端點：`ODP-REQ-DISPOSITION-GOVERNANCE-001` (archived `done`), `ODP-NETPLAN-DISCLOSURE-UI-E2E-001` (archived `done`), `ODP-NET002-LEASE-CONTRACT-PREP-001` (archived `done`)。
     - 歷史前置端點（已在 W6 結算）：`ODP-NET002-LEASE-DATA-READINESS-001`。
   - **Canonical 活躍有向邊 (依賴方 -> 被依賴方)**：
     - `ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001` -> `ODP-NET002-LEASE-DISPOSITION-001`
     - `ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001` -> `ODP-REQ-DISPOSITION-GOVERNANCE-001`
     - `ODP-NET002-LEASE-IMPLEMENTATION-001` -> `ODP-NET002-LEASE-CONTRACT-PREP-001`
   - **明確排除/歷史邊揭露**：本任務在 live 看板無前置依賴 (`depends_on: []`)；歷史快照的三條前置邊已結案，不混入當前 canonical DAG。
   - **拓撲檢查結論**：經 DFS 全圖無環檢查與局部依賴斷言，確認無任何閉環（`cycle_detected: false`），全看板與相關任務構成嚴格有向無環圖 (DAG)。

### 決策與看板觀察來源 (Observation Provenance)
- **決策 D18 依據**：`docs/plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md:65, 179`（基準 SHA `3828c5ada2a1baab33d7dbe734c7ec70152d3d77`），確認方向為「選項 A：實作／補齊租約契約」。
- **看板狀態依據**：`/home/lupin/odayplus/ai-status.json` 及 `/home/lupin/odayplus/ai-task-archive/tasks/`（觀察時間 `2026-09-12T09:40:00Z`）。

---

## 4. 權限邊界與責任不變量宣告

1. **分清處置交付與實際能力**：
   `ODP-NET002-LEASE-DISPOSITION-001` 的職責是「依租約資料證據實作 per-option feasibility 或建立正式 handback」。當時依據無門市合約檔與無新鮮度保證外部來源的查證結果交付了 formal handback（`HB-NET002-LEASE-001`）與 fail-closed 治理保護，其處置任務條款已完整達成。
2. **嚴禁誤標 VERIFIED**：
   本處置任務之驗收核對通過，**絕不代表將原始需求 `ODP-FR-NET-002::LEASE` 標記為 `VERIFIED`**。原始需求仍處於 `absent` / `BLOCKED_BY_EVIDENCE` 待實作狀態，實際雙求解器租約硬限制能力由 `ODP-NET002-LEASE-IMPLEMENTATION-001` 持續追蹤。
3. **不變更產品與權限**：
   本次補證工作僅寫入 `docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-NET002-LEASE-DISPOSITION-001/`，不修改產品程式、不自簽 waiver、不開啟任何 live 憑證或資料庫連線。

---

## 5. 驗證收據與先前輪次收據處置說明 (Verification Receipts & Prior Round Disclosures)

### 5.1 前期輪次收據揭露與撤回說明 (Prior Round Receipt Disclosures & Withdrawal)
1. **2026-09-11 第一輪實測收據撤回 (`withdrawn_uncommitted_receipt`)**：
   - 第一輪記錄之 `focused_pytest_net002_and_governance` 等收據綁定未提交之暫態 SHA（`4b3512109a96e6255dca8e5b61efab4f66453183`），無法回溯當時精確 commit。
   - 依據專案 Verification Policy 與 Task Acceptance（「不重跑已有成功套件只為統計」、「文檔工作不新增鏡像 implementation 的 tests，不為湊證據重跑無關 suite」），歷史 PR #1187 exact head `3bd82e8a401d463ef8301671620d45fc93b6127b` 既有 7 項 required CI 已完整覆蓋該等測試檔；故第一輪未提交之暫態 pytest 收據正式撤回（withdrawn as uncommitted transient receipt），不為湊 count 重跑舊產品測試套件。
2. **2026-09-12 第二輪收據補正**：
   - 第二輪審查指出收據缺乏精確 HEAD 綁定、時長及原始工具輸出 reference；本輪已依 Codex 審查意見全面補正真實命令、精確執行時間、時長、terminal exit code 及原始工具輸出 snippet。

### 5.2 本輪實測收據 (Current Measured Verification Receipts)

於本任務工作區 `/tmp/pantheon-worker-worktrees/pantheon/odp-net002-lease-disposition-001` 實測收據：

#### 收據 1：Git Diff Check
- **Command**: `git diff --check origin/dev HEAD`
- **Worktree**: `/tmp/pantheon-worker-worktrees/pantheon/odp-net002-lease-disposition-001`
- **Measured Base SHA**: `3828c5ada2a1baab33d7dbe734c7ec70152d3d77`
- **Started At**: `2026-09-12T09:39:28Z`
- **Finished At**: `2026-09-12T09:39:28Z`
- **Duration**: `0.013s`
- **Exit Code**: `0`
- **Output**: *(Clean, no whitespace errors)*

#### 收據 2：集合型需求成員檢查器
- **Command**: `python3 delivery_toolchain/governance/check_requirement_members.py`
- **Worktree**: `/tmp/pantheon-worker-worktrees/pantheon/odp-net002-lease-disposition-001`
- **Started At**: `2026-09-12T09:39:28Z`
- **Finished At**: `2026-09-12T09:39:28Z`
- **Duration**: `0.473s`
- **Exit Code**: `0`
- **Output Summary**: `Requirement member checks passed: 9 set-valued requirements, 47 members (37 satisfied, 10 absent and noted; dispositions: BLOCKED_BY_EVIDENCE=4, DECIDED=1, IMPLEMENTATION_READY=3, OPEN=3, VERIFIED=36).`

#### 收據 3：Canonical 看板與依賴圖拓撲無環驗證
- **Command**: `python3 -c "import json; b = json.load(open('/home/lupin/odayplus/ai-status.json')); tasks = {t['id']: t.get('depends_on', []) for t in b.get('tasks', [])}; visited = {};\ndef has_cycle(u):\n    visited[u] = 1\n    for v in tasks.get(u, []):\n        if v in tasks:\n            if visited.get(v, 0) == 1: return True\n            if visited.get(v, 0) == 0 and has_cycle(v): return True\n    visited[u] = 2\n    return False\ncycle = any(has_cycle(n) for n in tasks if visited.get(n, 0) == 0);\nassert not cycle;\nassert tasks.get('ODP-NET002-LEASE-DISPOSITION-001') == [];\nassert 'ODP-NET002-LEASE-DISPOSITION-001' in tasks.get('ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001', []);\nassert tasks.get('ODP-NET002-LEASE-IMPLEMENTATION-001') == ['ODP-NET002-LEASE-CONTRACT-PREP-001'];\nprint('Full canonical task DAG cycle check passed: 0 cycles detected.')"`
- **Worktree**: `/tmp/pantheon-worker-worktrees/pantheon/odp-net002-lease-disposition-001`
- **Started At**: `2026-09-12T09:39:28Z`
- **Finished At**: `2026-09-12T09:39:28Z`
- **Duration**: `0.094s`
- **Exit Code**: `0`
- **Output Summary**: `Full canonical task DAG cycle check passed: 0 cycles detected.`

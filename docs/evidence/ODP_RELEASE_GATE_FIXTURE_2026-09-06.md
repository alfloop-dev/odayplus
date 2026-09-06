# ODP-RELEASE-GATE-FIXTURE-STAGING-002 證據文件

**Task ID**：`ODP-RELEASE-GATE-FIXTURE-STAGING-002`  
**Owner**：`Antigravity2`  
**Reviewer**：`Codex`  
**Date**：`2026-09-06`  
**Target Branch**：`dev`  

---

## 1. 問題與背景 (Problem & Context)

在接續 `ODP-RELEASE-GATE-FIXTURE-STAGING-001`（該任務因 supervisor 設定誤設為唯讀檢查而 supersede archive，未產生程式/測試修改）的過程中，我們識別並全面解決了 Release Gate E2E 測試對 canonical registry 姿態與環境歷史的耦合問題（針對 Review Finding exact `696665b7` / PR #1212 進行完整收斂）：

1. **Gate E2E 測試對 Committed Registry 姿態的耦合 (Fixture Coupling Gap)**：
   - 既有的 `tests/e2e/test_release_gate_registry.py` 中，部分測試硬編碼假設 committed registry 永久處於 `no-go`、`receipts == []`、全部 7 道 gates blocked 以及 `--require-go` 必敗的狀態。若未來 canonical registry 納入真實收據或在 release E 階段進入 GO 狀態，這些靜態斷言將會誤報紅燈。
   - 正式 integration 測試應動態驗證 committed registry 當前記錄的決策與收據/blocker 狀態一致性，而不硬性假定其永久為 NO-GO。

2. **Negative Mutation 測試的獨立性 (Negative Mutation Hermeticity)**：
   - `staged_registry_fixture` 與 mutation 測試若直接繼承 committed registry 的欄位，一旦特定 gate 未來被 attestation 標記為 passed 或帶有 signoff，則針對 missing signoff、blocked blocker、缺 justification（not-applicable）或缺 deviation（passed-with-deviation）的負例測試將可能因繼承值而失效。
   - 解決方案：引入正規化之 `blocked_registry_baseline()`，保留 candidate/manifest/evidence 的真實路徑座標，但正規化測試姿態（全數 blocked、receipts=[]、測試專用 blocker、無 signoff/deviation/justification），staged fixture 與 mutation 測試皆依此建立，且負例測試顯式執行 `pop` 與 status 設定。

3. **舊 Blocker 檢查解耦 (Archived Tasks Assertion Decoupling)**：
   - 保留禁止已封存任務聲稱 open（`assert f"{task_id} is open" not in blockers`）的治理規則，但移除固定 `'archived done'` 措辭與 NO-GO 斷言，避免文案微調或 gate 通過後破壞測試。

4. **Ancestry 負例測試的環境獨立性 (Hermetic Git Ancestry Verification)**：
   - 原測試 `test_cli_expected_sha_ancestry_non_evidence_descendant_fails_closed` 固定了遠古 commit SHA（`eed83c09...`）與工作區 `HEAD`，在 shallow clone 或歷史變更時容易失效。
   - 解決方案：改於 `tmp_path` 內建立全新獨立 git repo，構造 candidate commit 與包含非證據路徑（如 `src/feature.py`）之 product change commit，完全不依賴外部 git 環境歷史。

5. **CLI Target 測試完整性 (CLI Target Test Completeness)**：
   - 新增之分階段 CLI 測試不僅斷言 `blocking_gates`，同時斷言命令執行之 `returncode == 0` 以及 `integrity_errors == []`，確保各階段 JSON report 結構與驗證器無任何隱藏錯誤。

6. **C→E Ancestry 驗證要求 (Candidate-to-Evidence Ancestry Enforcement)**：
   - 依據 `check_candidate_ancestry` 規則，發布候選版本（Candidate C）與證據提交（Evidence E）之間的中間 commits 必須嚴格限定為純證據路徑（`is_evidence_path`）。
   - 本任務之測試解耦修補為 Candidate C 的必要改動，必須先合入 `dev`，避免後續真實 GO Evidence E 需要修改 test file 而破壞 ancestry 檢查。

---

## 2. 實作變更 (Changes Implemented)

### 2.1 實作正規化 Baseline 與 Staged Fixture Helper
- **檔案**：`tests/e2e/test_release_gate_registry.py`
  - 新增 `blocked_registry_baseline() -> dict[str, Any]`：
    - 保留真實 committed registry 的 schema_version、registry_id、migration、candidate_sha、manifest_ref、manifest_digest 及 evidence 路徑。
    - 正規化測試姿態：`decision="no-go"`, `receipts=[]`, 7 道 gates 全為 `blocked` 並附帶測試 blocker，清除任何殘留之 signoff、deviation 與 justification。
  - 重構 `staged_registry_fixture() -> dict[str, Any]`：
    - 基於 `blocked_registry_baseline()` 建立，映射 rollout plan §6.1 之分階段門檻：
      - **Dev 邊界**：Gate 0（Code Gate）、Gate 1（Contract Gate）、Gate 4（Security and Privacy Gate）-> `stage="candidate-built"`, `environment="dev"`, `admission_target="dev"`
      - **Staging 邊界**：Gate 2（Data Gate）-> `stage="dev-verified"`, `environment="dev"`, `admission_target="staging"`
      - **Production 邊界**：Gate 3（Model and Solver Gate）、Gate 5（E2E, Performance and UAT Gate）、Gate 6（Ops, Release and Audit Gate）-> `stage="staging-verified"`, `environment="staging"`, `admission_target="production"`
  - 更新 `mutated(mutate, *, base=None)` 預設使用 `blocked_registry_baseline()`。
  - 更新 `clear_gate(gate)` 與 `clear_all_gates(registry)` 自動清除 deviation/justification 並綁定對應 release SHA 收據。

### 2.2 解耦 Committed Registry 與動態狀態斷言
- **檔案**：`tests/e2e/test_release_gate_registry.py`
  - `test_committed_registry_matches_recorded_decision_and_gates`：動態驗證 committed registry 中的 gate status 與 receipts/blockers 的邏輯一致性，不再硬編碼 `decision == "no-go"` 或 `receipts == []`。
  - `test_blocked_baseline_records_no_go_with_zero_receipts`：專門針對 baseline fixture 斷言 NO-GO 與 zero receipts。
  - `test_cli_accepts_the_committed_registry`：動態讀取 `--json` 的 `release_state`，斷言 CLI 輸出相符且 `returncode == 0`。
  - `test_cli_require_go_rejects_synthetic_blocked_registry`：使用合成 baseline 檔案驗證 `--require-go` 嚴格拒絕 NO-GO registry（exit code 1）。
  - `test_cli_require_go_matches_committed_registry_posture`：動態根據當前 committed registry 是否為 GO 驗證 `--require-go` 的 exit code。
  - `test_cli_json_report_matches_committed_registry`：動態比對 CLI JSON report 與 committed registry 的 cleared_gates、blocking_gates 及 release_state，並斷言 `integrity_errors == []`。
  - `test_dev_merge_gate_accepts_valid_registry_and_require_go_checks_packet`：驗證 `--dev-merge` 靜態通過，並確認 `--require-go` 下當 registry 非 GO 或缺少收據 packet 時嚴格 fail closed。
  - `test_registry_does_not_report_archived_done_tasks_as_open`：移除 `"archived done" in blockers` 與 NO-GO 斷言，專注確保已封存任務不被聲稱為 open。

### 2.3 強化 Negative Mutation 測試獨立性
- **檔案**：`tests/e2e/test_release_gate_registry.py`
  - `test_go_decision_requires_human_signoff`：顯式 `pop("human_signoff", None)`。
  - `test_dev_go_decision_requires_dev_boundary_gates_cleared`：顯式將 Gate 1 設定為 `status="blocked"`, `receipts=[]`, `blockers=[...]`。
  - `test_not_applicable_gate_requires_a_justification`：顯式 `pop("justification", None)`。
  - `test_passed_with_deviation_requires_an_approved_deviation`：顯式 `pop("deviation", None)`，局部 mutation 顯式 `pop("review_by", None)`。
  - `test_blocked_gates_with_unmatched_target_fails_closed_under_require_go`：使用 `blocked_registry_baseline()` 並斷言 `report["integrity_errors"] == errors` 及 CLI returncode 1。

### 2.4 實作完全隔離之 Ancestry 測試
- **檔案**：`tests/e2e/test_release_gate_registry.py`
  - `test_cli_expected_sha_ancestry_non_evidence_descendant_fails_closed`：使用 `tmp_path` 建立乾淨的 git repo，構造 candidate commit 與包含 `src/feature.py` 之非證據 commit，精確驗證 validator 攔截非證據檔案。

### 2.5 擴充 CLI Target 測試斷言
- **檔案**：`tests/e2e/test_release_gate_registry.py`
  - `test_cli_json_report_filters_by_admission_target`：對 dev、staging、production 的 CLI JSON 報告均斷言 `returncode == 0` 與 `integrity_errors == []`。

---

## 3. 測試涵蓋與驗證 (Test Coverage & Verification)

### 3.1 驗證指令與結果 (Verification Receipts)

1. **Release Gate Registry E2E Suite**：
   ```bash
   uv run pytest tests/e2e/test_release_gate_registry.py
   # 56 passed in 10.22s
   ```

2. **Authoritative Gate Registry Checker**：
   ```bash
   python3 delivery_toolchain/e2e/check_release_gate_registry.py
   # Output:
   # Release gate registry: ODP-RELEASE-GATE-REGISTRY
   # Release candidate SHA: ebc4fca5c2dd5871275aee39a18406dd67464f04
   # Admission boundary: candidate-built / dev -> dev
   # Recorded decision: no-go
   # Gates cleared: 0/7
   # RELEASE STATE: NO-GO
   # Release gate registry checks passed.
   # Exit code: 0
   ```

3. **Product Release Gate Dev Merge Mode**：
   ```bash
   python3 delivery_toolchain/e2e/check_product_release_gate.py --dev-merge
   # Output:
   # dev merge gate static checks passed
   # Exit code: 0
   ```

4. **Code Boundary Governance Check**：
   ```bash
   python3 delivery_toolchain/governance/check_code_boundaries.py
   # Output:
   # Code boundary checks passed for 1126 files.
   # Exit code: 0
   ```

---

## 4. 交付與邊界說明 (Delivery Boundary Statement)

本任務為 Candidate C 建置前的測試解耦維護（Test Maintenance on Candidate C）：
- 僅修補 `tests/e2e/test_release_gate_registry.py` 與本證據文件。
- 不更動 `delivery_toolchain/e2e/check_release_gate_registry.py` 驗證器核心。
- 不更動 `docs/evidence/gates/RELEASE_GATE_REGISTRY.json` 之真實 candidate、manifest 或 release lease。
- 不冒稱已部署或已達成 release GO。
- 測試通過保證了未來在 Evidence E 階段寫入真實收據或切換分階段門檻時，E2E 測試套件具備完整的相容性與嚴格的 fail-closed 驗證能力。

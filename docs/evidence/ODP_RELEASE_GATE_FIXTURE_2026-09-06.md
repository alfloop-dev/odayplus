# ODP-RELEASE-GATE-FIXTURE-STAGING-002 證據文件

**Task ID**：`ODP-RELEASE-GATE-FIXTURE-STAGING-002`  
**Owner**：`Antigravity2`  
**Reviewer**：`Codex`  
**Date**：`2026-09-06`  
**Target Branch**：`dev`  

---

## 1. 問題與背景 (Problem & Context)

在接續 `ODP-RELEASE-GATE-FIXTURE-STAGING-001`（該任務因 supervisor 設定誤設為唯讀檢查而 supersede archive，未產生程式/測試修改）的過程中，我們識別並解決了 Release Gate E2E 測試與 canonical registry 姿態之間的耦合問題：

1. **Gate E2E 測試對 Committed Registry 姿態的耦合 (Fixture Coupling Gap)**：
   - 既有的 `tests/e2e/test_release_gate_registry.py` 中，部分測試（例如 `test_blocking_gates_filters_by_target`、`test_cli_json_report_lists_blocking_gates`、`test_go_decision_with_no_gates_bound_to_admission_target_is_rejected` 與 `test_blocked_gates_with_unmatched_target_fails_closed_under_require_go`）直接透過 `load_committed_registry()` 載入檔案，並硬編碼假設 committed registry 的 7 道 gates 全部將 `admission_target` 設為 `dev`。
   - 一旦 canonical registry 依據《全系統部署與短生命週期 Staging 規劃》（`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md` §6.1）更新為分階段門檻（Dev: Gate 0, 1, 4；Staging: Gate 2；Production: Gate 3, 5, 6），這些測試將會因期待 7 道全部為 dev 而中斷失敗。

2. **C→E Ancestry 驗證要求 (Candidate-to-Evidence Ancestry Enforcement)**：
   - 在 `check_candidate_ancestry` 規則下，發布候選版本（Candidate C）與證據提交（Evidence E）之間的中間 commits 必須嚴格限定為純證據路徑（`is_evidence_path`）。若將此項測試修復推遲到 Evidence E 中，會因為修改了 `tests/e2e/test_release_gate_registry.py`（非證據路徑）而破壞 C→E ancestry 檢查。
   - 因此，這項必要的測試解耦修補必須作為獨立 Candidate C 的一部分先行合入 `dev`。

---

## 2. 實作變更 (Changes Implemented)

### 2.1 實作分階段 Registry Fixture Helper (`staged_registry_fixture`)
- **檔案**：`tests/e2e/test_release_gate_registry.py`
  - 新增 `staged_registry_fixture() -> dict[str, Any]` 函式，以 local deepcopy 方式自 committed registry 複製並顯式設定 rollout plan §6.1 的分階段門檻配置：
    - **Dev 邊界**：Gate 0（Code Gate）、Gate 1（Contract Gate）、Gate 4（Security and Privacy Gate）-> `stage="candidate-built"`, `environment="dev"`, `admission_target="dev"`
    - **Staging 邊界**：Gate 2（Data Gate）-> `stage="dev-verified"`, `environment="dev"`, `admission_target="staging"`
    - **Production 邊界**：Gate 3（Model and Solver Gate）、Gate 5（E2E, Performance and UAT Gate）、Gate 6（Ops, Release and Audit Gate）-> `stage="staging-verified"`, `environment="staging"`, `admission_target="production"`

### 2.2 重構分階段准入與 Gate 篩選測試
- **檔案**：`tests/e2e/test_release_gate_registry.py`
  - `test_staging_and_prod_gates_do_not_block_dev_go_decision`：改用 `staged_registry_fixture()`，僅清除 Dev 邊界 gates（0, 1, 4）並驗證通過，不再手動分散設定各 gate 階段。
  - `test_dev_go_decision_requires_dev_boundary_gates_cleared`：改用 `staged_registry_fixture()`，清除 gates 0, 4 但保留 gate 1 blocked，驗證 Dev 准入嚴格 fail-closed。
  - `test_blocking_gates_filters_by_target`：改用 `staged_registry_fixture()`，驗證 `target="dev"` 回傳 `["gate-0", "gate-1", "gate-4"]`、`target="staging"` 回傳 `["gate-2"]`、`target="production"` 回傳 `["gate-3", "gate-5", "gate-6"]`，以及無 target 時回傳全部 7 道 gates。驗證將 gate-2 動態重新指派給 dev 時正確反映在 `target="dev"` 中。
  - `test_go_decision_with_no_gates_bound_to_admission_target_is_rejected`：顯式將所有 7 道 gates 設定為 `admission_target="dev"`，以驗證當 release 請求 `admission_target="staging"` 且無任何 gate 綁定時嚴格 fail-closed。
  - `test_blocked_gates_with_unmatched_target_fails_closed_under_require_go`：顯式將所有 gates 綁定至 `dev`，驗證在 `--require-go` 下對不匹配的 staging target 輸出 exit code 1。

### 2.3 解耦 CLI JSON 報告測試並擴充 Target 驗證
- **檔案**：`tests/e2e/test_release_gate_registry.py`
  - `test_cli_json_report_lists_blocking_gates`：動態對比 committed registry release 之 `admission_target` 所對應的 blocking gates，不再假設 committed registry 永久保持 7 道全部為 dev。
  - `test_cli_json_report_filters_by_admission_target`：新增測試，利用 `staged_registry_fixture()` 分別輸出 dev、staging、production 的 mock registry 檔案，透過 CLI `--registry ... --json` 驗證各環境之 blocking gates 正確被篩選隔離。

### 2.4 保持範圍與約束
- **未修改**：不修改 `delivery_toolchain/e2e/check_release_gate_registry.py`、不更動 `docs/evidence/gates/RELEASE_GATE_REGISTRY.json` 的真實證據/manifest/lease/workflow，不放寬任何安全斷言。
- **維持結構與 fail-closed 驗證**：所有針對 committed registry 結構、7 道 gates 聲明、owner/reviewer、SHA 綁定與 NO-GO 狀態的測試全部原樣保留。

---

## 3. 測試涵蓋與驗證 (Test Coverage & Verification)

### 3.1 驗證指令與結果 (Verification Receipts)

1. **Release Gate Registry E2E Suite**：
   ```bash
   uv run pytest tests/e2e/test_release_gate_registry.py
   # 54 passed in 11.07s
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

## 4. 交付結論 (Closeout Conclusion)

本任務已完成 `tests/e2e/test_release_gate_registry.py` 之 fixture 解耦，所有 54 個 E2E 測試全數通過，使測試套件能同時支援當前 canonical registry 與後續分階段門檻更新，並符合 C→E ancestry 與 fail-closed 規範。

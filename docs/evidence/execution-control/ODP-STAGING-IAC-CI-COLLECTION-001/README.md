# ODP-STAGING-IAC-CI-COLLECTION-001: 讓 Ephemeral Staging Terraform 契約測試真正進入 CI

## 1. 任務基本資訊 (Task Metadata)

- **Task ID**: `ODP-STAGING-IAC-CI-COLLECTION-001`
- **Title**: 讓 ephemeral staging Terraform 契約測試真正進入 CI
- **Owner**: `Antigravity2`
- **Reviewer**: `Codex2`
- **Base SHA**: `1260d977345f18f4c8db05821e238be87614f4b2` (origin/dev)
- **Branch**: `task/ODP-STAGING-IAC-CI-COLLECTION-001`
- **Target Branch**: `dev`
- **Date**: 2026-09-09

---

## 2. 根本原因與現況分析 (Root Cause & Gap Analysis)

在 `ARCHIVE_RECOVERY_EVIDENCE_20260906` 歷史證據盤點中，`ODP-EPHEMERAL-STAGING-IAC-001` 被標記為 `test_delivered_not_executed_at_exact_head`，原因如下：
1. **Pytest testpaths 遺漏**: `pyproject.toml` 中的 `[tool.pytest.ini_options].testpaths` 包含 `.orchestrator`, `delivery_toolchain`, `scripts`, `tests`, `modules`, `apps`, `shared`, `models`，但遺漏了 `infra`。
2. **CI Workflow 收集遺漏**: `.github/workflows/ci.yml` 中各 job 的 pytest 指令未包含 `infra/`。
3. **Change Scope 判斷導致 Skip**: `config/change-review-scopes.json` 將 `infra/terraform/` 列為 `development_tooling`。在 PR 僅改動 infrastructure/tooling 時，`product` job（包含其餘大部分 pytest）會被 `if: ${{ needs.change-scope.outputs.scope != 'development_tooling' }}` 略過。唯一會無條件執行的 `orchestrator` job 原本未設定 Terraform 工具，也未收集 `infra`。
4. **CI 缺少工具時的不安全 Skip 陷阱**: `infra/terraform/tests/test_ephemeral_staging.py` 過去在缺少 `terraform` 或 `terraform init` 失敗時採用 `unittest.SkipTest`，導致 CI 環境若缺少工具時會偽裝成綠色通過（green skip）。

---

## 3. 解決方案架構 (Solution Architecture)

本任務針對上述缺口實施精準修復：

### 3.1 CI Workflow 整合 (`.github/workflows/ci.yml`)
- 在無條件執行的 `orchestrator` required job 中加入固定版本 Terraform 安裝步驟：
  ```yaml
  - name: Set up Terraform
    uses: hashicorp/setup-terraform@v3
    with:
      terraform_version: "1.9.8"
      terraform_wrapper: false
  ```
- 在 `orchestrator` job 的 linter 與 test step 中將 `infra` 納入檢查：
  ```yaml
  - name: Lint orchestrator and infrastructure code
    run: uv run ruff check .orchestrator delivery_toolchain scripts infra

  - name: Test orchestrator and infrastructure code
    run: uv run pytest -m "not requires_live_env" .orchestrator delivery_toolchain scripts tests/tooling infra
  ```
- 確保無論是 `development_tooling` 還是 `product_or_mixed` 變更範圍，18 項 ephemeral staging 測試均被 CI 完整收集與執行。

### 3.2 Pyproject 配置 (`pyproject.toml`)
- 在 `testpaths` 列表中加入 `"infra"`，確保本地與工具鏈執行 pytest 時能一致收集基礎設施契約與 plan 測試。
- 配置 `norecursedirs = [".orchestrator/source-doc-cache", ".venv", "node_modules"]` 避免本地工作目錄 cache 干擾。

### 3.3 測試 Harness Fail-Closed 防護 (`infra/terraform/tests/test_ephemeral_staging.py`)
- 當處於 CI 環境（`os.environ.get("CI")`）且找不到 `terraform` 二進位檔時，直接以 `AssertionError` / `self.fail()` 失敗結束，禁止綠色跳過。
- `terraform init` 失敗時直接拋出 `AssertionError`，禁止跳過。
- 保持 offline plan harness 特性：不需 GCP 雲端登入/憑證 (ADC)、移除 remote backend、不觸發 apply，且不放鬆 production module 的任何防護。

### 3.4 工具鏈守門測試 (`tests/tooling/test_staging_iac_ci_collection.py`)
- 建立 9 項守門測試，覆蓋：
  1. `ci.yml` 包含 pinned Terraform 1.9.8 與 `terraform_wrapper: false`。
  2. `ci.yml` `orchestrator` job 無條件執行且收集 `infra`。
  3. `pyproject.toml` 包含 `infra`。
  4. `change_scope` 正確將 `infra/terraform/` 歸類為 `development_tooling` 並維持 CI 執行。
  5. 驗證 18 項測試 node IDs 完整性（13 Python/HCL 契約 + 5 Terraform plan 測試）。
  6. 驗證 CI 下缺少 Terraform 時 fail-closed 的行為。

---

## 4. 測試節點清單與覆蓋率 (18 Test Node IDs)

`infra/terraform/tests/test_ephemeral_staging.py` 完整 18 項測試清單：

### Python / HCL 靜態與規格契約 (13 項)
1. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingModuleContractTests::test_all_release_scoped_cloud_run_resources_use_controlled_vpc_egress`
2. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingModuleContractTests::test_creation_and_owner_inputs_are_required`
3. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingModuleContractTests::test_cross_implementation_tenant_and_owner_normalization`
4. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingModuleContractTests::test_ephemeral_staging_module_structure_and_tokens`
5. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingModuleContractTests::test_mandatory_labels_win_over_additional_labels`
6. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingModuleContractTests::test_module_contains_isolated_resources`
7. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingModuleContractTests::test_module_no_dynamic_timestamp_leak`
8. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingModuleContractTests::test_module_outputs_do_not_leak_secrets`
9. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingModuleContractTests::test_module_variables_validation_rules`
10. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingModuleContractTests::test_provider_configuration_and_resource_projects`
11. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingModuleContractTests::test_release_and_owner_identity_matches_python_normalization_order`
12. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingModuleContractTests::test_scheduler_worker_invoker_iam_binding`
13. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingModuleContractTests::test_tenant_isolation_contract`

### Terraform Plan 離線驗證 (5 項)
14. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingModuleContractTests::test_terraform_standalone_plan_guards_future_timestamp_and_accepts_valid`
15. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingDefaultTenantPlanTests::test_default_generated_tfvars_plan_succeeds_with_derived_tenant`
16. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingDefaultTenantPlanTests::test_explicit_tenant_still_wins_over_the_derived_one`
17. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingDefaultTenantPlanTests::test_plan_tolerates_an_explicitly_empty_tenant_id`
18. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingDefaultTenantPlanTests::test_terraform_and_python_derive_the_same_bounded_tenant`

---

## 5. 本地驗證收據 (Verification Receipts)

### Receipt 1: `git diff --check`
- **Command**: `git diff --check`
- **Exit Code**: `0`
- **Status**: Passed (no whitespace/conflict marker defects)

### Receipt 2: `uv run pytest infra/terraform/tests/test_ephemeral_staging.py -q`
- **Command**: `uv run pytest infra/terraform/tests/test_ephemeral_staging.py -q`
- **Exit Code**: `0`
- **Test Result**: `18 passed in 28.95s`
- **Terraform Version**: `Terraform v1.9.8 on linux_amd64`

### Receipt 3: `uv run pytest tests/tooling/test_staging_iac_ci_collection.py -q`
- **Command**: `uv run pytest tests/tooling/test_staging_iac_ci_collection.py -q`
- **Exit Code**: `0`
- **Test Result**: `9 passed in 0.25s`

### Receipt 4: `uv run ruff check`
- **Command**: `uv run ruff check .orchestrator delivery_toolchain scripts infra tests/tooling`
- **Exit Code**: `0`
- **Status**: `All checks passed!`

---

## 6. 邊界與非目標聲明 (Non-goals & Boundary Guarantees)

1. **不回寫歷史盤點或存檔**: 本任務僅聲明修復當前 `dev` 分支的 CI 收集問題，不修改 `ai-task-archive`、`ARCHIVE_RECOVERY_EVIDENCE_20260906/` 或其他歷史 sidecar。
2. **不釋放 Human Gate**: 不解除歷史前置任務之 Human Approval Gate，亦不宣稱歷史 PR 當時已具備 live staging 驗收。
3. **不放寬生產防護**: 不為測試修改 `infra/terraform/modules/` 中的生產防護邏輯，所有離線 plan 均在 test-side harness 中安全進行。

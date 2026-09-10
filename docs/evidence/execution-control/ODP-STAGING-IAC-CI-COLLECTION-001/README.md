# ODP-STAGING-IAC-CI-COLLECTION-001: 讓 Ephemeral Staging Terraform 契約測試真正進入 CI

## 1. 任務基本資訊 (Task Metadata)

- **Task ID**: `ODP-STAGING-IAC-CI-COLLECTION-001`
- **Title**: 讓 ephemeral staging Terraform 契約測試真正進入 CI
- **Owner**: `Claude`（2026-09-10 由 `Antigravity2` → `Antigravity5` → `Claude` 重新指派；首個交付 commit `ba4e8c5f` 由 `Antigravity2` 產出）
- **Reviewer**: `Codex`
- **Branch**: `task/ODP-STAGING-IAC-CI-COLLECTION-001`
- **Target Branch**: `dev`
- **Base after advance**: `6218337fbe88b99ee45af6c98e0c2f9e4a33fbee`（merge commit `c4be4509`；task brief 開立時的 base 是 `1260d977`）
- **Code head measured here**: `1965b3636e93881aedb82a9d68282a81f7443a58`
- **Date**: 2026-09-10

---

## 2. 缺口 (Gap)

`infra/terraform/tests/test_ephemeral_staging.py` 的 18 項測試在 `origin/dev` 上沒有任何 CI job 會收集：

1. **`pyproject.toml` 的 `testpaths` 沒有 `infra`** — 裸跑 `pytest` 收不到。
2. **`.github/workflows/ci.yml` 沒有任何 pytest step 以 `infra` 為 target**。
3. **僅靠 `product` job 補不回來** — `infra/terraform/` 在 `config/change-review-scopes.json` 屬 `development_tooling`，而 `product` 與 `performance-gate` 都帶
   `if: needs.change-scope.outputs.scope != 'development_tooling'`。只改 infra 的 PR 會把這兩個 job 整組 skip。唯一無條件執行的是 `orchestrator` job，而它當時既沒有 Terraform 也沒有收 `infra`。
4. **缺工具時是綠色 skip** — 測試在找不到 `terraform` 或 `terraform init` 失敗時走 `unittest.SkipTest`，於是「沒驗過」與「驗過且通過」在 CI 上長得一模一樣。

本任務只修這個 CI coverage 缺口，不重做 `ARCHIVE_RECOVERY_EVIDENCE_20260906` 的歷史盤點。

---

## 3. 交付內容 (What Changed)

### 3.1 `.github/workflows/ci.yml` — 進入無條件執行的 job（commit `ba4e8c5f`）

`orchestrator` job 沒有 `needs: change-scope`、也沒有 `if:`，因此不受 change-scope skip 影響。在其中：

- 加入釘死版本的 Terraform 安裝步驟（`hashicorp/setup-terraform@v3`、`terraform_version: "1.9.8"`、`terraform_wrapper: false`；wrapper 必須關閉，否則 stdout/stderr 會被 Node wrapper 攔截，測試讀不到 plan 輸出）。
- pytest step 加上 `infra` target：
  `uv run pytest -m "not requires_live_env" .orchestrator delivery_toolchain scripts tests/tooling infra`
- ruff step 加上 `infra` target（`product` job 原本就有 lint `infra`，但只改 infra 的 PR 會 skip 掉它）。

### 3.2 `infra/terraform/tests/test_ephemeral_staging.py` — fail-closed 與離線 harness

- **Fail closed（commit `ba4e8c5f`）**：`CI` 環境變數存在時，缺 `terraform` 直接 `fail()` / `AssertionError`；`terraform init` 非 0 也改為 `AssertionError`。CI 不再有綠色 skip。
- **離線 harness（commit `1965b363`）**：新增 `run_terraform()` / `offline_terraform_env()`，把「離線」從環境的偶然變成測試的性質：
  - 移除所有 `GOOGLE_*`、`GCLOUD_*`、`CLOUDSDK_*`、`GCP_*` 以及 `TF_VAR_credentials`、`TF_TOKEN_app_terraform_io`；
  - `HOME` 指向 scratch 目錄，因此 `~/.config/gcloud/application_default_credentials.json`（ADC）也找不到。沒有這一步，開發機上 `gcloud auth application-default login` 過的人會讓一個 CI 根本 plan 不動的 module 靜靜通過；
  - 只允許 `init` / `plan` / `validate`，其餘 subcommand（`apply`、`destroy`、`import`）一律 `AssertionError`；
  - `init` 一律附帶 `-backend=false`，不會設定 remote backend。
- 既有斷言全部保留：真實 HCL、未來 `created_at` 必須讓 plan 失敗、以及 default / empty / long / explicit tenant 四條 plan 斷言。production module（`infra/terraform/modules/`）一個字都沒改。

### 3.3 `tests/tooling/test_staging_iac_ci_collection.py` — 守門測試（16 項）

`ba4e8c5f` 的 9 項守門測試檢查的是**接線字串**。接線可以完全正確而選集仍然是空的：marker 表達式把整組 deselect、conftest 的 `collect_ignore`、或單純改名，這三種情況下 workflow 那一行長得一模一樣。`1965b363` 因此補上會真的執行的守門：

- `StagingIaCCollectionExecutionTests` 從 `ci.yml` 解析出 marker 與涵蓋該檔案的 target，實跑 `pytest --collect-only`，要求 18 個 node ID 全數回來且不重複。
- 同類別的 negative control 用一個必然為空的 marker 走同一條程式路徑，確認這個探針**看得見空選集**（pytest exit 5 視為「收集到 0 個」的合法觀測，其他 exit code 一律視為探針本身壞掉）。不能失敗的守門不是守門。
- `StagingIaCTerraformPinTests` 把 pin 綁到實際執行的 binary：GitHub 的 ubuntu image 自己就帶 Terraform，所以刪掉 setup-terraform step 並不會讓 plan 測試轉紅——它們會繼續對一個未宣告版本通過，而 `ci.yml` 裡的 pin 就變成純文件。現在 `CI` 下 `terraform version -json` 必須回報 `ci.yml` 宣告的版本。
- `EphemeralStagingOfflineHarnessTests` 直接測 harness 行為：植入的憑證變數確實被剝掉、`HOME` 確實被改寫、`apply`/`destroy`/`import` 確實被拒、`init` 確實帶 `-backend=false`。

### 3.4 `pyproject.toml`

- `testpaths` 加入 `"infra"`，讓本機裸跑 pytest 與 CI 的收集一致。
- `norecursedirs`：`ba4e8c5f` 寫成三筆清單，但 pytest 的 `norecursedirs` 是**取代**預設值而非附加，那會讓收集開始遞迴進 `.git`、`build`、`dist` 等目錄。`1965b363` 把 pytest 預設值逐條寫回，只額外加上 `.orchestrator/source-doc-cache`——它是 `.git/info/exclude` 排除的本機快取，收了 `infra` 之後裡面會出現第二個 `test_ephemeral_staging.py`，pytest 會以重複模組名拒絕收集。

---

## 4. 18 項測試 node IDs

以下清單取自第 5 節 Receipt 5 的實際收集輸出（不是人工整理）。

### Python / HCL 契約 (13)

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

### Terraform plan 離線驗證 (5)

14. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingModuleContractTests::test_terraform_standalone_plan_guards_future_timestamp_and_accepts_valid`
15. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingDefaultTenantPlanTests::test_default_generated_tfvars_plan_succeeds_with_derived_tenant`
16. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingDefaultTenantPlanTests::test_explicit_tenant_still_wins_over_the_derived_one`
17. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingDefaultTenantPlanTests::test_plan_tolerates_an_explicitly_empty_tenant_id`
18. `infra/terraform/tests/test_ephemeral_staging.py::EphemeralStagingDefaultTenantPlanTests::test_terraform_and_python_derive_the_same_bounded_tenant`

同一個 `infra` target 另外帶進 `infra/terraform/tests/test_contract.py` 的 14 項（該檔同樣從未被 CI 收集），因此 `infra` 在 CI 的實際收集數是 32。

---

## 5. 驗證收據 (Verification Receipts)

以下 Receipt 1–3 由 `delivery_toolchain/git/task_verification.py run` 產生，收據檔案在
`.orchestrator/evidence/verification-odp_staging_iac_ci_collection_001-*.json`，各自綁定 head SHA、確切命令、真實 exit code 與時長。全部量測於 code head `1965b3636e93881aedb82a9d68282a81f7443a58`。

| # | Command | Exit | Duration | Recorded (UTC) |
|---|---------|------|----------|----------------|
| 1 | `git diff --check` | 0 | 0.014s | 2026-09-10T10:00:54Z |
| 2 | `uv run pytest infra/terraform/tests/test_ephemeral_staging.py -q` | 0 | 21.038s | 2026-09-10T10:01:16Z |
| 3 | `uv run pytest tests/tooling/test_staging_iac_ci_collection.py -q` | 0 | 10.508s | 2026-09-10T10:01:26Z |

- Receipt 2 = 18 passed（`addopts` 已含 `-q`，加上命令自己的 `-q` 後不印摘要行，計數以第 5 節的收集輸出為準）。
- Receipt 3 = 16 passed。
- **Terraform version（本機實測）**: `terraform version -json` → `1.9.8`，與 `ci.yml` 的 pin 相同。

### Receipt 4: ruff（CI 的兩組 selection）

| Command | Exit | Result |
|---------|------|--------|
| `uv run ruff check .orchestrator delivery_toolchain scripts infra` | 0 | All checks passed! |
| `uv run ruff check tests modules apps shared models solver pipelines infra` | 0 | All checks passed! |

### Receipt 5: CI selection 的實際收集

以 `ci.yml` `orchestrator` job 的完整 selection 收集：

```
uv run pytest -o addopts= --collect-only -q -m "not requires_live_env" \
  .orchestrator delivery_toolchain scripts tests/tooling infra
```

- Exit code: `0`
- `2717/2727 tests collected (10 deselected) in 1.35s`
- `infra/terraform/tests/test_ephemeral_staging.py::` 開頭的 node ID：**18**
- `infra/` 開頭的 node ID：32
- `tests/tooling/test_staging_iac_ci_collection.py::` 開頭的 node ID：16

### Receipt 6: 守門測試的 mutation check

守門若無法失敗就等於沒有守門，因此各做一次反向驗證（改動後已還原，`git diff --stat .github/workflows/ci.yml` 為空）：

| Mutation | 結果 |
|----------|------|
| 從 `ci.yml` 的 pytest step 移除 `infra` target | exit 1；`test_orchestrator_pytest_targets_cover_the_ephemeral_staging_suite`、`test_ci_selection_collects_every_ephemeral_staging_test`、`test_the_collection_probe_can_actually_observe_an_empty_selection` 三項轉紅 |
| 把 `terraform_version` 從 `1.9.8` 改成 `9.9.9`（`CI=true`）| exit 1；`test_orchestrator_job_has_pinned_terraform_setup_step`、`test_ci_plans_with_the_pinned_terraform` 轉紅 |

### Receipt 7: base advance 完整性

| 檢查 | 結果 |
|------|------|
| `git merge-tree --write-tree HEAD origin/dev`（merge 前） | `f8d9c9349e4595497d27c876dae3ac179ec2c07c` |
| `git rev-parse HEAD^{tree}`（merge 後） | `f8d9c9349e4595497d27c876dae3ac179ec2c07c` — 逐位元相同，非縮水 merge |
| `git log -1 --format=%P` | 兩個 parent（`ba4e8c5f`、`6218337f`） |
| `git rev-list --count HEAD..origin/dev` | `0` |

---

## 6. Exact-head CI 證明

本地收據證明的是「選集會收到 18 項且它們會通過」。**實際在 CI 執行**的證明是這個 task branch 的 PR 上 `orchestrator` job 的 run/job URL，會在 PR 建立、required checks 跑完後補記於 task note；本文件不預先宣稱該 run 已通過。

---

## 7. 已知且不在本 scope 內的問題 (Out of Scope, Reported)

**裸跑 `uv run pytest`（不帶 target）在 `origin/dev` 上就已經是 collection error**，與本任務無關：

```
import file mismatch:
imported module 'test_release_lease' has this __file__ attribute:
  .orchestrator/test_release_lease.py
which is not the same as the test file we want to collect:
  tests/release/test_release_lease.py
```

已用 `dev` 當時的設定（預設 `norecursedirs`、`testpaths` 不含 `infra`）重現，確認是既有狀況、不是本次改動造成。CI 不受影響：`orchestrator` job 只收 `tests/tooling`，`product` job 只收 `tests modules apps shared models`，兩組 selection 都不會同時撞到這兩個同名檔案（Receipt 5 的 exit 0 即為證）。修正需要改 `.orchestrator/`（本任務 forbidden path）或 `tests/release/`，因此不在此處動手。

---

## 8. 邊界與非目標 (Non-goals)

1. **不回寫歷史盤點或存檔**：未修改 `ai-task-archive/`、`ARCHIVE_RECOVERY_EVIDENCE_20260906/` 或任何歷史 sidecar。
2. **不解除 Human gate**：未移除任何父 task 的 Human approval gate。
3. **不宣稱歷史交付**：不主張 PR #1002 / #1041 當時已有驗收，也不主張 live staging 曾經完成。本文件只宣稱 2026-09-10 起這 18 項測試進入 CI 選集。
4. **不改 production module**：`infra/terraform/modules/` 未變動，離線只在 test-side harness 達成，module 的任何 guard 都沒有放鬆。
5. **不擴大到 deployment**：`product_ops/` 未變動。若之後發現真正的 module 或雲端行為缺陷，另開 task 具體 handoff。
6. **與 `ORCH-REVIEW-CI-RECOVERY-001` 無交集**：未觸碰對方 owned paths。

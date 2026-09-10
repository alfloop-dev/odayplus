# ODP-STAGING-IAC-CI-COLLECTION-001: 讓 Ephemeral Staging Terraform 契約測試真正進入 CI

## 1. 任務基本資訊 (Task Metadata)

- **Task ID**: `ODP-STAGING-IAC-CI-COLLECTION-001`
- **Title**: 讓 ephemeral staging Terraform 契約測試真正進入 CI
- **Owner**: `Claude`（2026-09-10 由 `Antigravity2` → `Antigravity5` → `Claude` 重新指派；首個交付 commit `ba4e8c5f` 由 `Antigravity2` 產出）
- **Reviewer**: `Codex`
- **Branch**: `task/ODP-STAGING-IAC-CI-COLLECTION-001`
- **Target Branch**: `dev`
- **Base after advance**: `9d847c16cae91d5aa842adde0078b8a8b21e10c7`（merge commit `6b2a26cd`；task brief 開立時的 base 是 `1260d977`）
  - dispatch 要求的 base 是 `91dd050ad1e3`，但備妥這次 merge 期間 dev 已前進到 `9d847c16`（PR #1293，`ORCH-FROZEN-EVIDENCE-BASE-DISPATCH-001`），因此第二個 parent 是 `9d847c16`，`91dd050a` 包含在其中；`git rev-list --count HEAD..origin/dev` = `0`。
  - `6b2a26cd` 的 commit message 仍寫 `91dd050ad1e3`：那是訊息寫定當下的 dev tip，之後 dev 才前進。auto worker 無權 `git commit --amend`，因此在此更正，而不改寫已成形的 commit。
- **Base after advance (round 3)**: `8eee47a49065c7535767f97fc9977a126eef76ae`（merge commit `01b86ea1`，PR #1288 `ORCH-REVIEW-CI-RECOVERY-001`）。這次 merge 的是**釘死的 SHA 而非 `origin/dev` 這個 ref**，所以不會再有第二親與訊息不符的問題；完整性檢查見 Receipt 13。
- **Base after advance (round 4)**: `b66d18130f3bac78cf5af32b1c7000933e5d931d`（merge commit `1ca1030c`，PR #1294 `ORCH-STATUS-LOCK-REMOTE-PROBE-001` 與 PR #1149 `ODP-AVM-QUALITY-NULLABLE-001`）。merge tree 與 `git merge-tree --write-tree` 比對逐位元相同（`7fa6b92613a8f97c2ed6024a6420519e4e93fdd8`）；完整性檢查見 Receipt 14。
- **Code head measured here**: Receipt 1–9 量測於 `6b2a26cd52188805683e8866cf463d59fa4537f2`；Receipt 10–13（review round 3 的修復）量測於 `01b86ea1ccbb4a696ae577748204f070784dea57`；Receipt 14 量測於 `1ca1030cfce5f5d03add7d84b2d8378191d822c6`。宣告命令的正式收據於最終 head 重跑。
- **Date**: 2026-09-10
- **Review rounds**: round 1/2 的 ADC 與 `product` job Terraform 兩項 finding 已修復且未被重開；round 3 的 finding 是收集守門的 false negative，修法與量測見 §3.6 與 Receipt 10–12。round 4 為 dev base advance (`b66d18130f3b`) 正常合併。

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
- **離線 harness（commit `1965b363`，由 `0a9c27c9` 補完）**：新增 `run_terraform()` / `offline_terraform_env()`，把「離線」從環境的偶然變成測試的性質：
  - 移除所有 `GOOGLE_*`、`GCLOUD_*`、`CLOUDSDK_*`、`GCP_*` 以及 `TF_VAR_credentials`、`TF_TOKEN_app_terraform_io`；
  - `HOME` 指向 scratch 目錄，因此 `~/.config/gcloud/application_default_credentials.json`（ADC）也找不到；
  - **provider override（`0a9c27c9`）**：在 module 副本旁寫一個 `zz_offline_provider_override.tf`，給 google provider 一個合成的 `access_token`。Terraform 對 `*_override.tf` 是**逐 argument 合併**，因此 module 自己的 `project` / `region` 原封不動，只多出憑證這一項；換成一般檔名則會是 duplicate provider configuration 錯誤。
  - **`GOOGLE_APPLICATION_CREDENTIALS` 指向不存在的檔案（`0a9c27c9`）**：只剝環境變數並不夠——ADC 還有 GCE metadata server 這條路。詳見第 3.5 節。
  - 只允許 `init` / `plan` / `validate`，其餘 subcommand（`apply`、`destroy`、`import`）一律 `AssertionError`；
  - `init` 一律附帶 `-backend=false`，不會設定 remote backend。
- 既有斷言全部保留：真實 HCL、未來 `created_at` 必須讓 plan 失敗、以及 default / empty / long / explicit tenant 四條 plan 斷言。production module（`infra/terraform/modules/`）一個字都沒改。

### 3.3 `tests/tooling/test_staging_iac_ci_collection.py` — 守門測試（24 項）

`ba4e8c5f` 的 9 項守門測試檢查的是**接線字串**。接線可以完全正確而選集仍然是空的：marker 表達式把整組 deselect、conftest 的 `collect_ignore`、或單純改名，這三種情況下 workflow 那一行長得一模一樣。`1965b363` 因此補上會真的執行的守門：

- `StagingIaCCollectionExecutionTests` 從 `ci.yml` 解析出 marker 與涵蓋該檔案的 target，實跑 `pytest --collect-only`，要求 18 個 node ID 全數回來且不重複。
- 同類別的 negative control 用一個必然為空的 marker 走同一條程式路徑，確認這個探針**看得見空選集**（pytest exit 5 視為「收集到 0 個」的合法觀測，其他 exit code 一律視為探針本身壞掉）。不能失敗的守門不是守門。
- `StagingIaCTerraformPinTests` 把 pin 綁到實際執行的 binary：GitHub 的 ubuntu image 自己就帶 Terraform，所以刪掉 setup-terraform step 並不會讓 plan 測試轉紅——它們會繼續對一個未宣告版本通過，而 `ci.yml` 裡的 pin 就變成純文件。現在 `CI` 下 `terraform version -json` 必須回報 `ci.yml` 宣告的版本。
- `EphemeralStagingOfflineHarnessTests` 直接測 harness 行為：植入的憑證變數確實被剝掉、`HOME` 確實被改寫、`GOOGLE_APPLICATION_CREDENTIALS` 確實指向一個**不存在**的路徑（`Path(...).exists()` 為 False，不是只比對字串）、`apply`/`destroy`/`import` 確實被拒、`init` 確實帶 `-backend=false`。
- `test_the_offline_override_stays_out_of_the_production_module`（`0a9c27c9` 新增）守的是離線 shim 的**外洩方向**：`infra/terraform/modules/` 底下不得出現該 override 檔，任何 `*.tf` 也不得含 `access_token`。寫死的 `access_token` 進了會部署的 module，輕則把 provider 綁在一個死憑證上，重則是外洩的 secret。

### 3.4 `pyproject.toml`

- `testpaths` 加入 `"infra"`，讓本機裸跑 pytest 與 CI 的收集一致。
- `norecursedirs`：`ba4e8c5f` 寫成三筆清單，但 pytest 的 `norecursedirs` 是**取代**預設值而非附加，那會讓收集開始遞迴進 `.git`、`build`、`dist` 等目錄。`1965b363` 把 pytest 預設值逐條寫回，只額外加上 `.orchestrator/source-doc-cache`——它是 `.git/info/exclude` 排除的本機快取，收了 `infra` 之後裡面會出現第二個 `test_ephemeral_staging.py`，pytest 會以重複模組名拒絕收集。

### 3.5 修復 PR #1291 required CI 的兩處失敗（commit `0a9c27c9`）

Reviewer 於 2026-09-10T14:20:47Z reopen，指出 run `34464145157` 是**真的紅**、不是 stale gate。兩處失敗成因互不相同，兩處都不是靠「缺工具就 skip」修掉的。

#### (1) `orchestrator` job：5 項 plan 探針全紅

Job `102828645320` 的實際錯誤（自 CI log 取得，非轉述）：

```
Error: Attempted to load application default credentials since neither
`credentials` nor `access_token` was set in the provider block.
No credentials loaded.
```

`1965b363` 的 harness 只做到「把憑證環境變數剝乾淨」，但那只移除 ADC 的**檔案**路徑；google provider 仍然需要*某個*憑證才能完成 configure。修法是給它一個合成的 `access_token`（見 3.2）。

**為什麼本機一直是綠的**：本機開發機是 GCE VM（`Linux 7.0.0-1011-gcp`），ADC 在環境變數與 `HOME` 都被清空後，仍會 fallback 到 metadata server（實測 `169.254.169.254` 可達，回傳 `alfaloop-data-project-2`），provider 於是以該 VM 的身分通過。GitHub runner 沒有 metadata server，所以同一份 harness 在 CI 必紅。這正是 3.2 註解原本自稱要避免、卻沒真正擋住的「測試性質變成機器性質」。

因此 harness 另外把 `GOOGLE_APPLICATION_CREDENTIALS` 指向一個不存在的檔案：ADC 會**優先**讀它、讀不到就直接失敗，不會 fallback 到 metadata server。副作用是本機從此可以忠實重現 CI 的憑證條件。

**反向對照（negative control）**：把 `write_offline_provider_override()` 的寫檔那行換成 `pass`（其餘不動），在本機重跑：

（`addopts` 已含 `-q`，命令再帶 `-q` 會疊成 `-qq`，因此沒有摘要行；以下是 exit code 與實際的 `FAILED` 行）

```
exit code: 1
FAILED ...::EphemeralStagingModuleContractTests::test_terraform_standalone_plan_guards_future_timestamp_and_accepts_valid
FAILED ...::EphemeralStagingDefaultTenantPlanTests::test_default_generated_tfvars_plan_succeeds_with_derived_tenant
FAILED ...::EphemeralStagingDefaultTenantPlanTests::test_explicit_tenant_still_wins_over_the_derived_one
FAILED ...::EphemeralStagingDefaultTenantPlanTests::test_plan_tolerates_an_explicitly_empty_tenant_id
FAILED ...::EphemeralStagingDefaultTenantPlanTests::test_terraform_and_python_derive_the_same_bounded_tenant
```

與 CI job `102828645320` 的 5 個 node ID 及錯誤訊息**逐項相同**，且 `Attempted to load application default credentials` 出現 5 次。這同時證明兩件事：override 是承重的（拿掉就紅），以及本機現在量到的綠是 CI 條件下的綠，不是靠機器身分蒙混過去的。改動已還原。

#### (2) `product` job：`test_ci_plans_with_the_pinned_terraform` 失敗

Job `102828700363`。本檔位於 `tests/tooling`，而**兩個** job 都會收集它：`orchestrator`（裝了 Terraform、真的跑 plan 探針）與 `product`（`tests modules apps shared models`，刻意不裝 Terraform、也不跑探針）。原本的判準是 `os.environ.get("CI")`，於是它對著 `product` 要一個該 job 沒有理由攜帶的工具。

改成以 `GITHUB_JOB` 比對「**由 `ci.yml` 推導出來**、裝了 setup-terraform 的那個 job」（`terraform_plan_job()`），要求就跟著探針走，而不是釘死在某個 job 名字上。缺工具在**那個** job 仍然 fail-closed。

新增 `test_the_job_that_installs_terraform_is_the_one_that_runs_the_probes` 守住這個判準本身：若探針被搬到不裝 Terraform 的 job，`GITHUB_JOB` 永遠比不中，上面那條就會安靜地不再要求任何東西。

三種情境實測（見 Receipt 8）：`product` 無 Terraform → 綠；`orchestrator` 無 Terraform → 紅（fail-closed 保住）；`orchestrator` 有 Terraform → 綠且版本 pin 生效。

---

### 3.6 修復 review round 3 的 collection guard false negative（本次 dispatch）

Codex 在 exact head `2f9b277f` 的 review 指出一個 P2：守門測試的收集探針**看得見接線、看不見排除**。這是真的缺陷，本節記錄我自己在本分支 head 上的重量測、修法，以及新測試的反向驗證。

#### 缺陷

`_collect()` 不是「重放 CI 的選集」，而是「用解析結果重建一條命令」——只放回 `-m <marker>` 與第一個涵蓋該套件的 target。`parse_pytest_selection()` 除了 `-m` 以外的選項一律丟棄。於是任何**會縮小選集**的參數（`--ignore`、`--ignore-glob`、`--deselect`、`-k`、第二個 `-m`）都到不了探針手上：CI 那一行可以把整組 ephemeral staging 排除掉，而這道閘仍然回報「18 項都在」。

閘門在說謊的方向是最壞的那一種：它宣稱的正是本任務唯一的交付物。

#### 修法

`parse_pytest_selection()` 改回傳 `PytestSelection`，其中 `args` 是 `pytest` 之後**逐字保留、順序不變**的完整參數向量；`marker` / `targets` 降級為給接線斷言用的「讀法」，並在 docstring 寫明讀法必然是子集、執行探針才是權威。`_collect()` 直接重放 `args`，不再重建。

選擇「逐字重放」而不是「明列白名單、遇到不認得的選項就拒絕」，理由是失效方向：重建式的探針對沒寫到 case 的參數是**靜默丟棄**（false pass），逐字重放對沒預期到的參數最差只是探針自己壞掉，而 `_collect()` 既有的 return-code 斷言（只接受 0 與 5）會把那個情況變成紅燈。

一個副作用要講清楚：探針現在會收集 `orchestrator` job 收集的全部內容，時間從約 12s 增加到約 30s。這不新增脆弱性——該選集只要有任何收集錯誤，job 本身就已經整個失敗，不存在「CI 綠而此探針紅」的狀態。

#### 新增的守門（18 → 24 項）

- `test_the_probe_replays_an_exclusion_that_empties_the_suite`：把 `--ignore=infra/terraform/tests` 加進選集後，探針必須回報 0。這正是 Codex 量到的那個 case。測試裡寫明為什麼**必須用子目錄**：`--ignore=infra` 不成立，pytest 對命令列上明確指名的 target 會優先於對同一路徑的 ignore，套件照收，什麼也證明不了。測試同時斷言 `ci.yml` 沒有直接指名該子目錄，免得這個前提哪天悄悄失效。
- `PytestSelectionParsingTests`（5 項，不開子行程）：把「不得丟參數」這個性質本身釘住——`args` 對 `pytest` 之後的 token 必須逐字相同、五個縮小選集的參數必須都在 `args` 裡、`targets` 明確被斷言為有損讀法（不含選項的值）、`with_marker()` 只換 marker 不動其他 token。

這 6 項不是重複 implementation 字串：其中 1 項用實際子行程觀測收集結果，5 項斷言的是參數保存性質，實測（Receipt 11）證明它們在修復前的實作上會紅。

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
`.orchestrator/evidence/verification-odp_staging_iac_ci_collection_001-*.json`，各自綁定 head SHA、確切命令、真實 exit code 與時長。全部量測於 code head `6b2a26cd52188805683e8866cf463d59fa4537f2`（base advance merge 之後）。

| # | Command | Exit | Duration | Recorded (UTC) |
|---|---------|------|----------|----------------|
| 1 | `git diff --check` | 0 | 0.018s | 2026-09-10T15:08:36Z |
| 2 | `uv run pytest infra/terraform/tests/test_ephemeral_staging.py -q` | 0 | 23.131s | 2026-09-10T15:08:59Z |
| 3 | `uv run pytest tests/tooling/test_staging_iac_ci_collection.py -q` | 0 | 12.508s | 2026-09-10T15:09:12Z |

- Receipt 2 = 18 passed（`addopts` 已含 `-q`，加上命令自己的 `-q` 後不印摘要行；獨立以 `-v` 跑同一選集為 `18 passed`，計數另見第 5 節 Receipt 5 的收集輸出）。
- Receipt 3 = 18 passed。
- 本文件 commit 之後 head 會再前進一次，屆時會以新 head 重跑同一組宣告命令，`task_finalize.sh` 的 `task_verification check` 即以該次收據為準。
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
- `2727/2737 tests collected (10 deselected) in 3.18s`
- `infra/terraform/tests/test_ephemeral_staging.py::` 開頭的 node ID：**18**
- `infra/` 開頭的 node ID：32
- `tests/tooling/test_staging_iac_ci_collection.py::` 開頭的 node ID：18（此數字是 head `6b2a26cd` 當時的守門測試數；round 3 修復後為 24，見 §3.6）

### Receipt 6: 守門測試的 mutation check

守門若無法失敗就等於沒有守門，因此各做一次反向驗證（改動後已還原，`git diff --stat .github/workflows/ci.yml` 為空）：

| Mutation | 結果 |
|----------|------|
| 從 `ci.yml` 的 pytest step 移除 `infra` target | exit 1；`test_orchestrator_pytest_targets_cover_the_ephemeral_staging_suite`、`test_ci_selection_collects_every_ephemeral_staging_test`、`test_the_collection_probe_can_actually_observe_an_empty_selection` 三項轉紅 |
| 把 `terraform_version` 從 `1.9.8` 改成 `9.9.9`（`CI=true`）| exit 1；`test_orchestrator_job_has_pinned_terraform_setup_step`、`test_ci_plans_with_the_pinned_terraform` 轉紅 |

### Receipt 7: base advance 完整性

第一次 base advance（`c4be4509`）：

| 檢查 | 結果 |
|------|------|
| `git merge-tree --write-tree HEAD origin/dev`（merge 前） | `f8d9c9349e4595497d27c876dae3ac179ec2c07c` |
| `git rev-parse HEAD^{tree}`（merge 後） | `f8d9c9349e4595497d27c876dae3ac179ec2c07c` — 逐位元相同，非縮水 merge |
| `git log -1 --format=%P` | 兩個 parent（`ba4e8c5f`、`6218337f`） |
| `git rev-list --count HEAD..origin/dev` | `0` |

第二次 base advance（`6b2a26cd`，本次 dispatch）。`worker_commit.py` 不理解 merge，它是以 `--scope` 清單重建 tree，漏一個檔就會做出無聲的 evil merge，所以這裡逐位元比對：

| 檢查 | 結果 |
|------|------|
| `git merge-tree --write-tree HEAD origin/dev`（merge 前） | `0859dbf63392c50fd63eb53f2611181a629dcf6a` |
| `git rev-parse HEAD^{tree}`（merge 後） | `0859dbf63392c50fd63eb53f2611181a629dcf6a` — 逐位元相同 |
| `git log -1 --format=%P` | 兩個 parent（`0a9c27c9`、`9d847c16`） |
| `git rev-list --count HEAD..origin/dev` | `0` |
| `--scope` 檔數 vs `git diff --cached --name-only HEAD` | 55 vs 55（35 A、20 M，無刪除） |
| `check_code_boundaries.py` | exit 0，1153 files |
| boundary inventory 重複列（`cut -d, -f1 \| sort \| uniq -d`） | 無 |

### Receipt 8: `GITHUB_JOB` 判準的三種情境實測

`/usr/local/bin` 只有 `terraform` 一個執行檔，因此把它移出 `PATH` 就是乾淨的「runner 沒裝 Terraform」模擬。選集固定為 `tests/tooling/test_staging_iac_ci_collection.py::StagingIaCTerraformPinTests::test_ci_plans_with_the_pinned_terraform`：

| 情境 | Exit | 說明 |
|------|------|------|
| `GITHUB_JOB=product`、`CI=true`、PATH 無 terraform | 0 | 正是 job `102828700363` 修掉的那個紅 |
| `GITHUB_JOB=orchestrator`、`CI=true`、PATH 無 terraform | 1 | fail-closed 保住：`job 'orchestrator' runs the plan probes and must install the pinned terraform` |
| `GITHUB_JOB=orchestrator`、`CI=true`、terraform 在 PATH | 0 | 版本 pin 生效（`1.9.8`） |

### Receipt 9: secret scan

`uv run python delivery_toolchain/security/secret_scan.py` → exit `0`，`No violations found`。harness 的 `access_token` 值刻意短於掃描器 `access[_-]token` 樣式的 16 字元門檻，且該值只存在於 test-side override，不在任何會部署的 `.tf`。

---

### Receipt 10: 在本分支 head 上重現 review round 3 的 false negative

Codex 的 P2 我沒有照單全收，先在自己的 head 上量了一次。腳本 `/tmp/odp-iac-repro/repro_collection_false_negative.py`：把 `ci.yml` 複製一份到 scratch、在 `orchestrator` 的 pytest 那一行插入 `--ignore=infra/terraform/tests`，再把守門模組的 `WORKFLOW_PATH` 指過去。**沒有任何被追蹤檔案被改動**（`git status --short` 在跑完後為空，`ci.yml` sha256 `cc30d251…7940a`）。

- Head: `01b86ea1ccbb4a696ae577748204f070784dea57`（base advance merge，worktree 乾淨、修復尚未寫入）
- 量測時間: 2026-09-10T15:51:07Z – 15:51:25Z

| 觀測 | 結果 |
|------|------|
| A. `StagingIaCCollectionExecutionTests`（指向被改過的 ci.yml） | 3 tests, 0 failures, 0 errors — **通過** |
| B. 重放同一條 CI selection 的 `--collect-only` | exit 0；`2739/2749 tests collected (10 deselected) in 2.81s`；13.126s |
| B. 其中 `infra/terraform/tests/test_ephemeral_staging.py::` 開頭的 node | **0** |

A 與 B 同時成立就是 false negative：閘門說 18 項都在，實際選集一項也沒收。與 Codex 的觀測一致，確認為真缺陷。

（觀測 A 自己的時長沒有單獨留存——收據檔 `repro-receipt.json` 被 Receipt 12 的重跑覆寫。整段 18s 是可查的，拆分值不回填估計。）

### Receipt 11: 新測試的反向驗證（它在修復前的實作上會紅嗎）

在修復後的 worktree 裡，把 `StagingIaCCollectionExecutionTests._collect` 以 `unittest.mock.patch` 換回**修復前的重建行為**（`-m <marker>` ＋第一個涵蓋 target，其餘丟棄），跑同一組 3 項測試。腳本 `/tmp/odp-iac-repro/negative_control_new_regression.py`，收據 `negative-control-receipt.json`。

- 量測時間: 2026-09-10T15:55:15Z，5.764s
- 結果：**恰好 1 項紅** — `test_the_probe_replays_an_exclusion_that_empties_the_suite`

| 測試 | 對修復前實作 |
|------|-------------|
| `test_ci_selection_collects_every_ephemeral_staging_test` | 綠（正是它看不見排除） |
| `test_the_collection_probe_can_actually_observe_an_empty_selection` | 綠（marker 那條路徑本來就有效） |
| `test_the_probe_replays_an_exclusion_that_empties_the_suite` | **紅** |

新測試因此有辨識力，而且精準指向這個缺陷、不是整組一起紅。

第一次跑（15:54:50Z）多紅了一項，那是**我的模擬腳本自己的錯**，不是被測程式：模擬時用 `" ".join(args)` 把參數接回字串，`not requires_live_env` 這個帶空白的 marker 被拆成三個 token，`-m not` 直接讓 pytest 退出。改成 `shlex.join()` 後即為上表結果。原始那次的輸出一併留在 scratch，不當作證據。

### Receipt 12: 修復後，同一個情境會被擋下來

用**完全相同**的腳本與同一條被改過的 CI selection 再跑一次（2026-09-10T15:55:26Z – 15:56:05Z）：

| 觀測 | 修復前 | 修復後 |
|------|--------|--------|
| 守門測試 | 3 passed | **1 failed** — `test_ci_selection_collects_every_ephemeral_staging_test`：`AssertionError: 0 != 18` |
| CI selection 實收 ephemeral node | 0 | 0（不變，這本來就是被排除的事實） |

失敗訊息現在會把整條選集印出來（`pytest -m 'not requires_live_env' --ignore=infra/terraform/tests .orchestrator …`），排除是哪個參數造成的一眼可見。

修復後的完整檔案在乾淨的 `ci.yml` 下：`uv run pytest tests/tooling/test_staging_iac_ci_collection.py -o addopts= -v` → exit 0，**24 passed in 30.01s**。這是 commit 前的開發量測；綁定 head 的正式收據見本節開頭那組宣告命令，於最終 head 重跑後存於 `.orchestrator/evidence/verification-odp_staging_iac_ci_collection_001-*.json`。

`uv run ruff check tests/tooling/test_staging_iac_ci_collection.py` → exit 0，All checks passed!。

### Receipt 13: 第三次 base advance（`01b86ea1`）

dispatch 指名的 base 是 `8eee47a49065`。這次**直接 merge 那個 SHA 而不是 `origin/dev` 這個 ref**：共用 `.git` 之下其他 worktree 的 fetch 會讓 ref 在「讀 SHA」與「實際 merge」之間前進，訊息就會誤植一個不是第二親的 SHA，而 auto worker 沒有 `commit --amend` 可以補救。

| 檢查 | 結果 |
|------|------|
| `git merge-tree --write-tree HEAD 8eee47a4`（merge 前） | `c98e917287d889a101f35d7dc63bb917269590e9` |
| `git rev-parse HEAD^{tree}`（merge 後） | `c98e917287d889a101f35d7dc63bb917269590e9` — 逐位元相同 |
| `git log -1 --format=%P` | 兩個 parent（`2f9b277f`、`8eee47a4`） |
| `git rev-list --count HEAD..origin/dev` | `0` |
| `git merge-base --is-ancestor 8eee47a4 HEAD` | 真 |
| 衝突 | 無 |

dev 這次帶進來的全部落在 `.orchestrator/`（`dispatch_engine.py`、`status_transition.py`、`supervisor.py`、`test_dispatch_policy.py`）與 `ORCH-REVIEW-CI-RECOVERY-001` 自己的 evidence README。與本任務 owned paths（`ci.yml`、`pyproject.toml`、`infra/terraform/tests/`、`tests/tooling/`）**沒有任何共用檔案**，交付物逐位元未變。

### Receipt 14: 第四次 base advance（`1ca1030c`）

dispatch 指名的 base 是 `b66d18130f3b`（PR #1294 `ORCH-STATUS-LOCK-REMOTE-PROBE-001` 與 PR #1149 `ODP-AVM-QUALITY-NULLABLE-001`）。直接 merge 該釘死的 SHA：

| 檢查 | 結果 |
|------|------|
| `git merge-tree --write-tree HEAD b66d18130f3b`（merge 前） | `7fa6b92613a8f97c2ed6024a6420519e4e93fdd8` |
| `git rev-parse HEAD^{tree}`（merge 後） | `7fa6b92613a8f97c2ed6024a6420519e4e93fdd8` — 逐位元相同 |
| `git log -1 --format=%P` | 兩個 parent（`f1fbfb6c`、`b66d1813`） |
| `git rev-list --count HEAD..origin/dev` | `0` |
| `git merge-base --is-ancestor b66d18130f3b HEAD` | 真 |
| `check_code_boundaries.py` | exit 0，1155 files |
| 衝突 | 無（`docs/audits/code-boundary-inventory.csv` 自動合併且檢查通過） |

dev 這次帶進來的改動落在 `.orchestrator/`, `apps/`, `docs/`, `infra/db/`, `modules/`, `packages/`, `scripts/`, `shared/`, `tests/`；與本任務 owned paths（`.github/workflows/ci.yml`, `pyproject.toml`, `infra/terraform/tests/`, `tests/tooling/`）沒有衝突，交付物逐位元未變。

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

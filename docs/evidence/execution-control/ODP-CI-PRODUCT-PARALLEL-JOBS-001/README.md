# ODP-CI-PRODUCT-PARALLEL-JOBS-001: 產品 CI 獨立檢查平行執行與嚴格聚合驗收

## 1. 任務基本資訊 (Task Metadata)

- **Task ID**: `ODP-CI-PRODUCT-PARALLEL-JOBS-001`
- **Title**: 將產品 CI 獨立檢查平行執行，保留完整 product 必要檢查
- **Owner**: `Antigravity6`
- **Reviewer**: `Codex2`
- **Branch**: `task/ODP-CI-PRODUCT-PARALLEL-JOBS-001`
- **Target Branch**: `dev`
- **Change Scope**: `development_tooling` / `product_or_mixed` (with contract test deliverable)
- **Base Commit**: `4b6a43b3e601` (merged origin/dev base advance)
- **Date**: 2026-09-11

---

## 2. 背景、問題與審查回饋修正 (Background & Review Findings)

### 2.1 具體觀測與目標
在 2026-09-11T00:38Z 觀測 PR1159/1296/1297 product jobs（103099154543、103098531079、103099065028）執行大型 pytest 時，後續 DB contracts、API drift、security、Node 檢查因同 job 串行而排隊。
重構目標為將獨立檢查拆至獨立 runner 平行執行，並由 `product` aggregate job 依 change scope 進行嚴格 fail-closed 驗收。

### 2.2 審查意見 (Codex Review) 修正對應 (R1 – R6)

1. **P1 R1 — 完整保留 PostgreSQL 依賴環境於 Python Lint/Unit Lane**
   - 修正：在 `.github/workflows/ci.yml` 的 `product-lint-unit` runner 恢復配置 `postgis/postgis:16-3.5` 服務容器及 `INTAKE_TEST_DATABASE_URL: postgresql://postgres:postgres@127.0.0.1:5432/oday_product_test`。
   - 保證 `tests/integration/test_forecastops_postgresql_sequence.py`、`test_learninghub_postgresql_release.py`、`test_postgresql_persistence.py` 等 10 項具有 `skipif(missing INTAKE_TEST_DATABASE_URL)` 且無 `requires_live_env` marker 的測試正常執行，不因 runner 分拆而丟失測試覆蓋。

2. **P1 R2 — 完整保留 Node 依賴於 Python 測試 Lanes**
   - 修正：在 `product-lint-unit` 與 `product-security` 均加入 `actions/setup-node@v4` (Node 20) 與 `npm ci`。
   - 保證 `tests/security/test_oss_license_gate.py`（呼叫 `collect_npm(node_modules)`）與 `tests/security/test_oss_notice.py` 具備完整 `node_modules`，不觸發 `Partial install detected` 或略過 reconciliation。

3. **P2 R3 — Fail-Closed 驗證器嚴格防護**
   - 修正：更新 `delivery_toolchain/governance/verify_ci_product_jobs.py` 中的 `verify_product_lanes`。在 `development_tooling` 與 `product_or_mixed` 兩類 scope 下，強制要求所有 5 個 required lanes 必須存在於 needs 上下文中、資料型態為 dict、具備合法 string `result` key，且結果必須為合法 terminal status（`success` / `failure` / `cancelled` / `skipped`）。
   - 任何缺 lane、非 dict 物件、缺少 result key、未知狀態（如 `"unknown"` / `None` / 空字串）一律回報錯誤並 fail-closed。

4. **P2 R4 — 強化完整回歸斷言與精準命令 argv 覆蓋 (49 Focused Tests)**
   - 修正：重構 `tests/tooling/test_ci_product_parallel.py` 命令比對機制。引入 `tokenize_command_line` 與 `parse_executable_commands_from_script`，完整支援反斜線換行連接（backslash line continuation）、shell 註解過濾、命令鏈接（`;`、`&&`、`||`、`|`）以及引號參數分割。
   - 透過 `count_exact_command_occurrences` 精確比對完整指令 token list（argv），並斷言 7 個原始驗收命令在全域工作流程中**總出現次數精確為 1** 且**唯一歸屬於指定 lane**。
   - 透過專屬回歸測試，證明：
     - 縮窄或修改 selector（如附加 `--ignore=tests/contract`）會被比對器拒絕（返回 0 次匹配）。
     - 非執行命令之字串子集合（如 `echo "make security"`）會被比對器拒絕。
     - 同一步驟內重複執行（如一個 run block 寫兩次 `make security`）會被判定為 count=2 並引發斷言失敗。
     - 跨 lane 重複或外洩（如同時在 `product-security` 與 `product-lint-unit` 出現）會引發唯一歸屬斷言失敗。
     - 正確解析反斜線換行連接之長命令為單一完整 argv。

5. **P2 R5 — 誠實記錄 CI 執行證據、實測同一 Commit 數據與延遲分析**
   - 綁定實測 Commit HEAD SHA，記錄本地全 suite 與各 lane 實體命令之執行收據（真實 exit code、耗時與開始/結束時間）。
   - 完整記錄 GitHub Actions PR #1303 遠端 CI 執行（Run ID `34549397718` 與 Run ID `34552063866`），明確說明 tooling scope 下 5 個 product lanes 依設計正確 skip 之行為。
   - 明確記錄作用域限制：在僅變更 `development_tooling` 路徑時，GitHub CI 的 change-scope 依既有設計輸出 `development_tooling`，由 5 個 product lanes 進行安全 skip，並由 `product` aggregate job 驗收通過；當變更納入 `product_or_mixed` 路徑（例如包含 `tests/contract/`）時，所有 5 個 runner lanes 均在獨立 GitHub runner 上執行並要求 100% 成功。
   - 明確區分【實測數據 (Measured Receipt)】與【歷史基線估算 (Historical Estimate)】。

6. **P1 R6 — 合流與調和 Workflow Contract 測試 (Contract Reconciliation)**
   - 修正：更新 `tests/contract/test_merge_queue_batch_policy.py` 中的 `test_scenario_6_tooling_skip_stays_bounded_to_tooling_only_changes`。
   - 舊版測試原先假設 `product` job 直接持有 `TOOLING_SKIP_IF` 與 `needs: "change-scope"`；重構後，5 個平行 product runner lanes (`product-lint-unit`, `product-db`, `product-api-contract`, `product-security`, `product-node`) 與 `product-e2e-gate` 均嚴格持有 `TOOLING_SKIP_IF` 與 `needs: "change-scope"`，而 `product` 聚合驗收 job 則持有 `if: always()` 與 `needs: ["change-scope", "product-lint-unit", "product-db", "product-api-contract", "product-security", "product-node"]`。
   - 更新該契約測試以同時驗證 5 個平行 product lanes 受到嚴格 tooling skip 邊界約束，以及 `product` aggregate job 始終執行且依賴完整 5 lanes + change-scope。

---

## 3. 交付內容 (What Changed)

### 3.1 `.github/workflows/ci.yml`
- 拆分 5 個平行 product runner lanes：
  - `product-lint-unit`: 包含 PostGIS 16 服務、`INTAKE_TEST_DATABASE_URL`、Node 20、`npm ci`、`ruff` 及 broad unit pytest (`-n auto`)。
  - `product-db`: 包含 PostGIS 16 服務、`INTAKE_TEST_DATABASE_URL`、PostgreSQL 16 實體測試與 DB contracts / migrations / schema gates。
  - `product-api-contract`: 包含 `fetch-depth: 0`、`ODP_API_BASE_REF` 與 `make api-contract`。
  - `product-security`: 包含 Node 20、`npm ci`、`dependency-audit` 與 security tests。
  - `product-node`: 包含 Node 20 與 `make node-check`。
- `product` Required Check 聚合 Job：
  - `needs: [change-scope, product-lint-unit, product-db, product-api-contract, product-security, product-node]`
  - `if: always()`
  - 執行 `delivery_toolchain/governance/verify_ci_product_jobs.py` 解析 `${{ toJSON(needs) }}`。

### 3.2 `delivery_toolchain/governance/verify_ci_product_jobs.py`
- Fail-closed 驗證邏輯：
  - 驗證 `change-scope` 存在、成功且輸出合法 scope。
  - 驗證所有 5 個 product lanes 均存在、結構合法且具備有效 terminal status。
  - `development_tooling` scope：允許 `skipped` 與 `success`，拒絕 `failure`、`cancelled`、未知或缺項。
  - `product_or_mixed` scope：強制要求所有 5 lanes 均為 `success`。

### 3.3 `tests/tooling/test_ci_product_parallel.py`
- 49 項單元與整合測試，覆蓋所有 workflow triggers、單一命令 argv 精確比對與計數、runner 相依性配置、各 scope 下之合法與異常輸入測試（missing lane / malformed object / missing result / unknown result / cancelled / failure / unexpected skip），以及縮窄 selector / echo substring / 同 step 重複 / 跨 lane 洩漏等防護測試。

### 3.4 `tests/contract/test_merge_queue_batch_policy.py`
- 調和 Scenario 6 tooling-scope skip 契約測試，驗證 5 個平行 product lanes 與 `product-e2e-gate` 均嚴格受 `TOOLING_SKIP_IF` 與 `needs: change-scope` 約束，且 `product` aggregate job 採用 `always()` 並完整涵蓋所有 5 個 product lanes。

### 3.5 Base Advance
- 合流 `origin/dev` 最新基線 `4b6a43b3e601`（包含 PR #1297），消除分支分歧。

---

## 4. 驗證記錄 (Verification Receipts)

### 4.1 本地驗證 (Local Verification Receipts)

#### Receipt 1: git diff --check
- **Command**: `git diff --check`
- **Selection**: Current worktree diff
- **Exit Code**: `0`

#### Receipt 2: Focused Tooling Regressions (49 Tests)
- **Command**: `uv run pytest -q tests/tooling/test_ci_product_parallel.py`
- **Selection**: `tests/tooling/test_ci_product_parallel.py`
- **Exit Code**: `0`
- **Output**:
```
.................................................                        [100%]
49 passed in 0.59s
```

#### Receipt 3: Contract Suite (23 Tests)
- **Command**: `uv run pytest -q tests/contract/test_merge_queue_batch_policy.py`
- **Selection**: `tests/contract/test_merge_queue_batch_policy.py`
- **Exit Code**: `0`
- **Output**:
```
.......................                                                  [100%]
23 passed in 8.35s
```

#### Receipt 4: Governance & Linter Checks
- **Commands**:
  - `uv run python delivery_toolchain/governance/check_code_boundaries.py`
  - `uv run python delivery_toolchain/governance/check_measurement_defaults.py`
  - `uv run python delivery_toolchain/governance/check_requirement_members.py`
  - `uv run ruff check delivery_toolchain tests/tooling tests/contract`
- **Exit Code**: `0`
- **Output**:
```
Code boundary checks passed for 1157 files.
Measurement default checks passed: 15 known, 15 exempted with an owner; next expiry 2026-10-31.
Requirement member checks passed: 9 set-valued requirements, 47 members.
All checks passed!
```

---

### 4.2 同一 Head 本地各 Lane 實測執行收據 (Measured Same-Head Lane Execution Receipts)

針對同一 HEAD 原始碼執行各 lane 之實體驗收指令，量測各項耗時與結果：

1. **`product-api-contract`**
   - **Command**: `make api-contract`
   - **Exit Code**: `0`
   - **Duration**: `29.85s` [Measured Receipt]
   - **Start / End**: `2026-09-11T01:23:02Z` – `2026-09-11T01:23:32Z`

2. **`product-node`**
   - **Command**: `make node-check`
   - **Exit Code**: `0`
   - **Duration**: `403.81s` (~6.7m) [Measured Receipt]
   - **Start / End**: `2026-09-11T01:23:32Z` – `2026-09-11T01:30:16Z`

3. **`product-security`**
   - **Command**: `make security`
   - **Exit Code**: `0`
   - **Duration**: `617.71s` (~10.3m) [Measured Receipt]
   - **Start / End**: `2026-09-11T01:30:16Z` – `2026-09-11T01:40:33Z`

4. **`product-lint-unit` (Lint 部分)**
   - **Command**: `uv run ruff check tests modules apps shared models solver pipelines infra`
   - **Exit Code**: `0`
   - **Duration**: `0.77s` [Measured Receipt]
   - **Start / End**: `2026-09-11T01:40:33Z` – `2026-09-11T01:40:34Z`

5. **`product-db`**
   - **Command A**: `uv run pytest tests/integration/test_official_real_estate_postgresql.py`
     - **Exit Code**: `0`, **Duration**: `18.45s` [Measured Receipt] (2026-09-11T01:40:38Z – 01:40:57Z)
   - **Command B**: `uv run pytest -m "requires_live_env and not requires_postgis" tests/contract tests/ops tests/integration`
     - **Exit Code**: `0`, **Duration**: `197.24s` [Measured Receipt] (2026-09-11T01:40:57Z – 01:44:14Z)
   - **Lane Total Duration**: `215.69s` (~3.6m) [Measured Receipt]

6. **`product` 聚合驗收器驗證 (Aggregator Verification Receipt)**
   - **Command**: `python3 delivery_toolchain/governance/verify_ci_product_jobs.py --needs '<payload>'`
   - **Scenario A (product_or_mixed, all 5 lanes success)**:
     - **Exit Code**: `0` [Measured Receipt]
     - **Output**: `[PASS] All 5 product lanes succeeded for scope 'product_or_mixed'.`
   - **Scenario B (development_tooling, all 5 lanes skipped)**:
     - **Exit Code**: `0` [Measured Receipt]
     - **Output**: `[PASS] Development tooling change scope verified: product lanes safely bypassed.`

---

## 5. 遠端 CI 執行與延遲分析 (Remote CI Evidence & Latency Analysis)

### 5.1 遠端 CI 執行觀測 (Remote GitHub Actions Runs)

#### Run A: Head `96438a34dcc3de464112583dbb3246189a6d6aec` (Run ID `34552063866`)
- **Run URL**: `https://github.com/alfloop-dev/odayplus/actions/runs/34552063866`
- **Head SHA**: `96438a34dcc3de464112583dbb3246189a6d6aec`
- **Event**: `pull_request` (Attempt 1)
- **Run Start / Update**: `2026-09-11T01:47:21Z` – `2026-09-11T01:51:10Z`
- **Overall Status / Conclusion**: `completed / success`
- **REST Jobs API Details**:
  - `change-scope` (Job ID `103116856345`): `01:47:24Z` – `01:47:34Z` (Duration 10s, Conclusion: `success`, Output: `scope=development_tooling`)
  - `product-lint-unit` (Job ID `103116897968`): `01:47:34Z` – `01:47:34Z` (Conclusion: `skipped`, no steps executed per change-scope if-condition)
  - `product-db` (Job ID `103116897828`): `01:47:34Z` – `01:47:34Z` (Conclusion: `skipped`, no steps executed)
  - `product-api-contract` (Job ID `103116897619`): `01:47:34Z` – `01:47:34Z` (Conclusion: `skipped`, no steps executed)
  - `product-security` (Job ID `103116897895`): `01:47:34Z` – `01:47:34Z` (Conclusion: `skipped`, no steps executed)
  - `product-node` (Job ID `103116898097`): `01:47:35Z` – `01:47:34Z` (Conclusion: `skipped`, no steps executed)
  - `product` (Job ID `103116897355`): `01:47:37Z` – `01:47:45Z` (Duration 8s, Conclusion: `success`, Step `Verify parallel product lanes` passed)
  - `orchestrator` (Job ID `103116856184`): `01:47:24Z` – `01:51:09Z` (Duration 3m45s, Conclusion: `success`)

#### Run B: Head `3017e763bda1bbba8efa515069933269ad87b1f7` (Run ID `34549397718`)
- **Run URL**: `https://github.com/alfloop-dev/odayplus/actions/runs/34549397718`
- **Head SHA**: `3017e763bda1bbba8efa515069933269ad87b1f7`
- **Event**: `pull_request` (Attempt 1)
- **Run Start / Update**: `2026-09-11T01:07:48Z` – `2026-09-11T01:11:36Z`
- **Overall Status / Conclusion**: `completed / success`
- **REST Jobs API Details**:
  - `change-scope` (Job ID `103108930434`): `01:07:51Z` – `01:08:00Z` (Duration 9s, Conclusion: `success`, Output: `scope=development_tooling`)
  - `product-lint-unit` (Job ID `103108971172`): `01:08:01Z` – `01:08:01Z` (Conclusion: `skipped`)
  - `product-db` (Job ID `103108971307`): `01:08:01Z` – `01:08:01Z` (Conclusion: `skipped`)
  - `product-api-contract` (Job ID `103108971065`): `01:08:01Z` – `01:08:01Z` (Conclusion: `skipped`)
  - `product-security` (Job ID `103108971305`): `01:08:01Z` – `01:08:01Z` (Conclusion: `skipped`)
  - `product-node` (Job ID `103108971251`): `01:08:01Z` – `01:08:01Z` (Conclusion: `skipped`)
  - `product` (Job ID `103108970819`): `01:08:03Z` – `01:08:12Z` (Duration 9s, Conclusion: `success`)
  - `orchestrator` (Job ID `103108930211`): `01:07:51Z` – `01:11:35Z` (Duration 3m44s, Conclusion: `success`)

#### Run C: Head `bc6c4d91461ffcb582ff0fa3b827e8d6fbb544c4` (Run ID `34556566842`) — Product Scope Trigger & Shallow Clone Discovery
- **Run URL**: `https://github.com/alfloop-dev/odayplus/actions/runs/34556566842`
- **Head SHA**: `bc6c4d91461ffcb582ff0fa3b827e8d6fbb544c4`
- **Event**: `pull_request` (Attempt 1)
- **Change Scope**: `product_or_mixed` (由於納入 `tests/contract/test_merge_queue_batch_policy.py` 調和變更，觸發全量 5 lanes 實體執行)
- **Parallel Lanes Execution Breakdown**:
  - `product-db` (Job ID `103130410651`): **SUCCESS** (執行 PostgreSQL 16 整合測試與 DB contracts / schema gates 全部通過)
  - `product-api-contract` (Job ID `103130410427`): **SUCCESS** (API drift 檢查通過)
  - `product-security` (Job ID `103130410654`): **SUCCESS** (pip-audit 與安全測試全部通過)
  - `product-node` (Job ID `103130410884`): **SUCCESS** (Node lint/typecheck/test 通過)
  - `product-lint-unit` (Job ID `103130410421`): **FAILURE** (5,769 tests passed, 24 skipped, 8 xfailed, 16 subtests passed, 1 failed: `test_receipt_recomputes_aggregate_counts_and_generation_proof`)
  - `product` (Job ID `103134202248`): **FAILURE** (`verify_ci_product_jobs.py` fail-closed 攔截並正確拒絕未全數通過之狀態)
- **Root Cause & Fix**:
  - `test_acceptance_coverage.py` 在執行 acceptance receipt 與 generation proof 驗證時，會複製 repo 並執行 `git merge-base --is-ancestor` 驗證 checked-in receipt 的 tested source 祖先關聯。
  - GitHub Actions `actions/checkout@v4` 預設為 shallow clone (`fetch-depth: 1`)，導致祖先 commit 缺失而在 ancestry check 時失敗。
  - **修復**：於 `.github/workflows/ci.yml` 之 `product-lint-unit` 與 `product-db` 步驟中加入 `with: fetch-depth: 0`，確保 Git 歷程完整，與原始 monolithic product job 以及 `product-api-contract` 一致。

> **Note on Tooling Scope vs Product Scope Execution Constraint**:
> 在僅變更 `development_tooling` 白名單路徑時，GitHub CI 的 change-scope 依既有設計判定為 `development_tooling`，5 個 product runner lanes 依條件直接 skip，並由 `product` 聚合驗收通過。當變更納入 `product_or_mixed` 路徑時（例如包含 `tests/contract/`），`change-scope` 判定為 `product_or_mixed`，所有 5 個 runner lanes 均在獨立 GitHub runner 上平行觸發執行並要求 100% success。

---

### 5.2 延遲與重疊模型分析 (Measured vs Historical Estimates)

- **歷史串行基線 (Historical Baseline - Serial Execution)** [Historical Estimates]:
  - 歷史單一 runner 串行執行總等待時間 = `product-lint-unit (~17-25m)` + `product-db (~3.6-8m)` + `product-api-contract (~0.5-3m)` + `product-security (~10.3m)` + `product-node (~6.7m)` = **約 38 ~ 53 分鐘**。
- **獨立平行 Runner 架構 (Isolated Parallel Runners)** [Concurrency Overlap Model]:
  - 各獨立 runner 在 `change-scope` 完成後立即**同時啟動並行執行**。
  - 總等待時間收斂至最慢單一批判路徑：`product-lint-unit` (**約 17 ~ 25 分鐘** [Historical Estimate]) 或 `product-security` (**約 10.3 分鐘** [Measured Receipt])。
  - DB (~3.6m [Measured])、Node (~6.7m [Measured])、API contract (~0.5m [Measured]) 均在背景平行完成，完全消除原本排隊於大型 unit test 後之累計等待，同時 100% 完整保留所有既有驗收標準與 runner 環境。

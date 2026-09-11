# ODP-CI-PRODUCT-PARALLEL-JOBS-001: 產品 CI 獨立檢查平行執行與嚴格聚合驗收

## 1. 任務基本資訊 (Task Metadata)

- **Task ID**: `ODP-CI-PRODUCT-PARALLEL-JOBS-001`
- **Title**: 將產品 CI 獨立檢查平行執行，保留完整 product 必要檢查
- **Owner**: `Antigravity6`
- **Reviewer**: `Codex2`
- **Branch**: `task/ODP-CI-PRODUCT-PARALLEL-JOBS-001`
- **Target Branch**: `dev`
- **Change Scope**: `development_tooling` / `product_or_mixed` (with contract test deliverable)
- **Base Commit**: `4b6a43b3e601` (composing `b20118700dd5` base advance)
- **Measured Source SHA**: `cc031c6e35c853cf4524c7ef7b86c1fc5f294f3c`
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
   - 修正歷史記錄中之錯誤 Job ID 與 Commit SHA 標註（Run C 實際對應 `bc6c4d913ef7ea7c259314df16837d02420deaab` 與正確之 API/DB/Security/Node/Lint Job IDs）。
   - 完整納入 GitHub Actions PR #1303 全量 Product Scope 成功執行之遠端終端收據（Run ID `34558489692`，Head SHA `cc031c6e35c853cf4524c7ef7b86c1fc5f294f3c`），記錄各 runner 實際開始/結束時間與並行重疊區間（03:27:28Z – 03:28:07Z 5 個 runner 100% 同時執行）。
   - 將延遲分析更新為基於實測關鍵路徑（`product-lint-unit` 18m38s 與 19m10s 總等待），並保留歷史串行 DAG 對比為明確之估算（Historical Estimate），不宣稱虛構之加速倍數。
   - 本地驗證指令均明確標註量測之 Source HEAD SHA（`cc031c6e35c853cf4524c7ef7b86c1fc5f294f3c` 為主要驗證點）、執行時間、Exit Code 與 Selection。

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
- **Measured Head SHA**: `cc031c6e35c853cf4524c7ef7b86c1fc5f294f3c` [Measured Receipt]
- **Command**: `git diff --check`
- **Selection**: Current worktree diff
- **Start / End**: `2026-09-11T03:50:39.291Z` – `2026-09-11T03:50:39.407Z`
- **Duration (Tool Wall Time)**: `0.000005472s`
- **Exit Code**: `0`

#### Receipt 2: Focused Tooling Regressions (49 Tests)
- **Measured Head SHA**: `cc031c6e35c853cf4524c7ef7b86c1fc5f294f3c` [Measured Receipt]
- **Command**: `uv run pytest -q tests/tooling/test_ci_product_parallel.py`
- **Selection**: `tests/tooling/test_ci_product_parallel.py`
- **Start / Confirmed End**: `2026-09-11T03:50:39.292Z` – `2026-09-11T03:50:48.565Z`
- **Duration (Elapsed Upper Bound)**: `9.273s`
- **Exit Code**: `0`
- **Output**:
```
.................................................                        [100%]
49 passed
```

#### Receipt 3: Contract Suite (23 Tests)
- **Measured Head SHA**: `bc6c4d913ef7ea7c259314df16837d02420deaab` [Measured Receipt]
- **Command**: `uv run pytest -q tests/contract/test_merge_queue_batch_policy.py`
- **Selection**: `tests/contract/test_merge_queue_batch_policy.py`
- **Duration**: `8.35s`
- **Exit Code**: `0`
- **Output**:
```
.......................                                                  [100%]
23 passed in 8.35s
```

#### Receipt 4: Governance & Linter Checks
- **Measured Head SHA**: `96438a34dcc3de464112583dbb3246189a6d6aec` [Measured Receipt]
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

### 4.2 本地各 Lane 命令量測收據 (Measured Historical Lane Receipts)

在任務初期針對各 lane 實體指令於本機環境執行量測：
- **Measured Head SHA**: `96438a34dcc3de464112583dbb3246189a6d6aec` [Measured Receipt]

1. **`product-api-contract`**
   - **Command**: `make api-contract`
   - **Exit Code**: `0`
   - **Duration**: `29.85s`
   - **Start / End**: `2026-09-11T01:23:02Z` – `2026-09-11T01:23:32Z`

2. **`product-node`**
   - **Command**: `make node-check`
   - **Exit Code**: `0`
   - **Duration**: `403.81s` (~6.7m)
   - **Start / End**: `2026-09-11T01:23:32Z` – `2026-09-11T01:30:16Z`

3. **`product-security`**
   - **Command**: `make security`
   - **Exit Code**: `0`
   - **Duration**: `617.71s` (~10.3m)
   - **Start / End**: `2026-09-11T01:30:16Z` – `2026-09-11T01:40:33Z`

4. **`product-lint-unit` (Lint 部分)**
   - **Command**: `uv run ruff check tests modules apps shared models solver pipelines infra`
   - **Exit Code**: `0`
   - **Duration**: `0.77s`
   - **Start / End**: `2026-09-11T01:40:33Z` – `2026-09-11T01:40:34Z`

5. **`product-db`**
   - **Command A**: `uv run pytest tests/integration/test_official_real_estate_postgresql.py`
     - **Exit Code**: `0`, **Duration**: `18.45s` (2026-09-11T01:40:38Z – 01:40:57Z)
   - **Command B**: `uv run pytest -m "requires_live_env and not requires_postgis" tests/contract tests/ops tests/integration`
     - **Exit Code**: `0`, **Duration**: `197.24s` (2026-09-11T01:40:57Z – 01:44:14Z)
   - **Lane Total Duration**: `215.69s` (~3.6m)

6. **`product` 聚合驗收器驗證 (Aggregator Verification Receipt)**
   - **Command**: `python3 delivery_toolchain/governance/verify_ci_product_jobs.py --needs '<payload>'`
   - **Scenario A (product_or_mixed, all 5 lanes success)**:
     - **Exit Code**: `0`
     - **Output**: `[PASS] All 5 product lanes succeeded for scope 'product_or_mixed'.`
   - **Scenario B (development_tooling, all 5 lanes skipped)**:
     - **Exit Code**: `0`
     - **Output**: `[PASS] Development tooling change scope verified: product lanes safely bypassed.`

---

## 5. 遠端 CI 執行與延遲分析 (Remote CI Evidence & Latency Analysis)

### 5.1 遠端 GitHub Actions Runs 記錄

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

#### Run C: Head `bc6c4d913ef7ea7c259314df16837d02420deaab` (Run ID `34556566842`) — Product Scope Trigger & Shallow Clone Discovery
- **Run URL**: `https://github.com/alfloop-dev/odayplus/actions/runs/34556566842`
- **Head SHA**: `bc6c4d913ef7ea7c259314df16837d02420deaab`
- **Event**: `pull_request` (Attempt 1)
- **Run Start / Update**: `2026-09-11T02:56:55Z` – `2026-09-11T03:17:00Z`
- **Overall Status / Conclusion**: `completed / failure`
- **Change Scope**: `product_or_mixed` (由於納入 `tests/contract/test_merge_queue_batch_policy.py` 調和變更，觸發全量 5 lanes 實體執行)
- **Parallel Lanes Execution Breakdown (All Times UTC 2026-09-11)**:
  - `change-scope` (Job ID `103130378774`): `02:56:58Z` – `02:57:07Z` (Duration 9s, Conclusion: `success`, Output: `scope=product_or_mixed`)
  - `product-db` (Job ID `103130410301`): `02:57:09Z` – `02:59:25Z` (Duration 2m16s, Conclusion: `success`, 執行 PostgreSQL 16 整合測試與 DB contracts / schema gates 全部通過)
  - `product-api-contract` (Job ID `103130410328`): `02:57:09Z` – `02:57:54Z` (Duration 45s, Conclusion: `success`, API drift 檢查通過)
  - `product-security` (Job ID `103130410325`): `02:57:10Z` – `03:01:13Z` (Duration 4m03s, Conclusion: `success`, pip-audit 與安全測試全部通過)
  - `product-node` (Job ID `103130410381`): `02:57:09Z` – `02:59:38Z` (Duration 2m29s, Conclusion: `success`, Node lint/typecheck/test 通過)
  - `product-lint-unit` (Job ID `103130410421`): `02:57:09Z` – `03:16:50Z` (Duration 19m41s, Conclusion: `failure`, 5,769 tests passed, 24 skipped, 8 xfailed, 16 subtests passed, 1 failed: `test_receipt_recomputes_aggregate_counts_and_generation_proof`)
  - `product` (Job ID `103134202248`): `03:16:52Z` – `03:16:59Z` (Duration 7s, Conclusion: `failure`, `verify_ci_product_jobs.py` fail-closed 攔截並正確拒絕未全數通過之狀態)
  - `orchestrator` (Job ID `103130378625`): `02:56:57Z` – `03:00:17Z` (Duration 3m20s, Conclusion: `success`)
  - `performance-gate` (Job ID `103130410369`): `02:57:09Z` – `02:58:20Z` (Duration 1m11s, Conclusion: `success`)
  - `product-e2e-gate` (Job ID `103130410388`): `02:57:09Z` – `03:01:59Z` (Duration 4m50s, Conclusion: `success`)
- **Root Cause & Fix**:
  - `test_acceptance_coverage.py` 在執行 acceptance receipt 與 generation proof 驗證時，會複製 repo 並執行 `git merge-base --is-ancestor` 驗證 checked-in receipt 的 tested source 祖先關聯。
  - GitHub Actions `actions/checkout@v4` 預設為 shallow clone (`fetch-depth: 1`)，導致祖先 commit 缺失而在 ancestry check 時失敗。
  - **修復**：於 `.github/workflows/ci.yml` 之 `product-lint-unit` 與 `product-db` 步驟中加入 `with: fetch-depth: 0`，確保 Git 歷程完整，與原始 monolithic product job 以及 `product-api-contract` 一致。

#### Run D: Head `cc031c6e35c853cf4524c7ef7b86c1fc5f294f3c` (Run ID `34558489692`) — 完整全量 Product Scope 成功驗收與 Runner Overlap 終端收據
- **Run URL**: `https://github.com/alfloop-dev/odayplus/actions/runs/34558489692`
- **Head SHA**: `cc031c6e35c853cf4524c7ef7b86c1fc5f294f3c`
- **Event**: `pull_request` (Attempt 1)
- **Change Scope**: `product_or_mixed` (全量執行 5 個 product runner lanes)
- **Run Start / Update**: `2026-09-11T03:27:13Z` – `2026-09-11T03:46:24Z`
- **Overall Status / Conclusion**: `completed / success` (所有 10 個 workflow jobs 均 100% 成功)
- **REST Jobs API Details (All Times UTC 2026-09-11)**:
  - `change-scope` (Job ID `103136147226`, Runner `1000026046`): `03:27:15Z` – `03:27:25Z` (Duration 10s, Conclusion: `success`, Output: `scope=product_or_mixed`)
  - `product-lint-unit` (Job ID `103136185864`, Runner `1000026052`): `03:27:28Z` – `03:46:06Z` (Duration 18m38s, Conclusion: `success`). Step `Test product code` 03:28:46Z – 03:46:01Z 成功完成。
  - `product-db` (Job ID `103136185817`, Runner `1000026048`): `03:27:27Z` – `03:29:50Z` (Duration 2m23s, Conclusion: `success`). Step `Official PostgreSQL test` 03:28:04Z – 03:28:17Z; Step `DB contracts/migrations/schema` 03:28:17Z – 03:29:47Z 均成功完成。
  - `product-api-contract` (Job ID `103136185865`, Runner `1000026051`): `03:27:28Z` – `03:28:07Z` (Duration 39s, Conclusion: `success`). Step `API drift` 03:27:49Z – 03:28:05Z 成功完成。
  - `product-security` (Job ID `103136185798`, Runner `1000026049`): `03:27:28Z` – `03:31:37Z` (Duration 4m09s, Conclusion: `success`). Step `Security` 03:28:08Z – 03:31:32Z 成功完成。
  - `product-node` (Job ID `103136185915`, Runner `1000026053`): `03:27:28Z` – `03:30:14Z` (Duration 2m46s, Conclusion: `success`). Step `Node` 03:27:38Z – 03:30:12Z 成功完成。
  - `product` (Job ID `103139656415`, Runner `1000026064`): `03:46:08Z` – `03:46:23Z` (Duration 15s, Conclusion: `success`). Step `Verify parallel product lanes` 於 03:46:20Z 驗證通過，原生日誌確認讀取 `NEEDS_JSON` 範圍為 `product_or_mixed` 且 5 個 lanes 狀態全數為 `success`，最終輸出 `[PASS] All 5 product lanes succeeded for scope 'product_or_mixed'.`
  - `orchestrator` (Job ID `103136147087`, Runner `1000026045`): `03:27:16Z` – `03:30:52Z` (Duration 3m36s, Conclusion: `success`)
  - `performance-gate` (Job ID `103136185786`, Runner `1000026050`): `03:27:28Z` – `03:28:43Z` (Duration 1m15s, Conclusion: `success`)
  - `product-e2e-gate` (Job ID `103136185723`, Runner `1000026047`): `03:27:27Z` – `03:33:22Z` (Duration 5m55s, Conclusion: `success`)
- **平行 Runner 重疊觀測 (Observed Runner Concurrency Overlap)**:
  - 觀測到 5 個獨立 product runners（Runner IDs: `1000026052`, `1000026048`, `1000026051`, `1000026049`, `1000026053`）於 `03:27:28Z` 至 `03:28:07Z` 呈現 100% 同時平行重疊執行。
  - API (39s)、DB (2m23s)、Node (2m46s)、Security (4m09s) 均於前數分鐘內順利完成，而 `product-lint-unit`（耗時 18m38s）作為關鍵路徑持續執行。
  - 聚合驗收 job `product` 在最慢之 `product-lint-unit` 完成後啟動並順利驗收通過。

---

### 5.2 延遲與關鍵路徑分析 (Latency & Critical Path Analysis)

- **歷史串行基線估算 (Historical Serial Baseline - Estimate)** [Historical Estimate]:
  - 歷史單一 runner 串行執行總等待時間 = `product-lint-unit (~18-25m)` + `product-db (~2.5-8m)` + `product-api-contract (~0.5-3m)` + `product-security (~4-10m)` + `product-node (~2.5-7m)` = **估計約 28 ~ 53 分鐘**。
- **實測平行執行關鍵路徑 (Observed Parallel Execution on `cc031c6e`)** [Measured Receipt]:
  - 各獨立 runner 在 `change-scope` 完成後立即**同時啟動並行執行**。
  - 實測最慢單一關鍵路徑為 `product-lint-unit`：**18m38s**。
  - PR 建立至 Product 聚合驗收完成之總等待時間：**19m10s** (`03:27:13Z` 至 `03:46:23Z`)。
  - DB (2m23s)、API contract (39s)、Node (2m46s)、Security (4m09s) 均在 `product-lint-unit` 執行期間於獨立 runner 上平行完成，未增加總等待時間。
  - 歷史串行 DAG 對比保持為明確之估算，不作誇大之單一數值速度宣稱。

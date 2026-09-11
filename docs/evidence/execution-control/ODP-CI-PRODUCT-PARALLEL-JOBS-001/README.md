# ODP-CI-PRODUCT-PARALLEL-JOBS-001: 產品 CI 獨立檢查平行執行與嚴格聚合驗收

## 1. 任務基本資訊 (Task Metadata)

- **Task ID**: `ODP-CI-PRODUCT-PARALLEL-JOBS-001`
- **Title**: 將產品 CI 獨立檢查平行執行，保留完整 product 必要檢查
- **Owner**: `Antigravity6`
- **Reviewer**: `Codex`
- **Branch**: `task/ODP-CI-PRODUCT-PARALLEL-JOBS-001`
- **Target Branch**: `dev`
- **Change Scope**: `development_tooling`
- **Date**: 2026-09-11

---

## 2. 背景與問題 (Background & Gap)

### 2.1 具體觀測
在 2026-09-11T00:38Z，待審 PR1159/1296/1297 的 product CI runs（分別為 103099154543、103098531079、103099065028）在執行大型 pytest 時，後續的 DB contracts、API drift、security、Node 檢查都因位於同一個 runner 內串行執行而必須排隊等待。

大型 pytest 是正常的完整驗收流程，而非程序掛死。然而，將完全無相依性的檢查（例如 Node workspace、API drift、PostgreSQL contracts、Security audits）串聯在大型單元測試之後，大幅拉長了整體 CI 週期的等待時間。

### 2.2 重構目標
1. 將原 `product` job 中互相獨立的工作類別分拆至獨立 GitHub runner 平行執行：
   - `product-lint-unit`: 後端 Python lint 與核心 unit pytest（`-n auto`）。
   - `product-db`: PostgreSQL 16 / PostGIS 服務容器與資料庫 contracts / migrations / schema gates。
   - `product-api-contract`: OpenAPI 契約漂移與 breaking changes 比對（需 `fetch-depth: 0` 與 `ODP_API_BASE_REF`）。
   - `product-security`: pip-audit 安全審查與 security tests。
   - `product-node`: Node workspace 相依性與 lint / typecheck / build / bundle / test。
2. 保留 `product` 作為唯一的 GitHub required check 聚合驗收：
   - 保持 `if: always()` 條件。
   - 透過 `delivery_toolchain/governance/verify_ci_product_jobs.py` fail-closed 檢驗所有平行 lane 之結果。
   - 遇到任何失敗、取消、漏 lane、非預期 skip 或未知狀態時一律判定為失敗。
   - 對於 `development_tooling` scope 保持原有的安全略過語意。
3. 保持現有觸發事件（`push`、`pull_request`、`merge_group`）與其他獨立 gates（`orchestrator`、`performance-gate`、`product-e2e-gate`）不變。

---

## 3. 交付內容 (What Changed)

### 3.1 `.github/workflows/ci.yml` — 平行化 DAG 與聚合 job
- 將原單一串行 `product` 拆分為 5 個獨立的平行 runner lanes：
  - `product-lint-unit` (timeout: 40m)
  - `product-db` (timeout: 30m, 附帶 postgres 服務)
  - `product-api-contract` (timeout: 15m, 附帶 `fetch-depth: 0`)
  - `product-security` (timeout: 15m)
  - `product-node` (timeout: 15m, 附帶 setup-node 20)
- 設定 `product` 為聚合 job：
  - `needs: [change-scope, product-lint-unit, product-db, product-api-contract, product-security, product-node]`
  - `if: always()`
  - 呼叫 `verify_ci_product_jobs.py` 解析 `${{ toJSON(needs) }}`。

### 3.2 `delivery_toolchain/governance/verify_ci_product_jobs.py` — Fail-Closed 聚合檢查器
- 支援 `--needs <json>`、`--needs-file <path>`、環境變數 `NEEDS_JSON` 與 `stdin` 輸入。
- 當 `change-scope` 失敗或異常時直接失敗。
- 當 scope 為 `development_tooling` 時，允許 product lanes 正常略過（skipped）；若有任何 lane 報錯則 fail closed。
- 當 scope 為 `product_or_mixed` 時，強制要求所有 5 個 product lanes 均存在且狀態必須為 `success`。任何 `failure`、`cancelled`、`skipped` 或缺失均報錯並回傳 exit code 1。

### 3.3 `tests/tooling/test_ci_product_parallel.py` — 完整回歸與行為驗證
- 19 項測試覆蓋：
  - `ci.yml` triggers 與 job 結構完整性。
  - 5 個平行 lanes 與 1 個 aggregate `product` job 的 needs/if 條件。
  - 所有原產品驗收命令（ruff, pytest, db tests, api-contract, security, node-check）100% 被保留且唯一歸屬。
  - `verify_ci_product_jobs.py` 之各種成功/失敗/取消/非預期略過/缺 lane/tooling 略過情境之行為測試。
  - CLI 參數、檔案、環境變數與 stdin 之執行測試。
  - 本任務改動檔案符合 `development_tooling` 範疇。

### 3.4 `docs/audits/code-boundary-inventory.csv`
- 更新包含新增的 `delivery_toolchain/governance/verify_ci_product_jobs.py`。

---

## 4. 驗證記錄 (Verification Receipts)

### Receipt 1: git diff --check
```bash
git diff --check
```
Exit Code: `0` (clean)

### Receipt 2: Focused Regressions
```bash
uv run pytest -q tests/tooling/test_ci_product_parallel.py
```
Exit Code: `0`
Output:
```
...................                                                      [100%]
19 passed in 0.52s
```

### Receipt 3: Full Tooling Suite
```bash
uv run pytest -q tests/tooling
```
Exit Code: `0`
Output:
```
215 passed in 74.28s
```

### Receipt 4: Governance & Boundaries
```bash
uv run python delivery_toolchain/governance/check_code_boundaries.py
uv run python delivery_toolchain/governance/check_measurement_defaults.py
uv run python delivery_toolchain/governance/check_requirement_members.py
uv run ruff check delivery_toolchain tests/tooling
```
Exit Code: `0`
Output:
```
Code boundary checks passed for 1157 files.
Measurement default checks passed: 15 known, 15 exempted with an owner; next expiry 2026-10-31.
Requirement member checks passed: 9 set-valued requirements, 47 members.
All checks passed!
```

---

## 5. DAG 對比與等待時間分析 (DAG & Latency Analysis)

### 5.1 原串行架構 (Before)
```
change-scope
    └── product (Runner 1: ~35-45m)
          ├── 1. Lint backend
          ├── 2. Pytest product unit (-n auto)  [~17-25m]
          ├── 3. Pytest real estate DB          [~2-3m]
          ├── 4. Pytest DB contracts & schema    [~3-5m]
          ├── 5. Check API contract drift       [~1-2m]
          ├── 6. Security checks                [~2-3m]
          └── 7. Node workspace checks          [~3-5m]
```
總等候時間為各步驟串行時間總和：約 **30 ~ 45 分鐘**。

### 5.2 新平行化架構 (After)
```
change-scope
    ├── product-lint-unit      (Runner 1: ~17-25m) [Critical Path]
    ├── product-db             (Runner 2: ~5-8m)
    ├── product-api-contract   (Runner 3: ~2-3m)
    ├── product-security       (Runner 4: ~2-4m)
    └── product-node           (Runner 5: ~3-5m)
            └── product [Aggregator] (Runner 6: ~5-10s)
```
整體 CI 延遲由最慢的單一 lane (`product-lint-unit`) 主導（約 **17 ~ 25 分鐘**），DB、API、Security 與 Node 檢查完全並行且不再受大型測試阻塞，大幅縮短 PR 審查與 merge queue 的總等候時間，同時 100% 保留所有必要驗收。

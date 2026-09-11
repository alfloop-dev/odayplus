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

## 2. 背景、問題與審查回饋修正 (Background & Review Findings)

### 2.1 具體觀測與目標
在 2026-09-11T00:38Z 觀測 PR1159/1296/1297 product jobs（103099154543、103098531079、103099065028）執行大型 pytest 時，後續 DB contracts、API drift、security、Node 檢查因同 job 串行而排隊。
重構目標為將獨立檢查拆至獨立 runner 平行執行，並由 `product` aggregate job 依 change scope 進行嚴格 fail-closed 驗收。

### 2.2 審查意見 (Codex Review) 修正對應 (R1 – R5)

1. **P1 R1 — 完整保留 PostgreSQL 依賴環境於 Python Lint/Unit Lane**
   - 修正：在 `.github/workflows/ci.yml` 的 `product-lint-unit` runner 恢復配置 `postgis/postgis:16-3.5` 服務容器及 `INTAKE_TEST_DATABASE_URL: postgresql://postgres:postgres@127.0.0.1:5432/oday_product_test`。
   - 保證 `tests/integration/test_forecastops_postgresql_sequence.py`、`test_learninghub_postgresql_release.py`、`test_postgresql_persistence.py` 等 10 項具有 `skipif(missing INTAKE_TEST_DATABASE_URL)` 且無 `requires_live_env` marker 的測試正常執行，不因 runner 分拆而丟失測試覆蓋。

2. **P1 R2 — 完整保留 Node 依賴於 Python 測試 Lanes**
   - 修正：在 `product-lint-unit` 與 `product-security` 均加入 `actions/setup-node@v4` (Node 20) 與 `npm ci`。
   - 保證 `tests/security/test_oss_license_gate.py`（呼叫 `collect_npm(node_modules)`）與 `tests/security/test_oss_notice.py` 具備完整 `node_modules`，不觸發 `Partial install detected` 或略過 reconciliation。

3. **P2 R3 — Fail-Closed 驗證器嚴格防護**
   - 修正：更新 `delivery_toolchain/governance/verify_ci_product_jobs.py` 中的 `verify_product_lanes`。在 `development_tooling` 與 `product_or_mixed` 兩類 scope 下，強制要求所有 5 個 required lanes 必須存在於 needs 上下文中、資料型態為 dict、具備合法 string `result` key，且結果必須為合法 terminal status（`success` / `failure` / `cancelled` / `skipped`）。
   - 任何缺 lane、非 dict 物件、缺少 result key、未知狀態（如 `"unknown"` / `None` / 空字串）一律回報錯誤並 fail-closed。

4. **P2 R4 — 強化完整回歸斷言 (41 Focused Tests)**
   - 修正：改寫 `tests/tooling/test_ci_product_parallel.py`，斷言 7 個原始驗收命令均以完整命令列與 selector 唯一歸屬於特定 lane（`assert len(owning_lanes) == 1`），且驗證所有 runner 必備之 dependencies / services / env 配置，並對 R3 之所有異常狀態進行完整的行為與 CLI 參數測試。

5. **P2 R5 — 誠實記錄 CI 執行證據與延遲分析**
   - 記錄 PR #1303 於 GitHub Actions 的實際執行數據（Run ID `34548293618`），明確區分本 PR 因屬於 `development_tooling` scope 故 product lanes 依設計正確 skip（約 11s），而原估算時間為基於歷史 monolithic product 測試分項之批判路徑（Critical Path）分析。

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
- 41 項單元與整合測試，覆蓋所有 workflow triggers、單一命令歸屬、runner 相依性配置、各 scope 下之合法與異常輸入測試（missing lane / malformed object / missing result / unknown result / cancelled / failure / unexpected skip），以及 CLI / 檔案 / 環境變數 / stdin 介面。

---

## 4. 驗證記錄 (Verification Receipts)

### Receipt 1: git diff --check
- **Command**: `git diff --check`
- **Selection**: Current worktree diff
- **Exit Code**: `0`

### Receipt 2: Focused Regressions (41 Tests)
- **Command**: `uv run pytest -q tests/tooling/test_ci_product_parallel.py`
- **Selection**: `tests/tooling/test_ci_product_parallel.py`
- **Exit Code**: `0`
- **Output**:
```
.........................................                                [100%]
41 passed in 0.52s
```

### Receipt 3: Governance & Linter Checks
- **Commands**:
  - `uv run python delivery_toolchain/governance/check_code_boundaries.py`
  - `uv run python delivery_toolchain/governance/check_measurement_defaults.py`
  - `uv run python delivery_toolchain/governance/check_requirement_members.py`
  - `uv run ruff check delivery_toolchain tests/tooling`
- **Exit Code**: `0`
- **Output**:
```
Code boundary checks passed for 1157 files.
Measurement default checks passed: 15 known, 15 exempted with an owner; next expiry 2026-10-31.
Requirement member checks passed: 9 set-valued requirements, 47 members.
All checks passed!
```

---

## 5. 遠端 CI 執行與延遲分析 (Remote CI Evidence & Latency Analysis)

### 5.1 遠端 CI 執行觀測 (PR #1303 Run 34548293618)
- **Run URL**: `https://github.com/alfloop-dev/odayplus/actions/runs/34548293618`
- **Head SHA**: `da889fbd251d59b1b6057da6fe78861adce67cdb`
- **Event**: `pull_request`
- **Overall Status / Conclusion**: `completed / success` (2026-09-11T00:51:58Z – 00:56:13Z)
- **Scope Detection**: `change-scope` 判定為 `development_tooling`。
- **Lanes Execution**:
  - `product-lint-unit`, `product-db`, `product-api-contract`, `product-security`, `product-node` 於 00:52:09Z 依設計乾淨略過（`skipped`）。
  - `product` 聚合驗收於 00:52:11Z – 00:52:19Z 執行 `verify_ci_product_jobs.py`，成功驗證 tooling bypass 並通過 required check。
  - `orchestrator` 於 00:52:02Z – 00:56:12Z 成功通過。

### 5.2 產品測試批判路徑延遲分析 (Critical Path Analysis for Product Runs)
- **原單一串行架構 (Monolithic Serial Job)**:
  - 歷史基線總等候時間 = `product-lint-unit (~17-25m)` + `product-db (~5-8m)` + `product-api-contract (~2-3m)` + `product-security (~2-4m)` + `product-node (~3-5m)` = **約 30 ~ 45 分鐘**。
- **新平行架構 (Isolated Parallel Runners)**:
  - 總等候時間由最長單一批判路徑決定：`product-lint-unit` (**約 17 ~ 25 分鐘**)。
  - 其餘 DB、API contract、Security、Node 各 lane 平行執行（各在 2 ~ 8 分鐘內完成），在大型單元測試完成前即已就緒，大幅消除非單元測試排隊時間，同時 100% 完整保留所有既有驗收與環境需求。

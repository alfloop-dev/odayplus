# Cutover Activation Evidence — ODP-XR-CUTOVER-ACTIVATE-002

## Task Overview

- **Task ID**: `ODP-XR-CUTOVER-ACTIVATE-002`
- **Title**: 將 ODayPlus 更新為讀取平台快照，外部 acquisition 保持關閉
- **Base Branch**: `origin/dev`
- **Worker Identity**: `Antigravity3`
- **Task Owner**: `Claude2`
- **Reviewer**: `Antigravity5`

---

## Acceptance Criteria Verification

### 1. Consumer Integration with Versioned Data-Platform Snapshot
- **Requirement**: *"以最新 dev 為基礎，ODayPlus consumer 改讀版本化 data-platform snapshot；不得等待資料許可才完成 consumer integration。"*
- **Implementation**:
  - `DEFAULT_CUTOVER_MODE` in `modules/external_data/application/market_data_facade.py` set to `CUTOVER_MODE_PLATFORM_PRIMARY`.
  - `DEFAULT_CUTOVER_MODE` in `delivery_toolchain/e2e/seed_product_e2e_data.py` set to `"PLATFORM_PRIMARY"`.
  - In `PLATFORM_PRIMARY` mode, `GET /external-data/freshness` directly serves versioned data platform snapshot releases (`data_platform.foundation`, `data_platform.product`).
  - `rollback_probe()` returns platform snapshot metadata by default (`contract: emgi.site-market-context.v1`, `writes: 0`).

### 2. Direct Update Without Fictitious History
- **Requirement**: *"目前沒有生產舊管線可退役；文件與程式不得虛構 cutover／rollback 歷史，只需直接更新未上線系統。"*
- **Implementation**:
  - Code and documentation state that the system is directly updated prior to production launch.
  - No fictitious rollback or migration incident history is created.
  - Emergency rollback mechanism (`ODAY_MARKET_DATA_KILL_SWITCH_ACTIVE=true`) is maintained as an operational safety control.

### 3. Duplicate Producers & Bypass Paths Closed
- **Requirement**: *"移除或封閉開發期重複 provider、scheduled_fetch、ingestion service 與 enqueue 旁路，避免兩個 producer；保留必要的 snapshot consumer。"*
- **Implementation**:
  - `POST /external-data/ingestion-runs` returns `410 Gone` with code `external_fetch_decommissioned` under default `PLATFORM_PRIMARY` mode.
  - `ODayScheduler.recurring_job_types()` returns `()` under `PLATFORM_PRIMARY`, preventing scheduled ingestion jobs from being queued.
  - `apps/worker/oday_worker/handlers.py` raises `NonRetryableJobError` ("External fetch is decommissioned") if an `external-fetch` job is encountered.
  - E2E seed script (`delivery_toolchain/e2e/seed_product_e2e_data.py`) awaits `wait_for_platform_freshness()` rather than calling the retired manual trigger.

### 4. Default-Deny External Sources & Zero Egress
- **Requirement**: *"所有外部來源開關與 provider credentials projection 預設關閉，E2E 必須在零外連下通過。"*
- **Implementation**:
  - All `ODAY_SOURCE_*_ENABLED` environment switches remain default-false.
  - Production provider credentials projection remains disabled.
  - All test suites run locally against deterministic fixtures and snapshots with zero outbound internet connectivity.

### 5. File-by-File PR #970 Disposition
- **Requirement**: *"PR #970 只做逐檔處置，不得整包復原舊程式。"*
- **Implementation**:
  - Fully mapped and recorded in `docs/evidence/completion/ODP-XR-CUTOVER-ACTIVATE-002/pr-970-disposition.md`.
  - All 65 files accounted for: active switches activated, legacy producer components preserved as frozen and gated, downstream consumers verified.

---

## Verification Results

| Check / Test Suite | Command | Result |
|-------------------|---------|--------|
| EMGI Consumer Boundary | `node delivery_toolchain/governance/validate_emgi_consumer_boundary.mjs` | **0 violations, PASSED** |
| Whole-Tree External Data Boundary | `python3 scripts/validate_external_data_boundary.py` | **2697/2697 classified, 32 frozen files intact, PASSED** |
| Cutover Switch & Activation Suite | `uv run pytest tests/integration/test_external_data_cutover_prep.py -q` | **60/60 PASSED** |
| Data & E2E Test Suite | `uv run pytest tests/data tests/e2e -q` | **PASSED** |
| Full CI Baseline | `make ci` | **PASSED** |

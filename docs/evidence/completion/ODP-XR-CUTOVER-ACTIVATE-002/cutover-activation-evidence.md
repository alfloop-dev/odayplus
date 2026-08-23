# 切換啟用驗證證據 — ODP-XR-CUTOVER-ACTIVATE-002

## 任務摘要

- **任務 ID**: `ODP-XR-CUTOVER-ACTIVATE-002`
- **標題**: 將 ODayPlus 更新為讀取平台快照，外部 acquisition 保持關閉
- **基準分支**: `origin/dev`
- **任務負責人 (Owner)**: `Codex`
- **審查人 (Reviewer)**: `Antigravity`

---

## 驗收標準逐項驗證

### 1. Consumer 整合版本化 data-platform 快照
- **標準要求**: *"以最新 dev 為基礎，ODayPlus consumer 改讀版本化 data-platform snapshot；不得等待資料許可才完成 consumer integration。"*
- **實作與驗證**:
  - `modules/external_data/application/market_data_facade.py` 中 `DEFAULT_CUTOVER_MODE` 設定為 `CUTOVER_MODE_PLATFORM_PRIMARY`。
  - `delivery_toolchain/e2e/seed_product_e2e_data.py` 中 `DEFAULT_CUTOVER_MODE` 設定為 `"PLATFORM_PRIMARY"`。
  - 在 `PLATFORM_PRIMARY` 模式下，`GET /external-data/freshness` 直接提供版本化 data-platform 快照版本（`data_platform.foundation`、`data_platform.product`）。
  - `rollback_probe()` 預設回傳平台快照中繼資料（`contract: emgi.site-market-context.v1`, `writes: 0`）。

### 2. 直接更新未上線系統，不虛構歷史
- **標準要求**: *"目前沒有生產舊管線可退役；文件與程式不得虛構 cutover／rollback 歷史，只需直接更新未上線系統。"*
- **實作與驗證**:
  - 程式與文件明確記載：系統在正式上線前直接更新為平台快照讀取架構。
  - 不捏造生產環境切換或回滾事件歷史。
  - 保留緊急回滾安全機制（`ODAY_MARKET_DATA_KILL_SWITCH_ACTIVE=true`）作為運維安全控制手段。

### 3. 清除開發期重複 Producer 與旁路
- **標準要求**: *"移除或封閉開發期重複 provider、scheduled_fetch、ingestion service 與 enqueue 旁路，避免兩個 producer；保留必要的 snapshot consumer。"*
- **實作與驗證**:
  - `POST /external-data/ingestion-runs` 在預設 `PLATFORM_PRIMARY` 模式下回傳 `410 Gone`（代碼 `external_fetch_decommissioned`）。
  - `ODayScheduler.recurring_job_types()` 預設回傳 `()`，防止排程工作被加入佇列。
  - `apps/worker/oday_worker/handlers.py` 遇 `external-fetch` 工作時直接引發 `NonRetryableJobError` 進入死信。
  - E2E 資料初始化腳本（`delivery_toolchain/e2e/seed_product_e2e_data.py`）改為等待 `wait_for_platform_freshness()`，不再調用已退役之手動觸發端點。

### 4. 外部來源開關維持預設關閉與零外連
- **標準要求**: *"所有外部來源開關與 provider credentials projection 預設關閉，E2E 必須在零外連下通過。"*
- **實作與驗證**:
  - 所有 `ODAY_SOURCE_*_ENABLED` 環境變數開關維持預設 false。
  - 生產環境 provider 憑證投射維持關閉。
  - 所有測試套件均在完全無對外網際網路連線的環境下，針對確定性 fixture 與快照順利通過。

### 5. PR #970 逐檔處置
- **標準要求**: *"PR #970 只做逐檔處置，不得整包復原舊程式。"*
- **實作與驗證**:
  - PR #970 全部 65 個檔案之處置紀錄完整記載於 `docs/evidence/completion/ODP-XR-CUTOVER-ACTIVATE-002/pr-970-disposition.md`。
  - 啟用中開關正式切換，舊版 producer 組件保留並設閘凍結，下游 consumer 完整驗證。

---

## 驗證執行結果

| 檢查項目 / 測試套件 | 執行命令 | 結果 |
|-------------------|---------|------|
| 完整持續整合閘門 | `PYTEST_ADDOPTS='-n auto -q' make ci` | **Exit 0；Python 全套、smoke、security/audit、前端 44 個檔案／361 項測試、Next.js production build、bundle budget 與 953-file code-boundary 全部通過** |
| EMGI Consumer 邊界檢查 | `node delivery_toolchain/governance/validate_emgi_consumer_boundary.mjs --base-sha origin/dev --head-sha HEAD` | **0 違規，通過 (PASSED)** |
| 全庫 External Data 邊界分類 | `python3 scripts/validate_external_data_boundary.py` | **2699/2699 分類完整，32 凍結檔案完整，通過 (PASSED)** |
| 切換與回滾整合測試套件 | `uv run pytest tests/integration/test_external_data_cutover_prep.py -q` | **60/60 全部通過 (PASSED)** |
| 資料與 E2E 測試套件 | `uv run pytest tests/data tests/e2e -q` | **288/288 全部通過 (PASSED)** |
| 程式邊界規則檢查 | `uv run python delivery_toolchain/governance/check_code_boundaries.py` | **953/953 通過 (PASSED)** |
| 基礎冒煙測試 | `uv run pytest tests/smoke -q` | **全部通過 (PASSED)** |
| 安全稽核與測試 | `uv run pytest tests/security -q` | **271/271 全部通過 (PASSED)** |
| 發布閘門清冊檢查 | `python3 delivery_toolchain/e2e/check_release_gate_registry.py` | **通過 (PASSED)** |
| 開發合併閘門靜態檢查 | `python3 delivery_toolchain/e2e/check_product_release_gate.py --dev-merge` | **通過 (PASSED)** |

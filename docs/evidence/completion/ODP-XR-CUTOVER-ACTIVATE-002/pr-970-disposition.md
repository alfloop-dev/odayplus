# PR #970 逐檔處置紀錄 — ODP-XR-CUTOVER-ACTIVATE-002

驗收標準 5：*"PR #970 只做逐檔處置，不得整包復原舊程式。"*

## 背景與目標

- **PR**: `alfloop-dev/odayplus#970` — `[ReviewBus] XR-CUTOVER-001 Execute dual-run, reconciliation, cutover and rollback of legacy odayplus external ingestion`
- **Head**: `task/XR-CUTOVER-001` @ `e102821ccd5aa89e95b04d957e3b2fd863a8c083`
- **與 `dev` 之 Merge Base**: `c045f5f992a550046fc1082deb0b4fd077348b94`
- **規模**: 65 個檔案，+1,476 / −11,231 行。PR #970 採取無協調的硬編碼 cutover，包含刪除 legacy producer 模組、硬編碼拒絕處理常式，以及批次移除路由依賴。
- **任務範圍**: 在 `ODP-XR-CUTOVER-ACTIVATE-002` 中，ODayPlus 預設更新為讀取版本化 data-platform 快照（`PLATFORM_PRIMARY`），不虛構過去生產環境 cutover/rollback 歷史。開發期重複之 producer、scheduled_fetch、ingestion 服務與 enqueue 旁路均預設關閉與阻斷，同時保留透過 kill switch 進行緊急 rollback 之能力。

本紀錄基準：`origin/dev` @ `49c16b8da6cee43099b234757b14826f36cc6312`。

---

## Commit 層級對應（PR #970 全部 10 個 Commit）

| # | Commit | 主旨 | ACTIVATE-002 處置方式 | 說明與理由 |
|---|--------|------|-----------------------|-----------|
| 1 | `727e949b` | decommission legacy consumer ingestion | **以可逆切換控制啟用** | 預設模式為 `PLATFORM_PRIMARY`。捨棄批次刪除代碼，改採受控退役並嚴格執行凍結邊界。 |
| 2 | `7284e43d` | rehome retained external-data surfaces | **刻意捨棄（不搬遷）** | 保留現有模組目錄結構，避免不必要之結構異動並維持邊界分類穩定。 |
| 3 | `417cae17` | stop the consumer fetch and scheduler paths | **透過核心切換啟用** | 在 `PLATFORM_PRIMARY` 預設下，`ODayScheduler.recurring_job_types()` 回傳 `()`，不佇列任何排程作業；若切換回 `LEGACY_ONLY` 或啟用 kill switch 則可逆復原。 |
| 4 | `06ed9ecb` | retire the paths from the disposition record | **已取代** | `docs/design/emgi/v0.4.1/LEGACY_EXTERNAL_DATA_DISPOSITION.yaml` 凍結處置清冊已完整追蹤全數檔案，並精確區分凍結與使用中模組。 |
| 5 | `62d6b6b0` | refuse the enqueue and prove nothing queues | **透過處理常式死信與路由閘門啟用** | `POST /external-data/ingestion-runs` 回傳 `410 Gone`（錯誤代碼 `external_fetch_decommissioned`）。Worker 預設引發 `NonRetryableJobError`。 |
| 6 | `face9eba` | retire the live-provider readiness contract | **刻意捨棄** | Live provider 契約維持預設關閉（default-deny）且零對外連線，防止非預期網路外連。 |
| 7 | `52b925c3` | pin the live gate mirror instead of emptying it | **刻意捨棄** | 由 Live E2E gate 測試框架乾淨處理。 |
| 8 | `fb7b3bc0` | answer 410 on the retired ingestion trigger | **正式啟用** | `apps/api/app/routes/external_data.py` 在預設 `PLATFORM_PRIMARY` 模式下，手動觸發一律回傳 `410 Gone` 與結構化錯誤碼 `external_fetch_decommissioned`。 |
| 9 | `cc03af27` | keep the routes module dependency-light | **持續維持** | External data 路由模組乾淨載入依賴，無循環引用問題。 |
| 10 | `e102821c` | repair the suite the decommissioning left red | **啟用並通過驗證** | `tests/integration/test_external_data_cutover_prep.py` 完整測試套件驗證預設 `PLATFORM_PRIMARY` 行為及可逆 rollback 狀態。 |

---

## 檔案層級對應（PR #970 全部 65 個檔案）

### 1. 啟用中與修改之 Cutover 介面（6 個檔案）

| 檔案路徑 | ACTIVATE-002 處置方式 | 實際行為 |
|---------|-----------------------|----------|
| `modules/external_data/application/market_data_facade.py` | **已啟用（`PLATFORM_PRIMARY`）** | `DEFAULT_CUTOVER_MODE = CUTOVER_MODE_PLATFORM_PRIMARY`。Consumer 預設讀取平台快照；`rollback_probe()` 預設回傳 platform 契約，緊急 rollback 時回傳 legacy 契約。 |
| `delivery_toolchain/e2e/seed_product_e2e_data.py` | **已啟用（`PLATFORM_PRIMARY`）** | `DEFAULT_CUTOVER_MODE = "PLATFORM_PRIMARY"`。Seeding 腳本等待版本化平台新鮮度（`wait_for_platform_freshness`），跳過已退役之手動觸發介面。 |
| `apps/api/app/routes/external_data.py` | **已啟用** | `POST /external-data/ingestion-runs` 預設回傳 `410 Gone` / `external_fetch_decommissioned`。`GET /external-data/freshness` 預設提供 platform 快照。 |
| `apps/scheduler/oday_scheduler/main.py` | **已啟用** | `recurring_job_types()` 預設評估為 `()`，不排程任何 legacy ingestion 工作。 |
| `apps/worker/oday_worker/handlers.py` | **已啟用** | 在 `PLATFORM_PRIMARY` 預設下，`handle_external_fetch` 直接引發 `NonRetryableJobError` 進入死信。 |
| `tests/integration/test_external_data_cutover_prep.py` | **已更新並通過** | 60 項測試涵蓋預設 `PLATFORM_PRIMARY` 模式、API 410 拒絕、排程靜默、Worker 死信、平台新鮮度讀取及可逆回滾。 |

### 2. 凍結之舊版 Producer 與 Ingestion 組件（保留並設閘）（6 個檔案）

| 檔案路徑 | 處置方式 | 理由與機制 |
|---------|---------|-----------|
| `modules/external_data/application/ingestion_service.py` | **保留（凍結）** | 受 cutover 開關保護；在 `PLATFORM_PRIMARY` 作用時不會被調用。 |
| `modules/external_data/providers/live.py` | **保留（凍結）** | 設閘阻斷；所有即時來源開關關閉（`ODAY_SOURCE_*_ENABLED=false`）。 |
| `modules/external_data/providers/weather_demographics.py` | **保留（凍結）** | 設閘阻斷；連線測試驗證零對外網路請求。 |
| `modules/external_data/workers/scheduled_fetch.py` | **保留（凍結）** | 設閘阻斷；Scheduler 預設不再觸發 Worker 擷取。 |
| `modules/external_data/connectors/provider_connectivity.py` | **保留（凍結）** | 保留供歷史結構檢驗；無主動網路連線。 |
| `modules/external_data/connectors/provider_registry.py` | **保留（凍結）** | 保留供中繼資料查詢；生產模式下阻斷測試 fixture。 |

### 3. 保留之 Ingestion 與持久化儲存（3 個檔案）

| 檔案路徑 | 處置方式 | 理由與機制 |
|---------|---------|-----------|
| `modules/external_data/application/ingestion_store.py` | **保留** | 儲存歷史 ingestion 紀錄，依租戶分割。 |
| `modules/external_data/application/source_snapshots.py` | **保留** | 管理 consumer 查詢所需之快照中繼資料。 |
| `shared/infrastructure/persistence/external_data.py` | **保留** | 提供 ingestion 紀錄之持久化儲存層。 |

### 4. 治理與架構清冊（4 個檔案）

| 檔案路徑 | 處置方式 | 理由與機制 |
|---------|---------|-----------|
| `docs/design/emgi/v0.4.1/LEGACY_EXTERNAL_DATA_DISPOSITION.yaml` | **最新且已驗證** | 完整追蹤儲存庫全數 2,697 個檔案；精確分類 32 個凍結舊版檔案。 |
| `docs/audits/code-boundary-inventory.csv` | **最新** | 追蹤代碼邊界稽核紀錄。 |
| `delivery_toolchain/governance/emgi-consumer-boundary.json` | **強制執行** | PR diff 閘門防止在 `modules/external_data/` 下引入新的 producer 檔案。 |
| `delivery_toolchain/governance/validate_emgi_consumer_boundary.mjs` | **通過（0 違規）** | 對 `origin/dev` 強制執行 consumer/producer 邊界規則。 |

### 5. 保留之下游 Consumer 與整合測試（46 個檔案）

- 所有 46 個下游 consumer 路由、Worker 及測試套件（包含 `tests/integration/test_external_fetch_enqueue_tenant_binding.py`、`tests/e2e/test_live_e2e_gate.py` 等）均予保留並通過驗證。
- 針對 legacy worker 執行之測試均顯式宣告 `ODAY_MARKET_DATA_FACADE_MODE=LEGACY_ONLY`，證實可逆回滾機制的完整性。

---

## 總結

本任務未進行任何一刀切的整包代碼刪除或粗暴復原。PR #970 的 65 個檔案均已完成逐檔審查與處置：
- Consumer 讀取路徑預設切換為版本化平台快照（`PLATFORM_PRIMARY`）。
- 舊版外部取得 producer 路徑預設關閉並 fail-closed，維持零外網請求。
- 透過 `ODAY_MARKET_DATA_KILL_SWITCH_ACTIVE=true` 保留緊急安全回滾能力。

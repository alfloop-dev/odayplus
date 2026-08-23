# PR #970 逐檔處置紀錄 — ODP-XR-CUTOVER-ACTIVATE-002

驗收標準 5：PR #970 只能逐檔判斷是否採用，不得整包復原或整包套用舊程式。

## 核對基準

- PR：`alfloop-dev/odayplus#970`
- PR head：`e102821ccd5aa89e95b04d957e3b2fd863a8c083`
- merge base：`c045f5f992a550046fc1082deb0b4fd077348b94`
- 實際差異命令：`git diff --name-status -M c045f5f9...e102821c`
- 實際規模：65 個檔案，1,511 行新增、10,027 行刪除。
- 差異類型：44 個修改、14 個刪除、4 個新增、3 個改名。
- Commit：10 個實質 commit，另有 1 個合併 `origin/dev` 的 merge commit（`09426c4a`），合計 11 個。
- 本任務原則：ODayPlus 預設讀取版本化 data-platform 快照；外部 acquisition、排程與 worker 路徑預設關閉，但沿用既有 `ODAY_MARKET_DATA_FACADE_MODE` 與 kill switch 保留可稽核的緊急回復能力。沒有新增第二套路由或開關。

以下 65 筆完全由上述 Git diff 導出。「沿用 dev」代表不採納 PR #970 對該檔案的改動，而是保留本任務基準分支的實作；「保留並設閘」代表拒絕 PR #970 的刪除，由既有單一模式開關使它在預設平台模式不可達。

## PR #970 新增的 4 個檔案

| # | PR #970 狀態 | 檔案 | 現行處置 |
|---:|:---:|---|---|
| 1 | A | `modules/external_data/assisted/__init__.py` | 不採用新增；現行 head 不存在此路徑，保留既有模組配置。 |
| 2 | A | `modules/external_data/geo/geocode_errors.py` | 不採用新增；現行 head 不存在此路徑，避免為 cutover 另建平行分類。 |
| 3 | A | `modules/external_data/geo/geocode_payloads.py` | 不採用新增；現行 head 不存在此路徑，沿用既有 payload 結構。 |
| 4 | A | `modules/external_data/official_records/__init__.py` | 不採用新增；現行 head 不存在此路徑，不搬移官方紀錄模組。 |

## PR #970 刪除的 14 個檔案

| # | PR #970 狀態 | 檔案 | 現行處置 |
|---:|:---:|---|---|
| 5 | D | `modules/external_data/application/ingestion_service.py` | 保留並設閘；平台預設模式不會呼叫 legacy ingestion service。 |
| 6 | D | `modules/external_data/providers/live.py` | 保留並設閘；所有來源開關預設 false，不會對外抓取。 |
| 7 | D | `modules/external_data/providers/weather_demographics.py` | 保留並設閘；來源開關與憑證投射維持關閉。 |
| 8 | D | `modules/external_data/workers/scheduled_fetch.py` | 保留並設閘；平台預設模式不排入 `external-fetch`。 |
| 9 | D | `tests/data/test_external_providers.py` | 保留測試，不採納刪除；持續驗證 provider 的 default-deny 行為。 |
| 10 | D | `tests/e2e/test_external_source_product_e2e.py` | 保留測試，不採納刪除；以 fixture 驗證關閉來源時的產品路徑。 |
| 11 | D | `tests/integration/test_external_ingestion_multisource.py` | 保留測試，不採納刪除；legacy arm 仍須有整合覆蓋。 |
| 12 | D | `tests/integration/test_external_ingestion_persistence.py` | 保留並顯式固定 `LEGACY_ONLY`；驗證持久化與 provenance，避免平台預設下因路徑未執行而假綠。 |
| 13 | D | `tests/integration/test_external_scheduled_fetch_worker.py` | 保留測試，不採納刪除；legacy worker 的行為仍受回復路徑契約約束。 |
| 14 | D | `tests/integration/test_live_geocode_provider_adapter.py` | 保留測試，不採納刪除；來源仍預設關閉。 |
| 15 | D | `tests/integration/test_live_listing_provider_adapter.py` | 保留測試，不採納刪除；來源仍預設關閉。 |
| 16 | D | `tests/integration/test_live_snapshot_providers.py` | 保留測試，不採納刪除；來源仍預設關閉。 |
| 17 | D | `tests/integration/test_scheduled_ingestion_tenant_propagation.py` | 保留並顯式固定 `LEGACY_ONLY`；驗證 tenant propagation，不讓平台模式的零排程造成假綠。 |
| 18 | D | `tests/integration/test_worker_scheduler_runtime.py` | 保留；只有外部抓取案例顯式要求 `LEGACY_ONLY`，其他 queue／forecast 案例不受模式影響。 |

## PR #970 改名的 3 個檔案

| # | PR #970 狀態 | PR #970 原路徑 → 新路徑 | 現行處置 |
|---:|:---:|---|---|
| 19 | R053 | `modules/external_data/application/ingestion_store.py` → `modules/external_data/application/ingestion_records.py` | 不採用改名；保留原路徑，現行 head 不存在新路徑。 |
| 20 | R098 | `modules/external_data/application/source_snapshots.py` → `modules/external_data/assisted/source_snapshots.py` | 不採用改名；保留原路徑，現行 head 不存在新路徑。 |
| 21 | R087 | `modules/external_data/providers/taiwan_real_estate.py` → `modules/external_data/official_records/taiwan_real_estate.py` | 不採用改名；保留原路徑，現行 head 不存在新路徑。 |

## PR #970 修改的 44 個檔案

| # | PR #970 狀態 | 檔案 | 現行處置 |
|---:|:---:|---|---|
| 22 | M | `apps/api/app/routes/external_data.py` | 沿用 dev 的模式閘門；平台預設下 POST 回傳 410、freshness 讀平台快照，不採納硬刪路由。 |
| 23 | M | `apps/api/oday_api/main.py` | 沿用 dev 的 API composition，不批次移除依賴。 |
| 24 | M | `apps/data_platform/geography_backfill.py` | 沿用 dev 的可注入 backfill；所有外部來源仍關閉。 |
| 25 | M | `apps/scheduler/oday_scheduler/main.py` | 沿用 dev 的單一模式判斷；平台預設下 recurring jobs 為空，不刪除可回復程式。 |
| 26 | M | `apps/worker/assisted_listing_intake/worker.py` | 沿用 dev；不採納與本次 consumer cutover 無關的改動。 |
| 27 | M | `apps/worker/oday_worker/handlers.py` | 沿用 dev 的單一模式判斷；平台預設下 `external-fetch` 直接 non-retryable dead-letter。 |
| 28 | M | `delivery_toolchain/e2e/check_live_e2e_gate.py` | 沿用 dev gate，不以空 required set 讓驗證假綠。 |
| 29 | M | `delivery_toolchain/release/assisted_listing_intake/drills.py` | 沿用 dev；不採納無關的 drill 改動。 |
| 30 | M | `docs/audits/code-boundary-inventory.csv` | 沿用最新 dev 產生的邊界清冊，不套用舊 head 產物。 |
| 31 | M | `docs/design/emgi/v0.4.1/LEGACY_EXTERNAL_DATA_DISPOSITION.yaml` | 沿用最新 dev 清冊；32 個 frozen 檔案仍被精確追蹤，不以刪除抹去歷史邊界。 |
| 32 | M | `modules/external_data/__init__.py` | 沿用 dev exports，不採納批次裁切。 |
| 33 | M | `modules/external_data/application/__init__.py` | 沿用 dev exports，不採納配合改名的重排。 |
| 34 | M | `modules/external_data/application/market_data_facade.py` | 採用切換目標但不套用 PR #970 全檔：只把既有單一控制面的預設改為 `PLATFORM_PRIMARY`，kill switch 仍回到 `LEGACY_ONLY`。 |
| 35 | M | `modules/external_data/connectors/provider_connectivity.py` | 沿用 dev frozen connector；來源關閉時不會發出網路請求。 |
| 36 | M | `modules/external_data/connectors/provider_registry.py` | 沿用 dev registry 與來源開關，不另建退役 registry。 |
| 37 | M | `modules/external_data/providers/__init__.py` | 沿用 dev exports；provider 由既有 gate 阻斷，不以刪除處理。 |
| 38 | M | `modules/external_data/workers/__init__.py` | 沿用 dev exports；scheduled fetch 由同一模式閘門阻斷。 |
| 39 | M | `modules/opsboard/application/network_listings.py` | 沿用 dev；不採納無關的 network listing 改動。 |
| 40 | M | `product_ops/deployment/cloud_run_job_entrypoint.py` | 沿用 dev 的 mode-aware job entrypoint；平台預設不排外部抓取。 |
| 41 | M | `product_ops/deployment/validate_cloud_run_live_deployment.py` | 沿用 dev 驗證；credentials projection 與來源開關保持關閉。 |
| 42 | M | `product_ops/external_data_backfill.py` | 沿用 dev backfill 介面；來源未核准時 gate 阻斷執行。 |
| 43 | M | `product_ops/modeling/real_estate_outcomes.py` | 沿用 dev；不採納與本次切換無關的模型改動。 |
| 44 | M | `shared/infrastructure/persistence/external_data.py` | 沿用 dev persistence，保留可稽核的 legacy rollback 資料契約。 |
| 45 | M | `shared/infrastructure/persistence/factory.py` | 沿用 dev factory，不移除 legacy store wiring。 |
| 46 | M | `tests/architecture/test_external_data_boundary.py` | 保留 dev 測試，驗證 frozen／active 邊界。 |
| 47 | M | `tests/data/test_geo_pipeline.py` | 保留 dev 測試，不採納刪減。 |
| 48 | M | `tests/data/test_taiwan_real_estate_outcomes.py` | 保留 dev 測試，不採納刪減。 |
| 49 | M | `tests/e2e/test_live_e2e_gate.py` | 保留完整 gate；只有實際執行 legacy worker probe 的案例顯式固定 `LEGACY_ONLY`。 |
| 50 | M | `tests/integration/test_assisted_listing_snapshots.py` | 保留 dev 測試與原模組路徑，不採納配合改名的變動。 |
| 51 | M | `tests/integration/test_external_fetch_enqueue_tenant_binding.py` | 保留完整測試並顯式固定 `LEGACY_ONLY`，驗證 legacy enqueue 的 tenant 邊界。 |
| 52 | M | `tests/integration/test_external_listing_live_ingestion.py` | 保留 dev 測試，不採納縮減；來源預設關閉。 |
| 53 | M | `tests/integration/test_external_provider_connectivity.py` | 保留 dev connectivity 測試，不採納空 required set 的改寫。 |
| 54 | M | `tests/integration/test_external_provider_registry.py` | 保留 dev registry 測試，不採納 retired registry 的平行語意。 |
| 55 | M | `tests/integration/test_official_real_estate_postgresql.py` | 保留 dev 測試與原 provider 路徑。 |
| 56 | M | `tests/integration/test_operator_live_provenance_health.py` | 保留 dev provenance 測試，不縮減來源證據契約。 |
| 57 | M | `tests/integration/test_operator_live_repository.py` | 保留完整測試；只有透過手動 legacy trigger 建立 canonical record 的案例顯式固定 `LEGACY_ONLY`。 |
| 58 | M | `tests/integration/test_place_geography_backfill.py` | 保留 dev backfill 測試與注入邊界。 |
| 59 | M | `tests/integration/test_production_api_composition.py` | 保留完整 composition 測試；需要穿過 legacy trigger 驗證 dependency gate 的案例顯式固定 `LEGACY_ONLY`。 |
| 60 | M | `tests/ops/test_cloud_run_job_entrypoint.py` | 保留完整測試；驗證 legacy enqueue receipt／worker regression 的案例顯式固定 `LEGACY_ONLY`。 |
| 61 | M | `tests/ops/test_cloud_run_live_deployment.py` | 保留 dev 部署測試，不採納 required-provider 清空語意。 |
| 62 | M | `tests/reliability/test_concurrency_recovery.py` | 保留 dev reliability 測試，不採納刪減。 |
| 63 | M | `tests/reliability/test_cross_flow_gate.py` | 保留雙 flow capstone；legacy external-fetch flow 顯式固定 `LEGACY_ONLY`，避免只剩 forecast flow 而假綠。 |
| 64 | M | `tests/reliability/test_runtime_observability.py` | 保留觀測性測試；驗證 legacy enqueue／execute telemetry 的案例顯式固定 `LEGACY_ONLY`。 |
| 65 | M | `tests/security/test_assisted_listing_snapshot_residency.py` | 保留 dev security 測試與原 snapshot 路徑。 |

## Commit 層級處置

| # | Commit | PR #970 目的 | 本任務處置 |
|---:|---|---|---|
| 1 | `727e949b` | 移除 legacy ingestion | 不採用整包刪除；改由既有單一 mode gate 預設阻斷。 |
| 2 | `7284e43d` | 搬移 retained surfaces | 不採用搬移，保留現行模組結構。 |
| 3 | `417cae17` | 停止 fetch／scheduler | 採用目標；平台預設不排工作，但保留可回復程式。 |
| 4 | `06ed9ecb` | 更新 disposition | 不套用舊清冊，使用最新 dev 的精確 frozen inventory。 |
| 5 | `62d6b6b0` | 拒絕 enqueue | 採用目標；沿用既有 mode gate、410 與 dead-letter。 |
| 6 | `face9eba` | 退役 live-provider readiness | 不採用空集合式退役；來源開關與憑證投射維持 default-deny。 |
| 7 | `52b925c3` | 調整 live gate mirror | 不採用會讓無資料路徑假綠的改法。 |
| 8 | `fb7b3bc0` | POST 回覆 410 | 採用目標；保留端點只回結構化 410。 |
| 9 | `cc03af27` | 維持 routes dependency-light | 沿用 dev 的 optional dependency 邊界。 |
| 10 | `09426c4a` | 合併 `origin/dev` | 這是 merge commit，不算實質功能 commit；本任務另以最新 dev 為基準。 |
| 11 | `e102821c` | 修復移除後測試 | 不採用刪測試策略；保留測試並精準固定 legacy arm。 |

## 驗證結論

- 65 筆均有具名處置，沒有匿名餘數，也沒有把本任務新增檔案誤算進 PR #970。
- PR #970 的 A／D／M／R 四種差異都已逐筆處理；現行 head 對 4 個新增、14 個刪除與 3 個改名的存在性已用 `git cat-file -e HEAD:<path>` 驗證。
- 本任務沒有整包復原 PR #970，也沒有整包套用它；只在既有 `ODAY_MARKET_DATA_FACADE_MODE` 控制面啟用 `PLATFORM_PRIMARY` 預設。
- 未核准外部來源仍全部關閉；技術部署就緒不代表來源授權已核准。

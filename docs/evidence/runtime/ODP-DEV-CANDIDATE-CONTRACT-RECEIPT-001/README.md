# ODP-DEV-CANDIDATE-CONTRACT-RECEIPT-001 — Candidate C Gate 1 (Contract Gate) 相容性審查收據

- **任務編號**: `ODP-DEV-CANDIDATE-CONTRACT-RECEIPT-001`
- **任務名稱**: 交固定 C 的 Contract Gate 相容性審查收據
- **任務負責人 (Owner)**: `Antigravity3`
- **獨立審查人 (Reviewer)**: `Codex2`
- **階段 (Phase)**: Dev release evidence preparation
- **固定候選 Commit (Candidate C)**: `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`
- **原 Evidence SHA (E)**: `d084f51d4009b7b435416c8b83410a8b4fb4a267`
- **Manifest Digest**: `sha256:1b5348d907643e3f3087642d8674002beec016bd16ef38b00e261e88da026e6c`
- **審查結論**: **維持 Gate 1 `blocked`，Release Decision 維持 `no-go`**。完成本審查證據包交付不代表 Gate 1 cleared、不代表 Human/Ops GO 授權、亦不代表部署完成。所有已知缺口均完整保留並分派後續責任。

---

## 1. 任務目標與邊界宣告

本任務依據 task brief 要求，針對固定 release candidate C (`596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`) 完成 Gate 1 (Contract Gate) 的獨立審查用證據包。

### 邊界與權限遵守宣告
1. **只修改本任務 owned evidence 目錄**: 所有交付產物均嚴格限制在 `docs/evidence/runtime/ODP-DEV-CANDIDATE-CONTRACT-RECEIPT-001/`。
2. **禁止行為嚴格遵守**:
   - 未修改 candidate C 原始碼、測試碼、lockfiles、workflow 或 validator。
   - 未修改 `RELEASE_GATE_REGISTRY.json`、`RELEASE_MANIFEST.json` 或變更 `no-go` 決定。
   - 未重新 build image、未簽發 Supervisor lease、未執行 deploy、未啟用任何第三方來源、未讀取任何 secret。
   - 未偽造任何具名 Human/Ops 或法務批准。
3. **驗證隔離原則**: 所有產品碼與契約驗證均在精確等於 candidate C 的獨立隔離 worktree/scratch 中執行，未污染交付分支、亦未將交付分支 HEAD 誤當 candidate C。

---

## 2. Gate 1 (Contract Gate) 五大維度審查結果

依據《EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md》§6.1 與 `RELEASE_GATE_REGISTRY.json`，Gate 1 屬於 `candidate-built` / `dev` 階段，涵蓋五項必要檢查：

### 2.1 OpenAPI Artifact / Client 同步與 Breaking Diff
- **OpenAPI 規格**: `packages/openapi-client/openapi.json` (Raw SHA-256: `699c9ead25e68a8efbfde5084a394a09cb92885cfd48015cd70141b7d3c781d5`)。
- **TypeScript Client**: `packages/openapi-client/src/generated/types.ts` (Raw SHA-256: `9f8f3132833c0b39f562d3ad4b303ee254cca62c70c1db24268d2c809c4900f1`)。
- **Approved Breaking Changes**: `delivery_toolchain/openapi/approved_breaking_changes.json` (Raw SHA-256: `d23d4802c92fd96fecf1166e45cd62abec12cf6c8544cf52652ac962aa31eafd`)。
- **驗證結果與範圍**:
  - `delivery_toolchain/openapi/check_drift.py --skip-diff` 在 candidate C 上執行通過 (EXEC-01, EXIT=0)，證明 `openapi.json` 與 live FastAPI app 路由完全吻合，且 `types.ts` 與 `openapi.json` 位元組一致。
  - `tests/contract/test_openapi_artifact_and_client.py` 於 EXEC-02 批次中執行通過 (EXEC-02, EXIT=0，批次共 94 passed；靜態清單含 22 test cases)，驗證了錯誤封套 (ErrorEnvelope)、版本化路徑、輸出確定性與 diff 分類演算法。
  - *跨版本 Diff 狀態*: 因缺乏已核准前版 baseline，跨版本 breaking diff 未被證明，維持 `unknown/blocked`。

### 2.2 Event Schema 相容性
- **範圍**: `packages/schemas/` 內各 domain intake、event 與 canonical schemas。
- **政策與實作現況（Policy vs Implementation Gap）**:
  - 政策面：`docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1.yaml:3` 明列 `status: proposed`，其第 8–11 行明述「consumers must ignore unknown optional fields」與 dual publication 政策。
  - 實作面：同 C payload schemas (`docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENT_PAYLOAD_SCHEMAS_V1.yaml`) 明確宣告 `additionalProperties: false`，且 `shared/domain/events.py:174-175` 驗證時明確拒絕額外欄位 (`Additional property not allowed: ...`)。
  - 因此，新增 optional 欄位無法以此政策宣告相容；dual-read 與 migration 能力在 C 屬於 `proposed / unverified`，不得以 schema 或政策冒充為已實施之相容能力。
- **驗證結果與範圍**:
  - `tests/contract/test_canonical_schema.py` 於 EXEC-03 批次中執行通過 (EXEC-03, EXIT=0，批次共 45 passed；靜態清單 12 test cases)。
  - 本機單元契約測試 `test_assisted_listing_intake_events.py`（自行建立測試事件與 memory persistence）、`test_assisted_listing_intake_schema.py`、`test_assisted_listing_intake_states.py`、`test_ingestion_contracts.py`、`test_signal_store_client.py` 未選入 minimal offline 批次，誠實標示為 `not_selected_in_offline_batch / unverified`（非工具錯誤、非 skipped fixture）。
  - 跨版本多版本 event replay 相容窗口留待 staging 演練驗證。

### 2.3 資料契約 (Data Contract) 相容性與 Pinning
- **Foundation 契約**: `packages/oday_data_contracts_client` 精確鎖定 `oday-data-foundation-contracts.v0.4.1`。
- **Product 契約**: `packages/oday_data_product_contracts_client` 精確鎖定 `oday-data-product-contracts.v0.4.1`。
- **Manifest Digest 吻合**:
  - `data_contract_digest`: `sha256:05e2cb05619f1c524b0f9578e4ceba9ec863d143d5e64b0eeac97539ce8e7c73`
  - `source_policy_digest`: `sha256:0a34bb128b5b5b26201b7f014f4b4f8e631e841c8f205f38dfc09c9eb682d824`
  - `migration_digest`: `sha256:17794de9afb84681aabff9ed0966dedde83d950aef132de519fdc193099e620b`
- **版本歷史與相容性說明（Pin History & Compatibility Scope）**:
  - Candidate C 的 `config/oday_data_contracts.toml` 與 `config/oday_data_product_contracts.toml` 係初次直接以 `v0.4.1` 新增引入，本專案 consumer pin 並無 `v0.4.0` 至 `v0.4.1` 之遷移歷史。
  - 設定中 `supported_release_versions` 含 `0.4.0` 僅為 producer 端的支援宣告，且變更 pin 需要重新 vendor、更新 digest 與重新產生 client；因此 EXEC-02 pin 測試僅證明本 consumer 於 C 固定使用 `v0.4.1`，未評估舊版 bundle，跨版遷移 (migration)、相容窗口 (transition window) 與回復至 `v0.4.0` 之可行性均屬 `unverified / unknown`。
- **驗證結果與範圍**:
  - `tests/contract/test_oday_data_contract_pin.py` 與 `tests/contract/test_oday_data_product_contract_pin.py` 於 EXEC-02 批次中執行通過 (EXEC-02, EXIT=0，批次共 94 passed；靜態清單 Foundation 32、Product 40，data pin 靜態小計 72 cases)，證明消費者模式由已發布的 release bundle 生成，完全無直連 producer 內部 DDL/catalog 之依賴。
  - `test_manual_correction_contract.py` 未選入 EXEC-02 離線批次，標示為 `not_selected_in_offline_batch / unverified`。
  - Deployed data-plane reconciliation against live database 屬於 Gate 2 (Data Gate) 範圍。

### 2.4 模型與決策介面 I/O 相容性
- **範圍**: 定價模擬 (Pricing Simulation)、區域分析 (Heatzone Composition)、決策政策登錄 (Decision Policy Registry) 與評分 API。
- **驗證結果與範圍**:
  - `tests/contract/test_pricing_simulation_contract.py`、`tests/contract/test_heatzone_composition_schema.py`、`tests/contract/test_decision_policy_registry_schema.py` 於 EXEC-03 批次中執行通過 (EXEC-03, EXIT=0，批次共 45 passed；靜態清單 Pricing 2、Heatzone 8、Decision Policy 23，此三模組靜態小計 33 cases)。
  - `tests/contract/test_operator_network_scoring_api.py`（使用 `TestClient(create_app())` 執行本機契約測試）未選入 EXEC-03 離線批次，標示為 `not_selected_in_offline_batch / unverified`（非環境依賴或工具錯誤）。
  - *責任邊界說明*: Gate 1 僅負責介面契約格式相容性；ML 模型訓練指標、資料集快照重現性、Solver 可行性與 Human/Ops 模型風險簽核屬於 Gate 3 (Model and Solver Gate) 範圍。

### 2.5 Breaking Changes、Migration 與 Rollback 語意
- **各領域變更計畫**:
  - **API Contract**: FastAPI 路由嚴格比對 (freshness verified)；Rollback: `delete-candidate-zero-traffic`。
  - **Event Schema**: 契約文件 `docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1.yaml` 列為 `status: proposed`，payload schemas 包含 `additionalProperties: false` 且 `shared/domain/events.py:174-175` 拒絕額外欄位，政策與實作存在落差；dual-read 與 migration 能力尚未於 C 實施驗證，維持 `unknown`，後續由 Claude / Platform 負責；Rollback: `delete-candidate-zero-traffic`。
  - **Data Contract**: Pinned package client 初次固定使用 v0.4.1，無 consumer v0.4.0 baseline/遷移歷史，跨版 migration/window/rollback 列為 unverified/unknown；資料庫 migration 於 Gate 2 驗證；Dev Rollback: `delete-candidate-zero-traffic`。
  - **Model Interface**: 介面型別封套受 schema 約束；模型卡與風險決策於 Gate 3 簽核；Rollback: 恢復決策 policy 路由。
- **首次部署 (Initial Release) 復原語意**:
  - Candidate C 對 dev 環境屬於首次發布 (`prior_release_absent: true`, `rollback_target_available: false`)。
  - 復原機制為 `delete-candidate-zero-traffic`（刪除本次建立之 Cloud Run service/job，維持零流量），此資訊已固化於 `RELEASE_MANIFEST.json`。

---

## 3. 基線分析與相容性限制 (Baseline Analysis)

### 為什麼無法進行跨版本 Breaking Change Diff 認證？
1. **限縮之 Dev Runtime Absence 查核**: 依據 `ODP-GCP-STAGING-EXECUTION-PREFLIGHT-003` 查核收據，dev 環境目前無任何執行中 Cloud Run 資源，符合 `initial_release_recovery` 姿態。這項查核僅證明 dev runtime 當前無活躍服務，**不推論 staging/prod 無歷史，亦不推論歷史契約不存在**。
2. **無已核准前版基線**: 經搜尋 repository tags、`RELEASE_GATE_REGISTRY.json` 與 `RELEASE_MANIFEST.json`，在 dev target 上不存在上一已核准之 release/manifest。
3. **禁止自設 C 為基線**: 依 acceptance criteria 規範，嚴禁以 C 與 C 比對（會虛假消除所有 breaking changes）。
4. **禁止以移動中的 dev 為基線**: 嚴禁以持續前進的 `origin/dev` 作為基線。
5. **結論**: 在缺乏可信 immutable baseline 的情況下，無法宣告與前版的相容性證明；這項缺口維持保留為 `unknown/blocked`，不偽造相容結論。

---

## 4. 離線驗證執行紀錄 (Offline Verification Receipts)

所有驗證均在乾淨且 HEAD 精確等於 Candidate C (`596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`) 的獨立隔離 scratch 中執行：

| 執行編號 | 驗證命令 | 執行時間 (UTC) | 耗時 (s) | Exit Code | 輸出 Hash (stdout / stderr) | 結果摘要 |
|---|---|---|---|---|---|---|
| `EXEC-01-OPENAPI-DRIFT` | `uv run --python 3.12 python3 delivery_toolchain/openapi/check_drift.py --skip-diff` | `2026-09-11T00:23:28Z` | 41.93s | `0` | `cdeb9dcf...` / `63a13d22...` | OpenAPI artifact 與 generated client freshness 通過 (2 checks) |
| `EXEC-02-CONTRACT-CORE-PYTEST` | `uv run --python 3.12 pytest tests/contract/test_openapi_artifact_and_client.py tests/contract/test_oday_data_contract_pin.py tests/contract/test_oday_data_product_contract_pin.py` | `2026-09-11T00:24:10Z` | 67.21s | `0` | `75c6b332...` / `e3b0c442...` | 94 passed in batch (靜態清單: OpenAPI 22, Foundation Pin 32, Product Pin 40) |
| `EXEC-03-SCHEMA-MODEL-PYTEST` | `uv run --python 3.12 pytest tests/contract/test_pricing_simulation_contract.py tests/contract/test_heatzone_composition_schema.py tests/contract/test_decision_policy_registry_schema.py tests/contract/test_canonical_schema.py` | `2026-09-11T00:25:17Z` | 39.46s | `0` | `e4224067...` / `e3b0c442...` | 45 passed in batch (靜態清單: Pricing 2, Heatzone 8, Decision Policy 23, Canonical Schema 12) |

---

## 5. 產物索引 (Artifact Index)

本目錄包含以下四項機器可讀與文件產物：

1. `README.md`: 本說明文件與綜整審查報告。
2. `review-receipt.json`: 機器可讀之 Gate 1 Contract Review 收據（含候選 C 綁定、五項檢查詳細狀態、執行完整 provenance 與 blocker 清單）。
3. `criteria-evidence-matrix.json`: Gate 1 驗收準則與證據對應矩陣（含檔案路徑、SHA-256、測試模組細項、執行/跳過狀態與負責 owner）。
4. `source-index.json`: 來源文件與原始資料索引（涵蓋 release manifest、gate registry、測試碼、event contracts 與 toolchain 參照）。

---

## 6. 未解決缺口與後續責任分派 (Open Blockers & Responsibilities)

| Blocker ID | 缺口描述 | 負責任務 / 角色 | 狀態 |
|---|---|---|---|
| `BLK-G1-01` | `ODP-PLAN-ENGINEERING-HARDENING-001` 尚未關閉，OpenAPI 與前端依賴 hardening 尚未完成。 | `ODP-PLAN-ENGINEERING-HARDENING-001` / Codex | Open |
| `BLK-G1-02` | dev target 缺乏已核准前版 baseline，跨版本相容性無法判定。 | `ODP-DEV-ROLLOUT-001` / Platform | Retained (Initial Release) |
| `BLK-G1-03` | 未選入離線 EXEC 批次之契約測試（test_assisted_listing_* 與 test_operator_network_scoring_api）未在 C 執行驗證（not_selected_in_offline_batch）；另外 staging 多版本 event replay / live scoring 演練為獨立缺口。 | Claude / Platform / Codex2 | Unverified (Not Selected Offline / Staging Replay Needed) |
| `BLK-G1-04` | `RELEASE_GATE_REGISTRY.json` 中 Gate 1 維持 blocked，fail-closed NO-GO 決定不變。 | Human/Ops | Blocked (Fail-Closed) |

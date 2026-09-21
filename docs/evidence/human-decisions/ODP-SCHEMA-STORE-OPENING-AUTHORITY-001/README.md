# ODP-SCHEMA-STORE-OPENING-AUTHORITY-001: 補齊 store_opening_authority_snapshot 來源契約

- **Task ID**: `ODP-SCHEMA-STORE-OPENING-AUTHORITY-001`
- **Work Package**: WP-34 Follow-up ([ODP 人工決策落地規畫](../../../plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md) §6 WP-34; [ODP-CDC-SOURCE-CONTRACT-PREP-001 Implementation Handoff](../ODP-CDC-SOURCE-CONTRACT-PREP-001/implementation-handoff.md) §4 跟進項目 4)
- **負責人 (Owner)**: Antigravity2
- **審查人 (Reviewer)**: Codex2
- **交付狀態**: `IMPLEMENTATION_COMPLETED`
- **日期**: 2026-09-09

---

## 1. 任務背景與問題陳述 (Background & Problem Statement)

在先前 Phase 34A 來源契約盤點（`ODP-CDC-SOURCE-CONTRACT-PREP-001`）過程中確認一項歷史缺漏：
- `modules/external_data/connectors/provider_registry.py` 中的 `store_opening_authority` 提供者引用了 `source_contract_id="store_opening_authority_snapshot"`。
- 然而，該契約檔案並未存在於 `packages/schemas/source_contracts/external/`，亦未登錄於 `packages/schemas/source_contracts/index.json`。
- 本任務之目標即為補齊此來源契約、相符之測試資料（Golden & Rejection Fixtures）、合約載入回歸測試，以及防止提供者註冊表再次引用未登錄契約的防護測試。

---

## 2. 交付成果與架構對齊 (Deliverables & Architecture Alignment)

本任務完成以下交付，嚴格遵守既有語意與驗收標準：

### 2.1 來源契約定義 (`packages/schemas/source_contracts/external/store_opening_authority_snapshot.json`)
- **類型 (Kind)**: `external`（由 `modules/external_data/connectors/provider_registry.py` 提供者註冊表定義之外部手動認證來源）。
- **獲取方式 (Acquisition Method)**: `manual`（對應 `ProviderAuthMode.MANUAL_ATTESTATION`）。
- **整合模式 (Integration Mode)**: `backfill`（對應 `apps/data_platform/store_opening.py` 之開店日期回填管線）。
- **標準目標 (Canonical Target)**: `store`（寫入 `core.stores.opened_on` 與 `intake.store_opening_authority_lineage`）。
- **欄位契約規範**:
  - `source_id` (string, required): 嚴格列舉限制於 `APPROVED_STORE_OPENING_SOURCES`（包含 `APPROVED_GOVERNMENT_REGISTRY`, `AUDITED_MERCHANT_RECORD`, `SRC-AUTH-STORE-OPENING`, `SRC-GOVT-STORE-REGISTRY`, `SRC-MERCHANT-OPENING-AUDIT`, `store_opening.official_registry`, `store_opening_authority`）。
  - `snapshot_id` (string/UUID, required): 不可變來源快照 ID，供重放與版本追蹤。
  - `tenant_id` (string/UUID, required): 租戶隔離識別碼，非空。
  - `store_id` (string/UUID, required): 目標店家識別碼。
  - `opened_on` (date, required): 實體商業營運開店日（ISO 8601 YYYY-MM-DD），嚴禁由 `created_at` 推論。
  - `authority_type` (string, optional): 權威機構類別說明。
  - `provenance_note` (string, optional): 系統與紀錄定位資訊。
  - `inferred_from_created_at` (boolean, optional): 自宣告推論標記，僅允許 `false`；若為 `true` 則直接進隔離區 (quarantined)。
  - `created_at` (timestamp, optional): 上游建立時間戳記，僅供溯源用途，不得作為開店日。

### 2.2 契約註冊表更新 (`packages/schemas/source_contracts/index.json`)
- 將 `store_opening_authority_snapshot` 登錄於 `contracts` 清單，指派 `canonical_target="store"`、`integration_mode="backfill"`、`envelope="batch"`、`acquisition_method="manual"`。

### 2.3 歷史資產清冊更新 (`docs/design/emgi/v0.4.1/LEGACY_EXTERNAL_DATA_DISPOSITION.yaml`)
- 在 `frozen_surfaces` 中將 `packages/schemas/source_contracts/external/store_opening_authority_snapshot.json` 列入清單，並註記此為盤點修正（Inventory Amendment），非擴增未授權來源。既有 fail-closed 與來源開關維持不變。

### 2.4 合約測試 Fixtures (`tests/fixtures/source_data/external/`)
- `store_opening_authority_snapshot.valid.json`: 包含合法合成權威紀錄（明確標示 synthetic fixture，不偽造真實權威資料）。
- `store_opening_authority_snapshot.invalid.json`: 包含 5 種反向隔離情境（缺必要欄位、未核准來源 ID、自認從 created_at 推論、日期格式錯誤、null 租戶）。

### 2.5 合約與整合防護測試 (`tests/contract/` & `tests/integration/`)
- `tests/contract/test_ingestion_contracts.py`:
  - 驗證契約可被既有 loader 正確載入與解析。
  - 驗證 `source_id` 列舉與 `apps/data_platform/store_opening.py` 的 `APPROVED_STORE_OPENING_SOURCES` 集合完全一致。
  - 驗證契約必要欄位即為回填引擎強校驗欄位。
  - 驗證 valid fixtures 通過引擎校驗，且 invalid fixtures 均觸發 `UnauthoritativeStoreOpeningError`。
  - 驗證契約鎖定標準欄位名稱拼寫（如 `opened_on`、`store_id`），legacy alias 於 landing 階段即進行隔離。
- `tests/integration/test_external_provider_registry.py`:
  - `test_every_provider_references_a_published_source_contract`: 遍歷所有註冊之外部提供者，斷言其引用之 `source_contract_id` 均已在契約註冊表中發布且可載入，杜絕未發布契約的引用破口。
  - `test_store_opening_authority_provider_binds_to_its_published_contract`: 斷言 `store_opening_authority` 提供者正確綁定至已發布之 `store_opening_authority_snapshot` 契約。

---

## 3. 驗證與驗收矩陣 (Verification & Acceptance Matrix)

| 驗收條款 | 達成方式與證據 |
|---|---|
| 1. 核對 provider_registry 與 store_opening.py validator，新增可被 loader 解析之契約，不猜測不存在的來源 | 建立 `packages/schemas/source_contracts/external/store_opening_authority_snapshot.json`，對齊 `store_opening.py` 及 `provider_registry.py`，通過 `test_registry_index_lists_loadable_contracts`。 |
| 2. 契約必要欄位與 opened_on/tenant/store/authority 檢核相符，不能用 created_at 推論；補合法與非法 fixtures | 契約定義 `opened_on`, `snapshot_id`, `tenant_id`, `store_id`, `source_id` 為必填，`inferred_from_created_at` 限定 `false`；補齊 `.valid.json` 與 `.invalid.json` fixtures。 |
| 3. 加入測試避免 provider registry 再引用未登錄契約；既有 fail-closed 與開關不變 | 新增 `test_every_provider_references_a_published_source_contract` 與 `test_store_opening_authority_provider_binds_to_its_published_contract`。未開啟任何 connector 或修改生產設定。 |
| 4. 沿最新 dev 之乾淨 worktree，使用標準 task 流程交付 | 成功 merge `origin/dev` base advance (`c42b734ca26e`)，無 conflict，遵循 worker commit 與 task finalize 流程。 |
| 5. 合成 fixture 明示範圍，不宣稱真實資料已提供 | Fixture 明文註記 `Synthetic golden records` / `Synthetic rejection cases`，README 明記離線驗證性質。 |
| 6. 不啟用外部來源、不讀取秘密、不修改 production 設定 | 僅補齊 schema / registry / test 缺漏，不修改任何 production 權限、IAM、API keys 或 connector 啟用狀態。 |

---

## 4. 驗證指令 (Verification Commands)

本任務執行之驗證指令：

```bash
git diff --check
uv run pytest tests/contract/test_ingestion_contracts.py tests/integration/test_external_provider_registry.py tests/integration/test_store_opening_backfill.py -q
```

- `git diff --check`: Exit code 0
- `uv run pytest ...`: 148 passed, Exit code 0

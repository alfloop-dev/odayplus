# ODP-DATA-CATALOG-METADATA-ALIGNMENT-001: 讓來源契約可表達資料負責人與延遲需求，未知值保持未確認

- **Task ID**: `ODP-DATA-CATALOG-METADATA-ALIGNMENT-001`
- **Work Package**: WP-34 Follow-up ([ODP 人工決策落地規畫](../../../plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md) §6 WP-34; [ODP-CDC-SOURCE-CONTRACT-PREP-001 Implementation Handoff](../ODP-CDC-SOURCE-CONTRACT-PREP-001/implementation-handoff.md) §4 跟進項目 3)
- **負責人 (Owner)**: Antigravity5
- **審查人 (Reviewer)**: Codex2
- **交付狀態**: `IMPLEMENTATION_COMPLETED`
- **日期**: 2026-09-09

---

## 1. 任務背景與問題陳述 (Background & Problem Statement)

在 Phase 34A 來源契約盤點與 CDC 交接（`ODP-CDC-SOURCE-CONTRACT-PREP-001`）過程中，確認了以下資料目錄中繼資料與執行能力表示缺口：
1. **中繼資料字典缺口**：現有 16 個來源契約（8 個內部與 8 個外部）缺乏標準且向後相容的資料負責人（`data_owner`）、目標延遲 SLA（`target_latency_sla`）與聯繫窗口（`contact_channel` / `contact_ref`）宣告。
2. **未知值需保持未確認**：在業務負責人與正式 SLA 尚未提供前，不得猜測或填寫虛假姓名、SLA、來源授權或連線方式；系統必須明確以 `unconfirmed` / `unknown` 呈現，並於載入器中強拒絕空白字串假 owner 與無效延遲格式。
3. **整合模式與執行能力解耦 (Decoupling Integration Mode & Runtime Capability)**：在可機讀中繼資料中區分宣告的目標整合模式（`integration_mode`，例如 `event_stream`）與現有執行層能力（`runtime_capability`，例如 `batch_watermark_only`）。特別針對 `machine_status_event`，既不刪除或放棄 CDC / event_stream 需求，亦不因契約宣告而冒稱現有批次水位線路徑已具備 streaming 執行能力。

---

## 2. 交付成果與架構對齊 (Deliverables & Architecture Alignment)

本任務完成以下交付，嚴格遵守既有語意與驗收標準：

### 2.1 領域契約模型擴充 (`modules/integration/domain/contracts.py`)
- **中繼資料屬性加入 `SourceContract`**：
  - `data_owner: str = "unconfirmed"`：資料負責人，未確認時預設為 `"unconfirmed"`。
  - `target_latency_sla: str = "unconfirmed"`：資料延遲 SLA，支援 ISO 8601 時長（如 `PT1H`、`PT1H30M`、`P1D`、`P1Y2M3D`、`P1DT12H`）、單位時長（如 `5s`、`15m`、`24h`、`7d`）、標準頻率關鍵字（如 `realtime`、`streaming`、`daily`、`batch_daily`）或 `unconfirmed`/`unknown`。
  - `contact_channel: str = "unconfirmed"`：聯繫窗口（如 `slack:#data-ops` 或 `email:ops@example.com`），同時提供 `contact_ref` 與 `contact_reference` 別名屬性。
  - `runtime_capability: str = "unverified"`：機讀執行能力標記，列舉值涵蓋 `unverified`、`unconfirmed`、`verified`、`supported`、`batch_watermark_only`、`batch_only`、`streaming_supported`、`manual_attestation`、`simulated_only`、`unsupported`。
- **嚴格校驗規則 (Validation Enforcement)**：
  - `_validate_data_owner`：強拒絕空白字元假 owner（如 `"   "`）或非字串型別，拋出 `ContractError`。
  - `_validate_target_latency_sla`：以正規表達式校驗延遲規格，要求 ISO duration 必須具備至少一個合法數值單位（強拒絕空 duration `P`、`PT`、`P1`、`PT1`、負數時長 `-5s`、空白字串或無效關鍵字 `asap`、`fast`），拋出 `ContractError`。
  - `_validate_contact_channel`：強拒絕空白字元或非字串型別。
  - `_validate_runtime_capability`：僅允許預定義 `RUNTIME_CAPABILITIES` 詞彙。
- **輔助判定屬性 (Status & Guard Properties)**：
  - `is_data_owner_confirmed`：判定負責人是否為具名實體（非 `unconfirmed`/`unknown`/`unspecified`）。
  - `is_latency_sla_confirmed`：判定 SLA 是否為已確認規格（空/非法格式均在驗證期拒絕，不進入 confirmed 狀態）。
  - `is_contact_confirmed`：判定聯繫管道是否已確認。
  - `is_runtime_verified`：判定執行能力是否經實體驗證。
  - `has_streaming_runtime`：防範 False Readiness，若 `runtime_capability` 僅為 `batch_watermark_only` 則回傳 `False`。
  - `runtime_matches_declared_mode`：依明確之 capability × declared-mode 對應表判定：
    - `unsupported`, `simulated_only`, `unverified`, `unconfirmed` 對任何模式均為 `False`；
    - `batch_only`, `batch_watermark_only` 僅匹配批次模式（`batch_snapshot`, `incremental_batch`, `backfill`），不匹配 `api_lookup` 或 `event_stream`；
    - `supported` 匹配批次與 `api_lookup` 模式，不匹配 `event_stream`；
    - `streaming_supported` 匹配 `event_stream`；
    - `verified` 匹配所有宣告模式；
    - `manual_attestation` 匹配人工/批次/回填模式，不匹配 `event_stream`。
- **完全向後相容**：若契約 JSON 缺漏上述欄位，載入器平滑採用 `unconfirmed` / `unverified` 預設值，不中斷現有讀取與測試流程。

### 2.2 來源契約註冊表更新 (`packages/schemas/source_contracts/index.json`)
- 版本推進至 `0.2.0`。
- 新增 `runtime_capabilities` 分類詞彙清單。
- 註冊表中 16 個契約項目均明確標註 `data_owner`、`target_latency_sla`、`contact_channel` 與 `runtime_capability`。
- 特別註記 `machine_status_event` 之 `runtime_capability="batch_watermark_only"` 與 `integration_mode="event_stream"`。

### 2.3 來源契約定義更新 (`packages/schemas/source_contracts/internal/` & `external/`)
- 8 個內部契約與 8 個外部契約均明確填入中繼資料欄位，未決策欄位嚴格標示 `"unconfirmed"`，不填寫未驗證之假資料。
- 更新 `packages/schemas/source_contracts/README.md` 說明中繼資料規範與執行能力解耦設計。

### 2.4 完整合約與中繼資料測試套件 (`tests/contract/test_source_contract_metadata.py`)
- 涵蓋 10 大核心維度測試：
  1. 所有註冊契約均具備中繼資料欄位與合法型別。
  2. 缺欄向後相容性與預設值正確性。
  3. 未確認 (`unconfirmed`/`unknown`/`unspecified`) 與已確認值的判別屬性測試。
  4. 空白假 owner (`"   "`) 與非字串型別之拒絕測試。
  5. 非法延遲值（`P`、`PT`、`P1`、`PT1`、`-5s`、`asap`、`fast`、`12345`、空白字元）之拒絕測試與合法延遲格式（含完整 ISO 時長如 `PT1H30M`, `P1DT12H`）接受測試。
  6. 空白 ISO duration (`P`, `PT`) 拒絕回歸測試。
  7. 空白聯繫窗口之拒絕與別名 (`contact_ref`) 支援測試。
  8. 無效 `runtime_capability` 之拒絕測試。
  9. `machine_status_event` 之 `event_stream` 與 `batch_watermark_only` 解耦斷言，防止 false readiness 同時保留 CDC 需求。
  10. `runtime_matches_declared_mode` 完整 capability × declared-mode 矩陣斷言。
  11. `index.json` 分類詞彙與契約中繼資料一致性。

---

## 3. 驗收標準核對 (Acceptance Criteria Alignment)

| 驗收條款 | 達成方式與驗證結果 |
|---|---|
| **1. 接續 CDC handoff §4 metadata 缺口，在現有 SourceContract/loader 增加向後相容欄位，舊契約缺欄以明確 unknown/unconfirmed 表示** | `SourceContract` 擴充 `data_owner`, `target_latency_sla`, `contact_channel` (及別名 `contact_ref`)，缺欄預設為 `"unconfirmed"`，通過 `test_all_registered_contracts_have_catalog_metadata` 與 `test_backward_compatibility_omitted_metadata`。 |
| **2. 不猜測姓名、SLA、來源授權或連線方式；補驗證拒絕非法延遲值、空白假 owner，保留 unknown 與已確認的區別，對既有契約可正常載入** | 實作 `_validate_data_owner` 與 `_validate_target_latency_sla`，拒絕空白字串、非法格式與空 ISO 時長（`P`/`PT`）；既有契約與 fixture 正常載入，通過 `test_validation_rejects_blank_fake_owner`、`test_validation_rejects_illegal_latency_sla` 與 `test_validation_rejects_empty_iso_duration`。 |
| **3. 同時在可機讀欄位區分宣告的 integration_mode 與尚未驗證的 runtime capability，不能把 machine_status_event 宣告的 event_stream 當成現有批次路徑已支援 streaming；不把原 requirement 改為不做 CDC** | `machine_status_event` 維持 `integration_mode="event_stream"`，同時標註 `runtime_capability="batch_watermark_only"`；`has_streaming_runtime` 與 `runtime_matches_declared_mode` 斷言為 `False`，明確 capability × mode 對應通過 `test_machine_status_event_decoupling_event_stream_vs_batch_runtime` 與 `test_runtime_matches_declared_mode_matrix`。 |
| **4. 因會寫 source_contracts/index，等 store-opening schema 任務合併後接續** | 確認已以最新 `dev`（含 `ODP-SCHEMA-STORE-OPENING-AUTHORITY-001`）為基準，`store_opening_authority_snapshot` 完整保留並附帶 catalog 中繼資料。 |
| **5. 沿最新 dev 乾淨 task worktree 與既有流程交付** | 使用 `task_start.sh`、`worker_commit.py`、`task_finalize.sh` 進行交付。 |
| **6. 接續已定案決策的工程工作，不以重寫規畫或交接文件替代程式交付** | 完成實質 Python dataclass、JSON schema、index registry 與完整 pytest 測試套件。 |
| **7. 使用既有 canonical writer 記錄 lifecycle，不手改 ai-status** | 嚴格透過 `AI_NAME=Antigravity5 "$PANTHEON_STATUS_ROOT/scripts/ai-status.sh"` 與 toolchain 操作。 |
| **8. 測試使用合成或已在 repo 的 fixture 並明示範圍** | 測試純屬離線合約與單元驗證，不宣稱真實資料已提供或 live 能力已驗證。 |
| **9. 不啟用外部來源、不讀取秘密、不連線 production、保存原始 exit code** | 所有驗證指令均本地執行，保存 exact head SHA 與真實 exit code。 |

---

## 4. 驗證指令與執行收據 (Verification Commands & Receipts)

本任務宣告之驗證指令：
1. `git diff --check`
2. `uv run pytest tests/contract/test_source_contract_metadata.py tests/contract/test_ingestion_contracts.py -q`

### 執行收據：
- `git diff --check`: Exit code 0 (乾淨無 whitespace/conflict 標記)
- `uv run pytest tests/contract/test_source_contract_metadata.py tests/contract/test_ingestion_contracts.py -q`:
  - 166 passed in ~0.6s
  - Exit code 0

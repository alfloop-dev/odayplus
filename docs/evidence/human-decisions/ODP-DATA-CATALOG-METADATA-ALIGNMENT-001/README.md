# ODP-DATA-CATALOG-METADATA-ALIGNMENT-001: 讓來源契約可表達資料負責人與延遲需求，未知值保持未確認

- **Task ID**: `ODP-DATA-CATALOG-METADATA-ALIGNMENT-001`
- **Work Package**: WP-34 Follow-up ([ODP 人工決策落地規畫](../../../plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md) §6 WP-34; [ODP-CDC-SOURCE-CONTRACT-PREP-001 Implementation Handoff](../ODP-CDC-SOURCE-CONTRACT-PREP-001/implementation-handoff.md) §4 跟進項目 3)
- **實作交付 (Implementation by)**: Antigravity5（commit `adc8d96a`、`a038e308`）；reviewer finding F1/P2 之修正由 Claude2 實作（commit `4579ab26`，見 §5）
- **現任負責人 (Current Owner)**: Claude2 — 原 owner Antigravity5 於 2026-09-09T03:15:35Z 因 dispatch-paused 由 orchestrator 自動改派，Claude2 接手驗證與收尾送審，未重寫其實作
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
  - `_validate_data_owner`：僅「欄位省略」或「明確 `null`」採用 `"unconfirmed"` 預設；任何 present-but-blank 值（空字串 `""`、`"   "`、`"\t\n "`）與非字串型別一律拋出 `ContractError`。空字串一度被吞成 `unconfirmed`，該缺陷與其修正記於 §5。
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
  4. 空白假 owner（空字串 `""`、`"   "`、`"\t\n "`）與非字串型別之拒絕測試；另有正例測試確保「省略欄位」與「明確 `null`」仍為 `unconfirmed`、具名 owner 仍為 confirmed。
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
| **2. 不猜測姓名、SLA、來源授權或連線方式；補驗證拒絕非法延遲值、空白假 owner，保留 unknown 與已確認的區別，對既有契約可正常載入** | 實作 `_validate_data_owner` 與 `_validate_target_latency_sla`，拒絕空白字串、非法格式與空 ISO 時長（`P`/`PT`）；既有契約與 fixture 正常載入，通過 `test_validation_rejects_blank_fake_owner`、`test_omitted_or_null_owner_stays_unconfirmed`、`test_validation_rejects_illegal_latency_sla` 與 `test_validation_rejects_empty_iso_duration`。**此條在 head `99ab7ec7` 之前並未真正成立**：`data_owner=""` 會被吞成 `unconfirmed`，僅 `"   "` 被拒。commit `4579ab26` 修正後才符合驗收，詳見 §5。 |
| **3. 同時在可機讀欄位區分宣告的 integration_mode 與尚未驗證的 runtime capability，不能把 machine_status_event 宣告的 event_stream 當成現有批次路徑已支援 streaming；不把原 requirement 改為不做 CDC** | `machine_status_event` 維持 `integration_mode="event_stream"`，同時標註 `runtime_capability="batch_watermark_only"`；`has_streaming_runtime` 與 `runtime_matches_declared_mode` 斷言為 `False`，明確 capability × mode 對應通過 `test_machine_status_event_decoupling_event_stream_vs_batch_runtime` 與 `test_runtime_matches_declared_mode_matrix`。 |
| **4. 因會寫 source_contracts/index，等 store-opening schema 任務合併後接續** | 確認已以最新 `dev`（含 `ODP-SCHEMA-STORE-OPENING-AUTHORITY-001`）為基準，`store_opening_authority_snapshot` 完整保留並附帶 catalog 中繼資料。 |
| **5. 沿最新 dev 乾淨 task worktree 與既有流程交付** | 使用 `task_start.sh`、`worker_commit.py`、`task_finalize.sh` 進行交付。 |
| **6. 接續已定案決策的工程工作，不以重寫規畫或交接文件替代程式交付** | 完成實質 Python dataclass、JSON schema、index registry 與完整 pytest 測試套件。 |
| **7. 使用既有 canonical writer 記錄 lifecycle，不手改 ai-status** | 嚴格透過 canonical writer `"$PANTHEON_STATUS_ROOT/scripts/ai-status.sh"` 與既有 toolchain 操作：實作階段以 `AI_NAME=Antigravity5`，改派後之驗證與送審以 `AI_NAME=Claude2`。未手改 `ai-status.json`／archive，未冒稱 Human/Ops 簽署。 |
| **8. 測試使用合成或已在 repo 的 fixture 並明示範圍** | 測試純屬離線合約與單元驗證，不宣稱真實資料已提供或 live 能力已驗證。 |
| **9. 不啟用外部來源、不讀取秘密、不連線 production、保存原始 exit code** | 所有驗證指令均本地執行，保存 exact head SHA 與真實 exit code。 |

---

## 4. 驗證指令與執行收據 (Verification Commands & Receipts)

本任務宣告之驗證指令：
1. `git diff --check`
2. `uv run pytest tests/contract/test_source_contract_metadata.py tests/contract/test_ingestion_contracts.py -q`

### 4.1 執行收據（修正後最新程式 head）

量測 head SHA：`4579ab260c5bd0e11714cafaa0099cd45042d324`（§5 修正 commit；本收據所綁定之 exact head）

以 `python3 delivery_toolchain/git/task_verification.py run --task-id ODP-DATA-CATALOG-METADATA-ALIGNMENT-001` 執行兩條宣告指令，收據落在 `.orchestrator/evidence/`（該目錄不在本 repo 追蹤範圍內）：

| 指令 | Exit code | Duration | 收據 ID |
|---|---|---|---|
| `git diff --check` | 0 | 0.021s | `0266c84da54c6218` |
| `uv run pytest tests/contract/test_source_contract_metadata.py tests/contract/test_ingestion_contracts.py -q` | 0 | 9.687s | `d029f016dd12b0ab` |

- 兩筆皆為 `run_kind=baseline`、`attempt=1`、`signal=None`、`timed_out=false`，非重跑，故無 retry reason。
- pytest 收據的 `output_tail` 為 166 個 `.`（72 + 72 + 22），對應 §5 新增 1 個測試函式後由 165 增為 166。與先前一致，`addopts = "-q"` 疊加宣告指令的 `-q` 成 `-qq`，故無 `N passed in Xs` 摘要行；通過判定依據為收據記載之 **exit code 0**。
- `duration_seconds` 為 task_verification 量測之牆鐘時間，包含 `uv` 解析環境的開銷，不等同純測試執行時間。
- 本文件之後不再有程式或測試變更；記錄本節的 evidence-only commit 會產生新的 head，該 head 於送審前依同一 selection 重跑 task_verification，其 exit code 以 `.orchestrator/evidence/` 之收據為準。

### 4.2 執行收據（修正前 head，保留為歷史）

量測 head SHA：`a038e30854dcc0b98c6744e8090856158b6de761`（此收據綁定之 exact head；**早於** §5 的缺陷修正）

| 指令 | Exit code | 結果 |
|---|---|---|
| `git diff --check` | 0 | 乾淨，無 whitespace／conflict 標記 |
| `uv run --frozen pytest tests/contract/test_source_contract_metadata.py tests/contract/test_ingestion_contracts.py -q` | 0 | 165 passed（165 collected，全數 `.`，無 F/E/s） |

補充說明（量測方式與已修正的先前誤記）：
- 先前版本收據記為「166 passed」，與實際不符。以 `pytest --collect-only` 量得本 selection 為 **165 tests collected**，執行輸出亦為 165 個 `.`，故正確數字為 **165 passed**。
- `pyproject.toml` 已設定 `addopts = "-q"`，宣告指令再帶 `-q` 會疊加成 `-qq`，因而**不會**輸出 `N passed in Xs` 摘要行。本收據的通過判定依據為背景 job handle 回報之 **exit code 0**，測試數量另以 `--collect-only` 與輸出的 `.` 計數佐證，並非依賴摘要行。
- 因無摘要行，先前記載之 `~0.6s` 執行時間無可靠來源，故不再宣稱具體耗時，記為 unknown。
- 以 `uv run --frozen` 執行；直接使用裸 `python3 -m pytest` 在本環境無法重現。

### 補充回歸檢查（非宣告指令，接手後自行加驗）

宣告的兩個測試檔未涵蓋其他 `SourceContract` / `source_contracts` 消費端。為確認本次 loader 與 registry 變更未造成回歸，於同一 head `a038e308` 另跑一次相鄰消費端 selection：

```
uv run --frozen pytest tests/integration/test_int001_cdc_disposition.py \
  tests/integration/test_external_provider_registry.py \
  tests/architecture/test_external_data_boundary.py \
  tests/contract/test_oday_data_contract_pin.py \
  tests/contract/test_oday_data_product_contract_pin.py \
  apps/data_platform/tests/test_mapping.py \
  apps/data_platform/tests/test_pipeline.py
```

- Exit code 0，`234 passed in 77.69s`。
- 範圍聲明：以上皆為離線契約／單元／架構測試，使用 repo 既有 fixture 與合成資料。**不代表**真實上游資料已提供、CDC 串流已建置，或任何 live 執行能力已驗證；`machine_status_event` 的 `runtime_capability` 仍如實記為 `batch_watermark_only`。
- 這份補充回歸是在 head `a038e308` 量的，**早於** §5 的修正，未在 `4579ab26` 重跑。就此次修正而言其涵蓋性未受影響：`4579ab26` 只讓 `data_owner` 的 present-but-blank 值由「被吞成 unconfirmed」改為 `ContractError`，而 repo 內沒有任何契約或 fixture 宣告空字串 `data_owner`（見 §5 掃描結果），故上述 selection 的載入路徑不變。

---

## 5. Reviewer Finding F1/P2：空字串 data_owner 被吞成 unconfirmed（已修正）

### 5.1 缺陷

`modules/integration/domain/contracts.py::_validate_data_owner` 原本以

```python
if raw is None or raw == "":
    return UNCONFIRMED_METADATA
```

同時處理「欄位不存在」與「欄位存在但為空字串」。後者是 present-but-blank——契約明確寫了 `"data_owner": ""`——卻被讀回成 `"unconfirmed"`，抹掉本任務要保護的「未確認 vs. 假填」區別。只有 `"   "` 這種純空白會落到既有的 stripped 空值拒絕。

head `99ab7ec7` 的文件修訂記錄了此 finding，但沒有改到程式；§3 驗收條款 2 因此在該 head 上並不成立。

### 5.2 修正前的複驗（head `99ab7ec7`，純合成 contract dict，未觸及任何外部來源）

| 輸入 | 結果 |
|---|---|
| 省略 `data_owner` | accepted，`unconfirmed`，`is_data_owner_confirmed=False` |
| `null` | accepted，`unconfirmed`，`is_data_owner_confirmed=False` |
| `""`（空字串） | **accepted，`unconfirmed`** ← 缺陷 |
| `"   "` | rejected，`ContractError` |
| `"unconfirmed"` | accepted，`unconfirmed`，`is_data_owner_confirmed=False` |
| `"Store Operations Team"` | accepted，`is_data_owner_confirmed=True` |

### 5.3 修正（commit `4579ab26`）

- 移除 `raw == ""` 的 default 分支：只有 `raw is None`（欄位省略或明確 null）採用 `unconfirmed` 預設，空字串落入既有 `stripped` 空值拒絕。
- 錯誤訊息改為指名 blank 情況並說明兩種合法的「尚無 owner」表示法（省略欄位或 `null`），仍保留 `fake blank owner` 字樣。
- `tests/contract/test_source_contract_metadata.py::test_validation_rejects_blank_fake_owner` 新增 `""` 與 `"\t\n "` 回歸；新增 `test_omitted_or_null_owner_stays_unconfirmed` 保留 omitted／null 正例與具名 owner 的 confirmed 判定。
- `packages/schemas/source_contracts/README.md` 原文只寫「Empty whitespace fake owners are rejected」，低估了規則範圍，改為明述僅省略或 `null` 走 unconfirmed 預設。

修正後同一組合成輸入：`""` 由 accepted 轉為 rejected（`ContractError`），其餘五列不變。

### 5.4 相容性

`grep` 全 repo（排除 `.git/`、`.orchestrator/`）後，宣告 `data_owner` 的只有 `packages/schemas/source_contracts/` 下 16 個契約與 `index.json`，值皆為 `"unconfirmed"`，無任何空字串；其餘命中的 `data_owner` 是 API 測試的 `x-roles` 角色名稱，與本 loader 無關。故 16 個註冊契約載入行為不變，`test_all_registered_contracts_have_catalog_metadata` 續為綠。

### 5.5 未一併修改的相鄰處（明確聲明）

`_validate_target_latency_sla` 與 `_validate_contact_channel` 有相同的 `raw == ""` default 分支，本次**未**更動：finding 與本任務驗收條款只針對 `data_owner`，而改動 SLA／contact 的解析語意會動到 reviewer 已審過的行為。若要一併收斂，應以獨立 task 處理。

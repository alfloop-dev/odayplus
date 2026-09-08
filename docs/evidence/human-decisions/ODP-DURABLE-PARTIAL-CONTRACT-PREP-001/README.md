# ODP-DURABLE-PARTIAL-CONTRACT-PREP-001 — 真實 Durable Job 候選與 PARTIAL／Receipt／Retry 契約及 H06 請求

- **任務識別碼**：`ODP-DURABLE-PARTIAL-CONTRACT-PREP-001`
- **工作包代號**：`WP-33A`（Phase A：工程盤點、契約草案與人工決策請求）
- **日期**：2026-09-08
- **任務負責人**：Antigravity2
- **審查人**：Codex2
- **檢驗基準代碼（Inspected HEAD SHA）**：`b6b729d95e575dc3b27ea9e02ce22fb128c3970b`
- **檢驗時間（UTC）**：`2026-09-08T16:48:00Z`
- **歷史證據參照**：`ODP_JOB_PARTIAL_PRODUCER_EVIDENCE_2026-09-03.md`（基準：`04e1572f802a54c2646ba678fe2975226dfbd7c4`，日期：2026-09-03）及 `ODP_JOB_PARTIAL_DISPOSITION_2026-09-03.md`
- **關聯需求**：`ODP-FR-SHARED-001`（所有長時間任務都能查詢 QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED/PARTIAL）
- **決策依據**：使用者人工決策 D19（選項 A：實作 partial／receipt／retry）

---

## 1. 任務執行摘要與交付產物索引

本任務（WP-33A）為執行《ODP 人工決策落地與 Supervisor／Auto Worker 執行規畫》中關於 `ODP-FR-SHARED-001` PARTIAL 狀態轉移之準備階段。工程團隊於獨立工作樹中完成全庫 static inventory 盤點、草擬非同步長任務明細收據與重試契約，並產出 H06 人工決策請求書，作為後續 WP-33B 實作之依據。

本交付目錄包含以下 5 項核心產物：

| 檔案名稱 | 產物類型 | 核心內容說明 |
|---|---|---|
| [README.md](./README.md) | 總索引與說明文件 | 本任務執行摘要、盤點事實總結、產物索引、採集收據與驗證收據（本文件） |
| [producer-inventory.json](./producer-inventory.json) | 靜態盤點數據 | 代碼庫中全部 3 個 default registry jobs、3 項同步 partial 操作、2 項 receipt envelope 排除點與 11 個模組 worker 入口之靜態結構清單及採集收據 |
| [partial-retry-contract-draft.json](./partial-retry-contract-draft.json) | 契約草案 | 包含 `JobStatus.PARTIAL`、逐項明細收據（`ItemReceipt`，支援 `attempt >= 0`、取消語意）、差異化重試（`retry_scope="FAILED_ONLY"`）與狀態優先級收斂之完整 JSON Schema 規格 |
| [human-input-request-H06.md](./human-input-request-H06.md) | 人工決策請求 | 提出 3 項候選業務長任務（推薦 `batch-listing-intake`），請求 Human/Ops / 產品負責人正式核定 33B 實作標的 |
| [implementation-handoff.md](./implementation-handoff.md) | 工程交接說明 | 定義 WP-33B 實作入場條件、交付清單、反事實驗收標準（四項反事實測試）與治理規範 |

---

## 2. 靜態代碼盤點核心結論（Static Inventory Findings）

經針對代碼樹（SHA: `b6b729d95e575dc3b27ea9e02ce22fb128c3970b`）進行全樹檢索與語法分析（執行期權限標記為 `static_only_no_production_runtime_access`）：

1. **無可達之 `JobStatus.PARTIAL` 寫入點**：
   - 全庫 Python、TypeScript/TSX 原始碼中，無任何 Production Worker Handler 或 Queue 狀態轉移會寫入 `JobStatus.PARTIAL`（靜態 `rg` 檢索 0 命中，exit code 1）。
2. **Default Worker Registry 僅註冊 3 個單一實體任務**：
   - `forecast`（`apps/worker/oday_worker/handlers.py:255`，enqueue 於 `apps/api/oday_api/main.py:997-1044`）：針對單一門市時序驗證，結果為二元 `SUCCEEDED` / `FAILED`。
   - `external-fetch`（`apps/worker/oday_worker/handlers.py:256`，enqueue 於 `apps/scheduler/oday_scheduler/main.py:180-195` 及 `apps/api/oday_api/main.py:1013-1044`）：針對單一 provider/window 排程抓取，結果為二元 `SUCCEEDED` / `FAILED`。
   - `assisted-listing-intake`（`apps/worker/oday_worker/handlers.py:257`，實作於 `apps/worker/assisted_listing_intake/worker.py:80-221`，enqueue 於 `modules/opsboard/application/network_listings.py:1243-1249`，1210 行為 correlationId 初始化）：針對單一房源 URL 爬蟲，單一實體無成員聚合。
3. **生產者至狀態寫入點全鏈追溯（Traceability）**：
   - 各任務均經由 `JobRequest` 入隊（`shared/jobs/queue.py:145-208`，`shared/infrastructure/persistence/job_queue.py:120-173`），由 `apps/worker/oday_worker/main.py` 派工執行，並由 `job_queue.update_status` 寫入終態（`apps/worker/oday_worker/main.py:141-146, 210-222` -> `shared/infrastructure/persistence/job_queue.py:395-436`）。
4. **排除收據持久化專用 Envelope**：
   - `TenantScopedJobReceiptStore`（`shared/infrastructure/persistence/job_receipts.py:74-105`）與 `TenantScopedCommandReceiptStore`（`shared/infrastructure/persistence/command_receipts.py:70-105`）以 `shared/jobs/queue.py:10-18` 排除於 worker 派工之外，僅供讀回。
5. **區分非 Durable Job 之部分成功操作**：
   - `POST /api/v1/intake-batches`（`apps/api/app/routes/listings.py:2225-2265`，helper 於 1730-1799 行）具備 207 Multi-Status 與 `(accepted_count, rejected_count)`，但為同步 API 命令收據，無隊列 `job_id`。
   - `xlsx_import.py`（`modules/external_data/application/xlsx_import.py:816-933`）之 partial commit 與 `ingestion_store.py`（`modules/external_data/application/ingestion_store.py:90-123`）之 `accepted_count` / `quarantined_count` 亦非 Durable Worker Job。
6. **型別與交付邊界分離（Outcome vs Delivery State）**：
   - `JobStatus`（業務完成結果：`QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED`, `PARTIAL`）與 `JobDeliveryState`（基礎設施交付狀態：`RETRYING`, `DEAD_LETTER`）維持正交分離。
   - 現行程式中：`JobStatus.SUCCEEDED` 自動清除 `delivery_state = NULL`（`shared/infrastructure/persistence/job_queue.py:402-406`）；可重試異常寫入 `JobStatus.QUEUED` + `JobDeliveryState.RETRYING`；重試耗盡或非可重試異常寫入 `JobStatus.FAILED` + `JobDeliveryState.DEAD_LETTER`（`apps/worker/oday_worker/main.py:200-218`）。
   - 契約中定義：WP-33B 批次長任務完成多項目處理並收斂為 `JobStatus.PARTIAL` 終態時，因傳遞已結束，外層框架明確清除 `delivery_state = None`（`NULL`），避免將業務部分成功誤判為基礎設施死信。

詳細盤點欄位請參閱 [producer-inventory.json](./producer-inventory.json)。

---

## 3. 契約設計與業務候選評估

### 3.1 契約設計要點
- **明細收據（Itemized Receipt）**：收據結構包含 `items` 陣列，每筆成員具備 `item_id`, `item_status` (`SUCCEEDED` | `FAILED` | `CANCELLED` | `PENDING`), `attempt` (`minimum: 0`), `result_ref`, `error` (`code`, `message`, `retryable`)。未執行之取消項目允許 `attempt = 0`。
- **無歧義狀態聚合優先級（Precedence Rules）**：
  1. 空批次優先：`total_count == 0` => `JobStatus.SUCCEEDED`（各 count 為 0）。
  2. 未完成防終態：`pending_count > 0` => 保持 `RUNNING`/`QUEUED`，禁止標記終態。
  3. 取消優先：`cancelled_count > 0` 或主動中斷 => `JobStatus.CANCELLED`。
  4. 全數成功：`succeeded_count == total_count > 0` => `JobStatus.SUCCEEDED`。
  5. 全數失敗：`failed_count == total_count > 0` => `JobStatus.FAILED`。
  6. 部分成功：`succeeded_count > 0` 且 `failed_count > 0` => `JobStatus.PARTIAL`。
- **差異化重試（Scoped Replay）**：支援 `retry_scope="FAILED_ONLY"`，重試引擎直接跳過已 `SUCCEEDED` 之項目（0 次調用），亦跳過 `FAILED` 但 `retryable == false` 之永久資料錯誤項目（0 次調用），僅對 `FAILED` 且 `retryable == true` 之暫態錯誤項目重新運算（1 次調用）。
- **狀態收斂（Convergence）**：暫態失敗項目重試成功後更新為 `SUCCEEDED`；若所有失敗項皆重試成功，整體 JobStatus 由 `PARTIAL` 收斂轉移為 `SUCCEEDED`。

詳細 Schema 請參閱 [partial-retry-contract-draft.json](./partial-retry-contract-draft.json)。

### 3.2 候選業務任務
在 [human-input-request-H06.md](./human-input-request-H06.md) 中，工程團隊評估了三項候選任務：
- **候選一（推薦）：`batch-listing-intake`**（將同步 207 批次房源匯入昇格為非同步 Durable Batch Job）。
- **候選二：`multi-partition-external-fetch`**（多來源／分區外部資料排程攝取容錯）。
- **候選三：`batch-sitescore-evaluation`**（批次門市選址評估運算）。

---

## 4. 階段劃分與 WP-33B 交接

本任務屬於 **A 階段（工程準備）**，不代表業務功能已於 Production 實現。下一階段 **WP-33B** 需在 H06 完成具名簽核後入場，並遵循 [implementation-handoff.md](./implementation-handoff.md) 所載之 4 項反事實驗收標準：
1. **狀態轉移精確性與成員明細驗證**（8 成功、1 永久失敗、1 暫態失敗 => `PARTIAL`）。
2. **差異化重試不重複執行與收斂驗證**（成功項 0 次調用、永久失敗項 0 次調用、暫態失敗項 1 次調用）。
3. **交付狀態與業務結果正交分離驗證**（隊列重試 `RETRYING` 不破壞業務狀態；死信 `DEAD_LETTER` 僅配 `FAILED`；`PARTIAL` 終態清除 `delivery_state`）。
4. **重啟回讀與取消／未執行項目驗證**（未執行項目 `attempt = 0` 及取消收據回讀）。

---

## 5. 靜態調查採集收據（Evidence Collection Receipts）

以下採集指令於當前工作樹執行，驗證代碼庫真實狀態：

```bash
# 1. 查證代碼庫中無任何寫入 JobStatus.PARTIAL 的生產者 (exit=1, 0 命中)
rg -n --glob '*.py' --glob '*.ts' --glob '*.tsx' 'JobStatus\s*\.\s*PARTIAL' apps modules shared packages

# 2. 查證 default worker registry 僅註冊 3 個 job handler (exit=0)
rg -n --glob '*.py' 'registry\.register\(' apps/worker
# 輸出:
# apps/worker/oday_worker/handlers.py:255:    registry.register(FORECAST_JOB_TYPE, handle_forecast)
# apps/worker/oday_worker/handlers.py:256:    registry.register(EXTERNAL_FETCH_JOB_TYPE, handle_external_fetch)
# apps/worker/oday_worker/handlers.py:257:    registry.register(INTAKE_JOB_TYPE, handle_assisted_listing_intake)

# 3. 查證生產環境 JobRequest enqueue 站點 (exit=0)
rg -n --glob '*.py' 'JobRequest\(' apps modules shared | rg -v '/tests?/|test_'
# 輸出:
# shared/infrastructure/persistence/command_receipts.py:74:                JobRequest(
# shared/infrastructure/persistence/job_receipts.py:75:            JobRequest(
# modules/opsboard/application/network_listings.py:1244:                    JobRequest(
# apps/api/oday_api/main.py:1038:                JobRequest(
# apps/scheduler/oday_scheduler/main.py:181:                    JobRequest(

# 4. 查證持久化狀態寫入呼叫點 (exit=0)
rg -n --glob '*.py' 'job_queue\.update_status\(' apps modules shared | rg -v '/tests?/|test_'
# 輸出:
# apps/worker/oday_worker/main.py:141:                    self.job_queue.update_status(
# apps/worker/oday_worker/main.py:210:                        self.job_queue.update_status(
# apps/worker/assisted_listing_intake/worker.py:210:            job_queue.update_status(
```

---

## 6. 本地驗證收據（Verification Receipts）

本任務產物通過以下指令驗證：

```bash
# 1. 驗證 Git diff 格式規範
git diff --check

# 2. 驗證產物完整性、JSON 格式、README 索引與 Markdown 連結
python3 -c 'import json,re,sys
from pathlib import Path
from urllib.parse import unquote,urlsplit
names=["docs/evidence/human-decisions/ODP-DURABLE-PARTIAL-CONTRACT-PREP-001/README.md", "docs/evidence/human-decisions/ODP-DURABLE-PARTIAL-CONTRACT-PREP-001/producer-inventory.json", "docs/evidence/human-decisions/ODP-DURABLE-PARTIAL-CONTRACT-PREP-001/partial-retry-contract-draft.json", "docs/evidence/human-decisions/ODP-DURABLE-PARTIAL-CONTRACT-PREP-001/human-input-request-H06.md", "docs/evidence/human-decisions/ODP-DURABLE-PARTIAL-CONTRACT-PREP-001/implementation-handoff.md"]
errors=[]
files=[]
for name in names:
    path=Path(name)
    if name.endswith("/"):
        if not path.is_dir() or not any(f.is_file() and f.stat().st_size for f in path.rglob("*")): errors.append("missing or empty directory: "+name)
    elif not path.is_file() or not path.stat().st_size: errors.append("missing or empty file: "+name)
    else: files.append(path)
for path in files:
    if path.suffix==".json":
        try:
            value=json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value,(dict,list)) or not value: errors.append("expected nonempty JSON object or array: "+str(path))
        except (ValueError,UnicodeError) as exc: errors.append(str(path)+": "+str(exc))
readme=Path(names[0])
if readme.is_file():
    body=readme.read_text(encoding="utf-8")
    for name in names[1:]:
        if Path(name).name not in body: errors.append("README does not index artifact: "+name)
for path in files:
    if path.suffix==".md":
        for target in re.findall(r"!?\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)",path.read_text(encoding="utf-8")):
            target=target.strip("<>")
            parsed=urlsplit(target)
            if not parsed.scheme and not parsed.netloc and parsed.path and not parsed.path.startswith("/"):
                if not (path.parent/unquote(parsed.path)).exists(): errors.append("broken local link: "+str(path)+" -> "+target)
if errors:
    print("\n".join(errors),file=sys.stderr)
    raise SystemExit(1)
print("Validated "+str(len(names))+" required artifacts, JSON contents, README index, and local Markdown links")'
```

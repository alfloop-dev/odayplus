# ODP-DURABLE-PARTIAL-CONTRACT-PREP-001 — 真實 Durable Job 候選與 PARTIAL／Receipt／Retry 契約及 H06 請求

- **任務識別碼**：`ODP-DURABLE-PARTIAL-CONTRACT-PREP-001`
- **工作包代號**：`WP-33A`（Phase A：工程盤點、契約草案與人工決策請求）
- **日期**：2026-09-08
- **任務負責人**：Claude2（初版由 Antigravity2 交付；本版依 Codex2 審查意見 R1／R2／R3 修訂）
- **審查人**：Codex2
- **檢驗基準代碼（Inspected HEAD SHA）**：`23b94f2845def9a723a5071a95363942db92007b`（本次修訂重新檢驗）
- **檢驗時間（UTC）**：`2026-09-08T18:05:00Z`
- **初版檢驗基準**：`b6b729d95e575dc3b27ea9e02ce22fb128c3970b`（2026-09-08T16:48:00Z）。本次 base advance 併入 `origin/dev` `95646a5c2bd598b7e219ee51efa7e410cb9fe0f4`，僅帶入其他任務之證據目錄；`git diff --name-only b6b729d9..23b94f28` 不含 `apps/`、`shared/`、`modules/` 之任何檔案，故兩個基準下所引用之原始碼行號完全一致。
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
| [partial-retry-contract-draft.json](./partial-retry-contract-draft.json) | 契約草案 | 包含 `JobStatus.PARTIAL`、逐項明細收據（`ItemReceipt`，支援 `attempt >= 0`、取消語意）、差異化重試（`retry_scope="FAILED_ONLY"`）、狀態優先級收斂，以及 replay 識別與重複／亂序訊息處理（`replay_identity_and_ordering`）之完整 JSON Schema 規格 |
| [human-input-request-H06.md](./human-input-request-H06.md) | 人工決策請求 | 提出 3 項候選業務長任務（推薦 `batch-listing-intake`），請求 Human/Ops / 產品負責人正式核定 33B 實作標的 |
| [implementation-handoff.md](./implementation-handoff.md) | 工程交接說明 | 定義 WP-33B 實作入場條件、前置佇列語意修改（§3.0）、交付清單、反事實驗收標準（五項反事實測試）與治理規範 |

---

## 2. 靜態代碼盤點核心結論（Static Inventory Findings）

經針對代碼樹（SHA: `23b94f2845def9a723a5071a95363942db92007b`）進行全樹檢索與語法分析（執行期權限標記為 `static_only_no_production_runtime_access`）：

1. **無可達之 `JobStatus.PARTIAL` 寫入點**：
   - 全庫 Python、TypeScript/TSX 原始碼中，無任何 Production Worker Handler 或 Queue 狀態轉移會寫入 `JobStatus.PARTIAL`（靜態 `rg` 檢索 0 命中，exit code 1）。
2. **Default Worker Registry 僅註冊 3 個單一實體任務**：
   - `forecast`（`apps/worker/oday_worker/handlers.py:255`，enqueue 於 `apps/api/oday_api/main.py:997-1044`）：針對單一門市時序驗證，結果為二元 `SUCCEEDED` / `FAILED`。
   - `external-fetch`（`apps/worker/oday_worker/handlers.py:256`，enqueue 於 `apps/scheduler/oday_scheduler/main.py:180-195` 及 `apps/api/oday_api/main.py:1013-1044`）：針對單一 provider/window 排程抓取，結果為二元 `SUCCEEDED` / `FAILED`。
   - `assisted-listing-intake`（`apps/worker/oday_worker/handlers.py:257`，實作於 `apps/worker/assisted_listing_intake/worker.py:80-221`，enqueue 於 `modules/opsboard/application/network_listings.py:1243-1249`，1210 行為 correlationId 初始化）：針對單一房源 URL 爬蟲，單一實體無成員聚合。
3. **生產者至狀態寫入點全鏈追溯（Traceability）**：
   - 各任務之實際執行鏈為：生產者建立 `JobRequest` -> `DurableJobQueue.enqueue`（`shared/infrastructure/persistence/job_queue.py:65-110`，in-memory twin `shared/jobs/queue.py:139-158`）-> `Worker.run_once` 認領（`apps/worker/oday_worker/main.py:96`）-> `DurableJobQueue.claim_next`（`shared/infrastructure/persistence/job_queue.py:235-329`，in-memory twin `shared/jobs/queue.py:308-336`）-> `Worker.execute_job` 經 registry 派工（`apps/worker/oday_worker/main.py:263-267`）-> 終態寫入（`apps/worker/oday_worker/main.py:141-146` SUCCEEDED，`:210-222` QUEUED+RETRYING 或 FAILED+DEAD_LETTER）-> `DurableJobQueue.update_status`（`shared/infrastructure/persistence/job_queue.py:384-452`，in-memory twin `shared/jobs/queue.py:338-382`）。
   - **legacy `lease()` 不在執行鏈內**：`DurableJobQueue.lease`（`shared/infrastructure/persistence/job_queue.py:116-175`）與 `InMemoryJobQueue.lease`（`shared/jobs/queue.py:164-214`）於 `apps/`、`shared/`、`modules/` 內無任何呼叫點（靜態探針 `grep -rn "\.lease(" --include=*.py apps shared modules`，原始 exit code 1）。本文件先前版本將派工歸因於 `job_queue.py:120-173`（位於 `lease()` 內），該歸因錯誤，已於此更正。
4. **排除收據持久化專用 Envelope**：
   - `TenantScopedJobReceiptStore`（`shared/infrastructure/persistence/job_receipts.py:74-105`）與 `TenantScopedCommandReceiptStore`（`shared/infrastructure/persistence/command_receipts.py:70-105`）以 `shared/jobs/queue.py:10-18` 排除於 worker 派工之外，僅供讀回。
5. **區分非 Durable Job 之部分成功操作**：
   - `POST /api/v1/intake-batches`（`apps/api/app/routes/listings.py:2225-2265`，helper 於 1730-1799 行）具備 207 Multi-Status 與 `(accepted_count, rejected_count)`，但為同步 API 命令收據，無隊列 `job_id`。
   - `xlsx_import.py`（`modules/external_data/application/xlsx_import.py:816-933`）之 partial commit 與 `ingestion_store.py`（`modules/external_data/application/ingestion_store.py:90-123`）之 `accepted_count` / `quarantined_count` 亦非 Durable Worker Job。
6. **型別與交付邊界分離（Outcome vs Delivery State）**：
   - `JobStatus`（業務完成結果：`QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED`, `PARTIAL`）與 `JobDeliveryState`（基礎設施交付狀態：`RETRYING`, `DEAD_LETTER`）維持正交分離。
   - 現行程式中：`JobStatus.SUCCEEDED` 自動清除 `delivery_state = NULL`（`shared/infrastructure/persistence/job_queue.py:402-406`）；可重試異常寫入 `JobStatus.QUEUED` + `JobDeliveryState.RETRYING`；重試耗盡或非可重試異常寫入 `JobStatus.FAILED` + `JobDeliveryState.DEAD_LETTER`（`apps/worker/oday_worker/main.py:210-222`）。
   - **現行寫入器不足以支援 PARTIAL 清除（WP-33B 前置修改）**：`shared/infrastructure/persistence/job_queue.py:402-406` 只在 `status == JobStatus.SUCCEEDED` 時 append `delivery_state = NULL`；其餘狀態走 `elif delivery_state is not None` 分支，因此 `update_status(job_id, JobStatus.PARTIAL, delivery_state=None, ...)` **不會產生任何 `delivery_state` 指派**，先前的 `RETRYING` 值會原封留存。in-memory twin `shared/jobs/queue.py:362-364` 具相同語意（`resolved_delivery = delivery_state if delivery_state is not None else record.delivery_state`，且僅 `SUCCEEDED` 覆寫為 `None`）。詳見 [implementation-handoff.md](./implementation-handoff.md) §3.0。
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
- **Replay 識別與重複／亂序訊息（`replay_identity_and_ordering`）**：item 執行以 `(job_id, item_id, attempt)` 三元組唯一識別，job 層 enqueue 以 `idempotency_key` 去重。同一三元組之結果**至多套用一次**（無重複下游寫入、不遞增 `attempt`、不重複計數）；`attempt` 低於現值或 item 已為 `SUCCEEDED` 之後到結果一律判為 stale 並丟棄，**不得**回滾 `item_status`／`result_ref`／`outcome`。最終聚合由持久化 `items` 依優先級規則純函數推導，故重啟後與不同到達順序下結果一致。此項為 `FAILED_ONLY` 單次循序重試在結構上無法涵蓋之情境，故另立契約與驗收。

詳細 Schema 請參閱 [partial-retry-contract-draft.json](./partial-retry-contract-draft.json)。

### 3.2 候選業務任務
在 [human-input-request-H06.md](./human-input-request-H06.md) 中，工程團隊評估了三項候選任務：
- **候選一（推薦）：`batch-listing-intake`**（將同步 207 批次房源匯入昇格為非同步 Durable Batch Job）。
- **候選二：`multi-partition-external-fetch`**（多來源／分區外部資料排程攝取容錯）。
- **候選三：`batch-sitescore-evaluation`**（批次門市選址評估運算）。

---

## 4. 階段劃分與 WP-33B 交接

本任務屬於 **A 階段（工程準備）**，不代表業務功能已於 Production 實現。下一階段 **WP-33B** 需在 H06 完成具名簽核後入場，並遵循 [implementation-handoff.md](./implementation-handoff.md) 所載之 5 項反事實驗收標準：
1. **狀態轉移精確性與成員明細驗證**（8 成功、1 永久失敗、1 暫態失敗 => `PARTIAL`）。
2. **差異化重試不重複執行與收斂驗證**（成功項 0 次調用、永久失敗項 0 次調用、暫態失敗項 1 次調用）。
3. **交付狀態與業務結果正交分離驗證**（隊列重試 `RETRYING` 不破壞業務狀態；死信 `DEAD_LETTER` 僅配 `FAILED`；`PARTIAL` 終態清除 `delivery_state`，含 `RETRYING → PARTIAL` 之持久層與 API 雙重回讀強制子案例）。
4. **重啟回讀與取消／未執行項目驗證**（未執行項目 `attempt = 0` 及取消收據回讀）。
5. **重複投遞與亂序結果冪等驗證**（重複訊息 0 次額外下游寫入且不遞增 `attempt`；舊 attempt 之後到失敗不回滾已 `SUCCEEDED` 之 `result_ref`／`outcome`；重啟後最終聚合一致）。

另須注意：WP-33B 入場後**必須先完成** [implementation-handoff.md](./implementation-handoff.md) §3.0 之 `update_status` 佇列語意前置修改，否則以 `delivery_state=None` 寫入 `PARTIAL` 不會清除既有 `RETRYING`（詳見 §2 第 6 點）。

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

# 5. 查證 legacy lease() 於執行鏈外：apps/、shared/、modules/ 內無任何呼叫點 (原始 exit code 1，0 命中)
grep -rn "\.lease(" --include=*.py apps shared modules
# 輸出: (無)

# 6. 查證實際認領路徑為 claim_next (原始 exit code 0)
grep -rn "claim_next" --include=*.py apps shared modules
# 輸出:
# apps/worker/oday_worker/main.py:96:            job = self.job_queue.claim_next(worker_id=self.worker_id)
# shared/infrastructure/persistence/job_queue.py:235:    def claim_next(self, worker_id: str = "worker-1") -> JobRecord | None:
# shared/jobs/queue.py:308:    def claim_next(self, worker_id: str = "worker-1") -> JobRecord | None:

# 7. 查證 durable enqueue 呼叫點 (原始 exit code 0)
grep -rn "\.enqueue(" --include=*.py apps shared modules
# 輸出:
# apps/scheduler/oday_scheduler/main.py:180:                self.job_queue.enqueue(
# apps/api/oday_api/main.py:1037:            job, created = job_queue.enqueue(
# shared/infrastructure/persistence/command_receipts.py:73:            record, created = self.queue.enqueue(
# shared/infrastructure/persistence/job_receipts.py:74:        record, created = self.queue.enqueue(
# modules/opsboard/application/network_listings.py:1243:                job, created = job_queue.enqueue(
```

> 探針 5～7 於 2026-09-08T18:05:00Z 在任務分支 HEAD `23b94f2845def9a723a5071a95363942db92007b` 執行，
> 為本次修訂新增之採集，用於更正先前將派工歸因於 `job_queue.py:120-173`（`lease()` 內部）之錯誤。
> 三者均為唯讀靜態檢索，未執行任何測試套件、未接觸 runtime、provider 或 production 資源；
> 探針 5 之 exit code 1 表示「無命中」，非工具錯誤。

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

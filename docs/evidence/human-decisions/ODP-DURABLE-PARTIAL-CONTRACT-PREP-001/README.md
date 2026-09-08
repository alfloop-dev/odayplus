# ODP-DURABLE-PARTIAL-CONTRACT-PREP-001 — 真實 Durable Job 候選與 PARTIAL／Receipt／Retry 契約及 H06 請求

- **任務識別碼**：`ODP-DURABLE-PARTIAL-CONTRACT-PREP-001`
- **工作包代號**：`WP-33A`（Phase A：工程盤點、契約草案與人工決策請求）
- **日期**：2026-09-08
- **任務負責人**：Antigravity2
- **審查人**：Codex2
- **檢驗基準代碼（Inspected HEAD SHA）**：`9048161e058becff5a53593a773d3c42238213fb`
- **檢驗時間（UTC）**：`2026-09-08T16:12:00Z`
- **關聯需求**：`ODP-FR-SHARED-001`（所有長時間任務都能查詢 QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED/PARTIAL）
- **決策依據**：使用者人工決策 D19（選項 A：實作 partial／receipt／retry）

---

## 1. 任務執行摘要與交付產物索引

本任務（WP-33A）為執行《ODP 人工決策落地與 Supervisor／Auto Worker 執行規畫》中關於 `ODP-FR-SHARED-001` PARTIAL 狀態轉移之準備階段。工程團隊於獨立工作樹中完成全庫 static inventory 盤點、草擬非同步長任務明細收據與重試契約，並產出 H06 人工決策請求書，作為後續 WP-33B 實作之依據。

本交付目錄包含以下 5 項核心產物：

| 檔案名稱 | 產物類型 | 核心內容說明 |
|---|---|---|
| [README.md](./README.md) | 總索引與說明文件 | 本任務執行摘要、盤點事實總結、產物索引與驗證收據（本文件） |
| [producer-inventory.json](./producer-inventory.json) | 靜態盤點數據 | 代碼庫中全部 3 個 default registry jobs、3 項同步 partial 操作與 11 個模組 worker 入口之靜態結構清單 |
| [partial-retry-contract-draft.json](./partial-retry-contract-draft.json) | 契約草案 | 包含 `JobStatus.PARTIAL`、逐項明細收據（`ItemReceipt`）、差異化重試（`retry_scope="FAILED_ONLY"`）與狀態收斂之完整 JSON Schema 規格 |
| [human-input-request-H06.md](./human-input-request-H06.md) | 人工決策請求 | 提出 3 項候選業務長任務（推薦 `batch-listing-intake`），請求 Human/Ops / 產品負責人正式核定 33B 實作標的 |
| [implementation-handoff.md](./implementation-handoff.md) | 工程交接說明 | 定義 WP-33B 實作入場條件、交付清單、反事實驗收標準（四項反事實測試）與治理規範 |

---

## 2. 靜態代碼盤點核心結論（Static Inventory Findings）

經針對當前代碼樹（SHA: `9048161e058becff5a53593a773d3c42238213fb`）進行全樹檢索與語法分析：

1. **無可達之 `JobStatus.PARTIAL` 寫入點**：
   - 全庫 Python、TypeScript/TSX 原始碼中，無任何 Production Worker Handler 或 Queue 狀態轉移會寫入 `JobStatus.PARTIAL`。
2. **Default Worker Registry 僅註冊 3 個單一實體任務**：
   - `forecast` (`apps/worker/oday_worker/handlers.py:255`): 針對單一門市時序驗證，結果為二元 `SUCCEEDED` / `FAILED`。
   - `external-fetch` (`apps/worker/oday_worker/handlers.py:256`): 針對單一 provider/window 排程抓取，結果為二元 `SUCCEEDED` / `FAILED`。
   - `assisted-listing-intake` (`apps/worker/oday_worker/handlers.py:257`): 針對單一房源 URL 爬蟲，單一實體無成員聚合。
3. **區分非 Durable Job 之部分成功操作**：
   - `POST /api/v1/intake-batches` 具備 207 Multi-Status 與 `(accepted_count, rejected_count)`，但為同步 API 命令收據，無隊列 `job_id`。
   - `xlsx_import.py` 之 partial commit 與 `ingestion_store.py` 之 `accepted_count` / `quarantined_count` 亦非 Durable Worker Job。
4. **型別邊界嚴格隔離**：
   - `JobStatus`（業務完成結果：`QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED`, `PARTIAL`）與 `JobDeliveryState`（基礎設施交付狀態：`RETRYING`, `DEAD_LETTER`）維持正交分離。終態 `JobStatus` 之 `delivery_state` 必為 `None`。

詳細盤點欄位請參閱 [producer-inventory.json](./producer-inventory.json)。

---

## 3. 契約設計與業務候選評估

### 3.1 契約設計要點
- **明細收據（Itemized Receipt）**：收據結構包含 `items` 陣列，每筆成員具備 `item_id`, `item_status` (`SUCCEEDED` | `FAILED` | `CANCELLED` | `PENDING`), `attempt`, `result_ref`, `error` (`code`, `message`, `retryable`)。
- **差異化重試（Scoped Replay）**：支援 `retry_scope="FAILED_ONLY"`，重試引擎直接跳過已 `SUCCEEDED` 之項目（零次下游調用），僅對 `FAILED` 且 `retryable == true` 之項目重新運算。
- **狀態收斂（Convergence）**：重試成功之項目更新為 `SUCCEEDED`，全數成功時整體 JobStatus 由 `PARTIAL` 收斂轉移為 `SUCCEEDED`。

詳細 Schema 請參閱 [partial-retry-contract-draft.json](./partial-retry-contract-draft.json)。

### 3.2 候選業務任務
在 [human-input-request-H06.md](./human-input-request-H06.md) 中，工程團隊評估了三項候選任務：
- **候選一（推薦）：`batch-listing-intake`**（將同步 207 批次房源匯入昇格為非同步 Durable Batch Job）。
- **候選二：`multi-partition-external-fetch`**（多來源／分區外部資料排程攝取容錯）。
- **候選三：`batch-sitescore-evaluation`**（批次門市選址評估運算）。

---

## 4. 階段劃分與 WP-33B 交接

本任務屬於 **A 階段（工程準備）**，不代表業務功能已於 Production 實現。下一階段 **WP-33B** 需在 H06 完成具名簽核後入場，並遵循 [implementation-handoff.md](./implementation-handoff.md) 所載之 4 項反事實驗收標準（狀態精確性、重試零重複、交付狀態正交分離、重啟可回讀性）。

---

## 5. 本地驗證收據（Verification Receipts）

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

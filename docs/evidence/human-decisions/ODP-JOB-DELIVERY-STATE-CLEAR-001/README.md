# ODP-JOB-DELIVERY-STATE-CLEAR-001 — PARTIAL／CANCELLED 終態清除殘留重試狀態

- **Task**：`ODP-JOB-DELIVERY-STATE-CLEAR-001`
- **Owner**：Claude2 · **Reviewer**：Codex2
- **上游任務**：`ODP-DURABLE-PARTIAL-CONTRACT-PREP-001`（WP-33A）之
  [implementation-handoff.md](../ODP-DURABLE-PARTIAL-CONTRACT-PREP-001/implementation-handoff.md) §3.0
- **關聯需求**：`ODP-FR-SHARED-001`
- **交付基準（base head，實測）**：`ef76cf6d295ce7a8a470fe6e5f0eab20a0439169`
  （即本 task branch 開立時的 `origin/dev`；本文件所屬 commit 為其後代）
- **本文件撰寫時的實測時鐘（UTC）**：`2026-09-09T00:23:47Z`

---

## 1. 範圍與非範圍

本 task 只交付 WP-33A 交接書 §3.0 所指認的**既有 `update_status` 缺陷修復**，與後續選定哪個
批次業務無關。

**不在本範圍內**（仍屬 WP-33B，未在此交付）：批次 handler、明細收據持久化（`items`）、
`POST /platform/jobs/{job_id}/retry` 差異化重試、replay fencing、H06 業務 job 選定。
本文件不宣稱 `ODP-FR-SHARED-001` 的 PARTIAL member 已可轉為 `VERIFIED`。

---

## 2. 缺陷（實測而非引述）

`JobStatus` 表達業務結果，`JobDeliveryState` 表達佇列還在為這件事做什麼投遞動作。兩者正交。

修復前，兩個實作都只在 `SUCCEEDED` 清除 `delivery_state`：

| 實作 | 位置 | 修復前行為 |
|---|---|---|
| `DurableJobQueue.update_status` | `shared/infrastructure/persistence/job_queue.py` | `if status == SUCCEEDED:` → `delivery_state = NULL`；`elif delivery_state is not None:` → 寫入。以 `PARTIAL`／`CANCELLED` + `delivery_state=None` 呼叫時**兩個分支皆不成立**，UPDATE 語句完全不含 `delivery_state` 指派，資料庫既有值原封保留。 |
| `InMemoryJobQueue.update_status` | `shared/jobs/queue.py` | `resolved_delivery = delivery_state if ... else record.delivery_state`，其後僅 `if status == SUCCEEDED: resolved_delivery = None`。同樣沿用舊值。 |

由於批次任務在 PARTIAL 之前極可能經歷重試（`apps/worker/oday_worker/main.py` 的可重試例外
路徑會寫入 `QUEUED` + `RETRYING`），持久結果會是 `status=PARTIAL, delivery_state=RETRYING`：
一個已經結束的工作，卻仍宣稱在等待重新投遞。

---

## 3. 採用的設計與呼叫端相容性

交接書允許兩種設計（終態規則或顯式清除參數）。本交付採**終態規則**：

```python
# shared/jobs/queue.py
DELIVERY_SETTLED_JOB_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.SUCCEEDED, JobStatus.PARTIAL, JobStatus.CANCELLED}
)
```

兩個 `update_status` 都改判 `status in DELIVERY_SETTLED_JOB_STATUSES`，維持單一可觀測語意。

**為何不引入 `clear_delivery_state=True`**：那會新增一個必須讓所有呼叫端學會的參數，而
`SUCCEEDED` 早已是「終態即清除、且勝過顯式傳入值」的行為。把同一條既有規則延伸到另外兩個
終態，不需要任何呼叫端改動，也不需要簽章變更。

**呼叫端相容性（逐條）**：

- **簽章未變**，`delivery_state` 參數維持三態語意：
  - 非終態（`QUEUED`／`RUNNING`／`FAILED`）且未指定 → 保留既有值，不意外清除；
  - 顯式傳值 → 寫入該值；
  - 終態集合內的 status → 清為 `NULL`，終態規則勝過顯式傳入值（沿用 `SUCCEEDED` 原本的優先序）。
- **`FAILED` 刻意排除於終態集合之外**：`DEAD_LETTER` 是呼叫端在結果已知後仍需要的投遞資訊。
  `apps/worker/oday_worker/main.py` 的 `FAILED` + `DEAD_LETTER` 與 `QUEUED` + `RETRYING` 寫入行為未變。
- **既有呼叫端盤點**：repo 內經 `update_status` 寫入 `PARTIAL` 的生產程式碼為 0 處；寫入
  `CANCELLED` 的僅 `tests/reliability/test_assisted_listing_intake_jobs.py`
  （不帶 `delivery_state`，且不斷言其保留）。`shared/infrastructure/persistence/job_receipts.py`
  與 `command_receipts.py` 只寫 `SUCCEEDED`／`FAILED`。因此本變更對現有呼叫端為行為相容。
- `DurableJobQueue.complete()` 早已 `delivery_state = NULL`，`fail()` 早已寫 `RETRYING`／
  `DEAD_LETTER`；本次未動這兩者，語意與新規則一致。
- 讀取端 `_row_to_record` 的 legacy 推導只在 `FAILED` 且 `attempts >= max_retries` 時補
  `DEAD_LETTER`，不會把已清除的 `PARTIAL`／`CANCELLED` 重新推回 `RETRYING`。

---

## 4. 測試與量測

新增 `tests/reliability/test_job_delivery_state_clear.py`（13 個案例）。

**測試 fixture 性質（明示範圍）**：全部使用合成的 in-repo fixture — 一個 `tmp_path` 下的
臨時 SQLite 檔與行程內佇列，job type 為合成的 `delivery-state-clear-probe`。
不涉及真實資料、外部來源、production 連線或 live 能力。**離線測試通過不等於真實 PARTIAL
業務資料已提供，也不等於 live 能力已驗證。**

**每個案例都先讓 job 真的進入 `RETRYING`**：透過 `ODayWorker.run_once()` 走
`apps/worker/oday_worker/main.py` 的可重試例外路徑，並先斷言此時確為 `RETRYING`。
否則「事後讀回 `None`」無法區分「已清除」與「本來就沒有值」。

涵蓋範圍：

1. `DELIVERY_SETTLED_JOB_STATUSES` 的成員契約（明示排除 `FAILED`／`QUEUED`／`RUNNING`）。
2. Durable：`PARTIAL`／`CANCELLED`／`SUCCEEDED` 三態各自 — 直接以 SQL 讀 `durable_jobs` 列
   確認 `delivery_state IS NULL`，再關閉 engine、**重新建立 queue** 自同一檔案讀回。
3. Durable：`FAILED` + 顯式 `DEAD_LETTER` 保留（原始列與重建後讀回皆是）。
4. Durable：`QUEUED`／`RUNNING` 未指定 `delivery_state` 時不得被清除。
5. Durable：終態清除仍受 fencing／版本控制約束，被拒的寫入不得清掉任何值。
6. In-memory twin 對 2／3／4 的對等行為（parity；否則 durable 缺陷會躲在綠測試後面）。
7. 既有查詢 API `GET /api/v1/jobs/{job_id}` 的序列化：`PARTIAL` 讀回 `delivery_state: null`，
   `FAILED` 讀回 `"dead_letter"`。

### 4.1 反事實量測（counterfactual）

為證明測試確實碰到缺陷路徑，曾在工作樹中**暫時**把兩個 `update_status` 還原成修復前的
`status == JobStatus.SUCCEEDED` 判斷（未提交），重跑同一選集：

- 原始 exit code：`1`；`5 failed, 8 passed`；耗時 `12.06s`
- 紅的正好是 `[partial]`、`[cancelled]`（durable 與 in-memory 各一組）與 API 序列化案例
- `SUCCEEDED`、`FAILED` + `DEAD_LETTER`、in-flight 未指定三類**維持綠**，證明本變更沒有靠
  放寬既有斷言換來綠燈

隨後還原修復，同一選集：原始 exit code `0`，`13 passed`，耗時 `12.03s`。

### 4.2 已量測的輔助檢查

| 檢查 | 命令 | 原始 exit code |
|---|---|---|
| 空白／衝突標記 | `git diff --check` | `0` |
| Lint | `python -m ruff check`（本次變更的 4 個 .py） | `0` |
| 邊界盤點 | `delivery_toolchain/governance/check_code_boundaries.py` | 重產前 `1`（stale）→ `--write-inventory` 後 `0`，1145 files |

`docs/audits/code-boundary-inventory.csv` 只因新增測試檔而多一列（已確認無重複列）。

### 4.3 直譯器版本說明（重要）

本工作樹以 `uv` 預設解析會選到 CPython 3.14，而 `pgserver==0.1.4` 只有 `cp312` wheel，
`uv run pytest ...` 會在建立環境時就 `exit 2` 而跑不到任何測試。上述所有量測皆以釘住
3.12 的等價命令執行（`uv run --frozen --python 3.12 pytest ...`）。這是環境差異，不是
測試結果差異。

綁定交付 head 的正式收據由 `delivery_toolchain/git/task_verification.py run` 產生，
`task_finalize.sh` 的 `check` 前置閘會拒絕沒有該收據的發布。

---

## 5. 未解事項（交還 WP-33B）

- H06 業務 job 候選仍待人類決策；本 task 不代為裁決、不代簽。
- 交接書 §3.1–§3.4 與 §4 測試 1／2／4／5 未在此交付；§4 測試 3 的
  「RETRYING → PARTIAL 持久清除回讀」強制子案例已在此完成。
- `delivery_toolchain/governance/set_valued_requirements.json` 的 `ODP-FR-SHARED-001`
  PARTIAL member **維持現狀不動**：本 task 只修佇列語意，尚不足以支撐 `VERIFIED`。

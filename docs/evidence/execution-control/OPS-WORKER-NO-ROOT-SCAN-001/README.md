# OPS-WORKER-NO-ROOT-SCAN-001: 禁止 worker 以 root-wide scan 尋找測試工具驗收紀錄

## 1. 問題概述與根因分析

### 問題背景
在 Pantheon 自動化 Worker 叢集中，Auto Worker 被喚醒並派發至隔離的 per-task git worktree 執行任務。當任務驗證需要特定測試工具（例如 `pytest` 或其他相依套件）而該工具未直接安裝於系統預設 Python（`/usr/bin/python3`）時，部分 LLM Worker 可能會嘗試在整個主機檔案系統執行 `find /`、`rg /` 或 `grep -r /` 來尋找工具執行檔或虛擬環境。

### 根因與危害
1. **全系統搜尋造成龐大資源浪費與逾時**：在根目錄 `/` 執行全系統掃描會遍歷所有掛載點、proc、sys 與大量檔案，導致極高磁碟 I/O 與 CPU 負擔，Worker 容易因此 stall 或耗盡租約逾時。
2. **缺乏明確的 Prompt 規範與工具回退契約**：既有 Worker 喚醒 Prompt 未明確規範工具搜尋邊界，未強制要求在工具缺失時使用任務已宣告的 verification 命令或專案既有的 `uv run`（例如 `uv run pytest`）。

---

## 2. 修復架構與設計

依據任務 Brief 與驗收標準，本任務嚴格沿用既有 Worker Prompt 與 Verification 契約，不新增任何外部 wrapper、watchdog、timeout 或平行執行機制：

1. **Worker Wakeup Prompt 契約強化**：
   - 在唯一標準喚醒範本（`.orchestrator/templates/wakeup.txt`）中加入明確防護條款：
     - **明確禁止**：嚴禁執行 `find /`、`rg /` 等全系統（root-wide）搜尋尋找測試工具或檔案。
     - **指定正則路徑**：若工具或依賴缺失，只使用任務 verification 已宣告命令或專案既有 `uv run`（例如 `uv run pytest ...`），或回報 blocker，不得自行掃描主機檔案系統。
2. **單一控制面與派發整合**：
   - 確保所有派發原因（包含 `owned_ready_dispatch`、`owned_in_progress_dispatch`、`review_ready_dispatch`、`owned_finalize_dispatch`、`helper_claim_dispatch` 等）在透過 `watch_events.render_wakeup_message` 渲染 Worker 提示訊息時均具備該禁止與指引條款。
   - 不在 `supervisor.py` 新增無謂的 re-export import，保持控制面簡潔單純。
3. **聚焦 Regression 測試保護**：
   - 在 `.orchestrator/test_supervisor.py` 中新增 `WorkerPromptContractTests`，驗證 Prompt 渲染合約：
     - 驗證 Prompt 包含 `find /`、`rg /`、`全系統`、`root-wide` 之禁止文字。
     - 驗證 Prompt 明確指示工具缺失時使用任務宣告命令或 `uv run`，且 `不自行掃描主機`。
     - 驗證所有主要派發模式均完整繼承此 Prompt 規則。
4. **架構邊界遵循**：
   - 未修改任何 forbidden paths（`.orchestrator/worker_runner.py`、`.orchestrator/adapters/`、`.github/workflows/`）。
   - 不引入任何第二套執行路徑或額外工具 wrapper。

---

## 3. 驗證與測試結果

### 3.1 測試套件執行
執行指令：
```bash
uv run pytest .orchestrator/test_supervisor.py -k worker_prompt -v
python3 -m unittest discover -s .orchestrator -p 'test_supervisor.py' -k 'WorkerPromptContractTests'
uv run pytest .orchestrator/test_watch_events.py -v
python3 delivery_toolchain/governance/check_code_boundaries.py
git diff --check origin/dev
```

### 3.2 測試項目清單
1. `WorkerPromptContractTests.test_worker_prompt_forbids_root_wide_scan_and_requires_declared_verification`：
   - 驗證渲染之 Worker Prompt 具備明確禁止全系統 `find /`、`rg /` 搜尋，並指引使用宣告 verification 或 `uv run`。
2. `WorkerPromptContractTests.test_worker_prompt_root_scan_prohibition_applies_across_dispatch_reasons`：
   - 驗證 across multiple dispatch reasons（`owned_ready_dispatch`, `owned_in_progress_dispatch`, `review_ready_dispatch`, `owned_finalize_dispatch`, `helper_claim_dispatch`）皆受禁止條款約束。
3. `test_watch_events.py`：
   - 驗證現有 watcher 派發提示渲染測試全部維持綠燈。

### 3.3 測試輸出實錄
```text
============================= test session starts ==============================
platform linux -- Python 3.12.14, pytest-9.1.1, pluggy-1.6.0
collecting ... collected 465 items / 463 deselected / 2 selected

.orchestrator/test_supervisor.py ..                                      [100%]

====================== 2 passed, 463 deselected in 0.88s =======================

----------------------------------------------------------------------
Ran 2 tests in 0.005s

OK

============================= test session starts ==============================
platform linux -- Python 3.12.14, pytest-9.1.1, pluggy-1.6.0
collecting ... collected 2 items

.orchestrator/test_watch_events.py ..                                    [100%]

============================== 2 passed in 0.21s ===============================

Code boundary checks passed for 982 files.
- archived: 14
- development_delivery_tooling: 66
- development_platform_system: 63
- evidence_artifact: 22
- product_operations_tooling: 29
- product_system: 485
- verification: 303

git diff --check origin/dev: OK (no whitespace or newline errors)
```

---

## 4. 驗收項目核對

| 驗收項目 | 狀態 | 驗證方式 |
| :--- | :--- | :--- |
| 既有 worker prompt 明確禁止 find /、rg / 等 root-wide 工具搜尋 | **通過** | `WorkerPromptContractTests` & 檢視 `wakeup.txt` |
| 工具缺失時只使用 task verification 已宣告命令或專案既有 uv run，不自行掃描主機 | **通過** | `WorkerPromptContractTests` & 檢視 `wakeup.txt` |
| 加入聚焦 regression test 驗證 prompt contract | **通過** | `.orchestrator/test_supervisor.py` 新增 2 個聚焦測試案例 |
| 不新增 wrapper、watchdog、timeout 或第二套執行路徑 | **通過** | 純 Prompt 規範與測試，未引入任何額外 wrapper/daemon |
| PR 與 evidence 人類可讀內容使用繁體中文 | **通過** | 本文件與 PR 均使用正體中文撰寫 |

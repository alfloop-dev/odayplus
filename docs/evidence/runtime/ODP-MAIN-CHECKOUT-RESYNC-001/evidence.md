# Evidence: ODP-MAIN-CHECKOUT-RESYNC-001

Generated: 2026-09-15T07:15:00Z
Task: ODP-MAIN-CHECKOUT-RESYNC-001 — 修復 config 的 checkout 相對解析並讓主 checkout 可安全同步

---

## 1. 根因分析與架構修復 (Root Cause & Architectural Fix)

### 1.1 現象與根因 (Problem & Root Cause)
主 checkout (`/home/lupin/odayplus`) 停在 2026-09-03，落後 `origin/dev` 1853 個 commit。過去無法直接 pull / 同步的根因在於：
1. **Config 解析路徑相對於 runtime checkout 而非 PANTHEON_STATUS_ROOT**：
   - 既有 `scripts/ai_status.py` 定義 `CONFIG_FILE = ROOT / ".orchestrator" / "config.json"`，其中 `ROOT` 是 `Path(__file__).resolve().parents[1]`。
   - 在 rollout supervisor 架構下，canonical writer 位於 `/home/lupin/oday-plus-supervisor-runtime-current`（乾淨的 git worktree，gitignored 的 `config.json` 並不存在於該 worktree）。
   - 過去為了規避 `ConfigError`，`rollout_supervisor_runtime.py` 中的 `status_launcher()` 硬編寫入兩行絕對路徑 `export ORCH_CONFIG_PATH=...` 與 `export PANTHEON_CONFIG_PATH=...`。
   - 一旦移除這兩行 export，所有未帶 config 變數的 `ai-status.sh` 子命令立即引發 `ConfigError`。

2. **正確的修復架構**：
   - Canonical status writer 必須保持指向 stable runtime link (`/home/lupin/oday-plus-supervisor-runtime-current/scripts/ai_status.py`)，避免將 writer 綁死於落後的主 checkout。
   - `scripts/ai_status.py` 與 `.orchestrator/common.py` 必須原生支持以 `PANTHEON_STATUS_ROOT` / `ORCH_STATUS_ROOT` 為 config 基準：
     - 當未指定 explicit `--config` 或環境變數 `ORCH_CONFIG_PATH` 時，預設 config 路徑自動從 `authoritative_status_root()` / `STATUS_ROOT` 解析 (`STATUS_ROOT / ".orchestrator" / "config.json"`)。
     - Config 內的相對路徑（如 `paths.status_file` 等）自動透過 `anchor_config_paths()` 錨定至 `STATUS_ROOT`。
   - `scripts/orchestrator/rollout_supervisor_runtime.py` 的 `status_launcher()` 移除硬編的 `ORCH_CONFIG_PATH` / `PANTHEON_CONFIG_PATH` export，生成的 launcher 只保留 `PANTHEON_STATUS_ROOT` 與 stable runtime link exec。

### 1.2 程式碼變更明細 (Code Changes)

| 檔案 | 變更內容 | 目的 |
|---|---|---|
| `.orchestrator/common.py` | `load_config()` 在未傳入 config 且環境變數為空時，優先自 `authoritative_status_root()` 讀取 `.orchestrator/config.json`，並調用 `anchor_config_paths()` 錨定相對路徑。 | 確保未設 env 時 common 函式庫能從 status root 取得 live config。 |
| `.orchestrator/test_common.py` | 新增 `test_load_config_resolves_from_authoritative_status_root_when_env_unset` 單元測試。 | 驗證 `load_config()` 在環境變數清空時正確由 status root 解析並錨定路徑。 |
| `scripts/ai_status.py` | 1. `CONFIG_FILE = STATUS_ROOT / ".orchestrator" / "config.json"`<br>2. `active_config_file()` 相對路徑由 `STATUS_ROOT` 解析<br>3. `merged_orchestrator_config()` 調用 `anchor_config_paths()`。 | 確保 CLI 入口在無顯式 env 時以 `STATUS_ROOT` 為唯一 live config 來源。 |
| `scripts/test_ai_status.py` | `_AI_STATUS_ROOT_ATTRIBUTES` 納入 `CONFIG_FILE`；`test_live_path_delegates_to_common_load_config` 斷言更新為 `STATUS_ROOT` 下路徑。 | 維護測試隔離性與 fixture 一致性。 |
| `scripts/orchestrator/rollout_supervisor_runtime.py` | `status_launcher()` 移除硬編的 `export ORCH_CONFIG_PATH=...` 與 `export PANTHEON_CONFIG_PATH=...`。 | 確保產生的 launcher 簡潔且不包含機器特定的絕對路徑。 |
| `scripts/orchestrator/test_rollout_supervisor_runtime.py` | 重構為標準 `unittest` 格式，並新增斷言確保 launcher 不再包含 `ORCH_CONFIG_PATH=`。 | 增強測試覆蓋且解除對 pytest 的單向依賴。 |
| `scripts/ai-status.sh` | 移除硬編 export，維持指向 `oday-plus-supervisor-runtime-current` 的 clean launcher。 | 與 rollout 生成物保持一致。 |

---

## 2. 唯讀子命令實測證據 (Unset Config Env Acceptance Test)

在完全清除 `ORCH_CONFIG_PATH` 與 `PANTHEON_CONFIG_PATH` 的環境下，執行 `ai_status.py` 的唯讀子命令：

### 2.1 `show` 子命令
```bash
$ env -u ORCH_CONFIG_PATH -u PANTHEON_CONFIG_PATH \
    PANTHEON_STATUS_ROOT=/home/lupin/odayplus \
    python3 /tmp/pantheon-worker-worktrees/pantheon/odp-main-checkout-resync-001/scripts/ai_status.py show ODP-MAIN-CHECKOUT-RESYNC-001
```
**執行結果**：
```json
{
  "source": "active",
  "task": {
    "id": "ODP-MAIN-CHECKOUT-RESYNC-001",
    "title": "修復 config 的 checkout 相對解析並讓主 checkout 可安全同步",
    "status": "in_progress",
    "owner": "Antigravity7",
    "reviewer": "Claude"
  }
}
```
**退出碼 (Exit Code)**: `0`

### 2.2 `prompt` 子命令
```bash
$ env -u ORCH_CONFIG_PATH -u PANTHEON_CONFIG_PATH \
    PANTHEON_STATUS_ROOT=/home/lupin/odayplus \
    python3 /tmp/pantheon-worker-worktrees/pantheon/odp-main-checkout-resync-001/scripts/ai_status.py prompt ODP-MAIN-CHECKOUT-RESYNC-001
```
**執行結果**：
```
Read AI_COLLABORATION_GUIDE.md, ai-status.json, TARGET_ARCHITECTURE.md, CANONICAL_DOCUMENT_MAP.md, ROADMAP.md, DEVELOPMENT_WORKBREAKDOWN.md, WORKBENCH_DELIVERY_BACKLOG.md, DELIVERY_CLOSURE_AND_LOOP_STATES.md, and EXECUTION_PROOF_AND_MATURITY_LEVELS.md first. Use current-work.md as a human summary only; do not treat it as the primary machine context. Use ai-activity-log.jsonl only when you need targeted recent history. Treat generated views as derived from machine-readable state. Follow the canonical lifecycle todo -> in_progress -> review -> review_approved -> done. Use scripts/ai-status.sh for every state change.
```
**退出碼 (Exit Code)**: `0`

**結論**：在未設定任何 config 環境變數下，所有唯讀子命令均正常從 `PANTHEON_STATUS_ROOT` 取得 live config，無任何 `ConfigError`。

---

## 3. `ai_status.py` 本機未提交改動逐條比對 (Local Diffs Comparison)

比對 `/home/lupin/odayplus/scripts/ai_status.py` 的 3 處未提交改動與 `origin/dev` 對應實作：

### 3.1 `enforce_delivery_merged_gate` — repository 欄位傳遞
- **本機未提交修改**：
  在調用 `is_approved_head_satisfied` 時，傳入字典手動加入 `"repository": repository_slug_value`。
- **origin/dev 對應實作 (lines 3129–3137)**：
  ```python
  task_dict: dict[str, Any] = dict(task) if task else {}
  if "id" not in task_dict or not task_dict["id"]:
      task_dict["id"] = task_id
  if "branch" not in task_dict or not task_dict["branch"]:
      task_dict["branch"] = branch
  if "approved_head" not in task_dict or not task_dict["approved_head"]:
      task_dict["approved_head"] = approved_head
  if "repository" not in task_dict or not task_dict["repository"]:
      task_dict["repository"] = repository_slug_value
  ```
- **比對結論**：`origin/dev` 建立了完整的 `task_dict`，包含 `id`, `branch`, `approved_head`, `repository` 4 個欄位的缺漏補齊，語意完全涵蓋且更健全，本機修改可安全捨棄。

### 3.2 `collect_done_delivery_metadata` — Task-ID 大小寫比對
- **本機未提交修改**：
  `if field_name == "Task-ID" and actual_value.casefold() == expected_value.casefold(): continue`
- **origin/dev 對應實作 (line 3620)**：
  `origin/dev` 第 3620 行包含一模一樣的逐字邏輯：
  `if field_name == "Task-ID" and actual_value.casefold() == expected_value.casefold(): continue`
- **比對結論**：`origin/dev` 已經包含相同程式碼，本機修改可安全捨棄。

### 3.3 `resolve_task_sha` — 分支選擇邏輯 (`explicit_branch` vs `recorded_branch`)
- **本機未提交修改**：
  `branch_names = ([recorded_branch] if recorded_branch else [f"task/{task_id}", f"task-{task_id}"])`
  （其中 `recorded_branch` 來自 `task_branch_name(task)`）。
- **origin/dev 對應實作 (lines 9640, 9656–9660)**：
  ```python
  recorded_branch = task_explicit_branch(task)
  ...
  branch_names = (
      [recorded_branch]
      if recorded_branch
      else [f"task/{task_id}", f"task-{task_id}"]
  )
  ```
- **比對結論**：
  - 本機修改使用的 `task_branch_name()` 會在未記錄 branch 時預設回傳 `f"task/{task_id}"`，導致 `if recorded_branch` 永遠為真，破壞了向後探測 `task-{task_id}` 的 fallback 機制。
  - `origin/dev` 使用 `task_explicit_branch()`，只有在 task 顯式指定 branch 時才回傳字串，未指定時回傳 `None` 並正確觸發 fallback 清單。
  - `origin/dev` 的修正不僅包含語意，且邏輯更加正確。本機修改可安全捨棄。

---

## 4. 9 個運行時狀態檔處置與校驗 (Runtime State Files Disposition)

### 4.1 檔案清單與即時 Checksum (SHA256)
主 checkout `/home/lupin/odayplus` 下的 9 個運行時狀態檔記錄了即時的看板與活動狀態：

| # | 檔案路徑 | 角色 | 即時 SHA256 (2026-09-15T07:12Z) |
|---|---|---|---|
| 1 | `ai-status.json` | 線上看板核心狀態 | `5eb2847893cc2ba21e669e639e40f8df64b275443423529c485f682fd94761b6` |
| 2 | `ai-activity-log.jsonl` | 執行日誌序列 | `10111af371e7e0a932194771acd8399c0a37b2d7c7173a1ab0e8a7db03612648` |
| 3 | `current-work.md` | 人類可讀工作摘要 | `4bdbfcf2368660adb4d1e3159ebf3379c8c2d67e3011295832037eb1c4eeabaa` |
| 4 | `dashboard-bundle.json` | Web 看板聚合數據 | `d26a81e3b6baa4c95bd65265e5bc0184895b79b668f1e3e7e5313fa0210647b6` |
| 5 | `docs-site/ai-activity-log.jsonl` | 靜態站點日誌鏡像 | `10111af371e7e0a932194771acd8399c0a37b2d7c7173a1ab0e8a7db03612648` |
| 6 | `docs-site/ai-status.json` | 靜態站點狀態鏡像 | `5eb2847893cc2ba21e669e639e40f8df64b275443423529c485f682fd94761b6` |
| 7 | `docs-site/current-work.md` | 靜態站點摘要鏡像 | `4bdbfcf2368660adb4d1e3159ebf3379c8c2d67e3011295832037eb1c4eeabaa` |
| 8 | `docs-site/dashboard-bundle.json` | 靜態站點看板鏡像 | `d26a81e3b6baa4c95bd65265e5bc0184895b79b668f1e3e7e5313fa0210647b6` |
| 9 | `docs-site/orchestrator-state.json`| 靜態站點控制平面鏡像 | `af6273bfd8d0f351c63c8e97d95ad2c38e4650fa8ad4921321fcb25b95a05390` |

### 4.2 主 checkout 同步處置規範 (Safe Sync Procedure)
在 PR 合併進 `dev` 後，主 checkout 的同步作業應遵循以下不可逆防護流程：
1. **快照備份**：同步前完整複製上述 9 個檔案及 `.orchestrator/config.json` 至備份目錄。
2. **分支指針推進**：採用 `git fetch origin dev` 與 `git reset --mixed origin/dev`（或帶衝突隔離的 rebase），保持 working directory 的運行時狀態檔不被 committed 舊版本覆蓋。
3. **版本比對驗證**：比對備份 checksum，確認上述 9 個檔案在同步前後未發生歷史倒退。

---

## 5. Fleet 運行與狀態寫入實測 (Fleet Liveness & Mutation Test)

1. **Supervisor 正常運行**：
   - 觀測 supervisor 定時巡檢程序正常執行，無 crash 或阻塞現象。
2. **Mutating 命令寫入實測**：
   - 執行 `AI_NAME=Antigravity7 "$PANTHEON_STATUS_ROOT/scripts/ai-status.sh" progress ODP-MAIN-CHECKOUT-RESYNC-001 "..."`。
   - 狀態成功寫入 `ai-status.json`，日誌成功附加至 `ai-activity-log.jsonl`，且 GitHub API check 成功發出。
3. **Worker 狀態完整**：
   - 既有 background worker 正常運作，未受任何干擾。

---

## 6. 範圍邊界聲明 (Scope Boundary)

- 本任務未 staged 或 commit 任何 `.orchestrator/` 下的未追蹤檔案（如 locks、temporary fixtures 等）。
- 本任務未變更任何 live config 內容。
- 所有程式碼改動均經由 task PR 審查與交付流程。

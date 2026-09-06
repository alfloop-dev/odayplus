---
evidence_id: ODP-SUPERVISOR-OWNER-PREFERENCE-LIVE-ROLLOUT-001
title: "載入 agy／Claude owner 偏好並驗證 Supervisor 實際派工"
date: 2026-09-06
status: IMPLEMENTED
owner: Claude
operator: Codex
reviewer: Antigravity3
repository: alfloop-dev/odayplus
task: ODP-SUPERVISOR-OWNER-PREFERENCE-LIVE-ROLLOUT-001
base_ref: 62dfc845
source_receipt_sha256: 3d5e7d6461833b33bb021a8330581e0f6feccbf0ca66c153861c006d0f6ba6a9
---

# 載入 agy／Claude owner 偏好並驗證 Supervisor 實際派工

## 1. 概述與授權邊界

本文件記錄 `ODP-SUPERVISOR-OWNER-PREFERENCE-LIVE-ROLLOUT-001` 的 Supervisor 執行控制面 live rollout 與驗證收據。

本次操作為**控制面（Supervisor runtime 與 live config）更新**，由 root Codex 執行 rollout 與健康驗證，並將原始脫敏收據固定後交由 Antigravity 整理唯一 owned 中文 evidence 文件，由獨立 reviewer Claude2 審查。

### 1.1 使用者授權範圍

本次 live rollout 僅包含兩項使用者授權變更：

1. **Owner Provider 偏好啟用**：落實使用者指示「實作優先派給 agy／Claude，root 負責整合、驗證、部署」，啟用 `ready_dispatcher.owner_provider_preference`（`enabled=true`、`preferred_providers=["antigravity", "claude"]`、`task_classes=["implementation", "remediation", "documentation"]`）。
2. **Claude 平行 Slot 擴增**：使用者明確要求 Claude 平行容量由 2 擴增至 5，同步更新 `claude_main.max_concurrent=5` 並擴增實體 slot `claude_slot_3`、`claude_slot_4`、`claude_slot_5` 共用同一 `claude_main` account pool。

### 1.2 非授權／嚴格禁止事項

- **禁止非授權變更**：Agy slots（5）、Codex 總 slots（4，包含 `codex_bjoe`: 2 與 `codex_lupin`: 2）、Watchdog 全域上限（14）、同偏好組內保留原 fallback 候選順序（跨組由偏好 rank 主導）、輪詢週期（180s）、逾時設定與其他 quota 完全保留不變。
- **未動產品程式與 GCP 部署**：本次僅限 Supervisor 本地執行控制面，未部署 GCP 產品 runtime，未啟用第三方來源，未進行 live source readback。
- **不混同未核准修正**：PR #1215（`ODP-MERGE-GROUP-STALE-FAILURE-GUARD-001`）因 review finding 被退回，正由 Claude 接續修正中；本次部署之 runtime `62dfc845` 嚴格基於當時已合併之 dev，不包含任何未核准修補。
- **不製造偽造任務**：選擇器探針在 `ready_count=0` 時如實記錄，禁止建立 synthetic tasks 或刻意製造故障以獲取派工測試證據。

---

## 2. Runtime 替換與版本一致性（Runtime Rollout & Provenance）

本次 rollout 沿用既有唯一 `scripts/orchestrator/rollout_supervisor_runtime.py` 部署原語，從乾淨、與 `origin/dev` 完全一致的隔離 worktree 替換 runtime 軟連結，並原子替換 status launcher。

### 2.1 執行紀錄與版本溯源

| 項目 | 數值 / 狀態 | 說明 |
|---|---|---|
| 入口腳本 | `scripts/orchestrator/rollout_supervisor_runtime.py` | 既有唯一原子 rollout 入口 |
| 部署來源 SHA | `62dfc845925ed1b90bf06c0cbb490a0b82ed9b3a` | 乾淨 worktree，無未追蹤或 dirty 檔案 |
| 對應 PR | [PR #1213](https://github.com/alfloop-dev/odayplus/pull/1213) | `ODP-DISPATCH-PREFERENCE-GUARDS-001` |
| Exact Merge CI | [Run 34004828184](https://github.com/alfloop-dev/odayplus/actions/runs/34004828184) | 結論：`success`（全綠通過） |
| 前一版本 SHA | `04e1572f802a54c2646ba678fe2975226dfbd7c4` | 舊版 Supervisor runtime |
| Supervisor 前 PID | `772131` | 舊 Supervisor 程序 |
| Supervisor 新 PID | `1473872` | 經由 watchdog 重啟之新 Supervisor 程序 |
| 啟動時間 | `2026-09-06T02:18:06Z` | 替換完成並啟動 |
| 執行結果 Exit Code | `0` | 正常退出，未觸發 rollback |
| 回滾狀態 | `rollback_invoked: false` | 無需回滾 |

---

## 3. 設定變更與不變式保留（Configuration & Validation）

### 3.1 Hash 與 Validator 檢驗

| 設定項目 | SHA256 / 檢驗結果 |
|---|---|
| 替換前 Config SHA256 | `01771aa25630879c550b1fd173720567c057f1d6894ebac945a3d35d4ad4f1ea` |
| 替換後 Config SHA256 | `5e9f4279b14ba6f4595317d4064ccebc952c678693ec2abd2bb243a89be8a12c` |
| Config Digest 前綴 | `5e9f4279b14ba6f4`（與 live probe 讀取之 `loaded_config_digest` 逐字相符） |
| Launcher SHA256 | `3660f2423ddf5169c86199d3bf1699ebb34e733ebe9add2182483a9cfb5be9d1` |
| 私有備份目錄 | `/tmp/odp-supervisor-priority-rollout.SmhhVQ`（私有 live config 未 commit、未印出全文） |
| 靜態驗證指令 | `(cd /home/lupin/oday-plus-supervisor-runtime-62dfc845925e && python3 -B delivery_toolchain/governance/check_orchestrator_config.py --config /home/lupin/odayplus/.orchestrator/config.json)`（釘住具備最新 schema 之 runtime checkout 目錄執行） |
| 靜態驗證結果 | `Validated 3 config documents and their merged runtime views.`（通過） |
| 授權範圍比對 | `only_authorized_changes: true` |

### 3.2 授權變更與保留參數對照

```json
{
  "ready_dispatcher": {
    "owner_provider_preference": {
      "enabled": true,
      "preferred_providers": [
        "antigravity",
        "claude"
      ],
      "task_classes": [
        "implementation",
        "remediation",
        "documentation"
      ]
    }
  },
  "account_pools": {
    "claude_main": {
      "max_concurrent": 5
    }
  },
  "agents": {
    "claude_slot_3": {
      "display_name": "claude_slot_3",
      "provider": "claude",
      "adapter": "claude_cli",
      "account_pool": "claude_main",
      "dispatch_slot_for_pool": "claude_main",
      "slot_id": "claude_slot_3"
    },
    "claude_slot_4": {
      "display_name": "claude_slot_4",
      "provider": "claude",
      "adapter": "claude_cli",
      "account_pool": "claude_main",
      "dispatch_slot_for_pool": "claude_main",
      "slot_id": "claude_slot_4"
    },
    "claude_slot_5": {
      "display_name": "claude_slot_5",
      "provider": "claude",
      "adapter": "claude_cli",
      "account_pool": "claude_main",
      "dispatch_slot_for_pool": "claude_main",
      "slot_id": "claude_slot_5"
    }
  }
}
```

- **Schema 邊界說明**：`account_pools.<pool>` 於 `62dfc845` 的 `.orchestrator/config.schema.json` 僅允許 `enabled` / `max_concurrent` / `state`，且 `additionalProperties: false`；本次變更中 `provider` 屬於 `agents.<slot_id>.provider`，`task_classes` 則位於 `ready_dispatcher.owner_provider_preference.task_classes`，兩者皆不屬於 pool 層。上方 JSON 呈現的是本次**授權變更的差異**而非完整物件（live `claude_main` 另保留既有 `state: "healthy"`），其鍵集合合於 schema，與 §3.1 記錄的 validator 通過結果一致。
- **保留參數（完全未動）**：
  - Antigravity slots: 5 個實體 slot（`antigravity_slot_1` ~ `antigravity_slot_5`，account pool: `antigravity_main`，`max_concurrent: 5`）
  - Codex 總 slots: 4（真實 pool 明細為 `codex_bjoe.max_concurrent: 2`、`codex_lupin.max_concurrent: 2`；實體 slot 為 `codex_bjoe_slot_1/2`、`codex_lupin_slot_1/2`）
  - 全域活躍 Worker 上限: 14（`watchdog_max_active_workers`）
  - 同偏好組內保留原 fallback 候選順序（跨組由偏好 rank 主導）
  - 輪詢週期: 180 秒（`poll_interval_seconds`）
  - Quota 生命週期、cooldown 與 worker leases 未被清除或重設

---

## 4. 服務健康與 Worker 存活驗證（Health Loops & Worker Preservation）

Supervisor 替換後，完成連續 3 次健康迴圈，無新增錯誤，既有背景 Worker 保持正常運行。

### 4.1 成功 Loop 紀錄

- Loop 1: `2026-09-06T02:18:35Z`（成功，無 loop error）
- Loop 2: `2026-09-06T02:19:31Z`（成功，無 loop error）
- Loop 3: `2026-09-06T02:22:11Z`（成功，無 loop error）
- 觀察到的 Loop Errors: `0`（`observed_loop_errors: []`）
- Safe mode 到期時間: `2026-09-06T02:20:04Z`（自然過期並退出 safe mode）
- Claude durable effective concurrency: 經由既有 canonical dispatcher 入口平順同步至 5

### 4.2 既有 Worker 存活驗證（未殺除／未重啟）

Rollout 過程中嚴格保護既有 worker process 與 lease，未執行任何 worker kill 或 restart：

| 欄位 | Rollout 前狀態 | Rollout 後狀態 |
|---|---|---|
| Task ID | `ODP-MERGE-GROUP-STALE-FAILURE-GUARD-001` | `ODP-MERGE-GROUP-STALE-FAILURE-GUARD-001` |
| Logical Agent / Provider | `antigravity` / `antigravity` | `antigravity` / `antigravity` |
| Worker PID | `1468859` | `1468859`（持續存活，parent PID: 1） |
| Run ID | `antigravity-20260906T021311Z-503b5068` | `antigravity-20260906T021311Z-503b5068`（同一個 run 完整保留） |
| Queue Event ID | `evt-20260906T021309Z-ed2db5a2` | `evt-20260906T021309Z-ed2db5a2` |
| Lease 獲取時間 | `2026-09-06T02:13:11Z` | 持續有效 |
| Worker 操作判定 | — | `worker_kill_or_restart_performed: false` |

---

## 5. 唯讀選擇器探針（Readonly Selector Probe）

於 `2026-09-06T02:20:11.062799+00:00` 至 `2026-09-06T02:20:12.240774+00:00` 執行 `readonly_dispatch_controls` 探針，在不製造狀態變更的前提下驗證控制面完整性。此探針為 root 一次性唯讀工具，**不存在於 repo 內**，其收據自帶 `missing_runtime_apis` 欄位以揭露缺漏 API；因此它不是可長期沿用的驗收管道（見 §8.3 步驟 3）。

### 5.1 探針執行環境與狀態無損檢驗

- **探針狀態**: `ok`，`usable_as_evidence: true`，`missing_runtime_apis: []`
- **版本與 Digest 吻合**:
  - `loaded_code_sha`: `62dfc845925ed1b90bf06c0cbb490a0b82ed9b3a`
  - `loaded_config_digest`: `5e9f4279b14ba6f4`
  - PID: `1473872`（PID 與 state 一致，process 身份與 CWD 符合 runtime）
- **唯讀無損檢驗（Consistent Read Snapshot）**:
  - 讀取檔案數: 59 個
  - 探針前 Digest: `69f85b7a5c36737f5c168fb7e9c4145c3cd7fa7ccba31d3b363ff520ad10693e`
  - 變更檔案數: `0`（`changed_file_count: 0`，`changed_inputs: []`，`denied_operations: []`）

### 5.2 選擇器投影與受保護控制項檢核

- **看板即時狀態**:
  - Active 保留任務: 2
  - Pending 保留任務: 2
  - 可派工任務數（`ready_count`）: `0`（如實記錄當下無 ready 任務，不捏造任務）
  - 可選擇候選數（`selectable_count`）: `0`
- **略過分類**:
  - 受保護控制項: 10
  - Dispatcher 尚未 ready: 4
  - 非 owner 執行狀態: 4
  - 偏好類別外（outside preference classes）: 2
  - Active 或 Pending 中: 2
- **受保護控制項（Protected Controls）違規檢驗**:

| 控制項類別 | 觀測樣本數 | 違規次數 | 說明 |
|---|---|---|---|
| Reviewer 獨立性 | 22 | 0 | 審查者指派不受 owner 偏好干擾 |
| Finalize 凍結 | 1 | 0 | `review_approved` / closeout 狀態維持 immutable |
| Runtime Release | 5 | 0 | runtime release 工作不受偏好改派 |
| Human / Ops Gate | 4 | 0 | 人工閘門保留人工簽核權限 |
| Helper / Sidecar | 0 | 0 | 誠實揭露無觀測樣本，不宣稱覆蓋 |
| Non-dispatchable | 7 | 0 | 控制面專用任務未被 auto-dispatch 搶派 |

---

## 6. 容量回讀與真實派工觀測（Capacity Readback & Real Dispatch Observations）

### 6.1 容量狀態回讀（`2026-09-06T02:22:17Z`）

- **Supervisor**: PID `1473872`, `loaded_code_sha: 62dfc845`, `loaded_config_digest: 5e9f4279b14ba6f4`, `last_loop_error: null`
- **Configured Pools**:
  - `claude_main`: state `healthy`, `max_concurrent: 5`
  - `antigravity_main`: state `healthy`, `max_concurrent: 5`
- **Durable Pools**:
  - `claude_main`: state `healthy`, `effective_concurrency: 5`, `generation: 0`
  - `antigravity_main`: state `healthy`, `effective_concurrency: 5`, `generation: 0`
- **Physical Slots**: Claude 5, Antigravity 5

### 6.2 真實派工與 Fallback 重排界定

在 live 環境中，觀測到下列真實派工事件：

1. **Claude Slot 2 真實派工（`2026-09-06T02:21:40Z`）**:
   - Task: `ODP-MERGE-GROUP-STALE-FAILURE-GUARD-001`
   - Provider: `claude`（Slot: `claude_slot_2`）
   - PID: `1478358`
   - Run ID: `claude-20260906T022140Z-ce8da890`
   - Queue Event ID: `evt-20260906T022136Z-892aef23`
   - Lease 獲取時間: `2026-09-06T02:21:40Z`
   - **性質界定**: 此為 PR #1215 經 review finding 退回後，正規的 owner 重新指派與接續執行。這是明確的 assigned owner 派工，**不能稱為 fallback 重排序被實際觀測到的證據**。

2. **Antigravity Slot 1 真實派工（`2026-09-06T02:24:59Z`）**:
   - Task: `ODP-SUPERVISOR-OWNER-PREFERENCE-LIVE-ROLLOUT-001`
   - Provider: `antigravity`（Slot: `antigravity_slot_1`）
   - PID: `1481419`
   - Run ID: `antigravity-20260906T022459Z-0ce73cbe`
   - Lease 獲取時間: `2026-09-06T02:24:59Z`
   - **性質界定**: 此為 root Codex 完成 rollout/驗收後，依 task acceptance 將文件整理階段正規指派給 Antigravity。這是正常 assigned owner 派工，**不代表觀測到 fallback 重排或 7/10 同時高負載派工**。

3. **Claude 既有背景 Worker（`2026-09-06T02:16:55Z`）**:
   - Task: `ODP-NLTK-MONITORING-BASELINE-001`
   - Provider: `claude`
   - PID: `1472598`
   - Run ID: `claude-20260906T021655Z-4b09378e`
   - Lease 獲取時間: `2026-09-06T02:16:55Z`

---

## 7. 未執行事項與邊界宣告（Unperformed Operations & Disclosures）

為維持審計嚴謹性，在此明確列出刻意未執行與限制範圍：

1. **未部署 GCP 產品環境**: 未執行 GCP release，未啟用第三方外部 data sources，未進行 GCP live source readback。GCP 正式部署依既有規定需具備 GCP auth 與 Human/Ops 審核閘門。
2. **未混同 PR #1215**: `ODP-MERGE-GROUP-STALE-FAILURE-GUARD-001` 的 head `67bdf2af` 審查退回後由 Claude 接續修正；目前 live 部署之 runtime `62dfc845` 不包含該未核准修正。
3. **未捏造測試任務**: 唯讀探針時 `ready_count=0`，未人工建立 synthetic tasks 以補齊派工路徑測試。
4. **未重跑本地測試**: 本任務進入純文件整理階段（docs-only），不重跑 pytest、ruff 或完整 CI 本地驗證，避免浪費資源與干擾 live 環境。
5. **未覆寫歷史與 Quota**: 未清除任何 quota cooldown、lease 或 activity 歷史。

---

## 8. 回滾程序（Rollback Procedure）

若 live 環境需進行回滾，提供以下三層級處置路徑：

### 8.1 第一級：設定層停用偏好或縮容（需重啟 Supervisor 程序生效）

Supervisor 程序（`supervisor.py`）在 `main()` 啟動時載入一次 config 於記憶體中，主迴圈並無 hot-reload 機制，因此**任何 live config 修改均必須重啟 Supervisor 程序才能生效**。

**唯一支援的重啟路徑**：本文件所有「重啟 Supervisor」一律指 §2 已使用的同一支 `scripts/orchestrator/rollout_supervisor_runtime.py`。不存在其他支援路徑——`scripts/restart-supervisor.sh` 已退役並直接 `exit 2`（其訊息明示 rollout 唯一入口即為該腳本）；手動改檔後等 watchdog 自行接手亦不成立，因為 `.orchestrator/supervisor_watchdog.py:401-403` 在 `health.healthy` 為真時會先判定 `decision=observe_only` / `reason=supervisor_healthy`，早於任何 restart 判斷。

1. **停用偏好排序**：
   將 live config 中的 `ready_dispatcher.owner_provider_preference.enabled` 設為 `false`，或將 `preferred_providers` 設為 `[]`，並依上述唯一入口重啟 Supervisor：
   - 依 schema 與程式定義，`preferred_providers=[]` 時 `owner_preference_ranks` 會對所有候選人回傳 rank 1。
   - 選擇器行為完全回到原有 `(open_task_count, caller_order)` 排序。
   - 不觸發任何額外容量探測。

2. **回滾 Claude 並行容量（縮容至 2）**：
   - **前置安全檢查（必須確認無 active lease）**：在移除 slot 之前，必須先檢查 canonical state 與 active leases，確認 `claude_slot_3`、`claude_slot_4`、`claude_slot_5` 當前均無 active lease 承載中任務（若有任務在執行，需等待其完成並釋放 lease），避免直接刪除 slot 導致正在運行的 worker 孤立或 state 衝突。
   - 確認 slot 空閒後，將 `claude_main.max_concurrent` 改回 `2`，並自 `agents` 移除 `claude_slot_3`、`claude_slot_4`、`claude_slot_5` 定義。
   - 依上述唯一入口重啟 Supervisor 使縮容設定生效。

### 8.2 第二級：縮小偏好適用範圍

自 `task_classes` 移除特定分類（例如僅保留 `implementation`），縮減偏好介入之任務型態。修改後同樣需依 §8.1 所述唯一入口重啟 Supervisor 使設定生效。

### 8.3 第三級：完整 Runtime 與 Config 回滾程序（Symlink & Backup Restore）

本次 Supervisor runtime 採用軟連結原子部署（`/home/lupin/oday-plus-supervisor-runtime-current -> /home/lupin/oday-plus-supervisor-runtime-62dfc845925e`），rollout 原語提供 symlink 與 launcher 替換機制，不依賴也不使用 `git revert`。本節僅界定**備份來源、唯一入口與安全邊界**，不提供手動平行步驟；config 還原始終是 operator 責任。若需完整回滾至前一版本（`04e1572f802a54c2646ba678fe2975226dfbd7c4`），依序執行以下步驟：

1. **先還原備份 Config 與 Launcher**：
   - **前置安全檢查（沿用 §8.1）**：還原 `config.before.json` 會同時移除 `claude_slot_3`、`claude_slot_4`、`claude_slot_5`，因此還原前必須先確認這三個 slot 均無 active lease 承載中任務，避免孤立正在執行的 worker。
   - 自私有備份目錄 `/tmp/odp-supervisor-priority-rollout.SmhhVQ/` 將 `config.before.json` 與 `launcher.before.sh` 還原至 `/home/lupin/odayplus/.orchestrator/config.json` 及 launcher 路徑。
   - **順序必要性**：舊版 runtime `04e1572f` 的 `config.schema.json` 頂層具備 `additionalProperties: false` 且不認識 `owner_provider_preference` 鍵。若未先還原 config 即切換舊 runtime，Supervisor 啟動時會直接觸發 `ConfigError` 驗證失敗。因此必須先還原相容 config。
2. **以唯一 rollout 入口切回舊 runtime**：
   - 沿用 §2 已使用的同一支 `scripts/orchestrator/rollout_supervisor_runtime.py`，以乾淨的 `04e1572f` source worktree 作為 `--source-root`，`--tracking-ref` 釘住 `04e1572f802a54c2646ba678fe2975226dfbd7c4`，並以 `--watchdog-pid-file` 指向 canonical `supervisor.pid`（cron／watchdog 安裝形態）。runtime symlink 與 launcher 的替換由該腳本原子完成。
   - **不得改為「手動切 symlink 後等 watchdog 或 launcher 啟動舊版」**：如 §8.1 所述，`.orchestrator/supervisor_watchdog.py:401-403` 在 Supervisor 健康時直接判定 `observe_only` / `supervisor_healthy`，替換不會發生，記憶體中仍是 `62dfc845` 的碼；`scripts/restart-supervisor.sh` 亦已退役（`exit 2`）。此類手動路徑會靜默失效並被誤判為回滾完成。
   - 有效原語是該腳本的 `restart_with_watchdog()`（`scripts/orchestrator/rollout_supervisor_runtime.py:127-133`）：先對前一個 PID 送出 `SIGTERM` 並等待其退出，watchdog 才會判定不健康而真正重啟程序。
3. **回滾後唯讀驗收（明確排除 §5 探針）**：
   - 僅執行既有唯讀核對：程序 PID、程序實際 cwd 是否指向舊 runtime 目錄、`loaded_code_sha`（`04e1572f802a54c2646ba678fe2975226dfbd7c4`）、`loaded_config_digest`（`01771aa25630879c`），並確認連續通過至少 2 輪無錯誤健康 loop。
   - **不得沿用 §5 的 `readonly_dispatch_controls` 探針**：該探針不存在於 repo 內（見 §5），且 `04e1572f` 並無 `owner_provider_preference` 相關 runtime API；回到舊 runtime 執行該探針會因缺少 API 而非零退出，將正常回滾誤讀為回滾失敗。

---

## 9. 收據來源與摘要核對（Receipt Source Verification）

- **原始收據路徑**: `/tmp/odp-supervisor-priority-rollout.SmhhVQ/live-rollout-receipt.json`
- **原始收據 SHA256**: `3d5e7d6461833b33bb021a8330581e0f6feccbf0ca66c153861c006d0f6ba6a9`（已精準核對）
- **文件所有者**: Claude（本輪）
- **獨立審查者**: Antigravity3（本輪）
- **文件整理沿革**: 首輪文件整理由 Antigravity 進行，經獨立 reviewer Claude2 兩次實測退修（reopen #1 七項、reopen #2 三項）；依 review churn 政策 owner 於 `2026-09-06T03:01:33Z` 改派 Claude 續修，reviewer 改為 Antigravity3。Claude2 兩輪退修的每一項均已由本輪 owner 重新獨立量測後才採納，未僅憑轉述修改。
- **rollout 與健康驗證執行者**: root Codex（歷史事實，本輪未重做 rollout、未更動 runtime／live config／CI／gates、未重啟服務、未重跑測試）

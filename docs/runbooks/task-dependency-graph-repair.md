---
doc_id: ODP-RUNBOOK-TASK-DEPENDENCY-GRAPH-REPAIR
title: Task Dependency Graph Repair Runbook
status: ready-for-maintenance-window
date: 2026-08-03
language: zh-TW
owner: "Platform/Ops"
---

# Task Dependency Graph 修復 Runbook

## 1. 為什麼需要這份 runbook

`scripts/orchestrator/check_task_dependency_resolvability.py` 對 live 狀態掃描
（2026-08-03）回報 **14 個 dangling dependency**，分布在 4 個 task：

| 受影響 task | dangling 依賴數 | 目前狀態 |
|---|---:|---|
| `ODP-RUNTIME-GCP-001` | 1 | blocked |
| `ODP-PRODUCTION-MODEL-REGISTRY-001` | 5 | blocked |
| `ODP-LIVE-RUNTIME-DEV-COMPOSE-001` | 6 | blocked |
| `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` | 2 | blocked |

依 Control Pack §3.1，dependency 必須在 live board 或官方 archive 解析成立才能派工。
這 4 個 task **在圖譜修好之前永遠不會被派工**，其中包含兩條關鍵路徑之一的
`ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001`（另一條是 required provider
的真實 ingestion）。

## 2. 哪些部分需要停機，哪些不需要

> **更正（2026-08-03）**：本節初版斷定整份修復都需要停機窗口。
> 實際查證 `task_archive.py` 後，**§4 的 archive 回填不需要停機**
> （理由見 §4.4）。只有修改 `depends_on` 需要，而在 §4 完成後通常不需要改它。

## 2bis. 為什麼 `ai-status.json` 不能直接改

### 2.1 並發寫入風險

`scripts/ai_status.py` 的 `save_state()` 是
**read → modify → `os.replace()`，沒有任何 file lock**。
live supervisor（本次觀測 PID `1452119`）持續在跑，`ai-status.json` 於
`2026-08-03T13:55:19Z` 仍在被寫入。此時任何外部寫入都可能被 supervisor 的
下一次 `save_state()` 覆蓋，或反向覆蓋掉 supervisor 的變更（lost update）。

### 2.2 CLI 能力不足

`ai_status.py` 的 20 個 mutating command
（`assign`/`start`/`progress`/`note`/`reopen`/`re_review`/`handoff`/`blocker`/
`retarget_blocker`/`prune_agents`/`done`/`restore_approved`/
`restore_approved_head`/`supersede`/`approve`/`archive_migrate`/`sync`/`wave`）
**沒有任何一個可以**：

- 編輯既有 task 的 `depends_on`
- 為「從未出現在 live board」的 task 建立 archive snapshot

因此本修復需要先擴充 CLI，或在停機窗口內以受控腳本直接處理。

## 3. 前置條件

§4 的 archive 回填**不需要停機**，但仍必須先備份。
只有需要改 `depends_on`（§5）時才要停止 live supervisor。

1. 若要進行 §5，取得維護窗口並**停止 live supervisor**（PID 於執行時重新確認）。
2. 備份（兩種情況都要）：
   ```bash
   cp ai-status.json ai-status.json.bak-$(date +%Y%m%dT%H%M%SZ)
   tar czf ai-task-archive.bak-$(date +%Y%m%dT%H%M%SZ).tgz ai-task-archive/
   ```
3. 確認備份可還原後才進行下一步。

## 4. 9 個 dangling 依賴的處置

> **更正（2026-08-03）**：本節初版把 9 個依賴分成「6 個有 repo 證據」與
> 「3 個查無證據、不得建 snapshot」。**該分類是錯的**，因為只搜尋了
> `docs/evidence/` 路徑。改以 git 合併記錄為統一客觀基準後，
> **9 個全部都有合併進 `origin/dev` 的證據**，全部可回溯歸檔。

### 4.1 統一判定基準

不採用文件措辭（「closeout」「完成」等易誤判——例如
`ODP-P10-DEV-LANDING-FIX-001` 的 evidence 文件最後一行仍寫「still requires
all three GitHub CI jobs ... before merge」，但該 task 實際已由 PR #419 合併）。

改用可機器驗證的基準：**`origin/dev` 上存在該 task 的合併 commit**。

### 4.2 驗證結果（全部 9 個，合併時間集中於 2026-07-28）

| 依賴 id | PR | 合併 commit |
|---|---|---|
| `ODP-MODEL-READY-COMPOSE-001` | #425 | `f7f5465f27caa91028a2adf03110c0825c5f7a73` |
| `ODP-P10-R3CD-DEV-COMPOSE-001` | #456 | `726b0b0ddf2ef1f608580c4311d31f18fd9d1a99` |
| `ODP-P10-DEV-LANDING-FIX-001` | #419 | `c7c6e925ebdc5a5026b25ca2c3319ca9139ec7e7` |
| `ODP-HEATZONE-PIT-LABEL-AUTHORITY-001` | #443 | `ceb9435aaaf36c23a8fdc203ff749157d5beb3bf` |
| `ODP-AUTH-RUNTIME-RECONCILE-001` | #447 | `bd0f46284ab75469c1ec176820074875a7df43ac` |
| `ODP-LEARNINGHUB-PROD-FIX-001` | #440 | `b607d216144869014b5eca50ab552c5ba7f6bb41` |
| `ODP-OPERATOR-LIVE-PREFLIGHT-001` | #460 | `afdb7e215e7cc087f9d4209b75e01330a1a5d280` |
| `ODP-FORECAST-LEARNINGHUB-TEMPORAL-COMPOSE-001` | #451 | `e874ec4f70ce4eb116fdec0094923685f39ee5a3` |
| `ODP-MODEL-CAPABILITY-READINESS-001` | #457 | `3ecdcdf1c2f0a98e5218a7989d4dae9bd48617c4` |

這是一批 2026-07-28 完成並合併、但從未寫入 `ai-task-archive/` 的 task。

### 4.3 執行方式

使用 `scripts/orchestrator/backfill_task_archive_snapshots.py`。它會在寫入前
**自行重新驗證每個 task 的合併 commit**，查不到就 fail closed 跳過該筆，
因此不會依賴本文件表格是否過時。

```bash
# 先看計畫，不寫入
python3 scripts/orchestrator/backfill_task_archive_snapshots.py \
  --archive-dir <path>/ai-task-archive/tasks --repo . --dry-run

# 確認後寫入
python3 scripts/orchestrator/backfill_task_archive_snapshots.py \
  --archive-dir <path>/ai-task-archive/tasks --repo . --apply
```

寫入的 snapshot 形狀（符合 `task_archive.py` 的解析規則
`task_satisfies_dependency` = `task.status == "done"` 且
`terminal_outcome != "superseded"`）：

```json
{
  "version": 1,
  "task_id": "<ID>",
  "archived_at": "<merge commit committer date>",
  "terminal_status": "done",
  "terminal_outcome": "completed",
  "task": {
    "id": "<ID>",
    "status": "done",
    "owner": "UNKNOWN-HISTORICAL",
    "reviewer": "UNKNOWN-HISTORICAL",
    "artifacts": ["<repo evidence paths if any>"]
  },
  "handoffs": [],
  "blockers": [],
  "backfill": {
    "retroactive": true,
    "created_by": "ODP-RUNBOOK-TASK-DEPENDENCY-GRAPH-REPAIR",
    "basis": "merge commit on origin/dev",
    "merge_commit": "<sha>",
    "merge_pr": "<#NNN>",
    "note": "Derived from repository merge evidence, not from a live lifecycle transition."
  }
}
```

`backfill.retroactive` 是必要的誠實標記，稽核時必須能分辨回溯歸檔與真實
lifecycle 轉換。

### 4.4 為什麼這一步不需要停機

`task_archive.load_archived_snapshot()` 依 **檔名直接解析**
（`ARCHIVE_DIR/tasks/<id>.json`），不經 `index.json`；而 `save_state()`
只寫 `ai-status.json`。兩者互不重疊，因此**新增 archive 檔案不會與執行中的
supervisor 產生 lost-update**。

`index.json` 僅供 `recent_terminal_summaries()` 顯示用，且
`rebuild_archive_index()` 是 glob 重建，會自行收斂。**不要手動改 `index.json`**
——那才是共用檔案。

### 4.5 仍需停機的部分

`depends_on` 欄位若需修改（例如 §5 的循環依賴拆解），因位於 `ai-status.json`
內，仍必須在停機窗口進行。但完成 4.3 後，9 個 dangling 依賴全部可解析，
**14 個 failure 應全數消除，不需要改任何 `depends_on`**。

## 5. 打破循環依賴（R3）

目前：

```
ODP-RUNTIME-GCP-001 ──▶ ODP-PRODUCTION-MODEL-REGISTRY-001
                              │ acceptance 要求 remote MLflow 解析 production alias
                              ▼ 需要 live GCP runtime
                        ODP-RUNTIME-GCP-001
```

將 `ODP-PRODUCTION-MODEL-REGISTRY-001` 拆為兩個 task：

| 新 task | 範圍 | depends_on |
|---|---|---|
| `ODP-PRODUCTION-MODEL-REGISTRY-INFRA-001` | MLflow tracking/registry 可達性、artifact bucket 綁定、alias 解析機制、四個能力的 governed-disabled binding 契約與 reason code | （空） |
| `ODP-PRODUCTION-MODEL-REGISTRY-GOVERNANCE-001` | ForecastOps 真實訓練 → DEV → SHADOW → production alias → model card → rollback candidate → live inference smoke | `…-INFRA-001`、`ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` |

然後把 `ODP-RUNTIME-GCP-001` 與 `ODP-LIVE-RUNTIME-DEV-COMPOSE-001` 的依賴
由 `ODP-PRODUCTION-MODEL-REGISTRY-001` 改指向 `…-INFRA-001`。

原 task 以 `supersede` 指向新的兩個 task（Control Pack §3.4.3 要求
replacement 必須存在於 canonical state、保存相同 packet/gap scope、不得成環）。

## 6. 驗證

### 6.1 正式執行記錄（2026-08-03T15:05Z，已完成）

於 live 狀態實際執行，supervisor 未停機：

```
備份 STAMP  20260803T150519Z
APPLY       Wrote 9 snapshot(s)
CHECK       Task dependency resolvability: OK (46 task(s) scanned)
```

執行後驗證：

- supervisor（PID 1452119）持續執行未中斷
- `ai-status.json` 最後寫入時間仍為 14:21:00Z，**未被觸碰**，確認無 race
- archive 檔案數 33 → 42
- 完全沒有修改任何 `depends_on`

### 6.2 「可解析」與「已滿足」是兩件事

`check_task_dependency_resolvability.py` 驗的是 Control Pack §3.1 的
**resolvable**（依賴存在於 board 或 archive）。官方
`TaskResolver.dependency_satisfied()` 另外要求依賴本身 **status = done**。

用官方 resolver 對 4 個受影響 task 複驗：

| Task | deps | 未滿足 | 說明 |
|---|---:|---:|---|
| `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` | 2 | **0** | 可派工 |
| `ODP-PRODUCTION-MODEL-REGISTRY-001` | 5 | **0** | 可派工 |
| `ODP-LIVE-RUNTIME-DEV-COMPOSE-001` | 7 | 1 | 等 MODEL-REGISTRY 完成 |
| `ODP-RUNTIME-GCP-001` | 3 | 2 | 等上述兩者完成 |

後兩者的未滿足依賴是**正常的工序先後**，不是圖譜損壞——它們等的是
board 上尚未完成的真實 task。

> **更正**：本文件先前寫「§5 的循環依賴拆解不是解除阻塞的必要條件」。
> **該敘述錯誤。** 對 resolvability 而言正確，但 `ODP-RUNTIME-GCP-001`
> 仍卡在 §5 描述的循環：它等 `ODP-PRODUCTION-MODEL-REGISTRY-001` 完成，
> 而後者的 acceptance 要求 remote MLflow 解析 production alias，
> 那需要 live GCP runtime。**§5 的 INFRA／GOVERNANCE 拆分仍是必要的。**

### 6.3 正式執行後的例行驗證

修復完成後執行：

```bash
make task-dependency-check \
  ODP_SUPERVISOR_STATUS_FILE=<path>/ai-status.json \
  ODP_SUPERVISOR_ARCHIVE_DIR=<path>/ai-task-archive/tasks
```

通過條件：exit code 0，輸出 `Task dependency resolvability: OK`。

同時確認：

- 4 個受影響 task 不再是 dangling 狀態
- `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` 可被派工
- 無新的 cycle 或 duplicate lifecycle 違規

## 7. 防止復發

`scripts/orchestrator/check_task_dependency_resolvability.py` 已納入
`make task-dependency-check`。建議進一步：

1. 接進 supervisor 的 dispatch preflight，對每個候選 task 執行
   `--task <id>`，不通過就不派工；
2. 為 `ai_status.py` 的 `save_state()` 加上 file lock，消除 lost-update race；
3. 為 CLI 新增 `archive_import` 指令，讓回溯 archive 有官方、可稽核的路徑，
   不必再靠停機窗口手動處理。

## 7bis. Dependency edge 的 canonical mutation 與啟動 gate

後續 dependency edge 不得透過直接編輯 `ai-status.json`、臨時腳本或
`assign` 對既有 task 的環境變數偷渡。新增 task 時，`assign` 會驗證
`TASK_DEPENDS_ON`；既有 task 必須使用 canonical CLI：

```bash
AI_NAME=<owner-or-reviewer> "$PANTHEON_STATUS_ROOT/scripts/ai-status.sh" \
  set_dependencies <task-id> <dep1,dep2|-> "中文修改理由"
```

該命令在同一個 status transaction 內先檢查：self-edge、重複 edge、dangling
task、同時存在於 live board 與 official archive 的 duplicate lifecycle、未完成
archive，以及 candidate 可達範圍內的 cycle。檢查失敗時不會改動 task；成功時會
在 `ai-activity-log.jsonl` 寫入 `type=dependency_update`、舊/新 edge 與修改理由。

驗證會檢查被編輯 task 的可達 dependency closure。若 closure 內已有 dangling、
self/cycle 或 archive 未完成問題，不以更新另一個 task 的方式放寬 fail-closed
規則；操作員應先從受損節點開始，用同一個 CLI 清除或修正其 edge（例如
`set_dependencies <task-id> - "修復既有圖譜"`），再由下游往上重建依賴。這讓修圖
本身仍可稽核，也不會在修復期間新增另一條不合法邊。

若 dependency 同時出現在 live board 與 official archive，這是 duplicate
lifecycle，不採用 `TaskResolver` 的 live-precedence 來掩蓋衝突；所有依賴該節點
的派工會保持 fail closed。操作員必須先依 archive/board 的正式生命週期記錄清理
重複來源，再重試 `set_dependencies` 或 dispatch；不得用臨時覆寫或第二套 resolver
繞過檢查。

Supervisor 對 owner execution 與 helper 的啟動使用既有 graph gate；依賴未完成、
圖譜無法解析或含 self/cycle/dangling 時一律 fail closed。finalize 是已合併 PR
的 immutable closeout，不是可執行 task dispatch，因此不受 dependency edge 影響，
也不會因 `set_dependencies` 產生第二個 finalize event。已建立的 execution
`owned_*_dispatch` queue event 只代表當時的候選資格，worker 啟動前會重讀
canonical board 並重新驗證 dependency；stale blocked recovery 也不得只依
blocker prose 判定可重派。這是既有 task graph 的 preflight，不另建 pause 或
scheduler。

## 8. 回滾

任一步驟出錯：

```bash
cp ai-status.json.bak-<stamp> ai-status.json
rm -rf ai-task-archive/ && tar xzf ai-task-archive.bak-<stamp>.tgz
```

再重啟 supervisor 並重跑 §6 驗證，確認回到修復前狀態。

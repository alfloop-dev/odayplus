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
這 4 個 task **在圖譜修好之前永遠不會被派工**，其中包含目前的唯一關鍵路徑
`ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001`。

## 2. 為什麼不能直接改

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

1. 取得維護窗口，**停止 live supervisor**（PID 於執行時重新確認）。
2. 備份：
   ```bash
   cp ai-status.json ai-status.json.bak-$(date +%Y%m%dT%H%M%SZ)
   tar czf ai-task-archive.bak-$(date +%Y%m%dT%H%M%SZ).tgz ai-task-archive/
   ```
3. 確認備份可還原後才進行下一步。

## 4. 9 個 dangling 依賴的處置

### 4.1 有 repo 完成證據 → 補 archive snapshot（6 個）

| 依賴 id | repo 證據路徑 |
|---|---|
| `ODP-AUTH-RUNTIME-RECONCILE-001` | `docs/evidence/runtime/ODP-AUTH-RUNTIME-RECONCILE-001.md` |
| `ODP-MODEL-READY-COMPOSE-001` | `docs/evidence/model_ready/ODP-MODEL-READY-COMPOSE-001.md` |
| `ODP-LEARNINGHUB-PROD-FIX-001` | `docs/evidence/completion/ODP-LEARNINGHUB-PROD-FIX-001` |
| `ODP-HEATZONE-PIT-LABEL-AUTHORITY-001` | `docs/evidence/runtime/ODP-HEATZONE-PIT-LABEL-AUTHORITY-001` |
| `ODP-P10-DEV-LANDING-FIX-001` | `docs/evidence/fleet_dispatch/package10_20260726/ODP-P10-DEV-LANDING-FIX-001.md` |
| `ODP-OPERATOR-LIVE-PREFLIGHT-001` | `docs/evidence/completion/ODP-OPERATOR-LIVE-PREFLIGHT-001` |

每個補一份 `ai-task-archive/tasks/<id>.json`，最低必要欄位（比照既有 snapshot）：

```json
{
  "version": 1,
  "task_id": "<ID>",
  "archived_at": "<ISO-8601>",
  "terminal_status": "done",
  "terminal_outcome": "completed",
  "task": {
    "id": "<ID>",
    "status": "done",
    "owner": "<原 owner，若不可考則記 UNKNOWN-HISTORICAL>",
    "reviewer": "<原 reviewer，同上>",
    "artifacts": ["<上表 repo 證據路徑>"]
  },
  "backfill_note": "Retroactive archive snapshot created by ODP-RUNBOOK-TASK-DEPENDENCY-GRAPH-REPAIR on <date>. Terminal status derived from repository completion evidence, not from a live lifecycle transition."
}
```

> `backfill_note` 是必要的誠實標記：這些 snapshot 是回溯建立的，不是真實
> lifecycle 轉換產生的。稽核時必須能分辨兩者。

### 4.2 查無任何證據 → 需人工裁決（3 個）

| 依賴 id | 建議處置 |
|---|---|
| `ODP-FORECAST-LEARNINGHUB-TEMPORAL-COMPOSE-001` | 確認是否真的存在過。若否，從 `depends_on` 移除並記錄決策 |
| `ODP-MODEL-CAPABILITY-READINESS-001` | 同上 |
| `ODP-P10-R3CD-DEV-COMPOSE-001` | 同上 |

**不得**為這三個建立 archive snapshot —— 沒有證據就宣告 done 等同偽造完成紀錄。
正確做法是移除依賴並在 task note 記錄「依賴不存在，經 <owner> 於 <date> 裁決移除」。

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

修復完成後、**重啟 supervisor 之前**執行：

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

## 8. 回滾

任一步驟出錯：

```bash
cp ai-status.json.bak-<stamp> ai-status.json
rm -rf ai-task-archive/ && tar xzf ai-task-archive.bak-<stamp>.tgz
```

再重啟 supervisor 並重跑 §6 驗證，確認回到修復前狀態。

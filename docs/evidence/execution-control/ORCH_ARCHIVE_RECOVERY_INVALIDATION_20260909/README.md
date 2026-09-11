# Archive Recovery Invalidation & Effective Blocked Resolver

**Task ID**: ORCH-ARCHIVE-RECOVERY-INVALIDATION-001  
**Author**: Claude / Antigravity3  
**Reviewer**: Codex  
**Date**: 2026-09-09  

---

## 1. 背景與問題陳述

在先前的歷史還原批次中，`ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001` 與 `ODP-MODELREADY-QUALITY-NULLABLE-001` 因回填規則被判定為完成（`done`）並寫入 `ai-task-archive/tasks/`。然而後續審查發現：
1. `ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001`：缺少 Human/Ops 權威決策與對應證據，下游 `ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001` 不得視為前置完成。
2. `ODP-MODELREADY-QUALITY-NULLABLE-001`：缺少 ModelReadyRecord 缺值生產入口驗證，下游 `ODP-CANONICAL-MEASUREMENT-NULLABLE-CUTOVER-001` 不得視為歷史已驗收。

既有控制面與 resolver 存在以下治理限制：
- Canonical writer 禁止直接對已封存（archived）任務執行 `reopen` 或原地修改。
- 原始 snapshot、index、recovery batch、maintenance hold 與 checkpoint 具有不可變（immutable）審計契約，嚴禁竄改歷史 bytes。
- 舊版 resolver 僅以 snapshot 的 `status=done` 判定依賴成立，無法撤回依賴完成語意。

---

## 2. 架構設計與安全邊界

本修補遵循 Pantheon 治理邊界與單一權威來源原則（Single Source of Truth）：

### 2.1 原始封存不可變性（Archive Immutability）
- 嚴禁改寫 `ai-task-archive/tasks/*.json`、`index.json`、recovery batch、maintenance hold 或 checkpoint 原始檔案。
- `load_archived_snapshot` 始終保留原始歷史快照與完全一致的唯讀 readback。

### 2.2 原子追加更正記錄（Append-Only Per-Task Correction）
- 更正記錄以原子方式寫入 `ai-task-archive/corrections/<task-id>.json`。
- 更正資料結構包含：
  - `schema_version`: 1
  - `type`: `archive_recovery_invalidation`
  - `task_id`: 目標任務 ID
  - `snapshot_sha256`: 原始 snapshot 檔案之 SHA256 雜湊
  - `invalidated_at`: ISO 8601 UTC 時間戳記
  - `actor`: 真實操作者（由 `current_actor_validated()` 獲取）
  - `coordination_task_id`: 授權協調任務 ID
  - `reason`: 撤銷原因
  - `evidence_ref`: 證據參照路徑
  - `effective_status`: `"blocked"`
  - `dependency_satisfied`: `false`

### 2.3 權限與獨立審查者檢查（Authorization & Reviewer Separation）
- 操作者身份強制透過 `current_actor_validated()` 獲取，不可透過參數自稱或偽造。
- 只有指定的協調任務（coordination task）的**真實 Reviewer** 才能執行撤回。
- 協調任務的 Owner 與 Reviewer 必須獨立（`owner != reviewer`）。
- 若非協調任務 Reviewer 執行，立即拒絕。

### 2.4 窄範圍撤回防護（Narrow-Scope Guardrails）
- **只能撤銷回填任務**：目標 snapshot 必須具備 `history_recovery.reconstructed=true`，普通歷史封存不可任意撤回。
- **活躍任務防護**：目標任務若已在活躍看板（active board），拒絕執行。
- **快照指紋比對（CAS/Hash Check）**：支援 `--expected-sha256` 比對；若磁碟上 snapshot 雜湊不符，立即拒絕寫入。
- **冪等性（Idempotency）**：相同參數重跑安全返回 `already_invalidated`；若存在不同更正內容，拒絕覆蓋。

### 2.5 唯一 Resolver 語意與 Fail-Closed 保證
- `task_archive.TaskResolver` 與 `task_archive.load_archived_task` 是 Supervisor 及 canonical writer 的唯一解析路徑，不建第二 scheduler/gate。
- 存在更正檔之任務回傳 effective `status="blocked"`, `terminal_outcome=None`, `blocked_by_invalidation=True`。
- `TaskResolver.dependency_satisfied()` 對該任務回傳 `False`，`dependency_status()` 回傳 `"blocked"`。
- **Fail-Closed 原則**：若更正檔存在但格式損壞（Invalid JSON）、非 JSON 物件、ID/hash 不符或缺必要欄位，該任務一律判定為 effective `status="blocked"`，絕不默默忽略放行；且拒絕寫入覆寫，保留磁碟上的原始 bytes。
- `ai-status show` 清楚區分 `effective_status` (blocked), `effective_task`, `correction`, 及原始 `snapshot` (done)。

---

## 3. 操作手冊（Operations Guide）

### 3.1 Dry Run 演練（預設無副作用，全狀態零寫入）

```bash
AI_NAME=Codex ./scripts/ai-status.sh archive_recovery_invalidate \
  ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001 \
  --coordination-task ORCH-ARCHIVE-HISTORY-EXECUTE-003 \
  --reason "Human/Ops decision missing for merge queue disposition audit" \
  --evidence-ref docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/control_plane_remediation_blocker_20260909.json \
  --expected-sha256 <snapshot-sha256>
```

預期輸出：
```json
{
  "status": "dry_run",
  "action": "archive_recovery_invalidation",
  "task_id": "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
  "snapshot_path": "ai-task-archive/tasks/ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001.json",
  "snapshot_sha256": "...",
  "actor": "Codex",
  "coordination_task_id": "ORCH-ARCHIVE-HISTORY-EXECUTE-003",
  "reason": "Human/Ops decision missing for merge queue disposition audit",
  "evidence_ref": "docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/control_plane_remediation_blocker_20260909.json",
  "effective_status": "blocked",
  "dependency_satisfied": false,
  "message": "Dry run succeeded with zero mutations. Re-run with --confirm to write correction record."
}
```

### 3.2 確認寫入更正（--confirm）

```bash
AI_NAME=Codex ./scripts/ai-status.sh archive_recovery_invalidate \
  ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001 \
  --coordination-task ORCH-ARCHIVE-HISTORY-EXECUTE-003 \
  --reason "Human/Ops decision missing for merge queue disposition audit" \
  --evidence-ref docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/control_plane_remediation_blocker_20260909.json \
  --expected-sha256 <snapshot-sha256> \
  --confirm
```

預期輸出：
```json
{
  "status": "applied",
  "action": "archive_recovery_invalidation",
  "task_id": "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
  "correction_path": "ai-task-archive/corrections/ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001.json",
  "correction": {
    "schema_version": 1,
    "type": "archive_recovery_invalidation",
    "task_id": "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
    "snapshot_sha256": "...",
    "invalidated_at": "2026-09-09T...",
    "actor": "Codex",
    "coordination_task_id": "ORCH-ARCHIVE-HISTORY-EXECUTE-003",
    "reason": "Human/Ops decision missing for merge queue disposition audit",
    "evidence_ref": "docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/control_plane_remediation_blocker_20260909.json",
    "effective_status": "blocked",
    "dependency_satisfied": false
  },
  "effective_status": "blocked",
  "dependency_satisfied": false
}
```

### 3.3 狀態檢視與 Readback

```bash
AI_NAME=Codex ./scripts/ai-status.sh show ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001
```

預期輸出結構：
```json
{
  "source": "archive",
  "effective_status": "blocked",
  "effective_task": {
    "id": "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
    "status": "blocked",
    "terminal_outcome": null,
    "blocked_by_invalidation": true,
    "invalidation": {
      "schema_version": 1,
      "type": "archive_recovery_invalidation",
      "task_id": "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
      "snapshot_sha256": "...",
      "invalidated_at": "2026-09-09T...",
      "actor": "Codex",
      "coordination_task_id": "ORCH-ARCHIVE-HISTORY-EXECUTE-003",
      "reason": "Human/Ops decision missing for merge queue disposition audit",
      "evidence_ref": "docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/control_plane_remediation_blocker_20260909.json",
      "effective_status": "blocked",
      "dependency_satisfied": false
    },
    "next": "Archive recovery invalidated by ORCH-ARCHIVE-HISTORY-EXECUTE-003: Human/Ops decision missing for merge queue disposition audit"
  },
  "correction": { ... },
  "snapshot_path": "ai-task-archive/tasks/ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001.json",
  "snapshot": { ... }
}
```

---

## 4. 驗證與測試（Verification）

本任務提供完整隔離測試套件，涵蓋以下測試項目：
- 權限檢驗：必須由協調任務真實 Reviewer 執行，Owner 與 Reviewer 不獨立時拒絕。
- 目標限制：拒絕活躍任務、拒絕非 reconstructed 歷史任務、拒絕快照雜湊不符。
- 零寫入驗證：Dry run 模式在全狀態根目錄下（含 `ai-status.json`, `current-work.md`, `ai-activity-log.jsonl`, `ai-task-archive/`）達成 100% 零寫入。
- 寫入與審計：Confirm 模式原子寫入更正檔並追加 activity log，原始 snapshot/index 保持不可變。
- 冪等性與衝突防護：相同參數重跑回傳 already_invalidated，衝突參數拒絕覆蓋。
- Fail-Closed 保證：格式損壞或欄位不符之更正檔判定為 blocked，且拒絕覆蓋並保留磁碟 bytes。
- 下游阻擋驗證：`ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001` 與 `ODP-MODELREADY-QUALITY-NULLABLE-001` 更正後，其下游任務 `ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001` 與 `ODP-CANONICAL-MEASUREMENT-NULLABLE-CUTOVER-001` 之 `dependencies_satisfied` 均判定為 `False`。

執行命令：
```bash
uv run --python 3.12 pytest .orchestrator/test_task_archive_recovery_invalidation.py .orchestrator/test_task_dependency_gate.py -v
```

---

## 5. 交付邊界與協調說明（Delivery Boundary & Handoff）

- 本 task **僅交付工具修補與隔離測試**。
- 本 task **不套用 live correction、不停 Supervisor、不修改其他任務工作樹或 active 看板**。
- 本 PR 合併後，由前景協調者完成 Supervisor runtime 更新與部署，再由協調任務 `ORCH-ARCHIVE-HISTORY-EXECUTE-003` 在前景完成 live invalidation 套用、readback 驗證與收尾。

---

## 6. 回退與版本降級風險警告（Rollback Risk Warning）

> [!WARNING]
> **關鍵回退風險（Rollback Risk）**：
> 舊版 Runtime（本修補合併前之程式碼）並不具備讀取 `ai-task-archive/corrections/` 的能力。
> 若在套用 invalidation 更正後將 Supervisor 或 ai-status 程式碼降版至未修補版本，舊版 resolver 將會忽略更正記錄，直接從原始 snapshot 讀取 `status=done`，導致錯誤放行依賴。
> **因此：一旦更正記錄落地，嚴禁未經協調直接將控制面 runtime 降版。**


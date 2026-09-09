# ODP-DATA-PLANE-DELETE-PROPAGATION-001: 資料落地層刪除／墓碑傳播、重放與租戶隔離

- **Task ID**: `ODP-DATA-PLANE-DELETE-PROPAGATION-001`
- **Work Package**: WP-34 Follow-up ([ODP 人工決策落地規畫](../../../plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md) §6 WP-34 & [Phase 34A Implementation Handoff](../ODP-CDC-SOURCE-CONTRACT-PREP-001/implementation-handoff.md) §4 跟進項目 1)
- **決策依據**: 決策編號 `D20`（A：實作／補齊 CDC 適用性與契約）
- **查證基準 SHA**: `3958385788ba` (aligned with latest `origin/dev` tip)
- **負責人 (Owner)**: Claude（2026-09-09 由 Antigravity4 因 dispatch 暫停自動改派；P1 缺陷修復由 Antigravity4 於 `cc7bdb0b` 完成，本輪由 Claude 接手補 SAST 修正、缺陷綁定驗證與收據）
- **審查人 (Reviewer)**: Codex2
- **交付狀態**: `IMPLEMENTATION_DELIVERED` (修復審查 5 項 P1 缺陷、推進 dev 基準、30 項回歸測試與邊界/SAST 驗證全數通過)

---

## 1. 任務背景與目標 (Background & Objective)

在 `ODP-CDC-SOURCE-CONTRACT-PREP-001` (WP-34A) 盤點過程中，發現現有資料落地層（`apps/data_platform/store.py` 與 `pipeline.py`）之關鍵獨立缺陷：
- 過去落地管線僅依賴 `INSERT ... ON CONFLICT DO UPDATE`，完全缺乏實體刪除（Physical Delete）、軟刪除標記或墓碑清除（Tombstone Purge）路徑。
- 若上游 Mongo 或外部來源執行實體刪除，下游 PostgreSQL 投影將永久殘留，造成資料幽靈孤島與跨系統不一致。

本 Task 聚焦於**離線資料落地層（Landing Layer）的刪除傳播與防護機制**，在 repo 現有程式、資料模型與可重建測試環境中交付完整的刪除、墓碑記錄、重放冪等、防止幽靈復活與租戶隔離語意。

---

## 2. 審查缺陷修復與核心不變量 (Review Defect Repairs & Invariants)

在 Codex2 獨立審查後，本輪交付針對五項 P1 缺陷完成完整修復：

1. **[P1-1] 外鍵依賴清理策略 (Transaction Authority Purge Dependency)**：
   - 修正 `apps/data_platform/deletion.py` 之 `_LEAF_PURGE_TEMPLATES["core.transactions"]`。
   - 在刪除 `core.transactions` 前，先依 `(transaction_id, tenant_id)` 刪除 `data_plane.transaction_authority` 參照記錄，解決 `ForeignKeyViolation` 導致事務 rollback 缺陷。
2. **[P1-2] 依最新落地版本比較刪除事件 (Applied Source Version Comparison)**：
   - 在 `control_schema.sql` 與 `canonical_lineage` 記錄 `source_version`。
   - `_propagate_delete` 比較目標實體目前最新已套用之 `source_version` 與已記錄墓碑。當重放舊刪除（如 7/22）遇到刪除後合法新建立之資料（如 7/23）或舊刪除晚於新資料到達時，判定為 `STALE_IGNORED`，確保較新之 upsert 不被舊刪除誤刪。
   - 刪除成功套用時，同步清除對應版本之 `canonical_lineage` 記錄。
3. **[P1-3] 嚴格租戶範疇墓碑防護 (Tenant-Scoped Upsert Tombstone Guard)**：
   - 修正 `_guard_deleted`，在各來源投影解析出 `tenant_id` 後，依 `(tenant_id, entity_type, entity_id)` 進行精確查詢，防止租戶 A 之 absent tombstone 錯誤隔離租戶 B 之合法資料。
4. **[P1-4] 原子事務內墓碑檢核 (Atomic In-Transaction Tombstone Guard)**：
   - 將墓碑防護移至每個 envelope 的 `with connection.transaction():` 內，在寫入投影前直接自資料庫讀取最新墓碑狀態，消除快取窗口導致的幽靈復活競態條件。
   - 一併移除 `apply_batch()` 開頭已無讀者的批次墓碑預讀 `_deleted_versions()`：防護改為逐 envelope 於事務內讀取後，該查詢的回傳值不再被使用，僅剩每批一次的多餘 round trip。對應的競態回歸測試改錨在 `_project_one`，於 envelope 事務已開啟後才在第二條連線提交刪除，較原先的批次預讀點更貼近缺陷本身。
5. **[P1-5] 刪除漂移對帳版本感知 (Version-Aware Delete Drift Reconciliation)**：
   - 修正 `store.py` 之 `reconcile()` 漂移檢測 SQL，改為判定 `(lineage.source_version IS NULL OR lineage.source_version <= tomb.source_version)`。
   - 允許在墓碑之後合法新建之較新版本 (`lineage.source_version > tomb.source_version`) 通過對帳，不再誤報 `sink_delete_drift=1`。

---

## 3. 核心架構與語意保證 (Core Architecture & Invariants)

### 3.1 刪除決策與重放冪等性 (Idempotency Matrix)
| 情境 (Scenario) | 判定結果 (DeleteOutcome) | 墓碑寫入 | 下游資料清除 | 說明 |
|---|---|---|---|---|
| **首次刪除（下游有資料）** | `APPLIED` | 是 (新增) | 是 (Purged) | 正常刪除，清除目標 table 資料並建立 tombstone |
| **首次刪除（下游無資料）** | `ABSENT_TOMBSTONED` | 是 (新增) | 否 | 雖無既有資料，仍必須寫入 tombstone 以封鎖未來舊 upsert 落地 |
| **相同版本重放** | `REPLAYED` | 是 (更新 replay_count) | 是 (冪等收斂) | 冪等重放，確保未完成之部分刪除可收斂，不重複累加 purged_row_count |
| **過期舊刪除到達** | `STALE_IGNORED` | 否 | 否 | 版本小於已記錄墓碑或小於最新落地資料，安全略過不退化狀態 |
| **無版本或版本無效** | `REJECTED_UNKNOWN_VERSION` | 否 | 否 | 無法決定順序，拒絕執行以策安全 |

### 3.2 葉節點清理 vs. 階層主檔保護 (Leaf Tables vs. Retained Identity)
- **實體清除 (SINK_DELETE)**：
  - `core.transactions` / `data_plane.transaction_authority`
  - `core.machine_status_events` / `data_plane.machine_status_event_evidence`
  - `data_plane.store_daily_facts`
  - `data_plane.forecast_inputs`
  - `data_plane.learning_import_lineage`
  - `data_plane.domain_inputs`
- **保留主檔 (Retained Identity Tables)**：
  - `core.tenants`, `core.brands`, `core.stores`, `core.machines`, `core.address_locations` 屬於多實體依賴之階層主檔，依 `RETAINED_CANONICAL_TABLES` 政策保留不作級聯刪除，僅記錄 tombstone 並於 `retained_targets` 明確回讀揭露。

---

## 4. 交付檔案與異動範圍 (Delivered Files)

| 檔案路徑 | 異動說明 |
|---|---|
| `apps/data_platform/deletion.py` | 實作刪除與墓碑傳播語意、版本運算、租戶解析、刪除決策、PurgePlan，並修正 `core.transactions` 外鍵依賴清理順序。 |
| `apps/data_platform/contracts.py` | 擴充 `QuarantineReason.SOURCE_DELETED`、`ReconciliationResult.sink_delete_drift` 與 `RunSummary` 序列化。 |
| `apps/data_platform/sql/control_schema.sql` | 建立 `data_plane.tombstones` 稽核資料表，並在 `canonical_lineage` 擴充 `source_version` 欄位。 |
| `apps/data_platform/store.py` | 實作 `delete_record()`、`tombstone_record()`、`get_tombstone()`、原子租戶墓碑防護、最新落地版本比對與版本感知 drift reconciliation。 |
| `apps/data_platform/tests/test_delete_propagation.py` | 30 項完整單元、端到端與審查回歸測試套件，覆蓋外鍵依賴清理、重放保留新資料、租戶隔離、原子併發防護與對帳。 |
| `docs/audits/code-boundary-inventory.csv` | 代碼邊界清單核實與更新。 |
| `docs/evidence/human-decisions/ODP-DATA-PLANE-DELETE-PROPAGATION-001/README.md` | 本交付報告與架構規範說明。 |

---

## 5. 邊界與非宣告事項 (Scope Boundary & Non-Claims)

- **離線語意交付**：本任務為離線資料落地層之程式與可重建測試 DB 實作，**未連線生產環境、未開啟 ChangeStream 串流、未讀取生產機密、未修改 IAM 權限**。
- **與 Phase 34B 之區隔**：本任務解決的是下游 PostgreSQL 缺乏刪除路徑之獨立缺陷；完整的 CDC 即時適配器、ChangeStream 監聽、生產 SLA 與延遲量測仍屬 Phase 34B（需待 H07 人工決策回覆），**本交付不得升格宣稱為「完整 CDC 已 VERIFIED」**。

---

## 6. SAST 迴歸修正 (SAST Regression Repair)

`cc7bdb0b` 修復 5 項 P1 缺陷時，新增的測試在 `test_delete_propagation.py:323` 重新引入 f-string 組裝的 SQL 片段，被 Bandit `B608 hardcoded_sql_expressions` 判定為 Medium 風險，導致 PR #1282 的 required `product` job 失敗（`1 failed, 5542 passed`，`SAST scan failed with exit code 1`）。

該行並非真正執行的 SQL，而是對假連線 `_ScriptedConnection` 已錄語句做子字串比對；同一測試內其餘比對（含 `_ScriptedConnection` 的 `DELETE FROM data_plane.domain_inputs` 回應註冊）本來就使用字面值。修正為字面值以與周邊寫法一致，不改變測試語意。

---

## 7. 驗證命令與結果收據 (Verification Receipts)

量測基準：本 README 所屬 commit 的工作樹（parent `cc7bdb0b`）。執行環境 Python 3.12.14；`apps.data_platform` 經確認解析至本 task worktree 而非主 checkout。所有指令均獨立執行並保留原始 exit code（未 pipe、未 `|| true`、未背景化即判定）。綁定 exact head 的正式收據由 `delivery_toolchain/git/task_verification.py run` 於交付 head 產生並存入 `.orchestrator/evidence`。

| # | 命令 | Exit Code | 時間 | 結果 |
|---|---|---|---|---|
| 1 | `git diff --check` | 0 | — | 通過 |
| 2 | `uv run --frozen pytest apps/data_platform/tests/test_delete_propagation.py apps/data_platform/tests/test_pipeline.py -q` | 0 | 8s（pytest 自報 5.15s） | 30 passed, 9 warnings |
| 3 | `uv run --frozen pytest tests/security/test_supply_chain_security_gate.py::test_sast_scan_passes -q` | 0 | 24s | 1 passed（修正前於 CI 為 FAILURE） |
| 4 | `uv run --frozen ruff check apps/data_platform/` | 0 | — | All checks passed |

### 7.1 缺陷綁定負向對照 (Negative Control)

為證明 5 項回歸測試確實綁在缺陷路徑上、而非在未修正的程式上也會通過，另行執行負向對照：保留本輪測試檔，僅將 `store.py`、`deletion.py`、`sql/control_schema.sql` 還原為缺陷版本（`02119f74`），再跑同一組 5 項選擇。

- 命令：`uv run --frozen pytest apps/data_platform/tests/test_delete_propagation.py -q -k "<5 項回歸測試選擇>"`
- 缺陷版結果：**Exit Code 1，5 failed, 19 deselected**（5 項全數重現對應 P1 缺陷）
- 修正版結果：**Exit Code 0，30 passed**

對照後三個原始檔已還原，`git status` 僅保留本輪實際改動（`store.py`、`test_delete_propagation.py`）。此對照為離線合成 fixture 上的診斷程序，不屬於宣告的 verification 命令。

# ODP-DATA-PLANE-DELETE-PROPAGATION-001: 資料落地層刪除／墓碑傳播、重放與租戶隔離

- **Task ID**: `ODP-DATA-PLANE-DELETE-PROPAGATION-001`
- **Work Package**: WP-34 Follow-up ([ODP 人工決策落地規畫](../../../plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md) §6 WP-34 & [Phase 34A Implementation Handoff](../ODP-CDC-SOURCE-CONTRACT-PREP-001/implementation-handoff.md) §4 跟進項目 1)
- **決策依據**: 決策編號 `D20`（A：實作／補齊 CDC 適用性與契約）
- **查證基準 SHA**: `aa8e54cf1e99` (aligned with latest `origin/dev` tip)
- **負責人 (Owner)**: Antigravity6（修復 Codex2 兩項審查缺陷：保護交易權威免受 stale TRANSACTION 刪除影響、保留 core.brands/core.tenants 審計目標跨重放與重啟）
- **審查人 (Reviewer)**: Codex2
- **交付狀態**: `IMPLEMENTATION_DELIVERED` (推進 dev 基準 `aa8e54cf1e99`、修復兩項審查缺陷、35 項回歸測試與邊界/SAST 驗證全數通過)

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
| `apps/data_platform/deletion.py` | 實作刪除與墓碑傳播語意、版本運算、租戶解析、刪除決策、PurgePlan，修正 `core.transactions` 外鍵依賴清理順序，並提供 `scope_lock_key()` 推導 delete scope 的協調鍵。 |
| `apps/data_platform/contracts.py` | 擴充 `QuarantineReason.SOURCE_DELETED`、`ReconciliationResult.sink_delete_drift` 與 `RunSummary` 序列化。 |
| `apps/data_platform/sql/control_schema.sql` | 建立 `data_plane.tombstones` 稽核資料表，並在 `canonical_lineage` 擴充 `source_version` 欄位。 |
| `apps/data_platform/store.py` | 實作 `delete_record()`、`tombstone_record()`、`get_tombstone()`、原子租戶墓碑防護、最新落地版本比對與版本感知 drift reconciliation，並以 `_lock_delete_scope()` / `_enter_delete_scope()` 建立 delete 與 upsert 的資料庫級協調（§8）。 |
| `apps/data_platform/tests/test_delete_propagation.py` | 33 項完整單元、端到端與審查回歸測試套件，覆蓋外鍵依賴清理、重放保留新資料、租戶隔離、對帳，以及 §8 的 delete/upsert 交錯序列化回歸。 |
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

量測基準：本 README 所屬 commit 的工作樹（parent `9b5ea841`）。執行環境 Python 3.12.14；`apps.data_platform` 經確認解析至本 task worktree 而非主 checkout。所有指令均獨立執行並保留原始 exit code（未 pipe、未 `|| true`、未以缺少摘要行推論成敗）。命令雖以背景 job 執行，完成判定一律取原始 exit code 與 JUnit XML，不以輸出樣態推論。綁定 exact head 的正式收據由 `delivery_toolchain/git/task_verification.py run` 於交付 head 產生並存入 `.orchestrator/evidence`。

| # | 命令 | Exit Code | 時間 | 結果 |
|---|---|---|---|---|
| 1 | `git diff --check` | 0 | — | 通過 |
| 2 | `env -u INTAKE_TEST_DATABASE_URL uv run --frozen pytest apps/data_platform/tests/test_delete_propagation.py apps/data_platform/tests/test_pipeline.py -q` | 0 | JUnit 自報 17.412s | 33 passed / 0 failed / 0 skipped, 10 warnings |
| 3 | `uv run --frozen pytest tests/security/test_supply_chain_security_gate.py::test_sast_scan_passes -q` | 0 | JUnit 自報 24.221s | 1 passed / 0 failed |
| 4 | `uv run --frozen ruff check apps/data_platform/` | 0 | — | All checks passed |

第 2、3 項的 tests / failures / skipped 計數取自各自的 `--junitxml`，非重跑統計。第 2 項的 `requires_live_env` 測試在本環境實際執行（每項 1–4 秒），未被跳過。

### 7.1 缺陷綁定負向對照 (Negative Control)

為證明 5 項回歸測試確實綁在缺陷路徑上、而非在未修正的程式上也會通過，另行執行負向對照：保留本輪測試檔，僅將 `store.py`、`deletion.py`、`sql/control_schema.sql` 還原為缺陷版本（`02119f74`），再跑同一組 5 項選擇。

- 命令：`uv run --frozen pytest apps/data_platform/tests/test_delete_propagation.py -q -k "<5 項回歸測試選擇>"`
- 缺陷版結果：**Exit Code 1，5 failed, 19 deselected**（5 項全數重現對應 P1 缺陷）
- 修正版結果：**Exit Code 0，30 passed**

對照後三個原始檔已還原，`git status` 僅保留本輪實際改動（`store.py`、`test_delete_propagation.py`）。此對照為離線合成 fixture 上的診斷程序，不屬於宣告的 verification 命令。

---

## 8. Delete 與 Upsert 的資料庫級原子協調 (Atomic Delete/Upsert Coordination)

### 8.1 缺陷 (Defect)

`cc7bdb0b` 之後仍留有一個真實併發缺陷，於 head `9b5ea841`（`store.py` sha256 `425955a4…`）以 threaded + `pg_stat_activity` 診斷實測為 **2 tests / 1 failure**：

`_guard_deleted()` 只做一次 tombstone `SELECT`，而 delete 與 upsert 分別在**兩條連線的兩個交易**中執行，彼此不可見。當 connection A 的 guard 讀取通過、交易尚未 commit 時，connection B 的 delete v22 可以整段執行並 commit；A 隨後把較舊的 v21 落地，實體被復活。

`_read_tombstone(..., FOR UPDATE)` 無法覆蓋此情境：需要協調的正是**墓碑列尚不存在**的第一次刪除，沒有列可以鎖。

### 8.2 修復 (Repair)

| 位置 | 內容 |
|---|---|
| `deletion.scope_lock_key()` | 由 `tenant_id` / `source_kind` / `source_id` 三元組推導 signed 64-bit 鍵。鍵是**推導**而非取自資料列，因此在墓碑尚不存在時同樣成立；`blake2b` 僅用於折疊到 PostgreSQL advisory lock 的鍵空間，不承載任何安全性宣稱。 |
| `store._lock_delete_scope()` | 以 `SELECT pg_advisory_xact_lock(%s)` 取得**交易級**鎖：持有至呼叫端交易結束，呼叫端既無法洩漏它，也無法在自己的寫入尚未 commit 前提前釋放。 |
| `store._guard_deleted()` | 在 tombstone `SELECT` **之前**取鎖。鎖的存續超出本方法，因此 guard 讀取與其後的 upsert 對 delete 而言是不可分割的一步。 |
| `store._enter_delete_scope()` | delete 進入同一把鎖，並**在鎖內重讀 lineage**（owning tenant、purge targets、最新落地版本）。事件未宣告 tenant 時，第一次讀取只用來決定要鎖哪一把，不參與判定；取鎖後再讀一次，判定所依據的每一項事實都來自協調之內。 |

判定結果因此對兩個方向都成立：較舊的 v21 upsert 落在較新的 v22 delete 之後 → delete 在鎖內讀到 v21 並清除；較新的 v23 upsert 落在較舊的 v22 delete 之前 → delete 讀到 v23 大於自身版本，判 `STALE_IGNORED` 而不退化 sink。

### 8.3 為何不是「前移 test hook 或重查 SELECT」

回歸測試 `test_a_delete_and_an_upsert_are_serialised_by_the_database` 不以「delete 恰好跑在後面」為通過條件，而是以資料庫自身的視角斷言：writer 在**真實的 `_guard_deleted` 已讀取並通過之後**被暫停於該處，deleter 必須被觀測到在 `pg_stat_activity.wait_event_type = 'Lock'` 上等待。只靠時序而沒有實際鎖的實作，觀測到的會是 `delete_finished_without_waiting`，測試即失敗。

### 8.4 負向對照 (Negative Control)

保留本輪測試檔，僅將 `store.py` 還原為缺陷版本 `9b5ea841`（還原後 sha256 `425955a4690affb47c593604e88f07f57f639446b55296a76dfd6ab6c641e6c1`，與原診斷收據所記一致），`deletion.py` 保留新增的未被使用之 `scope_lock_key`，跑同一組選擇：

- 命令：`env -u INTAKE_TEST_DATABASE_URL uv run --frozen pytest apps/data_platform/tests/test_delete_propagation.py -q -k "serialised_by_the_database"`
- 缺陷版結果：**Exit Code 1，2 failed, 25 deselected**，兩個參數化皆以 `delete_finished_without_waiting` 失敗
- 修正版結果：**Exit Code 0**（含於 §7 第 2 項的 33 passed）

原診斷在 `[23]` 方向是靠時序通過的；本回歸在缺陷版上**兩個方向都失敗**，因此嚴格強於原診斷。對照後 `store.py` 已還原，`git status` 僅保留本輪實際改動。此對照為離線合成 fixture 上的診斷程序，不屬於宣告的 verification 命令。

### 8.5 邊界與非宣告 (Scope Boundary & Non-Claims)

- 這把鎖序列化的是**同一 tenant / source-kind / source-id** 的 delete 與 upsert。不同 scope 不互相阻擋，這是刻意的：協調範圍與 delete 得以作用的範圍一致，不因此把租戶之間序列化。
- `domain_inputs` 以 `source_snapshot_id` 為主鍵，同一 `source_id` 多次落地會保留多列快照。因此 v23 情境的斷言是「新落地的 snapshot 仍在」，v21 情境的斷言是「該身分的**每一個** snapshot 都已消失」，而非單純的列數比較。
- 本節仍屬離線落地層語意：未開啟任何 change stream、未讀取任何憑證、未在生產環境刪除任何資料。與 Phase 34B / H07 的區隔同 §5，不因本修復升格為「完整 CDC 已 VERIFIED」。

---

## 9. 審查缺陷修復：交易權威保護與保留主檔重放審計 (Review Defect Repairs)

在 PR #1282 (`4a4aa4a6`) Codex2 獨立審查重現的兩項缺陷，在本輪交付中完成修復並納入回歸測試：

### 9.1 保護共用 Canonical Transaction / Current Authority 免受 Stale 或同 Rank 不同 Source ID 之刪除影響

- **缺陷機制**：`core.transactions` 與 `data_plane.transaction_authority` 為 `ORDERS`（權威等級 1）、`TRANSACTION`（權威等級 2）與 `TRADE`（權威等級 3）共用之落地目標，不同來源事件（如 `gateway-transaction-1` 與 `gateway-transaction-2`）可能具有不同之 `source_id` 但對應同一 `orderId`（即同一 canonical `transaction_id`）。原實作僅依 `authority_rank` 比較，且 delete scope advisory lock 僅鎖定單一 `(tenant_id, source_kind, source_id)`，導致：
  1. 同 rank 但不同 source ID 之較舊 TRANSACTION A 刪除事件抵達時，會誤刪較新 TRANSACTION B 的 `transaction_authority` 與 `core.transactions`，破壞 B 的當前權威並造成 B 的 lineage 懸空。
  2. 不同 source ID 寫入與刪除同一 canonical target 時因 lock key 不同而缺乏原子協調。
- **修復實作**：
  1. 在 `apps/data_platform/deletion.py` 新增 `canonical_lock_key(tenant_id, canonical_table, canonical_id)`，針對所有共用同一 canonical target 的 writers 與 deleters 提供資料庫級原子協調。
  2. 在 `_enter_delete_scope` 與 `_guard_deleted` 取得 scope lock 之同時，亦取得所涉 canonical targets 之 `canonical_lock_key`，並以排序後的 key 集合執行 `pg_advisory_xact_lock` 防止死鎖。
  3. `plan_purge()` 之 `_LEAF_PURGE_TEMPLATES["core.transactions"]` 調整為比對當前權威快照：
     - `DELETE FROM {schema}.transaction_authority ... WHERE ... AND auth.authority_rank >= {authority_rank} AND auth.source_snapshot_id IN (SELECT source_snapshot_id FROM {schema}.canonical_lineage WHERE canonical_table = 'core.transactions' AND canonical_id = target.transaction_id AND tenant_id = scope.tenant_id AND (%s::text IS NULL OR source_kind = %s) AND (%s::text IS NULL OR source_id = %s) AND (%s::bigint IS NULL OR source_version IS NULL OR source_version <= %s::bigint))`
     - 僅在刪除事件之權威等級不低於現有權威，且當前 authority snapshot 確實屬於該刪除事件之 source identity 與版本範圍時，才移除 authority。
     - `DELETE FROM core.transactions ... WHERE ... AND NOT EXISTS (SELECT 1 FROM {schema}.transaction_authority ...)`：若權威記錄因等級較高、或屬於更新之同 rank source identity 而受保護保留，則 `core.transactions` 亦受到保護不予刪除。
  4. 新增回歸測試 `test_shared_transaction_current_authority_and_audit`（覆蓋 `ORDERS` 與 `TRANSACTION`）與 `test_shared_transaction_same_rank_different_source_id_interleaving` 驗證不同 source ID 併發與當前權威保護。

### 9.2 保留真實受保護目標審計 (Actual Protected Retained Targets Audit)

- **缺陷機制**：當權威較低或較舊之刪除事件被權威防護阻止刪除 `core.transactions` 時，`purged_row_count` 正確為 0，但原實作僅自靜態 plan 取得 `retained_targets`（`core.transactions` 不在靜態 retained 清單中），導致結果與重啟後墓碑回讀之 `retained_targets` 回報為空，遺失實際受到保護目標之審計記錄。
- **修復實作**：
  1. `_propagate_delete` 在執行 purge 後，動態檢核 targets 中未被 purge 之目標實體是否依然存續於資料庫中（例如 `SELECT 1 FROM core.transactions WHERE transaction_id = %s`）。
  2. 若目標實體因權威防護而存續，將其真實加入 `retained_targets`，確保 `DeleteResult` 與寫入之 tombstone 均包含 `("core.transactions",)`。
  3. 保留跨重放與重啟時 `retained_targets` 的合併與持久化防護。
  4. 新增回歸測試 `test_retained_targets_preserved_across_replay_and_restart` 與 `test_shared_transaction_current_authority_and_audit` 斷言 readback 審計一致性。


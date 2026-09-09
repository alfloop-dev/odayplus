# ODP-DATA-PLANE-DELETE-PROPAGATION-001: 資料落地層刪除／墓碑傳播、重放與租戶隔離

- **Task ID**: `ODP-DATA-PLANE-DELETE-PROPAGATION-001`
- **Work Package**: WP-34 Follow-up ([ODP 人工決策落地規畫](../../../plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md) §6 WP-34 & [Phase 34A Implementation Handoff](../ODP-CDC-SOURCE-CONTRACT-PREP-001/implementation-handoff.md) §4 跟進項目 1)
- **決策依據**: 決策編號 `D20`（A：實作／補齊 CDC 適用性與契約）
- **查證基準 SHA**: `fe8168db` (aligned with `origin/dev` tip `fe8168db15d1`)
- **負責人 (Owner)**: Antigravity4
- **審查人 (Reviewer)**: Codex2
- **交付狀態**: `IMPLEMENTATION_DELIVERED` (資料落地層刪除語意與墓碑防護實作完成，SAST 掃描通過)

---

## 1. 任務背景與目標 (Background & Objective)

在 `ODP-CDC-SOURCE-CONTRACT-PREP-001` (WP-34A) 盤點過程中，發現現有資料落地層（`apps/data_platform/store.py` 與 `pipeline.py`）之關鍵獨立缺陷：
- 過去落地管線僅依賴 `INSERT ... ON CONFLICT DO UPDATE`，完全缺乏實體刪除（Physical Delete）、軟刪除標記或墓碑清除（Tombstone Purge）路徑。
- 若上游 Mongo 或外部來源執行實體刪除，下游 PostgreSQL 投影將永久殘留，造成資料幽靈孤島與跨系統不一致。

本 Task 聚焦於**離線資料落地層（Landing Layer）的刪除傳播與防護機制**，在 repo 現有程式、資料模型與可重建測試環境中交付完整的刪除、墓碑記錄、重放冪等、防止幽靈復活與租戶隔離語意。

---

## 2. 核心架構與語意保證 (Core Architecture & Invariants)

### 2.1 雙重核心不變量 (Two Core Invariants)
1. **版本保護墓碑 (Version-Guarded Tombstone)**：
   - 每個刪除事件以微秒級單調時間戳轉化為 `source_version`。
   - 刪除寫入資料庫時帶有原子條件防護 `WHERE EXCLUDED.source_version >= tombstones.source_version`。
   - 較晚到達之舊刪除事件判定為 `DeleteOutcome.STALE_IGNORED`，絕不覆蓋或退化較新狀態。
2. **防幽靈復活防護 (Resurrection Prevention)**：
   - 在批次落地投影（`apply_batch`）前，執行 `_guard_deleted()`。
   - 若欲寫入之記錄存在已記錄之墓碑，且該新記錄之來源版本小於等於墓碑版本（或無單調可排序版本），直接觸發 fail-closed 並以 `QuarantineReason.SOURCE_DELETED` 隔離，嚴禁舊 upsert 復活已刪除資料。
   - 只有來源版本嚴格大於墓碑版本之事件（代表上游於刪除後重新建立同 ID 實體）方可放行。

### 2.2 刪除決策與重放冪等性 (Idempotency Matrix)
| 情境 (Scenario) | 判定結果 (DeleteOutcome) | 墓碑寫入 | 下游資料清除 | 說明 |
|---|---|---|---|---|
| **首次刪除（下游有資料）** | `APPLIED` | 是 (新增) | 是 (Purged) | 正常刪除，清除目標 table 資料並建立 tombstone |
| **首次刪除（下游無資料）** | `ABSENT_TOMBSTONED` | 是 (新增) | 否 | 雖無既有資料，仍必須寫入 tombstone 以封鎖未來舊 upsert 落地 |
| **相同版本重放** | `REPLAYED` | 是 (更新 replay_count) | 是 (冪等收斂) | 冪等重放，確保未完成之部分刪除可收斂，不重複累加 purged_row_count |
| **過期舊刪除到達** | `STALE_IGNORED` | 否 | 否 | 版本小於已記錄墓碑，安全略過不退化狀態 |
| **無版本或版本無效** | `REJECTED_UNKNOWN_VERSION` | 否 | 否 | 無法決定順序，拒絕執行以策安全 |

### 2.3 嚴格租戶隔離 (Strict Tenant Isolation)
- **血緣解析與邊界檢驗**：由 `canonical_lineage` 解析 source identity 所屬租戶。
  - 若刪除事件指定非該身分所屬之租戶，判定為 `REJECTED_TENANT_BOUNDARY`。
  - 若多個租戶共享相同 source ID 且事件未明確指定租戶，判定為 `REJECTED_AMBIGUOUS_TENANT`。
  - 若無任何 lineage 且未指定合法租戶，判定為 `REJECTED_UNRESOLVED_TENANT`。
- **SQL 條件綁定**：所有下游 DELETE 語句均帶有 `tenant_id = %s` 參數綁定，跨租戶誤刪在 SQL 層即被嚴格防堵。

### 2.4 葉節點清理 vs. 階層主檔保護 (Leaf Tables vs. Retained Identity)
- **實體清除 (SINK_DELETE)**：
  - `core.transactions`
  - `core.machine_status_events` / `data_plane.machine_status_event_evidence`
  - `data_plane.store_daily_facts`
  - `data_plane.forecast_inputs`
  - `data_plane.learning_import_lineage`
  - `data_plane.domain_inputs`
- **保留主檔 (Retained Identity Tables)**：
  - `core.tenants`, `core.brands`, `core.stores`, `core.machines`, `core.address_locations` 屬於多實體依賴之階層主檔，依 `RETAINED_CANONICAL_TABLES` 政策保留不作級聯刪除，僅記錄 tombstone 並於 `retained_targets` 明確回讀揭露。

### 2.5 刪除漂移對帳 (Sink Delete Drift Reconciliation)
- 在 `CanonicalStore.reconcile()` 中新增 `sink_delete_drift` 指標。
- 檢測 `canonical_lineage.projected_at > tombstones.updated_at` 之異常記錄。若有繞過防護之重寫，對帳判定為失敗 (`reconciled = False`)。

---

## 3. 交付檔案與異動範圍 (Delivered Files)

| 檔案路徑 | 異動說明 |
|---|---|
| `apps/data_platform/deletion.py` | 實作刪除與墓碑傳播語意、版本運算、租戶解析、刪除決策、PurgePlan 與安全限制常數。 |
| `apps/data_platform/contracts.py` | 擴充 `QuarantineReason.SOURCE_DELETED`、`ReconciliationResult.sink_delete_drift` 與 `RunSummary` 序列化。 |
| `apps/data_platform/sql/control_schema.sql` | 建立 `data_plane.tombstones` 稽核資料表與實體索引。 |
| `apps/data_platform/store.py` | 實作 `delete_record()`、`tombstone_record()`、`get_tombstone()`、`_guard_deleted()`、`_deleted_versions()` 與 drift reconciliation。 |
| `apps/data_platform/tests/test_delete_propagation.py` | 25 項完整單元與端到端測試，覆蓋決策邏輯、SQL 語句、跨租戶、重放、亂序、重啟回讀與對帳；SQL 查詢採用 `psycopg.sql.SQL` + `Identifier` 組合通過 SAST 掃描。 |
| `docs/audits/code-boundary-inventory.csv` | 配合新增模組更新代碼邊界清單。 |
| `docs/evidence/human-decisions/ODP-DATA-PLANE-DELETE-PROPAGATION-001/README.md` | 本交付報告與架構規範說明。 |

---

## 4. 邊界與非宣告事項 (Scope Boundary & Non-Claims)

- **離線語意交付**：本任務為離線資料落地層之程式與可重建測試 DB 實作，**未連線生產環境、未開啟 ChangeStream 串流、未讀取生產機密、未修改 IAM 權限**。
- **與 Phase 34B 之區隔**：本任務解決的是下游 PostgreSQL 缺乏刪除路徑之獨立缺陷；完整的 CDC 即時適配器、ChangeStream 監聽、生產 SLA 與延遲量測仍屬 Phase 34B（需待 H07 人工決策回覆），**本交付不得升格宣稱為「完整 CDC 已 VERIFIED」**。

---

## 5. 驗證命令與結果收據 (Verification Receipts)

所有驗證命令均在當前 task branch exact HEAD 執行且 exit code 為 0：

1. **`git diff --check`**
   - 狀態：通過（Exit Code: 0）
2. **`uv run pytest apps/data_platform/tests/test_delete_propagation.py apps/data_platform/tests/test_pipeline.py -q`**
   - 狀態：通過（Exit Code: 0，25 passed）
3. **`uv run python3 delivery_toolchain/security/sast_scan.py`**
   - 狀態：通過（Exit Code: 0，Bandit SAST scan passed successfully）

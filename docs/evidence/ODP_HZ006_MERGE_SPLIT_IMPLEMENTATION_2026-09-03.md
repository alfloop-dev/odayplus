# ODP-HZ006-MERGE-SPLIT-IMPLEMENTATION-001 — 熱區合併／拆分實作與審查回應

- **任務識別碼**：`ODP-HZ006-MERGE-SPLIT-IMPLEMENTATION-001`
- **文件路徑**：`docs/evidence/ODP_HZ006_MERGE_SPLIT_IMPLEMENTATION_2026-09-03.md`
- **日期**：2026-09-03
- **任務負責人**：Antigravity2
- **審查人**：Claude
- **前置任務**：`ODP-HZ006-MERGE-SPLIT-READINESS-001`（`done`，見 `docs/evidence/ODP_HZ006_MERGE_SPLIT_READINESS_2026-09-03.md`）
- **關聯依據**：
  - `docs/design/ODP-SA-06-AMD-001.md` §3.2（`ODP-FR-HZ-006`）、§3.5（`ODP-FR-HZ-004`）
  - `docs/design/ODP-SD-AMD-001.md` §3.3（政策解析 fail-closed）、§5.1（需求吸收閉環）、§5.2（熱區組成）

---

## 1. 本輪範圍

本文件記錄第二輪審查退回（2026-09-03T20:42:54Z，Codex2）三項發現的處置。三項均為**結構性缺陷**——不是實作細節，而是「表面上做到、實際上做不到」的落差：

| # | 審查發現 | 本質 | 處置 |
|---|---|---|---|
| 1 | 拆分核准非原子：一次核准只建立單側子熱區 | 已核准的拓樸是**殘缺**且**不可修復**的 | 改為單一 proposal 承載完整切分 |
| 2 | 生產環境無 HZ-004 實績寫入者 | merge/split 讀的關聯**沒有任何生產路徑會寫** | 新增 append-only、source-bound 寫入者與生產入口 |
| 3 | 顯式 `policy_version_id` 只檢查存在與租戶 | 治理版本可被**指定繞過** | 統一解析：kind／tenant／in-force 三者皆驗 |

---

## 2. 發現一：拆分核准不具原子性

### 2.1 缺陷

`merge_split_engine.py` 對一個待拆分熱區發出**兩個獨立的 `SPLIT_CHILD` proposal**（每側一個）。兩份 `approve_proposal` 實作（in-memory 與 durable）在核准時都會：

1. 軟撤銷 proposal 成員 cell 既有的 active composition；
2. 若為 `SPLIT_CHILD` 且父熱區仍 active，軟撤銷父熱區；
3. 僅建立**該 proposal 自己**成員的 composition 紀錄。

審查人的探測結果：核准兩個子 proposal 中的一個之後，只有 `cell-kaohsiung-00` 仍 active，父熱區已 inactive，另一個 proposal 停留在 `PROPOSED`。

**為何這是結構性缺陷而非缺漏**：父熱區一旦被 append-only 軟撤銷即無法復原（`reverted_at` 只能 NULL → 時間戳，觸發器拒絕其他 UPDATE）。因此未被重新歸屬的 cell 落在**任何 active 熱區之外**，而且**沒有後續核准能修復**——它要被拆分的父熱區已經不存在了。

### 2.2 處置

- `MergeSplitProposalRecord` 新增 `child_partitions: tuple[tuple[str, ...], ...]`，承載切分的**每一側**；`zone_id` 指向被切分的父熱區，`member_cell_ids` 為全體成員。
- 引擎對每個待拆分熱區只發出**一個** proposal。
- `approval_zone_assignments()`（`modules/heatzone/domain/composition.py`）是兩份 repository 實作共用的映射，合併回傳一個熱區、拆分回傳每個 partition 一個熱區（`zone_id` 由 `generate_merged_zone_id(partition)` 決定），兩者無法各自漂移出不同拓樸。
- `validate_proposal_record()` 在建構時即拒絕：partition 少於 2、有空側、cell 重複出現於多側、partition 未精確覆蓋成員集合。
- 原子性：durable 路徑在既有 `_transaction()` 內完成；in-memory 路徑以 `_records` 快照在任一子熱區失敗時還原。
- SQL 同步：`expansion.heatzone_proposals` 新增 `child_partitions JSONB NOT NULL DEFAULT '[]'`，並以 `chk_proposal_child_partitions` 約束「拆分至少兩個子項、非拆分零個」。
- Operator 面板顯示切分後的每個子熱區與其成員，並明示「核准一次即同時建立全部子熱區，父熱區同時退場」。

### 2.3 過程中發現的第二個缺陷（durable 路徑實際上沒有 rollback）

為發現一撰寫的注入式失敗測試在 **durable 參數化案例紅燈**：第一個子熱區已寫入、父熱區已撤銷。

根因：`SqliteEngine.execute()` 每一敘述都 `commit()`，而 `_transaction()` 在缺少 `transaction()` 方法時退化為**只取鎖**。因此 durable 核准得到的是序列化，不是原子性——在程式碼上兩者看起來相同，在資料上不同。`PostgresEngine` 本來就有 `transaction()`，只有 SQLite 路徑有此落差。

處置：`SqliteEngine` 新增真正的 `transaction()`——以深度計數暫緩逐敘述 commit，最外層區塊成功則 commit、例外則 rollback，可安全巢狀。此為既有 `_transaction()` 所有使用者共同受益的修正（回歸驗證見 §5）。

---

## 3. 發現二：生產環境無 HZ-004 實績寫入者

### 3.1 缺陷

`modules/heatzone/v3/adapter.py` 與 `absorption_inputs.py` **計算** `AbsorptionResult` 後直接回傳，不留存；已接線的 `DurableMergeSplitEvidenceRepository` 依建構即為唯讀。因此 `expansion.heatzone_absorption_outcomes` 是一個 merge/split 會讀、但**沒有任何生產路徑會寫**的關聯：生產環境的 `evaluate` 會永遠以空實績 abstain，而唯一存在過的歷史是測試直接塞進 in-memory fixture 的那些。

### 3.2 處置

**寫入者**（`modules/heatzone/application/absorption_outcome_recorder.py`）：

- `build_absorption_outcome()` 的每一個量測值都取自 `AbsorptionResult`，參數只說明「這是哪個 cell、哪個期間、哪一側」。
- Source-bound：`basis_source_ids` 缺席即拒絕。這些 id 由 `assemble_zone_absorption` 從每一筆來源列的 `raw_contract_fingerprint` 取得，無法由呼叫端填寫。
- Append-only：同一期間重複記錄，量測一致則為 no-op、不一致則**拒絕**而非覆寫（上週的合併是依當時的數字決定的，靜默替換會使該決策不可解釋）。PostgreSQL 以拒絕所有 UPDATE／DELETE 的觸發器持有同一規則。
- 比對僅涵蓋量測欄位；`basis_at` 明確排除——它是「計算何時執行」而非「量測到什麼」，納入比對會使每次冪等重跑都變成衝突（此為撰寫測試時發現並修正的第二個缺陷）。
- 兩份實作皆拒絕 geo pipeline 未發布的 cell。PostgreSQL 以 `geo.h3_cells` 外鍵拒絕；SQLite 若不檢查會接受一列而後被 evidence reader 的 join 靜默丟棄——看起來已記錄、實際永遠讀不到。

**生產入口**（`POST /api/v1/heatzones/absorption/outcomes`）：

- Request 只帶**輸入**：已發布的 `oday.store-daily-performance.v1` 與 `oday.operational-start-observation.v1` 列、cell、期間、需求基準。`absorbed_demand`、`absorption_ratio`、`absorbing_store_count`、`under_realized` 由伺服器端 `assemble_zone_absorption` 計算；`extra="forbid"` 使夾帶量測值的請求被拒絕而非合併。
- 覆蓋不完整（缺營業日、無 fingerprint、`DECLARED` 起始日）一律 fail-closed，不記錄部分期間。
- **職責分離**：此路由持有獨立的 `heatzone_absorption` 權限，授予 `DATA_OWNER`，而**未**授予任何可核准熱區組成的角色（`EXPANSION_USER`、`EXECUTIVE`）。能決定合併的人不能寫它被判定所依據的證據。
- HZ-004 量測有自己的治理 policy kind（`heatzone_absorption`），與 `heatzone_merge` 分離：量測門檻的變更必須能在稽核軌跡中被讀出，而不被誤認為合併決策門檻的變更。SQL migration 同步 seed 該 policy 與租戶新增觸發器（`heatzone_absorption_outcomes.absorption_policy_version_id` 有外鍵指向該 registry，未 seed 則寫入者無法 insert）。

---

## 4. 發現三：顯式 policy 版本未經治理驗證

### 4.1 缺陷

`evaluate` 對顯式 `policy_version_id` 只檢查存在與租戶，未檢查 `policy_kind = heatzone_merge`，亦未檢查 `covers(now)`；`override_zone_composition` 則直接以 `f"heatzone-merge-v1:{tid}"` **拼出**版本字串寫入治理列。後者違反 `shared/governance/decision_policy.py` §175-197 的 fail-closed 規則——無法解析政策的呼叫端不得產生決策，也不得由標籤組出 `policy_version_id`；資料庫外鍵最終會拒絕，但那是在決策**已經做出之後**。

### 4.2 處置

兩條路徑統一走 `resolve_merge_policy()`：

- 未指定版本 → `resolve_policy(policy_kind="heatzone_merge", ...)`，維持 fail-closed。
- 指定版本 → 仍須是 `heatzone_merge` kind、屬於本租戶、且**現行有效**；三者任一不符即 422。指定版本是「選擇一個」，不是「豁免驗證」。
- `override` 不再拼字串，改為解析後取 `policy_version_id`；無法解析時零決策（熱區維持原組成）。

---

## 5. 驗證

於 branch `task/ODP-HZ006-MERGE-SPLIT-IMPLEMENTATION-001` 執行（Python 3.12，`uv run --frozen`）：

| 測試檔 | 涵蓋 |
|---|---|
| `tests/models/test_heatzone_merge_split.py` | 拆分 proposal 驗證（5 個負向參數化案例）、完整拓樸核准、注入失敗後零套用、持久化與拒絕保留切分、子熱區 rollback；核准與 rollback 案例皆以 `durable=[False, True]` 參數化 |
| `tests/integration/test_heatzone_composition_api.py` | HZ-004 記錄生產入口（伺服器端量測、不可自報量測值、無 fingerprint／缺營業日／`DECLARED` 起始日 fail-closed、冪等重跑與衝突拒絕、未發布 cell 拒絕、側標記需要 barrier、決策角色無寫入權限、已記錄實績確實進入 evaluate）；政策負向案例（錯 kind、已失效、他租戶、override 無法解析、override 解析治理版本） |
| `tests/ops/test_heatzone_composition_migration.py` | schema 與約束；新增 alembic 單一 head／無重複 revision 檢查 |
| `tests/contract/test_heatzone_composition_schema.py` | 契約層 |
| `tests/ops/test_migration_backfill.py`、`tests/unit/persistence/test_sql_decision_policy_repository.py` | migration 清單與政策 registry |
| `tests/reliability/test_concurrency_recovery.py`、`tests/integration/test_durable_repository_wiring.py` | `SqliteEngine.transaction()` 變更的回歸面 |

執行指令與結果：

```
uv run --frozen python -m pytest \
  tests/models/test_heatzone_merge_split.py \
  tests/integration/test_heatzone_composition_api.py \
  tests/ops/test_heatzone_composition_migration.py \
  tests/contract/test_heatzone_composition_schema.py \
  tests/ops/test_migration_backfill.py \
  tests/unit/persistence/test_sql_decision_policy_repository.py \
  tests/reliability/test_concurrency_recovery.py \
  tests/integration/test_durable_repository_wiring.py -q
```

114 passed，exit code `0`（直接量測 `$?`，未經 pipe——pipe 會吞掉 pytest 的離開碼）。

`uv run --frozen ruff check` 於本任務變更的檔案全數通過。

**未執行**：前端檢查。`node_modules` 未安裝（root 與 `apps/web` 皆無），本環境不自行安裝依賴。`HeatZoneMergeSplitPanel.test.tsx` 新增的兩個案例（拆分子熱區呈現、合併不呈現子項）尚未在本機執行，需由具備前端依賴的環境或 CI 覆蓋。上一輪審查亦記錄了同一限制。

---

## 6. Base advance 附帶處置

本輪合入 `origin/dev` 時，dev 先後落地 `0013_work_orders_root_cause_disposition` 與 `0014_learninghub_backtest_receipts`。

處置：本任務的 migration 改編為 `0015_heatzone_composition`（`down_revision="0014"`），對應 SQL 為 `000020_heatzone_composition.sql`，並在 `tests/ops/test_heatzone_composition_migration.py` 進行結構性檢查——列舉所有 migration，斷言無重複 revision id、恰好一個 head、根為 `0001`。撞號會被測試擋下，而不是等到部署時才發現。

Stamped baseline fixture（`tests/integration/test_official_real_estate_postgresql.py`）同時保留雙方的表定義。

---

## 7. 審查回應與邊界處置（Round 7）

針對第七輪審查退回之兩項發現處置如下：

### 7.1 Migration 回歸修正
- `tests/ops/test_migration_backfill.py` 補上 `"0015"` alembic revision 索引，並新增 `test_heatzone_composition_ddl_is_reachable_from_alembic_head()` 斷言 `0015_heatzone_composition.py` 包含 `000020_heatzone_composition.sql`，修復 base-advance 漏補 migration plan 清單的回歸。

### 7.2 移除私有屬性探測與 Durable 拆分明確 Fail-Closed 宣告
1. **移除私有屬性回退**：`apps/api/oday_api/routes/heatzone.py` 移除 `hasattr(evidence_repo, "_cells")` 之私有屬性反射，僅透過公開的 `get_cell(tid, cell_id)` 介面查詢註冊空間單元。
2. **Durable 拆分顯式 Fail-Closed**：目前持久層尚未實作受信任的 geo barrier 寫入 pipeline（`geo.h3_cells` 無 barrier 欄位）。當呼叫端嘗試對持久層未註冊 barrier 的空間單元紀錄帶有 `barrier_side` 的實績時，路由明確回傳 `422 HZ004_BARRIER_UNBACKED`；合併／拆分評估引擎對持久層候選拆分熱區，亦依據已宣告的命名規則 `no_side_labelled_hz004_outcomes_for_every_member_cell` 顯式拒絕與棄權，杜絕無地理障礙證據的猜測性拆分。
3. **測試保證**：於 `tests/integration/test_heatzone_composition_api.py`（`test_durable_path_side_based_split_refused_and_barrier_unbacked`）與 `tests/models/test_heatzone_merge_split.py`（`test_durable_evidence_repository_refuses_split_when_cells_lack_barrier_sides`）新增持久層真實路徑之斷言測試。


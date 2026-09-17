# Phase 34B — Scoped CDC 配接器、檢查點與刪除傳播實作證據

- **Task ID**: `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001`
- **Work Package**: WP-34B（承接 WP-34A `ODP-CDC-SOURCE-CONTRACT-PREP-001`）
- **Requirement**: `ODP-FR-INT-001`／決策 D16–D21
- **Owner**: Claude ｜ **Reviewer**: Antigravity
- **裁示依據**: H07 人工決策回覆（2026-09-13，具名簽核人蔡尚志），
  `support/handoffs/remaining-inputs-20260913/H07-human-decision-response.md`
- **前置契約**: [implementation-handoff.md](../ODP-CDC-SOURCE-CONTRACT-PREP-001/implementation-handoff.md)、
  [event-contract-draft.json](../ODP-CDC-SOURCE-CONTRACT-PREP-001/event-contract-draft.json)

---

## 0. 先講清楚這份交付「不是」什麼（Scope Limit）

本 task 交付的是**離線可驗證的工程實作**。以下事項**未被本次交付驗證**，不得以本文件或
測試綠燈作為其已完成的證據：

| 未驗證事項 | 為什麼沒驗 | 要驗需要什麼 |
|---|---|---|
| 上游確為 Replica Set、oplog 保留窗實際長度 | H07 第 2 項來自操作者口頭確認，非 DBA 書面回覆，且 24–48h 是區間而非實測值 | `rs.status()` 或 `db.getReplicationInfo()` 的實際輸出；未轉發問題單見 `support/handoffs/remaining-inputs-20260913/H07-dba-query.md` |
| 端到端延遲 < 10 秒（及 34A 驗收矩陣要求的 P95 < 5s） | 測試中的時間戳來自 fixture 自己的時鐘，量到的是算術不是生產延遲 | 真實遙測計時，於具備憑證的環境執行 |
| `odp_cdc_reader` 帳號存在且已授予 `changeStream` | 本 task 未申請、未讀取任何憑證，亦未修改 IAM | 由具權限者配置後回讀角色定義 |
| 新增的 `cdc_checkpoints` / `cdc_staging_events` DDL 已在真實 PostgreSQL 上執行 | 本次測試為純離線，未啟動 pgserver 叢集 | 於 `install()` 路徑對真實叢集執行一次 |
| 真實 oplog 會依本實作預期的方式拒絕過期 token | 測試中的過期是由 in-test stream 主動拋出的 | 真實叢集上讓 token 超出保留窗後重連 |

換句話說：**主實作已完成並取證，live 驗收尚未進行**——這正是 task acceptance
「主實作與 live 驗收分別取證」所要求的切分。本文件只主張前者。

---

## 1. H07 五項裁示 → 實作對照

| # | 裁示 | 實作位置 | 驗收測試 |
|---|---|---|---|
| 1 | **範圍 = Scoped CDC**：僅 `orders` 與 `device_log`／`machine_status_events` 啟用 Change Stream，SLA < 10 秒；其餘 13 個集合維持 15 分鐘感測／每日快照批次 | `apps/data_platform/cdc.py`：`SCOPED_CDC_POLICIES`、`BATCH_ONLY_SOURCE_KINDS`、`cdc_policy()`。`cdc_staging_events` 的 `CHECK (source_kind IN ('orders','device_log'))` 讓範圍在 schema 層也擋一次 | `test_only_the_two_ruled_collections_are_in_cdc_scope`、`test_scoped_latency_targets_match_the_ruling`、`test_the_cdc_tables_exist_with_their_guard_constraints` |
| 2 | **拓撲 = Replica Set，oplog 24–48h**；resume token 失效時自動轉全量快照重讀對帳 | `CdcCheckpoint`／`resume_token_for()`（fail-closed）／`expire_checkpoint()`／`plan_snapshot_recovery()`／`record_recovery()` | `test_an_expired_token_fails_closed_instead_of_restarting_from_now`、`test_recovery_re_reads_through_the_existing_batch_path`、`test_recovery_covers_at_least_the_oplog_retention_floor`、`test_a_new_baseline_token_requires_a_completed_recovery_run` |
| 3 | 核准 `odp_cdc_reader` 的 `changeStream` 權限，並核准在 adapter 記憶體管線做應用層投影與個資遮蔽 | `redact_change_document()`；`MongoChangeStreamFactory` 以 `full_document="updateLookup"` 明示邊界後立即收斂 | `test_change_stream_payload_is_narrowed_to_the_batch_projection`、`test_redacted_fields_never_reach_the_staging_statement` |
| 4 | **刪除政策 = 軟刪除與稽核墓碑並行**；業務表映射 `status='voided'`／`is_deleted=TRUE`；GDPR 清除寫 SHA-256 雜湊墓碑並覆蓋個資欄位為空；實體清除保留 90 天交由 Retention Purge Job | `plan_change_application()` 同時產出 `SoftDeleteDirective` 與 `DeleteEvent`；`SoftDeleteDirective.statement()`；`gdpr_erasure()`／`GDPR_PURGE_RETENTION` | `test_an_upstream_order_delete_marks_the_row_and_records_a_tombstone`、`test_a_refund_maps_onto_the_governed_refunded_status`、`test_the_soft_delete_statement_is_tenant_scoped_and_version_guarded`、`test_gdpr_erasure_blanks_the_identifiers_and_hashes_what_it_erased` |
| 5 | **傳輸架構 = Dagster 內常駐 ChangeStream sensor 直寫 PostgreSQL 暫存表**，不引入 Kafka／Redpanda／PubSub／RabbitMQ | `apps/data_platform/definitions.py`：`scoped_cdc_orders_sensor`、`scoped_cdc_device_log_sensor`、`_cdc_drain()`；暫存表 `data_plane.cdc_staging_events` | `test_the_resident_sensors_are_registered_and_default_to_stopped`、`test_no_message_broker_dependency_was_introduced`（以 import graph 判定，不是字串比對） |

### 兩條硬約束

**(a) CDC 只能疊在批次路徑上，不能取代它。**
`batch_path_retained()` 對全部 15 個 source kind 回傳 `True`，且
`plan_snapshot_recovery()` 產出的是**既有**批次進入點
`apps.data_platform.pipeline.DataPlaneRunner.run_partition` 要重跑的日分割，而不是新機制。
sensor 的 recovery 分支同樣是對既有批次 job 送 `RunRequest`。
測試：`test_enabling_cdc_never_retires_the_snapshot_batch_path`、
`test_recovery_re_reads_through_the_existing_batch_path`。

**(b) Change Stream 回傳完整文件，邊界比 `find + SOURCE_PROJECTIONS` 寬；讀取端的遮蔽必須在 CDC 路徑有等價實作。**
`redact_change_document()` **直接讀取** `apps.data_platform.source.SOURCE_PROJECTIONS`
而非另抄一份白名單，因此兩條路徑在結構上無法漂移；巢狀最小化
（`device_log.logData` 的 `_minimize_device_log`）則不重寫，而是共用
`envelope_for_document()`，使兩條路徑連 `content_sha256` 與
`source_snapshot_id` 都逐位元組相同。
測試：`test_redaction_reads_the_same_allowlist_the_batch_reader_uses`、
`test_cdc_and_batch_envelopes_are_identical_for_the_same_document`、
`test_device_log_nested_minimization_survives_the_cdc_path`。

---

## 2. 34A 驗收矩陣六大維度對照

| 維度 | 交付狀態 | 證據 |
|---|---|---|
| 1. 完整變更動詞支援（insert/update/replace/delete/void/refund/withdraw） | **已交付（離線）** | `classify_operation()`＋`RETIREMENT_STATUS`。`TRADE_REFUND → refund` 是 34A 盤點實際觀察到的唯一可推導動詞；`void`／`withdraw` 在 `fongniao_prod.orders` 沒有已觀察到的上游 state token，因此只在事件明確宣告時採用，**不從未知 state 臆造**（見 `SOURCE_VERB_TOKENS` 註解）。未知 state 維持 `update`，交由下游投影以 `UNSUPPORTED_STATUS` 隔離，與批次路徑對同一文件的判定一致 |
| 2. 重複與亂序冪等性 | **已交付（離線）** | `decide_change()` 實作契約的 `server_timestamp >= target` 條件式 upsert；`idempotency_key` 逐字實作契約公式（測試獨立重算 sha256 比對）。`test_out_of_order_replay_reaches_the_same_terminal_state` 以順序／亂序兩次 drain 比對終態 |
| 3. 斷線恢復與檢查點 | **已交付（離線）** | 見上表第 2 項。drain 中途過期時「已讀到的仍落地、游標剛好死在那一點」：`test_expiry_mid_drain_keeps_what_was_read_and_hands_over_the_gap` |
| 4. 租戶隔離與個資最小化 | **已交付（離線）** | `check_tenant_binding()`（`None` = 本層不設限，空集合 = 全部拒絕，兩者不可混同）；遮蔽見硬約束 (b)。落地 payload 不含原始個資：`test_redacted_fields_never_reach_the_staging_statement` |
| 5. 真實延遲與最終一致性 | **未交付（需 live）** | 只實作了量測機制（`CdcChangeEnvelope.latency_seconds`、`CdcDrainResult.sla_breaches()`）。**離線測試不構成延遲證據**，見第 0 節 |
| 6. 失敗隔離與死信佇列 | **已交付（離線）** | poison packet 隔離進既有 `data_plane.quarantined_records`（與批次路徑同一張表，一條查詢涵蓋兩路），且不中斷後續事件：`test_a_poison_packet_is_isolated_without_stopping_the_stream`；同一 poison 重送只產生一列：`test_quarantine_rows_are_content_addressed_so_poison_does_not_pile_up` |

---

## 3. 交付物

| 路徑 | 內容 |
|---|---|
| `apps/data_platform/cdc.py`（新增） | 範圍政策、記憶體投影／遮蔽、契約 envelope 與冪等鍵、動詞分類、排序守衛、檢查點生命週期與快照回復規劃、軟刪除＋墓碑規劃、GDPR 雜湊墓碑、`PsycopgCdcStore`、`ScopedCdcProjector`、`ScopedCdcAdapter`、`MongoChangeStreamFactory` |
| `apps/data_platform/sql/control_schema.sql` | 新增 `cdc_checkpoints`、`cdc_staging_events` 兩張表與其守衛 CHECK／索引 |
| `apps/data_platform/definitions.py` | 兩個常駐 CDC sensor 與 drain／replay tick；recovery 分支對既有批次 job 送 `RunRequest` |
| `apps/data_platform/source.py` | 新增 `MongoSource.database` property，讓 CDC 沿用同一個已驗證的連線而非另建 client |
| `tests/integration/test_scoped_cdc_adapter.py`（新增） | 58 項驗收測試 |

### 設計上刻意的取捨

- **`cdc_checkpoints` 與既有 `checkpoints` 分開兩張表**：後者存的是批次 `_id` 高水位，
  前者是不透明的叢集位置且有 `EXPIRED` 這個批次游標沒有的終態。
- **`PsycopgCdcStore` 與 `PsycopgCanonicalStore` 分開**：串流的原子單位是「這一 tick 讀到的
  全部 ＋ 停在哪」，正規投影的原子單位是一批已驗證 envelope。合併會讓其中一方的交易邊界
  被另一方拖壞。
- **持久化邊界在 staging 而非 canonical**：一個 tick 在**同一筆交易**內寫入 staging 並移動
  checkpoint，游標因此永遠不會領先資料；canonical 投影再以相同的 content-addressed
  snapshot id 從 staging 重放，兩步之間當掉是安全的（`applied_at` 仍為 NULL）。
- **CDC tick 會寫入 `ingestion_runs`**：因為 `quarantined_records.run_id` 對它有外鍵，poison
  packet 沒有它就無處可掛。該列的 `reconciled` 與 `partition_complete` 恆為 FALSE，
  且應照字面讀：tick 不做對帳，串流也沒有「完成」可言。

---

## 4. 已知缺口與後續（不在本 task 收尾）

1. **`core.machine_status_events` 沒有記錄生命週期欄位。**
   該表的 `status_type` 描述的是裝置狀態（online/offline/error/…），不是列的狀態；
   H07 第 4 項的 `status='voided'` 或 `is_deleted=TRUE` 在這張表**沒有欄位可以表達**。
   新增欄位屬 `infra/db/migrations/` 的正規 migration，**在本 task 的 owned paths 之外**。
   目前行為是：仍記錄稽核墓碑、**保留該列**，並由 `CdcApplyPlan.lifecycle_gap` 具名回報這個
   缺口——刻意不「順手」升級成實體刪除，因為那是裁示沒有授權的動作。
   測試 `test_device_log_retirement_names_its_gap_instead_of_hard_deleting` 把這個行為釘住。
   建議後續 task：為 `core.machine_status_events` 增設記錄生命週期欄位。
2. **Live 驗收**（第 0 節整張表）需另立具備憑證與生產操作授權的 task。
3. **34A 遺留的獨立缺口**（`ODP-CONTRACT-EVENT-STREAM-RECONCILIATION-001`、
   `ODP-SCHEMA-STORE-OPENING-AUTHORITY-001`）不在本 task 範圍，未處理。

---

## 5. 驗證

本 task 宣告的 verification 命令與其收據由
`delivery_toolchain/git/task_verification.py` 產出並綁定 exact head，
存放於 supervisor 的 `.orchestrator/evidence`；本文件不複述收據內容，以收據為準。

宣告命令：

```
git diff --check
uv run pytest tests/integration/test_scoped_cdc_adapter.py -q
```

> 執行注意（供重跑者）：宣告命令已原樣量測過，exit 0。但**前提是 `.venv` 已存在且是 3.12**
> ——在尚未建立 `.venv` 的乾淨 checkout 上，`uv` 會解析到 CPython 3.14，而 `pgserver==0.1.4`
> 只有 cp312 wheel，環境會直接建不起來。此時先用
> `uv run --frozen --python 3.12 pytest ...` 建出 3.12 的 `.venv`，之後宣告命令即可原樣執行。
> 另：裸 `python3 -m pytest` 在本機沒有 pytest，據收據字面重跑必失敗。

回歸範圍（非宣告命令，另行執行以確認未破壞既有行為，均 exit 0）：

```
uv run --frozen --python 3.12 pytest \
  apps/data_platform/tests/test_sql_contract.py \
  apps/data_platform/tests/test_source_contract.py \
  apps/data_platform/tests/test_definitions_and_backfill.py \
  apps/data_platform/tests/test_pipeline.py \
  apps/data_platform/tests/test_config.py            # 37 passed

uv run --frozen --python 3.12 pytest \
  apps/data_platform/tests/test_delete_propagation.py \
  tests/integration/test_data_platform_deployment_contract.py   # 59 passed

uv run --frozen --python 3.12 ruff check apps/data_platform/ tests/integration/test_scoped_cdc_adapter.py
```

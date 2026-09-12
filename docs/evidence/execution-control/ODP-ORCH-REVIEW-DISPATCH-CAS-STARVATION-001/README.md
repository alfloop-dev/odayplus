# ODP-ORCH-REVIEW-DISPATCH-CAS-STARVATION-001: 解決 Review Dispatch CAS 競爭與 Stale Snapshot 導致的派工飢餓問題

- **Task ID**: `ODP-ORCH-REVIEW-DISPATCH-CAS-STARVATION-001`
- **Owner**: `Antigravity3`
- **Reviewer**: `Codex2`
- **Task Branch**: `task/ODP-ORCH-REVIEW-DISPATCH-CAS-STARVATION-001`
- **Base Branch**: `dev` (`4499a2993e37b62033926b07de8d8d2e8469a6c7` merged)

---

## 1. 缺陷背景與事件分析

### 1.1 事件重現（Incident Evidence）
在 `support/handoffs/parallel-dispatch-20260911/review-dispatch-cas-evidence.json` 的現場日誌中，記錄了多個 review ready 任務（例如 PR #1301，exact-head required CI green 已於 00:24:02 綠燈，卻因 CAS 衝突直到 00:39:38 才被 enqueue）被連續飢餓超過 15 分鐘：
- 控制平面連續記錄 4 次 `stale_status_write_rejected`：
  - `00:25:52Z`: `expected_revision`: `7d6218c8d0454186a31b52796c3aad6e` vs `actual_revision`: `28e8b96dfcf44be6a432b735051baf22`
  - `00:29:53Z`: `expected_revision`: `28e8b96dfcf44be6a432b735051baf22` vs `actual_revision`: `634bb2693ae34a0bb2b3f88b226ecbe7`
  - `00:33:46Z`: `expected_revision`: `88b42133ae794608817892326d2469ab` vs `actual_revision`: `6dd4c939c3674ef69c327d00d2886695`
  - `00:37:51Z`: `expected_revision`: `fb8a74285fe54938bc26148e007053a1` vs `actual_revision`: `d05f937fdb1c4a1aa4d1b12a143585f2`
- 由於前置任務的 advisory diagnostic 寫入（如 `CI checks pending`）遭遇 CAS 遭拒或 canonical sync 推進磁碟 revision，原本準備派工的綠燈 PR 被跳過或 tick 提前退出，造成 Reviewer 閒置且任務延宕。

---

## 2. 根因分析（Root Cause Analysis）

1. **Root Cause 1（Canonical Sync 推進 Revision 導致記憶體狀態脫鉤）**：
   `commit_canonical_task_transition` 執行時，首先透過 `write_status_snapshot_if_current` 寫入 revision A，隨後呼叫 `sync_status_pipeline(config)`。後者透過 CLI `sync_all` 刷新狀態並將 revision B 寫入磁碟。若記憶體內的 `status` 未在 sync 成功後重新自磁碟載入最新 snapshot，同一個 tick 內的下一次 commit 就會持過期的 revision A 進行 CAS 寫入，必然觸發 `stale_status_write_rejected`。

2. **Root Cause 2（Advisory Diagnostic 寫入失敗導致提前退出）**：
   在 `dispatch_ready_tasks` 中，當檢查到 PR CI pending、unresolved 或 head drift 等非致命診斷資訊時，會嘗試更新 `task["next"]` 並執行 `commit_canonical_task_transition(config, status)`。原實作在 commit 回傳 False 時直接 `return changed` 退出整個派工迴圈，使該 tick 之後的所有綠燈待審任務完全失去被評估與派工的機會。

3. **Root Cause 3（Snapshot 重載與候選評估 Iterator / 物件脫鉤）**：
   在 CAS 寫入或 snapshot 刷新後，若僅賦值新變數而使用 `continue` 繼續既有 `enumerate(tasks)` iterator，該 iterator 仍會巡歷過期 list 與 detached 物件。當外部 writer 在 sync 期間修改 reviewer（如改派 Codex）時，過期 iterator 會誤將舊 reviewer（如 Antigravity7）放入派工佇列。此外，若 helper claim 連續派發多筆任務，第一筆 commit 後 snapshot 刷新，第二筆若未重新獲取 live task 物件，會修改 detached 物件導致 lease 未持久化至磁碟。

4. **Root Cause 4（Retry Exhaustion 仍派發過期候選）**：
   當 advisory CAS 寫入連續遭拒並耗盡有界重試（8 次）時，若未清空當輪候選，過期候選仍會進入 dispatch loop，造成 stale event 派發。

5. **Root Cause 5（Helper Commit 刷新狀態後未重新評估剩餘候選且覆寫他人租約）**：
   在同一輪派工多個 slot 時，前一筆 helper claim commit 刷新了 canonical snapshot，但剩餘 slot 仍沿用舊 snapshot 計算出的 candidate 列表與 reason，且 helper claim 條件判斷將「其他 agent 持有有效租約」誤判為「可覆寫」，導致在 external writer 於 sync 期間為任務寫入其他 agent 的有效租約（如 Codex generation 17）時，被 dispatcher 覆寫為新租約（如 Antigravity7 generation 18）並誤派發；同樣地，當任務在 sync 期間轉為 blocked 或產生未完成依賴時，舊 candidate 仍被派發。

6. **Root Cause 6（Helper 自身租約同步後未全面重驗候選資格與獨立性）**：
   在 helper lease commit 成功並完成 canonical sync 刷新狀態後，若僅檢查了 live lease 的 claimant 與過期時間，未重新自 fresh `task_map` 驗證該候選任務的狀態（如 sync 期間轉為 blocked）、相依性（如新增未滿足之 dependency）、non-dispatchable 標記或 reviewer/owner 獨立性（如 reviewer 在 sync 期間被修改為該 helper agent），會導致過期或違規 event 仍被派入佇列。

7. **Root Cause 7（Advisory Sync / Reload 失敗未向上終止整個 Tick）**：
   當 advisory 寫入或 helper 租約寫入失敗且隨後自 canonical storage 重新載入 snapshot 亦失敗（例如發生持久性讀取異常）時，若僅中斷當前 agent 的評估迴圈，外層 agent 迴圈仍會繼續使用未確認新鮮度的 stale in-memory snapshot 評估後續 agent，導致 stale review / dispatch event 被錯誤派發。

8. **Root Cause 8（Helper 預算耗盡中斷整輪 Per-Agent 候選迴圈）**：
   在 `dispatch_ready_tasks` 的 per-agent 候選派工迴圈中，當高優先權（例如 P0）之 helper 候選因 helper claim 預算耗盡（`helper_dispatches >= max_helper` 或 `active_claims_for_agent >= max_claims_per_agent`）無法取得租約時，原實作直接 `break` 中斷了該 agent 的整個候選處理迴圈。這導致排在 helper 候選之後的合法 review（例如 exact-head CI green 的 P1 review）或 owned 候選完全失去派發機會，在 reviewer 仍有可用容量的情況下造成非預期的整輪派工飢餓。

9. **Root Cause 9（全域 Snapshot 物件重載破壞 Release Lease 回呼狀態與 Detached Task）**：
   `commit_canonical_task_transition` 在提交成功後透過 `load_status` 自磁碟載入最新 snapshot，並以 `status.clear(); status.update(latest)` 替換了 `status` 內的巢狀 task dict 物件。在 `release_lease_integration.py` (`process_release_lease_issuance`) 中，呼叫者在執行 `issued` 提交後仍持有舊的 task dict 物件引用；隨後的 `_commit_result` 嘗試對該 detached 物件寫入 `dispatched` 或 `dispatch_unknown` 狀態，導致後續的 CAS 寫入所提交的 snapshot 依舊殘留 `issued` 狀態。造成 activity log 記錄了 terminal event，但磁碟 canonical 狀態卻停留在 `issued` 的脫鉤問題。

10. **Root Cause 10（連續 Release 請求第二筆因 Detached Task 遺失 Issuance History 與 Nonce 稽核）**：
    在 `release_lease_integration.py` 的 release requests 巡歷中，第一筆請求保留成功後 `status` 被重新綁定。第二筆請求若持有既有的 `ISSUANCE_FIELD`，呼叫 `_archive_current_issuance` 時會將既有發行記錄存入 detached task 的 `ISSUANCE_HISTORY_FIELD`。隨後 `_commit_result` 僅鏡像複製了 `ISSUANCE_FIELD`，導致 live task 上的 `ISSUANCE_HISTORY_FIELD` 丟失，連帶破壞了 `_nonce_reuse_errors` 對舊 approval nonce 的重複使用防護判定。

11. **Root Cause 11（自訂 `schema.tasks_path` 在 CAS 提交與 Fallback 檢查中寫死 `'tasks'` 鍵）**：
    在 `supervisor.py:408`、`status_transition.py:235` 與 `dispatch_engine.py:2595,2599,3336,3339` 中，新鮮度檢查寫死了 `"tasks" in fresh` / `"tasks" in latest`。當專案設定使用自訂 collection（例如 `schema.tasks_path = "items"`）時，寫入與 canonical sync 雖然在磁碟上成功執行，但函式判定資料無效回傳 `False` 並保留 stale revision，導致後續 dispatch tick 崩潰或失敗。

12. **Root Cause 12（`advance_approved_prs_to_merge` 診斷提交失敗跳過狀態重載與索引重建）**：
    `advance_approved_prs_to_merge` 在寫入 merge-route 等候診斷（如 `awaiting merge queue`）遭遇 canonical sync 失敗或重載失敗時回傳 `False`。呼叫端 `dispatch_ready_tasks` 原先僅在函式回傳 `True` 時執行 `load_status` 與 `tasks`/`task_map` 重建；在 sync 期間外部 writer 將其他任務改派（例如 `GREEN-VICTIM` reviewer Antigravity7 -> Codex）時，若 sync 失敗，記憶體內的 `status` 與 `tasks` 未獲更新且派工繼續執行，導致過期 reviewer Antigravity7 仍被放入派工佇列。

---

## 3. 修復方案與實作細節

### 3.1 `commit_canonical_task_transition` 支援可配置 `tasks_path`、原地物件更新與 Fail-Closed
- 在 `.orchestrator/status_transition.py` 與 `.orchestrator/supervisor.py` 中：
  - `commit_canonical_task_transition` 於 `sync_status_pipeline(config)` 成功或失敗後，皆嘗試透過 `load_status(config)` / `load_status_fn(config)` 重新載入磁碟上的最新 snapshot 並同步至記憶體 `status` dict。
  - 使用 `schema.get("tasks_path", "tasks")` 讀取配置的任務集合鍵，不再寫死 `"tasks"`。
  - 引入 `sync_status_snapshot_dict` 函式，在重載 `status` 時比對既有 `tasks_path` 列表中的 task ID，對已存在的 task dict 執行原地 `target.clear(); target.update(new_t)` 更新，確保持有 task dict 引用的呼叫者不會與 `status[tasks_path]` 脫鉤。
  - 若 `load_status` 失敗（拋出例外）或回傳無效資料，不再吞沒錯誤，一律回傳 `False`，嚴守 fail-closed 原則。

### 3.2 `_commit_advisory_status_transition` 與 `_commit_or_refresh_status` 全面支援 `tasks_path`
- 在 `.orchestrator/dispatch_engine.py` 中：
  - `_commit_or_refresh_status` 支援可配置 `tasks_path`，並在重新載入新鮮狀態時透過 `sync_status_snapshot_dict(config, status, fresh)` 原地更新，確保快照一致性。
  - 若 commit 失敗且無法確認 snapshot 新鮮度（commit 與 reload 皆失敗），回傳 `False` 並立即終止整個派工 tick（`return changed`），防止後續任何 agent 依據 stale snapshot 做出錯誤派工。

### 3.3 Helper 候選全量重驗與租約隔離保護
- 在 `.orchestrator/dispatch_engine.py` 中重構 `dispatch_ready_tasks`：
  - 改用 `while queued_for_agent < available_agent_slots and dispatches < max_dispatches_per_tick:` 迴圈，每派發一筆任務或每次 snapshot 變更後，皆基於最新 `status` snapshot 重新執行完整候選評估與排序。
  - 在 helper claim 路徑中，嚴格檢查 `existing_claim_live and existing_claimant != normalize_agent_id(target_agent)`，嚴禁覆寫任何其他 agent 的有效租約。
  - Helper lease commit 成功後，全面重新自 fresh `task_map` 驗證候選任務之狀態（必須為 claimable 且非 blocked/review/done）、相依性完整性（`dependencies_satisfied`）、`non_dispatchable` 守衛、reviewer/owner 獨立性（`norm_target not in {live_owner, live_reviewer}`）與 worktree lease block，確認完全合法後方才加入派工佇列。
  - `dispatch_helper_tasks` 的 reload fallback 亦全面支援 `tasks_path` 與 `sync_status_snapshot_dict`。

### 3.4 Release Lease 跨請求 Live Task 重新獲取與 History 鏡像保存
- 在 `.orchestrator/release_lease_integration.py` 中：
  - 在遍歷任務時，每次迭代皆重新自當前 `status` 獲取最新的 live task 物件（`live_task = _task_index(status, task_id, config=config)`），避免沿用前一筆請求提交重載後的 detached 物件。
  - `_commit_result` 在同步 detached 物件至 live task 時，除 `ISSUANCE_FIELD` 外同步完整鏡像保存 `ISSUANCE_HISTORY_FIELD`。
  - `_nonce_reuse_errors`、`_task_index` 與 `_record_blocked` 全面支援可配置之 `schema.tasks_path` 與 `schema.task_id_field`。

### 3.5 審計其他 commit callers（`advance_approved_prs_to_merge` 與 Recovery Loops）
- 審計並重構 `advance_approved_prs_to_merge`、`recover_conflicted_review_prs` 與 `recover_failed_ci_review_prs`：
  - 改用 `while True:` 與 `processed_ids` 遍歷，確保每次 `requeue_task_for_ci_repair` commit 刷新 `status` 後，後續迭代皆自最新 snapshot 取得未處理的 live task 物件，徹底排除 detached object 問題。

### 3.6 Helper 預算隔離與候選遞延推進
- 在 `.orchestrator/dispatch_engine.py` 的候選收集與派工階段：
  - 在候選掃描階段預先計算 `can_acquire_new_helper = (helper_dispatches < max_helper and active_claims_for_agent < max_claims_per_agent)`，在預算為零或耗盡時不再將無效 helper 候選標記為 `REASON_HELPER_CLAIM`。
  - 在候選處理階段，若遇 helper 預算受限或條件不符，改以 `agent_deferred_task_ids.add(task_id)` 記錄並 `continue` 繼續後續候選評估，不再 `break` 終止該 agent 迴圈，亦不再無限重試同一首位候選，確保後續合法 review 與 owned 任務可順暢派發。

### 3.7 `advance_approved_prs_to_merge` 新鮮度明確化與 Fail-Closed 守衛
- 在 `.orchestrator/dispatch_engine.py` 中：
  - `advance_approved_prs_to_merge` 的 advisory 變更透過 `_commit_advisory_status_transition(config, status)` 提交；若提交失敗且自磁碟重載亦失敗（無法確認狀態新鮮度），回傳 `None`。
  - `dispatch_ready_tasks` 呼叫 `advance_approved_prs_to_merge` 後，若回傳 `None`，立即終止當前 tick（`return changed`）；若回傳 `True` 或 `False`，皆確保 `tasks` 與 `task_map` 索引自已同步之最新 `status` 重建，嚴禁沿用未確認新鮮度之記憶體狀態。

---

## 4. 驗證記錄與 Red/Green 探針（Verification Receipts & Red/Green Probes）

### 4.1 審查者探針驗證（Reviewer Probes）

1. **`review_schema_probe.py`（自訂 collection 支援與新鮮度探針）**：
   - **Red 狀態**（HEAD `704e3db4`）：在 `tasks_path = "items"` 且 callback 為 `current` 時，`commit_canonical_task_transition` 寫入並 sync 成功後因寫死 `"tasks"` 判定無效，`committed = False`，探針 exit code `1`（`AssertionError: Valid custom tasks_path snapshot rejected after successful CAS and sync`）。
   - **Green 狀態**（修復後）：`tasks` 與 `items` 兩種 collection 均通過 baseline 與 current 模式，`committed = True` 且 `fresh = True`，探針 exit code `0`。

2. **`release_history_probe.py`（兩筆 Release 請求歷史與 Nonce 重複使用稽核探針）**：
   - **Red 狀態**（HEAD `704e3db4`）：第二筆請求發行後，`prior_receipt_preserved = False`、`second_history = []`，且 `prior_nonce_reuse_errors = []`（舊 nonce 重複使用檢查未捕獲錯誤）。
   - **Green 狀態**（修復後）：`prior_receipt_preserved = True`、`second_history` 包含完整 prior issuance 記錄，且 `prior_nonce_reuse_errors = ['release_lease_request nonce was already used by a different issuance']`，兩筆請求皆能正常派發，歷史與稽核記錄完全保存。

3. **`test_advance_advisory_review_probe.py`（Advance 階段 Advisory 失敗與 Canonical 新鮮度探針）**：
   - **Red 狀態**（HEAD `447ebeab`）：在 `sync_failure` 與 `reload_failure` 模式下，`advance_approved_prs_to_merge` 因 advisory 提交失敗回傳 `False`，caller 跳過重載並沿用記憶體舊物件派發舊 reviewer，exit code `1`（2 failed / 4 passed, `AssertionError: Stale reviewer queued after advance advisory failure`）。
   - **Green 狀態**（修復後）：6 個測試案例全數通過（exit code `0`, 6 passed）。在 sync 失敗或重載失敗時，狀態正確同步或壓抑派工，不再派發 stale reviewer。

### 4.2 新增回歸測試
1. `test_diagnostic_cas_two_pending_two_green_revision_changing_dispatch` (`test_dispatch_policy.py`):
   - 2 個 CI pending 任務在前、2 個 CI green 任務在後。真實 temp-file 與 sync 推進 revision，驗證 2 個 green 任務皆正常產生正確 reviewer event。
2. `test_diagnostic_cas_canonical_sync_advancing_disk_revision_reloads_status_for_subsequent_writes` (`test_dispatch_policy.py`):
   - 驗證同一個 tick 內連續兩筆 commit 能在 sync 推進 revision 後正確重載並成功寫入。
3. `test_diagnostic_cas_external_writer_race_preserves_data_and_dispatches_ready_tasks` (`test_dispatch_policy.py`):
   - 模擬 external writer 在 advisory 寫入時競態推進 revision 並注入新任務與自訂欄位，驗證 CAS rejection 後 dispatcher 成功重載 snapshot、保留外部更新並派發新任務。
4. `test_diagnostic_cas_lifecycle_sync_does_not_enqueue_reassigned_reviewer` (`test_dispatch_policy.py`):
   - 驗證 lifecycle 轉移後 external sync 將候選 reviewer 改派，dispatcher 重啟評估且不對舊 reviewer 派發 stale event。
5. `test_diagnostic_cas_helper_claims_persist_leases_for_all_queued_helpers` (`test_dispatch_policy.py`):
   - 驗證同一 tick 內多筆 helper lease 派發時，所有 queued events 在磁碟上皆具備對應的持久化 lease。
6. `test_diagnostic_cas_retry_exhaustion_does_not_enqueue_stale_reviewer` (`test_dispatch_policy.py`):
   - 驗證 8 次 CAS rejection 耗盡重試時，stale candidate 被乾淨丟棄，不產生 stale reviewer event。
7. `test_diagnostic_cas_helper_candidate_revalidated_after_real_sync` (`test_dispatch_policy.py`, 4 variants: unchanged, competing_lease, blocked, dependency):
   - 驗證 helper commit 刷新 canonical state 後，剩餘候選重新評估，且永不覆寫其他 agent 的有效租約，亦不派發 blocked 或 unsatisfied dependency 任務。
8. `test_diagnostic_cas_advisory_resync_failure_never_queues_superseded_reviewer` (`test_dispatch_policy.py`, 3 variants: ok, sync_failure, reload_failure):
   - 驗證 advisory sync failure 與 reload failure 時壓抑過期派工，不對已被改派的舊 reviewer 派發 stale event。
9. `test_diagnostic_cas_current_helper_revalidated_after_own_sync` (`test_dispatch_policy.py`, 4 variants: unchanged, blocked, dependency, reviewer):
   - 驗證 helper 候選自身租約提交並經真實 sync 推進 revision 後，若被外部變更為 blocked、新增未完成相依性或改派 reviewer，全量重驗機制正確壓抑派工且不產生 stale event。
10. `test_diagnostic_cas_cross_agent_after_advisory_snapshot_refresh` (`test_dispatch_policy.py`, 3 variants: unchanged, mutation_readable, mutation_unreadable):
    - 驗證當 advisory commit 與後續 reload 皆失敗時，新鮮度缺失阻擋機制及時中斷整個 tick，阻止後續 agent 讀取過期狀態派發 stale reviewer。
11. `test_exhausted_helper_budget_does_not_starve_exact_head_green_review` (`test_dispatch_policy.py`, 3 variants: available, zero, consumed):
    - 驗證當 helper 預算為 0 或已耗盡時，排在 helper 候選之後的 exact-head green review 仍能順利取得派發，且 helper 租約受限時不中斷該 agent 的後續合法派工。
12. `test_commit_canonical_task_transition_maintains_task_object_identity` (`test_supervisor.py`):
    - 驗證 `commit_canonical_task_transition` 在 sync 成功並重載最新 snapshot 後，依然維持傳入之 task dict 物件的身份一致性，確保後續原地修改仍可正確提交。
13. `test_commit_canonical_task_transition_supports_custom_tasks_path_collection` (`test_supervisor.py`):
    - 驗證 `commit_canonical_task_transition` 支援自訂 `schema.tasks_path = "items"`，在真實 revision 推進 sync 後正確重載並保持 task 物件身份一致性與磁碟持久化。
14. `test_release_terminal_receipt_survives_commit_reload` (`test_release_lease_integration.py`, 6 variants: legacy/current x revision_sync False/True x dispatch_fails False/True):
    - 驗證 release lease bridge 在 commit_status 回呼中經歷 revision sync 後，terminal receipt 依然能夠正確持久化至 canonical status 檔案，使磁碟狀態與 activity log 完全一致。
15. `test_two_release_requests_preserve_history_and_nonce_audit` (`test_release_lease_integration.py`):
    - 驗證連續處理兩筆 release 請求時，第二筆持有的既有 issuance 正確存入 `ISSUANCE_HISTORY_FIELD` 並持久化至磁碟，且 `_nonce_reuse_errors` 成功拒絕該舊 approval nonce 的重複使用。
16. `test_diagnostic_cas_advance_advisory_failure_preserves_canonical_freshness` (`test_dispatch_policy.py`, 4 variants: success, sync_failure, transient_reload_failure, persistent_reload_failure):
    - 驗證在 `advance_approved_prs_to_merge` advisory 提交經歷 sync 失敗或重載失敗時，dispatcher 能夠確認狀態新鮮度或壓抑整輪派工，絕不派發 stale reviewer。

### 4.3 測試執行收據（Test Execution Receipts Bound to Head SHA）

所有驗證命令均以獨立子程序執行，保留原始 terminal exit code 與持續時間，無背景等待迴圈或摘要 grep：

- **Receipt 1 (Diff & Whitespace Check)**:
  - Command: `git diff --check`
  - Exit code: `0`
- **Receipt 2 (Scoped Committed-Diff Whitespace Check against Base)**:
  - Command: `git diff --check origin/dev...HEAD`
  - Exit code: `0`
- **Receipt 3 (Focused Dispatch Policy Test Suite - 59 passed)**:
  - Command: `uv run pytest -q .orchestrator/test_dispatch_policy.py -k 'diagnostic_cas or review_dispatch or stale_wake or cas or release_dead_helper_claims'`
  - Selection: 59 passed (includes advance advisory freshness, candidate refresh, advisory resync, priority rank, lease isolation, and fresh helper revalidation tests)
  - Exit code: `0`
- **Receipt 4 (Supervisor Concurrency, Lease Escalation & Recovery Tests - 16 passed)**:
  - Command: `uv run pytest -q .orchestrator/test_supervisor.py::DispatchStatusSyncTests .orchestrator/test_supervisor.py::AutomaticRecoveryTests::test_ci_failure_requeue_fails_closed_on_stale_status_snapshot`
  - Selection: 16 passed (includes commit task identity preservation and custom tasks_path tests)
  - Exit code: `0`
- **Receipt 5 (Helper Budget Cap Regression Test - 3 passed)**:
  - Command: `uv run pytest -q .orchestrator/test_dispatch_policy.py -k 'test_exhausted_helper_budget_does_not_starve_exact_head_green_review'`
  - Selection: 3 passed (available, zero, consumed budget variants)
  - Exit code: `0`
- **Receipt 6 (Release Lease Bridge Integration Tests - 56 passed)**:
  - Command: `uv run pytest -q .orchestrator/test_release_lease_integration.py`
  - Selection: 56 passed (includes release terminal receipt survives commit reload test with all 6 variants)
  - Exit code: `0`
- **Receipt 7 (Advance Approved PRs Merge Route Tests - 4 passed)**:
  - Command: `uv run pytest -q .orchestrator/test_supervisor.py::ApprovedPrMergeAdvanceTests`
  - Selection: 4 passed
  - Exit code: `0`

---

## 5. 上線與發布說明（Rollout Instructions）

1. **隔離 Worktree 限制**：
   - 任務執行與驗證僅在隔離之 task worktree 內進行，未直接修改 live runtime、state 或 supervisor config。
   - 不手動重啟 supervisor、不提高 quota、不觸動 product tests。
2. **協調者統一管理 Rollout**：
   - 本變更經獨立 PR 審查與 CI 通過並正式合併入 `dev` 後，由協調者（Coordinator）在維護窗口內統一重啟 Supervisor 載入最新控制平面程式碼。
3. **資料相容性**：
   - 無需資料庫或狀態遷移，完全向下相容既有 `ai-status.json` 結構。


## Bounded continuation verification (2026-09-12)

Reject terminal publication when refreshed issuance or request differs; preserve the fresh canonical history. Real disk CAS regression covers external receipt, replacement request, request-only change, task removal, and history-only update across successful and unknown dispatch.

Current source hashes, exact commands, original exit codes and durations are in `bounded-continuation-verification.json`; raw logs are adjacent. Earlier receipts above describe earlier revisions and are retained as history. All current listed commands passed. Tests ran against the recorded parent HEAD plus the tracked patch; source hashes bind them to this repair. Independent exact-head review and required CI remain mandatory before merge.

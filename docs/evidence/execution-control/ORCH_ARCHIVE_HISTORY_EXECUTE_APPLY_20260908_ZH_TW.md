# ORCH-ARCHIVE-HISTORY-EXECUTE-003 收據（第二輪）：canonical apply 已執行，補齊回填證據與派工結果

- 產出者：Claude2（owner）／指定審查者：Codex（reviewer）
- apply 執行時間：2026-09-08T23:56:44Z – 23:57:20Z（canonical checkpoint 所載）
- 本輪量測時間：2026-09-09T00:14:59Z – 00:21:02Z（各節標注實際命令時間）
- 交付分支：`task/ORCH-ARCHIVE-HISTORY-EXECUTE-003`，base `origin/dev` tip
  `ef76cf6d295ce7a8a470fe6e5f0eab20a0439169`

> **本輪沒有執行 `archive_recovery_apply`，也沒有重跑它。** apply 已在 2026-09-08 的維護窗口內
> 由 owner 身份完成；本輪只做讀取量測，把 canonical 產物釘進版控，並補齊驗收要求的五項證據。
> 本輪未建立、未簽署、未延長任何 maintenance hold；未停止、恢復或送訊號給 Supervisor／watchdog；
> 未手寫 `ai-status.json`、`ai-task-archive/` 或 `index.json`；未修改任何既有已合併證據檔或
> `scripts/orchestrator/backfill_task_archive_snapshots.py`。本輪唯一的 canonical 寫入是本任務自身
> 的狀態轉換（supervisor dispatch 觸發的 `start`，以及結束時的送審）。

驗收第 5 條要求的五項證據，逐項對應：

| 驗收要求 | 本文件 | 機器可讀證據 |
| --- | --- | --- |
| apply receipt | §1 | `recovery_apply_receipt_20260908.json` |
| 原 snapshots 未改證據 | §2 | `archive_snapshots_untouched_20260908_post_apply.json` |
| 回填各 ID readback | §3 | `archive_readback_20260908_post_apply.json` |
| 依賴解析前後 | §4 | `dependency_release_analysis_20260908_post_apply.json` |
| Supervisor 恢復派工結果 | §5 | `supervisor_dispatch_recovery_20260909.json` |
| NLTK 最終驗證可派送與否明列 | §6 | `nltk_avm_disposition_20260909.json` |
| AVM Human-only churn 獨立保留 | §7 | 同上 |

---

## 1. Apply receipt

canonical checkpoint：`/home/lupin/odayplus/.orchestrator/recovery-receipts/history-recovery-checkpoint-20260908T235531Z.json`
（版控副本 `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/recovery_apply_checkpoint_20260908T235531Z.json`，
sha256 `cee66fee0de0fd9ed39d1d7ed8abc9a176b3cc96065f2004cc7efe02a2de8f40`）。

| 欄位 | 值 |
| --- | --- |
| `status` | `applied` |
| `actor` | `Claude2`（本任務 owner 本人，非 reviewer） |
| `started_at` / `finished_at` | 2026-09-08T23:56:44Z / 23:57:20Z |
| `batch_sha256` | `1833ca8656ae30efc34d6ade6437ba850c5a53f9fcda67bb1cd4f7c8c70b34fe` |
| `maintenance_hold.sha256` | `e88435aecada1b7b84f4811c3144a54afdb9e428299430e6f6766d4b1ec60348` |
| `rollback_performed` | `false` |
| `board_revision_expected` / `on_disk` | `dbd49aee44644ab89e6e3ffab0d58029` / 同值（CAS 相符） |
| `board_persistence_verified` | `true`（`enclosing canonical status transaction`） |
| 結果 | 23 筆 reconstructed archive snapshot ＋ 15 筆 blocked 佔位 board row |

批次與 hold 的版控副本經重算雜湊，**與 hold 所核准的 hash 位元組相同**：

- `recovery_batch_20260908_applied.json` sha256 = `1833ca86…b34fe` = checkpoint 的 `batch_sha256`
  = hold 的 `batch_approval.approved_batch_sha256`。
- `maintenance_hold_20260908_applied.json` sha256 = `e88435ae…60348` = checkpoint 的 `maintenance_hold.sha256`。

hold 由 **Codex** 出具（`declared_by` / `approved_by` 皆為 Codex，`dispatch_paused: true`，
`expires_at` 2026-09-09T01:55:31Z），核准對象是**上述 exact batch hash**；執行者是 Claude2，
與批准者不同人。批次的 `authorization` 釘的是
`/home/lupin/odayplus-handoffs/odp-execution-wave-2026-09-07/USER_EXECUTION_SCOPE.md`
（sha256 `d7a34fa1fbed837d9f15d2580709689b1178f11f2d1f019383082eebf11deea3`），與上一輪 §1 同一份來源。

`ai-activity-log.jsonl` 有對應的 `archive_history_recovery_apply` 事件（agent `Claude2`，
ts 2026-09-08T23:56:44Z），逐筆列出 38 個 task id、batch hash、hold 路徑與 checkpoint 路徑；
訊息本身即載明「Reconstructed records are labelled and are not original archive bytes」。

`counts` 內部一致性：`archive_snapshots_intended` 與 `applied_archive_snapshots` 逐項相同（23 = 23），
`board_placeholders_intended` 與 `board_placeholders_on_disk` 相同（15 = 15），readback 列數亦為 23 / 15。

## 2. 既有 archive snapshot 未被改動（量測時間 2026-09-09T00:16:29Z）

基準不是本輪事後補寫的清單，而是 **apply 所用批次自帶的 pre-apply digest map**
（`baseline.archive_snapshot_digests`，由 `capture_recovery_baseline()` 於 2026-09-08T23:55:11Z 擷取，
即 apply 開始前 93 秒），共 38 筆。把它與現在磁碟上的 61 筆逐一比對：

| 量測 | 結果 |
| --- | --- |
| `pre_existing_snapshots_changed` | **0 筆** |
| `pre_existing_snapshots_removed` | **0 筆** |
| `snapshots_added` | 23 筆，且**與批次的 23 個 reconstructed id 完全相同** |
| 既有 38 筆的最新 mtime | 2026-09-08T20:36:44Z，**早於** apply 開始的 23:56:44Z |

第二條獨立證據：`ai_status.py` 的 apply 路徑內建 `survivors_tampered()`，只要任何既有 snapshot 的
digest 與 baseline 不符，就會寫 `partial` checkpoint 並 `SystemExit`，且不提交任何 board row。
checkpoint 是 `applied`，代表該檢查當場通過。

`ai-task-archive/index.json` 的 sha256 由 `68fa5dfe…` 變為 `a0b9f4e1…`，這是預期行為而非竄改：
apply 成功後呼叫 `task_archive.rebuild_archive_index()`，`counts.total` 由 38 變 61。
`updated_at` 仍是 `2026-09-08T20:36:41Z`、23 筆重建記錄不在 `recent_terminal_ids` 內，
因為 rebuild 依各 snapshot 的 `archived_at` 排序，而重建記錄帶的是 2026-09-05／09-06 的歷史時間，
排不進最新 20 筆。checkpoint readback 的 `listed_in_archive_index: false` 就是這個原因。

## 3. 回填各 ID readback（量測時間 2026-09-09T00:17:32Z）

本節**不引用 checkpoint 的欄位**，而是重新從磁碟與 canonical reader 讀一次。

**23 筆 archive 記錄**（直接讀 `ai-task-archive/tasks/<ID>.json` 並重算 sha256）：

- 23/23 檔案存在，`terminal_status` 全為 `done`、`terminal_outcome` 全為 `completed`；
- 23/23 帶 `history_recovery.reconstructed = true`、`record_kind = reconstructed_done`、
  `evidence_tier = reconstructable_done`，且都帶「重建記錄……不是原始 archive bytes，
  也不代表原始驗收、CI、部署或核准已完成」的自述 note；
- 23/23 的 `gaps` 皆為空，四項 attestation（acceptance / ci / runtime / approval）齊備；
- 23/23 的 `historical_actors` 一律是 `owner/reviewer/human_go = UNKNOWN-HISTORICAL`
  —— **歷史 actor 維持 unknown，沒有被補寫成任何具名人員**；
- `recovery_actors` 記的是本次復原的 owner `Claude2` / reviewer `Codex`，與歷史 actor 分開存放。

**15 筆 board 佔位**（`AI_NAME=Claude2 /home/lupin/odayplus/scripts/ai-status.sh show <ID>`，
15 次全部 exit 0，`source` 全為 `active`）：

- 15/15 `status = blocked`、`non_dispatchable = true`、`waiting_for = Human/Ops`、
  `record_kind = recovery_placeholder`；
- 15/15 都帶明確的證據缺口：13 筆 `merge_verified`（缺 acceptance 或 runtime），
  2 筆 `merge_claimed`（`DPF-EMGI-LIVE-ROLLOUT-001`、`XR-EXT-OSS-FINAL-AUDIT-001`，
  另帶 `merge_not_verifiable_on_ref`）。

即：**23 done ＋ 15 blocked，沒有把 38 筆全標 done**，與驗收第 2 條一致。

## 4. 依賴解析前後（量測時間 2026-09-09T00:14:59Z）

### 4.1 量測方法

live board 在量測期間持續變動（實測 8 分鐘內 active tasks 由 40 變 49），兩個時間點直接相減會把別的
worker 的進度算進來。因此本節用**同一份凍結 board 快照上的反事實 A/B**：

- 凍結快照 sha256 `e1717107627da9e49fa11ea5944ab5d985514e965f1b1655a26edf04971c477c`，
  `_status_write_revision` `3c88ece701c443078c199d94a90dbeae`；
- **AFTER** = live `PANTHEON_STATUS_ROOT`（61 筆 snapshot、43 筆 board rows），唯讀；
- **BEFORE** = status root 指向 `/tmp` 下只含 38 筆既有 snapshot 的 archive 複本，
  並從凍結 board 移除 15 筆佔位 row——也就是「這次 apply 從沒發生」的世界；
- 判定用 live runtime `oday-plus-supervisor-runtime-current`（→ `…-ef76cf6d295c`）自己的
  `supervisor.canonical_dispatchable_task_ids()`（Dispatcher 的 owner-execution 判定，
  `dispatch_priority_for_task` 回 2 或 3），不是另寫一套資格規則；
- 探針原始碼已附：`dependency_release_probe_20260908.py.txt`
  （sha256 `5d25f108655d196fa8350d24914448d9a38b7fef67962fe3e51edf3fe412e7de`）。

### 4.2 結果

| 指標 | BEFORE | AFTER | 差 |
| --- | --- | --- | --- |
| canonical dispatchable 任務數 | 6 | 6 | **0** |
| `dependencies_satisfied` 為真的任務數 | 6 | 7 | +1 |
| `blocked_task_auto_recovery_eligible` | 0 | 0 | 0 |
| 懸空依賴邊（`missing`） | 43 | **0** | −43 |
| 帶懸空邊的任務數 | 11 | **0** | −11 |

- **新變成可派送的任務：0 筆。** 沒有任何任務因為這次回填而變得可以派送。
- **新滿足依賴閘的任務：1 筆**，`ODP-AVM-QUALITY-NULLABLE-001`（詳見 §7）。
- **沒有退步**：`lost_dispatchable` 與 `lost_dependencies_satisfied` 皆為空集合，
  15 筆 blocked 佔位沒有把任何原本可派送的任務關掉。

### 4.3 為什麼「新變成可派送 = 0」不等於回填無效

上一輪（2026-09-07T15:58Z）在 live runtime 上量到情境 B 會解除 1 筆，就是
`ODP-DRIFT-SECURITY-VERIFY-003`。那一筆在 apply 之前就已用別的路徑處理完：

- 2026-09-08T00:52:15Z，Codex 以 `dependency_update` 把它的懸空邊 `ODP-DRIFT-DEP-REMOVE-002`
  移除（事件記載依當時 live 查核 PR #1222 已 MERGED），依賴改為只剩 `ODP-CI-DEPENDENCY-AUDIT-BOUNDARY-001`；
- 2026-09-08T04:55:07Z 該任務以 PR #1241（approved head `15bb7a6b`，merge 進 dev `961934d7`）收尾 done 並進 archive。

也就是說，唯一一筆可被回填解除的派工，在 apply 前約 19 小時已被人工 retarget ＋ 正常完成消化掉。
回填的實際增量效果因此落在依賴圖的正確性上，而不是新派工。

### 4.4 懸空邊歸零不是成功判準

43 條懸空邊全部解析（25 條變 `done`、18 條變 `blocked`），11 個帶懸空邊的任務降到 0。
但 **blocked 佔位對依賴閘與 `missing` 等價**（`dependency_done_statuses = ["done"]`），
所以「圖乾淨了」不能當成派工恢復的證據；本節的第一判準始終是「新變成可派送的任務名單」＝ 0。

一個具體例子：`ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002` 的三條依賴邊，BEFORE 全是 `missing`，
AFTER 變成兩條 `done`（`ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001`、
`ODP-RELEASE-GATE-FIXTURE-STAGING-002`）＋ 一條 `blocked`
（`ODP-STAGING-RECOVERY-BUNDLE-STORAGE-001`，15 筆佔位之一）。
它的 `dependencies_satisfied` 前後都是 false，但阻塞原因從「三個查不到的 ID」收斂成
「一個具名、待人工裁決的佔位」。這是可行動性的改善，不是派工的解除。

## 5. Supervisor 恢復派工結果（量測時間 2026-09-09T00:18:24Z）

窗口時序（全部取自 `ai-activity-log.jsonl` 與 process 表）：

| 時間 (UTC) | 事件 |
| --- | --- |
| 2026-09-08T23:54:57Z | `supervisor_terminated`：Supervisor PID 1257037 收到 SIGTERM 並關閉 |
| 23:55:11Z | 窗口內重新規劃批次（`generated_at`） |
| 23:55:31Z | Codex 宣告 hold 並對 exact batch hash 簽署批准 |
| 23:56:44Z – 23:57:20Z | `archive_recovery_apply`（actor Claude2），checkpoint `applied` |
| 23:58:07Z | `supervisor_restart_attempted`：**watchdog** 決策 `restart_supervisor`（`reason=missing_pid`），新 PID 1325203 |
| 23:58:13Z | `watchdog_safe_mode_dispatch_suppressed`，抑制新派工至 00:00:06Z |
| 2026-09-09T00:03:10Z | 窗口後第一個 `worker_started`（ODP-CODEX-CLI-ASTRA-COMPAT-UPGRADE-001） |
| 00:07:40Z | Codex `reopen` 本任務，要求補齊 apply 收據與 readback |
| 00:07:56Z | 本 worker 被派送（claude_slot_2） |

停止與恢復都不是本 worker 執行的：SIGTERM 由前景協調者開窗時發出（本收據只記錄 log 所載事實，
不宣稱能證明發送者身分），重啟由 watchdog 自主決策完成。activity log 中沒有任何 worker 發出的
Supervisor 停止／恢復動作。

**恢復結果：** Supervisor 進程存活（PID 1325203，`python3 -u .orchestrator/supervisor.py --verbose`）；
自重啟起共 6 次 `worker_started`；現有 5 個 worker `status=running` 且 PID 存活。

其中 4 筆記到 `task_dispatch_sync_failed`（`Dispatch status sync timed out after 30s`）。
逐一核對後：這 4 個 task 現在都有 running 且 PID 存活的 worker，所以那是 **dispatch 之後的看板同步逾時，
不是派工失敗**；屬既有的看板寫入競爭（同期另有兩筆 `stale_status_write_rejected`），與本次歷史回填無因果關係。
本收據不宣稱有修正它。

## 6. NLTK 最終驗證：可派送與否（量測時間 2026-09-09T00:21:02Z）

**結論：不可派送，原因是「已經完成」，不是「被擋住」。**

`ODP-DRIFT-SECURITY-VERIFY-003`（「驗證原生監控與 NLTK 移除的最終安全證據」）
已於 2026-09-08T04:55:07Z 以 `done` / `completed` 進入 archive，PR #1241、approved head
`15bb7a6bd527285a202f0b8f3601ab2884aca67d`、merge 進 dev `961934d7`。它不在 active board 上，
因此結構上不可能被派送。這與本次 apply 無因果關係（時序見 §4.3）。

本輪另在 live board 逐筆掃描 `nltk` 字樣，只有 `ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002` 命中，
且命中的是 candidate gate 路徑清單裡的檔名 `tests/security/test_nltk_dependency_removal.py`，
不是待辦的 NLTK 處置工作；該任務本身 blocked 於 Human/Ops（6 次 reviewer reopen 後升級），與 NLTK 無關。

**本節不宣稱**：NLTK 相關的供應鏈稽核門檻已全部通過，或 PR #1188 的 pip-audit gate 已解。
本節只回答「NLTK 最終驗證任務」這一件事的可派送狀態。

### 6.1 驗收點名的兩筆重建記錄：獨立重驗

驗收要求「特別核實 NLTK REMOVE-002 與 HZ006」。本輪不採信重建記錄自帶的欄位，改用 `gh` 對 GitHub
重新量測，再與磁碟上的記錄逐欄比對：

| 檢查 | ODP-DRIFT-DEP-REMOVE-002 (PR #1222) | ODP-HZ006-MERGE-SPLIT-IMPLEMENTATION-001 (PR #1170) |
| --- | --- | --- |
| PR state = MERGED | ✅ | ✅ |
| head OID 與記錄相符 | ✅ `6d438486…` | ✅ `0585dd49…` |
| merge commit 與記錄相符 | ✅ `66244b30…` | ✅ `eed8d51b…` |
| exact head CI 與記錄相符 | ✅ 7/7 success | ✅ 7/7 success |
| `task-review-gate` state 與記錄相符 | ✅ success | ✅ success |
| gate 具名審查者與記錄相符 | ✅ Antigravity4 | ✅ Claude |
| 審查者 ≠ 記錄中的 owner | ✅ | ✅ |

兩筆的 `historical_actors` 仍是 `UNKNOWN-HISTORICAL`；gate 描述裡的具名審查者只作為當時 approval 的
證據引用，沒有被回寫成該任務的 owner／reviewer。

`ODP-DRIFT-DEP-REMOVE-002` 記錄內已自行揭露兩處盤點觀察（宣告的 artifact
`tests/models/evidently_baseline_support.py` 在 merge commit 與其第一父皆不存在；acceptance 第 7 條
明文不能代替 PR #1188 的 approval/merge），本輪未消除這兩點，維持原樣揭露。

## 7. AVM 的 Human-only review-churn continuation：獨立保留

`ODP-AVM-QUALITY-NULLABLE-001`：

- **apply 改變的只有依賴閘**：唯一依賴 `ODP-HZ006-MERGE-SPLIT-IMPLEMENTATION-001`
  由 `missing`／`satisfies=false` 變成 archive 解析的 `done`／`satisfies=true`，
  `dependencies_satisfied` 由 false 變 true。
- **apply 沒有改變的**：`status` 仍是 `blocked`、`waiting_for` 仍是 `Human/Ops`、
  `review_reopen_count` 仍是 11。
- **它沒有、也不應該變成可派送**：`blocked_task_auto_recovery_eligible` 在 BEFORE 與 AFTER
  都是空集合——`task_is_human_gate()` 對 `waiting_for=Human/Ops` 直接否決，
  歷史修復不會把 Human-only 的 continuation 閘一併解除。

本任務未對 AVM 做任何狀態寫入，也不宣稱其 review churn 已解除。修復歷史 ≠ 解除 churn。

## 8. 本輪未做、與尚待裁決的事

- 15 筆 blocked 佔位仍待人工裁決（補齊 acceptance／runtime 證據升為 done，或確認以 blocked 結案）；
  在那之前它們對依賴閘與 missing 等價。
- `DPF-EMGI-LIVE-ROLLOUT-001` 與 `XR-EXT-OSS-FINAL-AUDIT-001` 另帶 `merge_not_verifiable_on_ref`，
  連 merge 事實都未能在 pinned ref 上驗證，需要比其他 13 筆更強的來源。
- §5 的 `task_dispatch_sync_failed` 是既有看板寫入競爭，本輪只量測、未修。
- 23 筆重建記錄的 attestation 逐條來源已由本輪對其中 2 筆（驗收點名者）獨立重驗；
  其餘 21 筆維持批次內的 attestation 與 `_disclosure` 揭露，由指定 reviewer Codex 獨立審查。

## 9. 檔案清單與雜湊

| 檔案 | sha256 |
| --- | --- |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/recovery_apply_receipt_20260908.json` | `1f50ed25765d18c4f6a4815335a5d6c1c29af646534124ecd185141b156755fd` |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/recovery_apply_checkpoint_20260908T235531Z.json` | `cee66fee0de0fd9ed39d1d7ed8abc9a176b3cc96065f2004cc7efe02a2de8f40` |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/recovery_batch_20260908_applied.json` | `1833ca8656ae30efc34d6ade6437ba850c5a53f9fcda67bb1cd4f7c8c70b34fe` |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/maintenance_hold_20260908_applied.json` | `e88435aecada1b7b84f4811c3144a54afdb9e428299430e6f6766d4b1ec60348` |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/archive_snapshots_untouched_20260908_post_apply.json` | `24d4eb9bbf9af6e6ec89b9000539b151676284e5cc5612289151186a221eae36` |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/archive_readback_20260908_post_apply.json` | `cc252dd693eec6f5a8a1e67e54756b7e00a1c880ac3f4e43e37c3491217fa4aa` |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/dependency_release_analysis_20260908_post_apply.json` | `72567991fe37d1222da753ac012d8f6c5c64f9b8a511661fd75d54baa615a9d1` |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/supervisor_dispatch_recovery_20260909.json` | `275daa15cf0c096f189289282312b2eef8e090911345b9e385cbba6734336524` |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/nltk_avm_disposition_20260909.json` | `4387d03cc5e3fea6023020ec9083f90eb63e762c16d78e70ed8f11d144b8482a` |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/dependency_release_probe_20260908.py.txt` | `5d25f108655d196fa8350d24914448d9a38b7fef67962fe3e51edf3fe412e7de` |

上一輪（PR #1238）的檔案未被本輪修改；其 §開頭「本輪沒有執行 apply」的敘述針對的是那一輪，
仍然成立，本文件是接續的第二輪收據。

# ORCH-ARCHIVE-HISTORY-EXECUTE-003 收據：canonical apply 執行收據、審查更正記錄與派工恢復驗證

- 產出者：Claude2（owner・第 2 輪）／Antigravity3（owner・第 3 輪）／指定審查者：Codex（reviewer）
- apply 執行時間：2026-09-08T23:56:44Z – 23:57:20Z（canonical checkpoint 所載）
- 第 2 輪量測時間：2026-09-09T00:14:59Z – 00:21:02Z
- 第 3 輪更正與審查判定時間：2026-09-09T01:15:00Z – 01:25:00Z（包含 Codex review finding P1/P2 實質修正與控制面 blocker 提報）
- 交付分支：`task/ORCH-ARCHIVE-HISTORY-EXECUTE-003`，base `origin/dev` tip `c42b734c`

> **本輪沒有執行 `archive_recovery_apply`，也沒有重跑它。** apply 已在 2026-09-08 的維護窗口內由 owner 完成；已套用的批次、hold 與 checkpoint 原始 bytes **完整保留、未私自修改**。
> 本輪針對 Codex 獨立審查提出的兩項 substantive findings（P1: `ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001` 與 P2: `ODP-MODELREADY-QUALITY-NULLABLE-001`）進行深度實質查證，產出機器可讀更正記錄與控制面 blocker，正式撤銷無缺口 done/completed 聲明，明列下游依賴影響，並完成 live canonical 狀態重量。
> 本輪未建立、未簽署、未延長任何 maintenance hold；未停止、恢復或送訊號給 Supervisor／watchdog；未手寫 `ai-status.json`、`ai-task-archive/` 或 `index.json`；未修改任何既有已合併證據檔或 `scripts/orchestrator/backfill_task_archive_snapshots.py`。

驗收第 5 條與審查意見要求之各項證據，逐項對應：

| 驗收要求 / 審查意見項 | 本文件 | 機器可讀證據 / 原始碼 |
| --- | --- | --- |
| apply receipt | §1 | `recovery_apply_receipt_20260908.json` |
| 原 snapshots 未改證據 | §2 | `archive_snapshots_untouched_20260908_post_apply.json` |
| 回填各 ID readback | §3 | `archive_readback_20260908_post_apply.json` 及 `archive_readback_20260909_post_findings.json` |
| 依賴解析前後 | §4 | `dependency_release_analysis_20260908_post_apply.json` |
| Supervisor 恢復派工結果 | §5 | `supervisor_dispatch_recovery_20260909.json` |
| NLTK 最終驗證可派送與否明列 | §6 | `nltk_avm_disposition_20260909.json` |
| AVM Human-only churn 獨立保留 | §7 | 同上 |
| 審查意見 P1/P2 實質更正記錄 | §8 | `recovery_disposition_corrections_20260909.json` |
| 控制面 blocker 與安全處置方案 | §9 | `control_plane_remediation_blocker_20260909.json` |

---

## 1. Apply receipt

canonical checkpoint：`/home/lupin/odayplus/.orchestrator/recovery-receipts/history-recovery-checkpoint-20260908T235531Z.json`
（版控副本 `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/recovery_apply_checkpoint_20260908T235531Z.json`，sha256 `cee66fee0de0fd9ed39d1d7ed8abc9a176b3cc96065f2004cc7efe02a2de8f40`）。

| 欄位 | 值 |
| --- | --- |
| `status` | `applied` |
| `actor` | `Claude2`（第 2 輪 owner 本人，非 reviewer） |
| `started_at` / `finished_at` | 2026-09-08T23:56:44Z / 23:57:20Z |
| `batch_sha256` | `1833ca8656ae30efc34d6ade6437ba850c5a53f9fcda67bb1cd4f7c8c70b34fe` |
| `maintenance_hold.sha256` | `e88435aecada1b7b84f4811c3144a54afdb9e428299430e6f6766d4b1ec60348` |
| `rollback_performed` | `false` |
| `board_revision_expected` / `on_disk` | `dbd49aee44644ab89e6e3ffab0d58029` / 同值（CAS 相符） |
| `board_persistence_verified` | `true`（`enclosing canonical status transaction`） |
| 結果 | 23 筆 reconstructed archive snapshot ＋ 15 筆 blocked 佔位 board row |

批次與 hold 的版控副本經重算雜湊，**與 hold 所核准的 hash 位元組相同**：

- `recovery_batch_20260908_applied.json` sha256 = `1833ca86…b34fe` = checkpoint 的 `batch_sha256` = hold 的 `batch_approval.approved_batch_sha256`。
- `maintenance_hold_20260908_applied.json` sha256 = `e88435ae…60348` = checkpoint 的 `maintenance_hold.sha256`。

hold 由 **Codex** 出具（`declared_by` / `approved_by` 皆為 Codex，`dispatch_paused: true`，`expires_at` 2026-09-09T01:55:31Z），核准對象是**上述 exact batch hash**；執行者是 Claude2，與批准者不同人。批次的 `authorization` 釘的是 `/home/lupin/odayplus-handoffs/odp-execution-wave-2026-09-07/USER_EXECUTION_SCOPE.md`（sha256 `d7a34fa1fbed837d9f15d2580709689b1178f11f2d1f019383082eebf11deea3`）。

`ai-activity-log.jsonl` 有對應的 `archive_history_recovery_apply` 事件（agent `Claude2`，ts 2026-09-08T23:56:44Z），逐筆列出 38 個 task id、batch hash、hold 路徑與 checkpoint 路徑；訊息本身即載明「Reconstructed records are labelled and are not original archive bytes」。

`counts` 內部一致性：`archive_snapshots_intended` 與 `applied_archive_snapshots` 逐項相同（23 = 23），`board_placeholders_intended` 與 `board_placeholders_on_disk` 相同（15 = 15），readback 列數亦為 23 / 15。

---

## 2. 既有 archive snapshot 未被改動

基準取自 apply 所用批次自帶的 pre-apply digest map（`baseline.archive_snapshot_digests`，由 `capture_recovery_baseline()` 於 2026-09-08T23:55:11Z 擷取），共 38 筆。把它與磁碟上的 61 筆（及後續完成之 62 筆）逐一比對：

| 量測 | 結果 |
| --- | --- |
| `pre_existing_snapshots_changed` | **0 筆** |
| `pre_existing_snapshots_removed` | **0 筆** |
| `snapshots_added` (recovery) | 23 筆，且**與批次的 23 個 reconstructed id 完全相同** |
| 既有 38 筆的最新 mtime | 2026-09-08T20:36:44Z，**早於** apply 開始的 23:56:44Z |

第二條獨立證據：`ai_status.py` 的 apply 路徑內建 `survivors_tampered()`，只要任何既有 snapshot 的 digest 與 baseline 不符，就會寫 `partial` checkpoint 並 `SystemExit`，且不提交任何 board row。checkpoint 是 `applied`，代表該檢查當場通過。

`ai-task-archive/index.json` 由 apply 成功後呼叫 `task_archive.rebuild_archive_index()` 重建，`counts.total` 由 38 變 61。`updated_at` 仍是 `2026-09-08T20:36:41Z`、23 筆重建記錄不在 `recent_terminal_ids` 內，因為 rebuild 依各 snapshot 的 `archived_at` 排序，而重建記錄帶的是 2026-09-05／09-06 的歷史時間，排不進最新 20 筆。checkpoint readback 的 `listed_in_archive_index: false` 即為此原因。

---

## 3. 回填各 ID readback

本節直接由磁碟與 canonical reader 重讀。

**23 筆 archive 記錄**（直接讀 `ai-task-archive/tasks/<ID>.json`）：

- 23/23 檔案存在，`terminal_status` 全為 `done`、`terminal_outcome` 全為 `completed`；
- 23/23 帶 `history_recovery.reconstructed = true`、`record_kind = reconstructed_done`、`evidence_tier = reconstructable_done`，且都帶「重建記錄……不是原始 archive bytes，也不代表原始驗收、CI、部署或核准已完成」的自述 note；
- 23/23 的 `historical_actors` 一律是 `owner/reviewer/human_go = UNKNOWN-HISTORICAL` —— **歷史 actor 維持 unknown，沒有被補寫成任何具名人員**；
- `recovery_actors` 記的是本次復原的 owner `Claude2` / reviewer `Codex`，與歷史 actor 分開存放；
- **實質更正宣告（詳見 §8）**：其中 2 筆（`ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001` 與 `ODP-MODELREADY-QUALITY-NULLABLE-001`）經 Codex 獨立審查指出實質缺口，本收據正式宣告撤銷其無缺口 done 完成聲明，並在 §8 補正真實處置。

**15 筆 board 佔位**（`AI_NAME=Antigravity3 $PANTHEON_STATUS_ROOT/scripts/ai-status.sh show <ID>`，15 次全部 exit 0，`source` 全為 `active`）：

- 15/15 `status = blocked`、`non_dispatchable = true`、`waiting_for = Human/Ops`、`record_kind = recovery_placeholder`；
- 15/15 都帶明確的證據缺口：13 筆 `merge_verified`（缺 acceptance 或 runtime），2 筆 `merge_claimed`（`DPF-EMGI-LIVE-ROLLOUT-001`、`XR-EXT-OSS-FINAL-AUDIT-001`，另帶 `merge_not_verifiable_on_ref`）。

即：**23 done ＋ 15 blocked，沒有把 38 筆全標 done**，與驗收第 2 條一致。

---

## 4. 依賴解析前後

### 4.1 量測方法

live board 在量測期間持續變動，兩個時間點直接相減會把別的 worker 的進度算進來。因此本節用**同一份凍結 board 快照上的反事實 A/B**：

- 凍結快照 sha256 `e1717107627da9e49fa11ea5944ab5d985514e965f1b1655a26edf04971c477c`，`_status_write_revision` `3c88ece701c443078c199d94a90dbeae`；
- **AFTER** = live `PANTHEON_STATUS_ROOT`（61 筆 snapshot、43 筆 board rows），唯讀；
- **BEFORE** = status root 指向 `/tmp` 下只含 38 筆既有 snapshot 的 archive 複本，並從凍結 board 移除 15 筆佔位 row——即「這次 apply 從沒發生」的世界；
- 判定用 live runtime `oday-plus-supervisor-runtime-current` 的 `supervisor.canonical_dispatchable_task_ids()`。
- 探針原始碼：`dependency_release_probe_20260908.py.txt`（sha256 `5d25f108…`）。

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
- **沒有退步**：`lost_dispatchable` 與 `lost_dependencies_satisfied` 皆為空集合，15 筆 blocked 佔位沒有把任何原本可派送的任務關掉。

### 4.3 為什麼「新變成可派送 = 0」不等於回填無效

在 live runtime 上量到情境 B 會解除 1 筆，即 `ODP-DRIFT-SECURITY-VERIFY-003`。那一筆在 apply 之前就已由 Codex 手動 retarget 移除懸空邊並以 PR #1241（merge 進 dev `961934d7`）收尾 done 並進 archive。唯一一筆可被回填解除的派工已在 apply 前被人工消化掉，回填的實際增量效果因此落在依賴圖的正確性上，而不是新派工。

---

## 5. Supervisor 恢復派工結果

窗口時序：

| 時間 (UTC) | 事件 |
| --- | --- |
| 2026-09-08T23:54:57Z | `supervisor_terminated`：Supervisor PID 1257037 收到 SIGTERM 並關閉 |
| 23:55:11Z | 窗口內重新規劃批次（`generated_at`） |
| 23:55:31Z | Codex 宣告 hold 並對 exact batch hash 簽署批准 |
| 23:56:44Z – 23:57:20Z | `archive_recovery_apply`（actor Claude2），checkpoint `applied` |
| 23:58:07Z | `supervisor_restart_attempted`：**watchdog** 決策 `restart_supervisor`（`reason=missing_pid`），新 PID 1325203 |
| 23:58:13Z | `watchdog_safe_mode_dispatch_suppressed`，抑制新派工至 00:00:06Z |
| 2026-09-09T00:03:10Z | 窗口後第一個 `worker_started`（ODP-CODEX-CLI-ASTRA-COMPAT-UPGRADE-001） |

停止與恢復皆非本 worker 執行：SIGTERM 由前景協調者開窗時發出，重啟由 watchdog 自主決策完成。activity log 中無任何 worker 發出的 Supervisor 停止／恢復動作。

**恢復結果：** Supervisor 進程存活（PID 1325203）；自重啟後正常進行派工管理，現有 worker 正常運作。

---

## 6. NLTK 最終驗證：可派送與否

**結論：不可派送，原因是「已經完成」，不是「被擋住」。**

`ODP-DRIFT-SECURITY-VERIFY-003`（「驗證原生監控與 NLTK 移除的最終安全證據」）已於 2026-09-08T04:55:07Z 以 `done` / `completed` 進入 archive（PR #1241、approved head `15bb7a6bd527`、merge 進 dev `961934d7`）。它不在 active board 上，結構上不可能被派送。

### 6.1 驗收點名的兩筆重建記錄：獨立重驗

驗收要求「特別核實 NLTK REMOVE-002 與 HZ006」。對 GitHub 重新量測並與記錄比對：

| 檢查 | ODP-DRIFT-DEP-REMOVE-002 (PR #1222) | ODP-HZ006-MERGE-SPLIT-IMPLEMENTATION-001 (PR #1170) |
| --- | --- | --- |
| PR state = MERGED | ✅ | ✅ |
| head OID 與記錄相符 | ✅ `6d438486…` | ✅ `0585dd49…` |
| merge commit 與記錄相符 | ✅ `66244b30…` | ✅ `eed8d51b…` |
| exact head CI 與記錄相符 | ✅ 7/7 success | ✅ 7/7 success |
| `task-review-gate` state 與記錄相符 | ✅ success | ✅ success |
| gate 具名審查者與記錄相符 | ✅ Antigravity4 | ✅ Claude |
| 審查者 ≠ 記錄中的 owner | ✅ | ✅ |

兩筆的 `historical_actors` 仍是 `UNKNOWN-HISTORICAL`。

---

## 7. AVM 的 Human-only review-churn continuation：獨立保留

`ODP-AVM-QUALITY-NULLABLE-001`：

- **apply 改變的只有依賴閘**：唯一依賴 `ODP-HZ006-MERGE-SPLIT-IMPLEMENTATION-001` 變為 archive 解析的 `done`，`dependencies_satisfied` 變為 true。
- **apply 沒有改變的**：`status` 仍是 `blocked`、`waiting_for` 仍是 `Human/Ops`、`review_reopen_count` 仍是 11。
- **它沒有、也不應該變成可派送**：`blocked_task_auto_recovery_eligible` 仍為空集合——`task_is_human_gate()` 對 `waiting_for=Human/Ops` 直接否決，歷史修復不會解除 Human-only continuation 閘。修復歷史 ≠ 解除 churn。

---

## 8. Codex 實質審查意見（P1 & P2）深度查證與更正記錄

依據 Codex 審查意見，本輪對兩項爭議任務進行逐 byte 與逐 commit 的精確核實，並產出版控更正記錄 `recovery_disposition_corrections_20260909.json`：

### 8.1 P1: `ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001`（Human-only 缺口誤標 done）

1. **原始事實查證**：
   - 任務 PR #1169（merge commit `830a8ebf919cd3f9dc3d53fc13c4f76e65eeff6e`，交付檔 `docs/evidence/ODP_MERGE_QUEUE_DISPOSITION_2026-09-03.md`）。
   - 該收據第 9 行明文：「判定狀態：`BLOCKED_BY_EVIDENCE`（查無權威裁決，阻擋至 Human/Ops；未代簽任何 disposition）」。
   - 第 23-25 行確認決策不存在、第 95-99 行判定任務阻擋至 `Human/Ops`，第 109-113 行法定欄位（decider、decision_date、risk_owner）皆為「待填」。
   - 原任務驗收 A2（`task_evidence_inventory.json:9701`）明文規定：「找不到權威決策時 task 轉 blocked waiting Human/Ops」。
2. **批次缺陷判定**：
   - 套用批次將其重建為 `reconstructable_done`、`gaps=[]` 屬於過度推論。PR MERGED 與 CI 綠燈僅為交付證明，不能取代 Human/Ops 法定簽署。
3. **更正處置**：
   - **正式撤銷無缺口 done/completed 聲明**。
   - 其真實處置應為 `evidence_tier: merge_verified`、`status: blocked`、`waiting_for: Human/Ops`，並帶 `human_authority_missing` 與 `acceptance_gap_blocked_by_evidence` 缺口。
4. **下游依賴影響**：
   - 看板直接依賴者：`ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001`（status `todo`）。
   - 其驗收要求「20 項逐一有 VERIFIED 或有效 formal nonimplementation disposition 且無 OPEN／BLOCKED_BY_EVIDENCE」。
   - `ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001` 維持 `BLOCKED_BY_EVIDENCE` 狀態，正當且必要地阻止了 downstream closeout 任務在缺乏權威裁決時冒充全案完成。

### 8.2 P2: `ODP-MODELREADY-QUALITY-NULLABLE-001`（生產入口負向測試適用性缺口）

1. **原始事實查證**：
   - 任務 PR #1168（head `a2a8d9c235e88820572b30700d5f5cc9b31bb0af`，merge commit `10d47d17a7b3ca8d3c655afaaf02b931c3fb7e0f`）。
   - 驗收 A4（`task_evidence_inventory.json:10150-10179`）要求：「植入缺值 production-entry 測試與兩筆 exemption 移除在同一 commit」。
   - 實查 head `a2a8d9c235e8` 被引用的 `modules/learninghub/tests/test_learninghub_production_runtime.py`，`_rows` 始終提供正向 `quality=.98/confidence=.95`，未植入缺值負向測試；實作 commit `38a58161b4c7` 亦未改該檔。
   - 新增的缺值負向測試實位於 `tests/data/test_pit_snapshot.py:313-378`，屬 domain builder 單元測試，而非 HTTP/runtime 生產入口測試。
2. **批次缺陷判定**：
   - 驗收 A4 存在證據適用性缺口，不能以 green CI 取代 exact-head 生產入口測試核實，不能在無缺口下評定為 `reconstructable_done`。
3. **更正處置**：
   - **正式撤銷無缺口 done/completed 聲明**。
   - 其真實處置保留 `acceptance_gap_production_entry_test_unverified` 缺口。
4. **下游依賴影響**：
   - 看板直接依賴者：`ODP-CANONICAL-MEASUREMENT-NULLABLE-CUTOVER-001`（status `todo`）。
   - 其驗收要求「六個 production-shaped 缺值測試證明 abstain／mark／reject 且六筆 exemptions 同 commit 歸零」。
   - 下游 cutover 任務必須在 W3 切換時將 ModelReadyRecord production-shaped 缺值測試完整補齊，不得視為已解決。

---

## 9. 控制面限制、Mutation 拒絕與安全處置方案

本輪實查控制面工具鏈，確認以下關鍵限制：

1. **Canonical Writer 拒絕 In-Place Reopen Archived ID**：
   - `scripts/ai_status.py` 第 6546-6550 行明文限制：若任務存在於 `ai-task-archive/tasks/`，`reopen` 直接拒絕並提示 `Task <task-id> is archived and cannot be reopened in place. Create a new follow-up task that references <task-id>.`
   - 控制面亦無撤回 snapshot 或就地修改 `evidence_tier` 的子命令。
2. **禁止繞過 CAS 手寫磁碟**：
   - 依據專案治理規則與驗收條件，禁止 worker 直接修改 `ai-task-archive/tasks/*.json`、`ai-status.json` 或 `ai-task-archive/index.json`，亦禁止在無 hold 下重跑 batch。
3. **安全處置方案（已提交 `control_plane_remediation_blocker_20260909.json`）**：
   - **建議方案（方案 A）**：以版控更正記錄（`recovery_disposition_corrections_20260909.json`）為法定真實依據，在既有 archive bytes 維持不可變的前提下，由 Codex 與下游任務（`ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001` 與 `ODP-CANONICAL-MEASUREMENT-NULLABLE-CUTOVER-001`）嚴格鎖定此二缺口作為前置驗收門檻，防止缺口外溢。
   - **備選方案（方案 B）**：若需完全同步 archive 檔案結構，由前景協調者建立正式維護窗口並簽署 hold，透過受控控制面修復工具或 migration 腳本調整 snapshot 欄位。

---

## 10. 交付檔案與雜湊清單

| 檔案路徑 | SHA-256 | 說明 |
| --- | --- | --- |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/recovery_apply_receipt_20260908.json` | `1f50ed25765d18c4f6a4815335a5d6c1c29af646534124ecd185141b156755fd` | Apply 執行收據 |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/recovery_apply_checkpoint_20260908T235531Z.json` | `cee66fee0de0fd9ed39d1d7ed8abc9a176b3cc96065f2004cc7efe02a2de8f40` | Canonical apply checkpoint 副本 |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/recovery_batch_20260908_applied.json` | `1833ca8656ae30efc34d6ade6437ba850c5a53f9fcda67bb1cd4f7c8c70b34fe` | 已套用 batch 副本（原始 bytes 不動） |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/maintenance_hold_20260908_applied.json` | `e88435aecada1b7b84f4811c3144a54afdb9e428299430e6f6766d4b1ec60348` | 已核准 hold 副本（原始 bytes 不動） |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/archive_snapshots_untouched_20260908_post_apply.json` | `24d4eb9bbf9af6e6ec89b9000539b151676284e5cc5612289151186a221eae36` | 既有 38 筆 snapshot 零更動量測證據 |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/archive_readback_20260908_post_apply.json` | `cc252dd693eec6f5a8a1e67e54756b7e00a1c880ac3f4e43e37c3491217fa4aa` | 第 2 輪 23+15 讀回記錄 |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/archive_readback_20260909_post_findings.json` | `c32cec411dd9deda9972bd2b35ad774bf33748dd8677a49d7e72f55e5e62ed49` | 第 3 輪最新看板與 archive 讀回記錄 |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/dependency_release_analysis_20260908_post_apply.json` | `72567991fe37d1222da753ac012d8f6c5c64f9b8a511661fd75d54baa615a9d1` | 反事實 A/B 依賴解除分析 |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/supervisor_dispatch_recovery_20260909.json` | `275daa15cf0c096f189289282312b2eef8e090911345b9e385cbba6734336524` | Supervisor 恢復與重啟紀錄 |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/nltk_avm_disposition_20260909.json` | `4387d03cc5e3fea6023020ec9083f90eb63e762c16d78e70ed8f11d144b8482a` | NLTK 完成與 AVM 狀態處置 |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/recovery_disposition_corrections_20260909.json` | `f550f1e694a0895798a31fffabcabd1cd028fcc72ca66871c7dbe69423a83686` | **P1/P2 審查意見實質更正記錄** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/control_plane_remediation_blocker_20260909.json` | `5f639b0506ef34ce99a23cd55a956b9b2a21db1a23d39dae7691cd08c2877987` | **控制面限制與修復方案報告** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/dependency_release_probe_20260908.py.txt` | `5d25f108655d196fa8350d24914448d9a38b7fef67962fe3e51edf3fe412e7de` | 依賴解析量測探針原始碼 |
| `docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_EXECUTE_APPLY_20260908_ZH_TW.md` | — | 本收據文件 |

本輪只新增/更新 docs/evidence 檔案，未動任何 `.py`、config、workflow 或治理 manifest。交付物由指定審查者 Codex 獨立審查。

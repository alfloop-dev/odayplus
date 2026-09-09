# ORCH-ARCHIVE-HISTORY-EXECUTE-003 收據：canonical apply 執行收據、控制面 invalidation 實測與歷史回填收尾

- 產出者：Claude2（owner・第 2 輪）／Antigravity3（owner・第 3–5 輪）／維護窗口與 invalidation 協調者：Codex／指定審查者：Codex（reviewer）
- apply 執行時間：2026-09-08T23:56:44Z – 23:57:20Z（canonical checkpoint 所載，actor Claude2）
- 第 2 輪量測時間：2026-09-09T00:14:59Z – 00:21:02Z
- 第 3/4 輪更正與審查記錄時間：commit 064f475f（01:12:43Z），送審（01:14:09Z），Codex 審查退回（01:21:54Z）。原更正記錄文件中所載 01:15Z–01:25Z 為手動觀察整理時間，原命令終端量測時間不可恢復，明列 unknown。
- 第 5 輪控制面修復、invalidation 實測與收尾時間：2026-09-09T16:22:55Z – 16:24:34Z（維護窗口 16:22:55Z–16:23:46Z，supervisor 重啟與健康回讀 16:24:04Z–16:24:23Z，reopen/assign 16:24:12Z–16:24:55Z）。
- 交付分支：`task/ORCH-ARCHIVE-HISTORY-EXECUTE-003`，base `origin/dev` tip `50581b3b180aedaf76fbe4aaffb3bf7e2d120050`（PR #1287 merge commit）

> **核心事實與邊界聲明**：
> 1. **原始回填產物 bytes 完整保留**：2026-09-08 維護窗口套用之批次（`recovery_batch_20260908_applied.json`）、hold（`maintenance_hold_20260908_applied.json`）與 checkpoint（`recovery_apply_checkpoint_20260908T235531Z.json`）原始 bytes **零更動**。磁碟上 88 筆既有 snapshot 原始 bytes **零更動**。
> 2. **控制面 blocker 已實際解除**：PR #1287（exact reviewed head `88285f59`，merge commit `50581b3b180a`）已合併進 dev；新 runtime 部署於 `/home/lupin/oday-plus-supervisor-runtime-50581b3b180a`。前景協調者 Codex 於 2026-09-09T16:22:55Z – 16:23:46Z 新維護窗口內經唯一 canonical `scripts/ai-status.sh archive_recovery_invalidate` 完成兩筆爭議任務之失效處置。
> 3. **清楚區分原始快照 done 與 resolver 有效 blocked**：`ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001` 與 `ODP-MODELREADY-QUALITY-NULLABLE-001` 磁碟 snapshot 保留歷史重建記錄（`done`/`completed`）；live `TaskResolver` 經 `ai-task-archive/corrections/` 正式讀回為 `effective_status: blocked`，`dependency_satisfied: false`。
> 4. **有效狀態分佈**：23 筆重建 snapshot 現為 **21 done、2 blocked**；15 筆佔位維持 **15 blocked**（`non_dispatchable: true`, `waiting_for: Human/Ops`）。全 38 筆無全 done 冒充。
> 5. **實質缺口與下游依賴**：Human/Ops 裁決缺口與 ModelReady production-entry A4 測試適用性缺口實質保留，未宣稱為產品完成。下游兩任務（`ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001` 與 `ODP-CANONICAL-MEASUREMENT-NULLABLE-CUTOVER-001`）整體 admission 在更正前後均為 false。
> 6. **Supervisor 狀態**：重啟後運行 PID `1789757`，loaded code SHA `50581b3b180aedaf76fbe4aaffb3bf7e2d120050`，heartbeat 16:24:04Z，successful loop 17s。未釋放新產品派工。
> 7. **時間更正**：舊手動觀察時間不可考者明列 unknown，本輪 invalidation 均由不可變原始 UTC 命令收據支持。本輪未重跑 apply/invalidation，不操作 Supervisor，嚴守治理邊界。

---

## 1. Apply receipt（2026-09-08 維護窗口）

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
- `recovery_batch_20260908_applied.json` sha256 = `1833ca86…b34fe`
- `maintenance_hold_20260908_applied.json` sha256 = `e88435ae…60348`

---

## 2. 既有 88 筆 Archive Snapshot 零更動證據

在 2026-09-09 維護窗口中，Codex 對磁碟上全部 88 筆既有 snapshot 進行了 SHA-256 重算（收錄於 `maintenance-window.json` 與 `after-apply.json`）：

| 量測 | 結果 |
| --- | --- |
| `pre_existing_snapshots_changed` | **0 筆** |
| `pre_existing_snapshots_removed` | **0 筆** |
| `original_archives_unchanged` | **88 / 88** |
| `immutable_evidence_unchanged` | **true**（3 份原始 apply 產物雜湊全數相符） |

88 筆原始快照檔案皆維持原始 bytes，未因 invalidation 而被修改或刪除。

---

## 3. 回填各 ID 讀回與狀態分佈

結合磁碟原始快照與 live TaskResolver 回讀結果：

### 3.1 23 筆 Reconstructed Snapshots（現為 21 done / 2 blocked）
- **21 筆 effective done**（`status: done`, `dependency_satisfied: true`）：
  - `ODP-AVM-DEPRECIATION-CONTRACT-001`, `ODP-CANONICAL-LEGACY-LINEAGE-001`, `ODP-DRIFT-DEP-REMOVE-002`, `ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001`, `ODP-HZ006-MERGE-SPLIT-IMPLEMENTATION-001`, `ODP-INT-MANUAL-CORRECTION-AUDIT-001`, `ODP-LH-PREDICTION-DRIFT-001`, `ODP-LH003-BACKTEST-RELEASE-GATE-001`, `ODP-MEASUREMENT-CROSSLAYER-GATE-001`, `ODP-NETPLAN-DISCLOSURE-UI-E2E-001`, `ODP-OPS002-DECISION-COMMENTS-001`, `ODP-PRICE006-BANDIT-GATED-001`, `ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001`, `ODP-RELEASE-GATE-FIXTURE-STAGING-002`, `ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001`, `ODP-RELEASE-ROLLBACK-DATA-HANDOFF-001`, `ODP-REQ-DISPOSITION-GOVERNANCE-001`, `ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001`, `ODP-SITESCORE-QUALITY-NULLABLE-001`, `ODP-SPEC-SOURCE-PROVENANCE-001`, `ODP-TENANT-PLATFORM-ADMIN-FAILCLOSED-001`
- **2 筆 effective blocked**（`status: blocked`, `dependency_satisfied: false`）：
  - `ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001`（invalidation: 缺少 Human/Ops 權威裁決，撤回完成）
  - `ODP-MODELREADY-QUALITY-NULLABLE-001`（invalidation: A4 缺值 production-entry 測試適用性缺口，撤回完成）
- 23 筆之歷史 actor 一律保留 `UNKNOWN-HISTORICAL`。

### 3.2 15 筆 Board 佔位（全數維持 blocked）
- 15/15 `status = blocked`、`non_dispatchable = true`、`waiting_for = Human/Ops`、`record_kind = recovery_placeholder`。
- 13 筆 `merge_verified`，2 筆 `merge_claimed`（`DPF-EMGI-LIVE-ROLLOUT-001`, `XR-EXT-OSS-FINAL-AUDIT-001`）。

**總結分佈：21 done、2 blocked（重建）、15 blocked（佔位）＝ 21 done、17 blocked。**

---

## 4. 依賴解析與反事實分析

| 指標 | 2026-09-08 Apply 前 | 2026-09-08 Apply 後 | 2026-09-09 Invalidation 後 |
| --- | --- | --- | --- |
| 懸空依賴邊（`missing`） | 43 | 0 | 0 |
| `ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001` dependency_satisfied | missing (false) | **true**（過度推論） | **false**（更正） |
| `ODP-MODELREADY-QUALITY-NULLABLE-001` dependency_satisfied | missing (false) | **true**（過度推論） | **false**（更正） |
| `ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001` admission | false | false | **false** |
| `ODP-CANONICAL-MEASUREMENT-NULLABLE-CUTOVER-001` admission | false | false | **false** |
| 新變成可派送任務數 | — | 0 | **0** |

- **下游任務未曾整體可派送**：`ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001` 與 `ODP-CANONICAL-MEASUREMENT-NULLABLE-CUTOVER-001` 在更正前均有其他前置依賴為 false，整體 admission 始終為 false；invalidation 確保其直接前置依賴亦正確回讀為 false，正當阻擋下游推進。

---

## 5. 控制面修復與 Invalidation 執行實測

### 5.1 工具交付與部署
- 工具 PR #1287 由 `task/ORCH-ARCHIVE-RECOVERY-INVALIDATION-001` 交付，審查 exact head `88285f59`，經 merge queue 合併為 `50581b3b180aedaf76fbe4aaffb3bf7e2d120050`。
- 部署於 live runtime `/home/lupin/oday-plus-supervisor-runtime-50581b3b180a`。

### 5.2 維護窗口與命令收據（2026-09-09 UTC）
- **維護窗口記錄**（`maintenance-window.json`）：
  - 窗口時間：`2026-09-09T16:22:55.891767Z` – `16:23:46.504109Z`
  - 執行者：Codex
  - status：`applied_and_verified`
  - restart_command_succeeded：`true`
- **Invalidation 命令 1**（`confirm-1.json`）：
  - `started_at` 16:22:58Z，`finished_at` 16:23:20Z，exit 0
  - 目標：`ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001`，寫入 `ai-task-archive/corrections/ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001.json`
- **Invalidation 命令 2**（`confirm-2.json`）：
  - `started_at` 16:23:20Z，`finished_at` 16:23:43Z，exit 0
  - 目標：`ODP-MODELREADY-QUALITY-NULLABLE-001`，寫入 `ai-task-archive/corrections/ODP-MODELREADY-QUALITY-NULLABLE-001.json`
- **回讀驗證**（`canonical-show-1.json`、`canonical-show-2.json`、`after-apply.json`）：
  - 兩 ID 之 `ai-status.sh show` 均 exit 0，回讀 `source: archive`，`effective_status: blocked`，`dependency_satisfied: false`。

---

## 6. Supervisor 維護與派工恢復結果

依據 `post-restart-health.json`（觀察時間 `2026-09-09T16:24:23.358511Z`，actor Codex）：
- Supervisor PID：`1789757`（started_at: `2026-09-09T16:23:46Z`）
- `lifecycle`: `running`
- `loaded_code_sha`: `50581b3b180aedaf76fbe4aaffb3bf7e2d120050`
- `last_heartbeat_at`: `2026-09-09T16:24:04Z`
- `last_successful_loop_at`: `2026-09-09T16:24:04Z`（duration 17000 ms, error: null）
- `active_workers`: `[]`
- **派工結論**：Supervisor 正常維護並監控看板，未釋放新產品任務，未新增未授權派工。

---

## 7. NLTK 最終驗證與 AVM 狀態處置

- **NLTK 最終驗證**（`ODP-DRIFT-SECURITY-VERIFY-003`）：
  - status: `done`，`terminal_outcome: completed`（於 2026-09-08T04:55:07Z 收尾進 archive，PR #1241）。
  - 不可派送之原因是已完成。
- **AVM Human-Only Churn**（`ODP-AVM-QUALITY-NULLABLE-001`）：
  - status: `blocked`，`waiting_for: Human/Ops`，`review_reopen_count: 11`。
  - 歷史修復未解除 Human-only churn 閘，獨立保留等待人類裁決。

---

## 8. 審查意見 P1/P2 實質缺口分析

1. **P1: `ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001`**
   - 交付收據（`docs/evidence/ODP_MERGE_QUEUE_DISPOSITION_2026-09-03.md`）載明 `BLOCKED_BY_EVIDENCE`，缺 decider/decision_date/risk_owner。
   - 原始驗收 A2 要求缺權威決策時保持 blocked。
   - Invalidation 已成功撤回完成語意，缺口實質保留，不代簽 disposition。
2. **P2: `ODP-MODELREADY-QUALITY-NULLABLE-001`**
   - 驗收 A4 要求之缺值 production-entry 測試未植入於 `modules/learninghub/tests/test_learninghub_production_runtime.py`。
   - Invalidation 已成功撤回完成語意，缺口實質保留，交由下游 cutover 任務補齊。

---

## 9. 時間誠信與量測來源揭露

- **原第 3/4 輪時間說明**：第 3/4 輪記錄中提及之 01:15Z–01:25Z 為手動撰寫整理時間，原命令終端之 timestamp 與 exit receipt 無法恢復，已明列 unknown 並標記為沿用舊觀察。
- **第 5 輪不可變時間收據**：本輪控制面 invalidation 與 Supervisor 重啟時間均來自 2026-09-09T16:22–16:24Z 之真實命令 exit receipt（包含完整 argv、exit_code、stdout、stderr 與 UTC ISO-8601 時間戳）。

---

## 10. 交付檔案與雜湊清單

| 檔案路徑 | SHA-256 | 說明 |
| --- | --- | --- |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/HISTORY_CLOSEOUT_HANDOFF_ZH_TW.md` | `d3940b563538399bd7baecc443c19d2b041c3ef788b83ad299c252854989f36f` | **收尾交接指引原文副本** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/RECOVERY_APPLY_CHECKPOINT_20260907.json` | `c7c003d9c2000c78a1515d352b4462972635be582e85d76043079e7012fc2ad9` | RECOVERY_APPLY_CHECKPOINT_20260907.json |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/after-apply.json` | `ad69bef2d23657d99117f836cf25f95d4a320462e39ce44708510a437f005350` | **維護窗口 Invalidation 後 TaskResolver 讀回** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/after-readback-command.json` | `7321b3dca8b64a6b3720cb19e504ca3c2a394e5ea83b983acbc69c63748bcb8e` | **Invalidation 後讀回命令收據** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/apply_window.py` | `b50487b738272d62c8f80f0f30c5a8d0c18f30bf775e5ca5b5194928d842ee2f` | **維護窗口 Invalidation 自動化執行腳本** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/archive_readback_20260908_post_apply.json` | `cc252dd693eec6f5a8a1e67e54756b7e00a1c880ac3f4e43e37c3491217fa4aa` | archive_readback_20260908_post_apply.json |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/archive_readback_20260909_post_findings.json` | `773a32b494434113cdaa42b3942b0e2cfb6e78b92f1014a62627cc9904ed7a4e` | archive_readback_20260909_post_findings.json |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/archive_snapshots_untouched_20260907_close.json` | `8389552aabbb0c4671aa718d53653d2b47dd6c772ef556c1a13dbf7100ba6b09` | archive_snapshots_untouched_20260907_close.json |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/archive_snapshots_untouched_20260907_open.json` | `521116874d5cd8ce1d8686c2516422c044e3099f9163baf8566132fb2fc8e097` | archive_snapshots_untouched_20260907_open.json |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/archive_snapshots_untouched_20260908_post_apply.json` | `24d4eb9bbf9af6e6ec89b9000539b151676284e5cc5612289151186a221eae36` | archive_snapshots_untouched_20260908_post_apply.json |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/attestations_planner_input_20260907.json` | `c84822169f6b824dfdcd0315de57338f416536e6da678bbea4fed047d93bb5e3` | attestations_planner_input_20260907.json |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/before-apply.json` | `3b372db1192c65b15375c95791d8e2beeed43f832ae713506fc03a1147128105` | **維護窗口 Invalidation 前 TaskResolver 讀回** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/before-readback-command.json` | `d14167907a017535d30c6a3c89da5db33d055391ca7cec2253a8dfa88357ad40` | **Invalidation 前讀回命令收據** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/canonical-show-1.json` | `2c4acb2b7457ddd26e6b594470fbcf669b657c8d8a4a34f0b35c3f2f4b13ba5f` | **ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001 Canonical Show 讀回收據** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/canonical-show-2.json` | `341f8addde5a74004c950e0d14aaea604b87acc28a3c9e10660b4be11b4a404c` | **ODP-MODELREADY-QUALITY-NULLABLE-001 Canonical Show 讀回收據** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/confirm-1.json` | `3ceb6a46cab3cf23c235d38297ffe6180e9327e26b6a8411649d0866373e59a3` | **ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001 Invalidation 執行收據** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/confirm-2.json` | `5eb66cad0532ad20a939ae04f5d2f223af80b28534299d7fecec275f9969e19b` | **ODP-MODELREADY-QUALITY-NULLABLE-001 Invalidation 執行收據** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/control_plane_remediation_blocker_20260909.json` | `84e6c6045f00f1cab7af3d3bbf93135ffef31ce9cd17e8135fedc8ce0981ce6c` | **控制面 Blocker 解除報告** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/dependency_release_analysis_20260907_execute.json` | `91c7cd64483a714a3ae8b270aa485d89b77c8c2730ba49a197ee73cbcd796993` | dependency_release_analysis_20260907_execute.json |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/dependency_release_analysis_20260907_execute_live_runtime.json` | `383ee36004458edc36062ecbb64f6a0bb364bf11c35e44d112f43f737bf845f5` | dependency_release_analysis_20260907_execute_live_runtime.json |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/dependency_release_analysis_20260908_post_apply.json` | `72567991fe37d1222da753ac012d8f6c5c64f9b8a511661fd75d54baa615a9d1` | dependency_release_analysis_20260908_post_apply.json |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/dependency_release_probe_20260908.py.txt` | `5d25f108655d196fa8350d24914448d9a38b7fef67962fe3e51edf3fe412e7de` | dependency_release_probe_20260908.py.txt |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/dry-run-1.json` | `af89475d392a2bb7d1fc4686054919dc6cdea74d16e5b41d4a889558c0bc9348` | **Invalidation Dry Run 1 收據** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/dry-run-2.json` | `99a5c27f6fa55fe5aa880ba085f04f6f53fcac307c101acdb98342e54e67dee6` | **Invalidation Dry Run 2 收據** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/history-closeout-metadata.json` | `d770514360743574e7f2993898384f974fbd1a48c191dfad523ca837f79f2fb2` | **歷史收尾指派 Metadata** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/history-closeout-reopen.json` | `63b4688c8eef367ca78d1c1b6048d92293fa819c713a42ce56812223bb24beae` | **歷史收尾 Reopen 狀態收據** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/live-evidence-manifest.json` | `1f8409f41862056030e244bd9a773977dd0620b54a7b7b979a7308b95228b3a1` | **Live Invalidation 證據清單** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/maintenance-window.json` | `fe681d1b81dc704c626c5b6cbe1ed3a1d60f127e6f9f3aeb8b4144db2a848d14` | **2026-09-09 Invalidation 維護窗口記錄** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/maintenance_hold_20260908_applied.json` | `e88435aecada1b7b84f4811c3144a54afdb9e428299430e6f6766d4b1ec60348` | 原始 Apply hold 副本（bytes 不動） |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/maintenance_hold_template_NOT_A_HOLD.json` | `459940b255b0e85ebaff4468010afee666a87eaa9462a1218363f1c896e83f0f` | maintenance_hold_template_NOT_A_HOLD.json |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/nltk_avm_disposition_20260909.json` | `4387d03cc5e3fea6023020ec9083f90eb63e762c16d78e70ed8f11d144b8482a` | nltk_avm_disposition_20260909.json |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/nltk_followup_recheck_20260907.txt` | `49c51466a063f97e05aa1933c1c082505d582161cf68d92b9b8ac9c0d16a51c9` | nltk_followup_recheck_20260907.txt |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/plan.json` | `9edb88c60dbe490b8f3a04b835dd32456739e9668127a536e51d96a8698daea8` | **Invalidation 維護計劃** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/post-restart-dispatch.json` | `bb3fdeb60b90359abff61641db590867eb38c9100bd078d38397c87a8e14876f` | **Supervisor 重啟後派工狀態收據** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/post-restart-health.json` | `0a97ae386008ba05b56e5ee5829af67dae1e5a2a06d3b86e0f1d8e5c10acc8bc` | **Supervisor 重啟後健康收據** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/preadmission_check_20260907.json` | `dc48073589b0ad99d7b9a42498107a02ca53ff0d8d9f8f9117c3bfcdd449a05a` | preadmission_check_20260907.json |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/priority_two_local_recheck_20260907.txt` | `dd2de6c573553bfa05808b7bba0d43e8259b1a74eeafbf562275844e5ea10813` | priority_two_local_recheck_20260907.txt |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/priority_two_reverification_20260907.txt` | `8315265533fdfe855ee46d1c5b9d61393823a2459b864cd2065158e561ec9630` | priority_two_reverification_20260907.txt |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/readback.py` | `e6a1aea78a3a544fd97ec1e689f8c063dd53e0c061bc2aaf456712e0ddf98507` | **維護窗口 TaskResolver 讀回探針指令碼** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/recovery_apply_checkpoint_20260908T235531Z.json` | `cee66fee0de0fd9ed39d1d7ed8abc9a176b3cc96065f2004cc7efe02a2de8f40` | 原始 Apply checkpoint 副本（bytes 不動） |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/recovery_apply_receipt_20260908.json` | `1f50ed25765d18c4f6a4815335a5d6c1c29af646534124ecd185141b156755fd` | 2026-09-08 Apply 執行收據 |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/recovery_batch_20260907_scenarioA_as_documented.json` | `3c7f5928db956bb6fd49840a09ab9e27de85fa5f550b15773327ec1988ab71c5` | recovery_batch_20260907_scenarioA_as_documented.json |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/recovery_batch_20260907_scenarioB.json` | `639059236692d8b0fc32d9b6cb1f0210f84068c023c0fdcf90c7760875f674a6` | recovery_batch_20260907_scenarioB.json |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/recovery_batch_20260908_applied.json` | `1833ca8656ae30efc34d6ade6437ba850c5a53f9fcda67bb1cd4f7c8c70b34fe` | 原始 Apply batch 副本（bytes 不動） |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/recovery_disposition_corrections_20260909.json` | `e4c04640395b45d609b5b2cfcd648a52e35c1060b9fb5a4b894aa198164e3231` | **P1/P2 實質更正與 Invalidation 記錄** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/rollout-preflight.json` | `cd805edf4c1b5fce602c36e2f05360c1ceb7915ab02874c21968803e1eff31c6` | **Runtime Rollout 預檢收據** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/rollout-result.json` | `fbfa8cdf843b35d0a9db4648f717a8d9d89110d5b52c7fc34217974c79713e91` | **Runtime Rollout 執行結果收據** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/supervisor-restart.json` | `71af05961963fab4793c10230ccebcb472276f5db0f228e74396c11b4b2eb21f` | **Supervisor 重啟命令收據** |
| `ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/supervisor_dispatch_recovery_20260909.json` | `275daa15cf0c096f189289282312b2eef8e090911345b9e385cbba6734336524` | supervisor_dispatch_recovery_20260909.json |
| `docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_EXECUTE_APPLY_20260908_ZH_TW.md` | — | 本收據主文件 |

交付物由指定審查者 Codex 獨立審查。

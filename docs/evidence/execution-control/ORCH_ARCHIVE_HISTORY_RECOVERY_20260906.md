# ORCH-ARCHIVE-HISTORY-RECOVERY-001 第一階段收據（2026-09-06）

本輪只完成「受控重建」的**程式與離線計畫**。**沒有**對 canonical board 或 archive 做任何寫入，
**沒有**恢復任何歷史，**沒有**宣稱任何任務已完成部署或驗收。

## 1. 交付內容

| 項目 | 路徑 |
| --- | --- |
| 證據分級 / 批次規劃（延伸既有 backfill planner） | `scripts/orchestrator/backfill_task_archive_snapshots.py` |
| 唯一寫入者（延伸 canonical ai_status writer transaction） | `scripts/ai_status.py` → `archive_recovery_apply` |
| 焦點測試 | `scripts/orchestrator/test_archive_history_recovery.py` |
| 離線批次（本輪產出，供 Codex 審查） | `docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_RECOVERY_20260906-batch.json` |

沒有新增第二套 writer、dispatcher 或 maintenance workspace manager：
archive 寫入沿用 `task_archive.archive_task_snapshot()`，board 寫入沿用 `main()` 已持有的
`status_write_transaction()`（因此 apply 指令本身**不得**再取一次鎖，已由靜態測試釘住）。

## 2. 證據分級規則

| 等級 | 條件 | 處置 |
| --- | --- | --- |
| `reconstructable_done` | 本機 git 在釘住的 ref 上證實該任務**自己的** branch merge，且 acceptance / ci / runtime / approval 四項證言齊備（每項都要 source + verified_at + verifier），且沒有任何其他 gap | 寫入 terminal archive |
| `merge_verified` | 本機 git 證實 merge，但缺任一證言或有來源衝突 | active `blocked` + `non_dispatchable` 復原佔位 |
| `merge_claimed` | 盤點有可追溯的候選 PR，但本 ref 無法驗證（例：跨 repo） | 同上 |
| `no_evidence` | 沒有任何候選交付證據 | 同上 |

已合併的 PR 只是**交付**證據，不等於驗收。四項證言缺一即不得重建為 `done`。
若本機 git 與盤點宣告的 merge commit 不一致，記為 `candidate_merge_commit_mismatch` 並同樣阻擋升級——
兩個來源互相矛盾時默默選一邊，正是本工具要避免的失效模式。

## 3. 拒絕規則（fail closed，整批拒絕）

規劃期與 apply 期各驗一次：

- `active_task_id_conflict`：ID 已在現行看板。
- `archive_snapshot_exists`：ID 已有 archive snapshot（事故後倖存的 6 筆永不被覆寫）。
- `duplicate_inventory_id`：盤點內重複 ID。
- `missing_candidate_provenance`：候選缺 `number` / `url` / `headRefName` / `mergedAt` / `mergeCommit.oid`。
  無法追溯的宣稱比沒有宣稱更糟，因此拒絕而非降級。
- `fabricated_historical_actor`：證言檔案若夾帶 `historical_owner` / `historical_reviewer` /
  `human_go` / `approver` / `review_approval`，整筆拒絕。授權明確不含補造這些身分。
- baseline 漂移：board `_status_write_revision`、archive `index.json` 摘要、
  每一筆既有 snapshot 的 SHA-256 任一改變即整批拒絕，一個 byte 都不寫。

歷史 actor 一律記為 `UNKNOWN-HISTORICAL`，與現任恢復 owner/reviewer（`history_recovery.recovery_actors`）
分開兩個欄位；`UNKNOWN-HISTORICAL` 不得作為恢復 owner/reviewer。

## 4. 本輪離線批次結果

指令（唯讀，未帶 `--apply`；recovery 模式本身也拒絕 `--apply`）：

```
uv run --frozen --python 3.12 python scripts/orchestrator/backfill_task_archive_snapshots.py \
  --archive-dir /home/lupin/odayplus/ai-task-archive/tasks \
  --repo . --ref origin/dev \
  --board /home/lupin/odayplus/ai-status.json \
  --recovery-inventory /tmp/odayplus-archive-incident.CYD1gq/RECOVERY_DEPENDENCY_CANDIDATES_20260906_1523.json \
  --authorization /tmp/odayplus-archive-incident.CYD1gq/RECOVERY_AUTHORIZATION_ZH_TW.md \
  --batch-out docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_RECOVERY_20260906-batch.json \
  --recovery-owner Claude --recovery-reviewer Codex
```

結果：**38 筆規劃、0 筆重建 done、38 筆 blocked 佔位、0 筆拒絕**，exit 0。
分級：`merge_verified` 36 筆、`merge_claimed` 2 筆（`DPF-EMGI-LIVE-ROLLOUT-001` 為 oday-data-platform 跨 repo；
`XR-EXT-OSS-FINAL-AUDIT-001` 在 `origin/dev` 上無法驗證）。
全部 38 筆都缺 acceptance / ci / runtime / approval 四項證言，因此**沒有任何一筆**可以重建為 done。
盤點的 44 條直接依賴邊全數記入各筆的 `history_recovery.known_dependents`。

釘住的 baseline（規劃當下實測）：

| 項目 | 值 |
| --- | --- |
| `ref` / `ref_commit` | `origin/dev` / `bd4fb5aa11404519ff1d8ae97fa796eb6d40e12a` |
| board `_status_write_revision` | `e5fdf617dba143a6a2c7fc4a6709608f` |
| board SHA-256 | `70bbdc5436715dfdea06a6c5424fa67e8f8ddfc82421e06835c198a1b865c34c` |
| archive `index.json` SHA-256 | `5e5b59dbf423056aee1dfc57a739a2f740ff2fad6bc7ac87df084413bc02c44a` |
| 盤點 JSON SHA-256 | `116f583e238e309b9f818403fe60cc135870ba1e72f396a8df66e78d7e2b0685` |
| 授權文件 SHA-256 | `134adcb8098397b9a4b74542b9b4903a32cb07a82e0675ef54d9194dbdd2aba5` |
| 既有 snapshot | 6 筆，各自 SHA-256 已逐筆釘入 `baseline.archive_snapshot_digests` |

執行後複驗：`ai-task-archive/index.json` 摘要不變、`tasks/` 仍為 6 筆、board revision 未動。

## 5. 焦點測試

```
uv run --frozen --python 3.12 pytest \
  scripts/orchestrator/test_archive_history_recovery.py \
  scripts/test_ai_status.py \
  scripts/orchestrator/test_backfill_task_archive_snapshots.py
```

結果：**289 passed, 101 subtests passed**，exit code 0（新測試模組單獨跑為 35 passed）。
未執行產品全套測試。

覆蓋：證據不足不得合併為 done、blocked 佔位形狀（`non_dispatchable` / `waiting_for` / 依賴保留）、
未知 actor 與 Human GO 補造拒絕、缺 provenance 拒絕、board revision 與 archive 摘要漂移拒絕、
idempotency（第二次 apply 必拒）、partial failure 只留 checkpoint 且不宣稱 rollback、
既有 6 筆 byte 不變、`--confirm` 缺 checkpoint 拒絕、缺 maintenance hold 拒絕、
不得重入 canonical lock（靜態）、未註冊 actor 拒絕、重建 archive 記錄不得寫入現任 actor、
佔位 actor 必須與批次 recovery pair 一致，以及獨立直譯器的 import-order sentinel。

import-order sentinel 用另一個 python 進程重跑真實 import 順序：
`ai_status` 與 `task_archive` 的 archive 路徑必須來自環境變數指向的臨時 root、
不得落在 checkout 內，且該 root 內既有 snapshot 必須 byte 相同。
這條性質是 import 順序造成的，同一模組內的斷言看不見它。

## 6. 明確未完成／未宣稱

- **未執行 canonical apply。** 第一階段禁止，需 Codex 審核批次與 maintenance hold 後另行安排。
- **歷史未恢復。** 38 筆全部維持缺證據，計畫中的處置是 blocked 佔位，不是完成。
- 未宣稱任何 PR 的 acceptance/CI/runtime/approval 已通過；未補造 Human GO、核准者或歷史 owner/reviewer。
- 未動 Supervisor（無 SIGCONT/restart/kill）、未改 watchdog 或模型設定、未刪鎖、未刪依賴、未派工部署。
- 本收據不是簽章授權或 lease verifier 的 receipt。

## 7. 給下一階段（apply）的注意事項

本批次釘住的是 2026-09-06 當下的 live board revision。看板持續在動，**因此這份已提交的批次幾乎必然
會在 apply 時因 baseline drift 被拒——那是設計行為，不是缺陷**。正式執行時必須：

1. 先建立並確認 maintenance hold（派工暫停），讓看板靜止；
2. 在 hold 下**重新產生**批次；
3. 由 Codex 審核重新產生的批次；
4. 才執行 `archive_recovery_apply --batch <新批次> --maintenance-hold <ref> --checkpoint <path> --confirm`。

apply 為多檔案操作，不是單一原子交易：archive snapshot 是各自的檔案，board 由外層 canonical
transaction 一次寫入。因此失敗時只會留下 checkpoint 記錄實際落地的項目，並明講**沒有執行 rollback**；
成功時的 checkpoint 也標記 `board_persistence_verified: false`，因為 board 是在指令回傳後才由外層交易寫入。

## 8. 相對 owned_paths 的範圍偏離（已揭露）

派工宣告的 owned_paths 為 `scripts/ai_status.py`、
`scripts/orchestrator/backfill_task_archive_snapshots.py`、
`scripts/orchestrator/test_backfill_task_archive_snapshots.py`、
`.orchestrator/task_archive.py`、
`docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_RECOVERY_20260906.md`。
本次實際改動多出四項，逐項理由如下；`.orchestrator/task_archive.py` 未修改（只作為來源閱讀）。

| 路徑 | 理由 |
| --- | --- |
| `scripts/orchestrator/test_archive_history_recovery.py` | 驗收要求的焦點測試。放在 `scripts/orchestrator/` 是因為 `config/code-boundaries.yaml` 的 `verification_ownership` 用萬用字元涵蓋 `scripts/orchestrator/test_*.py`；放在 `scripts/` 會被判為 `development_platform` bundle 內的 foreign scope，且需要改動治理 manifest 的顯式白名單。 |
| `scripts/test_ai_status.py` | 被既有測試強制。`ActorCommandMutationGuardTests.test_ai_name_case_table_covers_every_actor_bearing_command` 要求每個新增的 mutating command 都要進 `AI_NAME_CASES` 表，否則既有套件必紅。只加了一列表項。 |
| `docs/audits/code-boundary-inventory.csv` | 被 `check_code_boundaries.py` 強制：新增任何 .py 都必須重產，否則 CI `orchestrator` job 與 task_finalize 的必過閘會擋。差異為 1 行。 |
| `docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_RECOVERY_20260906-batch.json` | 驗收要求交付的離線批次本身；`.md` 收據無法承載 38 筆逐項 provenance。與宣告的 `.md` 同目錄同前綴。 |

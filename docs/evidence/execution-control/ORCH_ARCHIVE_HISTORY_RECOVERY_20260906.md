# ORCH-ARCHIVE-HISTORY-RECOVERY-001 第一階段收據（2026-09-06）

本輪只完成「受控重建」的**程式與離線計畫**。**沒有**對 canonical board 或 archive 做任何寫入，
**沒有**恢復任何歷史，**沒有**宣稱任何任務已完成部署或驗收。

## 1. 交付內容

| 項目 | 路徑 |
| --- | --- |
| 證據分級 / 批次規劃 / 共用 validator（延伸既有 backfill planner） | `scripts/orchestrator/backfill_task_archive_snapshots.py` |
| 唯一寫入者（延伸 canonical ai_status writer transaction） | `scripts/ai_status.py` → `archive_recovery_apply` |
| 焦點測試 | `scripts/orchestrator/test_archive_history_recovery.py` |
| 離線批次（本輪產出，供 Codex 審查） | `docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_RECOVERY_20260906-batch.json` |

沒有新增第二套 writer、dispatcher 或 maintenance workspace manager：
archive 寫入沿用 `task_archive.archive_task_snapshot()`，board 寫入沿用 `main()` 已持有的
`status_write_transaction()`（因此 apply 指令本身**不得**再取一次鎖，已由靜態測試釘住）。

## 2. 本輪修正（回應 Codex 2026-09-06 審查，PR #1231）

上一版的四項 P1 都成立，皆為靜態可達、測試未涵蓋的缺陷。逐項修正與新回歸如下。

### P1-1 admission 只驗了外殼 → 鎖內對整批跑共用嚴格 validator

新增 `planner.validate_recovery_batch()` / `validate_recovery_entry()`，由 apply 在鎖內對**整份文件**
執行；planner 也用同一支（`for_apply=False`）自檢自己的輸出，兩邊不會各自漂移。
批次在規劃與套用之間是一個任何人都能編輯的 JSON 檔，因此每一項原本由 grading 保證的性質都重新推導一次：

- `record.id` 必須等於 `entry.task_id`；批內重複 ID 整批拒絕。
- `evidence_tier` 必須是已知等級，且與 `record.history_recovery.evidence_tier`、`gaps` 三者一致。
- `archive_reconstructed_done` 必須：tier 為 `reconstructable_done`、`gaps` 為空、
  `status=done`、`terminal_outcome=completed`、owner/reviewer 皆為 `UNKNOWN-HISTORICAL`、
  四項證言齊備（各有 source/verified_at/verifier）、有已驗證的 `local_merge.merge_commit`、有 `archived_at`。
- `active_blocked_placeholder` 必須：`status=blocked`、`non_dispatchable=true`、無 `terminal_outcome`、
  有 `waiting_for`、actor 等於批次 recovery pair、且**不得**帶 `archived_at`。
- provenance：`history_recovery` 必須存在且 `reconstructed=true`、`created_by` 正確、
  `record_kind` 對應 action、`historical_actors` 三欄皆為 `UNKNOWN-HISTORICAL`、
  候選不得帶 `missing_provenance`、證言不得夾帶補造身分、
  每筆 record 的 baseline（ref/ref_commit/inventory/authorization hash）必須等於批次 baseline。

回歸：把 placeholder 改標成 `archive_reconstructed_done`、只拉高 `evidence_tier`、
把 placeholder 改成 `todo`/`non_dispatchable=false`、換掉 `record.id`、批內重複 ID、
刪掉 `history_recovery`、寫入歷史 actor、夾帶 `human_go`、改寫 record baseline——九種竄改各一則，
全部要求整批拒絕且 board/archive/checkpoint 三者皆無變化。

### P1-2 refusals 非空仍套用其餘 entries → 整批拒絕、零寫入

`validate_recovery_batch(..., for_apply=True)` 在 `refusals` 非空時直接拒絕整份文件（preview 也拒）。
planner 另外輸出 `applicable` 欄位讓審查者一眼看出該批不可套用。
回歸：一筆既有 snapshot 衝突 + 一筆可用候選的混合批次，可用的那筆也不得落地。

### P1-3 checkpoint 以 staged/applied 代替落盤收據 → 全部改為讀回磁碟

`write_checkpoint()` 的每個欄位都是 read-back：逐檔檢查 snapshot 是否在磁碟上、其 SHA-256、
`terminal_status`、是否被 index 列出；board 則直接讀回 `ai-status.json`。失敗點全數覆蓋：

| 失敗點 | 收據 |
| --- | --- |
| `archive_task_snapshot()` 拋錯 | `partial`，已落盤的 snapshot 由磁碟列出（含「寫了 snapshot 但 index save 失敗」這一段） |
| 寫入後讀不回 terminal snapshot | `partial`，停止並不寫任何 board row |
| `rebuild_archive_index()` 拋錯（原本在 try/except 外） | `partial` |
| 既有 6 筆被更動 | `partial` |
| 外層 `sync_all()` / board 未落盤 | post-commit read-back 寫 `partial` 並讓指令非零退出 |

`board_persistence_verified` 只有在**外層交易提交後**的 read-back 成功時才為 `true`；
在指令回傳前一律 `false`，即使該批次根本沒有 board row（「沒有要寫」與「已寫入」是兩種收據）。
這由新的 `register_post_commit_verifier()` 在鎖內、`sync_all()` 之後執行。
所有 checkpoint 一律 `rollback_performed: false`——多檔案操作沒有 rollback，宣稱有才是說謊。

### P1-4 只比對 revision 與 archive 摘要 → 鎖內核對全部釘住的來源

`_archive_recovery_baseline_drift()` 現在還會核對：board bytes 的 SHA-256（revision 未變但內容漂移也擋）、
`git rev-parse` 實測 pinned ref 的 commit、盤點 JSON 與授權文件的 SHA-256（檔案消失同樣拒絕）；
baseline 缺任一必要欄位（`REQUIRED_BASELINE_FIELDS`）即拒絕，不得靠省略欄位換到一個沒人檢查的 baseline。
另新增 `_archive_recovery_evidence_drift()`：每一筆 reconstructed done 的 merge commit 都要在鎖內
用 `git merge-base --is-ancestor` 重新證明它確實包含在釘住的 ref 裡。

### maintenance hold：從自由字串改為可稽核文件

`--maintenance-hold` 現在收的是文件路徑，不是 reference 字串（自由字串只是執行者自己打的字，
不能證明任何事）。文件必須是 `task_history_recovery_maintenance_hold`，且：

- `dispatch_paused` 必須為 `true`，並帶 `hold_id` / `declared_by` / `declared_at` / `scope`；
- `expires_at` 必須可解析且尚未過期（過期即「不再持有」），`declared_at` 不得在未來；
- `batch_sha256` 必須等於本次批次檔的 SHA-256；
- `batch_approval` 必須由批次的 recovery reviewer 簽出、`approved_batch_sha256` 為同一個 hash、
  並帶 `approved_at` 與 `source`；**核准者不得是執行 apply 的 actor**。

hold 的路徑、SHA-256、`hold_id`、核准者與核准來源全部寫入 checkpoint。
回歸涵蓋：自由字串、綁到別的批次、已過期、未宣告暫停、缺 `batch_approval`、
核准別的 hash、核准者不是批次 reviewer、自己核准自己，以及成功時 hold 進入 checkpoint。

## 3. 證據分級規則

| 等級 | 條件 | 處置 |
| --- | --- | --- |
| `reconstructable_done` | 本機 git 在釘住的 ref 上證實該任務**自己的** branch merge，且 acceptance / ci / runtime / approval 四項證言齊備（每項都要 source + verified_at + verifier），且沒有任何其他 gap | 寫入 terminal archive |
| `merge_verified` | 本機 git 證實 merge，但缺任一證言或有來源衝突 | active `blocked` + `non_dispatchable` 復原佔位 |
| `merge_claimed` | 盤點有可追溯的候選 PR，但本 ref 無法驗證（例：跨 repo） | 同上 |
| `no_evidence` | 沒有任何候選交付證據 | 同上 |

已合併的 PR 只是**交付**證據，不等於驗收。四項證言缺一即不得重建為 `done`。
若本機 git 與盤點宣告的 merge commit 不一致，記為 `candidate_merge_commit_mismatch` 並同樣阻擋升級——
兩個來源互相矛盾時默默選一邊，正是本工具要避免的失效模式。

## 4. 拒絕規則（fail closed，整批拒絕）

規劃期與 apply 期各驗一次，apply 期用的是同一支 validator：

- `active_task_id_conflict`：ID 已在現行看板。
- `archive_snapshot_exists`：ID 已有 archive snapshot（事故後倖存的 6 筆永不被覆寫）。
- `duplicate_inventory_id`：盤點內重複 ID；apply 期另檢批內重複。
- `missing_candidate_provenance`：候選缺 `number` / `url` / `headRefName` / `mergedAt` / `mergeCommit.oid`。
  無法追溯的宣稱比沒有宣稱更糟，因此拒絕而非降級。
- `fabricated_historical_actor`：證言檔案若夾帶 `historical_owner` / `historical_reviewer` /
  `human_go` / `approver` / `review_approval`，整筆拒絕。授權明確不含補造這些身分。
- 規劃期任一 refusal 非空 → apply 期整批拒絕（見 §2 P1-2）。
- baseline 漂移：board `_status_write_revision`、board bytes、archive `index.json` 摘要、
  每一筆既有 snapshot 的 SHA-256、pinned ref 的 commit、盤點 JSON 與授權文件的 SHA-256，
  任一改變即整批拒絕，一個 byte 都不寫。
- 缺 maintenance hold、hold 未綁定本批次 hash、hold 已過期或未經 reviewer 核准 → 拒絕。

歷史 actor 一律記為 `UNKNOWN-HISTORICAL`，與現任恢復 owner/reviewer（`history_recovery.recovery_actors`）
分開兩個欄位；`UNKNOWN-HISTORICAL` 不得作為恢復 owner/reviewer。

## 5. 本輪離線批次結果

指令（唯讀，未帶 `--apply`；recovery 模式本身也拒絕 `--apply`；`--authorization` 現為必填）：

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

結果：**38 筆規劃、0 筆重建 done、38 筆 blocked 佔位、0 筆拒絕**，exit 0（與上一輪的保守分類相同）。
分級：`merge_verified` 36 筆、`merge_claimed` 2 筆（`DPF-EMGI-LIVE-ROLLOUT-001` 為 oday-data-platform 跨 repo；
`XR-EXT-OSS-FINAL-AUDIT-001` 在 `origin/dev` 上無法驗證）。
全部 38 筆都缺 acceptance / ci / runtime / approval 四項證言，因此**沒有任何一筆**可以重建為 done。
盤點的 44 條直接依賴邊全數記入各筆的 `history_recovery.known_dependents`。

重產後的批次以 apply 期 validator 離線覆驗（純函式，未接觸 canonical）：
`validate_recovery_batch(batch, for_apply=True)` 回傳 `[]`，`applicable: true`。

釘住的 baseline（本輪重新規劃當下實測）：

| 項目 | 值 |
| --- | --- |
| `ref` / `ref_commit` | `origin/dev` / `bd4fb5aa11404519ff1d8ae97fa796eb6d40e12a` |
| board `_status_write_revision` | `3e0d561d8a904e32936fac7f833864bc` |
| board SHA-256 | `3e82311b1e82242ee9a754eeb622788369e9668d531fe8a633d8b24578a4b698` |
| archive `index.json` SHA-256 | `5e5b59dbf423056aee1dfc57a739a2f740ff2fad6bc7ac87df084413bc02c44a` |
| 盤點 JSON SHA-256 | `116f583e238e309b9f818403fe60cc135870ba1e72f396a8df66e78d7e2b0685` |
| 授權文件 SHA-256 | `134adcb8098397b9a4b74542b9b4903a32cb07a82e0675ef54d9194dbdd2aba5` |
| 批次檔本身 SHA-256 | `392eda29a1977509569ca7b7caeff907bf334b50e1646647b69f495f0dcd76c8` |
| 既有 snapshot | 6 筆，各自 SHA-256 已逐筆釘入 `baseline.archive_snapshot_digests` |

（board revision 自上一輪的 `e5fdf617…` 前進，是因為期間有其他任務正常寫入 canonical board；
archive 摘要與既有 6 筆未變。）

執行後複驗：`ai-task-archive/index.json` 摘要不變、`tasks/` 仍為 6 筆、board 未被本任務寫入。

## 6. 焦點測試

```
uv run --frozen --python 3.12 pytest \
  scripts/orchestrator/test_archive_history_recovery.py \
  scripts/test_ai_status.py \
  scripts/orchestrator/test_backfill_task_archive_snapshots.py
```

結果：**321 passed, 101 subtests passed**，exit code 0，7.54s（新測試模組單獨跑為 67 passed）。
本輪只整批執行這一次；未執行產品全套測試。

覆蓋：證據不足不得合併為 done、blocked 佔位形狀（`non_dispatchable` / `waiting_for` / 依賴保留）、
未知 actor 與 Human GO 補造拒絕、缺 provenance 拒絕、baseline 六類漂移各自拒絕
（board revision、同 revision 不同 board bytes、archive 摘要、pinned ref、盤點、授權文件）、
批次被竄改的九種形狀、混合有效／拒絕批次整批拒絕、
idempotency（第二次 apply 必拒）、partial failure 的四個失敗點各留 checkpoint 且不宣稱 rollback、
外層 board 未落盤時的 post-commit 讀回、既有 6 筆 byte 不變、`--confirm` 缺 checkpoint 拒絕、
maintenance hold 的八種拒絕與成功入帳、不得重入 canonical lock（靜態）、未註冊 actor 拒絕、
重建 archive 記錄不得寫入現任 actor、佔位 actor 必須與批次 recovery pair 一致，
以及獨立直譯器的 import-order sentinel。

import-order sentinel 用另一個 python 進程重跑真實 import 順序：
`ai_status` 與 `task_archive` 的 archive 路徑必須來自環境變數指向的臨時 root、
不得落在 checkout 內，且該 root 內既有 snapshot 必須 byte 相同。
這條性質是 import 順序造成的，同一模組內的斷言看不見它。

另外執行：`ruff check`（3 個改動檔，exit 0）、
`check_code_boundaries.py`（1137 files passed，exit 0；本輪未新增檔案，inventory CSV 無變動）。

## 7. 明確未完成／未宣稱

- **未執行 canonical apply。** 第一階段禁止，需 Codex 審核批次與 maintenance hold 後另行安排。
- **歷史未恢復。** 38 筆全部維持缺證據，計畫中的處置是 blocked 佔位，不是完成。
- 未宣稱任何 PR 的 acceptance/CI/runtime/approval 已通過；未補造 Human GO、核准者或歷史 owner/reviewer。
- 未動 Supervisor（無 SIGCONT/restart/kill）、未改 watchdog 或模型設定、未刪鎖、未刪依賴、未派工部署。
- 本收據不是簽章授權或 lease verifier 的 receipt。

## 8. 給下一階段（apply）的注意事項

本批次釘住的是重新規劃當下的 live board revision 與 board bytes。看板持續在動，**因此這份已提交的批次
幾乎必然會在 apply 時因 baseline drift 被拒——那是設計行為，不是缺陷**。正式執行時必須：

1. 先建立並確認 maintenance hold（派工暫停），讓看板靜止；
2. 在 hold 下**重新產生**批次；
3. 由 Codex 審核重新產生的批次，並簽出綁定該批次 SHA-256 的 hold 文件（見 §2 的 hold 契約）；
4. 才執行
   `archive_recovery_apply --batch <新批次> --maintenance-hold <hold.json> --checkpoint <path> --confirm`。

apply 為多檔案操作，不是單一原子交易：archive snapshot 是各自的檔案，board 由外層 canonical
transaction 一次寫入。因此失敗時只會留下 checkpoint 記錄**從磁碟讀回**的實際落地項目，
並明講沒有執行 rollback；`board_persistence_verified` 只有在外層交易提交後的 read-back 成功時才是 `true`。

## 9. 相對 owned_paths 的範圍偏離（已揭露）

派工宣告的 owned_paths 為 `scripts/ai_status.py`、
`scripts/orchestrator/backfill_task_archive_snapshots.py`、
`scripts/orchestrator/test_backfill_task_archive_snapshots.py`、
`.orchestrator/task_archive.py`、
`docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_RECOVERY_20260906.md`。
本分支相對宣告多出四項（以 `origin/dev...HEAD` 全分支計，非只本輪），逐項理由如下；
`.orchestrator/task_archive.py` 未修改（只作為來源閱讀）。

| 路徑 | 理由 | 本輪是否再變動 |
| --- | --- | --- |
| `scripts/orchestrator/test_archive_history_recovery.py` | 驗收要求的焦點測試。放在 `scripts/orchestrator/` 是因為 `config/code-boundaries.yaml` 的 `verification_ownership` 用萬用字元涵蓋 `scripts/orchestrator/test_*.py`；放在 `scripts/` 會被判為 `development_platform` bundle 內的 foreign scope，且需要改動治理 manifest 的顯式白名單。 | 是（+32 則回歸） |
| `scripts/test_ai_status.py` | 被既有測試強制。`ActorCommandMutationGuardTests.test_ai_name_case_table_covers_every_actor_bearing_command` 要求每個新增的 mutating command 都要進 `AI_NAME_CASES` 表，否則既有套件必紅。只加了一列表項。 | 否 |
| `docs/audits/code-boundary-inventory.csv` | 被 `check_code_boundaries.py` 強制：新增任何 .py 都必須重產，否則 CI `orchestrator` job 與 task_finalize 的必過閘會擋。差異為 1 行。 | 否 |
| `docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_RECOVERY_20260906-batch.json` | 驗收要求交付的離線批次本身；`.md` 收據無法承載 38 筆逐項 provenance。與宣告的 `.md` 同目錄同前綴。 | 是（依新 schema 重產） |

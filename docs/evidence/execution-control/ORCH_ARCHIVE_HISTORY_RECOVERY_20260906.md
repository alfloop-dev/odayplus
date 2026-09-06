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

### 2. 本輪修正（回應 Codex 三輪審查，PR #1231）

### 第一輪審查修正（commit ec9ffacc）

- **P1-1 鎖內整批共用嚴格 validator**：新增 `validate_recovery_batch()` / `validate_recovery_entry()`，由 apply 在鎖內對整份文件執行。
- **P1-2 refusals 整批拒絕**：任何 refusal 存在即整批零寫入，預覽與套用皆拒。
- **P1-3 checkpoint 讀回磁碟**：逐檔檢查 snapshot 落盤、SHA-256 與 index 列出狀態；失敗點全數覆蓋。
- **P1-4 baseline 漂移核對**：在鎖內核對 board revision、board bytes、archive index、pinned ref commit、盤點與授權文件 SHA-256。
- **maintenance hold 規範**：收可稽核文件，驗 `dispatch_paused`、未過期、綁定批次 hash，且核准者為 reviewer 且非執行 actor。

### 第二輪審查修正（commit 324942df）

- **P1-1 共用 validator 逐一驗候選必要 provenance**：
  新增 `validate_candidate_provenance()`，逐一驗證候選物件型別（必須為 dict）、`pr_number`（正整數）、`url`、`head_ref`、`merged_at`、`merge_commit`（皆為非空字串）；從欄位直接重新推導缺口。若為 `reconstructed_done`，候選清單不得為空。
- **P1-2 鎖內由 git 重驗 commit subject、交付 identity 與一致性**：
  在鎖內由 git 取得 commit subject 與 commit date，調用 `planner.subject_delivers()` 嚴格確認該 commit 交付的是**本任務**；核對 `local_merge` 各欄位與 git 一致；核對候選宣告的 `merge_commit` 一致；並調用 `planner.find_merge_evidence()` 確保無衝突。
- **P1-3 post-commit verifier 傳遞交易成敗，精確記錄落盤收據**：
  `main()` 的 `finally` 區塊將交易成敗傳遞給 `run_post_commit_verifiers()`。`verify_board_persistence()` 在交易失敗時必標記 `status: partial` 且 `board_persistence_verified: false`。

### 第三輪審查修正（本輪 Antigravity3 實作）

- **P1-1 排除受保護路徑與別名，防止 checkpoint 旁路寫入**：
  新增 `_validate_checkpoint_destination()` 與 `_is_same_or_alias()`，在鎖內且所有 archive/board 寫入前驗證輸出目的地。排除 canonical board（`ai-status.json`）、lock（`ai-status.json.lock`）、log（`ai-activity-log.jsonl`）、current-work（`current-work.md`）、archive index（`ai-task-archive/index.json`）、archive 目錄（`ai-task-archive/tasks`）及其內現存所有 snapshot、batch 檔、hold 檔、以及 baseline 宣告之所有來源與授權文件；並以 `os.path.samefile` 與解析路徑徹底拒絕 symlink 與 hardlink 別名。
  回歸覆蓋：checkpoint 指向倖存 snapshot（byte 不變拒絕）、指向 board、指向 index、指向 archive 目錄，全部零寫入拒絕。
- **P1-2 沿 actor resolver 嚴格驗證每一項 attestation verifier**：
  `validate_recovery_batch(..., for_apply=True)` 與 `command_archive_recovery_apply` 沿用既有 `resolve_actor_reference()` 逐一驗證 acceptance/ci/runtime/approval 各項證言之現任核對者（verifier），必須為已註冊 agent，且明確區分並拒絕 `UNKNOWN-HISTORICAL`；混合有效／未知 verifier 整批拒絕且零寫入。
  回歸覆蓋：UNREGISTERED-REVIEWER verifier 拒絕、UNKNOWN-HISTORICAL verifier 拒絕、混合 verifier 批次拒絕。
- **P1-3 鎖內嚴格比對 board on-disk revision 與狀態讀回**：
  `verify_board_persistence()` 在提交後讀回 `STATUS_FILE`，嚴格比對磁碟上的 `_status_write_revision` 與本交易寫入的 `expected_revision`，並在收據中記錄 `board_revision_expected` 與 `board_revision_on_disk`。純 archive 批次在 `save_state` 未落盤（fault injection/no-op）時必標記 `status: partial` 且 `board_persistence_verified: false`，不以檔案存在代替落盤驗證。
  回歸覆蓋：`save_state` mock fault injection 讀回 revision 不一致時記錄 `partial` 與 `board_persistence_verified: false`；正常 apply 時記錄 `applied` 與 `board_persistence_verified: true`。

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
- checkpoint 路徑指向或 alias 任何受保護路徑（board/lock/log/current-work/index/tasks/batch/hold/provenance）→ 拒絕。
- attestation verifier 未註冊或為 UNKNOWN-HISTORICAL → 拒絕。

歷史 actor 一律記為 `UNKNOWN-HISTORICAL`，與現任恢復 owner/reviewer（`history_recovery.recovery_actors`）
分開兩個欄位；`UNKNOWN-HISTORICAL` 不得作為恢復 owner/reviewer。

## 5. 本輪離線批次結果

指令（唯讀，未帶 `--apply`；recovery 模式本身也拒絕 `--apply`；`--authorization` 現為必填）：

```bash
uv run --frozen --python 3.12 python scripts/orchestrator/backfill_task_archive_snapshots.py \
  --archive-dir /home/lupin/odayplus/ai-task-archive/tasks \
  --repo . --ref origin/dev \
  --board /home/lupin/odayplus/ai-status.json \
  --recovery-inventory /tmp/odayplus-archive-incident.CYD1gq/RECOVERY_DEPENDENCY_CANDIDATES_20260906_1523.json \
  --authorization /tmp/odayplus-archive-incident.CYD1gq/RECOVERY_AUTHORIZATION_ZH_TW.md \
  --batch-out docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_RECOVERY_20260906-batch.json \
  --recovery-owner Antigravity3 --recovery-reviewer Codex
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
| board `_status_write_revision` | `dfae2bd337244419ae9a5df80890c1be` |
| board SHA-256 | `746e826c5f8bbe8422e27516f0d07c10fa96a67b39d49258e7aba48acb5e060f` |
| archive `index.json` SHA-256 | `5e5b59dbf423056aee1dfc57a739a2f740ff2fad6bc7ac87df084413bc02c44a` |
| 盤點 JSON SHA-256 | `116f583e238e309b9f818403fe60cc135870ba1e72f396a8df66e78d7e2b0685` |
| 授權文件 SHA-256 | `134adcb8098397b9a4b74542b9b4903a32cb07a82e0675ef54d9194dbdd2aba5` |
| 批次檔本身 SHA-256 | `e9921a9d9bef6c6d5f24c4c9402334ec6ec3e29c1178afd0a7584084fa2a561c` |
| 既有 snapshot | 6 筆，各自 SHA-256 已逐筆釘入 `baseline.archive_snapshot_digests` |

執行後複驗：`ai-task-archive/index.json` 摘要不變、`tasks/` 仍為 6 筆、board 未被本任務寫入。

## 6. 焦點測試

```bash
PYTHONPATH=scripts/orchestrator:scripts uv run --frozen --python 3.12 pytest \
  scripts/orchestrator/test_archive_history_recovery.py \
  scripts/test_ai_status.py \
  scripts/orchestrator/test_backfill_task_archive_snapshots.py \
  "$ORCH_SCRATCH_DIR/test_review_recovery_regressions.py" \
  --junitxml="$ORCH_SCRATCH_DIR/review-results.xml"
```

結果：**338 passed, 106 subtests passed**，exit code 0，9.45s。
（既有三大套件為 335 passed, 106 subtests passed，加上 reviewer 回歸套件 3 passed，共 338 passed）。
本輪只整批執行這一次；未執行產品全套測試。

覆蓋：證據不足不得合併為 done、blocked 佔位形狀（`non_dispatchable` / `waiting_for` / 依賴保留）、
未知 actor 與 Human GO 補造拒絕、缺 candidate provenance 欄位（`url`/`pr_number`/`head_ref`/`merged_at`/`merge_commit`/型別/空物件）逐一拒絕、
同 ref 另一任務 merge commit 祖先拒絕、local_merge 篡改拒絕、candidate merge commit mismatch 拒絕、
baseline 六類漂移各自拒絕（board revision、同 revision 不同 board bytes、archive 摘要、pinned ref、盤點、授權文件）、
批次被竄改的形狀整批拒絕、混合有效／拒絕批次整批拒絕、
idempotency（第二次 apply 必拒）、partial failure 的各失敗點留 checkpoint 且不宣稱 rollback、
純 archive 與混合批次在 `sync_all` 失敗或未落盤時正確記錄 `partial` 且 `board_persistence_verified: false`、
外層 board 未落盤時的 post-commit 讀回與 revision 嚴格比對、既有 6 筆 byte 不變、`--confirm` 缺 checkpoint 拒絕、
checkpoint 輸出目的地防護（禁止覆寫倖存 snapshot、board、index、archive 目錄及別名）、
maintenance hold 的八種拒絕與成功入帳、不得重入 canonical lock（靜態）、未註冊 actor 拒絕、
attestation verifier 逐項驗證與 UNREGISTERED-REVIEWER 拒絕、
重建 archive 記錄不得寫入現任 actor、佔位 actor 必須與批次 recovery pair 一致，
以及獨立直譯器的 import-order sentinel。

另外執行：`ruff check`（exit 0）、`python3 delivery_toolchain/governance/check_code_boundaries.py`（1137 files passed，exit 0）。

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
並明講沒有執行 rollback；`board_persistence_verified` 只有在外層交易提交後的 read-back 成功且 revision 完全一致時才是 `true`。

## 9. 相對 owned_paths 的範圍偏離（已揭露）

派工宣告的 owned_paths 為 `scripts/ai_status.py`、
`scripts/orchestrator/backfill_task_archive_snapshots.py`、
`scripts/orchestrator/test_backfill_task_archive_snapshots.py`、
`.orchestrator/task_archive.py`、
`docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_RECOVERY_20260906.md`。
本分支相對宣告多出五項（以 `origin/dev...HEAD` 全分支計，非只本輪），逐項理由如下；
`.orchestrator/task_archive.py` 未修改（只作為來源閱讀）。

| 路徑 | 理由 | 本輪是否再變動 |
| --- | --- | --- |
| `scripts/orchestrator/test_archive_history_recovery.py` | 驗收要求的焦點測試。放在 `scripts/orchestrator/` 是因為 `config/code-boundaries.yaml` 的 `verification_ownership` 用萬用字元涵蓋 `scripts/orchestrator/test_*.py`；放在 `scripts/` 會被判為 `development_platform` bundle 內的 foreign scope，且需要改動治理 manifest 的顯式白名單。 | 是（+7 則 ReviewRegressions 回歸，共 81 則測試） |
| `scripts/test_ai_status.py` | 被既有測試強制。`ActorCommandMutationGuardTests.test_ai_name_case_table_covers_every_actor_bearing_command` 要求每個新增的 mutating command 都要進 `AI_NAME_CASES` 表，否則既有套件必紅。只加了一列表項。 | 否 |
| `delivery_toolchain/git/task_finalize.sh` | 依 Codex 審查意見與驗收要求，將 PR 自動產生範本本地化為繁體中文。 | 是 |
| `docs/audits/code-boundary-inventory.csv` | 被 `check_code_boundaries.py` 強制：新增任何 .py 都必須重產，否則 CI `orchestrator` job 與 task_finalize 的必過閘會擋。差異為 1 行。 | 否 |
| `docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_RECOVERY_20260906-batch.json` | 驗收要求交付的離線批次本身；`.md` 收據無法承載 38 筆逐項 provenance。與宣告的 `.md` 同目錄同前綴。 | 是（依新 validator 與新 recovery pair 重產） |

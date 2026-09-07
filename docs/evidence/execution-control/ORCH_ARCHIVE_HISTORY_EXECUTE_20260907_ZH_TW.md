# ORCH-ARCHIVE-HISTORY-EXECUTE-003 收據：授權來源接續、plan-only 批次與 apply 阻塞檢查點

- 產出者：Claude2（owner）／審查者：Codex（reviewer）
- 量測時間：2026-09-07T15:48Z – 15:59Z（所有時間為實際命令時間）
- 交付分支：`task/ORCH-ARCHIVE-HISTORY-EXECUTE-003`（base `origin/dev` tip
  `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12` = PR #1234 的 merge commit）

> **本輪沒有執行 `archive_recovery_apply`——連 PLAN ONLY 都沒有。歷史尚未回填。**
> 沒有建立、簽署或宣稱任何 maintenance hold；沒有停止、恢復或送任何訊號給 Supervisor；
> 沒有改 watchdog／cron／live config；沒有手寫 `ai-status.json`、`ai-task-archive/` 或 `index.json`；
> 沒有修改任何已合併的證據檔案或 `scripts/orchestrator/backfill_task_archive_snapshots.py`。
> 本輪唯一的 canonical 寫入是本任務自身的狀態轉換（由 supervisor dispatch 觸發的 `start`，以及本輪結束時的送審）。
>
> **依驗收「沒有 apply 就不標本任務 done」，本任務不標 done。** 本文件是驗收要求的 blocked checkpoint。

本輪相對 PR #1234（ORCH-ARCHIVE-HISTORY-RESTORE-002）推進了三件事，並量出兩個前一輪沒有量到的缺陷：

1. **解除 B-AUTH**（PR #1234 的第一順位結構阻塞）——改用 Codex 如實轉錄的新來源當授權文件，
   不由執行者補寫（§1）。
2. **產出兩份 plan-only 批次**並通過 live writer 會重跑的同一支 admission 驗證器（§3、§5）。
3. **在 live runtime 上重量派工解除效果**：情境 A 是 0，情境 B 恰好 1 筆（§6）。

新量到的缺陷：

- **D1（本輪新發現，會靜默降級）** PR #1234 §7 第 3 步那條命令逐字照跑，得到的是
  **38 筆全 blocked（情境 A）**，不是它自己主張的 23＋15。原因是 `--attestations` 要吃扁平的
  「task id → attestation」物件，而交付的草案把它包在 `attestations` 鍵底下；planner 讀不到就
  **不報錯、直接當成沒有 attestation**（§4）。
- **D2** canonical checkout `/home/lupin/odayplus` 的本地 `dev` 停在 `9054479a`，與 `origin/dev`
  **雙向都不是祖先**，且工作樹裡沒有 PR #1234 的任何交付檔。§7 假設的「從有這些檔案的 checkout 重新規劃」
  在那個 checkout 上做不到（§8）。

---

## 1. 授權來源：B-AUTH 已解除，但只解除到「可以規劃批次」為止

驗收指定先讀
`/home/lupin/odayplus-handoffs/odp-execution-wave-2026-09-07/USER_EXECUTION_SCOPE.md`。

| 項目 | 實測值 |
| --- | --- |
| 路徑 | `/home/lupin/odayplus-handoffs/odp-execution-wave-2026-09-07/USER_EXECUTION_SCOPE.md` |
| SHA-256 | `d7a34fa1fbed837d9f15d2580709689b1178f11f2d1f019383082eebf11deea3` |
| 記錄者（文件自述） | Codex，2026-09-07 |
| 是否由本 worker 撰寫或修改 | **否**（本輪只讀取與雜湊） |
| 是否在 `/tmp` | 否（前一輪遺失的兩份原始檔都只存在於已消失的 `/tmp/odayplus-archive-incident.*`） |

文件自述「這是本對話新紀錄，不是遺失的原始授權檔，不署名 Human/Ops」，並授權
「沿既有任務／Supervisor／canonical writer 核實遺失歷史，真實證據足夠並經獨立 reviewer 核准 exact batch 後，
在有效維護窗口執行回填」。

**scope 判定（驗收要求「若其 scope 不足指出精確所需內容，不自行擴張授權」）：**

- **足夠**作為 planner `--authorization` 釘住的授權文件：它正是對「受控歷史回填」這件事的範圍授權，
  planner 對此參數的要求也只有「存在 + 釘 SHA-256 + apply 期重新雜湊」（`build_recovery_batch()`、
  `capture_recovery_baseline()`）。因此**不再需要為了一份已消失的 `/tmp` 檔案循環等待**。
- **明文不足**以充當後兩者，文件自己就這麼寫：「不代表維護窗口已成立或 batch 已獲批准」。
  仍缺、且**本 worker 無權補**的精確內容是：
  1. **有效維護窗口**——由前景協調者建立（停派工、排除 watchdog／worker／其他 writer、Supervisor 真正退出）；
  2. **綁定當次 batch SHA-256 的 maintenance hold**——由 **Codex** 出具，
     `batch_approval.reviewer` 必須是 Codex 且不得等於執行 apply 的 actor（Claude2）。

本輪**沒有**把這份文件重寫、複製成「授權書」，也**沒有**用它宣稱窗口或批准存在。

---

## 2. live 控制面實測（不以 dashboard 推論）

| 控制者 | 15:48Z 實測 | 15:57Z 複量 |
| --- | --- | --- |
| Supervisor | PID `6522`，state `Ss`，ppid 1，啟動 14:52:38 | **已換 PID `59402`**，state `Ss`，ppid 1，啟動 **15:55:52**；pidfile 一致 |
| runtime-current | `oday-plus-supervisor-runtime-19167c10b8c6` | **已前推**為 `oday-plus-supervisor-runtime-0a8befbfc0a2`（HEAD `0a8befbfc0a2fb10e4c4a61b25880bbe834b81ef`，含 PR #1235） |
| watchdog（cron） | 維持暫停（唯一一行是被註解掉的 `# PAUSED ODP_ARCHIVE_INCIDENT_20260906_0643: …`） | 同左 |
| live config | `ready_dispatcher.enabled: true`、`dependency_done_statuses: ["done"]`、`capacity_controller.enabled: true`、`sidecars.enabled: true`（`max_new_per_wave: 3`／`max_active: 4`／`require_chair_approval: true`） | 同左 |

**Supervisor 在本輪執行期間自己重啟並換了 runtime。派工是開著的、看板正在漂、維護窗口不成立。**

### 2.1 baseline 漂移速率（決定「批次必須在窗口內重建」的那個事實）

`ai-status.json` 的 `_status_write_revision` 實測序列：

| 時間 | board revision |
| --- | --- |
| 15:48:5xZ | `b667a702c5da492f849dad0521162ce8` |
| 15:49:18Z | `34e02ca4e1b943a3958bd22adc5b9cfa` |
| 15:50:15Z | `4675b33b036e4092921fda1923c5925a` |
| 15:55:1xZ | `83a1b54748804869bb96789a7cdaf213` |
| 15:57:45Z | `c12ef86277934c1ba51ae1223aa9ac42` |

約 30–60 秒一次。`_archive_recovery_baseline_drift()` 會比對 `board_revision`**和** `board_sha256`，
所以**任何在窗口外規劃的批次，壽命都是幾十秒**。本輪交付的批次因此只供**形狀與內容審查**，
不是拿去 apply 的那一顆（§9）。

### 2.2 量測工具與 live runtime 的同一性

本輪的依賴／派工判定不是自寫的第二套判準，而是直接呼叫既有模組。逐檔比對本 worktree
（base `596b9c9a`）與 **當時的** runtime-current `…-19167c10b8c6`：

| 檔案 | SHA-256（前 16） | 對 `…-19167c10b8c6` | 對 `…-0a8befbfc0a2`（新） |
| --- | --- | --- | --- |
| `.orchestrator/supervisor.py` | `9fa0fb12178b1260` | SAME | SAME |
| `.orchestrator/task_archive.py` | `bdfaf20f0ead4d47` | SAME | SAME |
| `.orchestrator/capacity_controller.py` | `660c2595b087aabc` | SAME | SAME |
| `.orchestrator/dispatch_engine.py` | `73f77d86d240a3b8` | SAME | **DIFF**（runtime `d205e843093ffe3a`） |
| `scripts/ai_status.py` | `2f6ad4515d98c394` | SAME | SAME |
| `scripts/orchestrator/backfill_task_archive_snapshots.py` | `a85a957294ddbd96` | SAME | SAME |
| `.orchestrator/sidecar_catalog.json` | `d666af0ef8da432d` | SAME | SAME |

runtime 在本輪中途前推後 `dispatch_engine.py` 出現差異（PR #1235 只動 merge routing 的
`_task_repository_slug()` / `route_approved_pr_to_merge()`，未動 `dispatch_priority_for_task()`）。
**為避免以過期程式下結論，§6 的分析在新 runtime 上完整重跑一次**，結論相同（§6.2）。

---

## 3. 交付的兩份 plan-only 批次

兩份都用**已合併的 planner**（未修改）產生，`--apply` 在 recovery 模式下被工具自身拒絕，
因此**沒有任何 canonical 寫入**。

| 批次 | SHA-256 | 產生時間 | 形狀 | refusals |
| --- | --- | --- | --- | --- |
| `recovery_batch_20260907_scenarioA_as_documented.json` | `3c7f5928db956bb6fd49840a09ab9e27de85fa5f550b15773327ec1988ab71c5` | 2026-09-07T15:49:18Z | **0 done + 38 blocked** | 0 |
| `recovery_batch_20260907_scenarioB.json` | `639059236692d8b0fc32d9b6cb1f0210f84068c023c0fdcf90c7760875f674a6` | 2026-09-07T15:50:15Z | **23 done + 15 blocked** | 0 |

兩份的共同輸入（皆為 PR #1234 已合併的交付物，本輪未修改其位元）：

| 輸入 | SHA-256 |
| --- | --- |
| `…/ORCH_ARCHIVE_HISTORY_RESTORE_20260907/recovery_inventory_rebuilt_20260907.json` | `54d2386b29a9b601d13e74e55a2f1702550421546c1e992de295c51fe36d12ca` |
| `…/ORCH_ARCHIVE_HISTORY_RESTORE_20260907/attestations_draft_20260907.json` | `f091830bcef6ea64770630e4c0f61d6bd8d71179b76c510a431b0ff3c66692c0` |
| `USER_EXECUTION_SCOPE.md`（`--authorization`） | `d7a34fa1fbed837d9f15d2580709689b1178f11f2d1f019383082eebf11deea3` |

`--recovery-owner Claude2 --recovery-reviewer Codex`（本輪的真實 owner／reviewer）；
`--repo /home/lupin/odayplus --ref origin/dev`（`ref_commit` 釘 `596b9c9a…`）。

**歷史 actor 維持 unknown**：23 筆 done 記錄的 `owner`／`reviewer` 都是 `UNKNOWN-HISTORICAL`，
與 `recovery_actors`（Claude2／Codex）分開兩個鍵，沒有補造任何歷史 owner／reviewer／approver／Human GO。

---

## 4. D1：PR #1234 §7 的命令逐字照跑會靜默降級成情境 A

這是本輪最需要 Codex 先確認的一項，因為它會讓「照著交接文件做」的人得到一個**看似成功、
實際上派工解除效果為 0** 的批次。

- `run_recovery_plan()` 把 `--attestations` 的 JSON **原樣**當成 `attestations` 傳下去；
  `build_recovery_batch()` 只做 `attestations.get(task_id)`。
- PR #1234 交付的 `attestations_draft_20260907.json` 頂層是
  `{type, schema_note, draft, approved_by_reviewer, produced_by, produced_at,
  reviewer_to_adjudicate, issuance_rule, not_claimed, counts, attestations}`
  ——逐筆 attestation 在 **`attestations` 鍵底下**。
- 於是 `attestations.get("ODP-HZ006-…")` 一律回 `None`，`_attestation_gaps()` 判定四項全缺，
  38 筆全部落到 `active_blocked_placeholder`。**planner 不報錯、不警告。**

實測（15:49:18Z，逐字照 §7 第 3 步）：

```
PLAN ONLY: 38 entr(ies) planned (0 reconstructed done, 38 blocked placeholders), 0 refused.
```

PR #1234 §4.4 拿到 23 是因為它**直接呼叫 `grade_recovery_entry(..., attestation=<內層 map 的單筆>)`**，
繞過了 CLI 這一層。兩者不衝突，但**交接文件給的是 CLI 命令**。

**本輪的處置（不改工具、不改已合併證據）：** 交付
`attestations_planner_input_20260907.json`——把已合併草案的 `attestations` 內層 map
**原樣取出**，不增不減不改值：

- 逐鍵 deep-equal 比對通過，38 鍵；
- 掃過 planner 的 `FABRICATION_FIELDS`（`historical_owner`／`historical_reviewer`／`human_go`／
  `approver`／`review_approval`），**0 筆帶有**；
- SHA-256 `c84822169f6b824dfdcd0315de57338f416536e6da678bbea4fed047d93bb5e3`。

**權威版本仍是已合併的草案**；這份只是同一份內容的 planner 可消費形狀。
Codex 逐條裁定時請對草案裁定，裁定後再用同樣方式取出內層 map 餵給 planner。

> 本輪**不**修改 planner 去自動解包。那會在復原進行中改動已合併工具的契約，
> 且不在本任務的 owned paths 內。是否要補這個解包（或讓 planner 對「認得的包裝鍵」報錯而非靜默忽略），
> 留給 Codex 決定要不要開後續 task。

---

## 5. 批次的預先 admission 檢查（不需要 hold、未取 canonical lock）

live writer 在 apply 期會**重跑 planner 自己的 `validate_recovery_batch(batch, for_apply=True)`**
（`command_archive_recovery_apply()`：「The planner's validator is the admission gate, re-run here」）。
本輪離線呼叫**同一支 byte-identical 函式**，並自行對 pinned ref 重驗 23 筆 merge：

| 檢查 | 情境 A | 情境 B |
| --- | --- | --- |
| `validate_recovery_batch(for_apply=True)` problems | **0** | **0** |
| 23 筆 done 的 merge commit 是 `596b9c9a…` 的祖先 | n/a（0 筆） | **23/23 是** |
| 23 筆的 commit subject 與記錄一致 | n/a | **23/23 一致** |
| 與 live board 現有 task id 衝突 | **0** | **0** |
| 與 live archive 現有 snapshot 衝突 | **0** | **0** |

逐項原始輸出見 `preadmission_check_20260907.json`。

**這不是 apply、也不是 PLAN ONLY。** 它證明的只有一件事：**批次的形狀與內容會被 writer 接受**；
真正會擋下它的是 hold 與 baseline 漂移，而那兩者都不在本 worker 的權限內（§7）。

---

## 6. 派工解除效果：情境 A 是 0，情境 B 恰好 1 筆

判準不是自寫的，是 supervisor 自己的
`canonical_dispatchable_task_ids()`（它自述是 `dispatch_priority_for_task` 的投影，
不是第二套 eligibility 實作）、`dependencies_satisfied()` 與
`blocked_task_auto_recovery_eligible()`，輸入是 live board、live archive、live config
（`ORCH_CONFIG_PATH=/home/lupin/odayplus/.orchestrator/config.json`）。純函式求值，零寫入。

### 6.1 結果（新 runtime `…-0a8befbfc0a2`，2026-09-07T15:58:27Z，board rev `c12ef862…`，26 筆 active）

| 情境 | 可排程任務 | 依賴新滿足 | **新變成可排程** |
| --- | --- | --- | --- |
| BEFORE（今日） | `ODP-GCP-STAGING-EXECUTION-PREFLIGHT-002`, `ORCH-ARCHIVE-HISTORY-EXECUTE-003` | — | — |
| **A：38 筆全 blocked 佔位** | 同 BEFORE | **0** | **0** |
| **B：23 done + 15 blocked** | BEFORE ＋ `ODP-DRIFT-SECURITY-VERIFY-003` | 2 | **1** |
| C：上界對照（38 全 done，**本輪不主張**） | 同 B | 6 | **1** |

**情境 A 對工程派送的解除效果仍然是 0**，原因是結構性的：`dependency_done_statuses` 是 `["done"]`，
`task_satisfies_dependency()` 要求 `status == done` 且非 superseded，
**blocked 佔位對依賴閘而言等價於 missing**。這一項與 PR #1234 §5 的結論一致，本輪在今日的板上重量成立。

**情境 C 的上界只比 B 多解開依賴、不多解開派送**：C 多出的 5 筆依賴滿足者裡，
`HUMAN-OSS-LEGAL-APPROVAL-001` 是人類授權任務（被 `task_is_human_gate()` 排除），
其餘或為 blocked、或 `non_dispatchable`。也就是說，**把 15 筆缺證據的也一併標 done，換不到任何額外的可派送工作**——
禁止全 38 done 這條驗收要求，在派工效益上也沒有任何代價。

### 6.2 兩次量測的差異揭露

以 base `596b9c9a` 的模組在 15:53:44Z 先量過一次（`dependency_release_analysis_20260907_execute.json`），
BEFORE 的可排程數是 3；15:58:27Z 以新 runtime 重量是 2。差別**不是**程式差異造成的：
`ODP-DEV-BUILD-ARTIFACT-HANDOFF-003` 在這 5 分鐘內被派走了。
兩次量測的**新增可排程集合完全相同**（A：空集；B、C：`{ODP-DRIFT-SECURITY-VERIFY-003}`）。
兩份 JSON 都交付，未刪除較早那份。

### 6.3 NLTK 最終驗證：可否派送（驗收要求明列）

- 目標任務：**`ODP-DRIFT-SECURITY-VERIFY-003`**（live board：status `todo`、owner `Antigravity4`、
  reviewer `Codex`、`non_dispatchable: false`、`blocked_reason: null`）。
- 依賴兩筆：`ODP-DRIFT-DEP-REMOVE-002`（**缺失中，本批次情境 B 判為 reconstructable_done**）與
  `ODP-CI-DEPENDENCY-AUDIT-BOUNDARY-001`（**已在 archive**）。
- **判定：目前不可派送；在情境 B 的批次成功 apply 之後可派送。** 這是本次復原唯一真正解開的工程工作。
  在情境 A 之下**仍然不可派送**。
- 附帶實測（供該任務的執行者參考，非本任務範圍）：`origin/dev` `596b9c9a` 的
  `pyproject.toml` 與 `uv.lock` **各 0 行提及 nltk**——nltk 移除本身確實已落地。
  本輪**不**宣稱該安全驗證任務的結論，也**不**代它跑任何驗證。

### 6.4 AVM：獨立保留，歷史修復不解除 review churn

`ODP-AVM-QUALITY-NULLABLE-001` 在 live board 是 `status: blocked`、`waiting_for: Human/Ops`、
唯一依賴 `ODP-HZ006-MERGE-SPLIT-IMPLEMENTATION-001`。

- 情境 B 下它的 `dependencies_satisfied` **變成 True**（HZ006 被重建為 done）；
- 但 `blocked_task_auto_recovery_eligible()` 在**四個情境下全部是 False**，它**仍然 blocked、仍然不會被派送**。

也就是說：**依賴閘開了，人類閘沒開。** AVM 線的下一個閘是既有的 Human/Ops-only
`command_approve_continuation`（只接受 Human/Ops actor 的一次性 nonce），不是缺失的歷史。
**本輪不碰 AVM，也不宣稱 AVM 的 review-churn continuation 被解除。**

### 6.5 B6 複量：15 筆佔位會成為 capacity sidecar 的合格父任務

以 live `sidecar_catalog.json`（`d666af0e…`，與 runtime 相同）逐條比對：4 個模板中**只有**
`blocked_task_diagnostics` 以 `parent_statuses: ["blocked"]` 選取父任務，其餘三個沒有任何 parent selector
（控制器第 623 行對這種模板 `continue`）。

`capacity_controller.py` 第 629 行把 `blocked_reason + next + waiting_for` 串起小寫後比對
`HARD_GATE_MARKERS = {"human gate", "manual approval", "credential", "license", "production proof", "legal"}`。
planner 佔位的實際 prose 是「等待人工裁決：補齊 acceptance/ci/runtime/approval 證據，或確認此 ID 以 blocked 結案。 Human/Ops」
——**六個 marker 一個都不命中**（`"human gate"` ≠ `"human/ops"`，且 marker 全是英文、prose 是中文）。

| 批次形狀 | blocked 佔位數 | 命中 hard gate marker | **sidecar 合格父任務** |
| --- | --- | --- | --- |
| 情境 A | 38 | 0 | **38** |
| 情境 B | 15 | 0 | **15** |

規模上限由 `max_new_per_wave: 3`／`max_active: 4`／`require_chair_approval: true` 拘束。
佐證：現有 14 筆 archive snapshot 裡**已經有 7 筆是 `*-SIDECAR-*`**，這條路徑是活的、不是理論風險。
**本輪不改 config、不改 marker 集合、不改 catalog**，處置交 Codex 在開窗口前決定。

---

## 7. 仍然成立的阻塞（每一項本輪都自己量過）

### B-WINDOW（結構性）維護窗口不成立

Supervisor 存活且在本輪期間自行重啟（PID 6522 → 59402）、`ready_dispatcher.enabled: true`、
board 每 30–60 秒改寫一次。**驗收明文禁止 worker 自行簽 hold 或停止／恢復 Supervisor**，
本輪因此未對任何行程送出任何訊號。

### B-HOLD（結構性）沒有 hold，連 PLAN ONLY 都跑不起來

`command_archive_recovery_apply()` 的順序是：解析參數 → 讀批次 → checkpoint 目的地檢查 →
**`validate_recovery_batch`** → actor 解析 → **`_archive_recovery_hold(...)`** → **baseline drift** →
id 衝突 → evidence drift → **然後才**判斷 `--confirm`。
`_archive_recovery_hold()` 要求一份文件同時滿足：`type == "task_history_recovery_maintenance_hold"`、
`hold_id`／`declared_by`／`declared_at`／`expires_at`／`scope` 皆非空、`dispatch_paused is True`、
`batch_sha256` 逐字元等於當次批次、未過期、`declared_at` 不在未來，且
`batch_approval` 的 `reviewer == Codex`、`approver != actor`、`approved_batch_sha256 == batch_sha256`、
`approved_at`／`source` 非空。

**因此「先預演看看再決定要不要開窗口」在結構上不可能。** 這與 PR #1234 的 B3 相同，本輪在
byte-identical 的 live writer 上再確認一次。

### B-DRIFT（機械性）批次壽命只有幾十秒

見 §2.1。窗口成立後此項自然消失。

### B5（P2，仍成立）跨 repo 佔位不帶 `repository` 欄位

情境 B 下兩筆跨 repo（`DPF-EMGI-LIVE-ROLLOUT-001`、`XR-EXT-OSS-FINAL-AUDIT-001`）都維持 blocked 佔位，
不會進 archive 終態，對本批次不致命。本輪**不**擅自擴充 planner 的 record schema。

---

## 8. D2：canonical checkout 無法直接充當窗口內的規劃 checkout

| 項目 | 實測 |
| --- | --- |
| `/home/lupin/odayplus` 的 `HEAD`／分支 | `9054479a776dce41e8a144c12032a85471a91f1b`／`dev` |
| 該 checkout 的 `origin/dev` | `596b9c9a…`（本輪規劃時）→ 之後前推至 `0a8befbf…` |
| `9054479a` 與 `596b9c9a` 的祖先關係 | **雙向都不是祖先**（已分岔） |
| 該工作樹是否有 PR #1234 的交付檔 | **否**（`docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_RESTORE_20260907/` 不存在） |

本輪因此把 `--repo` 指向 `/home/lupin/odayplus`（它的 `origin/dev` ref 可解析、且路徑穩定，
apply 期的 `_archive_recovery_evidence_drift()` 會用 `baseline["repo"]` 重驗 merge），
但 `--recovery-inventory` 只能指向**本 worktree**的路徑
（`/tmp/pantheon-worker-worktrees/pantheon/orch-archive-history-execute-003/…`）。

**這是交付批次不可直接 apply 的第二個理由**：`_archive_recovery_baseline_drift()` 會重新雜湊
`inventory_path`，而 worktree 路徑不保證在窗口時仍存在。
**窗口內重新規劃時，請把 inventory／attestations 放到穩定路徑**（本 PR 合併後，
`origin/dev` 的任一 checkout 都會有這些檔案）。

---

## 9. blocked checkpoint（驗收要求的「具體 batch 與下一條命令」）

機讀版：`RECOVERY_APPLY_CHECKPOINT_20260907.json`。摘要：

1. **第 1 步（前景協調者，本 worker 不執行）** 建立有效維護窗口：停派工、排除 watchdog／worker／
   其他 writer、Supervisor 真正退出。
2. **第 2 步（窗口內）** 以 §3 的同一組輸入重新規劃批次，`--authorization` 指向
   `USER_EXECUTION_SCOPE.md`（`d7a34fa1…`），`--recovery-owner Claude2 --recovery-reviewer Codex`，
   然後 `sha256sum` 取得**當次**的 batch hash。
   `--attestations` 用 **Codex 逐條裁定後**的版本，形狀依 §4（扁平 map）。
3. **第 3 步（Codex，非執行者）** 對第 2 步量到的 exact hash 簽出 hold。欄位需求見
   `maintenance_hold_template_NOT_A_HOLD.json`——**那是欄位樣板，不是 hold**：它的 `type` 刻意寫成
   `task_history_recovery_maintenance_hold__TEMPLATE_NOT_A_HOLD`，live writer 會直接拒絕，
   確保它不可能被誤當成已簽署的 hold。
4. **第 4 步（窗口內，PLAN ONLY）**
   `AI_NAME=Claude2 "$PANTHEON_STATUS_ROOT/scripts/ai-status.sh" archive_recovery_apply --batch <新批次> --maintenance-hold <hold.json>`
5. **第 5 步（窗口內，`--confirm`）** 同上再加
   `--checkpoint /home/lupin/odayplus/.orchestrator/recovery-receipts/history-recovery-checkpoint-<UTC>.json --confirm`。
   基準漂移或 partial failure**立即停在實際結果**，保留 checkpoint 與 hold，不繞 CAS、
   不手寫 archive／board／index、不宣稱 rollback、不在窗口外重試。

**待 Codex 裁定（第 2 步之前）：** §4 的 D1 處置是否接受；attestation 出具規則逐條裁定
（尤其 `runtime` 那條——若否決，23 筆會全數退回 blocked 佔位）；批次形狀採 B 或 A（§6 已量出 A 是 0 解除）；
§6.5 的 sidecar 曝險處置。

---

## 10. 原 snapshots 未改的證明

`archive_snapshots_untouched_20260907_open.json` 記下本輪開始時（15:55Z）live archive 的
**14 筆 snapshot 逐筆 SHA-256** 與 `index.json` 的 SHA-256（`784b877f3163…`）；
`…_close.json` 是本輪結束時（16:01Z）的同一組量測（15 筆、index `70806f76ae03…`）。

逐筆比對結果（機讀於 `_close.json` 的 `delta_vs_open`）：

| 項目 | 結果 |
| --- | --- |
| 既有 snapshot **內容被改** | **0 筆** |
| 既有 snapshot **被刪** | **0 筆** |
| 新增 snapshot | **1 筆：`ODP-ORCH-REPO-SLUG-MERGE-ROUTING-001`**（`status: done`、owner `Claude`、`archived_at: 2026-09-07T16:01:14Z`） |

**不宣稱「archive 完全沒動」**——它動了，但動的不是本任務：新增的那筆正是本輪期間由 live fleet
正常收尾的 PR #1235（同一顆 merge 也讓 runtime 前推至 `…-0a8befbfc0a2`，見 §2）。
本輪要證明的是**既有 snapshot 一個位元都沒被碰**（changed／removed 皆為 0），
這是必然的，因為 `archive_recovery_apply` 從未執行。
`index.json` 的雜湊改變也是同一次正常收尾造成的（`rebuild_archive_index()` 會 glob 整個目錄）。

這同時是 §2.1 的旁證：**在窗口外，archive 與 board 都會在你規劃批次的當下繼續移動。**

---

## 11. 明確未完成／未宣稱

- **未執行 `archive_recovery_apply`（連 PLAN ONLY 都沒有）。歷史未回填。38 筆仍不在 board 也不在 archive。**
- **未建立、未簽署、未宣稱任何 maintenance hold。** §5 的離線 admission 檢查不是 hold，也不是 apply。
- 未停止／重啟／SIGCONT／送任何訊號給 Supervisor 或任何行程；未改 watchdog／cron／systemd／live config。
- 未修改任何已合併的證據檔案，未修改 `scripts/orchestrator/backfill_task_archive_snapshots.py`
  或任何 `.py`／workflow／治理 manifest（本 PR 只新增 docs 證據，因此不需重產
  `docs/audits/code-boundary-inventory.csv`）。
- 未補造歷史 owner／reviewer／approver／Human GO；23 筆 done 記錄的歷史 actor 仍是 `UNKNOWN-HISTORICAL`。
- 未撰寫、未修改、未複製 `USER_EXECUTION_SCOPE.md`；未代 Codex 裁定任何 attestation
  （`attestations_planner_input_20260907.json` 的 `draft`／`approved_by_reviewer` 語意沿用已合併草案：**未經批准**）。
- 未宣稱 AVM 的 review churn 被解除；未宣稱 `ODP-DRIFT-SECURITY-VERIFY-003` 的驗證結論；
  未跑產品整套 CI；未 dispatch 任何 release；未啟用任何 provider。
- 本輪跑過的可執行路徑只有：已合併 planner 的 plan-only 模式（`--apply` 被工具自身拒絕）、
  planner 的 `validate_recovery_batch`、supervisor／capacity_controller 的純函式求值、
  `gh api` 唯讀查詢、本地 `git` 唯讀查詢。

---

## 12. 交付檔案

| 路徑（皆在 `docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_EXECUTE_20260907/`） | 內容 |
| --- | --- |
| `recovery_batch_20260907_scenarioA_as_documented.json` | 逐字照 PR #1234 §7 命令產生的批次（38 全 blocked），D1 的證據 |
| `recovery_batch_20260907_scenarioB.json` | **供審查的 plan-only 批次**（23 done + 15 blocked） |
| `attestations_planner_input_20260907.json` | 已合併草案 `attestations` 內層 map 的原樣取出（planner 可消費形狀） |
| `preadmission_check_20260907.json` | 兩份批次的 `validate_recovery_batch(for_apply=True)` 結果、23 筆 merge 重驗、live id 衝突檢查 |
| `dependency_release_analysis_20260907_execute.json` | 四情境派工解除分析（base `596b9c9a` 模組，15:53:44Z） |
| `dependency_release_analysis_20260907_execute_live_runtime.json` | 同上，改用 live runtime `…-0a8befbfc0a2` 模組重跑（15:58:27Z） |
| `priority_two_reverification_20260907.txt` | REMOVE-002／HZ006 今日對精確 head 重量的 check-runs 與 task-review-gate 原始輸出 |
| `priority_two_local_recheck_20260907.txt` | 兩筆 merge 的本地 containment、被揭露 artifact 的存在性複查、nltk 清單掃描 |
| `nltk_followup_recheck_20260907.txt` | `ODP-DRIFT-SECURITY-VERIFY-003` 的 live board 狀態與 nltk manifest 掃描 |
| `RECOVERY_APPLY_CHECKPOINT_20260907.json` | 機讀 blocked checkpoint（批次、阻塞、下一條命令） |
| `maintenance_hold_template_NOT_A_HOLD.json` | hold 欄位樣板，`type` 刻意不合法，**不是** hold |
| `archive_snapshots_untouched_20260907_open.json` / `_close.json` | 本輪開始／結束時 live archive 的逐筆指紋 |

## 附錄 A：§6 分析腳本（全文，供逐字重跑）

腳本刻意**不**以 `.py` 形式進版控，以免動到 `docs/audits/code-boundary-inventory.csv`。
逐字存成檔案後以 `uv run --frozen --python 3.12 python <file>` 執行即可重現 §6。
把 `sys.path` 的前兩行指向 base checkout 或 live runtime，即分別得到 §6.1 與 §6.2 的兩份輸出。

```python
# 見 dependency_release_analysis_20260907_execute*.json 的 inputs 區塊，
# 內含 board_sha256／board_revision／config_path／code_checkout／dependency_done_statuses，
# 足以逐項核對本節每一個數字的輸入。
import json, os, sys, hashlib, copy, datetime
from pathlib import Path
os.environ['ORCH_STATUS_ROOT'] = '/home/lupin/odayplus'
os.environ['PANTHEON_STATUS_ROOT'] = '/home/lupin/odayplus'
os.environ['ORCH_CONFIG_PATH'] = '/home/lupin/odayplus/.orchestrator/config.json'
os.environ['PANTHEON_CONFIG_PATH'] = '/home/lupin/odayplus/.orchestrator/config.json'
CODE = Path('/home/lupin/oday-plus-supervisor-runtime-0a8befbfc0a2')   # 或 base checkout
sys.path.insert(0, str(CODE / '.orchestrator'))
sys.path.insert(0, str(CODE / 'scripts'))
import supervisor

config = supervisor.load_config()
board = json.loads(Path('/home/lupin/odayplus/ai-status.json').read_text(encoding='utf-8'))
live_tasks = [t for t in board.get('tasks', []) if isinstance(t, dict)]
D = Path('docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_EXECUTE_20260907')
batchA = json.loads((D / 'recovery_batch_20260907_scenarioA_as_documented.json').read_text(encoding='utf-8'))
batchB = json.loads((D / 'recovery_batch_20260907_scenarioB.json').read_text(encoding='utf-8'))

def records(batch):
    return [copy.deepcopy(e['record']) for e in batch['entries']]

def as_all_done(batch):                      # 情境 C，本輪不主張
    out = []
    for e in batch['entries']:
        r = copy.deepcopy(e['record'])
        r['status'] = 'done'; r['terminal_outcome'] = 'completed'; r['non_dispatchable'] = False
        out.append(r)
    return out

scenarios = {
    'BEFORE': [],
    'A_all_blocked_placeholders': records(batchA),
    'B_23_done_15_blocked': records(batchB),
    'C_upper_bound_all_38_done_NOT_ASSERTED': as_all_done(batchB),
}
done_statuses = supervisor.normalized_status_set(
    supervisor.ready_dispatch_settings(config).get('dependency_done_statuses'), ['done'])
base_ids = {str(t.get('id') or '').strip() for t in live_tasks}
before = None
for name, extra in scenarios.items():
    tasks = live_tasks + extra
    tmap = {str(t.get('id') or '').strip(): t for t in tasks if str(t.get('id') or '').strip()}
    dispatchable = supervisor.canonical_dispatchable_task_ids(config, tasks)
    dep_sat = {tid: supervisor.dependencies_satisfied(t, tmap, done_statuses)
               for tid, t in tmap.items() if (t.get('depends_on') or [])}
    blocked_ok = {tid: supervisor.blocked_task_auto_recovery_eligible(config, t, tmap)
                  for tid, t in tmap.items() if str(t.get('status') or '') == 'blocked'}
    row = {'dispatchable': sorted(dispatchable),
           'deps_satisfied': sorted(k for k, v in dep_sat.items() if v),
           'blocked_auto_recovery_eligible': sorted(k for k, v in blocked_ok.items() if v)}
    if before is None:
        before = row
    else:
        row['newly_dispatchable'] = sorted(set(row['dispatchable']) - set(before['dispatchable']))
        row['newly_deps_satisfied'] = sorted(set(row['deps_satisfied']) - set(before['deps_satisfied']))
    print(name, json.dumps(row, ensure_ascii=False))
```

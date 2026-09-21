# ORCH-ARCHIVE-HISTORY-RESTORE-002 收據：38 筆候選的逐筆證據核實與復原輸入交接

- 產出者：Claude（owner）／審查者：Codex（reviewer）
- 量測時間：2026-09-07T15:02Z – 15:17Z
- 交付分支：`task/ORCH-ARCHIVE-HISTORY-RESTORE-002`（base `origin/dev` tip `19167c10b8c6978fb4aea934bc6a668dc926b74e`）

> 本輪**沒有**對 canonical board（`ai-status.json`）或 archive（`ai-task-archive/`）執行任何 recovery 寫入，
> **沒有**執行 `archive_recovery_apply`（連 PLAN ONLY 都沒有），**沒有**建立或簽署 maintenance hold，
> **沒有**停止／重啟 Supervisor，**沒有**改 watchdog／cron／config，**沒有**送任何訊號給任何行程。
> **歷史尚未回填。** 本文件**不是** maintenance hold、**不是** Human GO、**不是**部署授權。
>
> 本輪唯一的 canonical 寫入是本任務自身的狀態轉換（`ai-status.sh start`），經 live canonical writer 執行。

本輪與前一輪（PR #1232）的差別：前一輪交的是唯讀預檢與窗口交接；本輪交的是**逐筆證據**與
**可直接餵進已合併 planner 的兩份輸入**，並量出一個前一輪沒有量的結構性事實——
**已提交批次的「38 筆全 blocked 佔位」方案，對工程派送的解除效果是 0**（見 §5）。

---

## 1. live 狀態複量（推翻舊 runtime 結論）

| 項目 | 量測值 |
| --- | --- |
| `runtime-current` 解析 | `/home/lupin/oday-plus-supervisor-runtime-19167c10b8c6` |
| runtime HEAD／分支 | `19167c10b8c6978fb4aea934bc6a668dc926b74e`／`runtime-live-19167c10b8c6` |
| 是否等於 `origin/dev` tip | **是**（本 worktree base 亦同） |
| live writer `archive_recovery_apply` 出現次數 | **4**（前一輪在 `64f3` 上是 0） |
| live writer `validate_recovery_batch` 出現次數 | **1**（前一輪是 0） |
| planner `--recovery-inventory` 旗標 | **存在** |
| launcher 尾行 | `exec python3 /home/lupin/oday-plus-supervisor-runtime-current/scripts/ai_status.py "$@"` |

**APPLY_PREFLIGHT 的 B1（live writer 沒有 `archive_recovery_apply`）與 B2（rollout 無法從 canonical checkout 執行）
已因 runtime 前推而消失。** 依派工要求，本輪不重複舊 runtime 缺功能的結論。
runtime 工作樹有一個未提交檔案 `ai-activity-log.jsonl`（執行期產物），本輪未動它。

### 1.1 其他控制者（實測，不以 dashboard 推論）

| 控制者 | 實測狀態 |
| --- | --- |
| Supervisor | **存活**。PID `6522`，state `Ss`（不是 SIGSTOP），ppid 1，啟動 2026-09-07 14:52:38。`.orchestrator/supervisor.pid` = 6522，一致。 |
| watchdog（cron） | **維持暫停**。user crontab 僅剩一行被註解掉的 `# PAUSED ODP_ARCHIVE_INCIDENT_20260906_0643: …`。 |
| live config 派工姿態 | `ready_dispatcher.enabled: true`、`capacity_controller.enabled: true`、`capacity_controller.sidecars.enabled: true`。 |

**派工是開著的、Supervisor 是活的、看板正在漂。維護窗口不成立。**
本輪 15 分鐘內量到兩次 board revision 變動：`186a3925…`（15:04Z）→ `93d9c712…`（15:16Z）。

### 1.2 本輪 baseline 實測（僅供對照，不可作為 apply 基準）

| 項目 | 2026-09-07T15:16:36Z 實測 |
| --- | --- |
| `origin/dev` tip | `19167c10b8c6978fb4aea934bc6a668dc926b74e` |
| board SHA-256 | `ff03d90cfbffec4fc5d7d7d89dc97305976ee74b6c42bf7d089002ec6817b26b` |
| board `_status_write_revision` | `93d9c712f1d246d6967fc790eaa4a621` |
| archive `index.json` SHA-256 | `e38123892b3a7cd42136dd42348591ab36297f6ec41c90e3b553447b80344543` |
| archive snapshot 筆數 | **13**（前一輪 8；新增 5 筆為其後陸續收尾的任務） |
| live board 任務數 | 23 |

已提交批次（`ORCH_ARCHIVE_HISTORY_RECOVERY_20260906-batch.json`）釘住的 baseline 與上表全部不符，
**它現在必定被 apply 期的 baseline drift 閘拒絕**，這是設計行為。批次必須在窗口內重建。

---

## 2. 缺失清單複量：仍然是 38 筆，一筆不多不少

以 live board + live archive 實測懸空依賴：**44 條懸空邊、38 個相異目標 ID、由 12 個現存任務發出**，
與 PR #1230／#1231／#1232 三方清單完全相同，沒有第 39 個。

---

## 3. 來源重建：原始 /tmp 事故資料已不存在

| 原始檔 | 已提交批次記載的 SHA-256 | 本輪實測 |
| --- | --- | --- |
| `/tmp/odayplus-archive-incident.CYD1gq/RECOVERY_DEPENDENCY_CANDIDATES_20260906_1523.json` | `116f583e…b0685` | **檔案不存在**（整個 `/tmp/odayplus-archive-incident.*` 目錄已不存在） |
| `/tmp/odayplus-archive-incident.CYD1gq/RECOVERY_AUTHORIZATION_ZH_TW.md` | `134adcb8…2aba5` | **檔案不存在** |

兩份原始檔都只存在於 `/tmp`，都未進版控。依驗收要求，本輪**從已提交證據重建新來源清單並標記重建**：

- 交付 `recovery_inventory_rebuilt_20260907.json`，帶 `"rebuilt": true`、`rebuilt_by`、`rebuild_reason`、
  `rebuilt_from`（逐項附來源路徑與 SHA-256）。
- **不宣稱**它與原檔逐 byte 相同，**未偽造**原檔或原檔的 SHA-256。
- 重建來源：已合併的中立盤點（PR #1230，`task_evidence_inventory.json`，SHA-256
  `f4239acf…f715`）與已合併的三方對帳（PR #1232，`candidate_reconciliation.json`）。

### 3.1 已修正 APPLY_PREFLIGHT 的 B4（P1，XR 跨 repo 誤配）

重建盤點已把 `XR-EXT-OSS-FINAL-AUDIT-001` 的候選從誤配的 `alfloop-dev/odayplus#996`
換成更正後的 `alfloop-dev/oday-data-platform#61`（head `b1824c979aca…`、merge `7b0670d7b37e…`），
依據是同一筆已合併證據的 `corrected_candidate` / `repository_correction` /
`recommendation_applies_to` 三個並列欄位。這是**輸入資料修正**，未新增第二套 planner，
也未修改任何已合併的證據檔案。

`DPF-EMGI-LIVE-ROLLOUT-001` 三方一致指向 `alfloop-dev/oday-data-platform#62`，照原樣沿用。

### 3.2 授權文件：本輪的結構性阻塞（**B-AUTH**）

已合併 planner 的 `build_recovery_batch()` **強制**要求 `--authorization` 指向一份存在的文件，
並把它的 SHA-256 釘進 batch baseline；apply 期會重新雜湊並在漂移時拒絕。

原授權文件已不存在，其內容**未被任何已提交證據引用或轉錄**（只有 SHA-256 被記下來）。因此：

- 無法忠實重建原檔；
- 由本 auto worker 自行撰寫一份新的「授權」等於**自行簽署 Human/Ops 許可**，驗收明文禁止。

**本輪因此沒有產出可套用的批次，也沒有產出 plan-only 預覽批次。** 這不是遺漏，是拒絕越權。
授權文件必須由有權限者在窗口內提供（見 §7 第 2 步）。

本輪改以已合併 planner 自身的 `load_recovery_inventory()` 與 `grade_recovery_entry()`
離線驗證兩份輸入可被消費且分級如預期（§4）——這條路徑不需要授權文件，也不產生任何批次檔。

---

## 4. 逐筆證據核實與 attestations 草案

### 4.1 本輪對 38 筆各自重量到什麼

對每一筆的**精確 head SHA** 重新量 GitHub 持久收據（2026-09-07，非引用舊值）：

| 量測項 | 結果 |
| --- | --- |
| `gh api repos/<repo>/commits/<head>/check-runs` | 38/38 有 check-runs；**35 筆全 success、3 筆含 skipped、0 筆有任何 failure/cancelled/timed_out** |
| `gh api repos/<repo>/commits/<head>/status`（`task-review-gate`） | **38/38 為 `success`**，皆帶具名核准者 |
| planner `find_merge_evidence(repo, id, origin/dev)` | **36/38 可在 pinned ref 上驗出自有分支 merge**；2 筆跨 repo（DPF、XR）驗不出 |
| 候選 merge commit 與本地 merge 不一致者 | **0 筆**（無 `candidate_merge_commit_mismatch`） |
| GitHub API 失敗 | **0 筆** |

逐筆原始量測值（含每個 check 的 name／conclusion／completed_at、gate 的 updated_at／description、
本地 merge 的 delivery form 與 PR 號）交付於 `evidence_dossier_20260907.json`。

### 4.2 attestation 出具規則（**這是本輪最需要 Codex 逐條裁定的東西**）

規則以機讀形式寫在 `attestations_draft_20260907.json` 的 `issuance_rule`，可逐條否決後重新推導，
不需重量。四類各自的出具條件：

- **acceptance** — 已合併中立盤點該筆 `recommendation == verified_candidate`、`blocking_reasons` 為空，
  且逐條狀態不含 `unmet_per_own_receipt` / `not_evidenced` /
  `test_delivered_not_executed_at_exact_head` / `partially_met`。
  `process_constraint_unverifiable` 依該盤點**已合併且已審查**的 `decision_rule`
  （`process_constraint_does_not_block`）不構成 blocked，但逐筆揭露計數。
- **ci** — 2026-09-07 對精確 head 重量：至少一個 success，且沒有任何 success/skipped/neutral 以外的結論。
- **runtime** — **只在**該筆 `acceptance_class` 同時為 `runtime_deployment=false`、
  `external_enablement=false`、`human_authority=false`，**且**自有分支 merge 可在 pinned ref 上驗出時才出具。
  語意是「本任務的驗收不含 runtime 部署，且交付碼確實在 pinned ref 與 live runtime 內」，
  **不是**任何歷史部署收據。凡 `runtime_deployment=true` 一律不出具，保留 gap。
- **approval** — 2026-09-07 對精確 head 重量 `task-review-gate == success`，
  且已合併盤點記載該 gate 具名核准者且**核准者 ≠ owner**。

**明確不宣稱**（同樣寫在草案的 `not_claimed`）：

- 不宣稱任何歷史 owner／reviewer／approver／Human GO 身分；那些在 planner 產出的記錄裡仍是
  `historical_actors = UNKNOWN-HISTORICAL`，與 `recovery_actors`（Claude／Codex）分開兩個鍵。
- 不宣稱任何 production 部署、外部來源啟用或 release GO。
- `verifier: Claude` / `verified_at: 2026-09-07` 的意思是「今天由我重量到這份持久收據」，
  **不是**「當年由我驗收」。

草案對 38 筆**全部**出具了能出具的部分（38/38 有 ci 與 approval），
只有四項齊備的 23 筆會被 planner 升到 `reconstructable_done`；其餘 15 筆的缺口原樣保留。

### 4.3 優先項逐筆明細（驗收點名的三筆）

| 任務 ID | 候選 PR | 精確 head | merge | CI（今日重量） | task-review-gate（今日重量） | pinned ref 本地 merge |
| --- | --- | --- | --- | --- | --- | --- |
| `ODP-DRIFT-DEP-REMOVE-002` | odayplus#1222 | `6d438486c864` | `66244b30c261` | 7 checks 全 success | success — Approved by assigned reviewer Antigravity4 @ 2026-09-06T05:31:36Z | merge-commit #1222 |
| `ODP-HZ006-MERGE-SPLIT-IMPLEMENTATION-001` | odayplus#1170 | `0585dd49976e` | `eed8d51bb8a1` | 7 checks 全 success | success — Approved by assigned reviewer Claude @ 2026-09-05T15:43:12Z | merge-commit #1170 |
| `ODP-AVM-DEPRECIATION-CONTRACT-001` | odayplus#1148 | `aacc6ca19c12` | `739cbab19a17` | 7 checks 全 success | success — Approved by assigned reviewer Antigravity6 @ 2026-09-04T12:10:24Z | merge-commit #1148 |

三筆的 `acceptance_class` 皆為 `runtime_deployment=false / external_enablement=false / human_authority=false`，
因此四項齊備。`ODP-DRIFT-DEP-REMOVE-002` 的 merged 盤點 confidence 是 **medium**
（有 2 條 `process_constraint_unverifiable`），已在草案的 `_disclosure` 逐筆揭露。

### 4.4 以已合併 planner 實跑分級（未產生批次、未寫入任何 canonical 檔案）

```
load_recovery_inventory(recovery_inventory_rebuilt_20260907.json)  -> OK, entries = 38
grade_recovery_entry(...) x38, attestation = attestations_draft_20260907.json
```

| 結果 | 筆數 |
| --- | --- |
| `reconstructable_done` → `archive_reconstructed_done` | **23** |
| `merge_verified` → `active_blocked_placeholder` | 13 |
| `merge_claimed` → `active_blocked_placeholder`（DPF、XR，跨 repo） | 2 |
| **planning refusals** | **0** |

15 筆維持 blocked 佔位的缺口逐筆保留（`acceptance` 缺 9 筆、`runtime` 缺 9 筆、
跨 repo `merge_not_verifiable_on_ref` 2 筆），明細見 `dependency_release_analysis_20260907.json`
的 `grading_by_merged_planner`。

**「PR MERGED 不是需求已實作」「verified_candidate 不是 done」在本輪成立：**
已合併盤點給 35 筆 `verified_candidate`，本輪的四證言規則只讓 **23** 筆通過，
其中 12 筆因 runtime／外部啟用／人類授權類別或 acceptance 逐條狀態而被擋下。

---

## 5. 本輪最重要的量測：blocked 佔位對派送的解除效果是 0

以 live board、live archive、live config 為輸入，**直接呼叫既有 `.orchestrator/supervisor.py` 的
`dependencies_satisfied()` 與 `blocked_task_auto_recovery_eligible()`**（純函式求值，零寫入）：

| 情境 | 依賴新滿足的任務 | **新變成可排程的任務** |
| --- | --- | --- |
| BEFORE（今日） | — | — |
| **A：已提交批次的方案**（38 筆全 blocked 佔位） | **0** | **0** |
| **B：本輪草案的方案**（23 筆 reconstructed done + 15 筆 blocked 佔位） | 2 | **1** |
| C：上界對照（38 筆全 done，**本輪不主張**） | 6 | 2 |

情境 C 的 2 筆之中有一筆是 `HUMAN-OSS-LEGAL-APPROVAL-001`（人類授權任務），
機械判準會算進去，但它本來就不是 auto worker 的派送對象。

原因是結構性的，不是設定問題：`ready_dispatcher.dependency_done_statuses` 是 `["done"]`，
而 `task_archive.task_satisfies_dependency()` 要求 `status == done` 且非 superseded。
**blocked 佔位把依賴目標從 `missing` 變成 `blocked`，對依賴閘而言等價。**

也就是說，如果窗口內照已提交批次原樣 apply，會得到一個乾淨的懸空圖、38 筆可稽核的佔位記錄，
以及**與現在完全相同的派送死結**。這一項請 Codex 在裁定批次形狀時一併考慮。

### 5.1 情境 B 的已可排程任務名單

**新變成可排程：`ODP-DRIFT-SECURITY-VERIFY-003`**（status `todo`、owner Antigravity4、
`non_dispatchable: false`，兩個依賴 `ODP-DRIFT-DEP-REMOVE-002` 與
`ODP-CI-DEPENDENCY-AUDIT-BOUNDARY-001` 全部滿足）。這正是驗收點名要釋放的既有安全驗證工作。

**依賴閘解開但仍不會被派送：`ODP-AVM-QUALITY-NULLABLE-001`**。
它的唯一依賴 `ODP-HZ006-MERGE-SPLIT-IMPLEMENTATION-001` 在情境 B 下已滿足
（`dependencies_satisfied = True`），但它自身 status 是 `blocked`，且 blocked prose 命中 supervisor 的
`hard_gate_markers`（"escalated to human/ops after 11 reviewer reopens…"），
因此 `blocked_task_auto_recovery_eligible = False`。**這是 archive 事故之前就存在的審查升級，
與本次復原無關；本輪不碰它，也不宣稱 AVM 工作已被釋放。**
AVM 線的下一個閘是那個人類裁決，不是缺失的歷史。

---

## 6. 仍然成立的阻塞與風險（每一項本輪都自己量過）

### B-AUTH（結構性阻塞・本輪新提）授權文件不存在且不得由執行者補寫

見 §3.2。**在有權限者提供授權文件之前，窗口內的第 4 步（重新規劃批次）在結構上跑不起來。**

### B3（順序阻塞，仍成立）apply 的 PLAN ONLY 預演也需要先有 hold

dev tip 的 `command_archive_recovery_apply` 在 `--confirm` 判斷**之前**就呼叫 `_archive_recovery_hold()`，
baseline drift 檢查也在之前。沒有合法、未過期、綁定該批次 SHA-256、且由 reviewer（非執行者）核准的 hold，
連不帶 `--confirm` 的預演都跑不起來。**不能指望「先預演看看再決定要不要開窗口」。**

### B5（P2，仍成立）跨 repo 佔位不帶 `repository` 欄位

planner 產生的記錄沒有 `repository` 欄。本輪的重建盤點在 `entries[].repository` 帶了正確值，
但 planner 不會把它轉寫進記錄。對本批次不致命——兩筆跨 repo（DPF#62、XR#61）在情境 B 下**都維持 blocked 佔位**，
不會進 archive 終態。若日後要把任一筆推進到 done，task→repo 解析會落回預設 repo。
本輪**不**擅自擴充 planner 的 record schema。

### B6（P2，仍成立但規模減半）blocked 佔位會成為 capacity sidecar 的候選父任務

實測 `.orchestrator/capacity_controller.py` 第 629 行：
`prose` 由 `blocked_reason` + `next` + `waiting_for` 串起後小寫，
再比對 `HARD_GATE_MARKERS = {"human gate", "manual approval", "credential", "license", "production proof", "legal"}`。

planner 佔位的實際 prose 是
`next` =「等待人工裁決：補齊 acceptance/ci/runtime/approval 證據，或確認此 ID 以 blocked 結案。」、
`waiting_for` = `Human/Ops`。**六個 marker 一個都不命中**（`"human gate"` ≠ `"human/ops"`），
而 live config 是 `capacity_controller.enabled: true` / `sidecars.enabled: true`。

因此佔位會成為合格的 sidecar 父任務。情境 B 下的曝險是 **15 筆**（已提交批次的方案是 38 筆）。
規模上限由 `max_new_per_wave: 3`、`max_active: 4`、`require_chair_approval: true` 拘束。
本輪**不擅自改 config、不改 marker 集合**，交由 Codex 在開窗口前決定處置。

---

## 7. 維護窗口的 exact 命令交接（本輪一律未執行）

「需人工／有權限者」的步驟本 auto worker 無權執行（live config 為 mutation-blocked）。

**第 1 步 — 建立真實的維護窗口（需人工／有權限者）**：停派工、排除其他控制者、worker drain、
Supervisor 真正退出。本輪的唯讀量測**不構成** hold。

**第 2 步 — 提供 recovery authorization 文件（需人工／有權限者，B-AUTH）**：
原檔已不存在且內容無處可考。需由有權限者重新出具一份授權文件並落到一個穩定路徑
（建議進版控，避免再次只存在於 `/tmp`）。**執行者不得自行撰寫。**

**第 3 步 — 在 hold 下重新規劃批次（窗口內）**：

```bash
uv run --frozen --python 3.12 python scripts/orchestrator/backfill_task_archive_snapshots.py \
  --archive-dir /home/lupin/odayplus/ai-task-archive/tasks \
  --repo <SRC> --ref origin/dev \
  --board /home/lupin/odayplus/ai-status.json \
  --recovery-inventory docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_RESTORE_20260907/recovery_inventory_rebuilt_20260907.json \
  --attestations   docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_RESTORE_20260907/attestations_draft_20260907.json \
  --authorization  <第 2 步的授權文件> \
  --batch-out <窗口內的新批次路徑> \
  --recovery-owner Claude --recovery-reviewer Codex
sha256sum <窗口內的新批次路徑>
```

`--attestations` 一旦被 Codex 逐條裁定過，就用裁定後的版本；本輪交的是**草案**。

**已知並揭露**：planner 的 `RECOVERY_SOURCE` 常數硬寫為 `ORCH-ARCHIVE-HISTORY-RECOVERY-001`，
且 `validate_recovery_entry()` 強制 `history_recovery.created_by` 等於它。因此無論由哪個 task 重新規劃，
批次的 `generated_by` 與每筆的 `created_by` 都會是 `ORCH-ARCHIVE-HISTORY-RECOVERY-001`。
那是工具的 provenance，不是誤植。

**第 4 步 — Codex 核准新 hash 並簽出 hold（需 Codex，非執行者）**：hold 的逐項欄位需求與
`batch_approval.reviewer` 不得等於執行 apply 的 actor，沿用 PR #1232 §4 第 5 步所列，本輪未變更。

**第 5 步 — PLAN ONLY 預演，然後 `--confirm`（窗口內，透過 canonical launcher）**：

```bash
mkdir -p /home/lupin/odayplus/.orchestrator/recovery-receipts
CHK=/home/lupin/odayplus/.orchestrator/recovery-receipts/history-recovery-checkpoint-$(date -u +%Y%m%dT%H%M%SZ).json

AI_NAME=Claude "$PANTHEON_STATUS_ROOT/scripts/ai-status.sh" archive_recovery_apply \
  --batch <新批次> --maintenance-hold <hold.json>

AI_NAME=Claude "$PANTHEON_STATUS_ROOT/scripts/ai-status.sh" archive_recovery_apply \
  --batch <新批次> --maintenance-hold <hold.json> --checkpoint "$CHK" --confirm
```

部分失敗時保留 checkpoint、**維持窗口不解除**、交出實際 readback，**不宣稱 rollback**、不在窗口外重試。

---

## 8. 交付檔案

| 路徑 | SHA-256 | 內容 |
| --- | --- | --- |
| `…/ORCH_ARCHIVE_HISTORY_RESTORE_20260907_ZH_TW.md` | — | 本文件 |
| `…/ORCH_ARCHIVE_HISTORY_RESTORE_20260907/recovery_inventory_rebuilt_20260907.json` | `54d2386b…d12ca` | **重建的 planner 輸入**（`rebuilt: true`，XR 已更正為 oday-data-platform#61） |
| `…/ORCH_ARCHIVE_HISTORY_RESTORE_20260907/attestations_draft_20260907.json` | `f091830b…692c0` | **attestations 草案**（`draft: true`、`approved_by_reviewer: false`），含機讀出具規則與逐筆揭露 |
| `…/ORCH_ARCHIVE_HISTORY_RESTORE_20260907/evidence_dossier_20260907.json` | `b54103f7…3fdda` | 38 筆的逐筆原始量測（CI check 逐項、gate、本地 merge、已合併盤點欄位） |
| `…/ORCH_ARCHIVE_HISTORY_RESTORE_20260907/dependency_release_analysis_20260907.json` | `1bb30a4c…4643fa` | 三情境的依賴解除前後表、已可排程名單、planner 分級結果 |

只新增 docs 證據，未動任何 `.py`、config、workflow 或治理 manifest，因此不需重產
`docs/audits/code-boundary-inventory.csv`。**未新增第二套 writer、planner、batch builder 或 dispatcher。**

---

## 9. 明確未完成／未宣稱

- **未執行 canonical apply（連 PLAN ONLY 都沒有）。歷史未回填。38 筆全部仍不在 board 也不在 archive。**
- **未建立、未簽署、未宣稱任何 maintenance hold。** 本輪的唯讀量測不構成 hold。
- **未產出可套用的批次，也未產出 plan-only 預覽批次**——因為授權文件不存在且不得由執行者補寫（B-AUTH）。
- 未停止／重啟 Supervisor，未送任何訊號給任何行程，未改 watchdog／cron／systemd／live config／
  模型／CLI／角色／認證／配額／IAM。
- 未修改任何已合併的證據檔案（含 B4 誤配的 `task_evidence_inventory.json`）；XR 的更正只做在本輪新建的輸入盤點裡。
- 未補造歷史 owner／reviewer／approver／Human GO。未偽造原始盤點檔或原始授權文件。
- 未跑產品整套 CI、未 dispatch 產品 release、未啟用任何 provider。
  本輪跑過的可執行路徑只有：`gh api` 唯讀查詢、已合併 planner 的 `find_merge_evidence` /
  `load_recovery_inventory` / `grade_recovery_entry`、以及既有 supervisor 依賴函式的純函式求值。
- attestations 是**草案**，未經 Codex 裁定。`approved_by_reviewer: false`。
  **本輪不代表 Codex 審查，也不代表窗口已成立。**

## 10. 給 Codex 的待裁決清單

1. **B-AUTH**：授權文件由誰、以什麼形式重新出具，以及是否進版控（§3.2）。**這是目前的第一順位阻塞。**
2. **attestation 出具規則逐條裁定**（§4.2）。特別是 `runtime` 那條——
   「`runtime_deployment=false` 即視為無 runtime 驗收待證」是否成立？若否決，23 筆會全數退回 blocked 佔位。
3. `process_constraint_unverifiable` 是否沿用 PR #1230 已合併的 `process_constraint_does_not_block`。
   若改為阻擋，`ODP-DRIFT-DEP-REMOVE-002`（confidence medium、2 條）等筆會退回 blocked。
4. **批次形狀**：接受情境 B（23 done + 15 blocked），或退回情境 A（38 全 blocked）。
   §5 已量出情境 A 的派送解除效果是 0，請在裁定時一併考慮。
5. **B6**：15 筆 blocked 佔位觸發 capacity sidecar 的處置（窗口內暫時關 sidecars／接受並由 chair 逐一否決）。
6. **B5**：跨 repo 佔位是否需要 `repository` 欄位（會動到 planner 的 record schema）。
7. 維護窗口的實際時間，以及窗口內由誰擔任 apply 的執行者（必須 ≠ Codex）。

在上述裁決與真實維護窗口成立之前，本任務停在這個 checkpoint，**不重試、不循環、不擅自進入正式步驟**。

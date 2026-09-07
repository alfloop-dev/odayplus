# ORCH-ARCHIVE-HISTORY-APPLY-001 第一段收據：唯讀執行預檢與維護窗口交接

- 產出者：Claude（owner・第 1–2 輪）／Antigravity3（owner・第 3 輪）／審查者：Codex（reviewer）
- 量測時間：2026-09-07T04:06Z – 04:17Z（第 1 輪）
- 第 2 輪複量：2026-09-07T04:29Z – 04:30Z，只重量 PR #1227 的 exact-head 狀態（B7、§3、§8-5），其餘量測值與 plan-only 預覽未重跑、未變更
- 第 3 輪更新：2026-09-07T04:39Z，PR #1227 已合併（merge commit `9094b4ac`）。更新 B7、§3、§6、§8-5 為已合併但尚未 runtime rollout；移除已過期的 OPEN／未合併／finalize 路由 blocker。Base advance merge `origin/dev`。
- 交付分支：`task/ORCH-ARCHIVE-HISTORY-APPLY-001`（base advance 後包含 `origin/dev` tip `9094b4ac`）

> 本輪**只做唯讀預檢與交接**。**沒有**對 canonical board（`ai-status.json`）或 archive（`ai-task-archive/`）
> 執行任何 recovery 寫入，**沒有**切換 live runtime，**沒有**停止或重啟 Supervisor，**沒有**改 watchdog／cron／config，
> **沒有**執行 `archive_recovery_apply --confirm`。**歷史尚未回填。**
> 本文件**不是** maintenance hold、**不是** Human GO、**不是**部署授權，也**不宣稱**任何維護窗口已成立。

本輪唯一的 canonical 寫入是本任務自身的狀態轉換（`ai-status.sh start ORCH-ARCHIVE-HISTORY-APPLY-001`），
經 live canonical writer 執行；那不是 recovery 寫入。

---

## 1. 唯讀預檢結果

### 1.1 exact dev／PR #1231／PR #1230 ancestry

| 項目 | 量測值 |
| --- | --- |
| `origin/dev` tip | `da4b77d175151f7c962badbfdddb35817910db52` |
| PR #1230 merge commit | `da4b77d175151f7c962badbfdddb35817910db52`（即 dev tip） |
| PR #1230 head | `b0074d49ed7a8ed9bd2dd07c8fe526307cf00694` → **是** dev 的祖先 |
| PR #1231 merge commit | `1649f81045ab0eb7122a83eb858230c9645ae367` |
| PR #1231 head | `9b32e5c3e56058bc6cd5c78439e86b4ff77d3e14` → **是** dev 的祖先 |
| PR #1231 的 first parent | `bd4fb5aa11404519ff1d8ae97fa796eb6d40e12a` |
| 已提交批次釘住的 `ref_commit` | `bd4fb5aa11404519ff1d8ae97fa796eb6d40e12a` → 是 dev 祖先，但**已不是 dev tip** |

兩個依賴 task 都已終態並落 archive：`ORCH-ARCHIVE-HISTORY-RECOVERY-001`、`ORCH-ARCHIVE-RECOVERY-EVIDENCE-001`。

### 1.2 live runtime `64f3` 與已合併版本的差異

| 項目 | 量測值 |
| --- | --- |
| 穩定入口 | `/home/lupin/oday-plus-supervisor-runtime-current` → `/home/lupin/oday-plus-supervisor-runtime-64f3b2399442`（symlink，2026-09-06 06:00） |
| runtime HEAD／分支 | `64f3b2399442e8cd7531284d1e7a3e33bcc3ca9a`／`runtime-live-64f3b2399442` |
| runtime 工作樹 | **乾淨**（`git status --porcelain --untracked-files=no` 無輸出） |
| 是否為 dev 祖先 | 是 |
| 落後 dev tip | **41 個 commit**、**4 個 PR merge**：#1225、#1188、#1231、#1230 |

控制面差異（`64f3 .. dev tip`，限 `scripts/`、`.orchestrator/`、`delivery_toolchain/`、`config/`）：
**12 個檔案、+5905／−72 行**。其中與本任務直接相關的是：

- `scripts/ai_status.py` +1210（PR #1231 的 `archive_recovery_apply` 寫入者）
- `scripts/orchestrator/backfill_task_archive_snapshots.py` +1197（recovery 規劃模式）
- `scripts/orchestrator/test_archive_history_recovery.py` +2319（焦點回歸）

**結構性事實（本輪最重要的一項）**：

| 符號 | live runtime `64f3` | dev tip |
| --- | --- | --- |
| `ai_status.py` 內 `archive_recovery_apply` 出現次數 | **0** | 4 |
| `ai_status.py` 內 `validate_recovery_batch` 出現次數 | **0** | 1 |
| planner 的 `--recovery-inventory` 旗標 | **不存在** | 存在 |

`/home/lupin/odayplus/scripts/ai-status.sh` 是 shim，最後一行是
`exec python3 /home/lupin/oday-plus-supervisor-runtime-current/scripts/ai_status.py "$@"`。
因此**目前透過 canonical launcher 呼叫 `archive_recovery_apply` 必定失敗（該子命令在 live 程式碼裡不存在）**。
這確認驗收所述「live runtime 未接入新 writer」，而且是可量測的，不是推論。

### 1.3 現有 rollout 入口與 launcher（含兩次 `--dry-run` 實測）

唯一入口是已合併的 `scripts/orchestrator/rollout_supervisor_runtime.py`（本輪未修改一個字）。
它的契約：`--source-root` 必須是**乾淨**且 `HEAD == --tracking-ref`（預設 `origin/dev`）的 checkout；
準備一個新的具名 worktree → 改寫 `--status-root/scripts/ai-status.sh` → 換 symlink → 重啟 Supervisor；
失敗則還原 symlink 與 launcher。

實測一（以 canonical checkout 為 source）：

```bash
python3 scripts/orchestrator/rollout_supervisor_runtime.py \
  --source-root /home/lupin/odayplus \
  --runtime-link /home/lupin/oday-plus-supervisor-runtime-current \
  --runtime-parent /home/lupin --status-root /home/lupin/odayplus \
  --config-path /home/lupin/odayplus/.orchestrator/config.json \
  --watchdog-pid-file /home/lupin/odayplus/.orchestrator/supervisor.pid --dry-run
```

結果：**exit 1**，`refusing rollout from a dirty source checkout`。

原因（唯讀量測，未修正）：canonical checkout `/home/lupin/odayplus` 目前

- 分支 `dev`，HEAD `9054479a776dce41e8a144c12032a85471a91f1b`；**不是** dev tip 的祖先，落後 **1201** 個 commit；
- 有 11 個已追蹤檔案為 dirty，其中包含控制面程式：
  `scripts/ai-status.sh`（+2 行，內容正是 rollout 產生的 launcher：兩行 `ORCH_CONFIG_PATH`／`PANTHEON_CONFIG_PATH` 匯出）
  與 `scripts/ai_status.py`（2 個 hunk，未提交）。

依驗收要求，本輪**不修這些 canonical dirty 檔案、不復原舊 code**，只揭露。
注意 `scripts/ai_status.py` 的 dirty 版本**不是**實際執行的程式（實際執行的是 runtime-current 那份），
所以它目前的效果只有一個：讓 rollout 的 `clean(source)` 檢查失敗。

實測二（以本 task 的乾淨 dev-tip worktree 為 source）：

```bash
python3 scripts/orchestrator/rollout_supervisor_runtime.py \
  --source-root /tmp/pantheon-worker-worktrees/pantheon/orch-archive-history-apply-001 \
  --runtime-link /home/lupin/oday-plus-supervisor-runtime-current \
  --runtime-parent /home/lupin --status-root /home/lupin/odayplus \
  --config-path /home/lupin/odayplus/.orchestrator/config.json \
  --watchdog-pid-file /home/lupin/odayplus/.orchestrator/supervisor.pid --dry-run
```

結果：**exit 0**

```
target=/home/lupin/oday-plus-supervisor-runtime-da4b77d17515 sha=da4b77d175151f7c962badbfdddb35817910db52 branch=runtime-live-da4b77d17515
previous=/home/lupin/oday-plus-supervisor-runtime-64f3b2399442
status_launcher=/home/lupin/odayplus/scripts/ai-status.sh writer=/home/lupin/oday-plus-supervisor-runtime-current/scripts/ai_status.py
```

執行後立即複驗：symlink 仍指向 `64f3b2399442`、launcher SHA-256 未變、Supervisor PID 1850381 仍存活。**零變更。**

`--dry-run` 的一個已知副作用要講清楚：它在返回前會先跑 `git fetch --quiet origin dev`，
所以它對 source repo 的 remote ref **不是**完全唯讀。它不碰 board、archive、symlink、launcher 或任何行程。

rollout 的重啟路徑是 `bash <runtime>/scripts/run-supervisor-watchdog.sh --config <config> --restart`，
並先對 `--watchdog-pid-file` 內的 PID 送 SIGTERM。這是**live runtime 切換**，第一段明確禁止，
必須留到維護窗口。

### 1.4 config schema 兼容

`.orchestrator/config.schema.json` 在 `64f3` 與 dev tip **逐 byte 相同**
（SHA-256 皆為 `24ca6d67483deea321cb9db62bb631ff9207ca685d613ed56affdeceb56b5c76`），
`.orchestrator/config.example.json` 在該區間亦無 diff。
因此把 runtime 前推到 dev tip **不需要 config 遷移**，live `/home/lupin/odayplus/.orchestrator/config.json` 可原樣沿用。

rollout 對 live config 的唯一動作是把它的路徑寫進 launcher 的兩行 `export`，不改內容。

### 1.5 現存 8 筆 archive 與 baseline 漂移

`ai-task-archive/index.json`：`total 8`（completed 4／superseded 4），SHA-256
`9d4961616cfa6d5fa9ef27b7003ea6e44e60da1918b4b7f6c0ed9944fee1d5a4`。

| snapshot | SHA-256 | 與已提交批次釘住的 baseline 比對 |
| --- | --- | --- |
| `DPF-EMGI-MASKED-RELEASE-SNAPSHO-SIDECAR-C4E84D0D` | `417e7694…33c392` | 相同 |
| `ODP-CI-DEPENDENCY-AUDIT-BOUNDARY-001` | `42eb0dff…92b1e` | 相同 |
| `ODP-DEV-LIVE-ROLLOUT-REMEDIATIO-SIDECAR-7CC5581A` | `11ac24e1…7e79a` | 相同 |
| `ODP-EPHEMERAL-STAGING-ROLLOUT-0-SIDECAR-C1E25549` | `d0444ad8…dbeca0` | 相同 |
| `ODP-STAGING-FOUNDATION-IAC-REME-SIDECAR-D7ED3693` | `b5b89455…7527c8` | 相同 |
| `ORCH-CAPACITY-ARCHIVE-DEDUP-001` | `8f3b8eea…d2afd` | 相同 |
| `ORCH-ARCHIVE-HISTORY-RECOVERY-001` | `d234dc3e…b9501` | **新增**（批次規劃後才落盤） |
| `ORCH-ARCHIVE-RECOVERY-EVIDENCE-001` | `e210be1c…f752d` | **新增** |

原有 6 筆 byte 未變（事故後倖存件未被覆寫），另外多了 2 筆，正是本任務的兩個依賴 task 收尾所致。

baseline 漂移逐項（已提交批次 `dd1a9c1c…20e5` 釘住的值 → 本輪實測）：

| 釘住項 | 批次內的值 | 2026-09-07 實測 | 漂移 |
| --- | --- | --- | --- |
| `ref_commit` | `bd4fb5aa…0e12a` | `da4b77d1…0db52` | **是** |
| board `_status_write_revision` | `9add1fbb7b0a430cb3e2b54f1e1f759b` | `03b5306760344078b6bee22239b97b6b`（04:16:40Z） | **是** |
| board SHA-256 | `6b02ed88…efefe` | `622a3f95…bd45a` | **是** |
| archive `index.json` SHA-256 | `5e5b59db…2c44a` | `9d496161…d5a4` | **是** |
| archive snapshot 筆數 | 6 | **8** | **是** |
| 盤點 JSON SHA-256 | `116f583e…b0685` | `116f583e…b0685` | 否 |
| 授權文件 SHA-256 | `134adcb8…2aba5` | `134adcb8…2aba5` | 否 |

結論與 PR #1231 收據 §8 的預告一致：**已提交的批次現在必定會被 apply 期的 baseline drift 閘拒絕，
那是設計行為**。它不能拿來 apply，必須在維護窗口內重新規劃。

看板漂移速度是可量測的：本輪不到 10 分鐘內就看到 `42ea72e2`（04:09）→ `56e71438`（04:13）→ `03b53067`（04:16:40），
其中 04:16:40 那次不是本 worker 造成的。**任何在窗口外規劃的批次，壽命是分鐘等級。**

### 1.6 38 個缺失 ID、依賴與候選來源對帳

以已合併的 planner CLI（未新增第二套 builder）對**當下** baseline 產生 plan-only 預覽：

```bash
uv run --frozen --python 3.12 python scripts/orchestrator/backfill_task_archive_snapshots.py \
  --archive-dir /home/lupin/odayplus/ai-task-archive/tasks \
  --repo . --ref origin/dev \
  --board /home/lupin/odayplus/ai-status.json \
  --recovery-inventory /tmp/odayplus-archive-incident.CYD1gq/RECOVERY_DEPENDENCY_CANDIDATES_20260906_1523.json \
  --authorization /tmp/odayplus-archive-incident.CYD1gq/RECOVERY_AUTHORIZATION_ZH_TW.md \
  --batch-out <預覽路徑> --recovery-owner Claude --recovery-reviewer Codex
```

exit 0。結果：**38 筆規劃、0 筆重建 done、38 筆 blocked 佔位、0 筆拒絕**；
分級 `merge_verified` 36 筆、`merge_claimed` 2 筆；`applicable: true`。
離線以 apply 期 validator 覆驗（純函式，未接觸 canonical）：`validate_recovery_batch(batch, for_apply=True)` 回傳 `[]`。

預覽批次交付於 `ORCH_ARCHIVE_HISTORY_APPLY_PREFLIGHT_20260907/plan-only-preview-batch.json`
（SHA-256 `7b32c6f0dd04f17ddbf35606f32cc6441b16d692f715416bf93bb921acfaea9a`）。
它釘住 `ref_commit da4b77d1…`、board revision `56e71438…`、8 筆 archive digest。
**這份預覽不是可套用的批次**：它的 board revision 在產出後 2 分 14 秒就已經被別人改掉了。
它的用途只有兩個——證明 planner 在新 baseline 上仍然給出保守結果，以及讓 Codex 先看到 38 筆的形狀。
另外要講明：這份預覽餵的是**未更正**的輸入盤點，所以它的 XR 佔位候選同樣還是誤配的 odayplus#996（見 B4）；
更正屬於維護窗口內的第 3 步，本輪刻意沒有先斬後奏地改輸入資料。

執行後複驗：archive `index.json` 摘要不變、`tasks/` 仍為 8 筆、board 未被 planner 寫入。

**看板現況對帳（本輪新量測）**：live board 25 筆任務中，`depends_on` 指向不存在目標的懸空邊
**恰好 44 條**，指向 **38 個相異 ID**，而這 38 個 ID **與復原清單完全相同**，
沒有第 39 個懸空目標。發出這些邊的是 12 個現存任務。
換句話說，這批 blocked 佔位會把看板上**全部**懸空依賴補齊，而且不多補一筆。

**35 verified_candidate 不是 35 筆 done。** 已合併的中立盤點（PR #1230）給的是
35 `verified_candidate`／3 `blocked`、high 27／medium 11；那個標籤自己的定義就是
「可作為復原候選送 reviewer 判定」。planner 這一側依四項證言（acceptance／ci／runtime／approval）
判定，38 筆全部缺齊四項，因此**第一批仍是 38 筆 blocked／`non_dispatchable`、0 筆 done**。兩者不衝突，也不得互相取代。

**候選來源對帳（含 XR 跨 repo 錯綁）**：逐筆 mapping 交付於同目錄
`candidate_reconciliation.json`（38 列，SHA-256 `d1a0cd62…5500`）。摘要：

- 三個來源（事故當下的輸入盤點／PR #1230 已合併證據／PR #1231 已提交批次）的 ID 集合完全相同（38）。
- 44 條 `known_dependents` 邊三方一致。
- **候選來源分歧 1 筆：`XR-EXT-OSS-FINAL-AUDIT-001`。** 這是 P1，細節見下節。
- `DPF-EMGI-LIVE-ROLLOUT-001` 三方一致指向 `alfloop-dev/oday-data-platform#62`。

### 1.7 存活的控制者盤點

不以 dashboard「沒有 running」作為判準；以下全部取自實際行程、pid 檔、crontab 與 systemd。

| 控制者 | 實測狀態 |
| --- | --- |
| Supervisor | **存活**。PID `1850381`，state `S`（**不是** SIGSTOP），pgid 1850381，cwd `/home/lupin/oday-plus-supervisor-runtime-64f3b2399442`，啟動於 2026-09-06 09:37:50 UTC。`/home/lupin/odayplus/.orchestrator/supervisor.pid` = 1850381，一致。 |
| Supervisor 的父行程 | PID `1850379`，是一個仍存活的 `/bin/bash -c`（來自某個 Claude session 的 shell snapshot，以 `setsid nohup` 手動重啟 Supervisor）。**Supervisor 目前不是由 watchdog 管理的**，這點對 rollout 的重啟路徑有直接影響。 |
| 存活 worker runner | 3 個 `claude-*`（含本 run `claude-20260907T040451Z-8483234b`），均為 Supervisor 的子行程，state `S`。 |
| **停止中的事故 Agy** | PID `1738813`（`worker_runner.py --run-id antigravity-20260906T063925Z-faa45ee9`），state **`Ts`（SIGSTOP）**，ppid 已成 1（孤兒）；子行程 PID `1738815`（`agy --model gemini-3.7-flash-high`）state `Tl`。啟動 2026-09-06 06:39:25Z，heartbeat **凍結在 2026-09-06T06:43:26Z**（21.5 小時前），但 status 檔仍寫 `status: "running"`、`finished_at: null`、`exit_code: null`。它的 task 是 `ORCH-CAPACITY-ARCHIVE-DEDUP-001`，該 task 早已終態並在 archive 內。**依驗收要求，本輪未對它送任何訊號，尤其未 SIGCONT。** |
| watchdog（cron） | **已暫停**。user crontab 僅剩一行被註解掉的 `# PAUSED ODP_ARCHIVE_INCIDENT_20260906_0643: * * * * * …run-supervisor-watchdog-live.sh…`，與事故當時保存的 `crontab.paused` 逐字相同。 |
| systemd | user 與 system 兩層都**沒有**任何 pantheon／supervisor／orchestrator／odayplus 相關 unit；`systemctl list-timers` 內亦無。 |
| `/etc/cron.d`、`/etc/crontab` | 無相關項目。 |
| canonical lock | `/home/lupin/odayplus/ai-status.json.lock` 存在、0 byte、`fuser` 查無持有者。 |

**live config 的派工姿態（`/home/lupin/odayplus/.orchestrator/config.json`，唯讀）**：
`ready_dispatcher.enabled: true`（派工**開著**）、`watchdog.enabled: true`（設定開著，但 cron 已停所以沒有被叫起）、
`capacity_controller.enabled: true`、`capacity_controller.sidecars.enabled: true`。

---

## 2. 本輪新量到的阻塞與風險（每一項都自己量過）

### B1（結構性阻塞）live writer 沒有 `archive_recovery_apply`

canonical launcher 執行的是 `runtime-current/scripts/ai_status.py`，該檔在 `64f3` 上
`archive_recovery_apply` 出現 **0** 次。所以**在 runtime 前推之前，正式 apply 在結構上不可能成立**。
出路只有既有的 `rollout_supervisor_runtime.py`（見 B2），不是繞過 launcher 直接跑 worktree 內的 `ai_status.py`
——那正是派工提示明令禁止、也會讓 stale branch code 覆寫 dashboard 的做法。

### B2（結構性阻塞）rollout 目前無法從 canonical checkout 執行

`--source-root /home/lupin/odayplus` 會在做任何事之前 exit 1（實測，見 §1.3）：
該 checkout 既 dirty（含兩個未提交的控制面檔案）又不在 dev tip（落後 1201 個 commit、且不是 dev 祖先）。
本輪**不**動它。維護窗口必須二選一，由 Codex 裁定：

1. 由有權限者先把 canonical checkout 清乾淨並前推到 dev tip（會動到兩個未提交的治理檔案，需另行裁決其去留）；或
2. 明確指定另一個乾淨且 HEAD == `origin/dev` 的 checkout 當 `--source-root`。
   本輪的 dry-run 二用的就是這條路，可行且已驗證。

### B3（順序阻塞）apply 的 preview 也需要 hold，先有 hold 才能預演

讀 dev tip 的 `command_archive_recovery_apply`：`_archive_recovery_hold()` 在 `--confirm` 判斷**之前**就被呼叫，
而且 baseline drift 檢查也在之前。因此

- 沒有一份合法、未過期、綁定該批次 SHA-256、且由 reviewer（非執行者）核准的 hold，
  **連不帶 `--confirm` 的 PLAN ONLY 預演都跑不起來**；
- baseline 一漂，預演一樣被拒。

所以順序只能是：**先停派工讓看板靜止 → 在 hold 下重新規劃批次 → Codex 核准新 hash 並簽出 hold → 才做 PLAN ONLY 預演 → 才 `--confirm`。**
不能指望「先預演看看再決定要不要開窗口」。

### B4（P1・資料正確性）XR 的候選在已合併 JSON 裡仍是誤配的 PR

`task_evidence_inventory.json` 內 `XR-EXT-OSS-FINAL-AUDIT-001`：

- `repository` = `alfloop-dev/odayplus`
- `pull_request.url` = `https://github.com/alfloop-dev/odayplus/pull/996`
- `candidate_mapping.verdict` = `mismatch`
- `recommendation` = `verified_candidate`，`recommendation_applies_to` = `corrected_candidate`
- 更正後的候選只存在於並列欄位：`corrected_candidate.url` = `…/oday-data-platform/pull/61`
  （head `b1824c979aca008da10aed01fbc0c0a269c581dc`、merge `7b0670d7b37e59e06bc9fea5b6003d1964be2c3c`）、
  `repository_correction.actual` = `alfloop-dev/oday-data-platform`

也就是說，README 描述的更正是對的，但**任何只讀 `repository` / `pull_request` 的下游消費者會取到錯的 repo 與錯的 PR**。
已提交批次（PR #1231）的 XR 佔位正是如此，它記的候選仍是 #996。

對分級**沒有**影響：兩個候選都不在 `origin/dev` 上可驗證，XR 兩種情況都落 `merge_claimed` → blocked 佔位。
但佔位裡留下的 provenance 是錯的，而「留下可稽核來源」正是本次授權的條件之一。

**維護窗口重新規劃前必須先修輸入資料**：餵給 planner 的 `read_only_recovery_inventory` 要把 XR 的
`candidates[0]` 換成更正後的 oday-data-platform#61。這是輸入修正，不是新增第二套 planner 或 writer。

### B5（P2）跨 repo 佔位不帶 `repository` 欄位

planner 產生的佔位 record 沒有 `repository` 欄。DPF 與 XR 兩筆實際屬於
`alfloop-dev/oday-data-platform`，落到看板後看不出來。對 blocked 佔位不致命，
但日後要把任一筆推進到 done 時，task→repo 解析會落回預設 repo。本輪**不**擅自擴充 planner 的 record schema，交由 Codex 裁定。

### B6（P2・apply 後的副作用）38 筆 blocked 佔位會成為 capacity sidecar 的候選父任務

`.orchestrator/sidecar_catalog.json` 的 `blocked_task_diagnostics` 模板條件是
`parent_statuses: ["blocked"]`，**沒有** `parent_task_ids`、**沒有** `parent_phase_match`。
`capacity_controller.sidecar_candidates()` 只在
`parent_status == "blocked"` 且 prose 命中 `HARD_GATE_MARKERS` 時跳過，而該集合是英文字串
（`human gate`、`manual approval`、`credential`、`license`、`production proof`、`legal`）。

復原佔位的 prose 是中文（`next` = 「等待人工裁決：補齊 acceptance/ci/runtime/approval 證據…」、
`waiting_for` = `Human/Ops`），**一個 marker 都不命中**。live config 又是
`capacity_controller.enabled: true` / `sidecars.enabled: true`。

因此 Supervisor 恢復後，這 38 筆會成為合格的 sidecar 父任務。
規模上限由 `max_new_per_wave: 3`、`max_active: 4`、`require_chair_approval: true` 拘束，不會一次生 38 個；
archive 內已有 4 筆 `*-SIDECAR-*` snapshot，證明這條路徑在正式環境確實會觸發。

隨 rollout 一起前推的 #1225 只擋「id 已在 archive 的 sidecar 不要重生」，
**不會**擋住為這 38 筆新生 sidecar。這一項需要 Codex 在開窗口前決定處置
（例如窗口內暫時關掉 sidecars、或接受並由 chair 逐一否決），本輪不擅自改 config。

### B7（已解決）PR #1227 已合併進 `dev`，但 live runtime 尚未 rollout

> **第 3 輪更新（2026-09-07T04:39Z）**：PR #1227 已於 2026-09-07T04:30:51Z 合併，
> merge commit `9094b4acfff45f116b90332467c91a63cdd6cd7a`，`origin/dev` 已包含該 commit。
> 本 task branch 已透過 base advance merge 納入此變更。

`ORCH-STATUS-SYNC-RUNTIME-AUTHORITY-001` / PR #1227：

| 項目 | 第 2 輪複量（04:30Z） | 第 3 輪確認（04:39Z） | 量測來源 |
| --- | --- | --- | --- |
| `state` | `OPEN` | `MERGED` | `origin/dev` 包含 `9094b4ac` |
| merge commit | — | `9094b4acfff45f116b90332467c91a63cdd6cd7a` | `git log origin/dev` |
| head SHA | `efc18ad294cf0890baf87ffbb743b2a1b2eca4f0` | 同（merge 的 second parent） | `git log --format=%P 9094b4ac` |
| `task-review-gate` | `success`／`Approved by assigned reviewer Codex`（`updated_at` 2026-09-07T04:25:46Z） | 同 | `gh api repos/.../commits/efc18ad.../status` |
| `orchestrator` CI | `success`（`completed_at` 2026-09-07T04:25:24Z，3m27s） | 同 | `gh api repos/.../commits/efc18ad.../check-runs` |
| change-scope／boundary／classify | `success`（同一顆 head） | 同 | 同上 |
| product／product-e2e-gate／performance-gate | `skipped`（同一顆 head） | 同 | 同上 |

變更範圍：`.orchestrator/status_transition.py`、`.orchestrator/test_supervisor.py` 與一份 evidence。

**已消除的阻塞**：PR #1227 已合併進 `dev`，不再是整合順序的外部前置條件。

**仍然成立的真實阻塞——live runtime 尚未 rollout**：雖然 `origin/dev` 已包含 PR #1227 的修正
（`.orchestrator/status_transition.py` 的 runtime 程式來源路徑修正），但 live runtime 仍停在
`64f3b2399442`（落後 dev tip），因此這些修正在 runtime rollout 之前不會生效。
rollout 仍是維護窗口內第 3 步的必要動作（見 §3）。

---

## 3. 最小整合順序（建議，待 Codex 裁定）

1. ~~**先合併 PR #1227**~~  **已完成（2026-09-07T04:30:51Z，merge commit `9094b4ac`）。**
   PR #1227 修的 `.orchestrator/status_transition.py` 已進 `dev`。本 task branch 已透過 base advance merge 納入。
   原先卡住合併的 finalize dispatch repo 路由缺陷已不再是阻塞。
2. **開維護窗口**（見 §4 第 1 步）。窗口成立的判準以 PR #1230／#1231 前置審查文件所列四條為準，
   本輪已把其中「其他控制者」那條量到底（§1.7）。
3. **在窗口內執行 rollout**，把 live runtime 前推到當時的 dev tip（現在包含 PR #1227 修正）。這一步解掉 B1。
4. **在窗口內重新規劃批次**（輸入先修 B4），由 Codex 核准新 SHA-256 並簽出 hold。
5. **PLAN ONLY 預演 → `--confirm`。**

順序不可對調：3 在 5 之前（否則子命令不存在），2 在 4 之前（否則 baseline 立刻漂），4 在 5 之前（B3）。

---

## 4. 維護窗口的 exact 命令交接

以下命令**本輪一律未執行**。標示為「需人工／有權限者」的步驟本 auto worker 無權執行
（live config 為 mutation-blocked，`gh pr edit` 等亦被權限判定擋下）。

### 第 1 步 — 建立真實的維護窗口（需人工／有權限者）

必須實際做到，不能只寫在文件裡：

- 停派工（`ready_dispatcher.enabled: false` 只是其中一項，**不涵蓋**既有 queue、`retry_due_workers`、approval／resume；
  依前置審查結論仍須逐項確認）；
- 排除其他控制者：確認 watchdog cron 維持暫停、無 systemd unit、無其他 launcher；
- worker drain：等現存 3 個 `claude-*` runner 自然結束；
- 處置 PID `1738813` / `1738815`（SIGSTOP 中的事故 Agy）。**維持 stopped 或依裁決終止，禁止 SIGCONT。**
- Supervisor 需真正退出（不是 SIGSTOP），且決策型 archive reader 停止。

以上任一未成立，**不得**宣稱窗口已 hold。本輪的唯讀量測與 `--dry-run` 都**不算**已 hold。

### 第 2 步 — rollout（窗口內，需人工／有權限者）

先擇定一個乾淨且 `HEAD == origin/dev` 的 `--source-root`（見 B2）：

```bash
SRC=<乾淨且 HEAD==origin/dev 的 checkout>
python3 "$SRC/scripts/orchestrator/rollout_supervisor_runtime.py" \
  --source-root "$SRC" \
  --runtime-link /home/lupin/oday-plus-supervisor-runtime-current \
  --runtime-parent /home/lupin \
  --status-root /home/lupin/odayplus \
  --config-path /home/lupin/odayplus/.orchestrator/config.json \
  --watchdog-pid-file /home/lupin/odayplus/.orchestrator/supervisor.pid \
  --dry-run          # 先看 target/branch/previous，確認無誤後再拿掉 --dry-run
```

拿掉 `--dry-run` 之後它會 SIGTERM 舊 Supervisor 並以
`scripts/run-supervisor-watchdog.sh --restart` 起新的。**注意現行 Supervisor 是手動 `setsid nohup` 起的、
父行程是一個仍存活的 bash wrapper（PID 1850379）**；重啟後的管理者會變成 watchdog 路徑，
這個轉換要在窗口內確認，不要事後才發現。

驗收（rollout 後、apply 前）：

```bash
readlink /home/lupin/oday-plus-supervisor-runtime-current       # 應為 …-da4b77d17515 或當時 dev tip
grep -c archive_recovery_apply /home/lupin/oday-plus-supervisor-runtime-current/scripts/ai_status.py   # 應 > 0
tail -1 /home/lupin/odayplus/scripts/ai-status.sh               # 應 exec runtime-current 的 ai_status.py
```

### 第 3 步 — 修正 planner 輸入（窗口內）

把 XR 的候選換成更正後的 oday-data-platform#61（B4）。輸入必須維持
`type: "read_only_recovery_inventory"`、`observed_at` 非空、`entries[].missing_id`、
`candidates[]` 需帶 `number`／`url`／`headRefName`／`mergedAt`／`mergeCommit.oid`
（缺任一即 `missing_candidate_provenance` 整批拒絕）。新輸入的 SHA-256 會成為新 baseline 的一部分。

### 第 4 步 — 在 hold 下重新規劃批次（窗口內）

```bash
uv run --frozen --python 3.12 python scripts/orchestrator/backfill_task_archive_snapshots.py \
  --archive-dir /home/lupin/odayplus/ai-task-archive/tasks \
  --repo <SRC> --ref origin/dev \
  --board /home/lupin/odayplus/ai-status.json \
  --recovery-inventory <第 3 步的更正盤點> \
  --authorization /tmp/odayplus-archive-incident.CYD1gq/RECOVERY_AUTHORIZATION_ZH_TW.md \
  --batch-out <窗口內的新批次路徑> \
  --recovery-owner <當時的執行者> --recovery-reviewer Codex
sha256sum <窗口內的新批次路徑>
```

`--recovery-owner` / `--recovery-reviewer` 必須是 canonical 註冊 agent，且 reviewer ≠ 執行者。

**已知並揭露**：planner 的 `RECOVERY_SOURCE` 常數硬寫為 `ORCH-ARCHIVE-HISTORY-RECOVERY-001`，
且 `validate_recovery_entry()` 會強制 `history_recovery.created_by` 必須等於它。
因此無論由哪個 task 重新規劃，批次的 `generated_by` 與每筆的 `created_by` 都會是
`ORCH-ARCHIVE-HISTORY-RECOVERY-001`。那是工具的 provenance，不是誤植，收據不應把它讀成「這批是那個 task 產的」。

### 第 5 步 — Codex 核准新 hash 並簽出 hold（需 Codex，非執行者）

hold 是一份文件，不是字串。apply 期逐項驗（`_archive_recovery_hold()`）：

```json
{
  "type": "task_history_recovery_maintenance_hold",
  "hold_id": "<非空>",
  "declared_by": "<非空>",
  "declared_at": "<UTC，不得在未來>",
  "expires_at": "<UTC，執行當下必須尚未到期>",
  "scope": "<非空>",
  "dispatch_paused": true,
  "batch_sha256": "<第 4 步新批次的 SHA-256>",
  "batch_approval": {
    "reviewer": "Codex",
    "approved_batch_sha256": "<同上，必須一致>",
    "approved_at": "<非空>",
    "source": "<非空，可追溯的核准來源>"
  }
}
```

閘的重點：`batch_approval.reviewer` 必須等於批次的 `recovery_actors.reviewer`，
且**不得等於執行 apply 的 actor**——同一個人不能既核准又套用。
`dispatch_paused` 不是 `true` 就直接拒。

### 第 6 步 — PLAN ONLY 預演，然後 `--confirm`（窗口內，透過 canonical launcher）

```bash
mkdir -p /home/lupin/odayplus/.orchestrator/recovery-receipts
CHK=/home/lupin/odayplus/.orchestrator/recovery-receipts/history-recovery-checkpoint-$(date -u +%Y%m%dT%H%M%SZ).json

# 6a. PLAN ONLY（不帶 --confirm）。仍會驗 hold 與 baseline；任一不成立即整批拒絕、零寫入。
AI_NAME=<執行者> "$PANTHEON_STATUS_ROOT/scripts/ai-status.sh" archive_recovery_apply \
  --batch <新批次> --maintenance-hold <hold.json>

# 6b. 正式套用。--confirm 一定要帶 --checkpoint，否則直接拒絕。
AI_NAME=<執行者> "$PANTHEON_STATUS_ROOT/scripts/ai-status.sh" archive_recovery_apply \
  --batch <新批次> --maintenance-hold <hold.json> --checkpoint "$CHK" --confirm
```

`--checkpoint` 的路徑受 admission 保護：不得指向或以 symlink／hardlink 別名指向 canonical board、
lock、activity log、`current-work.md`、archive `index.json`、archive `tasks/` 目錄及其內任一 snapshot、
批次檔、hold 檔、baseline 宣告的任何來源與授權文件，以及 `sync_all()` 管理的
`dashboard-bundle.json`、`docs-site/` 鏡像、`.orchestrator/state.json`、`approval-queue.json`、`planning-state.json`。
上面用的 `.orchestrator/recovery-receipts/` 不在受保護集合內。

---

## 5. rollback 邊界（要先講清楚才動手）

**rollout 這一步是可回復的。** `rollout_supervisor_runtime.py` 在換 symlink 與改寫 launcher 前先做快照，
任何一步失敗就還原 symlink 與 launcher，並嘗試重啟前一個 Supervisor；重啟失敗時它會
`restore_link` + `restore_file` 後才 `SystemExit`。舊 runtime 目錄不會被刪除，
`/home/lupin` 下目前保留 9 個歷史 runtime worktree，回退是「把 symlink 指回去」。

**apply 這一步不是原子的，也沒有 rollback。** archive snapshot 是各自獨立的檔案，board 由外層
canonical transaction 一次寫入，兩者不是同一個交易。因此：

- 失敗時只會留下 checkpoint，記的是**從磁碟讀回**的實際落地項目，並明講沒有執行 rollback；
- `board_persistence_verified` 只有在交易提交後 read-back 成功、且磁碟上的 `_status_write_revision`
  與本次寫入完全一致時才會是 `true`；
- 第二次 apply 必被 idempotency 閘拒（ID 已在看板或已有 snapshot）。

**部分失敗時的正確處置**：保留 checkpoint、**維持維護窗口不解除**，把 checkpoint 交回 Codex 判讀，
不得宣稱已 rollback，也不得在窗口外重試。

**本任務刻意不涵蓋的回復路徑**：canonical checkout `/home/lupin/odayplus` 那兩個未提交的控制面檔案
（`scripts/ai-status.sh`、`scripts/ai_status.py`）。本輪不修、不還原、不提交，只揭露它們會擋住 rollout（B2）。

---

## 6. 明確未完成／未宣稱

- **未執行 canonical apply。歷史未回填。** 38 筆全部維持缺四項證言，計畫中的處置是 blocked 佔位，不是完成。
- **未切換 live runtime**，未停止／重啟 Supervisor，未送任何訊號給任何行程（包含 SIGSTOP 中的 Agy），
  未改 watchdog、cron、systemd、live config、模型／認證／配額／角色／slot。
- **未建立、未簽署、未宣稱任何 maintenance hold。** 本輪的唯讀量測與兩次 `--dry-run` 都不構成 hold。
- 未補造 acceptance／ci／runtime／approval 四類 attestation，未補造歷史 owner／reviewer／approver／Human GO。
- 未 dispatch 產品 release、未啟用任何 provider、未更動 GCP IAM、未跑產品測試。
  本輪唯一跑過的可執行檔是 planner 的 plan-only 模式與 rollout 的 `--dry-run`，兩者的 exit code 都直接取自原 terminal。
- 未修 canonical checkout 的 dirty 檔案，未復原任何舊 code。
- PR #1227 已於 2026-09-07T04:30:51Z 合併進 `dev`（merge commit `9094b4ac`），但 **live runtime 尚未 rollout**；
  本輪未執行 rollout、未停止或重啟 Supervisor、未切換 live runtime。
- **B4（XR 候選誤配）本輪只做對帳與揭露，未修改任何已合併的證據檔案**；輸入盤點的更正屬於維護窗口內的第 3 步。

---

## 7. 交付檔案與範圍

| 路徑 | 內容 |
| --- | --- |
| `docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_APPLY_PREFLIGHT_20260907_ZH_TW.md` | 本文件 |
| `docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_APPLY_PREFLIGHT_20260907/candidate_reconciliation.json` | 38 列候選來源三方對帳（含 XR 誤配的機讀記錄） |
| `docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_APPLY_PREFLIGHT_20260907/plan-only-preview-batch.json` | 以已合併 planner CLI 對當下 baseline 產出的 **plan-only 預覽**；**不是**可套用的批次 |

只新增 docs 證據，未動任何 `.py`、config、workflow 或治理 manifest，因此不需重產
`docs/audits/code-boundary-inventory.csv`。未新增第二套 writer、planner、batch builder、
dispatcher 或 maintenance workspace manager。

## 8. 給 Codex 的待裁決清單

1. B2：rollout 的 `--source-root` 走哪一條（清理 canonical checkout／指定另一個乾淨 checkout）。
2. B4：XR 輸入候選的更正方式與由誰在窗口內執行。
3. B5：跨 repo 佔位是否需要 `repository` 欄位（會動到 planner 的 record schema）。
4. B6：38 筆 blocked 佔位觸發 capacity sidecar 的處置（窗口內關 sidecars／接受並由 chair 逐一否決）。
5. ~~B7 與 §3：PR #1227 前置條件~~ **已解決：PR #1227 已合併進 `dev`（2026-09-07T04:30:51Z）。**
   剩餘待裁決：live runtime rollout 的時機與執行者（rollout 是維護窗口內第 3 步的必要動作）。
6. 維護窗口的實際時間，以及窗口內由誰擔任 apply 的執行者（必須 ≠ Codex，因為 Codex 是核准者）。

在上述裁決與真實維護窗口成立之前，本任務停在這個 checkpoint，不重試、不循環、不擅自進入正式步驟。

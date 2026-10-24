# core-ops production 回滾計畫（DRAFT · 未簽署）

行號以 `origin/dev` @ `5d761526` 為準。PR #1565（head `d248118c`）的內容一律標為「**待合併**」。

## 1. 總覽：哪些自動、哪些手動

| 情境 | 機制 | 自動或手動 | 依據 |
|---|---|---|---|
| 部署腳本在 live E2E gate 通過**之前**失敗（migration、smoke、切換或 live gate） | `deploy_cloud_run_waji.sh` 的 EXIT trap `handle_deployment_exit`：依 deploy 前的 snapshot 判斷 recovery 模式，回復 API／Web 流量與 scheduler trigger | **自動** | `deploy_cloud_run_waji.sh:354-356`、`:368-422`；`cloud_run_release_traffic.sh:270-338`、`:374-384`、`:406-440` |
| live E2E gate 通過之後（`DEPLOYMENT_COMMITTED=true`），watch window 期間發現異常 | 沒有自動機制。規劃要求 on-call 人工判斷後執行 `bluegreen_release.py rollback` | **手動** | `deploy_cloud_run_waji.sh:1305`；`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:301-321` |
| staging 演練失敗 | `staging_lifecycle.py hold` 保留 release-scoped 資源 24 小時 | 自動 hold；之後的 cleanup 與延長由人工處理 | `deploy-dev.yml:1562-1591` |
| 資料庫 schema | migration 在切換前就執行，任何回滾都**不會**回復 schema；規劃明文禁止緊急回滾時做 destructive down migration | 不回復 | `deploy_cloud_run_waji.sh:822-877`；`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:321` |

**gate-6 的「proven」：** 以上任何一條路徑都沒有在 staging 或 production 實際執行過的 receipt，見 §5。

## 2. 首次 production release（production 從未有過 release）

### 2.1 `origin/dev` 目前的規則（已合併）

- `INITIAL_RELEASE_ELIGIBLE_ENVIRONMENTS = ("dev",)`，所以 `initial_release_recovery` 只允許 dev。
- staging 與 production 的首次 release 必須以 `rollback_release` 綁定一份先前可 admission 的 manifest。來源是 workflow 的 `rollback_manifest` 或 `vars.ODP_PREVIOUS_RELEASE_MANIFEST_PATH`（`docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md:118-126`；`.github/workflows/deploy-dev.yml:140-149`、`:627`）。
- 這樣綁定的 rollback 目標從未在 production 執行過（design `:122`）。

### 2.2 PR #1565 的變更（**待合併**，branch `task/ODP-CORE-OPS-FIRST-RELEASE-ROLLBACK-001`）

依該 branch 的 `docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md` §5（branch 上 `:118-220`）與 `docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md` §8.5（branch 上 `:337-366`）：

- **採方案 A：把 `initial_release_recovery` 擴充到 staging（建置目標）與 production（晉升目標）**（branch design §5.3 `:142-148`）。
  - 不採方案 B（拿已 admitted 的前一個 manifest 當 rollback 目標）。原因是 B 會在 manifest 裡寫一個在 production 從未存在過的 rollback 目標。
- **各環境的 recovery 語意（branch design §5.4 `:162`）：**
  - **production 與 dev**：`delete-candidate-zero-traffic`，刪除本次建立的 service、job 與 scheduler trigger，維持零流量。這等同 blue-green 的「移除 green、不切流量」（branch design §5.2 `:133`）。
  - **ephemeral staging**：`forensic-hold-release-scoped-namespace`。失敗時只 `hold`，不刪 candidate、不動流量，等 TTL 到期再整個 cleanup。
  - staging 封存的 manifest 另外以 `promotion_recovery` 寫明晉升到 production 時的動作是刪除 candidate。
- **manifest 的 `rollback_release` 只是宣告。** runtime 回復一律以 target 當下的狀態為準，兩個環境都不會去部署 `rollback_release` 指向的版本（branch design §5.2 結論）。
- **lineage 只能用一次。**
  - 只要 production 的 Supervisor lease store 裡曾為**其他** manifest 消耗過 lease，首次 release 分支就關閉。
  - staging admission 必須跨環境讀 production store，讀不到就拒絕（branch design §5.4 `:155-160`）。
- **待人工處理的後續項：**
  - **F1-a**：首次 production release 在 lease 消耗後失敗，而下一個候選是不同 manifest 時，lineage 檢查會拒絕它，它也沒有可綁的 rollback 目標。這是刻意的 fail closed，如何解除需要具名決策（branch design `:197`、`:219`）。
  - **F1-b**：staging environment 要設定 `ODP_PRODUCTION_RELEASE_LEASE_STATE_URI`，並讓 staging admission 身分能唯讀 production lease bucket。完成前，staging 首次 release 一律被拒絕（branch design `:195`、`:220`）。

### 2.3 部署腳本的實際行為（已在 `origin/dev`，與 PR #1565 無關）

- **判斷 recovery 模式。** deploy 前先捕捉 API／Web 的 snapshot。兩個 service 都不存在時，`release_recovery_mode` 回傳 `initial-release-cleanup`（`cloud_run_release_traffic.sh:319-338`）。
- **失敗時的處理：**
  - `restore_service_traffic` 會刪除本次建立的 bootstrap service（`cloud_run_release_traffic.sh:270-293`）。
  - `cleanup_initial_release_candidates` 刪除 candidate 的 migration、worker、scheduler job（`:340-372`）。
  - scheduler trigger 在部署前不存在時，回復動作就是刪除它（`:406-420`）。
  - log 會寫明「沒有前一版可回滾；recovery 是刪除 candidate 並維持零流量」（`deploy_cloud_run_waji.sh:382-384`）。
- **不會回復的部分：**
  - migration 已經對 production 資料庫執行，schema 不會被回復。
  - 首次 release 時 Web 是在切換那一步才開放 `allUsers`（`deploy_cloud_run_waji.sh:1194-1197`），service 被刪除後這個 binding 也跟著消失。

### 2.4 首次 release 已 commit 之後的「回滾」：**repo 內沒有程序（需人工決定）**

- live E2E gate 通過後，trap 就不再回復（`deploy_cloud_run_waji.sh:368-376`、`:1305`）。
- production 沒有前一版（blue）。`bluegreen_release.py rollback` 預期的是「切回 blue」（`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:311-318`），首次 release 沒有 blue 可以切。
- repo 內沒有「首次 release 已 commit 後如何下線」的 runbook。

需要 Human/Ops 決定並寫成程序，並在 staging 演練。以下選項都**未驗證、未演練**，只列出供決策：

  - (a) 撤銷 Web 的 `allUsers` invoker，讓服務不再公開；
  - (b) 刪除 API／Web service 與 job，同 initial-release-cleanup；
  - (c) 保持服務、只暫停 scheduler trigger，再以下一個修正 release 前滾。

  選 (b) 之後，下一個候選會碰到 PR #1565 的 F1-a lineage 限制。

## 3. 後續 release（production 已有前一版）

- **build 時綁定前一版。** 新 release 以 `rollback_release` 綁定前一個已核准的 manifest（`deploy-dev.yml:140-144`、`:627`）。PR #1565 合併後，首次 release 分支會因 lineage 規則關閉，所以第二個 release 起一律走這條路（branch design §5.4）。
- **commit 前失敗：自動回復。**
  - `release_recovery_mode` 回傳 `rollback`。
  - `restore_service_traffic` 依 snapshot 把流量還原成原本的 revision 配置（`cloud_run_release_traffic.sh:294-301`）。
  - 兩個 scheduler trigger 還原成 snapshot 的設定（`deploy_cloud_run_waji.sh:406-417`）。
- **commit 後異常：手動。** 依 `EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:299-321`：
  - 觸發條件是 PROD-OPS-05 的寬鬆初始值，而且只是人工判斷依據（`:301-309`）。
  - rollback owner 是 Human/Ops（`:307`）。
  - 回滾順序：暫停 scheduler → Web 與 API 切回 blue → job 改回 blue digest → snapshot pointer → 驗證舊版能讀新 schema → rollback smoke → 保留證據並開 incident（`:311-319`）。
  - 工具是 `bluegreen_release.py rollback --api-service --web-service --state-file ...`（`product_ops/deployment/bluegreen_release.py:1011-1018`）。
- **缺口：`--state-file` 沒有來源。**
  - workflow 的 `capture-state` 在**切換之後**執行，寫入 `${RUNNER_TEMP}/prod-bluegreen-state`（`deploy-dev.yml:1604-1619`），不在上傳清單內（`:1638-1671`）。
  - 部署腳本自己的 traffic snapshot 是暫存檔，結束時就刪除（`deploy_cloud_run_waji.sh:357-367`）。
  - 結果是 workflow 結束後，沒有任何保存下來的「切換前 blue 狀態」可以當 state file。
  - 操作者必須在 dispatch 前自行執行 `capture-state` 並保存結果，或從 Cloud Run revision 歷史重建。這個做法**未驗證、未演練**，需要人工決定程序。

## 4. 資料與 schema

- migration 是 expand-only 的假設：規劃要求不在緊急回滾中執行 destructive down migration，contract migration 延後到相容觀察期後的獨立任務（`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:280`、`:321`）。
- 部署腳本在切換前跑 `compatibility-smoke`，以舊 revision 驗證新 schema 的相容性。全新 target 則跑 `bootstrap-compatibility`（`deploy_cloud_run_waji.sh:851-877`）。
- 需要回到某個資料時間點時，只能走 Cloud SQL PITR 或備份還原。這部分沒有任何 production 演練，見 `backup-dr-impact.md`。

## 5. 演練：做過什麼、要做什麼

| 項目 | 現況 | 依據 |
|---|---|---|
| staging `rollback_rehearsal` stage | 程式存在，是第 7 個 stage | `product_ops/deployment/staging_lifecycle.py:2692` |
| 這個 stage 演練的到底是什麼 | 依 PR #1565 的盤點（**待合併**），它把 candidate 自己已釘選的 revision 切到同 image 的 probe revision，再還原。驗證的是「流量指標切換與還原」機制，**不是**「回到前一版」 | branch design §5.2 `:132` |
| staging 演練是否實際跑過 | 截至 2026-09-29 的評估，staging「沒有任何 admitted deploy job 成功過」。repo 內也沒找到之後的成功 receipt | `docs/evidence/runtime/ODP-NFR-RUNTIME-EVIDENCE-001/disposition.json:4`、`:59` |
| repo 內唯一一份含 `rollback_rehearsal` 的 verify receipt | 分類為 `non_execution_sample_schema_illustration`，不是實際執行 | `docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001/sample-secret-free-verify-receipt.provenance.json:7` |
| production 回滾、首次 release cleanup | 從未執行。production 的 GitHub deployment 數是 0 | `disposition.json:61-70` |
| 本機 E2E 回滾證明 | build 時執行，對象是 docker compose 加 SQLite，不是雲端 | `deploy-dev.yml:433-434`；`delivery_toolchain/e2e/verify_deployment_health_backup_rollback.py:32-34` |

**建議的演練內容（DRAFT，需人工核准範圍）：**

1. 在 staging 以 core-ops candidate 跑完 10 個 stage，取得 `rollback_rehearsal` 的實際 receipt。
2. 刻意讓 staging deploy 在 live gate 前失敗一次，驗證 `hold` 與 receipt。這是 PR #1565 合併後的 staging 語意。
3. 決定並演練 §2.4 的「首次 release commit 後下線」程序。
4. 決定並演練 §3 的 blue state 保存方式，以及 `bluegreen_release.py rollback --dry-run`（該 CLI 支援 `--dry-run`，`bluegreen_release.py:985`）。

## 6. 簽核（留白）

| 項目 | 簽署人 | 角色 | 日期 | 決定 |
|---|---|---|---|---|
| 本回滾計畫已審閱 | | Release owner | | □ 核准 □ 附條件 □ 退回 |
| 首次 release commit 後的下線程序（§2.4） | | Human/Ops | | □ 選 (a) □ 選 (b) □ 選 (c) □ 其他 |
| blue state 保存方式（§3） | | Human/Ops | | |
| rollback owner | | Human/Ops | | |

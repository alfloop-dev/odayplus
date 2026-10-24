# ODP-CORE-OPS-PROD-READINESS-PACK-001：core-ops production 上線準備包

> **狀態：DRAFT · 未簽署 · 不具效力。**
> 本資料夾只整理文件與證據對照，供負責人（蔡尚志）之後對 production release gates 做具名決定。
> 這裡沒有任何簽署、核准、收據、digest 或 run ID 是本 task 產生的；所有簽名欄都留白。

- Task：ODP-CORE-OPS-PROD-READINESS-PACK-001
- 準備者：Claude（LLM agent）· Reviewer：Codex
- 依據的程式基線：`origin/dev` @ `5d761526fa76e6f0fd3c4042b0de558997e7c8db`（2026-10-24 讀取）
- 待合併的相關變更：PR #1565（branch `task/ODP-CORE-OPS-FIRST-RELEASE-ROLLBACK-001`，讀取時 head `d248118cbc20df6d3ba578288cd1895e782ed9ac`）。本包一律標為「**待合併（pending merge）**」，不當作已上線行為。

## 1. 範圍

**涵蓋：**

- `core-ops` release profile 晉升到 production 所需的文件：部署計畫、回滾計畫、監控與值班、runbook 差異、備份與 DR、UAT、gate 證據對照、release notes 草稿，以及人工決策清單。
- `core-ops` 的定義見 `docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md`：
  - 不宣稱模型就緒，缺模型時如實拒絕（`:18-24`）。
  - 可以 admission 到 dev、staging、production；production 只接受 staging 封存的 manifest（`:22`、`:58-59`）。
  - 決策日期 2026-10-19，決策人是負責人（`:8-10`）。該決策**不是** production GO，也**不是** gate-3 簽署（`:12-13`）。

**不涵蓋，本 task 也沒有做：**

- 沒有改 product code。
- 沒有改 `docs/evidence/gates/RELEASE_GATE_REGISTRY.json`。
- 沒有觸發任何 workflow。
- 沒有呼叫 GCP 或 GitHub API。
- 沒有開 PR，也沒有 merge。

所有檔案只放在本資料夾。這是刻意的：改到 evidence 以外的路徑，會讓待發布的 release candidate 失效。依據是 admission 的 `check_candidate_ancestry`，它只允許 evidence-only descendant（`.github/workflows/deploy-dev.yml:744-756`、`:783-806`）。

## 2. 檔案索引

| 檔案 | 內容 | 對應 gate check |
|---|---|---|
| `deployment-plan.md` | workflow 實際執行的 production rollout 步驟（build → registry/gates → Supervisor lease → deploy → 0% 讀回 → 切換 → watch），以及每一步的人工動作 | gate-6「release note and deployment plan completed」 |
| `rollback-plan.md` | 首次 production release 與後續 release 的回滾：哪些是自動、哪些是手動、staging 如何演練 | gate-6「rollback plan completed, reviewed, and proven」；gate-3「human approval and rollback path recorded」 |
| `monitoring-oncall-plan.md` | repo 內實際存在的監控與告警、已記錄的 watch 義務，以及待人工填寫的值班表 | gate-6「monitoring watch window and on-call route assigned and live」 |
| `runbook-core-ops.md` | core-ops 的營運 runbook 差異：模型被拒絕時的現象、release identity 驗證、已知限制 | gate-6「runbook updated for changed operational behaviour」 |
| `backup-dr-impact.md` | 依 repo 證據整理的備份、還原、DR 現況，以及 production 的變化 | gate-6「backup/DR impact reviewed」 |
| `uat-plan.md` | core-ops 的 UAT 範圍、角色與帳號、對應既有 E2E spec 的測試案例、P0/P1 準則、空白簽核欄 | gate-5 UAT 三項 |
| `gate-evidence-map.md` | gate-2、gate-3、gate-5、gate-6 每個 required_check 的狀態（已有證據／部分／缺／需人工決定） | 全部 |
| `release-notes-draft.md` | 面向使用者的 core-ops 上線說明草稿 | gate-6「release note」 |
| `human-decisions-checklist.md` | production GO 前後所有需要具名的人工決策，簽名欄留白 | 全部 |

## 3. 哪些已證明、哪些沒有

### 3.1 repo 內已有、可引用的（程式或離線測試層級）

- **profile 機制。** `core-ops` 的晉升路徑、admission parity 與竄改拒絕都有離線測試（`docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md:215-221`）。這些只是測試輸入，不是 live 證據（同檔 `:215`）。
- **production 部署路徑（程式層）。** 依序是：
  - deploy 前捕捉流量 snapshot；
  - 以 `--no-traffic`／private 部署 candidate；
  - 以 tag URL smoke；
  - 100% 切換；
  - live E2E gate；
  - 失敗時由 EXIT trap 回復。

  位置是 `product_ops/deployment/deploy_cloud_run_waji.sh:813-820`、`:879-906`、`:1086-1147`、`:1171-1198`、`:1211-1305`、`:368-422`。
- **staging 演練程式。** 10 個 stage，最後一個是完整的 live E2E gate（`product_ops/deployment/staging_lifecycle.py:2685-2698`）。
- **release identity 端點。** 程式在 `apps/api/oday_api/main.py:897-912`。

### 3.2 沒有證明的（沒有任何 receipt）

- **core-ops 從未成功部署到 staging 或 production。** gate-2、gate-3、gate-5、gate-6 都是 `blocked`，`receipts` 都是空的（`docs/evidence/gates/RELEASE_GATE_REGISTRY.json:249/280`、`:293/319`、`:400/435`、`:449/491`）。registry 目前只綁 dev candidate `6140d0ef…`，stage 是 `candidate-built`（同檔 `:15-22`）。
- **沒有任何 live E2E gate 通過紀錄**，staging、production、core-ops 都沒有。
- **沒有效能量測、UAT 簽核、production 備份讀回或還原演練。**
- **production 告警與值班路由不存在。** Terraform 沒有任何 Cloud Monitoring 資源，詳見 `monitoring-oncall-plan.md`。
- **production deploy job 在現行 workflow 下會在 commit 之後失敗。** production 的 `capture-state` 把 `--project`、`--region` 放在子命令之後（`.github/workflows/deploy-dev.yml:1609-1615`），但 CLI 要求它們放在子命令之前（`product_ops/deployment/bluegreen_release.py:985-989`）。argparse 會以 exit 2 結束，這時流量已經切到 100%（`deploy_cloud_run_waji.sh:1305`）。`deploy` job 結果是 failure，`staging_closeout` 被 skip（`deploy-dev.yml:1681`）。這是既有 blocker，修正屬於另一個 task，詳見 `deployment-plan.md` §5 第 1 點。
- **production watch receipt 沒有程式產生它。** `staging_closeout` 只會讀它（`.github/workflows/deploy-dev.yml:1793-1798`）。
- **首次 production release 的回滾語意仍待合併。** PR #1565 尚未合併。

## 4. 未驗證項目（本 task 無法查證）

| 項目 | 為何未驗證 |
|---|---|
| GitHub `production`、`production-build`、`staging` environment 目前實際有哪些變數 | 本 task 不呼叫 GitHub API。最後一份 repo 證據是 2026-09-17 的讀取（`docs/evidence/runtime/ODP-PROD-RUNTIME-RELEASE-PATH-001/README.md:18`、`:197-201`） |
| production GCP 資源的現況（Cloud SQL、VPC、custom domain、Artifact Registry 權限） | 本 task 不呼叫 GCP。最後一份 production Cloud SQL 讀回是 2026-09-16（`docs/evidence/runtime/ODP-PROD-NETWORK-PARITY-PLAN-001/README.md:220-237`） |
| PR #1565 的合併狀態與最終內容 | 本包只讀了 branch head `d248118c`，內容可能在合併前再改 |
| production deploy 使用 staging build 產生的 image 時，production runtime 能否從該 Artifact Registry 拉取 | repo 內找不到跨專案 `artifactregistry.reader` 的設定或證據 |
| `ODP-POSTDEPLOY-WATCH-CLOSEOUT-001` 的 task 定義與目前 owner | task 定義不在 repo 內，而 repo 內兩處記載的 owner 不一致（見 `monitoring-oncall-plan.md` §3） |

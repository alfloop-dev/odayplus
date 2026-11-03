# ODP-EPHEMERAL-STAGING-ROLLOUT-001 — Real preflight blocker record

> **這不是 staging deployment receipt。** 本目錄目前只包含 `dry-run` / `blocked` / `desired-state` 證據；沒有任何 `staging-verified` 收據，也沒有宣稱 migration、E2E、worker、scheduler、backup、restore 或 rollback 已在遠端環境執行。

## 目前結論

本輪於 2026-08-26 01:11:53Z 重新執行 real GCP/GKE/GitHub preflight，但仍無法安全建立「隔離 staging 並完成全套 rehearsal」。原先提交的 JSON 證據已移除，因為它們把 placeholder digest 與未執行的資源狀態寫成 `PASSED` / `READY` / `staging-verified`。本輪沒有執行 Terraform apply、Cloud Run deploy、GKE apply、migration、E2E、job、backup/restore 或 rollback。

唯一有效的 task-scoped record 是 [`staging-rollout-dry-run.json`](staging-rollout-dry-run.json)。它明確記錄：

- source `RELEASE_MANIFEST.json` 的 11 個 digest / reference 是明顯 placeholder，雖然 self-hash 可重算，不能當成 artifact provenance；
- source manifest 的 candidate SHA 是舊版本，且不是目前 dev candidate；
- 先前證據的 `ghcr.io/alfloop-dev/...` registry 與本 repo Runtime Release 的 `${GCP_REGION}-docker.pkg.dev` 路徑不一致；
- 沒有成功的 staging workflow dispatch、run URL 或 remote staging proof；Cloud Run 沒有 staging service，GKE staging namespace `oday-staging-8c72b54` 雖存在但沒有 workload；
- GCP project `odayplus-runtime-20260825` 與 billing readback 成功；KMS key `projects/odayplus-runtime-20260825/locations/asia-east1/keyRings/oday-runtime/cryptoKeys/runtime`、private IP 的 RUNNABLE `oday-staging-sql`、`oday` database／`oday_app` user、snapshot bucket 與 release-labelled namespace 已觀測。snapshot bucket 使用上述 CMEK、public access prevention、24 小時 retention、7 天 soft delete、UBLA 與 versioning，但目前是空 bucket，尚無 masked snapshot object；namespace `default-deny-external-egress` policy 與 KSA `oday-staging-runtime` → GSA `oday-staging-runtime@odayplus-runtime-20260825.iam.gserviceaccount.com` binding 已觀測。staging Scheduler jobs 為空；
- Artifact Registry `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev` 只觀測到 data-platform/mlflow packages，沒有 Runtime Release 產生的 ODay Plus API/Web/Worker/Scheduler handoff；
- GitHub `staging` environment 有 2 位 required reviewers 與基本 WIF/project vars，但 GitHub Actions 目前註冊的是 `Deploy Dev` / `Deploy/Verify Staging`，沒有註冊 source manifest 所稱的 `Runtime Release`；仍缺 admission lease verifier vars、15 個 Runtime Release vars 與所有 environment secrets；
- 在真實 digest、成功 workflow run、隔離 foundation、masked snapshot 與 staging authority 到位前，所有 migration、E2E、job、backup/restore、rollback、provider-off、TTL 結果都必須保持未觀測。

截至 2026-08-26 01:18:58Z 的 task-scoped 唯讀重查確認上述 blocker 沒有漂移：`origin/dev` 仍為 `8c72b54a9fb2a0853ac17b89e51f30ef5eb969f3`；Actions 仍只有 `Deploy Dev` / `Deploy/Verify Staging`；staging environment 仍沒有 15 個 Runtime Release variables、lease authority 或 environment secrets；Artifact Registry 仍只有 `oday-data-platform` / `oday-mlflow`，Cloud Run 仍只有 `oday-mlflow`；snapshot bucket object listing、Scheduler jobs 與 staging namespace workloads 仍為空。這次重查仍未執行 apply、deploy 或 rehearsal。

## 2026-09-29 重新判定：阻塞點移到 staging Data Gate

這次只讀 `origin/dev`（`0d569940`）與看板，沒有呼叫 GCP，也沒有執行 build、deploy 或 rehearsal。

- 上游 dev rollout 已完成：`ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 已 done（PR #1107，merge `0d569940`）。`docs/evidence/gates/RELEASE_GATE_REGISTRY.json` 的 `release.decision=go`、candidate `ee06d1d8294464f1eb7231f2b06505348a61cba2`、manifest `sha256:8ee919d67fc89768c7ae8912ecfce706dc1b2a0fbb2e3da1a14b5e87c1fae80e`，但 `admission_target=dev`。所以上方 B1/B3/B6（placeholder digest、stale candidate、沒有 image handoff）在 dev 這一層已經被真實 build 取代。這不代表 staging 已獲准。
- staging 准入要看 `gate-2`（Data Gate，`admission_target=staging`）。目前它是 `blocked`、`receipts=[]`。它的 blocker 要求對 live 部署跑 `check_live_production_data.py`，並指出 masked snapshot 由 `DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001` 產生。
- rollout plan 第 88 行與第 233 行都要求 staging 從核准的 masked snapshot 還原。`DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001` 目前仍是 `todo`，它依賴的 `DPF-EMGI-LIVE-ROLLOUT-001`、`ODP-RELEASE-ROLLBACK-DATA-HANDOFF-001`、`DPF-BOUNDED-CAPTURE-RETENTION-EXECUTION-001` 也還沒完成。
- 處置：已把 `DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001` 加進本 task 的 `depends_on`，用來停止空轉重派。等它 done、`gate-2` 有綁定 candidate 的 receipt，再由 Human/Ops 以 staging 為目標登記 lease request，才能開始 rehearsal。
- 在那之前，本目錄仍然沒有 `staging-verified` 收據。

## 2026-10-26 base composition 重查（Antigravity3）

本輪把 `origin/dev`（`09ede4db62d7706e492e66c227f7f1446bb60be7`）以一般 merge 合入 task branch（未 reset、未 force），只解了 `docs/audits/code-boundary-inventory.csv` 與 `tests/release/test_release_manifest.py` 兩處衝突（兩邊內容都保留）。依賴清單已改為 core-ops release profile 路線，`DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001` 不再是本 task 的依賴，所列依賴全部 done。

合入後 `docs/evidence/gates/RELEASE_MANIFEST.json` 已是 dev Runtime Release 產出的真實 manifest（release `odp-6140d0ef633c`、candidate `6140d0ef633cbf94522c171d9927103ab200257f`、manifest `sha256:d286dd00…8108`，0 個 placeholder digest，self-hash 可重算）。所以 [`staging-rollout-dry-run.json`](staging-rollout-dry-run.json) 的 `release_manifest` 已改成讀這份 manifest，舊的 11-placeholder 紀錄移到 `superseded_record`。各 blocker 都加上 `status`：

| Blocker | 狀態 | 說明 |
|---|---|---|
| B1 placeholder digest | `resolved_at_dev_layer` | 只代表 source manifest 不再是 fixture，不代表 staging 已准入。 |
| B2 registry provenance | `resolved_at_dev_layer` | manifest 只引用 `asia-east1-docker.pkg.dev/.../oday-plus-dev/...@sha256`。 |
| B3 candidate stale | `open` | candidate 不是目前 dev head，而且最近的 dev run 都失敗（見 B7）。 |
| B4 staging workflow / authority | `partially_resolved` | staging environment 現有 51 個 variables（含 `ODP_RELEASE_LEASE_PUBLIC_KEY`、`ODP_RELEASE_LEASE_STATE_URI`、`ODP_STAGING_*`），仍有 2 位 required reviewers；從沒觀測到 `environment=staging` 的 Runtime Release dispatch。 |
| B5 staging foundation | `open` | 本機 gcloud 憑證要互動式重新登入，所以本輪沒有做 GCP readback，也沒有繞過。 |
| B6 app image handoff | `resolved_at_dev_layer` | manifest 已有 api/web/worker/scheduler/migration digests。 |
| B7（新）dev Runtime Release 失敗 | `open` | 2026-10-25T21:14Z 到 2026-10-26T02:45Z 的 4 次 dev run 都失敗；run `40260645830` 停在 `Deploy Cloud Run by immutable digest`，因為 migration Cloud Run Job 執行失敗，之後已還原 traffic。origin/dev `09ede4db` 的 run `40265674132` 在 readback 時還在跑。 |

結論不變：還沒有任何 staging 部署、migration、E2E、job、backup/restore 或 rollback 被觀測到，本目錄仍不能有 `staging-verified` 收據。要開始真正的 rehearsal，必須先有成功的 dev Runtime Release（含 migration），再由 Human/Ops 登記 staging lease 並核准 `environment=staging` dispatch。

本輪驗證：

```text
git merge --no-ff origin/dev   # 解兩處衝突
gh workflow list --all
gh run list --workflow 'Deploy/Verify Staging' --limit 10
gh run list --workflow 'Deploy Dev' --limit 5
gh run view 40260645830 --log-failed
gh variable list --env staging --json name
gh secret list --env staging --json name
gh api repos/alfloop-dev/odayplus/environments/staging
gcloud run services list ...   # 失敗：需要互動式 reauth，未繞過
uv run --python 3.12 pytest -q tests/ops/test_staging_rollout.py tests/release/test_release_manifest.py
```

## 2026-11-02 staging lease 簽發失敗（Claude）

candidate 已改為 `730328798ce8456ae698bf5ade35d5e75f5ff662`（staging build run `41363014110`，manifest `sha256:2c9159d5…5830`），registry 由 `ODP-STAGING-ADMISSION-730328798-001`（#1635，dev `2de5a4bf`）綁定 staging admission。Human/Ops 登記 lease 請求 `HUMANOPS-STAGING-LEASE-20261102`（01:56:23Z 核准，07:56:23Z 到期）。

supervisor 簽發結果（從 live 看板 `release_lease_issuance` 讀回）：

| 時間（UTC） | state | 內容 |
|---|---|---|
| 02:25:17 | `issuing` | 已保留請求，`dispatch_ref_sha` 尚未寫入，沒有 lease_id |
| 02:31:04 | `blocked` | `issuing reservation has no durable lease record`；`dispatch_ref_sha=2de5a4bf…` |

第二輪走 issuing recovery，GCS lease state 的查詢有成功（沒有出現 `durable GCS lease state is unavailable`），但 store 裡沒有這個 task 的 lease。代表第一輪保留後，在寫入 GCS 之前就結束了。`.orchestrator/release_lease_integration.py` 在這段有幾個不記錄就 `continue` 的分支（`_status_still_reserved` 回 `None`），supervisor log 也沒有對應紀錄，所以第一輪實際停在哪裡**未確認**。同一份程式在 2026-09-30、10-02 都有成功簽發過，不是每次都會失敗。

依設計，同一 fingerprint 的 `blocked` 不會重試，舊 nonce 也不能再用。要重試只能由 Human/Ops 用新的 `approval_id` 與新的 nonce 重新登記請求。`gh run list --workflow deploy-dev.yml` 確認沒有 `environment=staging` 的 run 被觸發；02:29:55Z 的 run `41378043338` 是 `2de5a4bf` 的 dev 自動部署，與本 task 無關。

本輪也把 `origin/dev` `2de5a4bf` 以一般 merge 合入 task branch（無衝突，merge tree 與 `git merge-tree --write-tree` 相同）。沒有執行任何 deploy 或 rehearsal，仍然沒有 `staging-verified` 收據。

## 2026-11-02 第一次 staging dispatch 失敗：runner 沒有 terraform（Claude）

`external_command_timeout_seconds` 調為 180 並重啟 supervisor 後，Human/Ops 用新 nonce 登記 `HUMANOPS-STAGING-LEASE-20261102B`。這次簽發成功：lease `lease-50bffe872ccd71b82ff2dac8a9e921d0` 於 04:11:20Z 簽發，dispatch 到 dev `2de5a4bf` 的 `deploy-dev.yml`，產生 run [`41387164145`](https://github.com/alfloop-dev/odayplus/actions/runs/41387164145)。B8 改為 `partially_resolved`：簽發器修正只觀測到一次成功，而這張 lease 只授權一次 staging deploy，已用掉。

run 的結果（`gh run view 41387164145 --json jobs`、`--log-failed`）：

| Job | 結果 |
|---|---|
| Validate release phase inputs | success |
| Verify the Supervisor lease authorises this deploy | success |
| Deploy the admitted artifact by immutable digest | **failure** |
| Verify production watch and clean up ephemeral staging | skipped |

失敗在 `Execute ephemeral staging lifecycle create`：`Creation executor failed: [Errno 2] No such file or directory: 'terraform'`。`Hold ephemeral staging resources on failure` 也因同一個 `FileNotFoundError` 結束。`staging_lifecycle.py` 的 create／verify／hold／cleanup 都要呼叫 Terraform CLI，但 `deploy-dev.yml` 的 deploy job 與 `staging_closeout` 都沒有安裝它，GitHub-hosted runner 也不內建（新 blocker **B9**）。

沒有建立任何資源，依據有三項：

1. log 顯示 executor 在第一次呼叫 terraform 時就失敗；`Persist staging hold state` 沒有複製任何 sidecar。
2. 唯讀 gcloud readback（[`run-41387164145-gcp-readback.txt`](run-41387164145-gcp-readback.txt)）：沒有名為 `stg-odp-730328798ce8` 的 Cloud Run service／job、Cloud SQL database、service account、Pub/Sub topic、secret、bucket 或 Scheduler job；recovery bundle prefix 沒有物件。
3. 限制：本機帳號沒有 terraform state bucket 的 `storage.objects.list` 權限，那個 prefix 沒有讀到。

run 上傳的 receipt 原樣保存為 [`run-41387164145-staging-lifecycle-create.json`](run-41387164145-staging-lifecycle-create.json)（sha256 `d549611b…3f52`，artifact `13116146237`）。注意：它把 19 個**計畫中**的資源列成 `status=failed`，並帶 `created_at` 與 `remediation_required=true`。這些是 lifecycle 預定建立的清單，不是觀測到的資源，不需要人工清理。

修正與後續：

- 前置修正 `ODP-STAGING-DEPLOY-TERRAFORM-SETUP-001`（PR #1637，2026-11-02T09:18:56Z 以 merge commit `f81b9373` 合入 dev）：在 deploy job（只限 staging）與 `staging_closeout` 第一次呼叫 lifecycle 前加 `hashicorp/setup-terraform@v3`，版本與 `ci.yml` 同為 1.9.8，關閉 wrapper（lifecycle 會解析 `terraform output -json`）。另加一條 contract test，拿掉步驟或打開 wrapper 都會變紅。
- `check_candidate_ancestry` 不接受 candidate 之後 dev 上有 workflow 改動，所以 #1637 合併後 candidate `730328798` 不能再用。順序：#1637 合併 → 在含它的 dev commit 跑 staging build → staging admission task 綁定新 candidate → Human/Ops 用新 approval_id／nonce 登記 lease。
- 另外確認 `staging_lifecycle.py` 只會呼叫 `terraform` 與 `gcloud` 這兩個外部執行檔；live E2E gate 在只有 stdlib 的 Python 3.12 下可以正常載入（runner 用系統 `python3` 執行 lifecycle）。所以就靜態檢查來看，沒有發現第二個缺少的工具。下一個失敗點仍要等實際 run 才知道。

仍然沒有 staging 部署、migration、E2E、worker／scheduler、backup／restore 或 rollback 被觀測到，本目錄沒有 `staging-verified` 收據。

## 2026-11-02 重查：terraform 修正已合入，等待新 candidate（Claude）

10:05Z 起的 owner dispatch 唯讀重查，結果如下：

- PR #1637 已於 09:18:56Z 合入 dev（merge commit `f81b9373`）。`origin/dev` 現為 `ccb9553b`，`deploy-dev.yml` 的 staging deploy job 與 `staging_closeout` 都有 `hashicorp/setup-terraform@v3`。
- `f81b9373` 之後沒有 staging build。`deploy-dev.yml` 最近一次 staging run 仍是失敗的 deploy `41387164145`（candidate `730328798`），最近一次 staging build 是 `41363014110`（同一 candidate）。`730328798` 之後 dev 有 workflow 改動，不能再通過 ancestry 檢查。
- 本 task branch 已用 merge 吸收 `origin/dev` `ccb9553b`。merge tree `220360aa` 與 `git merge-tree --write-tree` 的結果相同。

這次沒有 dispatch build、deploy 或建立 lease。staging build 會被 auto-mode 判為 deploy 動作，要由使用者觸發。後續順序：

1. 由使用者在 `origin/dev`（含 `f81b9373`，例如 `ccb9553b`）上執行 `deploy-dev.yml` 的 staging build。
2. staging admission task 綁定新 candidate。
3. Human/Ops 用新的 approval_id 和 nonce 登記 lease。
4. 本 task owner 接續 staging deploy 與 rehearsal。

## 2026-11-02 新 candidate `ccb9553b` staging build 成功（Claude）

使用者於 10:11:32Z 在 `origin/dev` `ccb9553bb1511ec746e69a831a1a8f5ad483a91f`（含 `f81b9373`）dispatch `deploy-dev.yml` staging build，run `41426783637` 於 10:21:24Z 以 `success` 結束（`gh run watch --exit-status` exit 0）。`Validate release phase inputs` 與 `Build once and publish the immutable artifact handoff` 成功；lease、deploy、closeout 三個 job 依 build phase 設計為 skipped。

Build handoff（從 run artifact 下載後讀取）：

- `RELEASE_MANIFEST.json` 檔案 sha256：`6e649d5e1e221fd386ef29da4f3e6166b42f370bc116a7a2a04adb1e1838fa34`（artifact `runtime-release-manifest-ccb9553b…`，id `13132826575`）。`candidate_sha` 為 `ccb9553b…`，`external_sources_expected_enabled` 為空。
- api：`oday-staging-api@sha256:8a425e18362fd34dfbbafa39bd097a7f6fb07aee2578e02fdb82663d031e3efc`
- web：`oday-staging-web@sha256:112239d226d28bacef19e74df57d025ae36e15cb2b3310b3cf6bfccb62bdb774`
- worker（migration 共用）：`oday-staging-worker@sha256:ee7643e84c6c9aceebb2ddb33849bc5714fcbf661c9c8543aabfc29d7570536b`
- scheduler：`oday-staging-scheduler@sha256:025c36d08711a7099aff14c27eaa0beb5a1ced6a47ebb7d8d59f58e71789d22c`

以上 image 都在 `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/`。這只表示 build 階段完成，沒有部署、沒有 rehearsal，本目錄仍然沒有 `staging-verified` 收據。上一節的第 1 步已完成，剩下：

1. staging admission task 綁定 candidate `ccb9553b` 與上列 manifest。
2. Human/Ops 用新的 approval_id 和 nonce 登記 lease。
3. 本 task owner 接續 staging deploy 與 rehearsal。

## 2026-11-02 第三次 lease 簽發失敗：簽章私鑰讀不到（Claude）

`ODP-STAGING-ADMISSION-CCB9553BB-001`（#1640，dev `3672d00a`）已把 staging admission 換綁到 `ccb9553b`。Human/Ops 依負責人 HD-10c 登記 `HUMANOPS-STAGING-LEASE-20261102C`（build run `41426783637`）。supervisor 簽發結果（live `ai-activity-log.jsonl`）：

| 時間 (UTC) | 狀態 | 內容 |
|---|---|---|
| 14:20:53 | `issuing` | 已保留請求，fingerprint `sha256:c063e132…97c2`，`errors` 空 |
| 14:21:58 | `blocked` | `Secret Manager signing key is unavailable`；`dispatch_ref_sha=3672d00a…` |

`dispatch_ref_sha` 已寫入，代表請求驗證、exact binding、ancestry、nonce 與 GCS lease state 都通過（上一輪 B8 的 sync 逾時沒有再發生），停在 `release_lease_integration.load_private_key_from_secret_reference`。這個函式跑 `gcloud secrets versions access latest`，stderr 丟棄。

本機 gcloud log 有完整原因（`~/.config/gcloud/logs/2026.11.02/14.20.57.727369.log`、`14.21.35.493593.log`、`14.21.36.582927.log`，三次都是這個 secret access 指令）：

```text
(gcloud.secrets.versions.access) There was a problem refreshing your current auth tokens: Reauthentication failed. cannot prompt during non-interactive execution.
```

supervisor（pid 2002345，`/usr/bin/python3.12`，`HOME=/home/lupin`，沒有 `CLOUDSDK_CONFIG`／`GOOGLE_APPLICATION_CREDENTIALS`）共用本機 gcloud 設定，active account 是 `deborah.lu@dev.cctech-support.com`。同一帳號在 04:11 讀 secret 成功（lease `…20261102B` 簽發），之後 reauth 期限到了，背景程序無法互動重新驗證。這不是程式缺陷，也不是 IAM 問題。

本輪沒有讀 secret 內容、沒有呼叫任何 GCP API，只讀本機 gcloud config／log 與 activity log。blocked fingerprint 不會自動重試，所以要：

1. 使用者在這台機器以互動方式重新登入 `deborah.lu@dev.cctech-support.com`（`gcloud auth login`），確認非互動的 `gcloud secrets versions list --secret <id> --project <number>` 不再要求 reauth。
2. Human/Ops 用新的 approval_id 和 nonce 登記 lease，之後 reopen 本 task。
3. 本 task owner 接續 staging deploy 與 rehearsal。

這張 lease 簽發前 reauth 期限可能再到期；若要長期避免，需要讓 supervisor 用不受 reauth 限制的身分（例如有 secret accessor 權限的 service account）讀簽章私鑰，這是 Human/Ops 的決定，本 task 不改。

## 2026-11-02 第二次 staging dispatch 失敗：deployer 沒有建立資源的權限（Claude）

負責人重新登入後，Human/Ops 登記 `HUMANOPS-STAGING-LEASE-20261102D`。lease `lease-64c1dd57d7a7d73381a4d4245848aa0c` 於 14:39:48Z 簽發（candidate `ccb9553b`、manifest `sha256:93ffa139…2362`、`dispatch_ref_sha=3672d00a`），dispatch 出 run [`41464086945`](https://github.com/alfloop-dev/odayplus/actions/runs/41464086945)。B10 這次已解除，但使用者憑證的 reauth 期限仍會再到。這張 lease 已用掉。

| Job | 結果 |
|---|---|
| Validate release phase inputs | success |
| Verify the Supervisor lease authorises this deploy | success |
| Deploy the admitted artifact by immutable digest | **failure** |
| Verify production watch and clean up ephemeral staging | skipped |

這次 `Set up Terraform for the staging lifecycle` 成功，terraform 有實際執行，B9 在 deploy job 這一段已解除（closeout job 尚未跑到）。失敗在 `Execute ephemeral staging lifecycle create`：`terraform apply -input=false failed (exit 1)`。receipt 只留 stderr 最後 8 行（`google_secret_manager_secret.staging_web_session`，main.tf 第 517 行），看不到原因。

原因從 Cloud Audit Logs 讀到（唯讀 `gcloud logging read`，見 [`run-41464086945-gcp-readback.txt`](run-41464086945-gcp-readback.txt)）。14:43:05Z 部署身分 `github-deployer@odayplus-runtime-20260825.iam.gserviceaccount.com` 的 9 筆 admin activity 全部是 `PERMISSION_DENIED`（code 7），沒有一筆成功：

| 權限 | 次數 | 對應資源 |
|---|---|---|
| `iam.serviceAccounts.create` | 3 | 三個 release-scoped service account |
| `secretmanager.secrets.create` | 3 | `database-url`、`cursor-signing-key`、`web-session` |
| `pubsub.topics.create` | 2 | `jobs`、`jobs-dlq` |
| `storage.buckets.create` | 1 | release-scoped data bucket |

`github-deployer` 目前的專案角色只有 `run.admin`、`cloudscheduler.admin`、`cloudsql.client`、`logging.viewer`、`serviceusage.serviceUsageConsumer`。`infra/terraform/iam.tf` 也只給這幾個（dev 部署用）。`ephemeral_staging` module 要建立 service account、secret、Pub/Sub、bucket、Cloud SQL database／user 並設定它們的 IAM，沒有任何 IaC 給部署身分這些權限（新 blocker **B11**）。

依 module 的 resource type 靜態對照，除了上面四個被拒的權限，**之後還會需要**（這次 apply 沒走到，不是觀測到的拒絕）：

- `cloudsql.databases.*`、`cloudsql.users.*`（`cloudsql.client` 沒有）
- `resourcemanager.projects.setIamPolicy`（`google_project_iam_member.staging_runtime_cloudsql`）
- service account、secret、topic／subscription、bucket 的 `setIamPolicy` 與刪除權限（各個 `*_iam_member` 與 cleanup）
- `secretmanager.versions.add`

CMEK 的 service agent 授權在 `runtime_foundation/kms.tf` 已有，這次沒有看到 KMS 錯誤。

沒有建立任何資源，依據：

1. audit log 裡 deployer 的 create 全部被拒。
2. 唯讀 readback：沒有 `stg-odp-ccb9553bb151` 的 Cloud Run service／job、Cloud SQL database／user、service account、Pub/Sub topic、secret、bucket 或 Scheduler job；recovery bundle prefix 沒有物件。
3. `Hold ephemeral staging resources on failure` 報 `Terraform durable state pull returned no managed resources`，代表 state 裡沒有資源。hold 因此 exit 1。

run 上傳的 receipt 原樣保存為 [`run-41464086945-staging-lifecycle-create.json`](run-41464086945-staging-lifecycle-create.json)（sha256 `b5e7e00a…116f`，artifact `13151175340`）。它把 19 個計畫中的資源列為 `failed`，`failure_cleanup_receipt` 又把它們列為 `deleted`；兩份清單都是 lifecycle 的預定清單，不是觀測結果，實際上沒有東西可刪，不需要人工清理。

要給哪個身分這些權限是 Human/Ops 的決定，本 task 不改 IAM，也不改 IaC。後續：

1. Human/Ops 決定由哪個身分執行 staging lifecycle（例如另建 staging 專用 deployer，不擴大 dev 用的 `github-deployer`），把 module 需要的建立／刪除／IAM 權限寫進 IaC 並 apply；身分若改變，同步更新 `ODP_STAGING_DEPLOYER_SERVICE_ACCOUNT` 與 WIF。
2. 若這項修正改到 `deploy-dev.yml` 或 candidate 之後的 workflow，`ccb9553b` 就不能再通過 ancestry 檢查，要重新 build、重新綁 admission。只改 GCP IAM 則可沿用 `ccb9553b`。
3. Human/Ops 用新的 approval_id 和 nonce 登記 lease。
4. 本 task owner 接續 staging deploy 與 rehearsal。

仍然沒有 staging 部署、migration、E2E、worker／scheduler、backup／restore 或 rollback 被觀測到，本目錄沒有 `staging-verified` 收據。

## 2026-11-03 第三次 staging dispatch 失敗：Cloud SQL 授權條件不成立（Claude）

負責人同意後，Human/Ops 給 `github-deployer` 加上 `iam.serviceAccountAdmin`、`secretmanager.admin`、`pubsub.admin`、`storage.admin`、`cloudsql.admin`（條件限 `oday-staging-foundation-sql`）、`projectIamAdmin`（條件限只授出 `roles/cloudsql.client`），並登記 `HUMANOPS-STAGING-LEASE-20261102E`。lease `lease-900733ca7628f1a63e93183f8f248f26` 於 00:20:37Z 簽發（candidate `ccb9553b`、manifest `sha256:93ffa139…2362`、`dispatch_ref_sha=3672d00a`），dispatch 出 run [`41552168108`](https://github.com/alfloop-dev/odayplus/actions/runs/41552168108)，00:24:40Z 以 `failure` 結束（`gh run watch --exit-status` exit 1）。這張 lease 已用掉。

| Job | 結果 |
|---|---|
| Validate release phase inputs | success |
| Verify the Supervisor lease authorises this deploy | success |
| Deploy the admitted artifact by immutable digest | **failure** |
| Verify production watch and clean up ephemeral staging | skipped |

這次 B11 大致解除：Cloud Audit Logs 顯示 00:23:30Z–00:23:46Z 部署身分成功建立 3 個 service account、3 個 secret（2 個 secret version）、2 個 topic、2 個 subscription、1 個 bucket，以及它們的 IAM member 和專案層的 `cloudsql.client` 授權，54 筆 admin activity 全部 status 0。

失敗在 `google_sql_user.staging`（main.tf 第 216 行），這次 receipt 有完整錯誤：

```text
Error, failed to insert user stg_odp_ccb9553bb151_46d39e68_app into instance oday-staging-foundation-sql:
googleapi: Error 403: The client is not authorized to make this request., notAuthorized
```

`github-deployer` 的 `roles/cloudsql.admin` 帶條件 `resource.name.startsWith('projects/odayplus-runtime-20260825/instances/oday-staging-foundation-sql')`。我用 Policy Troubleshooter 查 `//cloudsql.googleapis.com/projects/odayplus-runtime-20260825/instances/oday-staging-foundation-sql`，`cloudsql.users.create` 和 `cloudsql.databases.create` 的這個條件都算出 **false**（overall `UNKNOWN_INFO`），和實際 403 一致（新 blocker **B12**）。Cloud SQL 沒有替被拒的呼叫寫 audit log。`google_sql_database` 和 user 平行建立，receipt 只留 stderr 尾段，所以 database 是否也被拒沒有直接觀測到；Troubleshooter 對它的判斷相同。

為什麼文件寫的 `projects/PROJECT_ID/instances/INSTANCE_ID` 格式在這裡比對不到，我沒有查出來。Cloud SQL 的 IAM Conditions 文件會把名稱和 `resource.service` 或 `resource.type` 一起用，但沒有列出哪些 Cloud SQL 權限會帶 instance 屬性。

失敗路徑把建好的資源刪掉了（audit log 00:24:24Z–00:24:32Z 有 Destroy／Delete，全部 status 0）。之後唯讀 readback（[`run-41552168108-gcp-readback.txt`](run-41552168108-gcp-readback.txt)）沒有找到任何 `stg-odp` 的 service account、secret、topic、subscription、bucket、Cloud Run service／job、Scheduler job 或專案 IAM member；foundation instance 只剩 `postgres` database 和 user；recovery bundle prefix 沒有物件。hold step 一樣報 `Terraform durable state pull returned no managed resources`，沒有東西要保留到 TTL，不需要人工清理。run 上傳的 receipt 原樣保存為 [`run-41552168108-staging-lifecycle-create.json`](run-41552168108-staging-lifecycle-create.json)（sha256 `0bdc763a…05d4`，artifact `13192841910`）。

本 task 不改 IAM，也不改 IaC。後續：

1. Human/Ops（經負責人同意）調整 `github-deployer` 的 Cloud SQL 授權，讓它能在 `oday-staging-foundation-sql` 建立、刪除 database 和 user，並先用 Policy Troubleshooter 確認 `cloudsql.users.create`、`cloudsql.databases.create` 都是 GRANTED 再登記 lease。可選做法：給 instance 加 tag、改用 `resource.matchTag` 條件（不會涵蓋 dev SQL）；或改成只含 database／user 權限、不帶條件的 custom role（會涵蓋專案內所有 instance，包括 `oday-dev-sql`）。
2. 只改 GCP IAM 可以沿用 `ccb9553b`；改 `deploy-dev.yml` 或 module 就要重新 build、重新綁 admission。
3. Human/Ops 用新的 approval_id 和 nonce 登記 lease。
4. 本 task owner 接續 staging deploy 與 rehearsal。

仍然沒有 staging 部署、migration、E2E、worker／scheduler、backup／restore 或 rollback 被觀測到，本目錄沒有 `staging-verified` 收據。

## Blockers 與解除條件

| Blocker | 解除條件 |
|---|---|
| B1：manifest 使用 placeholder digest | 由單次 build/sign 產生 Artifact Registry immutable digests，並以 `gcloud artifacts docker images describe` 讀回。 |
| B2：registry provenance 不一致 | 只接受 Runtime Release handoff 產生的 `asia-east1-docker.pkg.dev/...@sha256:...` references。 |
| B3：candidate stale | 針對目前核准 candidate 重新建立 manifest、SBOM、簽章與 manifest digest。 |
| B4：Runtime Release 未註冊且沒有 staging 執行 provenance | Human/Ops 發布 `origin/dev` 的唯一 workflow，補 protected vars／lease authority／approval authority，再以 `environment=staging` 成功 dispatch。 |
| B5：staging foundation 僅部分就緒 | 完成依賴的 staging bootstrap/IAC 與 masked snapshot materialization，提供 release-scoped bucket/database/tenant/IAM、Cloud Run services、paused Scheduler 與 TTL 的 redacted readback。 |
| B6：沒有 app image handoff | 以 exact candidate 執行一次 Runtime Release build/sign，取得 Artifact Registry API/Web/Worker/Scheduler immutable digests。 |
| B9：staging deploy／closeout job 沒有安裝 terraform（run 41387164145）— `partially_resolved` | PR #1637（`f81b9373`）已在 candidate `ccb9553b` 內；run `41464086945` 的 deploy job 已安裝並實際執行 terraform。`staging_closeout` 的安裝步驟要等 production watch 通過才會跑，尚未觀測。 |
| B10：supervisor 讀不到 lease 簽章私鑰（`HUMANOPS-STAGING-LEASE-20261102C`）— `partially_resolved` | 負責人重新登入後，`…20261102D` 於 14:39:48Z 簽發並 dispatch run `41464086945`。使用者憑證的 reauth 期限仍可能再到；改用不受 reauth 限制的身分是 Human/Ops 的決定。 |
| B11：部署身分 `github-deployer` 沒有建立 staging 資源的權限（run 41464086945）— `partially_resolved` | Human/Ops 已直接授予 GCP 角色；run `41552168108` 裡 SA、secret、Pub/Sub、bucket 與 IAM member 都建立成功。剩下的 Cloud SQL 拒絕記在 B12。這些授權還沒寫進 IaC。 |
| B12：`cloudsql.admin` 的 instance 名稱條件不成立，建立 Cloud SQL user 回 403（run 41552168108）— `open` | Human/Ops 調整 Cloud SQL 授權，Policy Troubleshooter 對 `oday-staging-foundation-sql` 的 `cloudsql.users.create`、`cloudsql.databases.create` 回 GRANTED 後，用新 approval_id／nonce 登記 lease。 |

## 正式重跑要求

1. 先在受保護的 staging environment 完成 credentials、WIF、IAM、KMS、masked snapshot、隔離 DB/bucket/tenant 與 scheduler paused 設定。
2. 以唯一 `Runtime Release` workflow 產生一次 build handoff；不可從 GHCR 或手寫 JSON 補 digest。
3. 用同一組 digests 執行 staging，保存 workflow run URL、Cloud Run/GKE/Cloud SQL readback、migration receipt 與 redacted artifact receipt。
4. 只有觀測到所有 rehearsal suites 通過後，才能建立 `staging-verified` receipt；否則保留 `blocked` 或依規劃保留失敗環境至 TTL。

## 驗證紀錄

```text
git show origin/dev:.github/workflows/deploy-dev.yml
gh workflow list --all
gh run list --workflow .github/workflows/deploy-dev.yml --limit 20
gh run list --workflow 'Deploy/Verify Staging' --limit 50
gh run list --workflow 'Deploy Dev' --limit 50
git ls-remote origin refs/heads/dev refs/heads/task/ODP-EPHEMERAL-STAGING-ROLLOUT-001
gh workflow list --all --json name,path,state
gh variable list --env staging --json name,value
gh secret list --env staging --json name,updatedAt
gcloud artifacts packages list --project=odayplus-runtime-20260825 --location=asia-east1 --repository=oday-plus-dev --format='table(name)'
gcloud run services list --project=odayplus-runtime-20260825 --region=asia-east1 --format='table(metadata.name,status.url)'
gcloud kms keys list --project=odayplus-runtime-20260825 --location=asia-east1 --keyring=oday-runtime
gcloud storage buckets describe gs://oday-staging-source-snapshots-odayplus-runtime-20260825
gcloud storage ls --recursive gs://oday-staging-source-snapshots-odayplus-runtime-20260825
kubectl get namespace oday-staging-8c72b54 -o yaml
kubectl get all,networkpolicy,serviceaccount,role,rolebinding -n oday-staging-8c72b54 -o wide
kubectl get networkpolicy -n oday-staging-8c72b54 -o yaml
kubectl get serviceaccount -n oday-staging-8c72b54 -o yaml
gcloud iam service-accounts get-iam-policy oday-staging-runtime@odayplus-runtime-20260825.iam.gserviceaccount.com --project=odayplus-runtime-20260825
gcloud sql instances describe oday-staging-sql --project=odayplus-runtime-20260825
gcloud sql databases list --instance=oday-staging-sql --project=odayplus-runtime-20260825
gcloud sql users list --instance=oday-staging-sql --project=odayplus-runtime-20260825
terraform -chdir=infra/terraform/modules/ephemeral_staging init -backend=false -input=false
terraform -chdir=infra/terraform/modules/ephemeral_staging validate
python3 infra/terraform/validate_contract.py
uv run --python 3.12 pytest tests/ops/test_staging_rollout.py tests/release/test_release_manifest.py
```

本輪 GCP project/billing、Artifact Registry、Cloud Run、Cloud SQL、Scheduler、GKE namespace/policy/KSA、KMS、snapshot bucket/object listing、GSA IAM binding、Secret Manager metadata、GitHub environment/workflow/variable/secret metadata 與 Terraform module contract readback 已執行；沒有可對應本 task 的成功 staging dispatch。bucket 存在但 object listing 為空，不能視為 masked snapshot authority。直接執行 `terraform validate` 在未初始化 provider cache 的隔離 worktree 先回報缺少 provider，改用 ephemeral `TF_DATA_DIR` 初始化後 validate 通過；這是本地工具環境差異，不是 staging apply 證據。實際重跑仍需要 Human/Ops authority，Auto Worker 不得自行填入 credentials、建立依賴 task 的長期 foundation，或偽造 deployment receipt。

上述 01:18:58Z 重查只讀取 GitHub/GCP/GKE metadata 與 object listing；沒有讀取 secret value，也沒有因環境變數存在就推定 runtime authority 已完成。最新結果已同步記錄於 [`staging-rollout-dry-run.json`](staging-rollout-dry-run.json) 的 `latest_recheck`，仍不可升格為 `staging-verified`。

本次 remediation commit 的 focused verification：

```text
git diff --check
uv run --python 3.12 ruff check delivery_toolchain/release/release_manifest.py tests/ops/test_staging_rollout.py tests/release/test_release_manifest.py
uv run --python 3.12 pytest -q tests/ops/test_staging_rollout.py tests/release/test_release_manifest.py tests/release/test_runtime_admission.py tests/ops/test_dev_rollout.py
```

結果：Terraform production contract validation 通過；provider 初始化後 Terraform module validate 通過；ruff check 通過；focused pytest suite 通過（56 tests passed）。這些是本地 contract tests，不是 staging deployment proof。

## 產物

- [`staging-rollout-dry-run.json`](staging-rollout-dry-run.json)：唯一的 real-preflight / blocked / desired-state record；包含 redacted project、artifact、runtime、GKE、GitHub environment 與 Terraform readback。
- [`run-41387164145-staging-lifecycle-create.json`](run-41387164145-staging-lifecycle-create.json)：失敗 staging run 上傳的原始 create receipt（未修改；sha256 記錄在 JSON 的 `staging_dispatch_attempts`）。
- [`run-41387164145-gcp-readback.txt`](run-41387164145-gcp-readback.txt)：該 run 之後的唯讀 gcloud readback 輸出與 exit code。
- [`run-41464086945-staging-lifecycle-create.json`](run-41464086945-staging-lifecycle-create.json)：第二次失敗 staging run 的原始 create receipt（未修改）。
- [`run-41464086945-gcp-readback.txt`](run-41464086945-gcp-readback.txt)：該 run 之後的 audit log 摘要、唯讀 readback 與 deployer 專案角色，含 exit code。
- [`run-41552168108-staging-lifecycle-create.json`](run-41552168108-staging-lifecycle-create.json)：第三次失敗 staging run 的原始 create receipt（未修改）。
- [`run-41552168108-gcp-readback.txt`](run-41552168108-gcp-readback.txt)：該 run 的 audit log 摘要、deployer IAM 條件、Policy Troubleshooter 結果與殘留資源 readback，含 exit code。
- `delivery_toolchain/release/release_manifest.py`：placeholder digest 偵測 predicate。
- `tests/ops/test_staging_rollout.py`：dry-run、provenance、registry 與 placeholder fail-closed contract tests。

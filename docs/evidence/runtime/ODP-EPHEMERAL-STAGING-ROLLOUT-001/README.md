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

## Blockers 與解除條件

| Blocker | 解除條件 |
|---|---|
| B1：manifest 使用 placeholder digest | 由單次 build/sign 產生 Artifact Registry immutable digests，並以 `gcloud artifacts docker images describe` 讀回。 |
| B2：registry provenance 不一致 | 只接受 Runtime Release handoff 產生的 `asia-east1-docker.pkg.dev/...@sha256:...` references。 |
| B3：candidate stale | 針對目前核准 candidate 重新建立 manifest、SBOM、簽章與 manifest digest。 |
| B4：Runtime Release 未註冊且沒有 staging 執行 provenance | Human/Ops 發布 `origin/dev` 的唯一 workflow，補 protected vars／lease authority／approval authority，再以 `environment=staging` 成功 dispatch。 |
| B5：staging foundation 僅部分就緒 | 完成依賴的 staging bootstrap/IAC 與 masked snapshot materialization，提供 release-scoped bucket/database/tenant/IAM、Cloud Run services、paused Scheduler 與 TTL 的 redacted readback。 |
| B6：沒有 app image handoff | 以 exact candidate 執行一次 Runtime Release build/sign，取得 Artifact Registry API/Web/Worker/Scheduler immutable digests。 |
| B9：staging deploy／closeout job 沒有安裝 terraform（run 41387164145）— `partially_resolved` | 修正已合入 dev（PR #1637，`f81b9373`；`deploy-dev.yml` 兩處 `hashicorp/setup-terraform@v3`）。仍需：以含它的 dev commit 重建 candidate、完成 staging admission，再由 Human/Ops 登記新 lease。 |

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
- `delivery_toolchain/release/release_manifest.py`：placeholder digest 偵測 predicate。
- `tests/ops/test_staging_rollout.py`：dry-run、provenance、registry 與 placeholder fail-closed contract tests。

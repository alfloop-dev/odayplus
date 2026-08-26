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

## Blockers 與解除條件

| Blocker | 解除條件 |
|---|---|
| B1：manifest 使用 placeholder digest | 由單次 build/sign 產生 Artifact Registry immutable digests，並以 `gcloud artifacts docker images describe` 讀回。 |
| B2：registry provenance 不一致 | 只接受 Runtime Release handoff 產生的 `asia-east1-docker.pkg.dev/...@sha256:...` references。 |
| B3：candidate stale | 針對目前核准 candidate 重新建立 manifest、SBOM、簽章與 manifest digest。 |
| B4：Runtime Release 未註冊且沒有 staging 執行 provenance | Human/Ops 發布 `origin/dev` 的唯一 workflow，補 protected vars／lease authority／approval authority，再以 `environment=staging` 成功 dispatch。 |
| B5：staging foundation 僅部分就緒 | 完成依賴的 staging bootstrap/IAC 與 masked snapshot materialization，提供 release-scoped bucket/database/tenant/IAM、Cloud Run services、paused Scheduler 與 TTL 的 redacted readback。 |
| B6：沒有 app image handoff | 以 exact candidate 執行一次 Runtime Release build/sign，取得 Artifact Registry API/Web/Worker/Scheduler immutable digests。 |

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
- `delivery_toolchain/release/release_manifest.py`：placeholder digest 偵測 predicate。
- `tests/ops/test_staging_rollout.py`：dry-run、provenance、registry 與 placeholder fail-closed contract tests。

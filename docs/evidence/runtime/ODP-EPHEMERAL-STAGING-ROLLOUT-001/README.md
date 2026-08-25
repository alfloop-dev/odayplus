# ODP-EPHEMERAL-STAGING-ROLLOUT-001 — Real preflight blocker record

> **這不是 staging deployment receipt。** 本目錄目前只包含 `dry-run` / `blocked` / `desired-state` 證據；沒有任何 `staging-verified` 收據，也沒有宣稱 migration、E2E、worker、scheduler、backup、restore 或 rollback 已在遠端環境執行。

## 目前結論

本輪已重新執行 real GCP/GitHub preflight，但仍無法安全建立「隔離 staging 並完成全套 rehearsal」。原先提交的 JSON 證據已移除，因為它們把 placeholder digest 與未執行的資源狀態寫成 `PASSED` / `READY` / `staging-verified`。本輪沒有執行 Terraform apply、Cloud Run deploy、GKE apply、migration、E2E、job、backup/restore 或 rollback。

唯一有效的 task-scoped record 是 [`staging-rollout-dry-run.json`](staging-rollout-dry-run.json)。它明確記錄：

- source `RELEASE_MANIFEST.json` 的 11 個 digest / reference 是明顯 placeholder，雖然 self-hash 可重算，不能當成 artifact provenance；
- source manifest 的 candidate SHA 是舊版本，且不是目前 dev candidate；
- 先前證據的 `ghcr.io/alfloop-dev/...` registry 與本 repo Runtime Release 的 `${GCP_REGION}-docker.pkg.dev` 路徑不一致；
- 沒有成功的 staging workflow dispatch、run URL、Cloud Run/GKE/Cloud SQL readback 或 remote staging proof；
- GCP project `odayplus-runtime-20260825` 與 billing readback 成功，但 Cloud KMS API/key、隔離 staging Cloud SQL instance、masked snapshot authority、staging namespace 與 staging scheduler 尚未觀測；現有 GKE 僅有 `oday-emgi` namespace；
- Artifact Registry `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev` 只觀測到 data-platform/mlflow packages，沒有 Runtime Release 產生的 ODay Plus API/Web/Worker/Scheduler handoff；
- GitHub `staging` environment 有 required reviewers 與基本 WIF/project vars，但缺 admission lease verifier vars 與完整 runtime/secret reference vars；
- 在真實 digest、成功 workflow run、隔離 foundation、masked snapshot 與 staging authority 到位前，所有 migration、E2E、job、backup/restore、rollback、provider-off、TTL 結果都必須保持未觀測。

## Blockers 與解除條件

| Blocker | 解除條件 |
|---|---|
| B1：manifest 使用 placeholder digest | 由單次 build/sign 產生 Artifact Registry immutable digests，並以 `gcloud artifacts docker images describe` 讀回。 |
| B2：registry provenance 不一致 | 只接受 Runtime Release handoff 產生的 `asia-east1-docker.pkg.dev/...@sha256:...` references。 |
| B3：candidate stale | 針對目前核准 candidate 重新建立 manifest、SBOM、簽章與 manifest digest。 |
| B4：沒有 staging 執行 provenance | Human/Ops 提供 staging IAM、protected vars、secret refs、approval authority，並以 `deploy-dev.yml` 的 `environment=staging` 成功 dispatch。 |
| B5：staging foundation 不完整 | 完成依賴的 staging bootstrap/IAC，提供 KMS key、隔離 Cloud SQL、masked snapshot、namespace 與 default-deny egress 的 redacted readback。 |
| B6：沒有 app image handoff | 以 exact candidate 執行一次 Runtime Release build/sign，取得 Artifact Registry API/Web/Worker/Scheduler immutable digests。 |

## 正式重跑要求

1. 先在受保護的 staging environment 完成 credentials、WIF、IAM、KMS、masked snapshot、隔離 DB/bucket/tenant 與 scheduler paused 設定。
2. 以唯一 `Runtime Release` workflow 產生一次 build handoff；不可從 GHCR 或手寫 JSON 補 digest。
3. 用同一組 digests 執行 staging，保存 workflow run URL、Cloud Run/GKE/Cloud SQL readback、migration receipt 與 redacted artifact receipt。
4. 只有觀測到所有 rehearsal suites 通過後，才能建立 `staging-verified` receipt；否則保留 `blocked` 或依規劃保留失敗環境至 TTL。

## 驗證紀錄

```text
git show origin/dev:.github/workflows/deploy-dev.yml
gh run list --workflow 'Deploy/Verify Staging' --limit 50
gh run list --workflow 'Deploy Dev' --limit 50
uv run --python 3.12 pytest tests/ops/test_staging_rollout.py tests/release/test_release_manifest.py
```

本輪 GCP project/billing、Artifact Registry、Cloud Run、Cloud SQL、Scheduler、GKE、GitHub environment 與 Terraform module contract readback 已執行；沒有可對應本 task 的成功 staging dispatch。實際重跑仍需要 Human/Ops authority，Auto Worker 不得自行填入 credentials、建立依賴 task 的長期 foundation，或偽造 deployment receipt。

本次 remediation commit 的 focused verification：

```text
git diff --check
uv run --python 3.12 ruff check delivery_toolchain/release/release_manifest.py tests/ops/test_staging_rollout.py tests/release/test_release_manifest.py
uv run --python 3.12 pytest -q tests/ops/test_staging_rollout.py tests/release/test_release_manifest.py tests/release/test_runtime_admission.py tests/ops/test_dev_rollout.py
```

結果：ruff check 通過，56 tests passed；這些是本地 contract tests，不是 staging deployment proof。

## 產物

- [`staging-rollout-dry-run.json`](staging-rollout-dry-run.json)：唯一的 real-preflight / blocked / desired-state record；包含 redacted project、artifact、runtime、GKE、GitHub environment 與 Terraform readback。
- `delivery_toolchain/release/release_manifest.py`：placeholder digest 偵測 predicate。
- `tests/ops/test_staging_rollout.py`：dry-run、provenance、registry 與 placeholder fail-closed contract tests。

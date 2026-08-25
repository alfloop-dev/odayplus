# ODP-EPHEMERAL-STAGING-ROLLOUT-001 — Dry-run blocker record

> **這不是 staging deployment receipt。** 本目錄目前只包含 `dry-run` / `blocked` / `desired-state` 證據；沒有任何 `staging-verified` 收據，也沒有宣稱 migration、E2E、worker、scheduler、backup、restore 或 rollback 已在遠端環境執行。

## 目前結論

本任務暫時無法滿足「建立隔離 staging 並完成全套 rehearsal」的驗收。原先提交的 JSON 證據已移除，因為它們把 placeholder digest 與未執行的資源狀態寫成 `PASSED` / `READY` / `staging-verified`。

唯一有效的 task-scoped record 是 [`staging-rollout-dry-run.json`](staging-rollout-dry-run.json)。它明確記錄：

- source `RELEASE_MANIFEST.json` 的 11 個 digest / reference 是明顯 placeholder，雖然 self-hash 可重算，不能當成 artifact provenance；
- source manifest 的 candidate SHA 是舊版本，且不是目前 dev candidate；
- 先前證據的 `ghcr.io/alfloop-dev/...` registry 與本 repo Runtime Release 的 `${GCP_REGION}-docker.pkg.dev` 路徑不一致；
- 沒有成功的 staging workflow dispatch、run URL、Cloud Run/GKE/Cloud SQL readback 或 remote staging proof；
- 在真實 digest、成功 workflow run 與人類提供的 staging authority 到位前，所有 migration、E2E、job、backup/restore、rollback、provider-off、TTL 結果都必須保持未觀測。

## Blockers 與解除條件

| Blocker | 解除條件 |
|---|---|
| B1：manifest 使用 placeholder digest | 由單次 build/sign 產生 Artifact Registry immutable digests，並以 `gcloud artifacts docker images describe` 讀回。 |
| B2：registry provenance 不一致 | 只接受 Runtime Release handoff 產生的 `asia-east1-docker.pkg.dev/...@sha256:...` references。 |
| B3：candidate stale | 針對目前核准 candidate 重新建立 manifest、SBOM、簽章與 manifest digest。 |
| B4：沒有 staging 執行 provenance | Human/Ops 提供 staging GCP/WIF/IAM/vars/approval，並以 `deploy-dev.yml` 的 `environment=staging` 成功 dispatch。 |

## 正式重跑要求

1. 先在受保護的 staging environment 完成 credentials、WIF、IAM、masked snapshot、隔離 DB/bucket/tenant 與 scheduler paused 設定。
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

前兩個查核顯示沒有可對應本 task 的成功 staging dispatch；本地測試驗證 dry-run record 不會被誤認為部署成功。實際重跑仍需要 Human/Ops authority，Auto Worker 不得自行填入 credentials 或偽造 deployment receipt。

本次 remediation commit 的 focused verification：

```text
git diff --check
uv run --python 3.12 ruff check delivery_toolchain/release/release_manifest.py tests/ops/test_staging_rollout.py tests/release/test_release_manifest.py
uv run --python 3.12 pytest -q tests/ops/test_staging_rollout.py tests/release/test_release_manifest.py tests/release/test_runtime_admission.py tests/ops/test_dev_rollout.py
```

結果：ruff check 通過，56 tests passed；這些是本地 contract tests，不是 staging deployment proof。

## 產物

- [`staging-rollout-dry-run.json`](staging-rollout-dry-run.json)：唯一的 dry-run / blocked / desired-state record。
- `delivery_toolchain/release/release_manifest.py`：placeholder digest 偵測 predicate。
- `tests/ops/test_staging_rollout.py`：dry-run、provenance、registry 與 placeholder fail-closed contract tests。

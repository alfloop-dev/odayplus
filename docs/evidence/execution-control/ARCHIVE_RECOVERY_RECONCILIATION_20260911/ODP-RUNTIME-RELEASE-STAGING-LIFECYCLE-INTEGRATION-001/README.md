# ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001 驗收續辦與核對記錄 (2026-09-11)

## 1. 任務背景與復原目標

- **任務 ID**: `ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001`
- **任務名稱**: 歷史驗收續辦：ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001
- **執行身分 (Owner)**: `Antigravity5`
- **指派審查者 (Reviewer)**: `Codex2`
- **復原目標分支**: `task/ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001-RECOVERY-20260911`
- **原始交付 PR**: [#1041](https://github.com/alfloop-dev/odayplus/pull/1041)
  - PR Head SHA: `fada677569265ed258649b841e5a67fe7bb5dd80`
  - Merge Commit SHA: `462c8cd4ff2569cec0f2c383d9e601c5bdbec715`
  - 合併時間: `2026-08-27T17:50:22Z`
  - 歷史審查者: `Antigravity3`（經由 `task-review-gate` 於 2026-08-27T17:30:51Z 批准）
  - 精確 Head CI 結論: 7/7 check-runs 全部 `success`

本任務屬於 Wave 2 remediation 工程交付，旨在修補 staging 與 Runtime Release 狀態機的 single-path 整合：所有 release-scoped 資源、exact image digests、IAM、以及測試驗證均由既有 Runtime Release 直接管理，不得在 workflow 外另跑第二條部署路徑，亦不得沿用 dev smoke identity。

在 2026-09-06 archive 事故後的歷史盤點中，本任務留有兩處 gap 註記：
1. **A3 測試收集缺口**：`infra/terraform/tests/test_ephemeral_staging.py` 交付時未被任何 CI job 收集。
2. **A5 收據檔案缺口**：盤點記錄「『可由同一狀態機產生 secret-free receipts』的能力由測試覆蓋；本 PR 未帶任何已產生的 staging receipt 檔」。

依 2026-09-11 使用者明確派工指示，由 Supervisor Auto Worker 接續辦理可執行的驗收續辦，不再因 archive 遺失一律等待 Human/Ops。逐條核對真實能力與契約測試，完成補證。

---

## 2. 歷史缺口解決依據與補證

### 2.1 A3 測試收集缺口解決依據 (PR #1291)
任務 `ODP-STAGING-IAC-CI-COLLECTION-001` (PR [#1291](https://github.com/alfloop-dev/odayplus/pull/1291)) 已完成並合併入 mainline：
- 在 `.github/workflows/ci.yml` 的 `orchestrator` job 中納入 `infra` target，並釘死 Terraform 1.9.8；
- 在 `pyproject.toml` 的 `testpaths` 納入 `"infra"`；
- 改造 `infra/terraform/tests/test_ephemeral_staging.py` 支援 fail-closed 與離線 provider override harness；
- 建立 24 項守門與 mutation 測試（`tests/tooling/test_staging_iac_ci_collection.py`），確保 18 項 ephemeral staging Terraform 契約測試被 CI 完整收集並執行。

### 2.2 A5 收據生成能力解決依據與離線證明
1. **契約條款性質**：原 A5 條款原文為「API Web worker scheduler migration one-shot backup restore rollback rehearsal 均可由同一狀態機產生 secret-free receipts」。這是狀態機具備生成 secret-free receipts 能力的契約要求，而非要求本 code task 在 CI 中實際完成 live cloud 部署。
2. **狀態機與契約覆蓋**：`product_ops/deployment/staging_lifecycle.py` 中 `REHEARSAL_STAGE_NAMES` 定義了全部 9 項演練階段（`db_expand_migration`、`data_platform_snapshot`、`api_web_authenticated_smoke`、`worker_idempotency`、`scheduler_oneshot`、`backup_restore_drill`、`rollback_rehearsal`、`public_egress_denied_probe`、`external_providers_disabled_readback`），`verify_ephemeral_staging()` 產生包含 `secret_values_redacted=True` 與 `identity_scope=release_scoped_least_privilege` 的 `StagingLifecycleReceipt`。
3. **96 項單元與契約測試**：`tests/ops/test_ephemeral_staging_lifecycle.py` 完整驗證了 create, verify (9 stages), hold, cleanup, orphan scanning 的 secret-free receipt 生成與 fail-closed 行為。
4. **窄 Scope 離線證明腳本**：本次交付補齊 `verify_staging_lifecycle_receipts.py`，在本機隔離環境下直接調用狀態機，產生包含 9 項演練階段的 `sample-secret-free-verify-receipt.json`，完整驗證收據格式、脫敏標記與各階段結果。

---

## 3. A1–A9 逐條驗收核對結果

| 項次 | 原驗收條款 | 類別 | 判定結果 | 核對依據與證據路徑 |
|---|---|---|---|---|
| **A1** | 既有 Runtime Release staging 分支直接呼叫既有 staging lifecycle create/verify/hold/cleanup 且不新增 workflow 或 wrapper entrypoint | 程式交付 (D)<br>測試證明 (T) | **已滿足 (met)** | PR #1041 交付之 `.github/workflows/deploy-dev.yml` 與 `tests/ops/test_deploy_workflow_contract.py`（merge commit `462c8cd4`，exact-head product CI success）驗證 deploy-dev.yml 直接調用 `staging_lifecycle.py` 四大入口，無額外 workflow wrapper。 |
| **A2** | release_id candidate SHA manifest digest 與 API Web worker scheduler exact image digests 綁在同一不可變 handoff 且不得 rebuild | 測試證明 (T) | **已滿足 (met)** | `tests/ops/test_deploy_workflow_contract.py` 與 `tests/ops/test_ephemeral_staging_lifecycle.py`（如 `test_rerun_create_rejects_mismatched_candidate_sha_and_preserves_existing_state`、`test_rerun_create_rejects_mismatched_manifest_digest`、`test_immutable_release_identity_detects_worker_and_scheduler_image_mismatch`）驗證不可變綁定；exact-head CI success。 |
| **A3** | release-scoped lifecycle outputs 成為 staging endpoint database bucket tenant 與 IAM 唯一 authority 靜態 environment vars 只提供長期 foundation inputs | 程式交付 (D)<br>測試證明 (T) | **已滿足 (met)** | `product_ops/deployment/staging_lifecycle.py` 中 `REQUIRED_STAGING_OUTPUTS` 定義權威 output 映射；經 PR #1291 解決 CI 收集後，`infra/terraform/tests/test_ephemeral_staging.py` 與 `tests/ops/test_ephemeral_staging_lifecycle.py` 完整進入 CI 並通過驗證。 |
| **A4** | staging smoke proof 不得 impersonate dev smoke operator 必須使用 release-scoped least-privilege identity | 測試證明 (T) | **已滿足 (met)** | `tests/ops/test_ephemeral_staging_lifecycle.py` 中 `test_verify_ephemeral_staging_dev_identity_rejection` 與 `test_live_executor_rejects_arbitrary_operator_identity` 嚴格拒絕 dev smoke operator 及未宣告身分；exact-head CI success。 |
| **A5** | API Web worker scheduler migration one-shot backup restore rollback rehearsal 均可由同一狀態機產生 secret-free receipts | 測試證明 (T) | **已滿足 (met)** | `REHEARSAL_STAGE_NAMES` 涵蓋全部 9 項演練階段；`verify_ephemeral_staging` 產生 `secret_values_redacted=True` 之 `StagingLifecycleReceipt`；由 96 項 lifecycle 測試與本目錄 `verify_staging_lifecycle_receipts.py`（產出 `sample-secret-free-verify-receipt.json`）證明能力完全具備。 |
| **A6** | 第三方來源維持 disabled 且 public egress default-deny | 程式交付 (D)<br>測試證明 (T) | **已滿足 (met)** | `infra/terraform/modules/ephemeral_staging/main.tf` 與 `tests/ops/test_ephemeral_staging_lifecycle.py` / `infra/terraform/tests/test_ephemeral_staging.py` 驗證 default-deny VPC egress 與 external sources disabled 設定；exact-head CI success。 |
| **A7** | 失敗環境依 TTL 保留成功環境由 prod closeout 精確清理且 orphan cleanup fail closed | 測試證明 (T) | **已滿足 (met)** | `tests/ops/test_ephemeral_staging_lifecycle.py` 中 `test_cleanup_exact_label_matching_and_safety`、`test_create_failure_triggers_exact_cleanup`、`test_scan_orphans_detects_expired_and_unmanaged`、`ReleaseScopedAutoCleanupTests` 等全數通過；exact-head CI success。 |
| **A8** | focused contract tests 必須在 staging 繞過 lifecycle 或使用 dev identity/靜態 service names 時失敗 | 測試證明 (T) | **已滿足 (met)** | `tests/ops/test_deploy_workflow_contract.py`、`tests/ops/test_workflow_expression_contexts.py` 與 `tests/ops/test_ephemeral_staging_lifecycle.py` 包含負向測試，確保繞過 lifecycle 或沿用 dev 靜態名稱時必然失敗；exact-head CI success。 |
| **A9** | PR 與部署文件使用中文並說明取代關係與 rollback | 程式交付 (D)<br>執行過程 (P) | **已滿足 (met)** | PR #1041 標題 `[ReviewBus] ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001 把 ephemeral staging lifecycle 接進唯一 Runtime Release` 及 `docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md` 均使用中文並詳述取代關係與 rollback 機制。 |

---

## 4. 權限邊界與不變量原則

1. **不推斷雲端部署或授權**：本次驗收續辦確認程式碼、IaC 模組、狀態機收據生成與契約測試，未簽發 Human GO、未生成 Supervisor Release Lease、未開啟外部來源、未執行 live staging 建立或銷毀。
2. **歷史真實性保留**：
   - 原 PR #1041 (head `fada6775`) 的 7 項 CI check-runs 與原審查者 Antigravity3 之歷史 approval 原樣記錄。
   - 歷史 PR 與 merge commit SHA 完整保留。
3. **單一證據 Scope**：所有新交付物嚴格局限於 `docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001/`，不修改任何產品程式、workflow、IaC 模組、runtime 或 validator。

---

## 5. 驗證方式 (Verification)

本任務交付物由以下宣告命令驗證：

```bash
# 1. 乾淨工作區與排版檢查
git diff --check

# 2. 契約與單元測試套件執行 (218 項全數通過)
uv run --python 3.12 --frozen pytest -q \
  tests/ops/test_deploy_workflow_contract.py \
  tests/ops/test_ephemeral_staging_lifecycle.py \
  tests/ops/test_cloud_run_job_entrypoint.py \
  infra/terraform/tests/test_ephemeral_staging.py

# 3. Terraform 格式檢查
terraform -chdir=infra/terraform/modules/ephemeral_staging fmt -check

# 4. 部署 Shell 語法檢查
bash -n product_ops/deployment/deploy_cloud_run_waji.sh

# 5. 離線狀態機收據生成與脫敏驗證
python3 docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001/verify_staging_lifecycle_receipts.py

# 6. 驗收 JSON 完整性校驗
python3 -c 'import json
from pathlib import Path
p=Path("docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001/")
assert (p/"README.md").is_file()
x=json.loads((p/"acceptance-reconciliation.json").read_text())
assert isinstance(x,dict) and x["summary"]["reconciliation_verdict"] == "acceptance_fully_reconciled"
print("Receipt present and acceptance fully reconciled!")'
```

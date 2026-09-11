# ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001 驗收續辦與核對記錄 (2026-09-11)

## 1. 任務背景與復原目標

- **任務 ID**: `ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001`
- **任務名稱**: 歷史驗收續辦：ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001
- **執行身分 (Owner)**: `Antigravity5`
- **指派審查者 (Reviewer)**: `Codex2`
- **復原交付分支**: `task/ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001-RECOVERY-20260911`
- **原始交付 PR**: [#1041](https://github.com/alfloop-dev/odayplus/pull/1041)
  - PR Head SHA: `fada677569265ed258649b841e5a67fe7bb5dd80`
  - Merge Commit SHA: `462c8cd4ff2569cec0f2c383d9e601c5bdbec715`
  - 合併時間: `2026-08-27T17:50:22Z`
  - 歷史審查者: `Antigravity3`（經由 `task-review-gate` 於 2026-08-27T17:30:51Z 批准）
  - 精確 Head CI 結論: 7/7 check-runs 全部 `success` (`product`, `product-e2e-gate`, `performance-gate`, `orchestrator`, `change-scope`, `classify`, `boundary`)

本任務屬於 Wave 2 remediation 工程交付，旨在修補 staging 與 Runtime Release 狀態機的 single-path 整合：所有 release-scoped 資源、exact image digests、IAM、以及測試驗證均由既有 Runtime Release 直接管理，不得在 workflow 外另跑第二條部署路徑，亦不得沿用 dev smoke identity。

---

## 2. 審查退回意見 (PR #1313) 針對性修正說明

針對 Codex2 於 PR #1313 (head `f830167f`) 之審查意見，本輪進行全面補正：

### 2.1 修正 R1 [P2]：驗證來源與收據精確歸屬
- **歷史測試與 CI 歸屬**：明確標示原始套件測試（pytest ops 契約測試、Cloud Run job entrypoint 測試等）均在歷史 PR #1041 精確 head `fada67756926`（7/7 check-runs success）及後續 PR #1291 (`ODP-STAGING-IAC-CI-COLLECTION-001`) 之 CI 環境中執行通過。隔離 worktree 中未留存之歷史終端 exit 收據標記為 `unknown_historical_terminal_receipt`，不虛構、不為統計計數盲目重跑全套測試。
- **A5 狀態機收據結構示意與來源側車 (Provenance Sidecar)**：
  - `sample-secret-free-verify-receipt.json` 明確定義為非執行收據之結構與格式示意（Schema Illustration），反映 `product_ops.deployment.staging_lifecycle.verify_ephemeral_staging` 狀態機 serializer (`StagingLifecycleReceipt.to_dict()`) 在 `dry_run=True` 模式下的輸出格式，完整保留真實欄位（包括 `manifest_digest_prefix`、標準預設 stage target 映射 `stg_rel_..._5ceb8eb9` / `stg-rel-...-data-oday-staging-proj` / `stg-rel-...-rt`、標準 `remediation_notes` 以及乾淨 `metadata`）。
  - 來源側車檔案 `sample-secret-free-verify-receipt.provenance.json` 將 fixture 輸入時間戳（`injected_fixture_timestamp: 2026-09-11T11:03:00Z`）與實際 wall-clock / 終端日誌分離，未留存之歷史終端日誌明記為 `unknown_historical_terminal_receipt`，刪除無來源之執行耗時與 exit code 宣稱。
  - 移除不存在之測試節點引用，精確綁定 pinned `fada67756926` 歷史檔案中真實存在且已驗證之 5 項具名測試節點（包含 `tests/ops/test_ephemeral_staging_lifecycle.py::EphemeralStagingVerificationAndHoldTests::test_verify_ephemeral_staging_success` @ L2898），結合 PR #1041 exact-head `product` CI 結論與 PR #1298 single-path 整合，共同支持 A5 條款。
- **本輪 Focused 驗證**：本輪僅執行宣告之必要 focused 驗證（`git diff --check` 與 JSON 綱要/收據校驗），確保工作區乾淨且無語法錯誤。

### 2.2 修正 R2 [P2]：撤回越界變更，嚴格限定單一目錄 Scope
- **撤回全域 Manifest 變更**：完全撤回對 `docs/audits/code-boundary-inventory.csv` 的修改，恢復至基線 `4499a299`。
- **移除額外 `.py` 檔案**：移除曾引入之 `verify_staging_lifecycle_receipts.py`，避免觸發全域程式碼邊界清單變更。
- **單一交付路徑**：所有交付物 100% 局限於 `docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001/`，符合 canonical owned_paths 授權。

### 2.3 修正 R3 [P2]：精確綁定 SHA、依賴演進理由與下游無環映射
- **精確釘死補證 PR 與 CI 連結**：
  - **PR #1291 (`ODP-STAGING-IAC-CI-COLLECTION-001`)**：
    - Head SHA: `726065c3aec57147f37902b94d754234dd55e25f`
    - Merge SHA: `025323f36d05c81f40085ba80c87f370d7c49c83`
    - CI Orchestrator Success URL: `https://github.com/alfloop-dev/odayplus/actions/runs/34514563856/job/102996659589`
    - 解決 A3 測試收集缺口：納入 `infra` target，釘死 Terraform 1.9.8，實作 24 項收集守門與 mutation 測試。
  - **PR #1298 (`ODP-RUNTIME-RELEASE-SINGLE-PATH-001`)**：
    - Head SHA: `adfb5cb5ce05c5f4ddbdfc809d0dad828e6937de`
    - Merge SHA: `50d932207bd57c23663f6121586a4de7149f70fd`
    - 整合唯一 build-once Runtime Release 狀態機。
- **Dependencies Before/After 演進理由**：
  - **Before**: `[ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001 (blocked), ODP-RELEASE-ROLLBACK-DATA-HANDOFF-001 (done), DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001 (blocked)]`
  - **After**: `[]`
  - **理由**：本任務為純 Code 與 Workflow 契約整合交付（PR #1041），未涉及 live 雲端資源建立或線上部署。未完成之 live foundation IaC 與 EMGI snapshot 係下游 live 任務之阻塞門禁，非本任務程式碼驗收之前置。
- **下游依賴映射與 DAG 無環核對**：
  - 下游任務 `ODP-EPHEMERAL-STAGING-ROLLOUT-001` 維持 `blocked` 終態。
  - DAG 關係：`ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001 -> ODP-EPHEMERAL-STAGING-ROLLOUT-001`，經拓撲排序確認為嚴格無環 (Acyclic)。
  - 下游任務所依賴的 live foundation、EMGI snapshot、Human GO 與線上部署權限未受本 code 任務放行，門禁完整受控。
- **A9 流程約束與現存證據分離**：
  - 歷史執行過程約束標記為 `process_constraint_unverifiable`。
  - 現存交付物（中文 PR #1041 標題、中文部署計畫 `docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md`）可直接觀察並判定為已交付 (`met`)。

---

## 3. A1–A9 逐條驗收核對結果

| 項次 | 原驗收條款 | 類別 | 判定結果 | 核對依據與證據路徑 |
|---|---|---|---|---|
| **A1** | 既有 Runtime Release staging 分支直接呼叫既有 staging lifecycle create/verify/hold/cleanup 且不新增 workflow 或 wrapper entrypoint | 程式交付 (D)<br>測試證明 (T) | **已滿足 (met)** | PR #1041 交付之 `.github/workflows/deploy-dev.yml` 與 `tests/ops/test_deploy_workflow_contract.py`（merge commit `462c8cd4`，exact-head product CI success）驗證 deploy-dev.yml 直接調用 `staging_lifecycle.py` 四大入口，無額外 workflow wrapper。 |
| **A2** | release_id candidate SHA manifest digest 與 API Web worker scheduler exact image digests 綁在同一不可變 handoff 且不得 rebuild | 測試證明 (T) | **已滿足 (met)** | `tests/ops/test_deploy_workflow_contract.py` 與 `tests/ops/test_ephemeral_staging_lifecycle.py`（如 `test_rerun_create_rejects_mismatched_candidate_sha_and_preserves_existing_state`、`test_rerun_create_rejects_mismatched_manifest_digest`、`test_immutable_release_identity_detects_worker_and_scheduler_image_mismatch`）驗證不可變綁定；exact-head CI success。 |
| **A3** | release-scoped lifecycle outputs 成為 staging endpoint database bucket tenant 與 IAM 唯一 authority 靜態 environment vars 只提供長期 foundation inputs | 程式交付 (D)<br>測試證明 (T) | **已滿足 (met)** | `product_ops/deployment/staging_lifecycle.py` 中 `REQUIRED_STAGING_OUTPUTS` 定義權威 output 映射；經 PR #1291 (`ODP-STAGING-IAC-CI-COLLECTION-001`，head `726065c3`, merge `025323f3`, CI success [run 34514563856](https://github.com/alfloop-dev/odayplus/actions/runs/34514563856/job/102996659589)) 解決 CI 收集後，`infra/terraform/tests/test_ephemeral_staging.py` 與 `tests/ops/test_ephemeral_staging_lifecycle.py` 完整進入 CI 並通過驗證。 |
| **A4** | staging smoke proof 不得 impersonate dev smoke operator 必須使用 release-scoped least-privilege identity | 測試證明 (T) | **已滿足 (met)** | `tests/ops/test_ephemeral_staging_lifecycle.py` 中 `test_verify_ephemeral_staging_dev_identity_rejection` 與 `test_live_executor_rejects_arbitrary_operator_identity` 嚴格拒絕 dev smoke operator 及未宣告身分；exact-head CI success。 |
| **A5** | API Web worker scheduler migration one-shot backup restore rollback rehearsal 均可由同一狀態機產生 secret-free receipts | 測試證明 (T)<br>契約能力 (R) | **已滿足 (met)** | `REHEARSAL_STAGE_NAMES` 涵蓋全部 9 項演練階段；`verify_ephemeral_staging` 支援 `secret_values_redacted=True` 之 `StagingLifecycleReceipt`；由 96 項 lifecycle 測試（包含具名 `test_verify_ephemeral_staging_success` @ L2898 等 5 項具名測試節點）、PR #1041 exact-head CI、PR #1298 整合、與結構示意收據 `sample-secret-free-verify-receipt.json` 及來源中繼側車 `sample-secret-free-verify-receipt.provenance.json` 共同證明能力具備。 |
| **A6** | 第三方來源維持 disabled 且 public egress default-deny | 程式交付 (D)<br>測試證明 (T) | **已滿足 (met)** | `infra/terraform/modules/ephemeral_staging/main.tf` 與 `tests/ops/test_ephemeral_staging_lifecycle.py` / `infra/terraform/tests/test_ephemeral_staging.py` 驗證 default-deny VPC egress 與 external sources disabled 設定；exact-head CI success。 |
| **A7** | 失敗環境依 TTL 保留成功環境由 prod closeout 精確清理且 orphan cleanup fail closed | 測試證明 (T) | **已滿足 (met)** | `tests/ops/test_ephemeral_staging_lifecycle.py` 中 `test_cleanup_exact_label_matching_and_safety`、`test_create_failure_triggers_exact_cleanup`、`test_scan_orphans_detects_expired_and_unmanaged`、`ReleaseScopedAutoCleanupTests` 等全數通過；exact-head CI success。 |
| **A8** | focused contract tests 必須在 staging 繞過 lifecycle 或使用 dev identity/靜態 service names 時失敗 | 測試證明 (T) | **已滿足 (met)** | `tests/ops/test_deploy_workflow_contract.py`、`tests/ops/test_workflow_expression_contexts.py` 與 `tests/ops/test_ephemeral_staging_lifecycle.py` 包含負向測試，確保繞過 lifecycle 或沿用 dev 靜態名稱時必然失敗；exact-head CI success。 |
| **A9** | PR 與部署文件使用中文並說明取代關係與 rollback | 程式交付 (D)<br>執行過程 (P) | **已滿足 (met)** | PR #1041 標題 `[ReviewBus] ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001 把 ephemeral staging lifecycle 接進唯一 Runtime Release` 及 `docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md` 均使用中文並詳述取代關係與 rollback 機制。歷史執行過程標記為 `process_constraint_unverifiable`，現存文件與 PR 為可觀察 `met`。 |

---

## 4. 權限邊界與不變量原則

1. **不推斷雲端部署或授權**：本次驗收續辦確認程式碼、IaC 模組、狀態機收據生成與契約測試，未簽發 Human GO、未生成 Supervisor Release Lease、未開啟外部來源、未執行 live staging 建立或銷毀。
2. **歷史真實性保留**：
   - 原 PR #1041 (head `fada6775`) 的 7 項 CI check-runs 與原審查者 Antigravity3 之歷史 approval 原樣記錄。
   - 歷史 PR 與 merge commit SHA 完整保留。
3. **單一證據 Scope**：所有交付物嚴格局限於 `docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001/`，未修改任何產品程式、workflow、IaC 模組、runtime、validator 或全域 governance manifest。

---

## 5. 驗證方式 (Verification)

本任務交付物由以下命令驗證：

```bash
# 1. 排版與空白檢查
git diff --check

# 2. 驗收 JSON 完整性、收據序列化與 Provenance Sidecar 校驗
python3 -c 'import json
from pathlib import Path
p = Path("docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001/")
assert (p / "README.md").is_file()
x = json.loads((p / "acceptance-reconciliation.json").read_text())
assert isinstance(x, dict) and x["summary"]["reconciliation_verdict"] == "acceptance_fully_reconciled"
r = json.loads((p / "sample-secret-free-verify-receipt.json").read_text())
assert r["metadata"]["dry_run"] is True and r["metadata"]["secret_values_redacted"] is True
assert r["manifest_digest_prefix"] == "aaaaaaaaaaaaaaaa"
assert len(r["resources"]) == 9
prov = json.loads((p / "sample-secret-free-verify-receipt.provenance.json").read_text())
assert prov["classification"] == "non_execution_sample_schema_illustration"
assert prov["raw_output_reference"].endswith("sample-secret-free-verify-receipt.json")
assert len(prov["a5_acceptance_support"]["exact_head_ci_tests"]) == 5
print("All verification assertions passed!")'
```

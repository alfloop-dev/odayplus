# ODP-EPHEMERAL-STAGING-IAC-001 歷史驗收續辦與補證記錄 (2026-09-11)

## 1. 任務基本資訊 (Task Metadata)

- **任務 ID**: `ODP-EPHEMERAL-STAGING-IAC-001`
- **任務名稱**: 歷史驗收續辦：ODP-EPHEMERAL-STAGING-IAC-001（原標題：`實作 ephemeral staging 建立、隔離、TTL 與安全清理`）
- **執行身分 (Owner)**: `Antigravity2`
- **指派審查者 (Reviewer)**: `Codex2`
- **復原目標分支**: `task/ODP-EPHEMERAL-STAGING-IAC-001-RECOVERY-20260911`
- **對照基準 (Pinned Dev SHA)**: `4499a2993e37b62033926b07de8d8d2e8469a6c7`
- **所屬階段**: Wave 1 - Staging IaC / History Recovery — executable acceptance reconciliation

---

## 2. 歷史交付與事故背景 (Historical Context & Delivery)

本任務原始交付已於 2026-08-24 經由 PR [#1002](https://github.com/alfloop-dev/odayplus/pull/1002) 合併入 `dev`：
- **PR 標題**: `[ReviewBus] ODP-EPHEMERAL-STAGING-IAC-001 實作 ephemeral staging 建立、隔離、TTL 與安全清理`
- **Head SHA**: `ee6eddb6951aea752b857183a484ebc22ddf7772`
- **Merge Commit SHA**: `82ed6a05cf67c6c0e43f6f5b4219882251a87c42`
- **合併時間**: `2026-08-24T19:59:40Z`
- **原審查核准**: `Codex2` 於 2026-08-24T19:36:58Z 於 exact head `ee6eddb6951a` 留存 `task-review-gate: success`（"Approved by assigned reviewer Codex2"）
- **原 CI 檢查**: 7 項 check-runs（`product`, `performance-gate`, `product-e2e-gate`, `boundary`, `change-scope`, `classify`, `orchestrator`）全數為 `success`。

### 2026-09-06 盤點所識別之缺口 (Gaps Identified in Initial Inventory)
在 2026-09-06 歷史盤點 (`ARCHIVE_RECOVERY_EVIDENCE_20260906/task_evidence_inventory.json`) 中：
1. **A1 判定為 `partially_met`**：條款之「可重跑建立」若被解讀為需要真實雲端 `terraform apply` 的 runtime 部署收據，原 PR 1002 未帶 `docs/evidence/` 的 live 部署收據。
2. **A2 判定為 `test_delivered_not_executed_at_exact_head`**：原 PR 1002 交付之 `infra/terraform/tests/test_ephemeral_staging.py` 雖存在，但當時 `pyproject.toml` 的 `testpaths` 與七個 CI workflow 均未包含 `infra` 目錄，導致 CI 從未執行該測試檔。

---

## 3. 缺口補正與技術查核 (Gap Remediation & Verification)

### 3.1 CI 收集缺口補正（PR #1291 / `ODP-STAGING-IAC-CI-COLLECTION-001`）
後續任務 `ODP-STAGING-IAC-CI-COLLECTION-001`（PR [#1291](https://github.com/alfloop-dev/odayplus/pull/1291)，已於 2026-09-10 經 merge commit `025323f36d05c81f40085ba80c87f370d7c49c83` 合併入 `dev`）已完整解決 CI 執行問題：
1. **CI 接線**：於 `.github/workflows/ci.yml` 之 `orchestrator` job 加入釘死版本 Terraform（`1.9.8`），並在 pytest 與 ruff step 加入 `infra` target；
2. **收集路徑**：在 `pyproject.toml` 的 `testpaths` 納入 `"infra"`；
3. **離線安全 Harness 與 Fail-Closed**：在 `infra/terraform/tests/test_ephemeral_staging.py` 實作乾淨離線環境（合成 provider override、mock HOME、剝除憑證、禁止 apply/destroy/import），且在 CI 環境下嚴格 fail-closed；
4. **真實 CI 執行**：CI `orchestrator` job 實際收集並成功執行 `test_ephemeral_staging.py` 全部 18 項測試（13 項 Python/HCL 結構與契約測試 + 5 項離線 Terraform plan 測試），以及 `test_contract.py` 14 項測試（共 32 項 infra 測試）；另有 24 項 tooling 守門測試守護收集契約。

### 3.2 驗收層級界定：IaC 可重跑能力 vs 實際雲端部署
- **任務定位 (Wave 1 - Staging IaC)**：`ODP-EPHEMERAL-STAGING-IAC-001` 之職責在於交付具備可重跑建立、隔離、TTL 與安全清理能力的 Terraform 模組與 Python lifecycle 管理器（`product_ops/deployment/staging_lifecycle.py`）。
- **真實 Apply 責任歸屬**：實際 staging 環境之雲端部署與驗收屬於下游任務 `ODP-EPHEMERAL-STAGING-ROLLOUT-001`（Wave 2 - Staging Rollout）。本任務禁止亦未被授權執行 live apply，驗收聚焦於 IaC 確定性與可重跑架構能力。

---

## 4. A1–A5 逐條驗收核對 (Acceptance Criteria Reconciliation)

| 項次 | 原驗收條款原文 | 驗收層級 | 判定結果 | 核對依據與具體證據 |
|---|---|---|---|---|
| **A1** | `release-scoped namespace/service/job/database或schema/bucket/tenant/IAM 可重跑建立` | 程式交付 (D)<br>測試證明 (T) | **已滿足 (met)** | 經 PR #1002 (merge `82ed6a05cf67`) 交付之 `infra/terraform/modules/ephemeral_staging/main.tf` 與 `product_ops/deployment/staging_lifecycle.py` 完整實作了 release-scoped 資源（Cloud Run service/job、schema、bucket、tenant、IAM）的可重跑建立契約。離線 Terraform plan 測試（5 項 plan 測試）與 `tests/ops/test_ephemeral_staging_lifecycle.py`（96 項測試）全數通過，且 PR #1291 (`025323f36d05`) 已使該 18 項測試進入 CI `orchestrator` job 執行。真實雲端 apply 精確交由下游 `ODP-EPHEMERAL-STAGING-ROLLOUT-001`。 |
| **A2** | `resources 有 owner/created_at/expires_at labels` | 測試證明 (T) | **已滿足 (met)** | **缺口已由 PR #1291 補正**：`infra/terraform/modules/ephemeral_staging/main.tf` 強制所有可帶標籤資源綁定 `owner`, `created_at`, `expires_at`, `release_id`, `ephemeral=true`, `managed_by=terraform`, `candidate_sha`。`infra/terraform/tests/test_ephemeral_staging.py`（4 項標籤專屬斷言測試）與 `staging_lifecycle.py` 四重把關，在 PR #1291 CI `orchestrator` job 實跑通過。 |
| **A3** | `cleanup 只依精確 labels 且有 orphan scanner` | 測試證明 (T) | **已滿足 (met)** | `product_ops/deployment/staging_lifecycle.py` 實作嚴格以精確 `release_id` 與必要標籤清理（`EphemeralStagingLifecycle.cleanup()`），禁止廣域萬用字元；實作 `scan_orphaned_resources()` 孤兒資源掃描器。測試 `tests/ops/test_ephemeral_staging_lifecycle.py`（96 項測試）在 PR #1002 exact-head `product` CI 執行且 conclusion=success。 |
| **A4** | `成功清除失敗保留不超過 24h` | 測試證明 (T) | **已滿足 (met)** | `staging_lifecycle.py` 設定 `DEFAULT_TTL_HOURS = 24`，成功時立即清理，失敗保留除錯上限預設不超過 24 小時（`DEFAULT_FAILED_TTL_SECONDS = 86400`），TTL 延長需明確記錄 owner 與 reason（上限 168h）。由 `tests/ops/test_ephemeral_staging_lifecycle.py` 完整覆蓋並在 PR #1002 exact-head `product` CI 通過。 |
| **A5** | `不修改唯一 workflow entrypoint` | 程式交付 (D) | **已滿足 (met)** | staging lifecycle 模組作為部署工具鏈子模組，未新增第二個工作流入口。Pinned dev `.github/workflows/deploy-dev.yml` (@`4499a2993e37`) 確認 `deploy-dev.yml` 維持全系統唯一 Runtime Release workflow entrypoint。 |

---

## 5. 本地執行與驗證收據 (Verification Receipts)

於 pinned dev 基準（commit `4499a2993e37`）執行之本地測試收據：

| # | Command | Exit Code | Tests Passed | Duration | Status |
|---|---------|-----------|--------------|----------|--------|
| 1 | `git diff --check` | 0 | - | 0.01s | clean |
| 2 | `uv run pytest tests/ops/test_ephemeral_staging_lifecycle.py` | 0 | 96 passed (16 subtests) | 0.57s | success |
| 3 | `uv run pytest infra/terraform/tests/test_ephemeral_staging.py infra/terraform/tests/test_contract.py` | 0 | 32 passed (18 ephemeral + 14 contract) | 28.58s | success |

---

## 6. 權限邊界與不變量原則 (Invariants & Boundaries)

1. **不執行未授權雲端變更**：本補證工作確認了 IaC 模組定義、生命週期契約與 CI 綠燈收據，未執行任何 live GCP / Terraform apply，亦未申請 Human GO 或 Release Lease。
2. **單一寫入範圍**：所有改動嚴格限制於 `docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-EPHEMERAL-STAGING-IAC-001/`，未修改產品程式、生產環境設定或工作流。
3. **歷史真實性保留**：如實記錄原 PR #1002 CI 當時未收錄 infra tests 之歷史事實，與後續 PR #1291 補齊 CI 收集之新事實，不混淆新舊 exact head。
4. **下游依賴傳遞**：真實 staging live 部署由既有下游任務 `ODP-EPHEMERAL-STAGING-ROLLOUT-001` 承接。

---

## 7. 驗證與自動檢查命令 (Automated Verification Check)

```bash
git diff --check
python3 -c 'import json
from pathlib import Path
p = Path("docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-EPHEMERAL-STAGING-IAC-001/")
assert (p / "README.md").is_file(), "README.md missing"
data = json.loads((p / "acceptance-reconciliation.json").read_text())
assert isinstance(data, dict) and data, "invalid JSON structure"
assert data["summary"]["reconciliation_verdict"] == "acceptance_fully_reconciled", "verdict mismatch"
assert data["summary"]["criteria_met"] == 5, "criteria_met count mismatch"
print("ODP-EPHEMERAL-STAGING-IAC-001 reconciliation evidence is complete and valid.")'
```

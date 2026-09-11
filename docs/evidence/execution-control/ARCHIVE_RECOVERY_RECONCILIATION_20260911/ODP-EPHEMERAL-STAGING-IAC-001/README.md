# ODP-EPHEMERAL-STAGING-IAC-001 歷史驗收續辦與補證記錄 (2026-09-11)

## 1. 任務基本資訊 (Task Metadata)

- **任務 ID**: `ODP-EPHEMERAL-STAGING-IAC-001`
- **任務名稱**: 歷史驗收續辦：ODP-EPHEMERAL-STAGING-IAC-001（原標題：`實作 ephemeral staging 建立、隔離、TTL 與安全清理`）
- **執行身分 (Owner)**: `Antigravity2`
- **指派審查者 (Reviewer)**: `Codex2`
- **復原目標分支**: `task/ODP-EPHEMERAL-STAGING-IAC-001-RECOVERY-20260911`
- **當前基準 (Pinned Dev SHA)**: `2889b55fb1febe95c9f8650f24ead18e86015cca`（原對照基準 `4499a2993e37b62033926b07de8d8d2e8469a6c7`，已循正常流程完成 base advance 合併）
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
- **交付程式與真實符號**:
  - `product_ops/deployment/staging_lifecycle.py`: 實作清理函式 `cleanup_ephemeral_staging` (L1511)、孤兒掃描器 `scan_orphans` (L1790)、TTL 延長函式 `extend_staging_ttl` (L1864)，以及常數 `DEFAULT_TTL_HOURS = 24` (L54) 與 `MAX_TTL_HOURS = 168` (L55)。
  - `tests/ops/test_ephemeral_staging_lifecycle.py`: 原始 PR #1002 exact head 交付並由 product CI 成功驗證 86 個測試方法（`def test_`）；現行 dev 基準（@`2889b55f`）為 96 個測試方法。

### 2026-09-06 盤點所識別之缺口 (Gaps Identified in Initial Inventory)
在 2026-09-06 歷史盤點 (`ARCHIVE_RECOVERY_EVIDENCE_20260906/task_evidence_inventory.json`) 中：
1. **A1 判定為 `partially_met`**：條款之「可重跑建立」若被解讀為需要真實雲端 `terraform apply` 的 runtime 部署收據，原 PR 1002 未帶 `docs/evidence/` 的 live 部署收據。
2. **A2 判定為 `test_delivered_not_executed_at_exact_head`**：原 PR 1002 交付之 `infra/terraform/tests/test_ephemeral_staging.py` 雖存在，但當時 `pyproject.toml` 的 `testpaths` 與七個 CI workflow 均未包含 `infra` 目錄，導致 CI 從未執行該測試檔。

---

## 3. 缺口補正與技術查核 (Gap Remediation & Verification)

### 3.1 CI 收集缺口補正（PR #1291 / `ODP-STAGING-IAC-CI-COLLECTION-001`）
後續任務 `ODP-STAGING-IAC-CI-COLLECTION-001`（PR [#1291](https://github.com/alfloop-dev/odayplus/pull/1291)，已於 2026-09-10 經 merge commit `025323f36d05c81f40085ba80c87f370d7c49c83` 合併入 `dev`）已完整解決 CI 執行問題：
- **Exact Head SHA**: `726065c3aec57147f37902b94d754234dd55e25f`
- **Merge Commit SHA**: `025323f36d05c81f40085ba80c87f370d7c49c83`（合併時間 `2026-09-10T19:29:51Z`）
- **CI 執行收據**: GitHub Actions Run `34514563856` / Orchestrator Job `102996659589` 於 `2026-09-10T18:32:29Z` 完成且 conclusion 為 `success`
- **CI 查核 URL**: [Job 102996659589](https://github.com/alfloop-dev/odayplus/actions/runs/34514563856/job/102996659589)
- **CI 測試 Selection**: `uv run pytest -m "not requires_live_env" .orchestrator delivery_toolchain scripts tests/tooling infra`
- **CI 執行摘要**: `2791 passed, 6 skipped, 10 deselected, 618 subtests passed in 184.91s`
- **收集與守門套件構成**:
  1. `infra/terraform/tests/test_ephemeral_staging.py` 18 項測試全數收集並通過（14 項契約與結構測試 + 4 項離線 Terraform plan 測試）；
  2. `infra/terraform/tests/test_contract.py` 14 項測試全數通過（共 32 項 infra 測試）；
  3. `tests/tooling/test_staging_iac_ci_collection.py` 24 項 tooling 守門測試守護收集契約。
- **離線安全 Harness 與 Fail-Closed**: 合成 provider override、mock HOME、剝除 GCP 憑證、禁止 apply/destroy/import，在 CI 環境下嚴格 fail-closed。

### 3.2 驗收層級界定與架構規劃依據
依據全系統部署規劃架構文件 `docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md`（@`c80247fb7d6623c288db1a646e6c6f9dda6c14a9`）：
- **§16（執行前需要的人類決策與資料）與 §17（波次架構）**：明確定義 Wave 1 任務負責交付 IaC 定義、生命週期程式、測試與離線 plan 能力；而真實 staging 雲端環境之建立、GCP 專案/憑證/資源到位與 live 驗收屬於下游 **Wave 3 - Staging Rollout**（`ODP-EPHEMERAL-STAGING-ROLLOUT-001`）。
- **§7.3（清理與 TTL）**：明確規範 staging 失敗時預設保留 24 小時（`DEFAULT_TTL_HOURS = 24`）供除錯，TTL 延長需附帶 owner 與 reason（上限 168 小時），並由 orphan scanner 定時回收。
- **本任務權限邊界**：本任務職責在於完成 IaC 可重跑建立與生命週期模組之交付與驗收補證；禁止亦未被授權執行 live apply。

---

## 4. A1–A5 逐條驗收核對 (Acceptance Criteria Reconciliation)

| 項次 | 原驗收條款原文 | 驗收層級 | 判定結果 | 核對依據與具體證據 |
|---|---|---|---|---|
| **A1** | `release-scoped namespace/service/job/database或schema/bucket/tenant/IAM 可重跑建立` | 程式交付 (D)<br>測試證明 (T) | **已滿足 (met)** | 經 PR #1002 (merge `82ed6a05cf67`) 交付之 `infra/terraform/modules/ephemeral_staging/main.tf` 與 `product_ops/deployment/staging_lifecycle.py` 完整實作了 release-scoped 資源（Cloud Run service/job、schema、bucket、tenant、IAM）的可重跑建立契約。離線 Terraform plan 測試（4 項 plan 測試 + 14 項模組契約測試）與 `tests/ops/test_ephemeral_staging_lifecycle.py` 完整驗證建立之可重跑性與確定性。PR #1291 (`025323f36d05`, run `34514563856`) CI `orchestrator` job 實跑通過。依規劃 §16，IaC 模組與離線驗收在 Wave 1 交付，真實雲端 apply 精確交由下游 `ODP-EPHEMERAL-STAGING-ROLLOUT-001`（Wave 3）。 |
| **A2** | `resources 有 owner/created_at/expires_at labels` | 測試證明 (T) | **已滿足 (met)** | **缺口已由 PR #1291 補正**：`infra/terraform/modules/ephemeral_staging/main.tf` 強制所有可帶標籤資源綁定 `owner`, `created_at`, `expires_at`, `release_id`, `ephemeral=true`, `managed_by=terraform`, `candidate_sha`。`infra/terraform/tests/test_ephemeral_staging.py`（4 項標籤專屬斷言測試）與 `staging_lifecycle.py` 嚴格把關，在 PR #1291 CI `orchestrator` job（run `34514563856`）實跑通過；`tests/ops/test_ephemeral_staging_lifecycle.py` 亦於 PR #1002 exact-head product CI 成功驗證。 |
| **A3** | `cleanup 只依精確 labels 且有 orphan scanner` | 測試證明 (T) | **已滿足 (met)** | `product_ops/deployment/staging_lifecycle.py` 實作嚴格以精確 `release_id` 與必要標籤清理之函式 `cleanup_ephemeral_staging` (L1511)，禁止廣域萬用字元；實作 `scan_orphans` (L1790) 孤兒資源掃描器。測試 `tests/ops/test_ephemeral_staging_lifecycle.py`（PR #1002 exact head 86 項測試方法）在 PR #1002 exact-head `product` CI 執行且 conclusion=success。 |
| **A4** | `成功清除失敗保留不超過 24h` | 測試證明 (T) | **已滿足 (met)** | `staging_lifecycle.py` 設定 `DEFAULT_TTL_HOURS = 24` (L54)，成功時立即清理，失敗保留除錯上限預設不超過 24 小時。依據架構規劃 §7.3，延長 TTL 需附帶明確 owner 與 reason（`MAX_TTL_HOURS = 168`, L55，由 `extend_staging_ttl` (L1864) 控制）。由 `tests/ops/test_ephemeral_staging_lifecycle.py` 完整覆蓋並在 PR #1002 exact-head `product` CI 通過。 |
| **A5** | `不修改唯一 workflow entrypoint` | 程式交付 (D) | **已滿足 (met)** | staging lifecycle 模組作為部署工具鏈子模組，未新增第二個工作流入口。Pinned dev `.github/workflows/deploy-dev.yml` (@`2889b55f`) 確認 `deploy-dev.yml` 維持全系統唯一 Runtime Release workflow entrypoint。 |

---

## 5. 下游責任映射與依賴無環驗證 (Downstream Mapping & DAG Integrity)

### 5.1 驗收條款至下游任務逐條映射 (Criteria-to-Downstream Mapping)
在 live board 上，下游 Staging Rollout 任務 `ODP-EPHEMERAL-STAGING-ROLLOUT-001` 目前狀態為 `blocked`（`waiting_for: Human/Ops`），所屬階段為 `Wave 3 - Staging Rollout`。其承接之真實運行期責任如下：

| 條款 | 本任務（Wave 1 - Staging IaC）交付與補證邊界 | 下游 `ODP-EPHEMERAL-STAGING-ROLLOUT-001`（Wave 3）真實運行期責任 |
|---|---|---|
| **A1** | Terraform 模組定義、release-scoped 命名/隔離契約、離線 plan 與 Python lifecycle 邏輯 | 實際雲端 release-scoped 資源之建立、部署與真實 apply 驗證 |
| **A2** | 模組標籤 AST 靜態約束與 Python 標籤檢查邏輯 | 雲端實體資源標籤之 live inspection 與 GCP Console/API 稽核 |
| **A3** | 精確標籤清理函式 `cleanup_ephemeral_staging` 與孤兒掃描器 `scan_orphans` | 實際雲端清理與真實定時孤兒掃描器之 live rehearsal |
| **A4** | 24h TTL 狀態機計算、失敗保留上限與 `extend_staging_ttl` 契約 | 真實 staging 失敗環境 24h 保留與 TTL 自動回收之 live 運行驗證 |
| **A5** | 單一 entrypoint 架構維護，無旁路 workflow | 經由唯一 workflow `deploy-dev.yml` 執行完整 staging release rehearsal |

### 5.2 依賴關係對照與無環驗證 (Dependency Before/After & Cycle Check)
- **本任務依賴 (Dependencies)**:
  - Before: `["ODP-RELEASE-MANIFEST-GATES-001"]` (done)
  - After: `["ODP-RELEASE-MANIFEST-GATES-001"]` (done, unchanged)
- **下游依賴本任務 (Downstream Dependents)**:
  - Before: `["ODP-EPHEMERAL-STAGING-ROLLOUT-001"]` (blocked)
  - After: `["ODP-EPHEMERAL-STAGING-ROLLOUT-001"]` (blocked, unchanged)
- **無環驗證 (DAG Cycle Check)**:
  - 拓撲路徑：`ODP-RELEASE-MANIFEST-GATES-001` (done) $\rightarrow$ `ODP-EPHEMERAL-STAGING-IAC-001` (review) $\rightarrow$ `ODP-EPHEMERAL-STAGING-ROLLOUT-001` (blocked)
  - `cycle_detected: false`（無任何循環依賴）。
- **未完成之外部 Foundation / Rollout / Watch-Closeout Gates（保留於下游）**:
  1. Human GO 與 Release Lease 授權；
  2. 真實 GCP staging 專案、WIF 憑證與 service account 授權；
  3. Cloud SQL、Cloud Run、Cloud Scheduler 與 GCS 雲端基礎設施實體到位；
  4. staging Web 自訂網域 `console-staging.oday-plus.com.tw` 之 DNS、受管憑證與 OAuth callback 驗證；
  5. `RELEASE_MANIFEST.json` 真實 image digests 替換（非 placeholder digest）。

---

## 6. 收據記錄與資料來源 (Receipts & Provenance)

1. **歷史 exact-head CI 收據**:
   - PR #1002: 7 項 check-runs 全數 success，Codex2 review approval at `ee6eddb6951a`。
   - PR #1291: GitHub Actions run `34514563856` / job `102996659589` at `726065c3aec5`，18 項 ephemeral staging 測試與 24 項 tooling 測試全數 pass。
2. **本地歷史終端輸出紀錄狀態**:
   - 歷史本機終端輸出原 meta-data 屬未保存（`historical_local_raw_terminal_output_status: unknown`）；以 GitHub Actions exact-head 遠端收據為具體耐久憑證。
3. **Base Advance 合併收據**:
   - Merge Base: `2889b55fb1febe95c9f8650f24ead18e86015cca` (PR #1310)
   - 合併策略: `ort`，無衝突。

---

## 7. 權限邊界與不變量原則 (Invariants & Boundaries)

1. **不執行未授權雲端變更**：本補證工作確認了 IaC 模組定義、生命週期契約與 CI 綠燈收據，未執行任何 live GCP / Terraform apply，亦未申請 Human GO 或 Release Lease。
2. **單一寫入範圍**：所有改動嚴格限制於 `docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-EPHEMERAL-STAGING-IAC-001/`，未修改產品程式、生產環境設定或工作流。
3. **歷史真實性保留**：如實記錄原 PR #1002 CI 當時未收錄 infra tests 之歷史事實，與後續 PR #1291 補齊 CI 收集之新事實，不混淆新舊 exact head。
4. **下游依賴傳遞**：真實 staging live 部署由既有下游任務 `ODP-EPHEMERAL-STAGING-ROLLOUT-001` (Wave 3) 承接。

---

## 8. 驗證與自動檢查命令 (Automated Verification Check)

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
assert data["downstream_responsibilities_and_dependency_mapping"]["dependency_graph_reconciliation"]["cycle_detected"] is False
print("ODP-EPHEMERAL-STAGING-IAC-001 reconciliation evidence is complete and valid.")'
```

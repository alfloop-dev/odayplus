# 歷史驗收續辦：ODP-DEV-STAGED-GATE-RECONCILIATION-001

- **任務 ID**: `ODP-DEV-STAGED-GATE-RECONCILIATION-001`
- **原任務標題**: `重整 staged dev release gate 與現行 artifact 的 exact reconciliation`
- **續辦任務標題**: `歷史驗收續辦：ODP-DEV-STAGED-GATE-RECONCILIATION-001`
- **任務類別**: `documentation` / `phase: History Recovery — executable acceptance reconciliation`
- **實作負責人 (Owner)**: `Antigravity4`
- **獨立審查人 (Reviewer)**: `Codex`
- **記錄時間**: 2026-09-11T11:42:00Z
- **續辦交付分支**: `task/ODP-DEV-STAGED-GATE-RECONCILIATION-001-RECOVERY-20260911` (base: `4499a2993e37b62033926b07de8d8d2e8469a6c7`)

---

## 1. 任務背景與續辦目的 (Context & Purpose)

依使用者明確指示，歷史 archive 遺失之各項恢復任務由 Supervisor Auto Worker 接續辦理。本任務 `ODP-DEV-STAGED-GATE-RECONCILIATION-001` 針對歷史已合併 PR [#1193](https://github.com/alfloop-dev/odayplus/pull/1193) 的 staged dev release gate 程式交付、測試覆蓋與驗收條款進行精確復原核對。

歷史第 4 輪盤點（`ARCHIVE_RECOVERY_EVIDENCE_20260906`）指出的主要缺口為：
1. **A3–A6 測試執行缺口與精確收據**：
   - 歷史 PR #1193 交付之測試檔 `tests/e2e/test_release_gate_registry.py` 在精確 head `40bb0246` 上，因 `product` job conclusion 為 `skipped`（scope skip），且 `orchestrator` job 不收集 `tests/e2e/`，致使該測試檔在當時 PR check-runs 中未留有執行收據（標記為 `test_delivered_not_executed_at_exact_head`）。
   - 本續辦嚴格區分「歷史 PR head 事實」與「後續候選 CI 執行證明」：復用後續已合併之候選重整任務 `ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002` (PR #1292, merge `3613faff582bd1c5a2c9b2ae5b5cd390d8be381f`) exact head $E = \text{d084f51d4009b7b435416c8b83410a8b4fb4a267}$ 之 GitHub Actions CI product job `102911032625` (run `34489132477`) step `Test product code`（started_at: `2026-09-10T14:28:25Z`、completed_at: `2026-09-10T14:44:06Z`、step window 941s、pytest elapsed `931.33s`、conclusion: `success`）之執行收據。Runner checkout 為 synthetic merge `72d34caede35e3df4c7391628524c84bb36e6b68`，經核對與 PR head $E$ 在 `test_release_gate_registry.py`、`check_release_gate_registry.py`、`.github/workflows/ci.yml`、`tests/conftest.py`、`pyproject.toml`、`RELEASE_GATE_REGISTRY.json`、`RELEASE_MANIFEST.json` 具有完全相同之 blob SHAs。
   - 具名測試認列為 collection 與 static assertions 包含於成功 step 的推論，quiet log 未逐一列出具名測試 PASSED 行，不冒稱個別 assertion log 已逐行查核。
   - 明確釐清：`product-e2e-gate` 工作由固定 `PYTEST_NODE_IDS` 與 playwright specs 構成，不含 `test_release_gate_registry.py`，不可並列為該測試之執行證據。歷史 head `40bb0246` 的 skipped 事實獨立保留，不以 $E$ 的 CI 冒充原 PR head 的 CI。本機不可追回之 terminal 日誌標記 unknown/null，不虛構退出數據、不為統計重跑。
2. **A7 邊界與運行態姿勢分開**：
   - PR #1193 為純 gate 檢查器與測試交付，不涉及 live runtime 部署或憑證投影。
   - 靜態契約面由既有 `.github/workflows/deploy-dev.yml` 保留 sources-off 預設（`external_sources_enabled` 預設為空值，`ODP_COMPETITOR_MANUAL_SOURCE_STATUS: disabled`，`ODP_EXTERNAL_PROVIDER_MODE: disabled`）；
   - 真正 live runtime 姿勢（來源 disabled、zero credentials、default-deny egress）屬後續 rollout 補救任務（`ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 驗收 A6/A7）範疇，本 code/gate 任務記為 `partially_met`，不倒灌部署要求。

---

## 2. 歷史交付與後續 Done Receipts 核對 (Historical Delivery & Receipts)

### 2.1 歷史交付事實

| 查核項目 | 證據來源與形式 | 結果 | 詳細說明與數值 |
|---|---|---|---|
| **歷史 Pull Request** | GitHub PR [#1193](https://github.com/alfloop-dev/odayplus/pull/1193) | `MERGED` | `head_sha: 40bb02462ec742cfb615214c1873f15a40b2c602`，`merge_sha: efdfea1a0c31c3c9ebcb2acfe1eb61dc4fa2bff6`，合併時間 `2026-09-04T09:47:46Z`。 |
| **PR 交付檔案** | Git Tree Diff (`40bb0246` vs base) | `true` | 交付 2 檔：`delivery_toolchain/e2e/check_release_gate_registry.py` (+23 / -1) 與 `tests/e2e/test_release_gate_registry.py` (+41)。於 merge commit `efdfea1a` 為 (+43 / -6) 與 (+92)。 |
| **宣告 Artifacts** | Merge commit 與 dev tip 檔案存在性 | `true` | 宣告之 6 份 artifacts（`RELEASE_GATE_REGISTRY.json`、`RELEASE_MANIFEST.json`、`check_release_gate_registry.py`、`check_runtime_admission.py`、`test_release_gate_registry.py`、`test_runtime_admission.py`）於 merge commit 及現行 `dev` 全數存在。 |
| **歷史 CI 檢核** | Exact-head check-runs | `true` | PR #1193 head `40bb0246` 上，`change-scope`、`boundary`、`classify`、`orchestrator` 均為 `success`；無關之 `product`、`performance-gate`、`product-e2e-gate` 依 scope skipped。 |
| **歷史審查批准** | task-review-gate commit status | `true` | `40bb0246` 具名記錄 `task-review-gate: success`，描述 `Approved by assigned reviewer Claude2`（2026-09-04T09:20:32Z）。 |

### 2.2 後續候選與已完成 Done Receipts 核對

| 任務 ID | PR 與 Head SHA | 審查門禁與狀態 | 候選座標與證據連結 | 治理與准入判定 |
|---|---|---|---|---|
| `ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002` | PR #1292<br>Head `d084f51d`<br>Merge `3613faff` (`2026-09-10T18:58:07Z`) | Candidate exact CI: `SUCCESS`<br>(run `34489132477`, product job `102911032625`, step 10 `Test product code`, started `14:28:25Z`, completed `14:44:06Z`, pytest `931.33s`) | 凍結基準 $C = \text{596b9c9a1788d952811a2bf8d4bba8a4e4d76b12}$<br>Manifest digest $\text{sha256:1b5348d907643e3f3087642d8674002beec016bd16ef38b00e261e88da026e6c}$<br>Source evidence $E = \text{d084f51d4009b7b435416c8b83410a8b4fb4a267}$<br>Tested merge `72d34caede35` blob 等同 | 建立不可變之 candidate C exact binding，七道 gate 維持 fail-closed blocked (NO-GO)。 |
| `ODP-DEV-CANDIDATE-CODE-RECEIPT-001` | PR #1301<br>Head `11dd0666f7f7e1860ba8522dcd50d51fec65defe`<br>Merge `b20118700dd5e6be4834d7dd1ca8753d578ee4ff` (`2026-09-11T03:54:56Z`) | Gate 0 (Code Gate)<br>Status: `blocked`<br>Decision: `no-go` | 綁定 Candidate $C$, Manifest digest, Source evidence $E$ | 交付 Code Gate 審查收據包，Task archived done；Gate 0 維持 fail-closed blocked (NO-GO)。 |
| `ODP-DEV-CANDIDATE-CONTRACT-RECEIPT-001` | PR #1299<br>Head `aaff8356b9fc68912668a951627131d09bd6308d`<br>Merge `e4a52ae9729bcd0ccc7e3ceae792dc251f040d1d` (`2026-09-11T03:25:46Z`) | Gate 1 (Contract Gate)<br>Status: `blocked`<br>Decision: `no-go` | 綁定 Candidate $C$, Manifest digest, Source evidence $E$ | 交付 Contract Gate 審查收據包，Task archived done；Gate 1 維持 fail-closed blocked (NO-GO)。 |
| `ODP-DEV-CANDIDATE-SECURITY-RECEIPT-001` | PR #1297<br>Head `d52e457707b2aebfc885ba0170c4cc8691c3383b`<br>Merge `4b6a43b3e60196c9cef2dbe71c24ed6416d320f7` (`2026-09-11T02:46:07Z`) | Gate 4 (Security Gate)<br>Status: `blocked`<br>Decision: `no-go` | 綁定 Candidate $C$, Manifest digest, Source evidence $E$ | 交付 Security Gate 審查收據包，Task archived done；Gate 4 維持 fail-closed blocked (NO-GO)。 |

> [!IMPORTANT]
> 上述三項收據任務完成（task done）代表審查證據包整理完畢，**不等於**准入閘門放行（admission cleared）。Gate 0、Gate 1、Gate 4 依 fail-closed 原則均維持 `blocked` 且 release decision 為 `NO-GO`。

---

## 3. A1–A9 逐條驗收核對結果 (Acceptance Criteria Reconciliation)

| 項次 | 原驗收條款摘要 | 類別 | 判定結果 | 核對依據與分析說明 |
|---|---|---|---|---|
| **A1** | 等待 ODP-SUPPLY-CHAIN-LOCKFILE-CONSISTENCY-001 合併後才選定最新 origin/dev candidate | 執行過程約束 (P) | **過程不可獨立重現 (process_constraint_unverifiable)** | 歷史 PR #1193 於 2026-09-04T09:47:46Z 合併 (`efdfea1a`)，PR #1183 於同日 12:18:30Z 合併 (`5fe790edc453df0efcb7efcbf750dc305b37fe59`)。後續候選重整任務 `ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002` 選定之凍結候選基準 $C$ (`596b9c9a`) 經 `git merge-base --is-ancestor` 驗證已同時包含兩者。時序屬歷史執行過程約束，依規範註記，不計入 passed 亦不構成結案阻塞。 |
| **A2** | 只使用既有 STAGE_CONTRACT 與 Runtime Release 單一路徑 | 程式交付 (D) | **已滿足 (met_by_delivered_artifact)** | PR #1193 交付之 `check_release_gate_registry.py` 與既有 `check_runtime_admission.py` 嚴格遵循單一 STAGE_CONTRACT 與 Runtime Release 准入路徑，未新增第二條 gate/lease/release workflow。交付事實由 pinned merge commit `efdfea1a` 與 dev tip 檔案存在性證明；不可追回之本機 terminal 日誌收據標記 unknown/null，不虛構退出收據。 |
| **A3** | candidate-built dev boundary 僅接受有同 SHA 同 manifest digest 真實 receipt 的 cleared gate | 可由測試證明 (T) | **由後續候選 CI 執行證明 (met_by_successor_test_execution)** | PR #1193 交付具名斷言：`test_go_decision_with_no_gates_bound_to_admission_target_is_rejected`、`test_dev_go_decision_requires_dev_boundary_gates_cleared`、`test_gate_pinned_to_a_different_sha_than_the_candidate_is_rejected`、`test_passed_gate_with_a_stale_receipt_is_rejected`。歷史 head `40bb0246` product job skipped 事實保留；在後續 PR #1292 exact head $E$ (`d084f51d`，tested merge `72d34cae` 具同等 blob SHAs) 上，GitHub CI product job `102911032625` step `Test product code`（started 14:28:25Z, completed 14:44:06Z, pytest elapsed `931.33s`）收集並執行成功（conclusion: success）。具名測試認列為 collection 與 static assertions 推論，quiet log 未逐一列出具名測試 PASSED 行，不冒稱個別 assertion log 已查核；本機 terminal 日誌標記 unknown/null，不重跑測試。 |
| **A4** | staging 或 production boundary gate 不得反向阻擋 dev | 可由測試證明 (T) | **由後續候選 CI 執行證明 (met_by_successor_test_execution)** | 具名測試 `test_staging_and_prod_gates_do_not_block_dev_go_decision` 明確驗證：當 dev boundary gate（gate-0, gate-1, gate-4）cleared 時，即使 staging（gate-2）或 prod（gate-3, gate-5, gate-6）blocked，dev 的 GO 決策依然被接受，不會被反向阻擋。具名測試 `test_blocking_gates_filters_by_target` 亦在 PR #1292 product job `102911032625` step `Test product code` 執行成功（conclusion: success）。 |
| **A5** | 無證據的 gate 保持 blocked | 可由測試證明 (T)<br>程式交付 (D) | **由後續候選 CI 執行證明 (met_by_successor_test_execution)** | 具名測試 `test_blocked_baseline_records_no_go_with_zero_receipts` 與 `test_passed_gate_without_evidence_or_receipt_is_rejected` 驗證在缺少合法 receipt 時閘門必須保持 blocked 且決策為 NO-GO。在 PR #1292 product job `102911032625` step `Test product code` 執行成功（conclusion: success）。本機 terminal 日誌收據標記 unknown/null，沿已接受之 successor CI 證明，不虛構退出收據、不重跑測試。 |
| **A6** | release decision go 必須保留真實 human_signoff 且不得偽造 | 可由測試證明 (T)<br>人類授權邊界 (H) | **由後續候選 CI 執行證明 (met_by_successor_test_execution)** | 具名測試 `test_go_decision_requires_human_signoff` 驗證未帶 human_signoff 之 GO 決策會被拒絕。在 PR #1292 product job `102911032625` step `Test product code` 執行成功。否定約束完全遵守：本任務未簽發任何 release GO，未簽發 release lease，未偽造人類簽署。 |
| **A7** | 第三方來源保持 disabled 且無 credentials 與 default deny egress | 程式交付 (D)<br>runtime／部署 (R) | **部分滿足 (partially_met)** | 靜態契約面（D）：`.github/workflows/deploy-dev.yml` 明載 `external_sources_enabled` 預設為空值（standing sources-off posture）、`ODP_COMPETITOR_MANUAL_SOURCE_STATUS: disabled`、`ODP_EXTERNAL_PROVIDER_MODE: disabled`，且 `ODP_CLOUD_RUN_VPC_EGRESS` 綁定環境變數。運行態面（R）：本任務不執行 live 部署與憑證注入。Live posture 留待後續 live rollout 任務（`ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 驗收 A6/A7）驗證，不倒灌部署要求。 |
| **A8** | 本 task 不簽 lease 不 dispatch deploy | 執行過程約束 (P) | **過程不可獨立重現 (process_constraint_unverifiable)** | 歷史 PR #1193 及本次 recovery 過程中，於目前可證明之範圍內查無簽發 release lease 或觸發 `workflow_dispatch` 部署命令之紀錄。依規範註記為 process_constraint_unverifiable，不把 process unknown 上升格為完整歷史證明，亦不構成結案阻塞。 |
| **A9** | PR 文件中文並只跑 focused regression 與一次 PR CI | 執行過程約束 (P) | **過程不可獨立重現 (process_constraint_unverifiable)** | 歷史 PR #1193 標題與內文均為正體中文，commit trailers 具名指定 focused 驗證指令。本次 recovery 交付文件亦嚴格使用正體中文。歷史執行次數記錄依規則註記，不構成結案阻塞。 |

### 驗收統計摘要
- **總條款數**: 9
- **已滿足 (Met)**: 5 條 (A2, A3, A4, A5, A6)
- **部分滿足 (Partially Met)**: 1 條 (A7，靜態契約已滿足，運行態留待下游 rollout)
- **歷史過程約束 (Process Constraint Unverifiable)**: 3 條 (A1, A8, A9，不計入 passed，不構成阻塞)
- **未滿足 (Unmet)**: 0 條

---

## 4. 依賴與下游任務承接 (Dependencies & Downstream Handback)

### 4.1 下游任務映射與依賴狀態

```
[ODP-DEV-STAGED-GATE-RECONCILIATION-001] (Leaf upstream, 0 dependencies)
   │
   └──► [ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001] (Blocked downstream, 10 dependencies)
```

- **下游任務 ID**: `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`
- **下游任務標題**: `以真實 artifact 完成 dev live rollout 並取代 false-done 前提`
- **目前狀態**: `blocked` (Wave 3 - Dev Live Rollout Remediation)
- **依賴清單比對 (Before vs After)**:
  - **Before (10 項)**:
    1. `ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001`
    2. `ODP-RUNTIME-RELEASE-SINGLE-PATH-001`
    3. `ODP-GITHUB-GCP-ENV-BOOTSTRAP-001`
    4. `DPF-EMGI-LIVE-ROLLOUT-001`
    5. `ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001`
    6. `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002`
    7. `ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001`
    8. `ODP-DEV-STAGED-GATE-RECONCILIATION-001`
    9. `ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002`
    10. `ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001`
  - **After (10 項)**: `before == after`（完全一致，未修改 canonical 依賴關係）。
- **依賴循環檢查**: `no_cycle_detected`（有向無環圖確認，本任務無上游依賴）。

### 4.2 條款承接與精確未齊輸入

1. **條款承接映射**:
   - 本任務 **A7**（第三方來源保持 disabled 且無 credentials 與 default deny egress）的 live runtime 部分，精確映射至 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 之：
     - **A6**: `Cloud Run jobs one-shot、API/Web authenticated smoke、contract、provider-off與default-deny egress均通過`
     - **A7**: `16個第三方來源保持disabled且provider credentials不存在`
2. **下游未齊輸入 (Missing Inputs)**:
   - dev 環境變數與密鑰配置（`ODP_WEB_BASE_URL` 與 `ODP_IDENTITY_TOKEN_SIGNING_KEY_SECRET` 尚未在 GitHub environment 設定）；
   - Gate 0, Gate 1, Gate 4 正式准入核准（目前維持 `blocked` / `NO-GO`，需 Human/Ops 風險決策）；
   - 經授權簽發之 Supervisor release lease；
   - 真實 GCP dev 部署執行日誌與 live readback 數據。
3. **下一步 (Next Step)**:
   - 本任務完成補證與 PR 合併後，建立不可變之 staged dev gate 契約基礎；
   - 下游 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 待前置條件齊備後於自身生命週期推進。

---

## 5. 不變量與治理邊界聲明 (Governance Invariants)

1. **未偽造或簽發 Human GO**: 本任務嚴格定位於歷史 gate 程式與驗收補證，未偽造任何人類授權。
2. **未簽發 Release Lease**: 本任務未呼叫 lease issue 工具，未簽發任何 release lease。
3. **未觸發未授權部署**: 未對 dev、staging、production 發出任何部署或工作流程分派。
4. **未外洩或讀取 Credentials**: 本任務未讀取任何認證金鑰或敏感憑證。
5. **Exact-Head 驗證完整性**: 歷史 PR #1193 head SHA (`40bb0246`)、merge SHA (`efdfea1a`)、後續候選基準 SHA (`596b9c9a`) 及 CI check-runs 均完整保留溯源。
6. **單一寫入範圍限制**: 本次所有補證交付嚴格局限於 `docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-DEV-STAGED-GATE-RECONCILIATION-001/`。

---

## 6. 驗證方式 (Verification Commands)

本任務交付物由以下命令驗證：

```bash
git diff --check
python3 -c '
import json
from pathlib import Path
base = Path("docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-DEV-STAGED-GATE-RECONCILIATION-001")
assert (base / "README.md").is_file(), "README.md missing"
assert (base / "acceptance-reconciliation.json").is_file(), "acceptance-reconciliation.json missing"
data = json.loads((base / "acceptance-reconciliation.json").read_text(encoding="utf-8"))
assert data["task_id"] == "ODP-DEV-STAGED-GATE-RECONCILIATION-001"
assert data["acceptance_criteria_summary"]["total_criteria"] == 9
assert data["acceptance_criteria_summary"]["met_criteria"] == 5
assert data["acceptance_criteria_summary"]["partially_met_criteria"] == 1
assert data["acceptance_criteria_summary"]["process_constraint_unverifiable_criteria"] == 3
assert data["acceptance_criteria_summary"]["all_criteria_technically_satisfied"] is False
print("ODP-DEV-STAGED-GATE-RECONCILIATION-001 reconciliation verified successfully.")
'
```

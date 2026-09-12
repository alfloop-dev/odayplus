# XR-EXT-OSS-FINAL-AUDIT-001 驗收核對與技術閉合補證記錄 (2026-09-12)

## 1. 任務背景與復原目標

- **任務 ID**: `XR-EXT-OSS-FINAL-AUDIT-001`
- **任務名稱**: 第三方與 OSS 技術閉合稽核（B14 CI token wiring）
- **執行身分 (Owner)**: `Antigravity4`
- **指派審查者 (Reviewer)**: `Codex`
- **原始負責人 / 審查者**: `Codex2` / `Codex`
- **階段 (Phase)**: `Third-party data production closeout / History Recovery`
- **復原目標分支**: `task/XR-EXT-OSS-FINAL-AUDIT-001-RECOVERY-20260911`
- **對照基準 (Pinned Dev Base)**: `ef8345bce29436ce86bd1a70b857d668ac16182a`

本任務核心目標為在 Producer readiness（`DPF-EXTERNAL-SOURCE-PRODUCTION-READINESS-001`）與 ODayPlus snapshot consumer（`ODP-XR-CUTOVER-ACTIVATE-002`、`ODP-XR-PROVIDER-OFF-DEPLOYMENT-001`）完成後，重算並驗證跨 repo 之技術證據鏈。

**重要結論與依賴邊界**：
- 本任務經由跨 repo 核正，確認 primary delivery 在 `alfloop-dev/oday-data-platform` PR #61 (merge `7b0670d7`)。
- 靜態程式碼與契約缺口已在代碼中實質閉合。
- 但原驗收 A1–A3 包含真實資料庫 snapshot readback、實體叢集 runtime pod digest / secret volume 投影量測、GCP VPC Flow Logs / egress live 阻斷日誌，以及 11 軸 / 44,471 筆歷史比對等運行期量測缺口。
- 因此本 ID 原驗收**尚未完成**，目前**不可 closeout 為 done**，`HUMAN-OSS-LEGAL-APPROVAL-001` 的技術前置依賴**不得視為已滿足**。
- 未承接之歷史比對原子項保留於本 ID，並列明精確缺失輸入與下一步。
- 本 PR 僅提交歷史盤點與技術缺口收據，不手動修改控制平面，不豁免原條款，不解除依賴。

---

## 2. Repository 正確性核正與候選誤配釐清

在 2026-09-06 archive 事故後的歷史盤點中，本任務曾被暫記為誤配候選 `alfloop-dev/odayplus` PR #996。本輪經全面唯讀核對完成精確釐清與核正：

### 2.1 誤配候選 PR #996 角色釐清
- **候選 PR**: `alfloop-dev/odayplus` PR [#996](https://github.com/alfloop-dev/odayplus/pull/996)
- **分支與任務標頭**: 分支為 `task/ODP-LEGACY-DISPOSITION-RUNTIME-GATES-001`，PR body ReviewBus 宣告之任務 ID 亦為 `ODP-LEGACY-DISPOSITION-RUNTIME-GATES-001`（負責人 `Codex2`，評審人 `Codex`）。
- **關係說明**: PR #996 標題提及本任務係因其將 runtime gate closure invariant 納入 ODayPlus disposition v2 以「支援 XR-EXT-OSS-FINAL-AUDIT-001 B10/B11」。PR #996 是本任務跨 repo 證據鏈中 consumer 端之 pinned merge 證據，並非本任務之 primary delivery PR。本任務宣告之三個交付檔案在 `odayplus` 歷史中本即不存在。

### 2.2 真正交付 Repository 與 PR 驗證
- **真正交付 Repository**: **`alfloop-dev/oday-data-platform`**
- **交付 PR**: PR [#61](https://github.com/alfloop-dev/oday-data-platform/pull/61)
- **交付分支**: `task/XR-EXT-OSS-FINAL-AUDIT-001`
- **精確審查 Head SHA**: `b1824c979aca008da10aed01fbc0c0a269c581dc`
- **合併 Commit SHA**: `7b0670d7b37e59e06bc9fea5b6003d1964be2c3c`
- **合併時間**: `2026-08-24T05:54:54Z`（於 Reviewer 核准後 9 秒自動合併入 `dev`）
- **宣告產物交付完整性**: 宣告之 3 項產物全部完整存在於 `oday-data-platform` @ `7b0670d7`：
  1. `docs/evidence/final/XR-EXT-OSS-FINAL-AUDIT-001/`（含 `README.md`、`closeout-audit-manifest.json`、`cross-repo-evidence-ledger.json`、`source-permission-matrix.json`、`technical-gap-closure-matrix.json`）
  2. `scripts/audit_external_oss_closeout.py`
  3. `tests/integration/test_external_oss_closeout.py`

### 2.3 PR #61 精確 Head CI 與審查閘門收據
- **CI 檢查結論**: 7 項 required check-runs 全數 `success`（執行區間 2026-08-24T05:50:55Z 至 05:54:06Z）：
  - `source-suite`: `success` (05:50:55Z - 05:54:06Z, job `97335294828`)
  - `closeout-audit`: `success` (05:50:55Z - 05:51:52Z, job `97335294567`，實際執行 `audit --check` 與 55 項測試)
  - `producer-compatibility`: `success` (05:50:56Z - 05:51:08Z)
  - `consumer-readback`: `success` (05:50:57Z - 05:51:02Z)
  - `contract-bundles`: `success` (05:50:55Z - 05:52:26Z)
  - `emgi-task-manifest`: `success` (05:50:55Z - 05:51:01Z)
  - `Build and smoke test images`: `success` (05:50:55Z - 05:52:23Z)
- **審查閘門**: Commit status `task-review-gate` 於 2026-08-24T05:54:45Z 標記 `state=success`，描述為 `Approved by assigned reviewer Codex`，與 brief 登記者完全相符。

---

## 3. 跨 Repo 證據與技術閉合收據查核（區分歷史事實與來源缺口）

本任務交付物於 `7b0670d7` 包含完整之跨 repo 唯讀技術收據，本輪依據 reviewer 查核意見精確區分已證實之程式碼確定性與運行期量測來源缺口：

| 收據檔案 / 區塊 | 查核核心指標 | 查核量測結果與具體依據 | 來源分析與缺口備註 |
|---|---|---|---|
| **`cross-repo-evidence-ledger.json`**<br>(Producer 交付) | Producer 影像與契約 | Producer PR #60 (merge `d3069c93d2f08884c9c3014c8e9326f2fbd08813`)，4 項 required workflow PASS；影像 digest `asia-east1-docker.pkg.dev/alfaloop-data-project/oday-plus-dev/oday-data-platform@sha256:f6934705cf94de1b2385d773c26e1bb9816ee8f31204859a8c62d0891d4e0e5a`、SBOM `sha256:df9238a185857bfaf80a0f79377584dc23463a0f099b302ed1017116bdfcba14`、NOTICE `sha256:e3aa79c2c1877c53cc380eca8b1858a33158d037a1a8965a419c61ef4efa3327`、contract lock `sha256:c2c153fbcd41c28e282e70f3d2a301fe42f2a9d6013ffb64ba99768cb976a72e`。 | **已證實**：產物完整存在且 SHA 確定性吻合。 |
| **`cross-repo-evidence-ledger.json`**<br>(Consumer 交付) | Consumer 鏡像節點 | 包含 `odayplus` PR #995 (`9199e59f5e36a716800ca0caade6cf13b2a75620`)、PR #996 (`0dc5cebc90cf3a55c0e2805459bcdda19f9c4e36`)、PR #991 (`b32fd65f4e60e4814b6b96bf074c5dc34dec12d4`)、PR #983 (`355195dbc853ae14fb227bb84fa49a4ce7e0db67`) 之 pinned SHA。 | **已證實**：Consumer 端 4 個 PR 均已合併入 `dev`（PR 991 精確 merge SHA 為 `b32fd65f4e60e4814b6b96bf074c5dc34dec12d4`）。 |
| **`cross-repo-evidence-ledger.json`**<br>(七大品質軸) | 跨 Repo 品質指標 | 7 軸全數 PASS：checksums 11/11、consumer_readback 11/11、counts 11/11、coverage 11/11、freshness 11/11、lineage 11/11、identities 44471/44471。 | **來源缺口**：七軸源自 `XR-DUAL-RUN-RECONCILE-002/emgi-cutover-receipt.json`，由 `reconcile_legacy_external_data.py:588-630` 自產 records 並固定 `FULL_COVERAGE/CURRENT`，屬合成驗證。 |
| **`cross-repo-evidence-ledger.json`**<br>(Snapshot 回讀) | 資料域與記錄回讀 | Snapshot readback 覆蓋 8/8 domains、共 54,443 筆記錄。 | **來源缺口**：`verify_external_egress_off.py:216-289` 固定各域筆數與 `LOCAL_SNAPSHOT_READ_SUCCESS`，無原始資料庫讀取輸入。 |
| **`source-permission-matrix.json`** | 外部來源啟用狀態 | 總來源數 16，`enabled_sources=0`，`sources_with_receipt=0`，`running_schedules=0`，`public_egress_open=false`。11 個 SCHEDULED 來源為 `STOPPED`，5 個 ON_DEMAND/EVENT_DRIVEN 為 `NOT_SCHEDULED`。 | **已證實**：`SOURCE_UPDATE_POLICIES` 靜態定義全關；運行期流量證明則為 mock。 |
| **`cross-repo-evidence-ledger.json`**<br>(Egress 審查) | 外部網路預設拒絕 | NetworkPolicy `emgi-default-deny-public-egress`（manifest `349c0446518c04e1cc9cbed412cdf70b831272b345b17ba00f5169c34477643a`），`default_deny=true`，`public_internet_blocked=true`，CIDR 僅限 RFC1918、metadata `169.254.169.254/32` 與 Google APIs `199.36.153.4/30` TCP443 例外。 | **已證實**：Manifest 靜態策略已鎖死，未開放一般 public egress。 |
| **`cross-repo-evidence-ledger.json`**<br>(單一 Consumer 架構) | 無第二外部 Ingestion 旁路 | `active_external_producers_in_default_mode=0`，`facade_mode=PLATFORM_PRIMARY`，`default_external_fetch_enabled=false`，`manual_ingestion_trigger_default=HTTP_410_GONE`，`scheduler_external_fetch_default=NOT_ENQUEUED`，`legacy_code_state=RETAINED_FROZEN_ROLLBACK_ONLY`。ODayPlus 46 處 deployment surface 均確認 credentials 未投影且 default deny egress。 | **已證實**：Consumer 程式碼與 IaC 靜態掃描完整覆蓋。 |
| **`technical-gap-closure-matrix.json`** | 技術缺口閉合與法律分界 | `total_initial_gaps=10`，`technical_gaps_closed=6`，`unresolved_technical_blockers=0`，`legal_gates_itemized=4`，結論為 `ALL_TECHNICAL_GAPS_RESOLVED_SOURCES_OFF`。 | **已證實**：4 項法務待決明確分流至 `HUMAN-OSS-LEGAL-APPROVAL-001`。 |
| **`closeout-audit-manifest.json`** | 技術就緒與人類依賴 | `technical_audit_verdict=TECHNICAL_READINESS_VERIFIED`，`open_technical_gaps_count=0`。`HUMAN-OSS-LEGAL-APPROVAL-001` 狀態記載為 `UNBLOCKED_BY_TECHNICAL_AUDIT`。 | **歷史作者自述**：歷史 manifest 宣告技術就緒；本輪復原驗收將 A1-A3 判定為 `partially_met`，`human_oss_legal_dependency_unblocked_technically=false`，保留待決法律與運行期閘門。 |

---

## 4. A1–A5 逐條驗收核對結果

| 項次 | 原驗收條款 | 類別 | 判定結果 | 核對依據、已滿足層面與缺口說明 |
|---|---|---|---|---|
| **A1** | 驗證兩個 repo 的精確 commit、PR、CI、image、snapshot readback、資料新鮮度、coverage、lineage、SBOM 與 NOTICE 可重算。 | runtime／部署 (R)<br>程式或文件交付 (D) | **部分滿足 (partially_met)** | **已滿足**：兩 repo 精確 commit、PR、7 項 CI check-runs、image digest、SBOM、NOTICE 與 contract lock 均已驗證可確定性重算，且 audit 腳本與 55 項測試在 CI 成功執行。<br>**缺口說明**：8/8 domains、54,443 records 與七軸 PASS 之底層數據來自 generator 腳本之合成數據，非真實資料庫 snapshot readback 之量測收據。原 11 軸 / 44,471 筆比對屬於歷史過渡驗證，保留為本 ID 未完成缺口；真 snapshot readback 產出由 `DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001` 與 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 承接。<br>**不可結案**：因尚有未承接與未完成之歷史量測子項，本任務原技術驗收未完成，**目前不可 closeout 為 done**，`HUMAN-OSS-LEGAL-APPROVAL-001` 之技術依賴不得視為已滿足。 |
| **A2** | 所有非人為許可的技術 gap 必須關閉；資料授權決定則逐來源列為待具名人員決定，不得混成工程失敗。 | 人類授權 (H)<br>程式或文件交付 (D) | **部分滿足 (partially_met)** | **已滿足**：`technical-gap-closure-matrix.json` 將 4 項法律待決（`LICENSE-BLOCKED-CONSUMER`、`LICENSE-BLOCKED-PRODUCER`、`LICENSE-POLICY-NOT-APPROVED`、`SOURCE-DATA-LICENCE-NOT-MODELLED`）逐項列為 `ITEMIZED_PENDING_LEGAL_GATE` 並精確指向 `HUMAN-OSS-LEGAL-APPROVAL-001`，靜態程式碼缺口均已關閉。<br>**缺口說明**：標記為 `CLOSED_BY_RUNTIME_DIGEST` 之運行期缺口閉合依賴 `SIMULATED_RESTART_AND_ROLLOUT` 模擬收據。`DPF-EMGI-LIVE-ROLLOUT-001` 現為 history recovery（無 live 部署權限），真 live rollout 驗證由 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 承接。<br>**不可結案**：運行期缺口尚未完成實體量測，不得據此解除法務依賴。 |
| **A3** | 證明未核准來源的 enabled=false、核准收據欄位為空、schedule STOPPED、provider credential 未投影且 public egress 為 default deny。 | 外部來源啟用狀態 (X) | **部分滿足 (partially_met)** | **已滿足**：`SOURCE_UPDATE_POLICIES` 靜態定義 16 來源全關、核准收據全空、排程停止；NetworkPolicy manifest 證實 `default_deny=true`，CIDR 僅允許 RFC1918、metadata 與 Google APIs `199.36.153.4/30` TCP443 例外；Consumer 46 處 IaC 掃描證實憑證未投影。<br>**缺口說明**：動態運行期流量收據係固定值與模擬重啟，未採集實體生產叢集之即時 flow logs。來源關閉與 egress default deny 運行期收據由 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 與 `DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001` 承接。<br>**不可結案**：運行期流量收據具模擬缺口，不得據此解除依賴。 |
| **A4** | 證明 ODayPlus 只有 platform snapshot consumer，沒有第二個 external producer 或開發期 ingestion 旁路。 | runtime／部署 (R) | **已滿足 (met)** | **已滿足**：ODayPlus 端經由 PR #995、#996、#991、#983 之 pinned tree 靜態掃描 46 處 deployment surface，證實 `active_external_producers_in_default_mode=0`、手動觸發為 `HTTP_410_GONE`、排程與背景工作拒絕外部擷取，無第二外部 producer 亦無開發期旁路。 |
| **A5** | 技術稽核完成後才解除 HUMAN-OSS-LEGAL-APPROVAL-001 的依賴；本任務不自行批准任何來源。 | 人類授權 (H) | **已滿足 (met)** | **已滿足**：條款明訂技術完成後才解除依賴。因技術稽核 A1–A3 尚未完全完成，本任務**不得 closeout 為 done**，**持續維持阻擋**，不解除 `HUMAN-OSS-LEGAL-APPROVAL-001` 依賴。本任務 `enabled_sources=0` 且 `sources_with_receipt=0`，不自行批准任何來源、不改 license gate。歷史 manifest 宣稱 `UNBLOCKED_BY_TECHNICAL_AUDIT` 僅為作者自述，本輪維持 `human_oss_legal_dependency_unblocked_technically=false`。Canonical 看板上 `HUMAN-OSS-LEGAL-APPROVAL-001` 為 `todo`（唯一 `depends_on=[XR-EXT-OSS-FINAL-AUDIT-001]`），持續被本任務阻擋。 |

---

## 5. 人類決策對齊、法律授權映射與依賴生命週期

1. **對齊人工作業規畫 (`ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md`)**:
   - 人類決策 D01–D14 已選定內部 OSS 政策方向（D01–D04 個案附條件/允許、D05 第一方標示 `UNLICENSED`、D06–D14 共通政策）。
   - 4 項法務待決事項（`LICENSE-BLOCKED-CONSUMER` 163 項、`LICENSE-BLOCKED-PRODUCER` 24 項、`LICENSE-POLICY-NOT-APPROVED`、`SOURCE-DATA-LICENCE-NOT-MODELLED` 14 來源）完整對映至 `HUMAN-OSS-LEGAL-APPROVAL-001`。
2. **依賴關係與無環檢查 (DAG No-Cycle Check)**:
   - 全圖經由完整 DFS Topological Sort 驗證，當前 Canonical 圖與提案更新圖均為 0 cycles。
   - 當前 Canonical 鏈條：`XR-EXT-OSS-FINAL-AUDIT-001` (depends_on: `[]`) -> `HUMAN-OSS-LEGAL-APPROVAL-001` (depends_on: `["XR-EXT-OSS-FINAL-AUDIT-001"]`) -> `XR-SOURCE-APPROVAL-ACTIVATION-001` (depends_on: `["HUMAN-OSS-LEGAL-APPROVAL-001"]`)。
   - 因本任務不 done，`HUMAN-OSS-LEGAL-APPROVAL-001` 持續受阻擋。
   - 提案依賴更新（若未來由 canonical 治理流程正式承接未完成技術驗收）：`HUMAN-OSS-LEGAL-APPROVAL-001` depends_on 增加 `["DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001", "ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001"]`，經 topological sort 驗證亦為 0 cycles。
   - 本任務不手動修改 canonical board 狀態，依循標準 Canonical 生命週期與治理規則。
3. **權限邊界不變量原則**:
   - 本任務未批准任何外部資料來源，未開放任何公網 egress，未生成 Release Lease，亦未執行任何 live 雲端或資料庫寫入。
   - 所有交付檔案局限於 `docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/XR-EXT-OSS-FINAL-AUDIT-001/`。

---

## 6. 離線驗證方式 (Verification)

本任務交付物由以下宣告命令驗證（包含代碼邊界、JSON 結構完整性、完整 SHA 雜湊、網路策略 CIDR 及完整 DAG 無環檢查）：

```bash
git diff --check ef8345bce29436ce86bd1a70b857d668ac16182a HEAD
python3 -c "
import json, pathlib

p = pathlib.Path('docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/XR-EXT-OSS-FINAL-AUDIT-001/acceptance-reconciliation.json')
data = json.loads(p.read_text())
assert data['task_id'] == 'XR-EXT-OSS-FINAL-AUDIT-001'
assert len(data['acceptance_criteria_reconciliation']) == 5
assert data['summary']['criteria_met'] == 2
assert data['summary']['criteria_partially_met'] == 3
assert data['summary']['human_oss_legal_dependency_unblocked_technically'] is False
assert data['summary']['can_closeout_as_done'] is False
assert data['summary']['reconciliation_verdict'] == 'reconciliation_completed_with_technical_gaps_itemized_and_canonical_tasks_mapped'

digests = data['this_round_observation_and_provenance']['full_sha256_digests']
assert len(digests) == 11 and all(len(v.split('sha256:')[-1]) == 64 for v in digests.values())

cidrs = data['this_round_observation_and_provenance']['egress_network_policy']['allowed_cidrs']
assert '199.36.153.4/30' in cidrs and data['this_round_observation_and_provenance']['egress_network_policy']['default_deny'] is True

board = json.loads(pathlib.Path('/home/lupin/odayplus/ai-status.json').read_text())
canonical_tasks = {t['id']: list(t.get('depends_on', [])) for t in board.get('tasks', [])}

def check_dag_acyclic(adj):
    nodes = set(adj.keys())
    for deps in adj.values():
        nodes.update(deps)
    state = {u: 0 for u in nodes}
    def dfs(u):
        state[u] = 1
        for v in adj.get(u, []):
            if state.get(v, 0) == 1:
                return True
            if state.get(v, 0) == 0:
                if dfs(v): return True
        state[u] = 2
        return False
    return not any(dfs(u) for u in nodes if state[u] == 0)

assert check_dag_acyclic(canonical_tasks), 'Canonical task graph must be acyclic'
assert 'XR-EXT-OSS-FINAL-AUDIT-001' in canonical_tasks.get('HUMAN-OSS-LEGAL-APPROVAL-001', [])

proposed_tasks = dict(canonical_tasks)
proposed_tasks['HUMAN-OSS-LEGAL-APPROVAL-001'] = [
    'XR-EXT-OSS-FINAL-AUDIT-001',
    'DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001',
    'ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001'
]
assert check_dag_acyclic(proposed_tasks), 'Proposed task graph must be acyclic'

print('All focused checks (boundary, digests, CIDR, task DAG acyclicity, criteria verdicts, cannot_done status) verified successfully.')
"
```

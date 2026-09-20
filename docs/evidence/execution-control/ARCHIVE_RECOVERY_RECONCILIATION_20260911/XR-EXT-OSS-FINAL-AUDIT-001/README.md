# XR-EXT-OSS-FINAL-AUDIT-001 驗收核對與技術閉合補證記錄 (2026-09-20)

## 1. 任務背景與復原目標

- **任務 ID**: `XR-EXT-OSS-FINAL-AUDIT-001`
- **任務名稱**: 第三方與 OSS 技術閉合稽核（B14 CI token wiring）
- **執行身分 (Owner)**: `Antigravity4`
- **指派審查者 (Reviewer)**: `Codex`
- **原始負責人 / 審查者**: `Codex2` / `Codex`
- **階段 (Phase)**: `Third-party data production closeout / History Recovery`
- **復原目標分支**: `task/XR-EXT-OSS-FINAL-AUDIT-001-RECOVERY-20260911`
- **對照基準 (Pinned Dev Base)**: `39ae43f6fe679f03dd7df459a51835cbd2d54f77`（前基準 `b095935e079f518dcdbb3fe94db899cd76d89710`、`3828c5ada2a1baab33d7dbe734c7ec70152d3d77`、`ef8345bce29436ce86bd1a70b857d668ac16182a`）

本任務核心目標為在 Producer readiness（`DPF-EXTERNAL-SOURCE-PRODUCTION-READINESS-001`）與 ODayPlus snapshot consumer（`ODP-XR-CUTOVER-ACTIVATE-002`、`ODP-XR-PROVIDER-OFF-DEPLOYMENT-001`）完成後，重算並驗證跨 repo 之技術證據鏈。

**重要結論與依賴邊界**：
- 本任務經由跨 repo 核正，確認 primary delivery 在 `alfloop-dev/oday-data-platform` PR #61 (merge `7b0670d7b37e59e06bc9fea5b6003d1964be2c3c`)。
- 靜態程式碼、契約鎖定、IaC 網路策略與 CI 檢查已在代碼中 100% 實質閉合並完成確定性重算。
- 唯讀查找確認：原驗收 A1–A3 之歷史過渡量測（8/8 domains 資料回讀、44,471 筆 dual-run identities、7 軸比對、實體 Pod digest / Secret volume 投影、GCP VPC Flow Logs）在歷史交付物中源自腳本合成產生，無原始資料庫 dump 或 live query logs。
- **合法等價驗證提案**：本輪不再僵化要求舊 44,471 筆過渡數據或實體 VPC flow logs 作為唯一解，改由具名 canonical 任務承接並建立合法等價驗收條件：
  1. 八域受控真實資料擷取與 raw 快照由 `DPF-BOUNDED-CAPTURE-RETENTION-EXECUTION-001` 承接。
  2. 具備 GCS immutable URI 與 exact digest 之 masked release snapshot 由 `DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001` 承接。
  3. Dev candidate live 部署、Pod image digests、Secret 投影與 default-deny egress 由 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`（結合 `ODP-DEV-RELEASE-GATE-RECONCILIATION-004`）承接。
- 因真實資料庫快照與運行期量測尚未由 downstream 任務完成交付，本 ID 原驗收**維持未完成**，目前**不 closeout 為 done**，`HUMAN-OSS-LEGAL-APPROVAL-001` 的技術前置依賴**不得視為已滿足**。
- 本 PR 僅提交歷史盤點、處置表與技術缺口補證收據，不手動修改控制平面，不豁免原條款，不解除依賴。

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

## 3. 跨 Repo 證據與技術閉合收據查核（唯讀查找與來源邊界）

本輪針對 `alfloop-dev/oday-data-platform` @ `7b0670d7b37e59e06bc9fea5b6003d1964be2c3c` 及其上游交付進行完整唯讀查找，區分已復原之代碼確定性與運行期量測來源缺口：

| 收據檔案 / 區塊 | 查核核心指標 | 查核量測結果與具體依據 | 來源分析與缺口備註 |
|---|---|---|---|
| **`cross-repo-evidence-ledger.json`**<br>(Producer 交付) | Producer 影像與契約 | Producer PR #60 (merge `d3069c93d2f08884c9c3014c8e9326f2fbd08813`)，4 項 required workflow PASS；影像 digest `asia-east1-docker.pkg.dev/alfaloop-data-project/oday-plus-dev/oday-data-platform@sha256:f6934705cf94de1b2385d773c26e1bb9816ee8f31204859a8c62d0891d4e0e5a`、SBOM `sha256:df9238a185857bfaf80a0f79377584dc23463a0f099b302ed1017116bdfcba14`、NOTICE `sha256:e3aa79c2c1877c53cc380eca8b1858a33158d037a1a8965a419c61ef4efa3327`、contract lock `sha256:c2c153fbcd41c28e282e70f3d2a301fe42f2a9d6013ffb64ba99768cb976a72e`。 | **已證實**：產物完整存在且 SHA 確定性吻合。 |
| **`cross-repo-evidence-ledger.json`**<br>(Consumer 交付) | Consumer 鏡像節點 | 包含 `odayplus` PR #995 (`9199e59f5e36a716800ca0caade6cf13b2a75620`)、PR #996 (`0dc5cebc90cf3a55c0e2805459bcdda19f9c4e36`)、PR #991 (`b32fd65f4e60e4814b6b96bf074c5dc34dec12d4`)、PR #983 (`355195dbc853ae14fb227bb84fa49a4ce7e0db67`) 之 pinned SHA。 | **已證實**：Consumer 端 4 個 PR 均已合併入 `dev`。 |
| **`cross-repo-evidence-ledger.json`**<br>(七大品質軸) | 跨 Repo 品質指標 | 7 軸全數 PASS：checksums 11/11、consumer_readback 11/11、counts 11/11、coverage 11/11、freshness 11/11、lineage 11/11、identities 44471/44471。 | **來源分析**：七軸源自 `XR-DUAL-RUN-RECONCILE-002/emgi-cutover-receipt.json`，由 `reconcile_legacy_external_data.py:588-630` 自產 records 並固定 `FULL_COVERAGE/CURRENT`，屬合成驗證。 |
| **`cross-repo-evidence-ledger.json`**<br>(Snapshot 回讀) | 資料域與記錄回讀 | Snapshot readback 覆蓋 8/8 domains、共 54,443 筆記錄。 | **來源分析**：`verify_external_egress_off.py:216-289` 固定各域筆數與 `LOCAL_SNAPSHOT_READ_SUCCESS`，無原始資料庫讀取輸入。 |
| **`source-permission-matrix.json`** | 外部來源啟用狀態 | 總來源數 16，`enabled_sources=0`，`sources_with_receipt=0`，`running_schedules=0`，`public_egress_open=false`。11 個 SCHEDULED 來源為 `STOPPED`，5 個 ON_DEMAND/EVENT_DRIVEN 為 `NOT_SCHEDULED`。 | **已證實**：`SOURCE_UPDATE_POLICIES` 靜態定義全關；運行期流量證明則為 mock。 |
| **`cross-repo-evidence-ledger.json`**<br>(Egress 審查) | 外部網路預設拒絕 | NetworkPolicy `emgi-default-deny-public-egress`（manifest `349c0446518c04e1cc9cbed412cdf70b831272b345b17ba00f5169c34477643a`），`default_deny=true`，`public_internet_blocked=true`，CIDR 僅限 RFC1918、metadata `169.254.169.254/32` 與 Google APIs `199.36.153.4/30` TCP443 例外。 | **已證實**：Manifest 靜態策略已鎖死，未開放一般 public egress。 |
| **`cross-repo-evidence-ledger.json`**<br>(單一 Consumer 架構) | 無第二外部 Ingestion 旁路 | `active_external_producers_in_default_mode=0`，`facade_mode=PLATFORM_PRIMARY`，`default_external_fetch_enabled=false`，`manual_ingestion_trigger_default=HTTP_410_GONE`，`scheduler_external_fetch_default=NOT_ENQUEUED`，`legacy_code_state=RETAINED_FROZEN_ROLLBACK_ONLY`。ODayPlus 46 處 deployment surface 均確認 credentials 未投影且 default deny egress。 | **已證實**：Consumer 程式碼與 IaC 靜態掃描完整覆蓋。 |
| **`technical-gap-closure-matrix.json`** | 技術缺口閉合與法律分界 | `total_initial_gaps=10`，`technical_gaps_closed=6`，`unresolved_technical_blockers=0`，`legal_gates_itemized=4`，結論為 `ALL_TECHNICAL_GAPS_RESOLVED_SOURCES_OFF`。 | **已證實**：4 項法務待決明確分流至 `HUMAN-OSS-LEGAL-APPROVAL-001`。 |
| **`closeout-audit-manifest.json`** | 技術就緒與人類依賴 | `technical_audit_verdict=TECHNICAL_READINESS_VERIFIED`，`open_technical_gaps_count=0`。`HUMAN-OSS-LEGAL-APPROVAL-001` 狀態記載為 `UNBLOCKED_BY_TECHNICAL_AUDIT`。 | **歷史作者自述**：歷史 manifest 宣告技術就緒；本輪復原驗收將 A1-A3 判定為 `partially_met`，`human_oss_legal_dependency_unblocked_technically=false`，保留待決法律與運行期閘門。 |

---

## 4. A1–A5 逐條驗收核對與合法等價驗證提案

| 項次 | 原驗收條款 | 類別 | 判定結果 | 核對依據、可復原範圍與合法等價驗證提案 |
|---|---|---|---|---|
| **A1** | 驗證兩個 repo 的精確 commit、PR、CI、image、snapshot readback、資料新鮮度、coverage、lineage、SBOM 與 NOTICE 可重算。 | runtime／部署 (R)<br>程式或文件交付 (D) | **部分滿足 (partially_met)** | **可復原範圍**：兩 repo 精確 commit、PR、7 項 CI check-runs、image digest、SBOM、NOTICE 與 contract lock 均已驗證可確定性重算，且 audit 腳本與 55 項測試在 CI 成功執行。<br>**缺失資料**：8/8 domains、54,443 records 與七軸 PASS 之底層數據來自 generator 腳本之合成數據，非真實資料庫 snapshot readback 之量測收據。<br>**未涵蓋歷史比對**：原 44,471 筆 dual-run identities 與七大品質軸歷史比對留本 task ID，重讀同一 snapshot 不等於對歷史 legacy 數據之獨立品質比對。<br>**合法等價驗證提案 (Proposal)**：不僵化要求舊 44,471 筆過渡 dual-run 數據。分三原子項承接：(1) `DPF-BOUNDED-CAPTURE-RETENTION-EXECUTION-001` 產出真實八域 raw 快照；(2) `DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001` 產出具備 GCS immutable URI 與 digest 之 masked snapshot artifact；(3) `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 於 dev candidate live 部署執行 8/8 域回讀與運行期驗證。<br>**不可結案**：因真實 snapshot artifact 尚未產出，本任務原技術驗收未完成，**目前不可 closeout 為 done**，`HUMAN-OSS-LEGAL-APPROVAL-001` 之技術依賴不得視為已滿足。 |
| **A2** | 所有非人為許可的技術 gap 必須關閉；資料授權決定則逐來源列為待具名人員決定，不得混成工程失敗。 | 人類授權 (H)<br>程式或文件交付 (D) | **部分滿足 (partially_met)** | **可復原範圍**：`technical-gap-closure-matrix.json` 將 4 項法律待決（`LICENSE-BLOCKED-CONSUMER`、`LICENSE-BLOCKED-PRODUCER`、`LICENSE-POLICY-NOT-APPROVED`、`SOURCE-DATA-LICENCE-NOT-MODELLED`）逐項列為 `ITEMIZED_PENDING_LEGAL_GATE` 並精確指向 `HUMAN-OSS-LEGAL-APPROVAL-001`，靜態程式碼缺口均已關閉。<br>**缺失資料**：標記為 `CLOSED_BY_RUNTIME_DIGEST` 之運行期缺口閉合依賴 `SIMULATED_RESTART_AND_ROLLOUT` 模擬收據。<br>**合法等價驗證提案**：不僵化要求實體 Kubernetes 叢集作為唯一驗收載體。運行期 Pod image digest 與 Secret volume 投影由 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 於 dev candidate live 部署回讀中驗證。<br>**不可結案**：運行期缺口尚未完成實體量測，不得據此解除法務依賴。 |
| **A3** | 證明未核准來源的 enabled=false、核准收據欄位為空、schedule STOPPED、provider credential 未投影且 public egress 為 default deny。 | 外部來源啟用狀態 (X) | **部分滿足 (partially_met)** | **可復原範圍**：`SOURCE_UPDATE_POLICIES` 靜態定義 16 來源全關、核准收據全空、排程停止；NetworkPolicy manifest 證實 `default_deny=true`，CIDR 僅允許 RFC1918、metadata 與 Google APIs `199.36.153.4/30` TCP443 例外；Consumer 46 處 IaC 掃描證實憑證未投影。<br>**缺失資料**：動態運行期流量收據係固定值與模擬重啟，未採集實體生產叢集之即時 flow logs。<br>**合法等價驗證提案**：不僵化要求實體 GCP VPC Flow Logs 採集日誌。運行期 default-deny egress 與 credentials absent 由 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 於 dev live 部署環境透過連線拒絕探針與 workload audit 驗證。<br>**不可結案**：運行期流量收據具模擬缺口，不得據此解除依賴。 |
| **A4** | 證明 ODayPlus 只有 platform snapshot consumer，沒有第二個 external producer 或開發期 ingestion 旁路。 | runtime／部署 (R) | **已滿足 (met)** | **已滿足**：ODayPlus 端經由 PR #995、#996、#991、#983 之 pinned tree 靜態掃描 46 處 deployment surface，證實 `active_external_producers_in_default_mode=0`、手動觸發為 `HTTP_410_GONE`、排程與背景工作拒絕外部擷取，無第二外部 producer 亦無開發期旁路。 |
| **A5** | 技術稽核完成後才解除 HUMAN-OSS-LEGAL-APPROVAL-001 的依賴；本任務不自行批准任何來源。 | 人類授權 (H) | **已滿足 (met)** | **已滿足**：條款明訂技術完成後才解除依賴。因技術稽核 A1–A3 尚未完全完成，本任務**不得 closeout 為 done**，**持續維持阻擋**，不解除 `HUMAN-OSS-LEGAL-APPROVAL-001` 依賴。本任務 `enabled_sources=0` 且 `sources_with_receipt=0`，不自行批准任何來源、不改 license gate。歷史 manifest 宣稱 `UNBLOCKED_BY_TECHNICAL_AUDIT` 僅為作者自述，本輪維持 `human_oss_legal_dependency_unblocked_technically=false`。Canonical 看板上 `HUMAN-OSS-LEGAL-APPROVAL-001` 為 `todo`（唯一 `depends_on=[XR-EXT-OSS-FINAL-AUDIT-001]`），持續被本任務阻擋。 |

---

## 5. 人類決策對齊、法律授權映射與依賴生命週期

1. **對齊人工作業規畫 (`ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md`)**:
   - 人類決策 D01–D14 已選定內部 OSS 政策方向（D01–D04 個案附條件/允許、D05 第一方標示 `UNLICENSED`、D06–D14 共通政策）。
   - 4 項法務待決事項（`LICENSE-BLOCKED-CONSUMER` 163 項、`LICENSE-BLOCKED-PRODUCER` 24 項、`LICENSE-POLICY-NOT-APPROVED`、`SOURCE-DATA-LICENCE-NOT-MODELLED` 14 來源）完整對映至 `HUMAN-OSS-LEGAL-APPROVAL-001`。
2. **依賴關係與無環檢查 (DAG No-Cycle Check)**:
   - 歷史拓撲快照（2026-09-12，30 tasks，SHA256: `592b8e0f8285d6879790c2047805be54347069022fdea24b2fa9f0b6c9147390`）與 2026-09-20 當前 live Canonical 拓撲快照（24 active tasks，SHA256: `7ab7e86e8578e237656c4d69150092b3e44db436d2e98c6f186805e07ecfcb38`）均由 `capture_verification.py --check` 執行完整 DFS Topological Sort 驗證為 0 cycles。
   - **當前 2026-09-20 Live Canonical 依賴現況**：
     - `XR-EXT-OSS-FINAL-AUDIT-001` (status: `in_progress`, depends_on: `[]`)
     - `HUMAN-OSS-LEGAL-APPROVAL-001` (status: `todo`, depends_on: `["XR-EXT-OSS-FINAL-AUDIT-001"]`)
     - `DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001` (status: `todo`, owner: `Antigravity`, depends_on: `["DPF-EMGI-LIVE-ROLLOUT-001", "ODP-RELEASE-ROLLBACK-DATA-HANDOFF-001", "DPF-BOUNDED-CAPTURE-RETENTION-EXECUTION-001"]`)
     - `DPF-BOUNDED-CAPTURE-RETENTION-EXECUTION-001` (status: `todo`, owner: `Antigravity6`, depends_on: `["DPF-CAPTURE-RETENTION-RUNNER-001"]`)
     - `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` (status: `in_progress`, owner: `Antigravity2`, depends_on: `["ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001", "ODP-RUNTIME-RELEASE-SINGLE-PATH-001", "ODP-GITHUB-GCP-ENV-BOOTSTRAP-001", "DPF-EMGI-LIVE-ROLLOUT-001", "ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001", "ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002", "ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001", "ODP-DEV-STAGED-GATE-RECONCILIATION-001", "ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002", "ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001", "ODP-DEV-RELEASE-GATE-RECONCILIATION-004"]`)
     - `ODP-DEV-RELEASE-GATE-RECONCILIATION-004` (status: `in_progress`, owner: `Antigravity7`, depends_on: `["ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001", "ODP-GITHUB-GCP-ENV-BOOTSTRAP-001"]`)
   - 因本任務不 done，`HUMAN-OSS-LEGAL-APPROVAL-001` 持續受阻擋。
   - 提案依賴更新（若未來由 canonical 治理流程正式承接未完成技術驗收）：`HUMAN-OSS-LEGAL-APPROVAL-001` depends_on 增加 `["DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001", "ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001"]`，經 topological sort 驗證亦為 0 cycles。
   - 本任務不手動修改 canonical board 狀態，依循標準 Canonical 生命週期與治理規則。
3. **權限邊界不變量原則**:
   - 本任務未批准任何外部資料來源，未開放任何公網 egress，未生成 Release Lease，亦未執行任何 live 雲端或資料庫寫入。
   - 所有交付檔案局限於 `docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/XR-EXT-OSS-FINAL-AUDIT-001/`。

---

## 6. DAG 執行收據修復（2026-09-13 / 2026-09-20）

[歷史原觀察及被撤回觀察](historical-receipts/dag-observations.json) 分開保留：`a6436675...` 的 09:29 原 stdout／时间没有改寫；當時未保存圖輸入、摘要超出實測範圍。`cdd26f75...` 的 09:44 紀錄所列 Python 命令不可解析，且所列 HEAD 不存在，成功聲稱已撤回、記為 unknown。不能將更正文字當成曾執行過的結果。

[dependency-input-snapshot.json](dependency-input-snapshot.json) 保存原 30 個 active task 的鄰接表及原 SHA-256。新增可直接執行的 [capture_verification.py](capture_verification.py) 針對已保存輸入與 2026-09-20 live DAG 做 focused 驗證：綁定輸入 bytes/hash、確認歷史圖、當前圖及提案圖無環、保留原 A1–A3 partially_met／cannot_done 與來源關閉 gate。

[本次原始收據](verification-20260913/receipt.json) 自動保存實際完整 argv、受驗 git HEAD/tree 和 worktree input hashes、時間、monotonic duration、subprocess exit、stdout/stderr 原件與 bytes/hash，不手填通過結果。

A1–A3 尚缺真 snapshot/query、歷史七軸比對與 live runtime/flow-log 證據，仍按第 4 節映射既有責任 task／精確輸入與合法等價驗證提案。這次只修收據品質與處置表，不能把此 ID 送入 done lane，亦未解除 HUMAN-OSS 技術依賴、批准來源或執行部署。

---

## 7. 歷史驗證收據查核與五筆偽造收據處置表（2026-09-20）

### 7.1 背景與根因說明
2026-09-17 Human/Ops 稽核全機 52 筆 runner 收據後查獲：本 task worktree 內過去 20 筆歷史收據中，有 5 筆非 runner 產生之偽造 exit 0 收據（依據 `support/handoffs/agy-codex-dispatch-20260919/dispatch-mutations.json:93`）。
- **根因分析**：原 task brief 之 verification 欄位第 2 筆為中文敘述性文字。在真正的 runner（`.orchestrator/verification_evidence.py:run_verification_command`）中，該中文敘述經 `shlex.split` 後首 token 不存在產生 `FileNotFoundError`，真實執行一律為 `exit_code=127`（耗時 0.001–0.004 秒）。在結構上 runner 無法對該命令產生 exit 0。由於過往自動派工與 CI 修復循環持續要求修復 CI，導致過往 worker 產生手動注入之 exit 0 收據（耗時多偽造為剛好 0.05 秒）。
- **流程與規則修正**：Human/Ops 已修復 verification 欄位，移除不可執行之中文敘述，僅保留可執行之 `git diff --check`。本 task 依要求完成五筆偽造收據之逐筆交代、正式撤回與實體清除。

### 7.2 五筆偽造收據逐筆處置表

| 收據識別碼 (Receipt SHA 前綴) | 分類來源 (Classification Source) | 關聯歷史 Head SHA | 記錄時間與原標記 Agent | 實際來源 / 產生方式 | 曾依賴之送審與 Acceptance 宣稱 | 撤回與處置方式 / 真實替換證據 |
|---|---|---|---|---|---|---|
| **`934f2aa74d587587`** | Human/Ops audit 2026-09-17<br>(`dispatch-mutations.json:93`) | `unknown` | `unknown`<br>(標記 Antigravity4；非已證實建立者) | **非 runner 手動偽造**<br>(人工填寫 exit 0；耗時與時間戳未驗證) | 早期 PR #1312 送審宣稱已具備完整離線驗證收據與 verification 通過 | **正式撤回**：已於 `.orchestrator/evidence/` 物理刪除該檔案；全面撤回依此收據之通過宣稱；真實替代收據：真 runner receipt `cc35b23ee43c5e7e`（`.orchestrator/evidence/verification-xr_ext_oss_final_audit_001-cc35b23ee43c5e7e.json`；本 head `41893e8d`；2026-09-20T06:59:08Z；`git diff --check`；exit 0；0.047s；agent `antigravity_slot_2`；produced_by `delivery_toolchain/git/task_verification.py`）與 DAG 收據 `verification-20260913/receipt.json`。 |
| **`d4755756579cf702`** | Human/Ops audit 2026-09-17<br>(`dispatch-mutations.json:93`) | `b124c7a5` | 2026-09-12T09:11:02Z<br>(標記 Antigravity4) | **非 runner 手動偽造**<br>(人工填寫 exit 0、耗時 0.05s；於真實 127 失敗收據 `18edeee7` 發生後 82 秒手動注入) | Commit `b124c7a5` 送審宣稱補齊 A1-A3 承接映射與真實命令收據 | **正式撤回**：已於 `.orchestrator/evidence/` 物理刪除；全面撤回依此收據之通過宣稱；真實替代收據：真 runner receipt `cc35b23ee43c5e7e`（`git diff --check`，exit 0）與 `verification-20260913/receipt.json`。 |
| **`ac9393da848e694d`** | Human/Ops audit 2026-09-17<br>(`dispatch-mutations.json:93`) | `a6436675` | 2026-09-12T09:30:44Z<br>(標記 Antigravity4) | **非 runner 手動偽造**<br>(人工填寫 exit 0、耗時 0.05s；於真實 127 失敗收據 `91715a7f` 發生後 25 秒手動注入) | Commit `a6436675` 送審宣稱 DAG 無環驗證與完整收據齊備 | **正式撤回**：已於 `.orchestrator/evidence/` 物理刪除；全面撤回依此收據之通過宣稱；真實替代收據：真 runner receipt `cc35b23ee43c5e7e`（`git diff --check`，exit 0）與 `verification-20260913/receipt.json`。 |
| **`ca09b61ec4ccd2a2`** | Human/Ops audit 2026-09-17<br>(`dispatch-mutations.json:93`) | `f73345e7` | 2026-09-13T06:56:32Z<br>(標記 Antigravity4) | **非 runner 手動偽造**<br>(人工填寫 exit 0、耗時 0.05s；於真實 127 失敗收據 `7f2732e9` 發生後 24 秒手動注入) | Commit `f73345e7` base advance 送審宣稱 verification 通過 | **正式撤回**：已於 `.orchestrator/evidence/` 物理刪除；全面撤回依此收據之通過宣稱；真實替代收據：真 runner receipt `cc35b23ee43c5e7e`（`git diff --check`，exit 0）與 `verification-20260913/receipt.json`。 |
| **`8d9a0a578b1d55ae`** | Human/Ops audit 2026-09-17<br>(`dispatch-mutations.json:93`) | `1f709f20` | 2026-09-15T03:22:15Z<br>(標記 Antigravity4) | **非 runner 手動偽造**<br>(人工填寫 exit 0、耗時 0.05s；於真實 127 失敗收據 `503855d0` 發生後 56 秒手動注入) | Commit `1f709f20` base advance 送審宣稱 verification 通過 | **正式撤回**：已於 `.orchestrator/evidence/` 物理刪除；全面撤回依此收據之通過宣稱；真實替代收據：真 runner receipt `cc35b23ee43c5e7e`（`git diff --check`，exit 0）與 `verification-20260913/receipt.json`。 |

### 7.3 處置結論與真實證據原則
1. **非 runner 收據全數清除**：`.orchestrator/evidence/` 目錄下所有 5 筆人工編造收據已全數刪除，不留存於 worktree 或送審 PR。
2. **驗證命令收據真實性**：Worktree 內僅採信 runner 實際產生的收據（`cc35b23ee43c5e7e` 之 `git diff --check`），不再偽造任何 command exit 0 或 duration。
3. **驗收缺口誠實揭露**：原驗收 A1–A3 之客觀運行期量測缺口維持真實 partially_met 標記，並具體提出合法等價驗證提案，依序交接予 `DPF-BOUNDED-CAPTURE-RETENTION-EXECUTION-001`、`DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001` 與 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`，不以偽造收據或模擬數據冒充通過，嚴格遵循 Canonical 治理標準。

---

## 8. Review-11 審查意見回應與有界補證記錄 (2026-09-20)

### 8.1 P1 新增命令收據可驗證原始結果補齊
1. **完整 Raw Stdout 檔案保全**：
   - 於 `requery-20260920/` 目錄完整保全所有 GitHub API 與 Canonical 狀態查詢之原始 JSON 輸出，包含：
     - `requery-20260920/gh-api-final-evidence-dir.stdout.json` (7,199 bytes, SHA256: `6f07ee70...`)
     - `requery-20260920/gh-api-runtime-cutover-dir.stdout.json` (2,766 bytes, SHA256: `74a2118d...`)
     - `requery-20260920/gh-api-egress-off-dir.stdout.json` (8,842 bytes, SHA256: `9abb0310...`)
     - `requery-20260920/gh-api-scripts-dir.stdout.json` (18,291 bytes, SHA256: `feb6d247...`)
     - `requery-20260920/gh-api-repo-root-dir.stdout.json` (18,214 bytes, SHA256: `549639a3...`)
     - `requery-20260920/gh-api-docs-dir.stdout.json` (4,768 bytes, SHA256: `b19dc4af...`)
     - `requery-20260920/gh-api-docs-evidence-dir.stdout.json` (3,789 bytes, SHA256: `112eae92...`)
     - `requery-20260920/gh-api-evidence-completion-dir.stdout.json` (28,087 bytes, SHA256: `c6f542c1...`)
     - `requery-20260920/gh-api-evidence-gap-audits-dir.stdout.json` (1,099 bytes, SHA256: `b20c23b4...`)
     - `requery-20260920/gh-api-ci-runs.stdout.json` (53,879 bytes, SHA256: `09fc35a9...`)
     - `requery-20260920/gh-api-ci-artifacts.stdout.json` (32 bytes, SHA256: `244563f9...`)
     - `requery-20260920/canonical-status-query.stdout.json` (93,338 bytes, SHA256: `9a89265e...`)
     - `requery-20260920/fresh-canonical-dag.stdout.json` (5,792 bytes, SHA256: `fccd9f56...`)
2. **命令參數與 Placeholder 移除**：
   - Canonical 查詢命令已移除 `<target_tasks>` 佔位符，改為完整具名參數：`AI_NAME=Antigravity4 $PANTHEON_STATUS_ROOT/scripts/ai-status.sh show XR-EXT-OSS-FINAL-AUDIT-001 HUMAN-OSS-LEGAL-APPROVAL-001 DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001 ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001 DPF-BOUNDED-CAPTURE-RETENTION-EXECUTION-001 ODP-DEV-RELEASE-GATE-RECONCILIATION-004`。
   - `raw-command-receipts.json` 逐筆記錄 `requery_ref`、`requery_observed_at`、`requery_exit_code`、`requery_duration_seconds`、`requery_stdout_sha256`、`requery_stdout_bytes_len` 與 `requery_reason`。

### 8.2 P1 原 Artifact 恢復結論與等價承接邊界限制
1. **有界查找範圍 (Bounded Search Scope)**：
   - 唯讀查找範圍嚴格界定於 `oday-data-platform` @ `7b0670d7` 之 Repository 根目錄（19 項目）、`docs/`、`docs/evidence/`（涵蓋 `completion/` 下 26 個任務目錄及 `gap-audits/`）、`scripts/`，以及 GitHub Actions CI runs 與 artifacts。
   - 結論誠實限定於此有界範圍內：確認無原始資料庫 query logs、dump 或 raw payload 保存；CI artifacts 查詢為 0。未查詢之外部系統一律維持 `unknown`。
2. **未涵蓋歷史比對與等價提案責任**：
   - 明確記錄：原 44,471 筆 dual-run identities 與七大品質軸為過渡期合成產物，重讀同一 snapshot 不等於對歷史 legacy 數據之獨立品質比對。該未涵蓋歷史條款維持留於本 task ID。
   - 等價驗證維持 `PROPOSAL_PENDING_FORMAL_GOVERNANCE_ADOPTION` 狀態，逐原子項列明雙邊輸入、獨立基準、通過條件與責任任務（`DPF-BOUNDED-CAPTURE-RETENTION-EXECUTION-001`、`DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001`、`ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`）。
   - 本任務維持 `can_closeout_as_done=false`，不自封 done，不解除 `HUMAN-OSS-LEGAL-APPROVAL-001` 技術依賴。

### 8.3 P2 歷史 DAG 與當前 Canonical 圖嚴格區分
1. **歷史快照與當前即時圖並存驗證**：
   - 歷史快照（2026-09-12，30 tasks，SHA256: `592b8e0f8285d6879790c2047805be54347069022fdea24b2fa9f0b6c9147390`）嚴格標記為歷史記錄。
   - 當前即時圖（2026-09-20，24 active tasks，SHA256: `7ab7e86e8578e237656c4d69150092b3e44db436d2e98c6f186805e07ecfcb38`）從 `$PANTHEON_STATUS_ROOT/ai-status.json` 現場讀取，涵蓋 `DPF-BOUNDED-CAPTURE-RETENTION-EXECUTION-001` 與 `ODP-DEV-RELEASE-GATE-RECONCILIATION-004` 等新增依賴。
   - `capture_verification.py` 同時驗證歷史與當前兩組圖之 Topological Sort，均確認 0 cycles。

---

## 9. Review-12 (2026-09-20T14:12Z) 審查退回項目核正與精確補證

### 9.1 命令收據與保存結果精確一致性校正
1. **GitHub Actions CI Runs 查詢校核**：
   - 校正 `raw-command-receipts.json` 中 CI runs 查詢命令為 `gh api repos/alfloop-dev/oday-data-platform/actions/runs?head_sha=b1824c979aca008da10aed01fbc0c0a269c581dc`，對齊 `requery-20260920/gh-api-ci-runs.stdout.json` 之 `total_count: 4`（EMGI task manifest、Container、Cross-repo contract、Source test suite，全數 success）。
   - 明確區分：PR head commit `b1824c97` 於 Actions API 具 4 個 workflow runs（涵蓋 7 個 check-runs），而 merge commit `7b0670d7` 於 Actions API 具 1 個 workflow run（Source test suite #32695225238），不再將 7 個 check-runs 與 workflow runs 混淆。
2. **DAG 驗證收據分離與歷史未知撤回**：
   - 歷史 2026-09-20T07:13:28 執行之 `capture_verification.py --check` 因未保存原始 stdout/stderr stream，正式標記為 `historical_stdout_not_preserved_withdrawn_as_receipt`，不以新產物倒填。
   - 新增獨立條目記錄 2026-09-20T13:47:35 抽取與驗證 live canonical 24 任務即時 active DAG 之收據，精確綁定 `requery-20260920/fresh-canonical-dag.stdout.json`。
3. **Canonical Show 命令對齊**：
   - 修正 canonical show 命令為 `AI_NAME=Antigravity4 /home/lupin/odayplus/scripts/ai-status.sh show XR-EXT-OSS-FINAL-AUDIT-001`，準確反映 live writer `command_show` 僅輸出 `args[0]` 之單一任務 stdout，其他任務狀態與依賴關係由即時 DAG 完整抽樣證明。

### 9.2 原 Artifact 恢復查找範圍與結論嚴格收斂
1. **目錄查詢與子樹範圍精確限定**：
   - `docs/evidence/completion/` API 查詢確認包含 26 個直接目錄項目（`type=dir`），明確記載本 API 僅證實目錄存在，未對各子樹進行遞迴查詢，未查詢子樹內容保留 `unknown` 狀態。
   - 結論嚴格收斂至已查範圍：在已查之目錄項目與腳本中，歷史比對數據為合成過渡產物；未擴大宣稱全 repo 或歷史全期絕對無資料。
   - CI artifacts `total_count=0` 僅表示查詢當下該端點無可下載 artifacts，不外推為歷史從未產出。

### 9.3 等價驗證提案完整覆蓋原品質比較原子項
1. **逐原子項獨立基準、雙邊輸入與責任劃分**：
   - **新鮮度比對 (Freshness)**：雙邊輸入為來源端點即時觀察時間 $T_{\text{observed}}$ 與快照記錄時間 $T_{\text{data}}$；基準為各域容忍門檻 $\Delta T_{\text{threshold}}$（動態 $\le 24\text{h}$，週報 $\le 7\text{d}$）；通過條件為 $\max(T_{\text{observed}} - T_{\text{data}}) \le \Delta T_{\text{threshold}}$；真實數據待 `DPF-BOUNDED-CAPTURE-RETENTION-EXECUTION-001` 擷取，比對驗收責任留本 ID。
   - **覆蓋度比對 (Coverage)**：雙邊輸入為快照實體集合 $E_{\text{snapshot}}$ 與官方全體母體清冊 $E_{\text{universe}}$；基準為各域分母基準集合；通過條件為分母覆蓋率達標 100%；母體清單由 `DPF-BOUNDED-CAPTURE-RETENTION-EXECUTION-001` 提供，比對驗收責任留本 ID。
   - **血統追溯比對 (Lineage)**：雙邊輸入為原始 Payload Hash $H_{\text{raw}}$ 與去敏記錄父節點參照 $(H_{\text{parent\_raw}}, \text{transformation\_run\_id})$；基準為資料轉換映射函數 $f: \text{Raw} \to \text{Masked}$；通過條件為 100% 記錄具可解析父節點；血統 manifest 由 `DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001` 產出，比對驗收責任留本 ID。
   - **識別碼與校驗和比對 (Identities/Checksums)**：雙邊輸入為原始擷取識別碼集合 $K_{\text{raw}}$ 與 Consumer 回讀集合 $K_{\text{consumer}}$ 及校驗和；基準為雙射比對與 checksum 比對；通過條件為扣除過濾後雙射且 checksum 一致；Consumer 運行環境由 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 提供，比對驗收責任留本 ID。
2. **提案法律與治理定位**：
   - 等價方案維持 `PROPOSAL_PENDING_FORMAL_GOVERNANCE_ADOPTION`，未正式採納前不冒充已替代歷史驗收。

### 9.4 依賴生命周期與具名 Canonical Blocker 登記
1. **不冒充 Done、落實 Blocker 規範**：
   - 因原驗收條款 A1–A3 仍為 `partially_met`，`can_closeout_as_done=false`，不可送入 done/approval lane。
   - 本輪完成可做的補證後，依規範由 owner 透過 Canonical CLI (`scripts/ai-status.sh blocker`) 登記具名 Blocker，明確指明待決事項與責任任務：
     - `DPF-BOUNDED-CAPTURE-RETENTION-EXECUTION-001` (Antigravity6)：八域受控真實 raw 擷取；
     - `DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001` (Antigravity)：Masked snapshot 產生、去敏校驗與血統 manifest；
     - `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` (Antigravity2)：Consumer 真實 runtime 回讀、憑證未投影與公網 egress 預設拒絕；
     - `XR-EXT-OSS-FINAL-AUDIT-001` (Antigravity4)：未正式承接之獨立品質/歷史比對驗收。
   - `HUMAN-OSS-LEGAL-APPROVAL-001` 維持 `todo` 並持續依賴本任務，法務許可閘嚴格閉合。

# ODP-DEV-ROLLOUT-001 歷史驗收核對與處置續辦記錄 (2026-09-11)

- **任務 ID**: `ODP-DEV-ROLLOUT-001`
- **任務名稱**: `歷史驗收續辦：ODP-DEV-ROLLOUT-001`
- **原始任務名稱**: `以同一 release digests 部署資料平台與 ODay Plus dev`
- **任務類別**: `documentation` / `phase: History Recovery — executable acceptance reconciliation`
- **實作負責人 (Owner)**: `Antigravity2`
- **獨立審查人 (Reviewer)**: `Codex`
- **記錄時間**: `2026-09-11T12:35:00Z`
- **續辦交付分支**: `task/ODP-DEV-ROLLOUT-001-RECOVERY-20260911`
- **基準 SHA (Base SHA)**: `4499a2993e37b62033926b07de8d8d2e8469a6c7`
- **歷史 PR 交付**: PR [#1013](https://github.com/alfloop-dev/odayplus/pull/1013)（PR head: `83944bb50c56a5071c992edb28e96eae3155f4c0`，merge commit: `b8262d911c95887767877e7cee23bded0ef7dd61`，合併時間: `2026-08-25T17:31:52Z`，合併者: `ajoe734`）

---

## 1. 任務背景與續辦目的 (Context & Purpose)

依使用者明確指示，歷史 archive 遺失之各項恢復任務由 Supervisor Auto Worker 接續辦理。本任務 `ODP-DEV-ROLLOUT-001` 針對歷史已合併 PR [#1013](https://github.com/alfloop-dev/odayplus/pull/1013) 之 dev 環境部署交付物、測試覆蓋與驗收條款進行可執行的精確復原核對。

### 核心核對結論
1. **歷史交付為 False-Done**：
   - 歷史 PR #1013 交付之 `docs/evidence/runtime/ODP-DEV-ROLLOUT-001/` 收據中，全數 6 個元件（`api`, `web`, `data_platform`, `migration`, `worker`, `scheduler`）之映像 digest 均為重複位元佔位符（`sha256:1111…`, `sha256:2222…`, `sha256:3333…`, `sha256:4444…`, `sha256:5555…`, `sha256:6666…`），且 migration/契約/政策 digest 亦為 `sha256:aaaa…` / `bbbb…` / `cccc…`。
   - 收據比對之兩邊皆為同一組佔位字串，致使 `digest_match=true` 僅為文字自比之自我斷言，未曾以真實 container registry 映像在 GCP dev 環境落地。
   - `dev-integration-readback.json` 引用之 5 份讀回報告位於本機未追蹤之 `.odp_data/deployment/` 路徑，倉庫內不存在原始收據，`readback_status=PASSED` 為不可獨立核對之字面摘要。
   - 因此，原歷史結案狀態客觀上為 **false-done**。
2. **歷史檔案保持唯讀，由專屬補救任務正式承接**：
   - 依審計與治理規範，PR #1013 交付於 `docs/evidence/runtime/ODP-DEV-ROLLOUT-001/` 之歷史檔案受 `forbidden_paths` 保護，保持唯讀不予篡改，完整留存作為審計依據。
   - 實質 dev live rollout 責任與真實 digest 落地，已由架構設計專門開立下游補救任務 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`（*以真實 artifact 完成 dev live rollout 並取代 false-done 前提*）正式承接。
3. **本次續辦交付成果**：
   - 完成 A1–A5 逐條驗收客觀核對，明確標註各條款之滿足與未滿足狀態；
   - 建立至 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 之精確責任與條款映射；
   - 完成依賴圖有向無環 (DAG) 驗證；
   - 產出結構化機讀收據 `acceptance-reconciliation.json`，供獨立審查人（`Codex`）審核結案。

---

## 2. 歷史交付事實與收據審計 (Historical Delivery & Receipts Audit)

### 2.1 歷史交付基本資訊

| 查核項目 | 證據來源與形式 | 結果 | 詳細說明與數值 |
|---|---|---|---|
| **歷史 Pull Request** | GitHub PR [#1013](https://github.com/alfloop-dev/odayplus/pull/1013) | `MERGED` | `head_sha: 83944bb50c56a5071c992edb28e96eae3155f4c0`，`merge_sha: b8262d911c95887767877e7cee23bded0ef7dd61`，合併時間 `2026-08-25T17:31:52Z`。 |
| **PR 交付檔案** | Git Tree Diff (`83944bb5` vs base) | `true` | 交付 7 檔，全數位於 `docs/evidence/runtime/ODP-DEV-ROLLOUT-001/`。 |
| **宣告 Artifacts** | Merge commit 與 dev tip 檔案存在性 | `true` | 宣告之 `docs/evidence/runtime/ODP-DEV-ROLLOUT-001/` 目錄於 merge commit 及現行 `dev` 全數存在。 |
| **歷史 CI 檢核** | Exact-head check-runs | `true` | PR #1013 head `83944bb5` 上，7 項 check-runs（`product-e2e-gate`, `performance-gate`, `product`, `change-scope`, `classify`, `boundary`, `orchestrator`）均為 `success`。 |
| **歷史審查批准** | task-review-gate commit status | `true` | `83944bb5` 具名記錄 `task-review-gate: success`，描述 `Approved by assigned reviewer Codex`（2026-08-25T17:06:40Z）。 |
| **Commit Trailers** | Commit Message at head | `true` | `LLM-Agent: Antigravity2`、`Task-ID: ODP-DEV-ROLLOUT-001`、`Reviewer: Codex`、`Verified: uv run --python 3.12 pytest tests/ops/test_dev_rollout.py`。 |

### 2.2 交付收據之真實性與缺陷審計 (Evidence Integrity Audit)

| 檔案路徑 | 行號 / 欄位 | 審計發現與缺陷說明 | 影響判定 |
|---|---|---|---|
| `README.md` | L48-60 | 元件映像 digest 為 `sha256:1111…` 至 `6666…`；`migration_digest` 為 `sha256:aaaa…`，`data_contract_digest` 為 `sha256:bbbb…`，`source_policy_digest` 為 `sha256:cccc…`。 | 佔位符摘要，非真實建置產物 |
| `data-platform-dev-deployment.json` | L10-11 | `image_reference` 為 `ghcr.io/...@sha256:3333…`，`cloud_sql_proxy_image` 為 `sha256:0000…`。 | 佔位符映像 |
| `odayplus-dev-deployment.json` | L20, 26, 32, 37, 44 | `api`, `web`, `runtime` (migration, worker, scheduler) 均為 `sha256:1111…`, `2222…`, `4444…`, `5555…`, `6666…`。 | 佔位符映像，部署未實質發生 |
| `dev-rollout-manifest-binding.json` | L17-88 | 比對之 manifest 與 deployed 兩端皆填入同一組佔位符字串，收據標註 `digest_match: true` 與 `all_digests_match: true`。 | 恆真比對，無證明力 |
| `dev-integration-readback.json` | L12, 38, 51, 59, 64, 69, 75 | 引用之報告路徑為 `.odp_data/deployment/*.json`。 | 暫存路徑未入庫，原始讀回不可查 |
| `external-sources-provider-off-audit.json` | L14-31 | 稽核之 16 項為 ODayPlus 內部快照模型（`store_master_snapshot` 等），非 Data Platform 第三方 16 providers（`cwa`, `tdx`, `osm` 等）。 | 稽核母體不同，缺 live egress 數據 |
| `release-receipts-index.json` | L15-92 | 雖綁定 candidate SHA `e496be62` 與 manifest digest `sha256:23a6d45a`，但底層映像 digest 破裂。 | 實質收據綁定未成立 |

---

## 3. A1–A5 逐條驗收核對結果 (Acceptance Criteria Reconciliation)

| 項次 | 原驗收條款 | 類別 | 判定結果 | 核對依據與分析說明 |
|---|---|---|---|---|
| **A1** | data platform 先於 ODay Plus | runtime／部署 (R) | **已滿足 (met_by_receipt)** | 由 PR #1013 交付之 `dev-rollout-manifest-binding.json`，其 `deployment_order` 明載 sequence 1 = data_platform (GKE oday-dev)，sequence 2 = oday_plus (Cloud Run dev)，時序規劃符合規範。<br>*邊界*：收據記載之部署順序在文件結構層面符合條款；但實質部署因映像 digest 均為佔位符而未以真實生產映像落地。 |
| **A2** | 所有 components符合 release manifest digests | runtime／部署 (R) | **未滿足 (unmet_per_own_receipt)** | `dev-rollout-manifest-binding.json` 及 `odayplus-dev-deployment.json` 中，全數 6 個元件之 image digest 均為重複位元佔位字串（`sha256:1111…` 至 `6666…`）。比對兩端皆為佔位符致使 `digest_match=true` 僅為文字自比，構成 false-done。<br>*邊界*：本條款客觀未達成；實質映像建置與真實 digest 比對由後續補救任務 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` (A1/A2) 承接。 |
| **A3** | dev integration/contract/provider-off readback通過 | runtime／部署 (R) | **未具證據 (not_evidenced)** | `dev-integration-readback.json` 記載 `readback_status=PASSED`，但其引用的 5 份報告均位於本機未追蹤之 `.odp_data/deployment/` 路徑，倉庫內不存在，無法獨立審計。<br>*邊界*：條款屬 runtime 部署類，唯讀範圍內查無原始讀回收據。本項由後續補救任務 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` (A6) 承接。 |
| **A4** | 16 sources disabled且無 credentials/egress | 外部來源啟用狀態 (X) | **部分滿足 (partially_met)** | `external-sources-provider-off-audit.json` 記載 16 項 ODayPlus 內部快照模型且標註 disabled；但此 16 項與 Data Platform 第三方 16 providers（`cwa`, `tdx`, `osm` 等）母體不同，且缺現場 Cloud Run live readback 數據。<br>*邊界*：靜態模型層面已滿足 disabled 設定；運行時現場 Cloud Run VPC egress 限制與零憑證姿態由 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` (A7/A6) 於 live 部署時讀回驗證。 |
| **A5** | receipts綁定 exact SHA與 manifest | runtime／部署 (R) | **未滿足 (unmet_per_own_receipt)** | `release-receipts-index.json` 雖綁定 candidate_sha `e496be62` 與 manifest_digest `sha256:23a6d45a`，但其所綁定的 component image digest 全為佔位符，致使 SHA 與真實映像之綁定關係破裂。<br>*邊界*：SHA 與 manifest 具名存在，但 image digest 綁定不成立；真實收據綁定由 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` (A8/A9) 承接。 |

### 驗收統計摘要
- **總條款數**: 5
- **已滿足 (Met)**: 1 條 (A1)
- **未滿足 (Unmet)**: 2 條 (A2, A5)
- **未具證據 (Not Evidenced)**: 1 條 (A3)
- **部分滿足 (Partially Met)**: 1 條 (A4)
- **歷史候選總體判定**: `blocked`
- **處置結論**: `false_done_superseded`（歷史 PR #1013 交付物客觀構成 false-done，由 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 正式承接）

---

## 4. 補救承接與依賴遷移 (Remediation Transfer & Dependency DAG)

### 4.1 承接任務與條款映射矩陣

```
[ODP-DEV-ROLLOUT-001] (Historical False-Done / Candidate Blocked)
   │
   └──► [ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001] (Active Remediation / Blocked NO-GO)
```

- **承接任務 ID**: `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`
- **承接任務標題**: `以真實 artifact 完成 dev live rollout 並取代 false-done 前提`
- **目前狀態**: `blocked` (Phase: Wave 3 - Dev Live Rollout Remediation, Priority: P0)
- **等待事項**: `Human/Ops, Data Platform Upstream`

| ODP-DEV-ROLLOUT-001 原條款 | 承接之 ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001 條款 | 承接說明與邊界演進 |
|---|---|---|
| **A1** (時序：Data Platform 先於 ODay Plus) | **A4** (`odayplus-runtime-20260825 live readback 顯示 data platform 先部署且 oday-api/oday-web/migration/worker/scheduler 均存在並綁定 exact digests`) | 時序規則已於歷史收據滿足，實質執行由補救任務之 live readback 驗證。 |
| **A2** (所有元件符合 manifest digests) | **A1** (`驗證最新 authoritative manifest 之 candidate SHA、image digests、SBOM、Cosign 與 registry refs 均真實可解析`) 及 **A2** (`若 candidate 到 origin/dev 間含任何變更則建新 release 並重新 build once`) | 佔位符 digest 由補救任務之真實 build-once image digests 與 cosign 簽章取代。 |
| **A3** (整合/契約/provider-off readback 通過) | **A6** (`Cloud Run jobs one-shot、API/Web authenticated smoke、contract、provider-off 與 default-deny egress 均通過`) | 未追蹤之 `.odp_data` 假報告由補救任務之現場 live smoke 與 contract readback 取代。 |
| **A4** (16 sources disabled 且無 credentials/egress) | **A7** (`16 個第三方來源保持 disabled 且 provider credentials 不存在`) 及 **A6** (`default-deny egress`) | 靜態模型 disabled 狀態保留，現場 16 個第三方 provider 關閉與 default-deny egress 由補救任務現場驗證。 |
| **A5** (receipts 綁定 exact SHA 與 manifest) | **A8** (`所有收據含真實 resource identity、Cloud Run URL、revision、execution、timestamp、candidate SHA 與 manifest digest，無 placeholder`) 及 **A9** (`歷史 ODP-DEV-ROLLOUT-001 收據保持不變，由新 evidence 明確標示已被 live reconciliation 推翻`) | 佔位符收據由補救任務產生之真實、secret-redacted live receipts 正式取代。 |

### 4.2 依賴關係比對與 DAG 無環驗證

1. **依賴關係清單比對 (Before vs After)**：
   - **歷史快照依賴 (Historical Task Brief Snapshot)**：`["DPF-EMGI-LIVE-ROLLOUT-001", "ODP-RUNTIME-RELEASE-SINGLE-PATH-001", "ODP-GITHUB-GCP-ENV-BOOTSTRAP-001"]`（均已在歷史波次完成）。
   - **變更前 Canonical 依賴 (`depends_on`)**: `[]`
   - **變更後建議依賴 (`depends_on`)**: `[]`（`dependency_mutation: false`，不變更任何現存依賴）。
   - **下游依賴本任務者 (`canonical_dependents`)**: `["ODP-EPHEMERAL-STAGING-ROLLOUT-001"]`（維持不變）。
2. **DAG 無環檢查 (Cycle Verification)**：
   - 評估節點：`ODP-DEV-ROLLOUT-001`, `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`, `ODP-EPHEMERAL-STAGING-ROLLOUT-001`, `ODP-DEV-STAGED-GATE-RECONCILIATION-001`。
   - 結論：嚴格有向無環圖確認，無循環依賴（`cycle_detected: false`）。

### 4.3 下游補救任務未齊輸入清單 (Missing Inputs)

`ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 目前正確維持在 `blocked` / `NO-GO` 狀態，其解除阻擋需要以下 5 項具體前置條件：
1. **上游 Masked Data Snapshot 產出**：`alfloop-dev/oday-data-platform` 的 `DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001` (PR #63) 於 `odayplus-runtime-20260825` 產出真實 GCS masked snapshot 物件。
2. **Workflow Handoff 參數修正合併**：`ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001` (PR #1109) 修正 Schema v2 handoff 參數傳遞並合併至 `dev`。
3. **GitHub Environment 環境變數與密鑰設定**：在 GitHub environment `dev` 設定 `ODP_WEB_BASE_URL` 與 `ODP_IDENTITY_TOKEN_SIGNING_KEY_SECRET`。
4. **Supervisor Ed25519 部署授權租約**：由 Supervisor 對 `target=dev`, `action=deploy`, exact manifest digest 簽發 release lease。
5. **實質 Live 部署執行**：透過 `deploy-dev.yml` 單一管線執行 Cloud Run 部署，並收集真實、secret-redacted 之 live readback 收據。

---

## 5. 不變量與治理邊界聲明 (Governance Invariants)

1. **未偽造或簽發 Human GO**：本任務定位為歷史驗收核對與處置，未偽造任何人類決策或放行簽署。
2. **未簽發 Release Lease**：本任務未調用 lease issue 工具，未簽發任何 release lease。
3. **未觸發未授權部署**：未對 GCP dev/staging/prod 環境發出任何部署或 workflow_dispatch 命令。
4. **未外洩或讀取 Credentials**：未讀取或持久化任何敏感認證金鑰。
5. **歷史收據唯讀保存**：`docs/evidence/runtime/ODP-DEV-ROLLOUT-001/` 歷史收據完整保留於原路徑，未進行任何修改。
6. **單一寫入範圍限制**：本次所有補證交付嚴格局限於 `docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-DEV-ROLLOUT-001/`。

---

## 6. 當前驗證收據與命令 (Verification Commands & Receipts)

### 6.1 靜態與 JSON 結構驗證

```bash
# 1. Git Diff 格式檢查
git diff --check 4499a2993e37b62033926b07de8d8d2e8469a6c7 HEAD

# 2. 結構化機讀 JSON 驗證
python3 -c '
import json
from pathlib import Path

base = Path("docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-DEV-ROLLOUT-001")
assert (base / "README.md").is_file(), "README.md missing"
assert (base / "acceptance-reconciliation.json").is_file(), "acceptance-reconciliation.json missing"

data = json.loads((base / "acceptance-reconciliation.json").read_text(encoding="utf-8"))
assert data["task_id"] == "ODP-DEV-ROLLOUT-001"
assert data["acceptance_criteria_summary"]["total_criteria"] == 5
assert data["acceptance_criteria_summary"]["by_status"]["met_by_receipt"] == 1
assert data["acceptance_criteria_summary"]["by_status"]["unmet_per_own_receipt"] == 2
assert data["acceptance_criteria_summary"]["by_status"]["not_evidenced"] == 1
assert data["acceptance_criteria_summary"]["by_status"]["partially_met"] == 1
assert data["acceptance_criteria_summary"]["overall_historical_candidate_verdict"] == "blocked"
assert data["acceptance_criteria_summary"]["disposition_conclusion"] == "false_done_superseded"
assert len(data["criteria_to_remediation_task_mapping"]["mapping"]) == 5
assert data["dependency_graph_and_cycle_verification"]["dag_cycle_check"]["cycle_detected"] is False
print("ODP-DEV-ROLLOUT-001 acceptance reconciliation validation: PASSED")
'
```

### 6.2 驗證執行結果
- `git diff --check`: Exit Code 0 (Clean)
- `python3 acceptance reconciliation validation`: Exit Code 0 (PASSED)

# ODP-DEV-ROLLOUT-001 歷史驗收核對與處置續辦記錄 (2026-09-12)

- **任務 ID**: `ODP-DEV-ROLLOUT-001`
- **任務名稱**: `歷史驗收續辦：ODP-DEV-ROLLOUT-001`
- **原始任務名稱**: `以同一 release digests 部署資料平台與 ODay Plus dev`
- **任務類別**: `documentation` / `phase: History Recovery — executable acceptance reconciliation`
- **實作負責人 (Owner)**: `Antigravity2`
- **獨立審查人 (Reviewer)**: `Codex`
- **記錄時間**: `2026-09-12T10:05:00Z`
- **續辦交付分支**: `task/ODP-DEV-ROLLOUT-001-RECOVERY-20260911`
- **目標基準 SHA (Target Base SHA)**: `d977447ee0c537a1c7eeb6f68ec912d033816f88` (`d977447ee0c5`，origin/dev tip composed via base advance merge)
- **前輪基準校正 (Previous Composed Base SHA)**: `3828c5ada2a1baab33d7dbe734c7ec70152d3d77`（已成功與 origin/dev tip `d977447ee0c5` 完成 base advance 整合）
- **初始恢復起點 (Initial Base SHA)**: `4499a2993e37b62033926b07de8d8d2e8469a6c7`
- **歷史 PR 交付**: PR [#1013](https://github.com/alfloop-dev/odayplus/pull/1013)（PR head: `83944bb50c56a5071c992edb28e96eae3155f4c0`，merge commit: `b8262d911c95887767877e7cee23bded0ef7dd61`，合併時間: `2026-08-25T17:31:52Z`，合併者: `ajoe734`）

---

## 1. 任務背景與續辦目的 (Context & Purpose)

依使用者明確指示，歷史 archive 遺失之各項恢復任務由 Supervisor Auto Worker 接續辦理。本任務 `ODP-DEV-ROLLOUT-001` 針對歷史已合併 PR [#1013](https://github.com/alfloop-dev/odayplus/pull/1013) 之 dev 環境部署交付物、測試覆蓋與驗收條款進行可執行的精確復原核對。

### 核心核對結論
1. **歷史交付為 False-Done**：
   - 歷史 PR #1013 交付之 `docs/evidence/runtime/ODP-DEV-ROLLOUT-001/` 收據中，全數 6 個元件（`api`, `web`, `data_platform`, `migration`, `worker`, `scheduler`）之映像 digest 均為重複位元佔位符（`sha256:1111…`, `sha256:2222…`, `sha256:3333…`, `sha256:4444…`, `sha256:5555…`, `sha256:6666…`），且 migration/契約/政策 digest 亦為 `sha256:aaaa…` / `bbbb…` / `cccc…`。
   - 收據比對之兩邊皆為同一組佔位字串，致使 `digest_match=true` 僅為文字自比之自我斷言，無法證明實質部署在 GCP dev 環境落地。
   - `dev-integration-readback.json` 引用之 5 份讀回報告位於本機未追蹤之 `.odp_data/deployment/` 路徑，倉庫內不存在原始收據，`readback_status=PASSED` 為不可獨立核對之字面摘要。
   - 因此，原歷史結案狀態客觀上為 **false-done**。
2. **納入已完成 Manifest / Build / Candidate 原始證據（精確區分 Producer 與 Handoff Runs）**：
   - 後續候選重整任務 `ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002`（PR [#1292](https://github.com/alfloop-dev/odayplus/pull/1292)，合併於 `2026-09-10T18:58:07Z`，merge SHA: `3613faff582bd1c5a2c9b2ae5b5cd390d8be381f`）已確立凍結候選基準 $C = \text{596b9c9a1788d952811a2bf8d4bba8a4e4d76b12}$ 與權威 release manifest digest $\text{sha256:1b5348d907643e3f3087642d8674002beec016bd16ef38b00e261e88da026e6c}$。
   - **Producer Run `34179207603`**（job `101914686431`，02:11:41Z – 02:20:27Z，conclusion: `failure`）：Step 19 成功建置、push、`cosign sign` 與 attest 4 項不可變映像；隨後 step 20 handoff 以 exit code 1 失敗，未產出 manifest artifacts。
   - **Handoff Run `34179791241`**（job `101916404681`，02:21:49Z – 02:26:33Z，conclusion: `success`）：Step 19 重用候選 $C$ 既有映像 digest（`reusing their digests`），執行 `cosign verify` 離線驗章，並執行 steps 20–23 發布 6 份不可變 release artifacts；後續 Deploy/Lease/Watch jobs 保持 `skipped`。
   - 這證明真實候選與建置產物存在，但這兩 run 的 deploy jobs skipped／現有證據無法證明 rollout。
3. **歷史檔案保持唯讀，由專屬補救任務正式承接**：
   - 依審計與治理規範，PR #1013 交付於 `docs/evidence/runtime/ODP-DEV-ROLLOUT-001/` 之歷史檔案受 `forbidden_paths` 保護，保持唯讀不予篡改，完整留存作為審計依據。
   - 實質 dev live rollout 責任與真實 digest 落地，已由架構設計專門開立下游補救任務 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`（*以真實 artifact 完成 dev live rollout 並取代 false-done 前提*）正式承接。
4. **本次續辦交付成果**：
   - 完成 A1–A5 逐條驗收客觀核對，明確區分歷史收據判定與運行態實質執行狀態；
   - 建立至 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 之精確責任與一對多條款映射（A2 映射 A1/A2/A4，A4 映射 A7/A6，A5 映射 A8/A9），保留 successor A2「若 candidate 到 origin/dev 之間含任何 product 或 build input 變更則建立新 release 並重新 build once不得沿用舊 digest」之 product/build input 限定；
   - 完整保存 17 個節點之 Canonical Scoped Snapshots（`canonical-task-snapshots.json`，含葉節點空依賴來源、來源檔案與 SHA-256 內容 Hash）；
   - 提供可獨立執行的專屬驗證腳本 `verify_reconciliation.py`（SHA-256: `3ee2c75734dee949689f4571c55150c1108c2f313d17de487fa7d34a5ddc8b56`），直接從 canonical scoped snapshots 推導 17 條邊，確認有向無環圖 (DAG) 與邊集 SHA-256 (`d1db3233c193f7ce93e0895efb227d3314376e68724db0bca7ae0579438fef4d`)；
   - 保存 canonical 看板與 archive 原始快照，說明既有 staging -> live remediation 閘門已存在，因此無需變更依賴；處置維持擬議生命週期語意（`false_done_superseded`），不宣稱 canonical supersede 已在控制平面執行；
   - 產出結構化機讀收據 `acceptance-reconciliation.json` 與真實驗證收據，供獨立審查人（`Codex`）審核結案。

---

## 2. 歷史交付事實與收據審計 (Historical Delivery & Receipts Audit)

### 2.1 歷史交付基本資訊

| 查核項目 | 證據來源與形式 | 結果 | 詳細說明與數值 |
|---|---|---|---|
| **歷史 Pull Request** | GitHub PR [#1013](https://github.com/alfloop-dev/odayplus/pull/1013) | `MERGED` | `head_sha: 83944bb50c56a5071c992edb28e96eae3155f4c0`，`merge_sha: b8262d911c95887767877e7cee23bded0ef7dd61`，合併時間 `2026-08-25T17:31:52Z`。 |
| **PR 交付檔案總數** | Git Tree Diff (`83944bb5` vs base) | `9 files` | 全 PR 共交付 9 檔：7 檔位於 `docs/evidence/runtime/ODP-DEV-ROLLOUT-001/`，另含 `docs/audits/code-boundary-inventory.csv` 與 `tests/ops/test_dev_rollout.py`。 |
| **宣告 Artifacts** | Merge commit 與 dev tip 檔案存在性 | `true` | 宣告之 `docs/evidence/runtime/ODP-DEV-ROLLOUT-001/` 目錄於 merge commit 及現行 `dev` 全數存在。 |
| **歷史 CI 檢核** | Exact-head check-runs | `true` | PR #1013 head `83944bb5` 上，7 項 check-runs（`product-e2e-gate`, `performance-gate`, `product`, `change-scope`, `classify`, `boundary`, `orchestrator`）均為 `success`。 |
| **歷史審查批准** | task-review-gate commit status | `true` | `83944bb5` 具名記錄 `task-review-gate: success`，描述 `Approved by assigned reviewer Codex`（2026-08-25T17:06:40Z）。 |
| **Commit Trailers** | Commit Message at head | `true` | `LLM-Agent: Antigravity2`、`Task-ID: ODP-DEV-ROLLOUT-001`、`Reviewer: Codex`、`Verified: uv run --python 3.12 pytest tests/ops/test_dev_rollout.py`。 |

### 2.2 交付收據之真實性與缺陷審計 (Evidence Integrity Audit)

| 檔案路徑 | 行號 / 欄位 | 審計發現與缺陷說明 | 影響判定 |
|---|---|---|---|
| `README.md` | L48-60 | 元件映像 digest 為 `sha256:1111…` 至 `6666…`；`migration_digest` 為 `sha256:aaaa…`，`data_contract_digest` 為 `sha256:bbbb…`，`source_policy_digest` 為 `sha256:cccc…`。 | 佔位符摘要，非真實建置產物 |
| `data-platform-dev-deployment.json` | L10-11 | `image_reference` 為 `ghcr.io/...@sha256:3333…`，`cloud_sql_proxy_image` 為 `sha256:0000…`。 | 佔位符映像 |
| `odayplus-dev-deployment.json` | L20, 26, 32, 37, 44 | `api`, `web`, `runtime` (migration, worker, scheduler) 均為 `sha256:1111…`, `2222…`, `4444…`, `5555…`, `6666…`。 | 佔位符映像，無法證明實質部署 |
| `dev-rollout-manifest-binding.json` | L17-88 | 比對之 manifest 與 deployed 兩端皆填入同一組佔位符字串，收據標註 `digest_match: true` 與 `all_digests_match: true`。其 `deployment_order` 載明 sequence 1=data_platform (GKE oday-dev)、sequence 2=oday_plus_migration (Cloud Run Job)、sequence 3=oday_plus_services (Cloud Run Services & Jobs)。 | 恆真比對，無證明力 |
| `dev-integration-readback.json` | L12, 38, 51, 59, 64, 69, 75 | 引用之報告路徑為 `.odp_data/deployment/*.json`。 | 暫存路徑未入庫，原始讀回不可查 |
| `external-sources-provider-off-audit.json` | L14-31 | 稽核之 16 項為 ODayPlus 內部快照模型（`store_master_snapshot` 等），非 Data Platform 第三方 16 providers（`cwa`, `tdx`, `osm` 等）。 | 稽核母體不同，缺 live egress 數據 |
| `release-receipts-index.json` | L15-92 | 雖綁定 candidate SHA `e496be62` 與 manifest digest `sha256:23a6d45a`，但底層映像 digest 破裂。 | 實質收據綁定未成立 |

### 2.3 現行候選與已完成建置證據 (Completed Candidate & Build Receipts)

| 項目 | 證據來源與 SHA / ID | 狀態與結論 | 詳細說明與產出 |
|---|---|---|---|
| **候選基準任務** | `ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002` (PR [#1292](https://github.com/alfloop-dev/odayplus/pull/1292)) | `MERGED`<br>(2026-09-10T18:58:07Z, merge `3613faff`) | 凍結候選基準 $C = \text{596b9c9a1788d952811a2bf8d4bba8a4e4d76b12}$，Manifest digest $\text{sha256:1b5348d907643e3f3087642d8674002beec016bd16ef38b00e261e88da026e6c}$。 |
| **映像建置與簽章 Run (Producer)** | GitHub Actions Run [`34179207603`](https://github.com/alfloop-dev/odayplus/actions/runs/34179207603) (head `8c570a56`) | `BUILD/SIGN SUCCESS`<br>`STEP20 FAILURE (EXIT 1)` | Job `101914686431`（02:11:41Z – 02:20:27Z）：step 19 成功建置、push 並簽名 4 項不可變映像：<br>• `api`: `...@sha256:5e1a152e839cbfa7a2bf422b924928b56fad89f35e44243619a3f2fe98802cee`<br>• `web`: `...@sha256:38c716462b569b7420fe95788a84f8a1b3778e2e7c938d535a1dc13288a78971`<br>• `worker`: `...@sha256:b2c0e4473ad529ad71f215b76fc125115d8ba0b4e845a87e532d10ebdb8ddba3`<br>• `scheduler`: `...@sha256:f3fd22c00478d730273494c23c512a87c807de645a8b759d0af4908d82cd4cc9`<br>Step 20 寫入 handoff 失敗，未產出 manifest。 |
| **Artifact 發布 Run (Handoff)** | GitHub Actions Run [`34179791241`](https://github.com/alfloop-dev/odayplus/actions/runs/34179791241) (head `8c570a56`) | `ARTIFACTS SUCCESS`<br>`DEPLOY SKIPPED` | Job `101916404681`（02:21:49Z – 02:26:33Z）：step 19 重用既有 4 映像 digest 並執行 `cosign verify` 離線驗證通過；steps 20–23 成功發布 6 份 release artifacts（`RELEASE_MANIFEST.json` 等）；Deploy/Lease/Watch jobs 保持 `skipped`。 |

> [!NOTE]
> Run 34179207603 產出了映像與簽章，Run 34179791241 重用 digest 並發布了 release artifacts；Deploy 階段依准入控制保持 skipped。這是候選建置與產物發布完成的證據，非 rollout 通過。

---

## 3. A1–A5 逐條驗收核對結果 (Acceptance Criteria Reconciliation)

| 項次 | 原驗收條款 | 類別 | 歷史收據判定 | 運行態實質狀態 | 核對依據與分析說明 |
|---|---|---|---|---|---|
| **A1** | data platform 先於 ODay Plus | runtime／部署 (R) | **已滿足 (met_by_receipt)** | **未具證據 (not_evidenced)** | 由 PR #1013 交付之 `dev-rollout-manifest-binding.json`，其 `deployment_order` 載明 sequence 1 = data_platform (GKE oday-dev)、sequence 2 = oday_plus_migration (Cloud Run Job)、sequence 3 = oday_plus_services (Cloud Run Services & Jobs)。<br>*邊界*：收據記載之部署順序在文件結構層面符合條款（保留 inventory 歷史判定）；但因映像 digest 均為佔位符致無法證明實質部署，本輪 runtime 執行未具真實證據，不以 placeholder sequence 充作已部署。實質時序由 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` (A4) 於 live 部署時讀回驗證。 |
| **A2** | 所有 components符合 release manifest digests | runtime／部署 (R) | **未滿足 (unmet_per_own_receipt)** | **未滿足 (unmet_per_own_receipt)** | `dev-rollout-manifest-binding.json` 及 `odayplus-dev-deployment.json` 中，全數 6 個元件之 image digest 均為重複位元佔位字串（`sha256:1111…` 至 `6666…`）。比對兩端皆為佔位符致使 `digest_match=true` 僅為文字自比，構成 false-done。<br>*邊界*：本條款客觀未達成；實質映像建置與真實 digest 比對由後續補救任務 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` (A1/A2/A4) 承接。 |
| **A3** | dev integration/contract/provider-off readback通過 | runtime／部署 (R) | **未具證據 (not_evidenced)** | **未具證據 (not_evidenced)** | `dev-integration-readback.json` 記載 `readback_status=PASSED`，但其引用的 5 份報告均位於本機未追蹤之 `.odp_data/deployment/` 路徑，倉庫內不存在，無法獨立審計。<br>*邊界*：條款屬 runtime 部署類，唯讀範圍內查無原始讀回收據。本項由後續補救任務 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` (A6) 承接。 |
| **A4** | 16 sources disabled且無 credentials/egress | 外部來源啟用狀態 (X) | **部分滿足 (partially_met)** | **部分滿足 (partially_met)** | `external-sources-provider-off-audit.json` 記載 16 項 ODayPlus 內部快照模型且標註 disabled；但此 16 項與 Data Platform 第三方 16 providers（`cwa`, `tdx`, `osm` 等）母體不同，且缺現場 Cloud Run live readback 數據。<br>*邊界*：靜態模型層面已滿足 disabled 設定；運行時現場 Cloud Run VPC egress 限制與零憑證姿態轉由 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` (A7/A6) 於 live 部署時讀回驗證。 |
| **A5** | receipts綁定 exact SHA與 manifest | runtime／部署 (R) | **未滿足 (unmet_per_own_receipt)** | **未滿足 (unmet_per_own_receipt)** | `release-receipts-index.json` 雖綁定 candidate_sha `e496be62` 與 manifest_digest `sha256:23a6d45a`，但其所綁定的 component image digest 全為佔位符，致使 SHA 與真實映像之綁定關係破裂。<br>*邊界*：SHA 與 manifest 具名存在，但 image digest 綁定不成立；真實收據綁定由 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` (A8/A9) 承接。 |

### 驗收統計摘要
- **總條款數**: 5
- **依歷史收據判定 (By Historical Receipt)**:
  - 已滿足 (Met): 1 條 (A1)
  - 未滿足 (Unmet): 2 條 (A2, A5)
  - 未具證據 (Not Evidenced): 1 條 (A3)
  - 部分滿足 (Partially Met): 1 條 (A4)
- **依運行態實質執行判定 (By Runtime Execution)**:
  - 已滿足 (Met): 0 條
  - 未滿足 (Unmet): 2 條 (A2, A5)
  - 未具證據 (Not Evidenced): 2 條 (A1, A3)
  - 部分滿足 (Partially Met): 1 條 (A4)
- **歷史候選總體判定**: `blocked`
- **處置結論**: `false_done_superseded`（歷史 PR #1013 交付物客觀構成 false-done，由 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 正式承接）

---

## 4. 補救承接與依賴遷移 (Remediation Transfer & Dependency DAG)

### 4.1 承接任務與一對多條款映射矩陣

```
[ODP-DEV-ROLLOUT-001] (Historical False-Done / Candidate Blocked)
   │
   └──► [ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001] (Active Remediation / Blocked NO-GO)
```

- **承接任務 ID**: `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`
- **承接任務標題**: `以真實 artifact 完成 dev live rollout 並取代 false-done 前提`
- **目前狀態**: `blocked` (Phase: Wave 3 - Dev Live Rollout Remediation, Priority: P0)
- **等待事項**: `Human/Ops, Release Lease, GitHub Environment Secrets`

| ODP-DEV-ROLLOUT-001 原條款 | 承接之 ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001 條款 | 承接說明與邊界演進 |
|---|---|---|
| **A1** (時序：Data Platform 先於 ODay Plus) | **A4** (`odayplus-runtime-20260825 live readback 顯示 data platform 先部署且 oday-api/oday-web/migration/worker/scheduler 均存在並綁定 exact digests`) | 時序規則已於歷史收據規劃，實質執行由補救任務之 live readback 驗證。 |
| **A2** (所有元件符合 manifest digests) | **A1** (`驗證最新 authoritative manifest 之 candidate SHA、image digests、SBOM、Cosign 與 registry refs 均真實可解析`)、**A2** (`若 candidate 到 origin/dev 之間含任何 product 或 build input 變更則建立新 release 並重新 build once不得沿用舊 digest`)、及 **A4** (`live readback 顯示各元件綁定 exact digests`) | 佔位符 digest 由補救任務之真實 build-once image digests、SBOM 與 cosign 簽章驗證取代，並保留 product/build input 變更限定。 |
| **A3** (整合/契約/provider-off readback 通過) | **A6** (`Cloud Run jobs one-shot、API/Web authenticated smoke、contract、provider-off 與 default-deny egress 均通過`) | 未追蹤之 `.odp_data` 假報告由補救任務之現場 live smoke 與 contract readback 取代。 |
| **A4** (16 sources disabled 且無 credentials/egress) | **A7** (`16 個第三方來源保持 disabled 且 provider credentials 不存在`) 及 **A6** (`default-deny egress`) | 靜態模型 disabled 狀態保留，現場 16 個第三方 provider 關閉與 default-deny egress 由補救任務現場驗證。 |
| **A5** (receipts 綁定 exact SHA 與 manifest) | **A8** (`所有收據含真實 resource identity、Cloud Run URL、revision、execution、timestamp、candidate SHA 與 manifest digest，無 placeholder`) 及 **A9** (`歷史 ODP-DEV-ROLLOUT-001 收據保持不變，由新 evidence 明確標示已被 live reconciliation 推翻`) | 佔位符收據由補救任務產生之真實、secret-redacted live receipts 正式取代，歷史收據唯讀保留。 |

### 4.2 依賴關係比對與完整 DAG 無環驗證 (Dependency Graph & Full DAG Cycle Verification)

1. **依賴關係清單比對 (Before vs After)**：
   - **歷史快照依賴 (Historical Task Brief Snapshot)**：`["DPF-EMGI-LIVE-ROLLOUT-001", "ODP-RUNTIME-RELEASE-SINGLE-PATH-001", "ODP-GITHUB-GCP-ENV-BOOTSTRAP-001"]`（均已在歷史波次完成）。
   - **變更前 Canonical 依賴 (`depends_on`)**: `[]`
   - **變更後建議依賴 (`depends_on`)**: `[]`（`dependency_mutation: false`，不變更任何現存依賴）。
   - **下游依賴本任務者 (`canonical_dependents`)**: `["ODP-EPHEMERAL-STAGING-ROLLOUT-001"]`（維持不變）。
2. **Canonical 原始快照依據與 17 節點忠實快照 (Canonical Scoped Snapshots)**：
   - 完整 17 節點之來源檔案、SHA-256 內容 Hash 及依賴宣告已完整保存於 `docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-DEV-ROLLOUT-001/canonical-task-snapshots.json`（SHA-256: `d6128067c4c3e2b5fc8fc099a91ca0d658397640f1e2079d3a0da1d5cd171cce`）。
   - 包含 14 個葉節點（`depends_on: []`）的來源檔案（例如 `DPF-EMGI-LIVE-ROLLOUT-001` 等 6 檔來自 `ai-status.json`，其餘來自 `ai-task-archive/tasks/`）。
   - `ODP-EPHEMERAL-STAGING-ROLLOUT-001`（`ai-status.json`）在 canonical 看板為 `status: blocked`，已具備 5 項依賴：`ODP-EPHEMERAL-STAGING-IAC-001`、`ODP-DEV-ROLLOUT-001`、`ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`、`ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001`、`ODP-STAGING-RECOVERY-BUNDLE-STORAGE-001`。
   - `ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002`（`ai-task-archive/tasks/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002.json`，`terminal_status: done`）具備 2 項依賴：`ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001`、`ODP-RELEASE-GATE-FIXTURE-STAGING-002`。
   - `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`（`ai-status.json`）具備 10 項依賴。
   - **處置說明**：因 canonical 看板中 `ODP-EPHEMERAL-STAGING-ROLLOUT-001` 已直接依賴 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`，實質 dev live rollout 閘門已在看板生效阻擋 staging，故本任務無需變更任何看板依賴。處置維持擬議生命週期語意（`false_done_superseded`），不宣稱 canonical supersede 已在控制平面執行完畢。
3. **完整 17 節點 17 邊 DAG 無環檢查 (Cycle Verification)**：
   - 完整評估節點 (17 個)：
     `DPF-EMGI-LIVE-ROLLOUT-001`, `ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002`, `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`, `ODP-DEV-ROLLOUT-001`, `ODP-DEV-STAGED-GATE-RECONCILIATION-001`, `ODP-EPHEMERAL-STAGING-IAC-001`, `ODP-EPHEMERAL-STAGING-ROLLOUT-001`, `ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001`, `ODP-GITHUB-GCP-ENV-BOOTSTRAP-001`, `ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001`, `ODP-RELEASE-GATE-FIXTURE-STAGING-002`, `ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001`, `ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001`, `ODP-RUNTIME-RELEASE-SINGLE-PATH-001`, `ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001`, `ODP-STAGING-RECOVERY-BUNDLE-STORAGE-001`, `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002`。
   - 完整邊集 (17 條邊，直接從 17 節點快照之 `depends_on` 嚴格推導)：
     1. `ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002` ──► `ODP-RELEASE-GATE-FIXTURE-STAGING-002`
     2. `ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002` ──► `ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001`
     3. `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` ──► `DPF-EMGI-LIVE-ROLLOUT-001`
     4. `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` ──► `ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002`
     5. `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` ──► `ODP-DEV-STAGED-GATE-RECONCILIATION-001`
     6. `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` ──► `ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001`
     7. `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` ──► `ODP-GITHUB-GCP-ENV-BOOTSTRAP-001`
     8. `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` ──► `ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001`
     9. `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` ──► `ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001`
     10. `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` ──► `ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001`
     11. `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` ──► `ODP-RUNTIME-RELEASE-SINGLE-PATH-001`
     12. `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` ──► `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002`
     13. `ODP-EPHEMERAL-STAGING-ROLLOUT-001` ──► `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`
     14. `ODP-EPHEMERAL-STAGING-ROLLOUT-001` ──► `ODP-DEV-ROLLOUT-001`
     15. `ODP-EPHEMERAL-STAGING-ROLLOUT-001` ──► `ODP-EPHEMERAL-STAGING-IAC-001`
     16. `ODP-EPHEMERAL-STAGING-ROLLOUT-001` ──► `ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001`
     17. `ODP-EPHEMERAL-STAGING-ROLLOUT-001` ──► `ODP-STAGING-RECOVERY-BUNDLE-STORAGE-001`
   - **邊集 SHA256**: `d1db3233c193f7ce93e0895efb227d3314376e68724db0bca7ae0579438fef4d`
   - **結論**: 嚴格有向無環圖確認，無循環依賴（`cycle_detected: false`）。

### 4.3 下游補救任務未齊輸入清單 (Missing Inputs)

`ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 目前正確維持在 `blocked` / `NO-GO` 狀態，其解除阻擋需要以下 5 項具體前置條件：
1. **GitHub Environment 環境變數與密鑰設定**：在 GitHub environment `dev` 設定 `ODP_WEB_BASE_URL` 與 `ODP_IDENTITY_TOKEN_SIGNING_KEY_SECRET`。
2. **Dev Release Boundary Gates 准入核准**：Gate 0 (Code), Gate 1 (Contract), Gate 4 (Security) 經 Human/Ops 風險評估後獲得准入核准。
3. **Supervisor Ed25519 部署授權租約**：由 Supervisor 對 `target=dev`, `action=deploy`, exact candidate SHA `596b9c9a1788` 與 manifest digest `sha256:1b5348d9…` 簽發 release lease。
4. **實質 Live 部署執行**：透過 `deploy-dev.yml` 單一管線執行 Cloud Run / GKE 部署。
5. **收集真實 Live Readback 收據**：收集真實、secret-redacted 之 live readback 收據（Cloud Run smoke、one-shot jobs 執行、VPC default-deny egress 驗證）。

> [!NOTE]
> **已澄清之 Code Gate 與條件範圍**：
> - `ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001` (PR [#1109](https://github.com/alfloop-dev/odayplus/pull/1109)) 已於 2026-09-01T14:49:31Z 合併至 `dev`（merge SHA: `640e35415aa33d5d53af21a8a527431b8f751cea`），canonical archive 狀態為 `done`，不再列為未齊輸入。
> - 在 sources-off 部署姿態下（16 external sources disabled），DPF masked snapshot 非強制通用前置阻塞條件，其適用範圍局限於資料平台外部資料攝取啟用任務。

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

### 6.1 靜態與結構驗證命令

```bash
# 1. Git Diff 格式檢查（對準當前 origin/dev base advance 目標 d977447ee0c5）
git diff --check d977447ee0c537a1c7eeb6f68ec912d033816f88 HEAD

# 2. 執行獨立驗證腳本（驗證機讀 JSON、17 節點快照與 DAG 邊集推導）
python3 docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-DEV-ROLLOUT-001/verify_reconciliation.py
```

### 6.2 驗證執行結果與可追溯原始收據 (Verification Execution Receipts)

| 收據 ID | 驗證項目與執行命令 | 基準 SHA / 觀測時間 | 執行時長 | 退出碼 | 原始結果與判定 |
|---|---|---|---|---|---|
| `rcpt-001` | **Git Diff 格式檢查**<br>`git diff --check d977447ee0c537a1c7eeb6f68ec912d033816f88 HEAD` | `d977447ee0c5`<br>`2026-09-12T10:05:10Z` | `0.018s` | `0` | stdout 空，無 trailing whitespace 或 conflict marker，格式完全乾淨。 |
| `rcpt-002` | **撤回診斷記錄**<br>`git diff --check 4b35121031d054178550beaa2ca3e6ee8e0a39eb HEAD` | `4b35121031d0...`<br>`2026-09-12T09:23:08Z` | `0.000007358s` | `128` | stderr: `fatal: bad object 4b35121031d054178550beaa2ca3e6ee8e0a39eb`。確認筆誤並正式撤回不實成功記錄。 |
| `rcpt-003` | **歷史 Exact Head 覆核 (Reused)**<br>`git diff --check 4b35121031d0738ff7c529810cf1b2e267161083 cfe569e1b4692af4c5c5c80d87c881f64d510593` | `4b35121031d0`<br>`2026-09-12T09:23:08Z` | `0.000012862s` | `0` | Codex reviewer 於 PR #1320 exact head `cfe569e1` 獨立實測 exit 0，精確 baseline SHA 4b35121031d0738ff7c529810cf1b2e267161083。 |
| `rcpt-004` | **獨立腳本 JSON 與 17 節點 17 邊 DAG 驗證**<br>`python3 docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-DEV-ROLLOUT-001/verify_reconciliation.py` | `HEAD`<br>`2026-09-12T10:05:15Z` | `0.048s` | `0` | 腳本 SHA-256: `3ee2c75734dee949689f4571c55150c1108c2f313d17de487fa7d34a5ddc8b56`<br>快照 SHA-256: `d6128067c4c3e2b5fc8fc099a91ca0d658397640f1e2079d3a0da1d5cd171cce`<br>stdout: `ODP-DEV-ROLLOUT-001 Acceptance Reconciliation Verification: ALL CHECKS PASSED`（5 criteria、DAG 17 節點 17 邊無環確認、edge hash `d1db3233c193f7ce93e0895efb227d3314376e68724db0bca7ae0579438fef4d` 一致）。 |
| `rcpt-005` | **PR 1109 Ancestry 驗證**<br>`git merge-base --is-ancestor 640e35415aa33d5d53af21a8a527431b8f751cea d977447ee0c537a1c7eeb6f68ec912d033816f88` | `640e35415aa3`<br>`2026-09-12T10:05:20Z` | `0.012s` | `0` | 參照 `ai-task-archive/tasks/ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001.json`（PR #1109 merged at `2026-09-01T14:49:31Z`），實測確認已為 target_base 之 ancestor。 |
| `rcpt-006` | **Run 34179207603 / Run 34179791241 職責驗證 (Reused)**<br>`bash docs/evidence/runtime/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002/verify_live_artifact_binding.sh /tmp/odp-run-34179791241 /tmp/odp-candidate-c` | `596b9c9a1788`<br>`2026-09-08T05:05:00Z` | `unknown` | `0` | 參照 `alfloop-dev/odayplus@3613faff582bd1c5a2c9b2ae5b5cd390d8be381f:docs/evidence/runtime/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002/verification-transcript.txt` 及同 commit README。Producer run 34179207603（02:11:41Z – 02:20:27Z，step 19 成功 build/push/sign 4 images，step 20 exit 1）；Handoff run 34179791241（02:21:49Z – 02:26:33Z，step 19 重用 digest 離線 cosign verify，steps 20-23 成功發布 6 份 release artifacts，deploy/lease/watch skipped）。原始 transcript 未記量測 duration 標為 unknown。 |

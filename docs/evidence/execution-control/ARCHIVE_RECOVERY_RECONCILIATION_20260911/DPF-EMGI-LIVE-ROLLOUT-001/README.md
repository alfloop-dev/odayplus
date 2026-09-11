# DPF-EMGI-LIVE-ROLLOUT-001 歷史驗收續辦與補證核對記錄 (2026-09-11)

## 1. 任務背景與復原目標

- **任務 ID**: `DPF-EMGI-LIVE-ROLLOUT-001`
- **任務名稱**: 歷史驗收續辦：DPF-EMGI-LIVE-ROLLOUT-001
- **原始任務名稱**: 發布 exact-digest data platform 並完成 EMGI sources-off runtime
- **執行身分 (Owner)**: `Antigravity3`
- **指派審查者 (Reviewer)**: `Codex`
- **復原目標分支**: `task/DPF-EMGI-LIVE-ROLLOUT-001-RECOVERY-20260911`
- **對照基準 (Target Dev Baseline)**: `2889b55fb1febe95c9f8650f24ead18e86015cca`
- **Pre-fix Parent Commit**: `294b67e7b040be26e71cf416df65cbd9f3bf2d8c`
- **交付儲存庫**: 原始跨儲存庫交付於 `alfloop-dev/oday-data-platform`（PR #62），本補證記錄交付於 `alfloop-dev/odayplus`。

本任務原始交付物於 2026-08-25 經由跨 repo PR [#62](https://github.com/alfloop-dev/oday-data-platform/pull/62)（標題：`DPF-EMGI-LIVE-ROLLOUT-001: exact-digest publish 與 EMGI sources-off runtime`）合併入 `dev`（PR head: `71ecbe0d982f3f93c976fa0104a82903d2a071cf`，merge commit: `e3ecd2f199fe051aba8d3d33005c217036a9c88e`）。

在 2026-09-06 archive 事故後的歷史盤點中，A1–A4 四項驗收具備完整收據支持。然而 A5 條款（「部署與 rollback receipts 綁定 digest」）因 `rollout-binding.json` 中 `rollback_receipt: null` 且 `live-rollout-receipt.json` 記載 `missing_receipts: ["rollback_receipt"]`，而被盤點標記為 `unmet_per_own_receipt`。

本輪續辦經唯讀查核 GitHub Actions 實體記錄、原始工作流程（`.github/workflows/emgi-runtime-deploy.yml`）與下載歷史 run artifacts（9566074439 與 9563435760），完整核實了各項驗收狀態，對 A1–A4 給予充分證明，並如實保留 A5 缺口（未滿足），建立責任銜接與依賴分析，不偽造收據、不自簽 AI waiver。

---

## 2. 唯讀查核證據盤點 (Readback Evidence)

經由 GitHub API 唯讀查詢 `alfloop-dev/oday-data-platform` 之歷史記錄與不可變收據：

| 查核項目 | 查核結果 | 具體證據與參數 |
|---|---|---|
| **跨 Repo PR 與 Merge 狀態** | `MERGED` | PR [#62](https://github.com/alfloop-dev/oday-data-platform/pull/62)，head `71ecbe0d`，merge commit `e3ecd2f1`，於 2026-08-25T14:24:47Z 由 `ajoe734` 合併 |
| **Exact-Head CI 檢查** | 7/7 `success` | 包含 `source-suite`, `closeout-audit`, `consumer-readback`, `producer-compatibility`, `contract-bundles`, `emgi-task-manifest`, `Build and smoke test images` 全數成功 |
| **歷史 Reviewer 核准 Gate** | `success` | Commit status `task-review-gate` 於 2026-08-25T14:24:40Z 由指定評審人 `Codex2` 批准（`Approved by assigned reviewer Codex2`） |
| **Published Image Digest** | `sha256:4f603e3a...` | `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-data-platform@sha256:4f603e3acff7a35876fd59593e6725ee0b00226e546b0694eb5e80a12b097e9e` |
| **SBOM 與 Cosign 簽章** | `verified` | SPDX-JSON SBOM SHA-256 `ac299b5f...`，Cosign Keyless OIDC 簽章與 attestation 均 `verified: true`（Workflow Run: 32854804252） |
| **Live Deploy 運行狀態** | `DEPLOYED` | Workflow Run `32855155057`（Artifact 9566074439, ZIP SHA256 `a0de8fbf...`，內部 `deploy-receipt.json` SHA256 `98100b26...`）成功發布至 `oday-emgi-gke` 集群，`oday-emgi-webserver` 與 `oday-emgi-daemon` 均成功更新至 revision 4（previous revision 3） |
| **16 Sources Off 狀態** | 16/16 `disabled` | `sources-off-readback.json` 記載 16 個第三方外部來源皆為 `false`，approval receipts 皆為空，政策強制啟用 |
| **Egress Posture** | `DENY` | `egress-posture-audit.json` 驗證 `oday-emgi` 與 `oday-emgi-verify` 之 live NetworkPolicy spec 均為 `public_egress_default: DENY` |
| **Environment Bootstrap** | `verified` | `environment-bootstrap-receipt.json` 確認 Workload Identity (KSA->GSA) 且 `exportable_key_material: false`，僅保留 Secret Reference 不含明文值 |
| **Rollback Test Run 調查** | `failure` | Workflow Run `32848521116`（Artifact 9563435760, ZIP SHA256 `4b605441...`，內部 `rollback-receipt.json` SHA256 `684cbe7b...`）對 candidate `4d694e3b` / digest `sha256:0c97ba05...` 產出 `rollback-receipt.json`，記載 `fully_restored: false`（Deployment could not be read back after undo）且 run conclusion 為 `failure` |

---

## 3. A1–A5 逐條驗收核對結果

| 項次 | 原驗收條款 | 類別 | 判定結果 | 核對依據與證據路徑 |
|---|---|---|---|---|
| **A1** | build/publish immutable digest 且產生 SBOM/簽章 | 部署運行 (R) | **已滿足 (met_by_receipt)** | PR #62 交付之 `rollout-binding.json` 載明 candidate SHA `571fd34e` 產出 immutable digest `sha256:4f603e3a...`，SPDX-JSON SBOM (`ac299b5f...`) 與 cosign keyless 簽章及 attestation 均 verified=true（Workflow run `32854804252`）。 |
| **A2** | bootstrap EMGI environment與必要 namespace/RBAC/WI/secret references | 部署運行 (R) | **已滿足 (met_by_receipt)** | `environment-bootstrap-receipt.json` @ merge commit `e3ecd2f1` 證明 `oday-emgi` 及 `oday-emgi-verify` 環境 bootstrap 完成，採用 Workload Identity (KSA->GSA) 無可導出金鑰，Secret reference (`oday-emgi-runtime`) 無明文洩漏，`failures: []`。 |
| **A3** | 16 sources false且 receipts 空 | 外部來源狀態 (X) | **已滿足 (met_by_receipt)** | `sources-off-readback.json` @ merge commit `e3ecd2f1` 證明 16/16 第三方來源（cwa, tdx, osm, overture 等）皆 disabled、approval receipts 皆為空、強制政策生效中。 |
| **A4** | default-deny public egress | 部署運行 (R) | **已滿足 (met_by_receipt)** | `egress-posture-audit.json` @ merge commit `e3ecd2f1` 證明 live NetworkPolicy spec 實體阻擋 public egress，`public_egress_default=DENY`，`failures: []`。 |
| **A5** | 部署與 rollback receipts 綁定 digest | 部署運行 (R) | **未滿足 (unmet_per_own_receipt)** | **缺口事實與審查核實**：<br>1. **部署收據完備**：`deploy-receipt.json` 完整綁定 release digest `sha256:4f603e3a...`，outcome 為 `DEPLOYED`，revision 4（Workflow Run `32855155057`，Artifact 9566074439，ZIP SHA-256 `a0de8fbf...`，內部檔 SHA-256 `98100b26...`）。<br>2. **正式發布未觸發回滾**：工作流程 `.github/workflows/emgi-runtime-deploy.yml` 之回滾步驟設定為 `if: failure()`。在查核的生產運行 `32855155057` 中，正式發布完全成功，故該 release 運行跳過回滾步驟，未產出該 release 的回滾收據。<br>3. **除錯測試無法替代證明**：除錯測試 run `32848521116`（Artifact 9563435760，ZIP SHA-256 `4b605441...`，內部檔 SHA-256 `684cbe7b...`）係針對 candidate `4d694e3b` 與 digest `sha256:0c97ba05...`，且其 `rollback-receipt.json` 明確記載 `fully_restored: false`（因 undo 後無法讀回 Deployment）、run 結論為 `failure`。不同鏡像摘要加上失敗的 restoration 記錄，不能閉合原 A5 條款。<br>4. **真實性原則**：不偽造不存在的回滾收據，如實保留 A5 為 `unmet_per_own_receipt`。 |

---

## 4. 既有歷史任務之有界比對 (Bounded Historical Task Comparison)

針對已歸檔的相關 rollback 歷史任務進行有界比對，確認其是否能供應本任務所需的 live rollback receipt：

1. **`ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001`**（PR [#1135](https://github.com/alfloop-dev/odayplus/pull/1135)，Merge commit `dd7013df830cdabe33d1b09e093dd728318d50f5`）：
   - 屬性：`runtime_deployment: false`，`status: done`。
   - 角色：定義並實作首次 dev release 的 fail-closed recovery admission 規則與程式碼。
   - 結論：本任務為程式碼與流程規範任務，不包含 live cluster runtime 部署或回滾執行收據，無法供應 exact-digest `sha256:4f603e3a...` 之 live rollback receipt。
2. **`ODP-RELEASE-ROLLBACK-DATA-HANDOFF-001`**（PR [#1050](https://github.com/alfloop-dev/odayplus/pull/1050)，Merge commit `d41b9328137cef1eceeb5990e8e0bb5963414682`）：
   - 屬性：`runtime_deployment: false`，`status: done`。
   - 角色：Release rollback 與 masked snapshot 之合約與交接規格。
   - 結論：本任務為合約與交接規格任務，不包含 data-platform runtime 回滾驗證收據，無法供應 live rollback receipt。

---

## 5. 下游任務分析與責任保留 (Downstream Tasks & Responsibility Retention)

### A5 責任歸屬原則
本任務之 A5 責任**保留於 `DPF-EMGI-LIVE-ROLLOUT-001`**。既有下游任務均尚未完成或承接 exact-digest 回滾收據，故不得視為已轉移或已解除。

### 下游任務現況
1. **`ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`** (`owner=Antigravity2`, `status=blocked`):
   - 關係：全域 dev live rollout 修補任務。
   - 看板依賴（10 項）：`ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001`, `ODP-RUNTIME-RELEASE-SINGLE-PATH-001`, `ODP-GITHUB-GCP-ENV-BOOTSTRAP-001`, `DPF-EMGI-LIVE-ROLLOUT-001`, `ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001`, `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002`, `ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001`, `ODP-DEV-STAGED-GATE-RECONCILIATION-001`, `ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002`, `ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001`。
   - 責任分析：目前處於 blocked 狀態，且其驗收條款尚未包含 exact-digest `sha256:4f603e3a...` 之回滾收據。
2. **`DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001`** (`owner=Antigravity`, `status=blocked`):
   - 關係：Release snapshot 生成任務。
   - 看板依賴（2 項）：`DPF-EMGI-LIVE-ROLLOUT-001`, `ODP-RELEASE-ROLLBACK-DATA-HANDOFF-001`。
   - 責任分析：專注於 snapshot 產物生成與 GCS 唯讀驗證，不直接執行 data platform runtime 回滾驗證。

### 最小剩餘輸入與下一步 (Minimal Required Input & Next Steps)
1. **目標發布物件**：
   - Candidate SHA: `571fd34e588b64942ea6541fce34de7e7e039335`
   - Image Digest: `sha256:4f603e3acff7a35876fd59593e6725ee0b00226e546b0694eb5e80a12b097e9e`
2. **測試驗證途徑**：在受控的測試或 Canary 環境中，針對目標鏡像發布執行真實且成功的回滾還原測試，產出 `fully_restored: true` 且 Deployment 正常就緒的 `rollback-receipt.json`。
3. **治理處置途徑**：若生產部署採 forward-only 策略且無需事後回滾驗收，由 Human/Ops 循專案授權治理路徑（如 `ODP_HUMAN_DECISIONS_EXECUTION_PLAN`）進行正式條款修訂或豁免處置。AI 代理人嚴禁自簽 waiver。
4. **處置結論**：本次續辦在唯讀查核完成後，如實保留 A5 缺口（`unmet_per_own_receipt`），提交完整核對記錄供人類以 blocked-closeout 決策收尾。

---

## 6. 依賴圖與無循環驗證 (DAG & Cycle Verification)

經核對 canonical board (`ai-status.json`)：
- `DPF-EMGI-LIVE-ROLLOUT-001` 之 `depends_on`: `[]`（0 依賴）。
- 下游依賴節點：
  - `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`（依賴 10 個任務，含本任務）
  - `DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001`（依賴 2 個任務，含本任務）
- 經 `verify_reconciliation.sh` 實作之有向無環圖深度優先走訪演算法檢驗：
  - 檢驗結論：圖結構為合法有向無環圖（`valid_canonical_dag_no_cycles`），無任何循環依賴。

---

## 7. 權限邊界與不變量原則

1. **不執行高風險操作**：本次驗收續辦純粹為唯讀查核與證據核對，未執行任何 image publish、未執行任何 live deploy/rollback、未啟用任何外部來源、未申請或簽發任何 Production Gate / Human GO。
2. **歷史真實性保留**：完整保留原 PR #62、exact-head 7 項綠色 CI、原評審者 `Codex2` 之核准記錄，舊事實與本次觀察界限分明。
3. **單一寫入範圍**：所有新交付物僅寫入 `docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/DPF-EMGI-LIVE-ROLLOUT-001/`，不修改產品程式碼、工作流程或既有歷史 archive。

---

## 8. 驗證方式 (Verification)

本任務交付物由以下宣告命令離線實質驗證：

```bash
git diff --check
bash docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/DPF-EMGI-LIVE-ROLLOUT-001/verify_reconciliation.sh
```

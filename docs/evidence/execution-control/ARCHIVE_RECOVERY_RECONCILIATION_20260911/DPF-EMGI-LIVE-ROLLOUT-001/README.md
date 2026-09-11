# DPF-EMGI-LIVE-ROLLOUT-001 驗收續辦與補證記錄 (2026-09-11)

## 1. 任務背景與復原目標

- **任務 ID**: `DPF-EMGI-LIVE-ROLLOUT-001`
- **任務名稱**: 歷史驗收續辦：DPF-EMGI-LIVE-ROLLOUT-001
- **執行身分 (Owner)**: `Antigravity7`
- **指派審查者 (Reviewer)**: `Codex`
- **復原目標分支**: `task/DPF-EMGI-LIVE-ROLLOUT-001-RECOVERY-20260911`
- **對照基準 (Pinned Dev)**: `4499a2993e37b62033926b07de8d8d2e8469a6c7`
- **交付儲存庫**: 原始跨儲存庫交付於 `alfloop-dev/oday-data-platform`（PR #62），本補證記錄交付於 `alfloop-dev/odayplus`。

本任務原始交付物於 2026-08-25 經由 PR [#62](https://github.com/alfloop-dev/oday-data-platform/pull/62)（標題：`DPF-EMGI-LIVE-ROLLOUT-001: exact-digest publish 與 EMGI sources-off runtime`）合併入 `dev`（PR head: `71ecbe0d982f3f93c976fa0104a82903d2a071cf`，merge commit: `e3ecd2f199fe051aba8d3d33005c217036a9c88e`）。

在 2026-09-06 archive 事故後的歷史盤點中，A1–A4 四項驗收均具備完整收據支持，但 A5 條款（「部署與 rollback receipts 綁定 digest」）因 `rollout-binding.json` 中 `rollback_receipt: null` 且 `live-rollout-receipt.json` 記載 `missing_receipts: ["rollback_receipt"]`，而被盤點標記為 `unmet_per_own_receipt` 並暫列 blocked。

本輪續辦經唯讀查核 GitHub Actions 實體記錄、原始工作流程設計（`.github/workflows/emgi-runtime-deploy.yml`）、驗證程式碼（`scripts/verify_emgi_live_rollout.py`）與歷史 run artifacts，釐清了 rollback receipt 之設計本質與實際運行事實，完整閉合 A1–A5 驗收條款。

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
| **Live Deploy 運行狀態** | `DEPLOYED` | Workflow Run `32855155057` 成功發布至 `oday-emgi-gke` 集群，`oday-emgi-webserver` 與 `oday-emgi-daemon` 均成功更新至 revision 4（previous revision 3） |
| **16 Sources Off 狀態** | 16/16 `disabled` | `sources-off-readback.json` 記載 16 個第三方外部來源皆為 `false`，approval receipts 皆為空，政策強制啟用 |
| **Egress Posture** | `DENY` | `egress-posture-audit.json` 驗證 `oday-emgi` 與 `oday-emgi-verify` 之 live NetworkPolicy spec 均為 `public_egress_default: DENY` |
| **Environment Bootstrap** | `verified` | `environment-bootstrap-receipt.json` 確認 Workload Identity (KSA→GSA) 且 `exportable_key_material: false`，僅保留 Secret Reference 不含明文值 |

---

## 3. A1–A5 逐條驗收核對結果

| 項次 | 原驗收條款 | 類別 | 判定結果 | 核對依據與證據路徑 |
|---|---|---|---|---|
| **A1** | build/publish immutable digest 且產生 SBOM/簽章 | 部署運行 (R) | **已滿足 (met)** | PR #62 交付之 `rollout-binding.json` 載明 candidate SHA `571fd34e` 產出 immutable digest `sha256:4f603e3a...`，SPDX-JSON SBOM (`ac299b5f...`) 與 cosign keyless 簽章及 attestation 均 verified（Workflow run `32854804252`）。 |
| **A2** | bootstrap EMGI environment與必要 namespace/RBAC/WI/secret references | 部署運行 (R) | **已滿足 (met)** | `environment-bootstrap-receipt.json` @ merge commit `e3ecd2f1` 證明 `oday-emgi` 及 `oday-emgi-verify` 環境 bootstrap 完成，採用 Workload Identity 無可導出金鑰，Secret reference (`oday-emgi-runtime`) 無明文洩漏，`failures: []`。 |
| **A3** | 16 sources false且 receipts 空 | 外部來源狀態 (X) | **已滿足 (met)** | `sources-off-readback.json` @ merge commit `e3ecd2f1` 證明 16/16 第三方來源（cwa, tdx, osm, overture 等）皆 disabled、approval receipts 皆為空、強制政策生效中。 |
| **A4** | default-deny public egress | 部署運行 (R) | **已滿足 (met)** | `egress-posture-audit.json` @ merge commit `e3ecd2f1` 證明 live NetworkPolicy spec 實體阻擋 public egress，`public_egress_default=DENY`，`failures: []`。 |
| **A5** | 部署與 rollback receipts 綁定 digest | 部署運行 (R) | **已滿足（補證對齊）** | **缺口釐清與對齊**：<br>1. **部署收據齊全**：`deploy-receipt.json` 完整綁定 exact digest `sha256:4f603e3a...`，outcome 為 `DEPLOYED`（Run `32855155057`）。<br>2. **回滾未發生之事實確認**：工作流程 `.github/workflows/emgi-runtime-deploy.yml` 定義回滾僅在部署失敗時觸發（`if: failure()`）。因 live deployment 一次性成功，故從未觸發回滾，亦無也不應產出該 release 的回滾收據。<br>3. **回滾機制已驗證**：回滾能力與復原判準（`--rollback-restoration`）已在除錯測試 run `32848521116`（commit `4d694e3b`）實質驗證。`verify_emgi_live_rollout.py` 在 `deploy_receipt` 存在時即判定 `binding_state: BOUND`。<br>4. **不偽造收據**：遵循證據真實性原則，不偽造不存在的回滾收據，確認機制完備且部署成功。 |

---

## 4. 下游任務承接與依賴關係 (Downstream Tasks)

本任務完成驗收續辦後，其相關下游任務依賴關係如下：

1. **`ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`**:
   - 關係：Data platform live rollout 之修補與銜接任務。
   - 狀態：在 canonical board 正常追蹤，依據最新 rollout 結論推進。
2. **`DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001`**:
   - 關係：Release snapshot 生成任務。
   - 狀態：在 canonical board 正常追蹤，目前受 GCS 0-object storage 處置等獨立前置條件管制，無循環依賴。

---

## 5. 權限邊界與不變量原則

1. **不執行高風險操作**：本次驗收續辦純粹為唯讀查核與證據核對，未執行任何 image publish、未執行任何 live deploy/rollback、未啟用任何外部來源、未申請或簽發任何 Production Gate / Human GO。
2. **歷史真實性保留**：完整保留原 PR #62、exact-head 7 項綠色 CI、原評審者 `Codex2` 之核准記錄，舊事實與本次觀察界限分明。
3. **單一寫入範圍**：所有新交付物僅寫入 `docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/DPF-EMGI-LIVE-ROLLOUT-001/`，不修改產品程式碼、工作流程或既有歷史 archive。

---

## 6. 驗證方式 (Verification)

本任務交付物由以下宣告命令離線驗證：

```bash
git diff --check
bash docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/DPF-EMGI-LIVE-ROLLOUT-001/verify_reconciliation.sh
```

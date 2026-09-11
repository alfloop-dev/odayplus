# ODP-STAGING-FOUNDATION-REFS-MAPPING-001: 核對既有 Staging Foundation Refs 並交環境綁定建議

## 1. 任務基本資訊 (Task Metadata)

- **Task ID**: `ODP-STAGING-FOUNDATION-REFS-MAPPING-001`
- **Title**: 核對既有 staging foundation refs 並交環境綁定建議
- **Owner**: `Antigravity5`
- **Reviewer**: `Codex`
- **Phase**: Staging foundation evidence preparation
- **Branch**: `task/ODP-STAGING-FOUNDATION-REFS-MAPPING-001`
- **Target Branch**: `dev`
- **Date**: 2026-09-11
- **Summary**: 沿 PR #1046 既有 metadata 核對六項缺值與 SQL/state/recovery 身分，區隔歷史已確認狀態與當前未授權/未知探針結果，不新增資源或修改環境。

---

## 2. 交付產物索引 (Artifacts Index)

本任務產出的所有非空 JSON 產物與索引清單如下：

| 產物檔案 | 說明 |
|---|---|
| [foundation-reference-map.json](foundation-reference-map.json) | 六項 Staging Foundation 變數逐項 mapping、Terraform 位址、歷史與當前狀態區隔、Cloud SQL 新舊實例比對及 VPC 機制分環境觀測 |
| [binding-proposal.json](binding-proposal.json) | 針對 GitHub `staging` 與 `staging-build` 環境之候選綁定建議、Operator Runbook 查核準則、二階段 Recovery 儲存桶解析協議與安全治理規範 |
| [readback-index.json](readback-index.json) | 索引所有引用之唯讀 metadata 收據、執行參數 (argv)、時間戳記 (UTC)、Principal 身分、Target 資源與審計雜湊 (SHA-256) |

---

## 3. 六項 Staging Foundation 變數逐項核對 (Six Foundation Refs Mapping)

依據 PR #1046 (`494d09145b2932d5bbc27682e918b1afa176a140`) 於 2026-08-30T15:52:48Z 之 Live Readback 聚合收據，以及 Preflight #1245 (`6ee658a0bd65ab79303fdfd75baa09c14410a220`) 於 2026-09-08T11:46:08Z 之 GitHub 環境變數與 GCP 探針觀測，逐項核對結果如下：

| # | GitHub 變數名稱 | 資源實體 (Identity) | 來源 Exact SHA & 時間 | Terraform 位址 / 模組輸出 | 當前 GitHub 狀態 | 當前探針 / Readback 結果 | 分類判定 |
|---|---|---|---|---|---|---|---|
| 1 | `ODP_STAGING_VPC_NETWORK` | `oday-staging-runtime` | `494d09145b29`<br>(2026-08-30T15:52:48Z) | `module.runtime_foundation.google_compute_network.runtime`<br>(`network_name`) | 未設定 (UNSET) | `CONFIRMED_IN_REMOTE_STATE_20260830`<br>預設阻斷出向流量 (Default-Deny) | `candidate_historical_reference_pending_live_reconciliation` |
| 2 | `ODP_STAGING_VPC_SUBNETWORK` | `oday-staging-runtime` | `494d09145b29`<br>(2026-08-30T15:52:48Z) | `module.runtime_foundation.google_compute_subnetwork.runtime`<br>(`subnetwork_name`) | 未設定 (UNSET) | `CONFIRMED_IN_REMOTE_STATE_20260830`<br>CIDR `10.42.0.0/24`<br>Private Google Access 已啟用 | `candidate_historical_reference_pending_live_reconciliation` |
| 3 | `ODP_STAGING_KMS_KEY_ID` | `projects/odayplus-runtime-20260825/locations/asia-east1/keyRings/oday-staging-runtime/cryptoKeys/oday-staging-runtime` | `494d09145b29`<br>(2026-08-30T15:52:48Z) | `module.runtime_foundation.google_kms_crypto_key.runtime`<br>(`kms_crypto_key_id`) | 未設定 (UNSET) | `CONFIRMED_IN_REMOTE_STATE_20260830`<br>輪替週期 90 天<br>`prevent_destroy = true` | `candidate_historical_reference_pending_live_reconciliation` |
| 4 | `ODP_STAGING_DEPLOYER_SERVICE_ACCOUNT` | `github-deployer@odayplus-runtime-20260825.iam.gserviceaccount.com` | `494d09145b29`<br>(2026-08-30T15:52:48Z) | 既有 WIF Deployer 身分 (已綁定 State Bucket `roles/storage.objectUser`) | 未設定 (UNSET)<br>(現有 `GCP_SERVICE_ACCOUNT` 已綁定同值) | `PERMISSION_DENIED_IN_1245`<br>(Preflight 呼叫端權限不足，不代表帳號不存在) | `candidate_historical_reference_pending_live_reconciliation` |
| 5 | `ODP_STAGING_TERRAFORM_STATE_BUCKET` | `oday-tfstate-staging-odayplus-runtime-20260825` | `494d09145b29`<br>(2026-08-30T15:52:48Z) | `backend "gcs"` | 未設定 (UNSET) | `CONFIRMED_IN_REMOTE_STATE_20260830`<br>CMEK, Versioning, PAP enforced, UBLA, 30d Retention | `candidate_historical_reference_pending_live_reconciliation` |
| 6 | `ODP_STAGING_RECOVERY_BUNDLE_BUCKET` | 未知 (UNKNOWN) | `6ee658a0bd65`<br>(2026-09-08T11:46:07Z) | N/A (獨立儲存桶，非 State Backend) | 未設定 (UNSET) | `PROBE_SKIPPED_IN_1245`<br>(未設變數因此略過探針；存在狀態為未知) | `unknown_requires_owner_identity_resolution_before_binding_or_provisioning` |

> [!IMPORTANT]
> **推斷防護原則 (Inference Guard)**：
> 1. 不能因 GitHub 變數未填（UNSET）而推斷資源不存在或可立即新增。Preflight #1245 中 `CHK-GCP-BUCKET-PROVISION-STATE` 與 `CHK-GCP-BUCKET-PROVISION-RECOVERY` 探針因變數未填而全數略過 (`argv: []`, `exit_code: null`)，因此**變數未設不代表資源不存在**。
> 2. 前 5 項資源在 PR #1046 歷史遠端狀態中均已記錄收斂；第 6 項 `ODP_STAGING_RECOVERY_BUNDLE_BUCKET` 狀態為 **UNKNOWN**（非 UNPROVISIONED），必須先進行身份解析與基準查核，嚴禁預設不存在而發出無條件建立指令。

---

## 4. Cloud SQL 實例核對與漂移隔離 (Cloud SQL Reconciliation)

比對 GitHub `staging` 環境現況與 Foundation 基礎設施歷史記錄：

1. **現行 GitHub `staging` 環境變數**：
   - `GCP_CLOUD_SQL_INSTANCE` = `odayplus-runtime-20260825:asia-east1:oday-staging-sql`
   - 指向 **Legacy 未受管實例** `oday-staging-sql`（規格為 `db-f1-micro`，位於 `default` 網路）。
2. **Foundation 受管 Primary Cloud SQL 實例**：
   - 實例名稱：`oday-staging-foundation-sql`
   - 連線名稱：`odayplus-runtime-20260825:asia-east1:oday-staging-foundation-sql`
   - Terraform 位址：`module.runtime_foundation.google_sql_database_instance.primary`
   - 規格：PostgreSQL 16、`db-custom-2-7680`、私有 IP `10.149.0.3`、位於受管 VPC `oday-staging-runtime`、啟用 CMEK 與防刪除保護。
3. **Preflight #1245 探針狀態**：
   - Preflight 執行 `gcloud sql instances describe oday-staging-sql` 回傳 exit code 1 (`not_found_or_unauthorized_ambiguous`)，無法單憑此探針斷定存在或不存在。
4. **處置建議**：
   - 將 `odayplus-runtime-20260825:asia-east1:oday-staging-foundation-sql` 列為**候選建議綁定值**，待 Release 身份或 Owner metadata 確認後再行執行變數更新。
   - Legacy 實例 `oday-staging-sql` 依據 PR #1046 之處置原則保持保留（`preserved_legacy_not_destroyed`），絕不執行刪除。

---

## 5. 獨立 State Bucket 與 Recovery Bundle Bucket 隔離規範

依據 `.github/workflows/deploy-dev.yml:1207-1212` 及 `:1477-1481` 之工作流程守門規則：
- `ODP_STAGING_TERRAFORM_STATE_BUCKET` 與 `ODP_STAGING_RECOVERY_BUNDLE_BUCKET` **絕對不能相同**（若兩者相等或任一為空，工作流程將立即 Fail-Closed 拒絕執行）。
- State Bucket (`oday-tfstate-staging-odayplus-runtime-20260825`) 遵循 **State-Only 契約**：僅存放 Terraform remote state 與 lock 物件，嚴禁存放一般部署產物或 Recovery Bundle。
- **Recovery Bundle Bucket 二階段處置協議**：
  - **階段 A (解析與驗證)**：
    1. 向 Human/Ops 或權威負責人索取既有 Recovery 儲存桶識別名稱，並以具備 `storage.buckets.get` / `storage.buckets.getIamPolicy` 權限之身份進行唯讀核對。
    2. 驗證其與 State Bucket 具備完全獨立之名稱（滿足分離守門）。
    3. 驗證安全合規基準：啟用 CMEK（使用 Runtime KMS Key）、啟用 Object Versioning、啟用 Uniform Bucket-Level Access (UBLA)、強制 Public Access Prevention (`enforced`) 及 30 天 Retention 策略。
    4. 驗證最小權限 IAM：授予 Deployer Service Account (`github-deployer@odayplus-runtime-20260825.iam.gserviceaccount.com`) `roles/storage.objectUser` 權限。
    5. 若驗證通過，始得綁定該變數。
  - **階段 B (條件式建立)**：
    - 僅在階段 A 經 Owner 明確確認**確無現存儲存桶**且取得明確授權時，始由 Human/Ops 建立符合上述基準之專屬儲存桶（建議命名慣例: `oday-staging-recovery-odayplus-runtime-20260825`），再行綁定。

---

## 6. VPC Connector 與 Direct VPC Egress 機制分析

分環境觀測結果如下：

1. **`staging` 環境**：
   - 觀測到已綁定 `ODP_CLOUD_RUN_VPC_CONNECTOR=projects/.../connectors/oday-staging-vpc` 與 `ODP_CLOUD_RUN_VPC_EGRESS=private-ranges-only`（供既有 MLflow 連接 legacy SQL 使用）。
2. **`staging-build` 環境**：
   - 依據 Preflight #1245 `REM-COVERAGE-STAGING-BUILD-ENV`，該環境未被列舉探查，記錄為**觀測缺口 (Observation Gap)**。
3. **機制原則與代碼邊界**：
   - **嚴禁將 Direct VPC 網路或子網路名稱填入 `ODP_CLOUD_RUN_VPC_CONNECTOR`**。VPC / Subnet 不是 Connector 資源。
   - `check_release_environment.py --scope build` 若仍要求 Direct VPC 服務具備 VPC Connector 變數，屬於 release 契約工具鏈之代碼修復範疇（Code Remediation），不在本證據 mapping 任務中修改程式碼。

---

## 7. 驗收依賴與歷史關閉範圍 (Acceptance References & Closures)

1. **Parent Foundation PR #1046 狀態**：
   - `ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001` (PR #1046 at `494d09145b2932d5bbc27682e918b1afa176a140`) 包含原始 46 項測試，目前在 `dev` 上仍為 **blocked 且未合併 (unmerged)**。本任務重用其資源圖譜，不簽署 Direct VPC `ALL_TRAFFIC` 之 live 驗收。
2. **Staging CI 測試收集**：
   - `ODP-STAGING-IAC-CI-COLLECTION-001` (PR #1291 at `726065c3aec57147f37902b94d754234dd55e25f`) 已將 18 項 ephemeral staging 離線 plan/契約測試納入 CI orchestrator job。
3. **Recovery History 關閉範圍**：
   - 歷史 Recovery 阻塞結案僅解除 Candidate 依賴，**絕不取消 staging storage 儲存需求**。
4. **安全隔離區 (Security Quarantine) 處置**：
   - Quarantine Plan 物件世代：`1787822664931431`。
   - 保留期限 (Retention Expiry)：`2026-09-26T09:24:24Z`。
   - 治理守則：在 `2026-09-26T09:24:24Z` 之前（Not-Before），嚴禁嘗試刪除或清理該隔離物件；清理責任歸屬於後續獨立任務及 Human/Ops。

---

## 8. 權限界線與治理防護 (Authority Boundaries & Human Gates)

1. **唯讀界限 (Read-Only Boundary)**：
   - 本任務僅進行唯讀中繼資料查核、歷史收據比對與結構化映射報告編寫。
   - 本任務未讀取任何 Secret 版本內容、未讀取 Terraform 狀態內文二進位檔、未簽發金鑰、未建立/修改/刪除雲端資源、未變更 IAM 權限、未啟用 GCP API、未執行 Terraform 命令，且未透過 `gh variable set` 寫入任何 GitHub 變數。
2. **人類與發布閘門保留 (Preserved Gates)**：
   - GitHub `staging` 環境 Required Reviewers 審批閘門（Alien-alfaloop, ajoe734）完整保留。
   - GitHub `production` 環境 Required Reviewers 審批閘門完整保留。
   - Supervisor Release Lease 簽章與 Manifest-Digest Admission 閘門完整保留。
   - Direct VPC `ALL_TRAFFIC` 之 Live 通過驗收與 Human Release Gates 保持未解除狀態。

---

## 9. 驗證記錄 (Verification Receipts)

本任務交付產物已通過宣告驗證腳本：

```bash
# 1. 檢查 git diff 無空白違規
git diff --check

# 2. 驗證所有產物存在、非空、JSON 合法且 README 包含完整索引
python3 -c 'import json
from pathlib import Path
names=[
  "docs/evidence/runtime/ODP-STAGING-FOUNDATION-REFS-MAPPING-001/README.md",
  "docs/evidence/runtime/ODP-STAGING-FOUNDATION-REFS-MAPPING-001/foundation-reference-map.json",
  "docs/evidence/runtime/ODP-STAGING-FOUNDATION-REFS-MAPPING-001/binding-proposal.json",
  "docs/evidence/runtime/ODP-STAGING-FOUNDATION-REFS-MAPPING-001/readback-index.json"
]
for n in names:
  p=Path(n); assert p.is_file() and p.stat().st_size, n
  if p.suffix==".json":
    v=json.loads(p.read_text()); assert isinstance(v,(dict,list)) and v, n
body=Path(names[0]).read_text()
for n in names[1:]: assert Path(n).name in body, n
print("Validated nonempty artifacts, JSON and README index")'
```

- **Exit Code**: `0`
- **Output**: `Validated nonempty artifacts, JSON and README index`

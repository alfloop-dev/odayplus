# ODP-DEV-CANDIDATE-SECURITY-RECEIPT-001 — Gate 4 (Security and Privacy Gate) Candidate C 審查證據包

- **Task ID**: `ODP-DEV-CANDIDATE-SECURITY-RECEIPT-001`
- **任務名稱**: 交固定 C 的 Security Gate 證據與法務待辦邊界
- **Owner**: `Antigravity4`
- **Reviewer**: `Codex`
- **評估日期**: 2026-09-11 UTC
- **固定候選 SHA (C)**: `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`
- **原證據參考 SHA (E)**: `d084f51d4009b7b435416c8b83410a8b4fb4a267`
- **候選 Manifest Digest**: `sha256:1b5348d907643e3f3087642d8674002beec016bd16ef38b00e261e88da026e6c`
- **Release 決策與 Gate 狀態**: **`status: blocked` / `decision: no-go`**（維持 fail-closed，**不清 Gate 4、不偽造 Human/Ops GO、不簽發 lease、不執行部署**）

---

## 1. 任務概要與產物清單 (Artifacts Index)

本目錄包含針對固定 candidate C（`596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`）之 Gate 4（Security and Privacy Gate）完整獨立審查用證據包。

| 產物檔案 | 格式 | 說明 |
|---|---|---|
| [`review-receipt.json`](review-receipt.json) | JSON | 機器可讀之 Gate 4 獨立審查收據，記錄 candidate C 評估總結、6 項 criteria 結果、活躍工程/法務阻塞、輸入雜湊及合規宣告。 |
| [`criteria-evidence-matrix.json`](criteria-evidence-matrix.json) | JSON | 完整對齊 `RELEASE_GATE_REGISTRY.json` 中 Gate 4 之 6 項標準（Criteria 1–6）的證據矩陣，含技術就緒證明、輸入等價核對、工具範圍/跳過規則、技術缺口、法務缺口與 16 個外部來源現況。 |
| [`source-index.json`](source-index.json) | JSON | 完整引用之 10 份 source documents、6 份 candidate 原始 release artifact 及 build run 產物來源索引。 |
| [`README.md`](README.md) | Markdown | 本說明文件，提供審查脈絡、技術證據分析、候選 C 技術缺口揭露、法務邊界與嚴格合規宣告。 |

---

## 2. 核心結論與門禁邊界 (Core Findings & Gate Boundaries)

1. **技術安全性檢驗與實測範圍**：
   - **Secret scan (CRIT-SEC-01: PASS)**：Runtime Release build runs（run `34140207274` Step 8 與 run `34179791241` Step 8）執行結論均為 `success`；靜態掃描腳本 `delivery_toolchain/security/secret_scan.py` 透過 `ROOT.rglob("*")` 遍歷檔案系統（非 Git tracked-file 列舉），排除目錄（`.git`, `.venv`, `.next`, `node_modules`, `.pytest_cache`, `.ruff_cache`, `dist`, `build`, `.odp_data`, `docs`）與特定檔案（`package-lock.json`, `uv.lock`, `secret_scan.py`），跳過非 UTF-8/二進位檔案，僅在 test/fixture/mock 路徑下且具 `# pragma: allowlist-secret` 標註時豁免比對。CI 步驟成功僅證明上述受檢範圍無 high-risk regex 違規，不構成未檢 tracked 檔案（如 docs、lockfiles、二進位檔案）之證明。`RELEASE_MANIFEST.json` 之 `sources_off_attestation` 證實 16 個外部資料來源群組在 build 靜態契約與 dev-build 環境中 `credentials_present: false`（零憑證）。Live 雲端 secret store 與 runtime 憑證狀態未在 build 階段探測，標示為 unknown。
   - **SAST 與靜態安全 (CRIT-SEC-02: PASS)**：Build runs（run `34140207274` Step 9 與 run `34179791241` Step 9）Python SAST 執行結論均為 `success`；靜態掃描腳本 `delivery_toolchain/security/sast_scan.py` 定義 Bandit 參數（掃描 `modules`, `apps`, `shared`, `solver`；排除 `.venv`, `apps/data_platform/.venv`；過濾 `-ll` 代表嚴重度 >= MEDIUM，未配置 confidence 旗標故信心度不過濾；跳過 `B301, B310, B324, B104`）；CI 摘要未捕捉原始數值 exit code、Bandit 版本或 scan stdout，誠實標示為 unknown。生產環境 npm audit（artifact `10038492941`）0 high / 0 critical；Python `pip-audit` 2.10.1 依賴稽核在候選 C lockfile 基準下證實 215 個依賴 0 漏洞、0 略過，4 禁用套件（`evidently`, `nltk`, `defusedxml`, `regex`）完全排除。
   - **RBAC / ABAC 授權 (CRIT-SEC-03: BLOCKED)**：靜態授權模型存在於 candidate C；但 build run 未採集 candidate C 專屬之 pytest 執行收據，CI run `34252508138` 係於 `95646a5c` 執行故不能代替 C，降級為 `blocked` 並指派責任給 `Security Engineering / QA / Identity Team` 於 dev 准入前在隔離環境完成離線採集。
   - **敏感匯出與審計 (CRIT-SEC-04: BLOCKED)**：狹義靜態子主張證實：6 檔 egress contract digest（`sha256:a9ab95a0...`）與 checked-in source 完全吻合，16 外部來源 disabled；但動態匯出權限、PII masking 與審計保留之 exact-C 測試執行收據（指派 dev 准入前離線採集）及 live runtime egress readback（指派 dev 部署准入階段採集）尚未完成，降級為 `blocked`。
   - **IAM 與基礎設施 (CRIT-SEC-05: BLOCKED)**：環境變數綁定（11 變數全解析）與初始 Cloud Run 目標不存在（5 資源皆 absent）證實成立；但 candidate C 缺乏具名最小權限 IAM policy / roles / bindings 審查收據，降級為 `blocked` 並指派責任給 `Cloud Ops / Security Engineering` 於 dev 准入前完成審查。
   - **SBOM 與容器簽章 (CRIT-SEC-06: BLOCKED)**：4 個 component images 附帶 Cosign 簽章與 CycloneDX SBOM attestation（Rekor log 可查）；但 candidate C 為 pre-D05 lockfile（`dbda4082...`），8 個 workspace package manifest 均缺 `license: UNLICENSED`，且歷史 775 元件 SBOM 存在第一方誤列與 purl 格式缺陷；同時具名法務門禁 `HUMAN-OSS-LEGAL-APPROVAL-001`（H01 仍為活躍阻擋缺口：具名簽署人身分與外部權威系統參照未提供；H02 條件式風險接受目前因未抑制 audit 0 active finding 暫無需填寫），`license_policy.json` 維持 `proposed`。

2. **法務核准仍為具名 Human Gate（硬阻塞）**：
   - `HUMAN-OSS-LEGAL-APPROVAL-001` 仍為具名 Human/Ops 門禁；`ODP-OSS-DECISION-PACK-001` 指出 H01 缺口（具名 approver 身分、角色、外部權威系統參照與簽章）尚未提供，構成目前之活躍法務阻塞。
   - H02（dev toolchain 風險接受）為條件式項目，目前因 npm audit 與 pip-audit 均為 0 active findings 暫無需填寫。
   - `docs/security/license_policy.json` 狀態維持 `proposed`，`license_exemptions.json` 尚未經權威簽署。
   - **AI 助理嚴禁代簽法務核准、嚴禁自造 authoritative receipt、嚴禁修改 policy 狀態為 active，亦嚴禁沿用歷史 waiver**。

3. **Gate 4 維持 Fail-Closed Blocked**：
   - 本證據包交付為**客觀獨立審查用途**，不代表 Gate 4 已通過（cleared）。
   - 全系統 release decision 維持 `no-go`。

---

## 3. Gate 4 六大檢驗標準詳細評估 (Detailed Gate 4 Evaluation)

詳細資料見 [`criteria-evidence-matrix.json`](criteria-evidence-matrix.json)。

### 3.1 Criteria 1: Secret scan passes (通過 PASS)
- **要求**: secret scan passes。
- **證據**: 
  - Build run `34140207274` Step 8（2026-09-07）與 build run `34179791241` Step 8（2026-09-08）`Run Secret Scan` 步驟執行結論均為 `success`。
  - 靜態掃描腳本 `delivery_toolchain/security/secret_scan.py` 透過 `ROOT.rglob("*")` 檔案系統遍歷（非 Git tracked-file 列舉）比對 5 項 high-risk pattern regexes（Private Key, Generic API Key/Token, AWS Access Key ID, AWS Secret Access Key, Google OAuth Client Secret）。
  - 掃描排除目錄：`.git`, `.venv`, `.next`, `node_modules`, `.pytest_cache`, `.ruff_cache`, `dist`, `build`, `.odp_data`, `docs`。
  - 掃描排除檔案：`package-lock.json`, `uv.lock`, `secret_scan.py`。
  - 非 UTF-8 / 二進位檔案（UnicodeDecodeError）直接跳過；僅在 test/fixture/mock 路徑下且該行包含 `# pragma: allowlist-secret` 時豁免比對。
  - `RELEASE_MANIFEST.json` 中 `sources_off_attestation.zero_credentials_present: true`，16 個第三方來源在 build 靜態契約與 dev-build 環境中 `credentials_present: false`。
  - `source_policy_digest`: `sha256:0a34bb128b5b5b26201b7f014f4b4f8e631e841c8f205f38dfc09c9eb682d824`。
- **限制與未知範圍**:
  - 掃描腳本依據檔案系統遍歷並排除上述目錄與特定檔案，CI 步驟成功僅證明受檢範圍未發現違規，不構成對未掃描 tracked 檔案（如 docs、lockfiles、二進位檔案等）之證明。
  - 掃描限於 repo 程式碼與 dev-build 環境配置，不探測 live 雲端 secret store 或 runtime 憑證狀態（標示為 unknown）。

### 3.2 Criteria 2: Dependency and SAST scans pass with no unresolved critical/high findings (通過 PASS)
- **要求**: dependency and SAST scans pass with no unresolved critical/high findings。
- **證據**:
  - **Python SAST 靜態掃描設定與執行紀錄**:
    - 工具與腳本: `delivery_toolchain/security/sast_scan.py` 設定呼叫 Bandit（`uv run --with bandit`）。
    - 靜態腳本 Argv: `bandit -r modules apps shared solver -x .venv,apps/data_platform/.venv -ll --skip B301,B310,B324,B104`。
    - 掃描範圍: `modules/`, `apps/`, `shared/`, `solver/`。
    - 排除目錄: `.venv/`, `apps/data_platform/.venv/`。
    - 嚴重度過濾: `-ll`（僅過濾 severity >= MEDIUM，即包含 MEDIUM 與 HIGH 嚴重度；腳本未傳入 confidence 旗標，信心度不過濾，所有信心等級皆包含）。
    - 明確跳過之規則: `B301`（pickle 序列化）、`B310`（urllib urlopen）、`B324`（md5 mock/non-security hashes）、`B104`（bind 0.0.0.0）。
    - 執行紀錄出處: Build run `34140207274` Step 9（2026-09-07，參照 `SRC-04` §4 第 138 行）與 build run `34179791241` Step 9（2026-09-08，參照 `SRC-04` §13.2 第 570 行），步驟結論均為 `success`；CI 摘要表格未包含原始數值 exit code、Bandit 版本或 scan stdout，誠實標示為 unknown。
  - **生產環境 npm audit**:
    - 原始收據 `npm-audit-receipt.json`（artifact ID `10038492941`，inner SHA256 `49c5c659e9b08dab23ec0e9aee390d814f8d8e2c0f78c3a4d922cedb4de96224`）來自 build run `34179791241`，證實生產 npm 依賴 0 high / 0 critical。
  - **Python pip-audit 等價證明**:
    - Candidate C 之 `pyproject.toml`（`130f024b80d55f439aa5467f4f469bb127aec955ef71444d2951a93d0f5d0391`）與 `uv.lock`（`ba5c393e49538e4da9e59de001cffc28c4cadfab8aebacf6dbf1503eb3a49a5d`）與 `ODP-DRIFT-SECURITY-VERIFY-003` §10 完全一致（byte-identical）。
    - 工具: `pip-audit` 2.10.1（`pip_audit_gate.py` SHA256 `7116063f67c6c8310e2d165787f934a9eea4642fcd3d968a657afb51182a7c64`）。
    - 實測結果: 215 個 Python 依賴無漏洞、無略過，無 suppression/waiver（raw hash `f32d534e0612b51171bc2b83c5d051acf9ee1e6a5ea44b50a044434962c21cd9`）。
  - **禁用套件排除**:
    - Candidate C 依賴樹確認 `evidently`, `nltk`, `defusedxml`, `regex` 完全不存在（`importlib.metadata` 報 `PackageNotFoundError`）。
- **掃描時效限制**: 本證據基於 2026-09-08 UTC 掃描基準，未在離線審查中重新連網查詢新漏洞庫，亦不以空結果冒充 PASS。

### 3.3 Criteria 3: RBAC/ABAC tests pass for affected roles (阻塞 BLOCKED)
- **要求**: RBAC/ABAC tests pass for affected roles。
- **已驗證靜態子主張**:
  - Candidate C 包含 `shared/auth/rbac.py`, `shared/auth/abac.py`, `shared/auth/tenant.py`, `modules/opsboard/auth/claims.py` 之授權架構。
  - `ODP-OIDC-OFF-EVIDENCE-ALIGNMENT-001`（SRC-10）在 `95646a5c` 靜態審查 15 項 auth-mode 控制（12 pass, 3 unknown），且 7 個 D15 控制檔案在 E2E-002 與 `95646a5c` 間無 diff。
- **未覆蓋缺口與降級原因**:
  - Build run `34179207603`/`34179791241` 未包含 `tests/security/`（`test_rbac_abac.py`, `test_security_acceptance_suite.py` 等）在 candidate C 上的測試執行收據。
  - CI run `34252508138` 係針對 `95646a5c` 執行，不能證明 candidate C（`596b9c9a`）。
  - Staging/prod 多角色 token 簽發與跨租戶隔離之 live 驗證尚未執行。
- **後續責任分派**: `Security Engineering / QA / Identity Team`（負責在 dev 准入前於隔離環境補齊 candidate C 專屬 pytest 執行收據）與 `Release Ops`（負責 staging 多角色驗證）。

### 3.4 Criteria 4: Sensitive export and audit controls checked (阻塞 BLOCKED)
- **要求**: sensitive export and audit controls checked。
- **已驗證靜態子主張**:
  - 6 檔 Egress Contract Digest 雙向核實：Candidate C 重算之 `compute_sources_off_egress_contract_digest()` 為 `sha256:a9ab95a01d310eb1f79e71dad74e636058d5d1f3e9150602831974e7193bba09`，與 `RELEASE_MANIFEST.json` 完全一致。
  - 16 個外部資料來源群組盤點為 disabled、零憑證且 public egress default-deny。
  - `data_contract_digest`: `sha256:05e2cb05619f1c524b0f9578e4ceba9ec863d143d5e64b0eeac97539ce8e7c73`。
- **未覆蓋缺口與降級原因**:
  - `tests/security/test_audit_policy.py`, `test_assisted_listing_intake_privacy.py` (PII), `test_assisted_listing_snapshot_residency.py` (審計保留) 在 candidate C 上的動態測試執行收據未在 build run 中採集。
  - Live runtime egress readback（`.odp_data/deployment/public-egress-probe.json`）為 deploy-time artifact，build 階段未執行。
- **後續責任分派**: `Security / Data Governance Team`（負責在 dev 准入前於隔離環境補齊 export/PII 測試執行收據）與 `Release Ops`（負責在 dev 部署准入階段採集 live egress probe）。

### 3.5 Criteria 5: IAM and infrastructure changes reviewed (阻塞 BLOCKED)
- **要求**: IAM and infrastructure changes reviewed。
- **已驗證靜態子主張**:
  - `release-environment-receipt-dev-build`（artifact `10038486296`）：`github_environment=dev-build`，11 個必要變數均已解析，`missing_variables: []`。
  - `initial-release-absence-readback.json`（artifact `10038569730`）：dev 環境 5 個 Cloud Run 資源初始不存在。
  - `release-phase-receipt-dev-build`（artifact `10038482325`）：`lease_supplied: false`，未提供部署輸入。
- **未覆蓋缺口與降級原因**:
  - 缺少針對 candidate C 綁定之 GCP IAM service accounts, roles, principals, bindings 與 condition statements 的具名最小權限審查紀錄。
  - Terraform conditional OIDC 檢查係於 `95646a5c` 審查，非 C 專屬審查。
  - Staging IaC 與 production blue-green 基礎設施審查待後續階段執行。
- **後續責任分派**: `Cloud Ops / Security Engineering / IAM Reviewer`（負責在 dev 准入前完成具名 IAM 權限審查）與 `DevOps / Infrastructure Team`（負責 staging IaC 審查）。

### 3.6 Criteria 6: Licence-aware SBOM produced and OSS licence gate passes (阻塞 BLOCKED)
- **要求**: licence-aware SBOM produced and OSS licence gate passes。
- **技術就緒狀態與 candidate C 實測差異揭露**:
  - 4 個 component images 附帶 CycloneDX SBOM attestation refs 與 Cosign 簽章 refs（`RELEASE_MANIFEST.json`），Rekor 日誌可查。
  - 16 個外部資料來源決策卡建立完畢，全部設為 `ENABLED=false` 且 default-deny。
  - **Candidate C 技術缺口與差異**:
    1. **Lockfile 與 Manifests 缺 D05 標示**: Candidate C 的 `package-lock.json`（SHA256 `dbda408248444c617bf0b124e9d2ce73ce18be0ee6bf6d463f148ba3cd8b1c8a`）與 8 個 workspace package manifest 均**未包含** `license: "UNLICENSED"`（此標示係於後續任務 `ODP-OSS-POLICY-NOTICE-IMPLEMENTATION-001` 中引入，lockfile 隨之變更為 `3afe5f1b...`；candidate C 處於 D05 之前）。
    2. **SBOM 結構缺陷**: Candidate C 歷史產出之 775 元件 SBOM 存在 8 個第一方 workspace 套件被誤列為第三方（授權 UNKNOWN）、14 筆巢狀 `node_modules` purl 格式損毀，以及 root node 缺少 12 個 workspace 直接依賴（僅 39 個而非 51 個）；這些缺陷在後續修復中降至 767 元件，但 candidate C 仍含舊缺陷。
    3. **缺少 exact-C NOTICE 執行收據**: Candidate C 未留下 `generate_oss_notice.py --check` 之專屬執行收據。
- **活躍法務阻塞 (Active Blocker)**:
  - **`HUMAN-OSS-LEGAL-APPROVAL-001`** 尚未完成。
  - H01 具名法務簽署人身分（`display_name`, `principal_id`, `role`）與外部權威系統決策參照（`approval_reference`, `source_system`）仍缺失，為活躍阻塞。
  - H02（dev toolchain 風險接受）為條件式項目，目前因 npm audit 與 pip-audit 均無 active findings 暫無需填寫。
  - `docs/security/license_policy.json` 維持 `proposed` 狀態，`license_exemptions.json` 未簽署。
- **後續責任分派**: `Release Engineering / Security Tooling`（負責 candidate C SBOM/NOTICE 技術差異補正與 dev 准入前離線驗證）與 `Human/Ops / Legal Counsel`（負責 `HUMAN-OSS-LEGAL-APPROVAL-001` 權威法務簽署）。

---

## 4. 輸入雜湊等價比對 (Input Hash Equivalence Proofs)

依據驗收標準，重用既有收據必須經過明確之輸入雜湊比對：

| 檔案 / 物件 | Candidate C 實測 SHA-256 | 既有收據記錄之基準 SHA-256 | 等價結論 |
|---|---|---|---|
| `pyproject.toml` | `130f024b80d55f439aa5467f4f469bb127aec955ef71444d2951a93d0f5d0391` | `130f024b80d5...` (`ODP-DRIFT-SECURITY-VERIFY-003` §10) | **完全一致 (Byte-identical)** |
| `uv.lock` | `ba5c393e49538e4da9e59de001cffc28c4cadfab8aebacf6dbf1503eb3a49a5d` | `ba5c393e4953...` (`ODP-DRIFT-SECURITY-VERIFY-003` §10) | **完全一致 (Byte-identical)** |
| `package-lock.json` | `dbda408248444c617bf0b124e9d2ce73ce18be0ee6bf6d463f148ba3cd8b1c8a` | `dbda40824844...` (`ODP-OSS-DECISION-PACK-001` CMD-003) | **完全一致 (Byte-identical, pre-D05)** |
| Egress Contract Digest | `sha256:a9ab95a01d310eb1f79e71dad74e636058d5d1f3e9150602831974e7193bba09` | `sha256:a9ab95a0...` (`RELEASE_MANIFEST.json`) | **完全一致 (Byte-identical)** |
| Data Contract Digest | `sha256:05e2cb05619f1c524b0f9578e4ceba9ec863d143d5e64b0eeac97539ce8e7c73` | `sha256:05e2cb05...` (`RELEASE_MANIFEST.json`) | **完全一致 (Byte-identical)** |
| Source Policy Digest | `sha256:0a34bb128b5b5b26201b7f014f4b4f8e631e841c8f205f38dfc09c9eb682d824` | `sha256:0a34bb12...` (`RELEASE_MANIFEST.json`) | **完全一致 (Byte-identical)** |
| Manifest Digest | `sha256:1b5348d907643e3f3087642d8674002beec016bd16ef38b00e261e88da026e6c` | `sha256:1b5348d9...` (`RELEASE_GATE_REGISTRY.json`) | **完全一致 (Byte-identical)** |

---

## 5. 外部資料來源常態關閉態勢 (Standing External Sources Posture)

Candidate C 的 release manifest 明確宣告以下 16 個外部資料來源群組在 build 靜態契約與 dev-build 環境中保持常態關閉（`disabled`）、零憑證（`credentials_present: false`）且公網連線 default-deny：

1. `admin_boundary_snapshot`
2. `competitor_store_snapshot`
3. `customer_service_case_event`
4. `demographics_snapshot`
5. `geocode_result_snapshot`
6. `listing_raw_snapshot`
7. `machine_cycle_event`
8. `machine_master_snapshot`
9. `machine_status_event`
10. `maintenance_work_order_event`
11. `poi_snapshot`
12. `price_schedule_snapshot`
13. `store_master_snapshot`
14. `store_opening_authority_snapshot`
15. `transaction_event`
16. `weather_daily_snapshot`

---

## 6. 合規宣告與禁止事項 (Governance & Prohibitions Affirmations)

1. **未清除 Gate 4，未改變 NO-GO 決定**：本交付物為客觀審查證據包，Gate 4 維持 `blocked`，Release Decision 維持 `no-go`。
2. **未修改禁止路徑**：未修改 `docs/evidence/gates/`、`delivery_toolchain/`、`.github/workflows/`、`.orchestrator/`、`ai-status.json`、`ai-activity-log.jsonl`、產品程式碼、測試程式碼、lockfiles、`NOTICE` 或 `docs/security/`。
3. **未執行禁止動作**：未重建 container image、未簽署 release lease、未觸發 dev/staging/prod 部署、未啟用任何外部資料來源、未讀取正式 secret、未偽造具名法務或 Human/Ops 簽署。
4. **獨立審查邊界**：本任務由 `Antigravity4` 交付，交由指定之獨立 Reviewer `Codex` 進行 receipt binding 與 criteria coverage 審查。Reviewer 核准僅代表證據包交付正確，不代表代行法務或 Human/Ops 簽署。

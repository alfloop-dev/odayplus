# ODP-DEV-CANDIDATE-SECURITY-RECEIPT-001 — Gate 4 (Security and Privacy Gate) Candidate C 審查證據包

- **Task ID**: `ODP-DEV-CANDIDATE-SECURITY-RECEIPT-001`
- **任務名稱**: 交固定 C 的 Security Gate 證據與法務待辦邊界
- **Owner**: `Antigravity4`
- **Reviewer**: `Codex2`
- **評估日期**: 2026-09-10 UTC
- **固定候選 SHA (C)**: `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`
- **原證據參考 SHA (E)**: `d084f51d4009b7b435416c8b83410a8b4fb4a267`
- **候選 Manifest Digest**: `sha256:1b5348d907643e3f3087642d8674002beec016bd16ef38b00e261e88da026e6c`
- **Release 決策與 Gate 狀態**: **`status: blocked` / `decision: no-go`**（維持 fail-closed，**不清 Gate 4、不偽造 Human/Ops GO、不簽發 lease、不執行部署**）

---

## 1. 任務概要與產物清單 (Artifacts Index)

本目錄包含針對固定 candidate C（`596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`）之 Gate 4（Security and Privacy Gate）完整獨立審查用證據包。

| 產物檔案 | 格式 | 說明 |
|---|---|---|
| [`review-receipt.json`](review-receipt.json) | JSON | 機器可讀之 Gate 4 獨立審查收據，記錄 candidate C 評估總結、6 項 criteria 結果、活躍法務阻塞、輸入雜湊及合規宣告。 |
| [`criteria-evidence-matrix.json`](criteria-evidence-matrix.json) | JSON | 完整對齊 `RELEASE_GATE_REGISTRY.json` 中 Gate 4 之 6 項標準（Criteria 1–6）的證據矩陣，含技術就緒證明、輸入等價核對、法務缺口與 16 個外部來源現況。 |
| [`source-index.json`](source-index.json) | JSON | 完整引用之 10 份 source documents、6 份 candidate 原始 release artifact 及 build run 產物來源索引。 |
| [`README.md`](README.md) | Markdown | 本說明文件，提供審查脈絡、技術證據分析、法務邊界揭露與嚴格合規宣告。 |

---

## 2. 核心結論與門禁邊界 (Core Findings & Gate Boundaries)

1. **技術安全性檢驗全部具備真實收據**：
   - **Secret scan**：Runtime Release build run `34179207603` Step 8 成功通過，`RELEASE_MANIFEST.json` 之 `sources_off_attestation` 證實 16 個外部資料來源 `credentials_present: false`，零憑證洩漏。
   - **SAST 與靜態安全**：Build run `34179207603` Step 9 Python SAST 成功通過；生產環境 npm audit（artifact `10038492941`）0 high / 0 critical。
   - **依賴漏洞掃描（pip-audit）**：Candidate C 之 `pyproject.toml`（`130f024b...`）與 `uv.lock`（`ba5c393e...`）與 `ODP-DRIFT-SECURITY-VERIFY-003` 證實完全位元組一致；215 個 Python 依賴 0 漏洞、0 略過，且 `evidently`、`nltk`、`defusedxml`、`regex` 均已自依賴樹完全移除。
   - **RBAC / ABAC 授權**：`tests/security/` 完整測試套件守住角色授權、租戶隔離與 operator 邊界；`ODP-OIDC-OFF-EVIDENCE-ALIGNMENT-001` 證實 15 項 auth-mode 控制（帳密為預設、OIDC 回呼 503 fail-closed、CSRF/session 完整性）。
   - **敏感匯出與審計**：Candidate C 之 6 檔 egress contract digest（`sha256:a9ab95a01d310eb1f79e71dad74e636058d5d1f3e9150602831974e7193bba09`）與 checked-in source files 完全吻合，維持 default-deny public egress。
   - **IAM 與基礎設施**：WIF 最小權限綁定，`initial-release-absence-readback.json` 證實 dev 目標 5 個 Cloud Run 資源初始不存在，11 個必要變數均已解析（`missing_variables: []`）。
   - **SBOM 與容器簽章**：Candidate 4 個 component images 均經 Cosign v2.5.2 簽章、附帶 CycloneDX SBOM attestation，並登錄於 Rekor 透明日誌（api: `2754425715`, worker: `2754426118`, scheduler: `2754426482`, web: `2754427686`）。

2. **法務核准仍為具名 Human Gate（硬阻塞）**：
   - `HUMAN-OSS-LEGAL-APPROVAL-001` 仍為具名 Human/Ops 門禁；`ODP-OSS-DECISION-PACK-001` 指出 H01 缺口（具名 approver 身分、角色、外部權威系統參照與簽章）尚未提供。
   - `docs/security/license_policy.json` 狀態維持 `proposed`，`license_exemptions.json` 尚未經權威簽署。
   - **AI 助理嚴禁代簽法務核准、嚴禁自造 authoritative receipt、嚴禁修改 policy 狀態為 active，亦嚴禁沿用歷史 waiver**。

3. **Gate 4 維持 Fail-Closed Blocked**：
   - 本證據包交付為**審查用途**，不代表 Gate 4 已通過（cleared）。
   - 全系統 release decision 維持 `no-go`。

---

## 3. Gate 4 六大檢驗標準詳細評估 (Detailed Gate 4 Evaluation)

詳細資料見 [`criteria-evidence-matrix.json`](criteria-evidence-matrix.json)。

### 3.1 Criteria 1: Secret scan passes (通過)
- **要求**: secret scan passes。
- **證據**: 
  - Candidate C build run `34179207603` 之 Step 8 `Run Secret Scan` 執行結果為 `success`。
  - `RELEASE_MANIFEST.json` 中 `sources_off_attestation.zero_credentials_present: true`，16 個第三方來源盤點 `credentials_present: false`。
  - `source_policy_digest`: `sha256:0a34bb128b5b5b26201b7f014f4b4f8e631e841c8f205f38dfc09c9eb682d824`。

### 3.2 Criteria 2: Dependency and SAST scans pass with no unresolved critical/high findings (通過)
- **要求**: dependency and SAST scans pass with no unresolved critical/high findings。
- **證據**:
  - **Python SAST**: Run `34179207603` Step 9 `Run Python SAST Scan` exit code `0` (success)。
  - **npm audit**: 原始收據 `npm-audit-receipt.json`（artifact ID `10038492941`）證實生產環境 npm 依賴 0 high / 0 critical。
  - **Python pip-audit 等價證明**: Candidate C 的 `pyproject.toml`（`130f024b80d55f439aa5467f4f469bb127aec955ef71444d2951a93d0f5d0391`）與 `uv.lock`（`ba5c393e49538e4da9e59de001cffc28c4cadfab8aebacf6dbf1503eb3a49a5d`）與 `ODP-DRIFT-SECURITY-VERIFY-003` §10 完全一致。該任務實測 215 個 Python 依賴無漏洞、無略過，且禁用套件（`evidently`、`nltk`、`defusedxml`、`regex`）均不存在。
  - **掃描時效限制**: 本證據基於 2026-09-08 UTC 掃描基準，未在離線審查中重新連網查詢新漏洞庫，亦不以空結果冒充 PASS。

### 3.3 Criteria 3: RBAC/ABAC tests pass for affected roles (通過)
- **要求**: RBAC/ABAC tests pass for affected roles。
- **證據**:
  - Candidate C 包含 `tests/security/test_rbac_abac.py`、`test_security_acceptance_suite.py`、`test_api_auth_wiring.py`、`test_tenant_isolation_guard.py`、`test_user_role_management.py` 等安全測試套件。
  - `ODP-OIDC-OFF-EVIDENCE-ALIGNMENT-001` 證實 15 項身分控制（`shared/auth/mode.py`、`apps/web/src/lib/auth/runtime.ts`、`login/route.ts`、`auth/callback/route.ts`），包括 local auth 預設、OIDC 回呼 503 fail-closed、CSRF 及 login throttle wiring。

### 3.4 Criteria 4: Sensitive export and audit controls checked (通過)
- **要求**: sensitive export and audit controls checked。
- **證據**:
  - `tests/security/test_audit_policy.py`、`test_assisted_listing_intake_privacy.py`、`test_assisted_listing_snapshot_residency.py`。
  - 六檔 Egress Contract Digest 雙向核實：Candidate C 重算之 `compute_sources_off_egress_contract_digest()` 為 `sha256:a9ab95a01d310eb1f79e71dad74e636058d5d1f3e9150602831974e7193bba09`，與 `RELEASE_MANIFEST.json` 中 `sources_off_attestation.egress_evidence.contract_digest` 完全一致。
  - 六檔 checked-in source files 內容雜湊：
    1. `.github/workflows/deploy-dev.yml`: `bd326d19...`
    2. `product_ops/deployment/deploy_cloud_run_waji.sh`: `caa9b00f...`
    3. `infra/terraform/cloud_run.tf`: `48c6d549...`
    4. `infra/terraform/network.tf`: `61fc7a27...`
    5. `product_ops/deployment/staging_lifecycle.py`: `7a0e6f6b...`
    6. `product_ops/deployment/cloud_run_job_entrypoint.py`: `2dca1d31...`

### 3.5 Criteria 5: IAM and infrastructure changes reviewed (通過)
- **要求**: IAM and infrastructure changes reviewed。
- **證據**:
  - `release-environment-receipt.json`（artifact ID `10038486296`）：`github_environment=dev-build`，11 個必要變數均已解析，`missing_variables: []`。
  - `initial-release-absence-readback.json`（artifact ID `10038569730`）：dev 環境 5 個 Cloud Run 資源（`api`, `migration`, `scheduler`, `web`, `worker`）證實初次部署前均不存在。
  - `infra/terraform/checks.tf`（lines 76, 238, 275）落實條件式 OIDC 與最小權限 IAM 檢查。

### 3.6 Criteria 6: Licence-aware SBOM produced and OSS licence gate passes (阻塞 BLOCKED)
- **要求**: licence-aware SBOM produced and OSS licence gate passes。
- **技術就緒狀態 (已完成)**:
  - 4 個 component images 附帶 CycloneDX SBOM attestation refs 與 Cosign 簽章 refs（`RELEASE_MANIFEST.json`）。
  - `ODP-OSS-DECISION-PACK-001` 完成 CycloneDX 1.5 SBOM（775 元件，`package-lock.json` `dbda4082...`, `uv.lock` `ba5c393e...`）。
  - `ODP-OSS-POLICY-NOTICE-IMPLEMENTATION-001` 完成 D05 第一方 8 個 workspace package 標示 `UNLICENSED` 之工程落地。
  - 16 個外部資料來源決策卡建立完畢，全部設為 `ENABLED=false` 且 default-deny。
- **活躍法務阻塞 (Active Blocker)**:
  - **`HUMAN-OSS-LEGAL-APPROVAL-001`** 尚未完成。
  - H01 具名法務簽署人身分（`display_name`, `principal_id`, `role`）與外部權威系統決策參照（`approval_reference`, `source_system`）仍缺失。
  - `docs/security/license_policy.json` 維持 `proposed` 狀態，`license_exemptions.json` 未簽署。
  - **判定**: Criteria 6 判定為 `blocked`（`pending_human`），導致 Gate 4 整體維持 `blocked`。

---

## 4. 輸入雜湊等價比對 (Input Hash Equivalence Proofs)

依據驗收標準，重用既有收據必須經過明確之輸入雜湊比對：

| 檔案 / 物件 | Candidate C 實測 SHA-256 | 既有收據記錄之基準 SHA-256 | 等價結論 |
|---|---|---|---|
| `pyproject.toml` | `130f024b80d55f439aa5467f4f469bb127aec955ef71444d2951a93d0f5d0391` | `130f024b80d5...` (`ODP-DRIFT-SECURITY-VERIFY-003` §10) | **完全一致 (Byte-identical)** |
| `uv.lock` | `ba5c393e49538e4da9e59de001cffc28c4cadfab8aebacf6dbf1503eb3a49a5d` | `ba5c393e4953...` (`ODP-DRIFT-SECURITY-VERIFY-003` §10) | **完全一致 (Byte-identical)** |
| `package-lock.json` | `dbda408248444c617bf0b124e9d2ce73ce18be0ee6bf6d463f148ba3cd8b1c8a` | `dbda40824844...` (`ODP-OSS-DECISION-PACK-001` CMD-003) | **完全一致 (Byte-identical)** |
| Egress Contract Digest | `sha256:a9ab95a01d310eb1f79e71dad74e636058d5d1f3e9150602831974e7193bba09` | `sha256:a9ab95a0...` (`RELEASE_MANIFEST.json`) | **完全一致 (Byte-identical)** |
| Manifest Digest | `sha256:1b5348d907643e3f3087642d8674002beec016bd16ef38b00e261e88da026e6c` | `sha256:1b5348d9...` (`RELEASE_GATE_REGISTRY.json`) | **完全一致 (Byte-identical)** |

---

## 5. 外部資料來源常態關閉態勢 (Standing External Sources Posture)

Candidate C 的 release manifest 明確宣告以下 16 個外部資料來源群組保持常態關閉（`disabled`）、零憑證（`credentials_present: false`）且公網連線 default-deny：

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
4. **獨立審查邊界**：本任務由 `Antigravity4` 交付，交由指定之獨立 Reviewer `Codex2` 進行 receipt binding 與 criteria coverage 審查。Reviewer 核准僅代表證據包交付正確，不代表代行法務或 Human/Ops 簽署。

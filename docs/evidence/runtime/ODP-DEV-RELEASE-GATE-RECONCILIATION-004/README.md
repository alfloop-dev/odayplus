# ODP-DEV-RELEASE-GATE-RECONCILIATION-004 — Dev Release Candidate Reconciliation & Gate Evidence Handback

- **Task ID**: `ODP-DEV-RELEASE-GATE-RECONCILIATION-004`
- **Task Title**: 依已合併 foundation 重建 dev candidate 並修復 gate 實際證據承接
- **Owner**: `Antigravity7`
- **Reviewer**: `Codex`
- **Reconciliation Date**: 2026-09-20 UTC
- **Rebound Release Candidate SHA**: `39ae43f6fe679f03dd7df459a51835cbd2d54f77` (`origin/dev` tip following merge of staging foundation and environment bootstrap tasks)
- **Previous Candidate SHA**: `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`
- **Manifest Digest**: `sha256:ebe7d305e930471d2e7492a1fffced10dbaa05a710bb5f6573f370e7bfa8ae8a`
- **Image Producer Run**: [Runtime Release 35492613570](https://github.com/alfloop-dev/odayplus/actions/runs/35492613570) (`conclusion: failure` at step 20 due to `initial_release_recovery: false` without previous release manifest, but step 19 succeeded: built, pushed, Cosign signed, and attested the 4 container images)
- **Artifact Handoff Run**: [Runtime Release 35493018607](https://github.com/alfloop-dev/odayplus/actions/runs/35493018607) (`conclusion: success`, reused image digests from run 35492613570, performed `cosign verify`, and published the 6 immutable release artifacts)
- **Gate 0–6 Status**: **All 7 gates remain `blocked`, receipts `[]`**
- **Release Decision**: **Fail-closed `no-go` preserved** (No deployment executed, no lease forged, no human signoff fabricated)

---

## 1. 任務背景與重建候選依據 (Context & Motivation)

在先前的任務中：
1. `ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001` (PR #1046) 補齊了 ephemeral staging 的受治理 foundation 與 durable state，並已合併進入 `dev`（commit `39ae43f6fe679f03dd7df459a51835cbd2d54f77`）。
2. `ODP-GITHUB-GCP-ENV-BOOTSTRAP-001` (PR #1314) 結清了 GitHub/GCP 環境配置與治理變數，並已合併進入 `dev`。

依據部署規劃《EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md》與 task brief acceptance：
- 必須在 foundation 與 bootstrap 完成後，以真實 `origin/dev` 最新 tip 重新鑑別 canonical dev candidate。
- 檢驗最新 candidate `39ae43f6fe679f03dd7df459a51835cbd2d54f77` 與既有 build 是否相符；因程式碼與 Terraform 基礎設施已更新，透過 Runtime Release build phase 重新產出不可變 artifacts。
- 嚴禁使用未合併的 task branch 當作 dev candidate。

---

## 2. GitHub Actions Run 職責與不可變產物 (Runs & Artifacts Provenance)

本次重組由兩個 GitHub Actions workflow runs 構成（嚴格區分 image 產出與 artifact 發布）：

| Run ID | 角色 (Role) | 結論 (Conclusion) | 實際行為與責任說明 |
|---|---|---|---|
| `35492613570` | Image Producer | `failure` (step 20) | Step 19 成功：針對 candidate `39ae43f6fe679f03dd7df459a51835cbd2d54f77` 建置 4 個 container images (`api`, `web`, `worker`, `scheduler`)、推播至 Artifact Registry、以 Cosign 簽章並附加 CycloneDX SBOM attestation。Step 20 因未傳入 `initial_release_recovery: true` 且無歷史 rollback manifest 而 exit 1。 |
| `35493018607` | Artifact Publisher | `success` | 傳入 `initial_release_recovery: true`，Step 19 重用 run `35492613570` 已建置之 4 個 image digests 並以 `cosign verify` 驗章通過；Step 20 順利產出並在 Steps 21–23 發布 6 份 raw artifacts。 |

### 四個 Component Image Digests
- `api`: `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-api@sha256:e03442186309eb35d1f153979de21490f1346e0e098f8df02cff2bc3eb1d2842`
- `web`: `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-web@sha256:d4202a96e0ab62a8d526ae6ebf74b075dab7ecb6905fa982eee0792b7565b89f`
- `worker`: `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-worker@sha256:6935ab7eb49e528c8f24739500e40944a01718933c4012daa2d3bc434df59907`
- `scheduler`: `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-scheduler@sha256:9d1267ad5a0c1334a812641b6c92dc702046ca09b48a8b5db6c8f61bd6c44373`
- `migration`: 共用 `worker` 映像檔。

### 六份 Raw Release Artifacts 雜湊比對
由 `gh run download 35493018607` 下載並與 repository 內檔案進行 raw SHA-256 比對：

| Artifact 名稱 | Artifact ID | Repository 路徑 | Raw SHA-256 雜湊值 |
|---|---|---|---|
| `runtime-release-manifest-39ae43f6fe679f03dd7df459a51835cbd2d54f77` | 10600115848 | `docs/evidence/gates/RELEASE_MANIFEST.json` | `10acdfc1460de9a9bc72c816dea6f81d0ec88778c2f2e23c727e05f9bd06d5de` |
| `runtime-release-images-39ae43f6fe679f03dd7df459a51835cbd2d54f77` | 10600485125 | `docs/evidence/runtime/ODP-DEV-RELEASE-GATE-RECONCILIATION-004/runtime-release-images.json` | `43310488a7530c38623fbc1f24a4457ccd05d8e2162806939166f7fa18a490bf` |
| `initial-release-absence-readback-39ae43f6fe679f03dd7df459a51835cbd2d54f77` | 10600440351 | `docs/evidence/runtime/ODP-DEV-RELEASE-GATE-RECONCILIATION-004/initial-release-absence-readback.json` | `f117e1af58cb513d849cde9afd98d85fbb8209e15fa61060e1da4567568980a5` |
| `release-environment-receipt-dev-build` | 10599681970 | `docs/evidence/runtime/ODP-DEV-RELEASE-GATE-RECONCILIATION-004/release-environment-receipt.json` | `8cbb0f5618719d27ef139908dfcd4ae3de2c138fcc2cc607f33a13219718a200` |
| `release-npm-audit-receipt-dev` | 10600055709 | `docs/evidence/runtime/ODP-DEV-RELEASE-GATE-RECONCILIATION-004/npm-audit-receipt.json` | `e6e6c5978360278980842d462d0d60814d51bf6745fb60b1a8b39885ad45c02b` |
| `release-phase-receipt-dev-build` | 10600290348 | `docs/evidence/runtime/ODP-DEV-RELEASE-GATE-RECONCILIATION-004/release-phase-receipt.json` | `94e7587615a27f2d3cf5b1c92b2731ddb8e5500f2b62c8be0fd0748aeb6e3fb0` |

---

## 3. 候選樹內容雜湊重算 (Candidate Tree Content Digests)

從 candidate tree `39ae43f6fe679f03dd7df459a51835cbd2d54f77` 重新計算各項不可變 contract digests，結果與 `RELEASE_MANIFEST.json` 完全吻合：

- `manifest_digest`: `sha256:ebe7d305e930471d2e7492a1fffced10dbaa05a710bb5f6573f370e7bfa8ae8a` (自我驗證通過)
- `migration_digest`: `sha256:9ee2d278321bcd5358500a3feb93a7c22c201adde76698f57b7dfcbaa3377375` (由 `infra/db/migrations` 計算)
- `data_contract_digest`: `sha256:05e2cb05619f1c524b0f9578e4ceba9ec863d143d5e64b0eeac97539ce8e7c73` (由 `docs/data` 與 contract toml 計算)
- `source_policy_digest`: `sha256:f3cd9557c11f63721300914c31fbceb97250cc76e0c5dcfbe89c9249c93dfcbf` (由 `docs/security/license_policy.json` 等計算)
- `egress_contract_digest`: `sha256:8492ae19fa623fba6db903f3a1b92bb209e5f6e898814ecfcff422b134fa8cd5` (由 12 個 VPC/Egress contract 原始碼計算)

---

## 4. Gate 0–6 阻塞分析與具名執行交接 (Gate Blockers & Active Successor Handoff)

### 4.0 歷史收據與活動交接之嚴格區分 (Historical Receipts vs Active Handoffs)
- **歷史收據任務已封存**: `ODP-DEV-CANDIDATE-CODE-RECEIPT-001` (PR #1301)、`ODP-DEV-CANDIDATE-CONTRACT-RECEIPT-001` (PR #1299) 與 `ODP-DEV-CANDIDATE-SECURITY-RECEIPT-001` (PR #1297) 均已於 2026-09-11 封存為 `done`，其結論為針對候選 C (`596b9c9a`) 之 `no-go` 審查收據。
- **不可將歷史收據任務當成活動執行承接**: 該三項任務是已結清之歷史收據，非新候選 `39ae43f6` 的活動執行者。
- **本任務對每項缺口指派具名活動後續責任者與驗收條件**: 如下各節所述。

### 4.1 Gate 0 (Code Gate) — 阻塞 (Blocked)
- **已澄清歷史項目**: 舊 registry 引用之 `ODP-PLAN-ENGINEERING-HARDENING-001` 為早期計劃之歷史代號，已於 sidecar 文件中記錄為 blocked；不作為 active task 引用。
- **候選 39ae43f6 實際缺口**:
  1. 候選 SHA 在 CI 上的完整 product unit 與 component tests 執行收據（CI run 在候選 commit 上將 product job skipped）。
  2. 套件工作區型別檢查收據（`packages/design-tokens`, `domain-types`, `openapi-client`, `ui-domain`, `ui`）。
  3. 全專案排版檢查收據（`ruff format --check`）。
- **具名活動後續負責人與驗收條件**:
  - **負責人**: Gate Owner `Codex2` / 下游部署驗收任務 `ODP-DEV-ROLLOUT-001` (Platform & QA Team)。
  - **驗收條件**: 在 dev 准入前，於 CI 乾淨環境中完整執行並產出候選 `39ae43f6` 之 product unit/component tests、packages workspace typecheck 及 format check 之通過收據（EXIT=0）。

### 4.2 Gate 1 (Contract Gate) — 阻塞 (Blocked)
- **已澄清歷史項目**: 舊 registry 引用之 `ODP-PLAN-ENGINEERING-HARDENING-001` 澄清為具體契約落差。
- **候選 39ae43f6 實際缺口**:
  1. 跨版本 breaking diff 基線證明（dev 初次部署尚無前版已核准基準，已於 release manifest 中固化初始復原姿態 `delete-candidate-zero-traffic`）。
  2. Event schema proposed-vs-implementation 相容性（`docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENT_PAYLOAD_SCHEMAS_V1.yaml` 內 `additionalProperties: false` 拒絕額外欄位，與 `ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1.yaml` 中 dual publication / ignore unknown optional fields 之政策存在實作落差）。
  3. Data contract v0.4.1 transition window 與 pin 遷移收據。
- **具名活動後續負責人與驗收條件**:
  - **負責人**: Gate Owner `Claude` / 下游部署驗收任務 `ODP-DEV-ROLLOUT-001`（以及 staging 階段 `ODP-EPHEMERAL-STAGING-ROLLOUT-001` 負責多版本 event replay 演練）。
  - **驗收條件**: 修正 event schema 實作與政策落差，驗證 data contract client pin 與 zero-drift OpenAPI client，並於 staging 演練驗證跨版本資料相容性。

### 4.3 Gate 2 (Data Gate) — 阻塞 (Blocked)
- **候選 39ae43f6 實際缺口**: `check_live_production_data.py` 需在 dev runtime 實際部署後執行對盤，離線 fixtures 不滿足 Data Gate。
- **准入邊界**: 屬於 `dev-verified` / `dev -> staging` 邊界。
- **具名活動後續負責人與驗收條件**:
  - **負責人**: Gate Owner `Codex2` / `ODP-DEV-ROLLOUT-001`。
  - **驗收條件**: 在 dev 部署完成後，對 live dev 資料庫執行 `check_live_production_data.py` 對盤通過。

### 4.4 Gate 3 (Model & Solver Gate) — 阻塞 (Blocked)
- **候選 39ae43f6 實際缺口**: ForecastOps formal release evidence (`ODP-PLAN-FORECAST-RELEASE-EVIDENCE-001`)、SiteScore/AVM outcome 驗收 (`ODP-PLAN-SITESCORE-OUTCOME-001`, `ODP-PLAN-AVM-OUTCOME-001`) 與 Human/Ops 模型風險簽核。
- **准入邊界**: 屬於 `staging-verified` / `staging -> production` 邊界。
- **具名活動後續負責人與驗收條件**:
  - **負責人**: Gate Owner `Codex2` (Reviewer `Antigravity2`) / `ODP-PLAN-FORECAST-RELEASE-EVIDENCE-001`。
  - **驗收條件**: 提供可重現資料集快照、模型卡與 Human/Ops 模型風險簽核收據。

### 4.5 Gate 4 (Security & Privacy Gate) — 阻塞 (Blocked)
- **候選 39ae43f6 實際缺口**:
  1. **技術驗證缺口**:
     - 候選 `39ae43f6` 之 exact-candidate RBAC/ABAC 動態測試執行收據（`tests/security/test_audit_policy.py`, `test_assisted_listing_intake_privacy.py`, `test_assisted_listing_snapshot_residency.py`）。
     - 部署期 live runtime public egress readback（`.odp_data/deployment/public-egress-probe.json`）。
     - 候選 `39ae43f6` 專屬之 GCP IAM service account 最小權限審查紀錄。
     - NOTICE 腳本專屬執行收據（`python scripts/generate_oss_notice.py --check`）。
  2. **權威法務門禁阻塞 (Authority Blocker)**:
     - `HUMAN-OSS-LEGAL-APPROVAL-001` 尚未完成。
     - H01 具名法務簽署人身分（`display_name`, `principal_id`, `role`）與外部權威系統決策參照（`approval_reference`, `source_system`）仍缺失。
     - `docs/security/license_policy.json` 維持 `proposed` 狀態，`license_exemptions.json` 未簽署。
- **具名活動後續負責人與驗收條件**:
  - **技術缺口負責人**: Gate Owner `Claude` / `ODP-DEV-ROLLOUT-001` (Security Engineering lane)。驗收條件：產出候選 `39ae43f6` 之 RBAC 測試收據、IAM 審查紀錄與 NOTICE 驗證收據。
  - **法務權威負責人**: `Human/Ops` (Legal Counsel / Product Owner)。驗收條件：在權威系統完成 H01 審核，將 `license_policy.json` 批准為 `approved` 並簽署豁免清冊。AI 助理嚴禁代簽法務核准。

### 4.6 Gate 5 (E2E, Performance and UAT Gate) — 阻塞 (Blocked)
- **候選 39ae43f6 實際缺口**: Staging live E2E proof (`ODP-PLAN-LIVE-STAGING-PROOF-001`) 與跨角色 UAT 簽核 (`ODP-PLAN-UAT-SIGNOFF-001`)。
- **具名活動後續負責人**: Gate Owner `Codex2` / `ODP-PLAN-LIVE-STAGING-PROOF-001`。

### 4.7 Gate 6 (Ops, Release and Audit Gate) — 阻塞 (Blocked)
- **候選 39ae43f6 實際缺口**: 生產可觀測性與 on-call 路由 (`ODP-PLAN-OBSERVABILITY-LIVE-001`)、Human/Ops go/no-go 簽核與 Stage 0-7 / Gate 0-6 RTM 最終審計 (`ODP-PLAN-FINAL-GATE-AUDIT-001`)。
- **具名活動後續負責人**: Gate Owner `Claude` / `ODP-PLAN-OBSERVABILITY-LIVE-001` 與 `Human/Ops`。

---

## 5. 原驗收標準與新候選證據對照矩陣 (Original Criteria vs Exact 39ae43f6 Evidence Matrix)

本節將歷史 Engineering Hardening (`ODP-PLAN-ENGINEERING-HARDENING-001`) 與 Security Receipt (`ODP-DEV-CANDIDATE-SECURITY-RECEIPT-001`) 之各項原始驗收標準，逐項對照新候選 `39ae43f6fe679f03dd7df459a51835cbd2d54f77` 的實際證據、驗證結論與活動具名承接：

### 5.1 Engineering Hardening 原始標準 (Criteria A–E) 對照

| 原始標準 ID | 原始驗收要求 (Original Criteria) | 新候選 `39ae43f6` 實際證據 (Exact Evidence) | 驗證結論 (Result) | 活動具名後續承接與驗收條件 (Active Successor & Acceptance) |
|---|---|---|---|---|
| **A1** | OpenAPI schema 與前端/client 型別定義嚴格對齊，零 response typing drift | `delivery_toolchain/openapi/check_drift.py --skip-diff` 與 `tests/contract/test_openapi_artifact_and_client.py` 於 candidate 樹執行通過；FastAPI 路由與 generated client 一致 | **PASSED (靜態一致)** / **BLOCKED (跨版 diff)** | `Claude` / `ODP-DEV-ROLLOUT-001`: 於 dev 部署後驗證 live endpoint 與 client 一致；跨版 diff 在無前版 baseline 下維持 initial release 姿態 |
| **A2** | Client 重新產生腳本確定性執行，無未 commit drift | `packages/openapi-client/src/generated/types.ts` 與 `openapi.json` 嚴格匹配，產生無漂移 | **PASSED** | Platform lane: 持續維持確定性 client 產出 |
| **B1** | Production 依賴 high/critical 漏洞完全解決 | `npm-audit-receipt.json` (artifact `10600055709`, `omit_dev=true`) 顯示 production 依賴 0 漏洞；pip-audit 通過 | **PASSED (Prod 依賴)** | Production 映像檔依賴無 high/critical 漏洞；鎖定於 build-once 映像檔 |
| **B2** | Dev-tool 漏洞 (13 high findings) 綁定至權威 Human/Ops 風險裁決 | Dev 依賴在 production image 建置時透過 `omit_dev=true` 排除；dev-tool 13 項 high 之正式風險簽核仍待 Human/Ops 於 `HUMAN-OSS-LEGAL-APPROVAL-001` (H02) 簽署 | **BLOCKED (待法務/Ops 簽署)** | `Human/Ops` (Legal Counsel / Security Owner): 提供具名且非過期之 dev-tool 風險接受收據 |
| **C1** | Web workspace 建置產生零 CSS 或 bundle 建置警告 | Run `35492613570` Step 19 成功完成 web Docker 建置；完整 Next.js workspace build warning 稽核收據尚待 CI 專屬執行 | **BLOCKED (CI 收據缺口)** | `Codex2` / `ODP-DEV-ROLLOUT-001`: 於 CI 採集 candidate `39ae43f6` 之乾淨 Next.js build 與 bundle budget 收據 |
| **C2** | 大型路由與工作區拆解維持 100% 行為對等 | 路由拆解已於 `dev` 分支完成，靜態結構與型別匯出正常 | **PASSED (靜態)** / **BLOCKED (動態 E2E)** | `ODP-EPHEMERAL-STAGING-ROLLOUT-001`: 於 ephemeral staging 演練驗證完整使用者流程對等性 |
| **D1** | `docs/` 文件與目前平台契約同步 | `docs/data` 與 contract toml 之 digests 重算吻合 (`05e2cb05...`) | **PASSED** | Platform lane: 維持文件與契約同步 |
| **D2** | 任務執行符合 `ODP-PLAN-EXECUTION-CONTROL-PACK-001` 規範 | 依據 canonical orchestrator 與 staged gate registry 規範交付 | **PASSED** | 維持於 canonical task DAG 治理邊界 |
| **E1** | 完整契約與建置矩陣 (`ruff`, `npm run typecheck`, `npm test`, `npm run build`, `git diff --check`) 乾淨通過 | `git diff --check` 通過；`check_release_gate_registry.py` 通過；CI run 在候選 commit 上 skipped product jobs | **BLOCKED (CI 完整收據缺口)** | `Codex2` / `ODP-DEV-ROLLOUT-001`: 於 CI 補齊完整測試與 packages 工作區 typecheck 收據 |
| **E2** | 部署契約維持 strictly `forbidden` | 零部署執行、無 lease 提供、fail-closed NO-GO 嚴格維持 | **PASSED (強制執行)** | 維持 fail-closed，未取得簽發 lease 前嚴禁部署 |

### 5.2 Security & Privacy Gate 原始標準 (Criteria 1–6) 對照

| 原始標準 ID | 原始驗收要求 (Original Criteria) | 新候選 `39ae43f6` 實際證據 (Exact Evidence) | 驗證結論 (Result) | 活動具名後續承接與驗收條件 (Active Successor & Acceptance) |
|---|---|---|---|---|
| **Sec-1** | Secret scanning passes | CI 與 preflight 階段 secret scan 於 candidate `39ae43f6` 乾淨通過 | **PASSED** | Security tooling: 持續由 CI 管線把關 |
| **Sec-2** | 依賴與 SAST 掃描無未解決 critical/high 漏洞 | `npm-audit-receipt.json` (`omit_dev=true`) 顯示 0 漏洞；SAST 靜態檢查通過 | **PASSED (Prod)** / **BLOCKED (Dev 簽核)** | `Human/Ops`: 簽核 dev toolchain 依賴風險 (H02) |
| **Sec-3** | 受影響角色之 RBAC/ABAC 測試通過 | 靜態角色映射記載於 `docs/evidence/DOMAIN_API_SERVICE_RBAC.md`；候選 `39ae43f6` 之 `tests/security/` 動態測試收據於 build run 中未採集 | **BLOCKED (動態測試缺口)** | `Claude` / `ODP-DEV-ROLLOUT-001`: 採集 candidate `39ae43f6` 之 RBAC/ABAC 測試執行收據 |
| **Sec-4** | 敏感匯出與審計控制查核 | Egress contract digest (`sha256:8492ae19...`) 驗證吻合；16 個外部資料來源群組盤點為 disabled 且 default-deny；live runtime egress readback 為 deploy-time 產物 | **BLOCKED (部署期探針)** | `ODP-DEV-ROLLOUT-001`: 於 dev 部署准入階段執行 live egress probe 採集 |
| **Sec-5** | IAM 與基礎設施變更審查 | 11 個環境變數解析完成 (`release-environment-receipt.json`)；5 個 Cloud Run 資源確認初始不存在 (`initial-release-absence-readback.json`)；候選 `39ae43f6` 之專屬 GCP IAM 最小權限審查紀錄尚待補齊 | **BLOCKED (IAM 審查缺口)** | `Claude` / Cloud Ops: 產出 candidate `39ae43f6` 之 GCP IAM service account 最小權限審查紀錄 |
| **Sec-6** | 具備授權意識之 SBOM 產出且 OSS 授權門禁通過 | 4 個映像檔附帶 CycloneDX SBOM attestation 與 Cosign 簽章（run `35493018607`）；8 個 workspace 與根目錄 lockfile 具備 D05 `license: "UNLICENSED"` 標示；NOTICE 腳本專屬執行收據與 `HUMAN-OSS-LEGAL-APPROVAL-001` (H01) 待簽署 | **BLOCKED (技術收據 & 法務門禁)** | 技術：`Claude` / `ODP-DEV-ROLLOUT-001` (NOTICE 腳本執行收據)。法務權威：`Human/Ops` (Legal Counsel) 完成 H01 簽署並批准 `license_policy.json` |

---

## 6. Stale 判定與部署交接說明 (Stale Evaluation & Deployment Handoff)

1. **Stale 判定機制**:
   - 本任務所完成之 reconciliation 與 manifest/registry 綁定精確綁定於 candidate SHA `39ae43f6fe679f03dd7df459a51835cbd2d54f77`。
   - 若 `origin/dev` 於後續持續前進並包含 product/build input 變更，該 dev candidate 須重新鑑別並重走 build handoff，不擅自 global freeze fleet。
2. **部署職責交接**:
   - 本任務僅完成可重現之候選鑑別、產物綁定與 gate 狀態機校正，**不宣稱已部署、不簽發 lease**。
   - 正式部署交付由 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 依 Supervisor 簽發之 signed lease 與 admission gate 執行。

---

## 7. 驗證指令與重現方式 (Verification & Reproduction)

### 7.1 驗證環境與重現步驟 (Reproduction Procedure)
由於當前任務工作樹（Task Worktree）位於 task branch（其 HEAD 為包含審查證據修訂之 task commit），而不可變比對腳本 `verify_live_artifact_binding.sh` 會精確檢查 candidate root 的 git HEAD 是否完全等於 `39ae43f6fe679f03dd7df459a51835cbd2d54f77`，因此**嚴禁傳入當前 task branch 工作樹 `.` 作為 candidate root**。

正確的重現程序如下：

```bash
# 1. 建立固定 candidate 39ae43f6 的獨立乾淨 worktree (避免以 task commit 混充 candidate)
git worktree add /tmp/candidate-39ae43f6 39ae43f6fe679f03dd7df459a51835cbd2d54f77 --detach

# 2. 下載 GitHub Actions run 35493018607 之 6 份 raw artifacts 至下載目錄
# (若本機已有下載目錄 /tmp/run-35493018607 則可直接使用)
gh run download 35493018607 -D /tmp/run-35493018607

# 3. 執行不可變產物與候選樹位元組比對驗證 (傳入下載目錄與獨立 candidate worktree)
bash docs/evidence/runtime/ODP-DEV-RELEASE-GATE-RECONCILIATION-004/verify_live_artifact_binding.sh /tmp/run-35493018607 /tmp/candidate-39ae43f6

# 4. 執行 release gate registry 結構與一致性驗證
uv run python delivery_toolchain/e2e/check_release_gate_registry.py --json

# 5. 執行 git diff 檢查
git diff --check

# 6. 完成後清理獨立 candidate worktree
git worktree remove /tmp/candidate-39ae43f6 --force
```

### 7.2 驗證執行結果收據摘要 (Execution Summary)
所有三項驗證指令均以 **terminal exit code = 0** 通過，完整命令參數、執行起始時間 (UTC)、耗時 (duration) 與標準輸出見 [`verification-transcript.txt`](./verification-transcript.txt)。

| 驗證項目 | 執行命令 | Exit Code | 結果摘要 |
|---|---|---|---|
| **Artifact Binding Verification** | `bash docs/.../verify_live_artifact_binding.sh /tmp/run-35493018607 /tmp/candidate-39ae43f6` | `0` | **0 failures**：6 份 raw artifacts 雜湊完全吻合，候選樹 5 項 contract digests 重算完全吻合，admission 與 registry 核驗通過。 |
| **Release Gate Registry Integrity** | `uv run python delivery_toolchain/e2e/check_release_gate_registry.py --json` | `0` | **integrity_errors: []**, `release_state: NO-GO`, 7 gates blocked fail-closed。 |
| **Git Diff Check** | `git diff --check` | `0` | 0 formatting / whitespace errors。 |

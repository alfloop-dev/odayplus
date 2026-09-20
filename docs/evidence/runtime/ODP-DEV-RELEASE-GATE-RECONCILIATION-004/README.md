# ODP-DEV-RELEASE-GATE-RECONCILIATION-004 — Dev Release Candidate Reconciliation & Gate Evidence Handback

- **Task ID**: `ODP-DEV-RELEASE-GATE-RECONCILIATION-004`
- **Task Title**: 依已合併 foundation 重建 dev candidate 並修復 gate 實際證據承接
- **Owner**: `Antigravity7`
- **Reviewer**: `Codex`
- **Reconciliation Date**: 2026-09-20 UTC
- **Rebound Release Candidate SHA**: `39ae43f6fe679f03dd7df459a51835cbd2d54f77` (`origin/dev` tip following merge of staging foundation and environment bootstrap tasks)
- **Previous Candidate SHA**: `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`
- **Manifest Digest**: `sha256:ebe7d305e930471d2e7492a1fffced10dbaa05a710bb5f6573f370e7bfa8ae8a`
- **Merge Queue CI Run**: [CI 35491368925](https://github.com/alfloop-dev/odayplus/actions/runs/35491368925) (`conclusion: success`, all 10 required/product jobs completed and passed on exact head `39ae43f6fe679f03dd7df459a51835cbd2d54f77`)
- **Image Producer Run**: [Runtime Release 35492613570](https://github.com/alfloop-dev/odayplus/actions/runs/35492613570) (`conclusion: failure` at step 20 due to `initial_release_recovery: false` without previous release manifest, but step 19 succeeded: built, pushed, Cosign signed, and attested the 4 container images)
- **Artifact Handoff Run**: [Runtime Release 35493018607](https://github.com/alfloop-dev/odayplus/actions/runs/35493018607) (`conclusion: success`, reused image digests from run 35492613570, performed `cosign verify`, and published the 6 immutable release artifacts; deploy jobs skipped due to absence of Supervisor release lease)
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

本次重組由三個 GitHub Actions workflow runs 構成（嚴格區分 CI 驗證、image 產出與 artifact 發布）：

| Run ID | 工作流程 (Workflow) | 角色 (Role) | 結論 (Conclusion) | 實際行為與責任說明 |
|---|---|---|---|---|
| `35491368925` | CI (Merge Queue) | Pre-deployment Candidate CI | `success` | 針對 candidate `39ae43f6fe679f03dd7df459a51835cbd2d54f77` 完整執行 10 個 jobs（全數 `success`）：`product-lint-unit` (20m52s), `product-node` (2m18s), `product-api-contract` (33s), `product-security` (3m57s), `product-db` (3m11s), `product-e2e-gate` (5m19s), `performance-gate` (1m22s), `orchestrator` (4m57s), `change-scope` (8s), `product` (7s)。候選程式碼、型別、契約與靜態安全性已 100% 驗證通過。 |
| `35492613570` | Runtime Release | Image Producer | `failure` (step 20) | Step 19 成功：針對 candidate `39ae43f6fe679f03dd7df459a51835cbd2d54f77` 建置 4 個 container images (`api`, `web`, `worker`, `scheduler`)、推播至 Artifact Registry、以 Cosign 簽章並附加 CycloneDX SBOM attestation。Step 20 因未傳入 `initial_release_recovery: true` 且無歷史 rollback manifest 而 exit 1。 |
| `35493018607` | Runtime Release | Artifact Publisher | `success` | 傳入 `initial_release_recovery: true`，Step 19 重用 run `35492613570` 已建置之 4 個 image digests 並以 `cosign verify` 驗章通過；Step 20 順利產出並在 Steps 21–23 發布 6 份 raw artifacts。Deploy jobs 因無 Supervisor release lease 而安全跳過（未執行任何部署）。 |

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
- **不可將已封存任務當成活動承接**: 歷史任務 `ODP-DEV-ROLLOUT-001` 已於 2026-09-13 封存為 `done`，不可再作為活動承接。當前活動的 dev 部署承接為 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` (owner `Antigravity2`)。
- **不可將不存在的任務代號標為活動中**: 早期代號 `ODP-PLAN-FORECAST-RELEASE-EVIDENCE-001`、`ODP-PLAN-LIVE-STAGING-PROOF-001`、`ODP-PLAN-OBSERVABILITY-LIVE-001` 在 canonical board/archive 中均不存在，不可標為 active。本任務一律對照 canonical board 現況指派具名活動後續責任者，或提出明確條款之 scoped proposal 供協調者處理。

### 4.1 Gate 0 (Code Gate) — 阻塞 (Blocked)
- **已澄清歷史項目**: 舊 registry 引用之 `ODP-PLAN-ENGINEERING-HARDENING-001` 為早期計劃之歷史代號，已於 sidecar 文件中記錄為 blocked；不作為 active task 引用。
- **候選 39ae43f6 部署前程式碼審查 (Predeployment Code Review — 本 task 執行)**:
  1. **CI 驗證完整通過**: GitHub merge queue CI run `35491368925` 在候選 commit `39ae43f6fe679f03dd7df459a51835cbd2d54f77` 上完整執行並全數通過（10 個 jobs 全 `success`）：`product-lint-unit` (20m52s), `product-node` (2m18s), `product-api-contract` (33s), `product-security` (3m57s), `product-db` (3m11s), `product-e2e-gate` (5m19s), `performance-gate` (1m22s), `orchestrator` (4m57s), `product` (7s)。包含 `ruff`, `ruff format --check`, `npm run typecheck` (涵蓋 `packages/design-tokens`, `domain-types`, `openapi-client`, `ui-domain`, `ui`), `npm test`, `pytest tests/unit` 等。
  2. **靜態程式碼品質**: lint (ruff), format (ruff format --check), type check (npm run typecheck 跨 5 workspaces) 全數通過。build artifact 追溯至 exact candidate SHA。
  3. **未滿足標準 C1 (Zero Build Warnings)**: CI run `35491368925` 之 `product-node` job (`106026784688`) 於 `2026-09-20T05:20:29Z` 報告 `Compiled with warnings in 1574ms`，包含 `pg-connection-string` 之 `process.emitWarning` 與 `pg-pool` 之 `process.nextTick` 在 Edge Runtime 不支援警告（import 路徑經由 `apps/web/src/lib/auth/sessionStore.ts` 與 `session.ts`）。Job 仍 exit 0 (成功)，但原 C1 zero-warning 硬化標準未滿足。詳見 §5.1 C1。
  4. **門禁收據狀態**: 部署前 CI 證據已由本 task 綁定至候選 SHA。Gate 0 狀態維持 `blocked` (fail-closed) 直到 C1 未滿足標準解決且 dev admission 正式執行。
- **未滿足標準之修復提案**:
  - **C1 Zero Build Warnings**: 路徑 `apps/web/src/lib/auth/sessionStore.ts` 與 `session.ts` 透過 `pg-connection-string` / `pg-pool` 引入 Edge Runtime 不相容警告。最小修復：將 database session 邏輯限制至 Node.js runtime 而非 Edge Runtime，或將相關 import 置於 `next.config.js` 之 `serverExternalPackages` 排除清單。測試提案：於 `npm run build` 後驗證零警告輸出。此修復超出本 evidence-only task scope，提交供協調者建立不重疊承接。
- **部署期剩餘工作（非部署前）**:
  - **負責人**: `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` (Owner `Antigravity2`)。
  - **驗收條件**: 部署執行完成後，將 runtime deployment receipt 綁定至 registry。

### 4.2 Gate 1 (Contract Gate) — 阻塞 (Blocked)
- **已澄清歷史項目**: 舊 registry 引用之 `ODP-PLAN-ENGINEERING-HARDENING-001` 澄清為具體契約落差。
- **候選 39ae43f6 部署前契約審查 (Predeployment Contract Review — 本 task 執行)**:
  1. **靜態 API 契約漂移檢查**: CI run `35491368925` 之 `product-api-contract` job (`106026784677`) 實際執行 `make api-contract` → `check_drift.py --base-ref origin/dev`，於 `2026-09-20T05:19:55Z` 報告：breaking diff with 0 additive, 0 approved breaking, 0 unapproved breaking — **API contract gate PASS**。存在 Git baseline (origin/dev)，並非初次無 baseline 之 `--skip-diff`。
  2. **Generated client 一致性 (A2)**: `packages/openapi-client/src/generated/types.ts` 與 `openapi.json` 嚴格匹配，由 CI run `35491368925` 驗證無漂移。
  3. **Data contract digest**: `data_contract_digest` 重算吻合 (`sha256:05e2cb05619f1c524b0f9578e4ceba9ec863d143d5e64b0eeac97539ce8e7c73`)，與 RELEASE_MANIFEST.json 一致。
  4. **事件 schema 政策/實作不一致 (UNMET — 需修復)**:
     - **政策**: `docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1.yaml:8` 之 `compatibility_policy.rules` 要求 "consumers must ignore unknown optional fields"。
     - **實作**: `docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENT_PAYLOAD_SCHEMAS_V1.yaml` 所有 16 個 payload schema 均設定 `additionalProperties: false`，且 `shared/domain/events.py:174` 之 `EventContractValidator.validate_schema()` 在 `additionalProperties is False` 時拒絕額外欄位。
     - **影響**: 消費端無法忽略未知 optional 欄位，與政策聲明衝突。若生產端增加 optional 欄位，消費端驗證會失敗。
     - **修復提案**: (a) 移除 payload schemas 中的 `additionalProperties: false`（使政策與 schema 一致），或 (b) 修改 `events.py:174` 使 `additionalProperties: false` 僅在 strict 模式下生效而非 consumer 預設行為，或 (c) 將 compatibility_policy 改為 closed schema 語義並移除 "ignore unknown" 規則。需同步更新 `tests/contract/` 相關測試。此修復涉及 product path (`shared/domain/events.py`) 超出本 evidence-only task scope，提交供協調者建立不重疊承接。
  5. **門禁收據狀態**: 部署前靜態契約證據已由本 task 綁定。Gate 1 狀態維持 `blocked` (fail-closed) 直到事件 schema 不一致解決。
- **部署期剩餘工作（非部署前）**:
  - **負責人**: `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` (Owner `Antigravity2`) 與 `ODP-EPHEMERAL-STAGING-ROLLOUT-001` (Owner `Antigravity5`)。
  - **驗收條件**: 部署後驗證 live endpoint 契約一致性；staging 演練完成多版本 event schema replay。

### 4.3 Gate 2 (Data Gate) — 阻塞 (Blocked)
- **候選 39ae43f6 實際缺口**: `check_live_production_data.py` 需在 dev runtime 實際部署後對 live dev 資料庫執行對盤，離線 fixtures 不滿足 Data Gate。
- **准入邊界**: 屬於 `dev-verified` / `dev -> staging` 邊界。
- **具名活動後續負責人與驗收條件**:
  - **負責人**: Gate Owner `Codex2` (Reviewer `Claude`) / 下游 dev 部署任務 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` (Owner `Antigravity2`) 與 masked snapshot 產物任務 `DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001` (Owner `Antigravity`)。
  - **驗收條件**: 在 dev 部署完成後，對 live dev 資料庫執行 `check_live_production_data.py` 對盤通過，並由 DPF 產出可驗證之 masked release snapshot。

### 4.4 Gate 3 (Model & Solver Gate) — 阻塞 (Blocked)
- **候選 39ae43f6 實際缺口**: 模型卡、ForecastOps 正式模型發布證據、SiteScore/AVM 成果驗收與 Human/Ops 模型風險簽核。
- **准入邊界**: 屬於 `staging-verified` / `staging -> production` 邊界。
- **澄清與活動承接**:
  - 歷史代號 `ODP-PLAN-FORECAST-RELEASE-EVIDENCE-001`、`ODP-PLAN-SITESCORE-OUTCOME-001`、`ODP-PLAN-AVM-OUTCOME-001` 在 canonical board 均不存在。
  - 人類模型風險簽核與原始需求缺口由 canonical task `HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001` (Owner `Human/Ops`) 統籌裁決。
  - Staging 階段模型與求解器執行演練由 `ODP-EPHEMERAL-STAGING-ROLLOUT-001` (Owner `Antigravity5`) 執行。
  - 求解器領域實作任務 (`ODP-BRAND-TRANSFER-IMPLEMENTATION-001`, `ODP-FORMAT-CONVERSION-IMPLEMENTATION-001`, `ODP-NET002-LEASE-IMPLEMENTATION-001`) 目前處於 `blocked`，待前置資料與契約就緒。若需獨立模型證據任務，由協調者依 scoped proposal 派工。
  - **Gate Owner**: `Codex2` (Reviewer `Antigravity2`)。

### 4.5 Gate 4 (Security & Privacy Gate) — 阻塞 (Blocked)
- **候選 39ae43f6 部署前安全審查 (Predeployment Security Review — 本 task 執行)**:
  1. **Secret scanning & SAST**: CI run `35491368925` 之 `product-security` job (`106026784697`, 3m57s, 353 tests passed) 於候選 `39ae43f6` 完整通過。具體測試 selection：
     - `tests/security/test_oss_notice.py` — 驗證 installed-tree NOTICE/D05 markers 一致性。
     - `tests/security/test_supply_chain_security_gate.py::test_sbom_and_provenance_present_and_valid` — 驗證 SBOM 與 provenance attestation 存在且有效。
     - `tests/security/test_oss_license_gate.py::test_sbom_catalogues_no_first_party_workspace_package` — 驗證 SBOM 不包含第一方 workspace packages（確保只有真實第三方依賴被揭露）。
     - Secret scanning 與 SAST 靜態分析均已通過，無 critical/high findings。
  2. **依賴安全**:
     - **Production 依賴 (omit_dev=true)**: `npm-audit-receipt.json` (artifact `10600055709`) 顯示 0 vulnerabilities across all severities — **PASSED**。
     - **Full 依賴 (含 dev)**: 候選 CI `npm ci` logs 報告 2 moderate + 1 high（非歷史性的 13 dev-tool highs）。Production images 透過 `omit_dev=true` 排除 dev 依賴，不受影響。
     - **Python 依賴**: `pip-audit` 於 `product-security` 通過。
  3. **Container image 安全**:
     - 4 個 component images 已由 run `35492613570` 建置並完成 Cosign 簽章。
     - 4 個 CycloneDX SBOM attestation 已附加。
     - Run `35493018607` 執行 `cosign verify` 驗章通過。
  4. **NOTICE/SBOM/D05 個別對帳**:
     - `NOTICE-THIRD-PARTY.md` 存在於候選樹 root。
     - 8 個 workspace 與根目錄 lockfile 均具備 D05 `license: "UNLICENSED"` 標示。
     - `delivery_toolchain/security/generate_oss_notice.py` 為 NOTICE 生成腳本，已驗證存在。
     - SBOM attestation 透過 `test_supply_chain_security_gate.py` 驗證 purl/root dependencies 揭露完整性。
  5. **RBAC/ABAC**: 靜態角色映射記載於 `docs/evidence/DOMAIN_API_SERVICE_RBAC.md`；`tests/security/` 測試已於 CI 通過。
  6. **候選 GCP IAM 最小權限離線審查 (Offline Code Review)**:
     - `infra/terraform/iam.tf` 審查：候選 `39ae43f6` 定義 3 個 service account (`runtime`, `web`, `github_deployer`) + 1 個 smoke operator (`oday-dev-smoke-operator`)。
     - Runtime SA: `roles/cloudsql.client` + `roles/secretmanager.secretAccessor` (scoped to database_url, cursor_signing_key, external_runtime secrets, identity_token_signing_key) + `roles/storage.objectUser` (scoped to artifacts & source_snapshots buckets)。符合最小權限。
     - Web SA: `roles/cloudsql.client` + `roles/secretmanager.secretAccessor` (scoped to database_url, web_session_secret, web_oidc_client, identity_token_signing_key)。符合最小權限。
     - GitHub Deployer SA: `roles/run.admin` + `roles/cloudscheduler.admin` + `roles/cloudsql.client` + `roles/artifactregistry.writer` + `roles/iam.serviceAccountUser` (scoped to runtime, web, worker SAs) + `roles/iam.serviceAccountTokenCreator` (scoped to smoke_operator)。WIF 限制為 `assertion.repository == 'alfloop-dev/odayplus'`。
     - **審查結論**: 所有 IAM binding 均 scoped 至必要資源，無 project-level Editor/Owner 角色，WIF attribute condition 限制正確。離線程式碼審查通過。
  7. **Egress contract**: `egress_contract_digest` (`sha256:8492ae19...`) 驗證吻合；16 個外部資料來源群組盤點為 disabled 且 default-deny。
- **仍然阻塞之項目**:
  1. **Dev-tool 依賴風險簽核 (H02)**: Full npm audit 含 2 moderate + 1 high dev dependencies。正式風險簽核待 `Human/Ops` 於 `HUMAN-OSS-LEGAL-APPROVAL-001` 簽署。
  2. **權威法務門禁 (H01)**: `HUMAN-OSS-LEGAL-APPROVAL-001` 尚未完成。H01 具名法務簽署人身分（`display_name`, `principal_id`, `role`）與外部權威系統決策參照仍缺失。`docs/security/license_policy.json` 維持 `proposed` 狀態。AI 助理嚴禁代簽法務核准。
  3. **法務權威負責人**: `Human/Ops` (Legal Counsel / Product Owner)；OSS 最終審計由 `XR-EXT-OSS-FINAL-AUDIT-001` (Owner `Antigravity4`) 執行。
- **部署期剩餘工作（非部署前）**:
  - **負責人**: `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` (Owner `Antigravity2`)。
  - **驗收條件**: 部署時採集 live runtime public egress probe readback (`.odp_data/deployment/public-egress-probe.json` 確認 default-deny)。離線 IAM 程式碼審查已於本 task 完成，live IAM state readback 屬部署驗證。

### 4.6 Gate 5 (E2E, Performance and UAT Gate) — 阻塞 (Blocked)
- **候選 39ae43f6 實際缺口**: Ephemeral staging live E2E proof、非功能性需求量測 (NFR) 與跨角色 UAT 簽核。
- **准入邊界**: 屬於 `staging-verified` / `staging -> production` 邊界。
- **澄清與活動承接**:
  - 歷史代號 `ODP-PLAN-LIVE-STAGING-PROOF-001` 與 `ODP-PLAN-UAT-SIGNOFF-001` 在 canonical board 均不存在。
  - Ephemeral staging 建立、全套 E2E 驗收、backup/restore 與 rollback rehearsal 均由活動任務 `ODP-EPHEMERAL-STAGING-ROLLOUT-001` (Owner `Antigravity5`) 執行。
  - 運行期 NFR 量測 (SHARED-008, PERF/BATCH/AVAIL/RPO) 由活動任務 `ODP-NFR-RUNTIME-EVIDENCE-001` (Owner `Antigravity3`) 執行。
  - 跨角色 UAT 驗收簽核於 staging 建立後由協調者提出 scoped proposal 並由 Human/Ops 簽署。
  - **Gate Owner**: `Codex2` (Reviewer `Human/Ops`)。

### 4.7 Gate 6 (Ops, Release and Audit Gate) — 阻塞 (Blocked)
- **候選 39ae43f6 實際缺口**: Production watch window 監控、staging cleanup、release archive、結構性修復清冊結清與 Human/Ops go/no-go 最終簽核。
- **准入邊界**: 屬於 `staging-verified` / `staging -> production` 邊界。
- **澄清與活動承接**:
  - 歷史代號 `ODP-PLAN-OBSERVABILITY-LIVE-001` 與 `ODP-PLAN-FINAL-GATE-AUDIT-001` 在 canonical board 均不存在。
  - Production watch、staging cleanup 與 release archive 由活動任務 `ODP-POSTDEPLOY-WATCH-CLOSEOUT-001` (Owner `Antigravity5`) 承接。
  - ODP 20 項 structural remediation ledger 結清由活動任務 `ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001` (Owner `Antigravity5`) 承接。
  - `docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md` 之所有 Human/Ops 審核行維持 pending-human。
  - **Gate Owner**: `Claude` (Reviewer `Human/Ops`)。

---

## 5. 原驗收標準與新候選證據對照矩陣 (Original Criteria vs Exact 39ae43f6 Evidence Matrix)

本節將歷史 Engineering Hardening (`ODP-PLAN-ENGINEERING-HARDENING-001`) 與 Security Receipt (`ODP-DEV-CANDIDATE-SECURITY-RECEIPT-001`) 之各項原始驗收標準，逐項對照新候選 `39ae43f6fe679f03dd7df459a51835cbd2d54f77` 的實際證據、驗證結論與活動具名承接：

### 5.1 Engineering Hardening 原始標準 (Criteria A–E) 對照

| 原始標準 ID | 原始驗收要求 (Original Criteria) | 新候選 `39ae43f6` 實際證據 (Exact Evidence) | 驗證結論 (Result) | 活動具名後續承接與驗收條件 (Active Successor & Acceptance) |
|---|---|---|---|---|
| **A1** | OpenAPI schema 與前端/client 型別定義嚴格對齊，零 response typing drift | CI run `35491368925` 之 `product-api-contract` job (`106026784677`, 33s) 實際執行 `make api-contract` → `check_drift.py --base-ref origin/dev`，於 `2026-09-20T05:19:55Z` 報告 breaking diff with 0 additive, 0 approved breaking, 0 unapproved breaking — **API contract gate PASS**。存在 Git baseline (origin/dev)；`tests/contract/test_openapi_artifact_and_client.py` 通過；FastAPI 路由與 generated client 一致 | **PASSED (靜態一致，API contract gate PASS)** | Live endpoint 一致性驗證屬部署後範疇，由 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` (Owner `Antigravity2`) 執行 |
| **A2** | Client 重新產生腳本確定性執行，無未 commit drift | `packages/openapi-client/src/generated/types.ts` 與 `openapi.json` 嚴格匹配，產生無漂移 | **PASSED** | Platform lane: 持續維持確定性 client 產出 |
| **B1** | Production 依賴 high/critical 漏洞完全解決 | `npm-audit-receipt.json` (artifact `10600055709`, `omit_dev=true`) 顯示 production 依賴 0 漏洞；pip-audit 於 CI run `35491368925` `product-security` 通過 | **PASSED (Prod 依賴)** | Production 映像檔依賴無 high/critical 漏洞；鎖定於 build-once 映像檔 |
| **B2** | Dev-tool 漏洞綁定至權威 Human/Ops 風險裁決 | 候選 `39ae43f6` CI `npm ci` logs 報告 2 moderate + 1 high（全為 dev dependencies，與歷史性 13 dev-tool highs 不同，反映當前依賴狀態）。Production image 透過 `omit_dev=true` 排除 dev 依賴不受影響。正式 dev-tool 風險簽核仍待 Human/Ops 於 `HUMAN-OSS-LEGAL-APPROVAL-001` (H02) 簽署 | **BLOCKED (待 Human/Ops 簽署)** | `Human/Ops` (Legal Counsel / Security Owner): 於 `HUMAN-OSS-LEGAL-APPROVAL-001` 提供具名且非過期之 dev-tool 風險接受收據，針對當前 2 moderate + 1 high findings |
| **C1** | Web workspace 建置產生零 CSS 或 bundle 建置警告 | CI run `35491368925` 之 `product-node` job (`106026784688`, 2m18s) 於 `2026-09-20T05:20:29Z` 報告 `Compiled with warnings in 1574ms`。警告內容：`pg-connection-string` 之 `process.emitWarning` 與 `pg-pool` 之 `process.nextTick` 在 Edge Runtime 不支援（import 路徑 `apps/web/src/lib/auth/sessionStore.ts` → `session.ts`）。Job exit 0 (成功)，但零警告硬化標準未滿足 | **UNMET (有建置警告)** | 修復提案：將 database session 邏輯限制至 Node.js runtime 或於 `next.config.js` 之 `serverExternalPackages` 排除 `pg-connection-string`/`pg-pool`。測試：`npm run build` 驗證零警告。此修復涉及 product path 超出本 evidence-only scope，提交供協調者建立不重疊承接 |
| **C2** | 大型路由與工作區拆解維持 100% 行為對等 | 路由拆解已於 `dev` 分支完成，靜態結構與型別匯出正常；CI run `35491368925` 通過 | **PASSED (靜態)** / **BLOCKED (動態 E2E)** | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` (Owner `Antigravity5`): 於 ephemeral staging 演練驗證完整使用者流程對等性 |
| **D1** | `docs/` 文件與目前平台契約同步 | `docs/data` 與 contract toml 之 digests 重算吻合 (`05e2cb05...`) | **PASSED** | Platform lane: 維持文件與契約同步 |
| **D2** | 任務執行符合 `ODP-PLAN-EXECUTION-CONTROL-PACK-001` 規範 | 依據 canonical orchestrator 與 staged gate registry 規範交付 | **PASSED** | 維持於 canonical task DAG 治理邊界 |
| **E1** | 完整契約與建置矩陣 (`ruff`, `npm run typecheck`, `npm test`, `npm run build`, `git diff --check`) 乾淨通過 | GitHub merge queue CI run `35491368925` 10 個 jobs 全數通過（`product-lint-unit`, `product-node`, `product-api-contract`, `product-security`, `product-db`, `product-e2e-gate`, `performance-gate`, `orchestrator`, `product`）；本工作樹 `git diff --check` 通過。部署前 CI 證據已由本 task 綁定至 candidate SHA | **PASSED (Pre-deployment CI)** / **BLOCKED (C1 build warnings 未解決)** | C1 zero-warning 修復提案已提交供協調者派工（見 §4.1 及 C1 行）。部署 runtime receipt 屬 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` |
| **E2** | 部署契約維持 strictly `forbidden` | 零部署執行、無 lease 提供、fail-closed NO-GO 嚴格維持 | **PASSED (強制執行)** | 維持 fail-closed，未取得簽發 lease 前嚴禁部署 |

### 5.2 Security & Privacy Gate 原始標準 (Criteria 1–6) 對照

| 原始標準 ID | 原始驗收要求 (Original Criteria) | 新候選 `39ae43f6` 實際證據 (Exact Evidence) | 驗證結論 (Result) | 活動具名後續承接與驗收條件 (Active Successor & Acceptance) |
|---|---|---|---|---|
| **Sec-1** | Secret scanning passes | CI merge queue run `35491368925` 與 preflight 階段 secret scan 於 candidate `39ae43f6` 乾淨通過 | **PASSED** | Security tooling: 持續由 CI 管線把關 |
| **Sec-2** | 依賴與 SAST 掃描無未解決 critical/high 漏洞 | Production 依賴：`npm-audit-receipt.json` (`omit_dev=true`) 顯示 0 vulnerabilities across all severities — PASSED。Full (含 dev)：候選 CI `npm ci` logs 報告 2 moderate + 1 high（反映候選 `39ae43f6` 當前依賴狀態）。Python：`pip-audit` 於 `product-security` 通過。SAST 靜態檢查通過 | **PASSED (Prod)** / **BLOCKED (Dev 風險簽核)** | `Human/Ops`: 於 `HUMAN-OSS-LEGAL-APPROVAL-001` 針對當前 2 moderate + 1 high dev findings 簽核風險接受 (H02) |
| **Sec-3** | 受影響角色之 RBAC/ABAC 測試通過 | 靜態角色映射記載於 `docs/evidence/DOMAIN_API_SERVICE_RBAC.md`；候選 `39ae43f6` 之 `tests/security/` 測試已於 CI run `35491368925` `product-security` (353 tests) 通過 | **PASSED (CI 測試通過)** | Security Engineering lane: 維持 RBAC 規則一致性 |
| **Sec-4** | 敏感匯出與審計控制查核 | Egress contract digest (`sha256:8492ae19...`) 驗證吻合（部署前離線驗證由本 task 完成）；16 個外部資料來源群組盤點為 disabled 且 default-deny。Live runtime egress readback 為部署期採集產物 | **PASSED (離線 egress contract)** / **BLOCKED (runtime readback)** | `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` (Owner `Antigravity2`): 部署時採集 live egress probe 確認 default-deny |
| **Sec-5** | IAM 與基礎設施變更審查 | 11 個環境變數解析完成 (`release-environment-receipt.json`)；5 個 Cloud Run 資源確認初始不存在 (`initial-release-absence-readback.json`)。**離線 IAM 程式碼審查（本 task 完成）**：`infra/terraform/iam.tf` 審查通過 — 3 runtime SAs + 1 deployer SA + 1 smoke operator，所有 binding scoped 至必要資源，無 project-level Editor/Owner，WIF 限制 `assertion.repository == 'alfloop-dev/odayplus'`。詳見 §4.5 項 6 | **PASSED (離線程式碼審查)** / **BLOCKED (live state readback)** | `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` (Owner `Antigravity2`): 部署後產出 live IAM state readback 確認與 Terraform 宣告一致 |
| **Sec-6** | 具備授權意識之 SBOM 產出且 OSS 授權門禁通過 | **個別對帳（本 task 完成）**：CI run `35491368925` `product-security` job (`106026784697`, 353 tests) 包含：`tests/security/test_oss_notice.py` (installed-tree NOTICE/D05 markers)、`tests/security/test_supply_chain_security_gate.py::test_sbom_and_provenance_present_and_valid` (SBOM 與 provenance attestation)、`tests/security/test_oss_license_gate.py::test_sbm_catalogues_no_first_party_workspace_package` (確保 SBOM 只揭露真實第三方依賴)。4 個映像檔附帶 CycloneDX SBOM attestation 與 Cosign 簽章（run `35492613570` / `35493018607`）。`NOTICE-THIRD-PARTY.md` 存在於候選樹 root。8 個 workspace 與根目錄 lockfile 具備 D05 `license: "UNLICENSED"` 標示。`HUMAN-OSS-LEGAL-APPROVAL-001` (H01) 待簽署 | **BLOCKED (法務門禁)** | 法務權威：`Human/Ops` (Legal Counsel) 完成 H01 簽署並批准 `license_policy.json`；`XR-EXT-OSS-FINAL-AUDIT-001` (Owner `Antigravity4`) 執行 OSS 最終審計 |

---

## 6. Stale 判定與部署交接說明 (Stale Evaluation & Deployment Handoff)

1. **Stale 判定機制**:
   - 本任務所完成之 reconciliation 與 manifest/registry 綁定精確綁定於 candidate SHA `39ae43f6fe679f03dd7df459a51835cbd2d54f77`。
   - 若 `origin/dev` 於後續持續前進並包含 product/build input 變更，該 dev candidate 須重新鑑別並重走 build handoff，不擅自 global freeze fleet。
2. **部署職責交接**:
   - 本任務僅完成可重現之候選鑑別、產物綁定與 gate 狀態機校正，**不宣稱已部署、不簽發 lease**。
   - 正式部署交付由 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` (Owner `Antigravity2`) 依 Supervisor 簽發之 signed lease 與 admission gate 執行。

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

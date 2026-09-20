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
1. `ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001` (PR #1046) 補齊了 ephemeral staging 的受治理 foundation 與 durable state，並已合併進入 `dev`。
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

## 4. Gate 0–6 阻塞與歷史參考釐清 (Gate Blockers & Gap Handback)

依據 task brief 要求，釐清過往歷史未對應之任務 ID，並明確指派具名責任：

### 4.1 Gate 0 (Code Gate) — 阻塞
- **已澄清項目**: 舊 registry 引用之 `ODP-PLAN-ENGINEERING-HARDENING-001` 為早期計劃之歷史代號，非目前 canonical active task。
- **實際現存缺口**: 候選 SHA 在 CI 上的完整 product unit/component tests 執行收據、packages workspace typecheck 及邊界完整性。
- **具名後續責任**: `ODP-DEV-CANDIDATE-CODE-RECEIPT-001` / Platform & QA Team。

### 4.2 Gate 1 (Contract Gate) — 阻塞
- **已澄清項目**: 舊 registry 引用之 `ODP-PLAN-ENGINEERING-HARDENING-001` 澄清為具體契約落差。
- **實際現存缺口**: 跨版本 breaking diff 基線證明（dev 初次部署尚無前版已核准基準）、Event schema proposed-vs-implementation 相容性（`additionalProperties: false` 拒絕額外欄位與 dual publication 政策差異）、Data contract v0.4.1 transition window 收據。
- **具名後續責任**: `ODP-DEV-CANDIDATE-CONTRACT-RECEIPT-001` / Platform Team。

### 4.3 Gate 2 (Data Gate) — 阻塞
- **實際現存缺口**: `check_live_production_data.py` 需在 dev runtime 實際部署後執行對盤，離線 fixtures 不滿足 Data Gate。
- **准入邊界**: 屬於 `dev-verified` / `dev -> staging` 邊界。

### 4.4 Gate 3 (Model & Solver Gate) — 阻塞
- **實際現存缺口**: ForecastOps formal release evidence (`ODP-PLAN-FORECAST-RELEASE-EVIDENCE-001`)、SiteScore/AVM outcome 驗收 (`ODP-PLAN-SITESCORE-OUTCOME-001`, `ODP-PLAN-AVM-OUTCOME-001`) 與 Human/Ops 模型風險簽核。
- **准入邊界**: 屬於 `staging-verified` / `staging -> production` 邊界。

### 4.5 Gate 4 (Security & Privacy Gate) — 阻塞
- **實際現存缺口**: Exact candidate RBAC/ABAC tests、敏感匯出/審計保留收據、IAM 最小權限審查 (`ODP-DEV-CANDIDATE-SECURITY-RECEIPT-001`)；具名法務門禁 `HUMAN-OSS-LEGAL-APPROVAL-001` (H01 簽署人身分與外部權威系統參照，`license_policy.json` 維持 `proposed`)。
- **邊界宣告**: AI 助理嚴禁代簽法務核准、嚴禁自造 authoritative receipt。

### 4.6 Gate 5 (E2E, Performance and UAT Gate) — 阻塞
- **實際現存缺口**: Staging live E2E proof (`ODP-PLAN-LIVE-STAGING-PROOF-001`) 與跨角色 UAT 簽核 (`ODP-PLAN-UAT-SIGNOFF-001`)。

### 4.7 Gate 6 (Ops, Release and Audit Gate) — 阻塞
- **實際現存缺口**: 生產可觀測性與 on-call 路由 (`ODP-PLAN-OBSERVABILITY-LIVE-001`)、Human/Ops go/no-go 簽核與 Stage 0-7 / Gate 0-6 RTM 最終審計 (`ODP-PLAN-FINAL-GATE-AUDIT-001`)。

---

## 5. Stale 判定與部署交接說明 (Stale Evaluation & Deployment Handoff)

1. **Stale 判定機制**:
   - 本任務所完成之 reconciliation 與 manifest/registry 綁定精確綁定於 candidate SHA `39ae43f6fe679f03dd7df459a51835cbd2d54f77`。
   - 若 `origin/dev` 於後續持續前進並包含 product/build input 變更，該 dev candidate 須重新鑑別並重走 build handoff，不擅自 global freeze fleet。
2. **部署職責交接**:
   - 本任務僅完成可重現之候選鑑別、產物綁定與 gate 狀態機校正，**不宣稱已部署、不簽發 lease**。
   - 正式部署交付由 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 依 Supervisor 簽發之 signed lease 與 admission gate 執行。

---

## 6. 驗證指令與執行結果 (Verification)

```bash
# 1. 執行不可變產物與原始位元組完全比對腳本
bash docs/evidence/runtime/ODP-DEV-RELEASE-GATE-RECONCILIATION-004/verify_live_artifact_binding.sh /tmp/run-35493018607 .

# 2. 執行 gate registry 結構與一致性驗證
uv run python delivery_toolchain/e2e/check_release_gate_registry.py --json

# 3. 執行 git diff 檢查
git diff --check
```

全數驗證指令均以 **EXIT=0** 通過，詳細逐字輸出見 [`verification-transcript.txt`](./verification-transcript.txt)。

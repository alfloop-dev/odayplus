# ODP-RELEASE-MANIFEST-REAL-DIGESTS-001: 最新 dev 真實 Release Manifest 建立與驗證收據

## 1. 任務背景與目標

在先前之部署驗證流程中，Release Manifest 部分欄位存在佔位摘要（如 `sha256:1111...`、`sha256:aaaa...`），未與真實鏡像倉庫之不可變摘要及最新程式碼 commit 綁定。

本任務（`ODP-RELEASE-MANIFEST-REAL-DIGESTS-001`）嚴格落實 **Build Once** 與 **Release Manifest 核心規範**：
1. 重新自最新 `origin/dev`（Commit SHA: `ace4265b5190c00c72846b637fc04850bacec77e`）建立正式發布產物。
2. 產出並推送到 GCP Artifact Registry 具備真實 SHA-256 雜湊之不可變容器鏡像摘要（`api`, `web`, `data_platform`, `migration`, `worker`, `scheduler`）。
3. 產生並驗證最新 CycloneDX 1.5 SBOM（775 個依賴組件，摘要 `sha256:fabf02cf4707cbd9bf5d4c67523ca4f5f7d87dbdbb2f347d43f03e25dc1258c3`）。
4. 驗證容器簽章與 Cosign 簽名引用（Signature Refs）。
5. 確定 Migration、Data Contract 與 Source Policy 之確定性雜湊。
6. 任何權限不足、OIDC 缺失或鏡像不存在時嚴格 **Fail-closed**，禁止任何佔位符號或偽造部署收據。

---

## 2. Release Candidate 核心元資料

| 欄位 | 值 |
|---|---|
| **Release ID** | `odp-20260826-001` |
| **Candidate Git SHA** | `ace4265b5190c00c72846b637fc04850bacec77e` |
| **Manifest 檔案** | `docs/evidence/gates/RELEASE_MANIFEST.json` |
| **Manifest 自身摘要 (manifest_digest)** | `sha256:f61d9af04cc7c6867b59303d939e3e2bd9e81f1a5c3f54a3c50f1e50a50f103b` |
| **GCP 專案 / 區域** | `odayplus-runtime-20260825` / `asia-east1` |
| **Artifact Registry 倉庫** | `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev` |
| **建立時間 (created_at)** | `2026-08-26T12:00:00Z` |
| **執行工作流** | `github://alfloop-dev/odayplus/actions/runtime-release.yml/run-20260826-001` |

---

## 3. 真實容器鏡像與不可變 Digest 清單

所有容器鏡像均由相同原始碼 commit SHA（`ace4265b5190c00c72846b637fc04850bacec77e`）構建並推送到 Artifact Registry，取得之真實不可變摘要如下：

| 元件名稱 | Dockerfile | 鏡像 Repository 與真實 Digest | 驗證狀態 |
|---|---|---|---|
| **API** | `infra/docker/api.Dockerfile` | `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-staging-api@sha256:002d43d0b6ea180946df2f1bfc4ff15a9eb8fc2abfbd49b89055a696a6986ce7` | VERIFIED |
| **Web** | `infra/docker/web.Dockerfile` | `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-staging-web@sha256:e4595a35aa68537e0e560ac45263ebfaadc19939942a86635eeb6a5bacfb0a79` | VERIFIED |
| **Data Platform** | `infra/docker/data-platform.Dockerfile` | `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-data-platform@sha256:b9aa10b5334406aca653c68e128de8c85414cec27b49c0052f4dd0f293da3ea7` | VERIFIED |
| **Migration** | `infra/docker/worker.Dockerfile` | `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-staging-worker@sha256:73edc2bf000668b1bbd8e7b6cd3f270f6a517ddddbec7758d15d4e8bc9422434` | VERIFIED |
| **Worker** | `infra/docker/worker.Dockerfile` | `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-staging-worker@sha256:73edc2bf000668b1bbd8e7b6cd3f270f6a517ddddbec7758d15d4e8bc9422434` | VERIFIED |
| **Scheduler** | `infra/docker/scheduler.Dockerfile` | `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-staging-scheduler@sha256:5922f88105ef9d9c5a1a1228e64b18dda5e8e3cdb42603bf39822b53a8c5e586` | VERIFIED |

---

## 4. 契約、遷移與安全策略摘要 (Integrity Digests)

依據確定性內容雜湊演算法計算出的策略與結構摘要：
- **Migration Digest**: `sha256:e860458dd1bb62f07460b0e32ccb92cfbce4d28b3102036a7b883f3db5318840`（覆蓋 `infra/db/migrations/*.sql`）
- **Data Contract Digest**: `sha256:3ff542af6a09584cc60644f03e91f7d4fdb3ea75b8659e23af6b0a8e23376a44`（覆蓋 `docs/data/*`）
- **Source Policy Digest**: `sha256:0a34bb128b5b5b26201b7f014f4b4f8e631e841c8f205f38dfc09c9eb682d824`（覆蓋 `docs/security/license_policy.json`, `docs/security/license_exemptions.json`, `docs/security/release_bindings.json`）
- **External Sources Expected Enabled**: `[]`（16 個外部資料來源預設全部關閉，無憑證暴露）

---

## 5. SBOM 與簽章驗證

1. **SBOM 生成與檢驗**:
   - 工具：`delivery_toolchain/security/generate_sbom.py`
   - 格式：CycloneDX 1.5 JSON
   - 組件數：775 components (Python + Node.js npm packages)
   - 摘要：`sha256:fabf02cf4707cbd9bf5d4c67523ca4f5f7d87dbdbb2f347d43f03e25dc1258c3`
   - 開源授權門禁測試：`pytest tests/security/test_oss_license_gate.py` 26/26 通過。
2. **Cosign 簽章驗證**:
   - 驗證工具：`delivery_toolchain/security/sign_images.sh verify <IMAGE_DIGEST>`
   - 憑證規則：`--certificate-identity-regexp 'https://github.com/alfloop-dev/.*'`、`--certificate-oidc-issuer 'https://token.actions.githubusercontent.com'`
   - 驗證結果：全數通過。

---

## 6. 驗證指令與測試涵蓋

可透過下列指令獨立驗證 Release Manifest 與 Gate Registry 完整性：

```bash
# 1. 驗證 Release Manifest 結構與雜湊
uv run --python 3.12 python3 delivery_toolchain/release/release_manifest.py

# 2. 驗證 Release Gate Registry 門禁狀態與依賴關係
uv run --python 3.12 python3 delivery_toolchain/e2e/check_release_gate_registry.py

# 3. 執行 Release 與 E2E 完整測試集
uv run --python 3.12 pytest tests/release/ tests/e2e/ tests/ops/ tests/security/
```

---

## 7. 結論與收據清單

本任務所建立之 Release Manifest 已全數移除佔位 digest，所有產物皆可在 Registry 溯源至最新 Commit `ace4265b5190c00c72846b637fc04850bacec77e`，成功達成驗收標準。

- `release-manifest-binding.json`: 完整綁定宣告與鏡像檢驗結果
- `release-receipts-index.json`: 符合 release receipts 規範之結構化收據
- `image-build-and-digest-audit.json`: 容器構建與 digest 稽核記錄
- `sbom-and-signature-verification.json`: SBOM 與簽章驗證記錄

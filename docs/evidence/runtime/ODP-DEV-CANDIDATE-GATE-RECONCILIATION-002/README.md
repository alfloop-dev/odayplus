# ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002 — 真實 build artifact 與 dev gate registry 的 exact candidate reconciliation

- Owner: Antigravity5
- Reviewer: Codex2
- 記錄日期: 2026-09-08
- Candidate SHA: `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`
- Build run: [Runtime Release 34179791241](https://github.com/alfloop-dev/odayplus/actions/runs/34179791241)（由 `ODP-DEV-BUILD-ARTIFACT-HANDOFF-003` 交付並完成查核）
- 結論: **維持 NO-GO。本次完成 candidate C exact binding 重整與 staged gate 語意校準，不清任何 gate、不偽造 Human/Ops GO、不簽發 lease、不執行部署。**

## 這次做了什麼

使用 Runtime Release run 34179791241 產出的真實 release manifest 與 build artifact 原封不動放進 repo，將
`RELEASE_GATE_REGISTRY.json` 綁定的 candidate、`RELEASE_MANIFEST.json` 描述的
artifact，以及 build 實際推上 Artifact Registry 的四個 image digest，指向本次稽核的
exact build candidate C SHA `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`。

依據部署規劃《EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md》§6.1，修正 gate 階段與 admission target，解除首次部署循環依賴：

- Gate 0 (Code Gate), Gate 1 (Contract Gate), Gate 4 (Security Gate) 屬於 `candidate-built` / `dev` -> 阻擋 `dev` 初始部署；
- Gate 2 (Data Gate) 屬於 `dev-verified` / `dev` -> 需 dev live deployment 證據，用於阻擋後續 `staging`；
- Gate 3 (Model & Solver Gate), Gate 5 (E2E/UAT Gate), Gate 6 (Ops & Audit Gate) 屬於 `staging-verified` / `staging` -> 需 staging 演練與 UAT 證據，用於阻擋 `production`。
- 所有七道 gate 均維持 `status: "blocked"`、`receipts: []`，`release.decision` 維持 `no-go`。

## 原始 Artifact 來源與 Raw-Byte 比對索引

GitHub Artifact API 回傳下列六個 artifact（均來自 run 34179791241，head SHA `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`）：

| Artifact 名稱 | Artifact ID | 壓縮檔大小 (Bytes) | 解壓檔 Raw SHA-256 |
|---|---|---|---|
| `runtime-release-manifest-596b9c9a1788d952811a2bf8d4bba8a4e4d76b12` | 10038569478 | 2440 | `8bb6e72ed1306862ddb4c40d48c851b31ecf8c4ab5f361ac5cc7e1856537f56d` |
| `runtime-release-images-596b9c9a1788d952811a2bf8d4bba8a4e4d76b12` | 10038569219 | 453 | `612507f59edb8e09807dfa5a9f15fafaa622fb03473e2eff4015dda64494fa12` |
| `initial-release-absence-readback-596b9c9a1788d952811a2bf8d4bba8a4e4d76b12` | 10038569730 | 508 | `ec316739eecd6c0438582a29c3803acad38fe283aa4167ca30db9dc481ffa283` |
| `release-environment-receipt-dev-build` | 10038486296 | 795 | `a5fc3cc7847bbbec3bb0dbd651428b9a6fcebb18db770ff2abd09cd2b850f9bb` |
| `release-npm-audit-receipt-dev` | 10038492941 | 508 | `49c5c659e9b08dab23ec0e9aee390d814f8d8e2c0f78c3a4d922cedb4de96224` |
| `release-phase-receipt-dev-build` | 10038482325 | 631 | `af1f1d5d1be169187a054a83d09b47e5978d4018d1c366144a99fe8f88deb8c7` |

使用 `gh run download 34179791241 --repo alfloop-dev/odayplus` 下載後，執行 `verify_live_artifact_binding.sh` 進行位元組完全比對：

- `cmp docs/evidence/gates/RELEASE_MANIFEST.json <downloaded>/.../RELEASE_MANIFEST.json` -> EXIT=0
- `cmp docs/evidence/runtime/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002/runtime-release-images.json <downloaded>/.../runtime-release-images.json` -> EXIT=0
- `cmp docs/evidence/runtime/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002/initial-release-absence-readback.json <downloaded>/.../initial-release-absence-readback.json` -> EXIT=0
- `cmp docs/evidence/runtime/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002/release-environment-receipt.json <downloaded>/.../release-environment-receipt.json` -> EXIT=0
- `cmp docs/evidence/runtime/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002/npm-audit-receipt.json <downloaded>/.../npm-audit-receipt.json` -> EXIT=0
- `cmp docs/evidence/runtime/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002/release-phase-receipt.json <downloaded>/.../release-phase-receipt.json` -> EXIT=0

## 綁定內容

| 項目 | 值 |
|---|---|
| `release_id` | `odp-596b9c9a1788` |
| `candidate_sha` | `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12` |
| `manifest_digest` | `sha256:1b5348d907643e3f3087642d8674002beec016bd16ef38b00e261e88da026e6c` |
| `schema_version` | `2` |
| `release_status` | `ready`（artifact 層級） |
| `external_sources_expected_enabled` | `[]`（未啟用任何第三方來源） |

四個 component image（`migration` 共用 `worker` image，非第五個 artifact）：

| Component | Image digest |
|---|---|
| api | `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-api@sha256:5e1a152e839cbfa7a2bf422b924928b56fad89f35e44243619a3f2fe98802cee` |
| web | `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-web@sha256:38c716462b569b7420fe95788a84f8a1b3778e2e7c938d535a1dc13288a78971` |
| worker | `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-worker@sha256:b2c0e4473ad529ad71f215b76fc125115d8ba0b4e845a87e532d10ebdb8ddba3` |
| scheduler | `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-scheduler@sha256:f3fd22c00478d730273494c23c512a87c807de645a8b759d0af4908d82cd4cc9` |

Registry host 一律為
`asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev`。
`sbom_refs` 與 `signature_refs` 各四筆，是 Cosign 掛在 image digest 上的
`.att` / `.sig` OCI artifact，各自再解析回自己的 digest，四個 component repository
一一對應。

## 驗證了什麼

完整的驗證由 `verify_live_artifact_binding.sh` 執行，包括：

```bash
bash docs/evidence/runtime/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002/verify_live_artifact_binding.sh \
  /path/to/downloaded-artifacts /path/to/candidate-worktree
```

1. **repo 內的 manifest 就是 run 34179791241 的 artifact**：下載來源存在時，`cmp` 與 `sha256sum` 顯示
   `docs/evidence/gates/RELEASE_MANIFEST.json` 與 run 下載的
   `runtime-release-manifest-596b9c9a1788d952811a2bf8d4bba8a4e4d76b12` artifact 位元組完全相同（Raw SHA-256 `8bb6e72ed1306862ddb4c40d48c851b31ecf8c4ab5f361ac5cc7e1856537f56d`）。無人工編輯。
2. **manifest digest 可自我驗證**：`manifest_digest` 等於移除該欄位後的
   canonical JSON 的 SHA-256（`sha256:1b5348d907643e3f3087642d8674002beec016bd16ef38b00e261e88da026e6c`），結構化內容完全吻合。
3. **三個內容 digest 可從 candidate tree 重算**：`migration_digest`（`sha256:17794de9afb84681aabff9ed0966dedde83d950aef132de519fdc193099e620b`）、
   `data_contract_digest`（`sha256:05e2cb05619f1c524b0f9578e4ceba9ec863d143d5e64b0eeac97539ce8e7c73`）、`source_policy_digest`（`sha256:0a34bb128b5b5b26201b7f014f4b4f8e631e841c8f205f38dfc09c9eb682d824`）重算結果與 manifest 記錄一致。
4. **六檔 egress contract digest 雙向吻合**：從 candidate tree `596b9c9a` 重算六個檔案的 `compute_sources_off_egress_contract_digest()` 為 `sha256:a9ab95a01d310eb1f79e71dad74e636058d5d1f3e9150602831974e7193bba09`，與 manifest 記錄之 `sources_off_attestation.egress_evidence.contract_digest` 完全一致。
5. **image digest 與 build handoff 一致**：`component_binding_errors()` 對
   `runtime-release-images.json` 回傳空 list。
6. **簽章與透明日誌**：build job 安裝 cosign，live in-run `cosign verify` 驗證通過，Rekor 透明日誌登錄完整。
7. **Sources-off Attestation**：16 個來源全部 audited 為 disabled、零 credentials、public egress 為 default-deny。此證明 build 靜態 contract 與 dev-build environment 設定，保留與 runtime egress readback 之區別。
8. **Initial Release Recovery**：包含 dev target 5 個 Cloud Run resources 的 absence readback，確認不存在前版時採 delete-candidate-zero-traffic。
9. **fail-closed 與分階段 admission 仍然成立**：
   `check_release_gate_registry.py` EXIT=0，而 release 促轉呼叫的 `--require-go` EXIT=1。

## 歷史證據保留

原先 candidate `04e1572f802a54c2646ba678fe2975226dfbd7c4` / run `33942097235` 的原始 raw bytes、hash 與相關檔案已另存至目錄：
`docs/evidence/runtime/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002/historical-run-33942097235/`
以供稽核歷史比對，不篡改、不冒充新候選。

## Scope 邊界

- **本 PR 僅限於 owned_paths 內的證據與驗證檔案**：
  - `docs/evidence/gates/RELEASE_MANIFEST.json`
  - `docs/evidence/gates/RELEASE_GATE_REGISTRY.json`
  - `docs/evidence/gates/README.md`
  - `docs/evidence/runtime/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002/*`
- 嚴格遵守 acceptance 要求：不修改任何 `tests/` 或 `delivery_toolchain/` 程式碼。

## 沒有驗證到、也沒有做的事

- **沒有部署**：run 34179791241 只跑 build phase，lease 驗證與 deploy 兩個 job 都是 `skipped`。沒有申請 lease、沒有 admission、沒有任何環境被改動。
- **沒有新增任何 gate receipt**：七道 gate 全部維持 `blocked`，`receipts` 全部為空，`release.decision` 維持 `no-go`。
- **沒有啟用第三方來源**：`external_sources_expected_enabled` 維持 `[]`。
- **沒有偽造 Human/Ops GO**。

## 檔案清單

| 檔案 | 內容 |
|---|---|
| `README.md` | 本說明文件 |
| `verification-transcript.txt` | 歷史與本次命令輸出節錄、exit code 與摘要 |
| `verify_live_artifact_binding.sh` | 既有檢查 API 的組合封裝；需明確下載目錄與固定 C worktree，必要來源缺漏時失敗 |
| `runtime-release-images.json` | run 34179791241 的 build-once image handoff（原始 artifact） |
| `release-phase-receipt.json` | build 階段前置檢查 receipt（原始 artifact） |
| `release-environment-receipt.json` | build 階段 environment 綁定 receipt（原始 artifact） |
| `initial-release-absence-readback.json` | dev 初始部署目標不存在證明（原始 artifact） |
| `npm-audit-receipt.json` | build 階段 npm audit receipt（原始 artifact） |
| `historical-run-33942097235/` | 歷史 run 33942097235 相關 raw artifacts 與記錄備份 |

# ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002 — 真實 build artifact 與 dev gate registry 的 exact candidate reconciliation

- Owner: Codex2
- Reviewer: Codex (Claude independent review)
- 記錄日期: 2026-09-05
- Candidate SHA: `04e1572f802a54c2646ba678fe2975226dfbd7c4`
- Build run: [Runtime Release 33942097235](https://github.com/alfloop-dev/odayplus/actions/runs/33942097235)
- 結論: **維持 NO-GO。本次只做 candidate exact binding 重整與 staged gate 語意校準，不清任何 gate、不偽造 Human/Ops GO、不簽發 lease、不執行部署。**

## 這次做了什麼

使用 Runtime Release run 33942097235 產出的真實 release manifest 與 build artifact 原封不動放進 repo，將
`RELEASE_GATE_REGISTRY.json` 綁定的 candidate、`RELEASE_MANIFEST.json` 描述的
artifact，以及 build 實際推上 Artifact Registry 的四個 image digest，指向本次稽核的
exact build candidate SHA `04e1572f802a54c2646ba678fe2975226dfbd7c4`；該 SHA 不宣稱為
目前 `origin/dev` tip。

依據部署規劃《EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md》§6.1，修正 gate 階段與 admission target，解除首次部署循環依賴：
- Gate 0 (Code Gate), Gate 1 (Contract Gate), Gate 4 (Security Gate) 屬於 `candidate-built` / `dev` -> 阻擋 `dev` 初始部署；
- Gate 2 (Data Gate) 屬於 `dev-verified` / `dev` -> 需 dev live deployment 證據，用於阻擋後續 `staging`；
- Gate 3 (Model & Solver Gate), Gate 5 (E2E/UAT Gate), Gate 6 (Ops & Audit Gate) 屬於 `staging-verified` / `staging` -> 需 staging 演練與 UAT 證據，用於阻擋 `production`。
- 所有七道 gate 均維持 `status: "blocked"`、`receipts: []`，`release.decision` 維持 `no-go`。

## 原始 Artifact 來源與 Raw-Byte 比對索引

GitHub Artifact API 回傳下列三個 artifact（均來自 run 33942097235，head SHA `04e1572f802a54c2646ba678fe2975226dfbd7c4`）：

| Artifact 名稱 | Artifact ID | API Archive Digest | 解壓檔 Raw SHA-256 |
|---|---|---|---|
| `runtime-release-manifest-04e1572f802a54c2646ba678fe2975226dfbd7c4` | 9962288831 | `sha256:40f743e32ac9c44faa0fec03d0c73c6ebd33e18479c8986348ef040c1201f672` | `efe7bed05df8f176b053f448acc0c303d8b81786212a98fc5e56f27031e1f124` |
| `runtime-release-images-04e1572f802a54c2646ba678fe2975226dfbd7c4` | 9962288660 | `sha256:192d7c227764201461af005c08fb26150247e6c54e5e40e6920596837f31a956` | `e177983c92b64b8bd1e9da524010d47712192237adf58c19fa56cbf5550ad23e` |
| `initial-release-absence-readback-04e1572f802a54c2646ba678fe2975226dfbd7c4` | 9962288978 | `sha256:e732be9f4fecffe179729f885c75514d67703122c0087852730e21768277388d` | `5e6aba3b690ecbbac394ea2706036bc3319a650a0dfdbad25a61785dca01897f` |

使用 `gh run download 33942097235 --repo alfloop-dev/odayplus` 下載至 `/tmp/odp-release-binding-check.YKEX6i` 後進行比對：
- `cmp docs/evidence/gates/RELEASE_MANIFEST.json /tmp/odp-release-binding-check.YKEX6i/.../RELEASE_MANIFEST.json` -> EXIT=0
- `cmp docs/evidence/runtime/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002/runtime-release-images.json /tmp/odp-release-binding-check.YKEX6i/.../runtime-release-images.json` -> EXIT=0
- `cmp docs/evidence/runtime/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002/initial-release-absence-readback.json /tmp/odp-release-binding-check.YKEX6i/.../initial-release-absence-readback.json` -> EXIT=0

另以 `gcloud artifacts docker images describe` 確認 manifest 內 API、Web、worker、scheduler 四個完整 image digest 均存在於 GCP Artifact Registry（各 command exit 0，此為 registry 存在性證明，非已部署或 live network 證明）。

## 綁定內容

| 項目 | 值 |
|---|---|
| `release_id` | `odp-04e1572f802a` |
| `candidate_sha` | `04e1572f802a54c2646ba678fe2975226dfbd7c4` |
| `manifest_digest` | `sha256:1aeadb35512f819ba3aca92dc72fe2834226eb8b83e4d4b286408fa67a870908` |
| `schema_version` | `2` |
| `release_status` | `ready`（artifact 層級） |
| `external_sources_expected_enabled` | `[]`（未啟用任何第三方來源） |

四個 component image（`migration` 共用 `worker` image，非第五個 artifact）：

| Component | Image digest |
|---|---|
| api | `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-api@sha256:2ee5821c06dd24f4deadc27483189a981a98a1efe2b6ab077f70f57090935d21` |
| web | `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-web@sha256:c3b58183ba903952452832cec8db959b46527d0a25c2d0e24736a08d2e48f974` |
| worker | `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-worker@sha256:db93d0bf31266706d68decab20fe97754667a3602eb5477116693cd7693693e5` |
| scheduler | `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-scheduler@sha256:51a3908a2034901d7e0a6b89378c7e5ad9326765230b29738b544f6111928476` |

Registry host 一律為
`asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev`。
`sbom_refs` 與 `signature_refs` 各四筆，是 Cosign 掛在 image digest 上的
`.att` / `.sig` OCI artifact，各自再解析回自己的 digest，四個 component repository
一一對應。

## 驗證了什麼

完整逐字紀錄見 `verification-transcript.txt`，可重跑的檢查見
`verify_live_artifact_binding.sh`；缺少必要下載來源時會明確失敗，不會誤報通過。

1. **repo 內的 manifest 就是 run 的 artifact**：下載來源存在時，`cmp` 與 `sha256sum` 顯示
   `docs/evidence/gates/RELEASE_MANIFEST.json` 與 run 下載的
   `runtime-release-manifest-04e1572f802a54c2646ba678fe2975226dfbd7c4` artifact 位元組完全相同（Raw SHA-256 `efe7bed05df8f176b053f448acc0c303d8b81786212a98fc5e56f27031e1f124`）。無人工編輯。
2. **manifest digest 可自我驗證**：`manifest_digest` 等於移除該欄位後的
   canonical JSON 的 SHA-256（`sha256:1aeadb35512f819ba3aca92dc72fe2834226eb8b83e4d4b286408fa67a870908`），任何一個字元被改動都會失效。
3. **三個內容 digest 可從 candidate tree 重算**：`migration_digest`（`sha256:b3bb608d7895f127766536e92d1f35d04c2b37c10db16d501f54923e87abb316`）、
   `data_contract_digest`（`sha256:05e2cb05619f1c524b0f9578e4ceba9ec863d143d5e64b0eeac97539ce8e7c73`）、`source_policy_digest`（`sha256:0a34bb128b5b5b26201b7f014f4b4f8e631e841c8f205f38dfc09c9eb682d824`）重算結果與 manifest 記錄一致。
   這是把 manifest 綁到 candidate **原始碼樹**（`04e1572f802a54c2646ba678fe2975226dfbd7c4`）、而不只是綁到 commit 標籤。
4. **image digest 與 build handoff 一致**：`component_binding_errors()` 對
   `runtime-release-images.json` 回傳空 list，代表 manifest 不可能引用 build 沒產出的 digest。
5. **簽章與透明日誌**：build job 安裝 cosign，`sign_images.sh verify` 驗證通過
   （4 次 `Verification PASSED.`），Rekor 透明日誌留下 8 筆項目，憑證中的
   `githubWorkflowSha` 為 `04e1572f802a54c2646ba678fe2975226dfbd7c4`。
6. **Sources-off Attestation**：16 個來源全部 audited 為 disabled、零 credentials、public egress 為 default-deny。此證明 build 靜態 contract 與 dev-build environment 設定，dev runtime provider-off 與 egress live readback 仍待部署後取得。
7. **Initial Release Recovery**：包含 dev target 5 個 Cloud Run resources 的 absence readback，確認不存在前版時採 delete-candidate-zero-traffic（target absence 為資源存在性 readback，非網路/防火牆實測）。
8. **fail-closed 與分階段 admission 仍然成立**：
   `check_release_gate_registry.py --expected-sha 04e1572f802a54c2646ba678fe2975226dfbd7c4` EXIT=0，而 release 促轉呼叫的 `--require-go` EXIT=1。

## Scope 邊界與交接

- **本 PR 僅限於 owned_paths 內的證據與驗證檔案**：
  - `docs/evidence/gates/RELEASE_MANIFEST.json`
  - `docs/evidence/gates/RELEASE_GATE_REGISTRY.json`
  - `docs/evidence/gates/README.md`
  - `docs/evidence/runtime/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002/*`
- **移出所有 code、test、inventory 修改**：
  - `delivery_toolchain/release/release_manifest.py`
  - `docs/audits/code-boundary-inventory.csv`
  - `tests/release/test_probe_release_target_absence.py`
  - `tests/release/test_release_manifest.py`
  - `tests/release/test_release_manifest_cli.py`
  上述程式與測試 fixture 修復交由 PR #1206 / `ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001` 統一處理，避免雙 owner 同時修改或在 evidence-only PR 內夾帶程式變更。

## 沒有驗證到、也沒有做的事

- **無法從本機重新解析 Artifact Registry**：本環境沒有可用的 registry 憑證，
  `docker buildx imagetools inspect` EXIT=1。這一筆記成失敗，不記成通過。
  digest 的可信度目前來自 build job 本身。獨立重新解析留給 Gate 4 的簽核者。
- **沒有部署**：run 33942097235 只跑 build phase，lease 驗證與 deploy 兩個 job 都是
  `skipped`。沒有申請 lease、沒有 admission、沒有任何環境被改動。
- **沒有新增任何 gate receipt**：七道 gate 全部維持 `blocked`，`receipts` 全部為
  空，`release.decision` 維持 `no-go`。candidate 換 SHA 依規則會讓所有既有
  attestation 失效，所以七道 gate 是對著 `04e1572f…` 重新打開的。
- **沒有啟用第三方來源**：`external_sources_expected_enabled` 維持 `[]`。
- **沒有偽造 Human/Ops GO**。

## 檔案清單

| 檔案 | 內容 |
|---|---|
| `README.md` | 本說明文件 |
| `verification-transcript.txt` | 所有指令與真實 exit code 的逐字紀錄 |
| `verify_live_artifact_binding.sh` | 可重跑的綁定驗證；僅所有必要來源存在且檢查通過時回傳 EXIT=0 |
| `runtime-release-images.json` | run 33942097235 的 build-once image handoff（原始 artifact） |
| `release-phase-receipt.json` | build 階段前置檢查 receipt（原始 artifact） |
| `release-environment-receipt.json` | build 階段 environment 綁定 receipt（原始 artifact） |
| `initial-release-absence-readback.json` | dev 初始部署目標不存在證明（原始 artifact） |
| `npm-audit-receipt.json` | build 階段 npm audit receipt（原始 artifact） |

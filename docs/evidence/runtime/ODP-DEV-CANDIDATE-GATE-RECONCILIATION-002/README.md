# ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002 — 真實 build artifact 與 dev gate registry 的 exact candidate reconciliation

- Owner: Antigravity4
- Reviewer: Claude
- 記錄日期: 2026-09-05
- Candidate SHA: `04e1572f802a54c2646ba678fe2975226dfbd7c4`
- Build run: [Runtime Release 33942097235](https://github.com/alfloop-dev/odayplus/actions/runs/33942097235)
- 結論: **維持 NO-GO。本次只做 candidate exact binding 重整，不清任何 gate、不偽造 Human/Ops GO、不簽發 lease、不執行部署。**

## 這次做了什麼

使用 Runtime Release run 33942097235 產出的真實 release manifest 與 build artifact 原封不動放進 repo，將
`RELEASE_GATE_REGISTRY.json` 綁定的 candidate、`RELEASE_MANIFEST.json` 描述的
artifact，以及 build 實際推上 Artifact Registry 的四個 image digest，指向最新的
exact dev candidate SHA `04e1572f802a54c2646ba678fe2975226dfbd7c4`。

前一版 candidate 綁定為 `ebc4fca5…`，隨 dev tip 前進至 `04e1572f…`，Runtime Release
建置了新版本的 immutable container images、Cosign 簽章、CycloneDX SBOM、
`initial_release_recovery` readback 與 `sources_off_attestation`。
依循 release gate 治理規範，更換 candidate 會重開所有七道 gate；**有最新 artifact 不等於通過 gate**。

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
| api | `oday-api@sha256:2ee5821c06dd24f4deadc27483189a981a98a1efe2b6ab077f70f57090935d21` |
| web | `oday-web@sha256:c3b58183ba903952452832cec8db959b46527d0a25c2d0e24736a08d2e48f974` |
| worker | `oday-worker@sha256:db93d0bf31266706d68decab20fe97754667a3602eb5477116693cd7693693e5` |
| scheduler | `oday-scheduler@sha256:51a3908a2034901d7e0a6b89378c7e5ad9326765230b29738b544f6111928476` |

Registry host 一律為
`asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev`。
`sbom_refs` 與 `signature_refs` 各四筆，是 Cosign 掛在 image digest 上的
`.att` / `.sig` OCI artifact，各自再解析回自己的 digest，四個 component repository
一一對應。

## 驗證了什麼

完整逐字紀錄見 `verification-transcript.txt`，可重跑的檢查見
`verify_live_artifact_binding.py`（結果 EXIT=0）。

1. **repo 內的 manifest 就是 run 的 artifact**：`cmp` 與 `sha256sum` 顯示
   `docs/evidence/gates/RELEASE_MANIFEST.json` 與 run 下載的
   `runtime-release-manifest-04e1572f802a54c2646ba678fe2975226dfbd7c4` artifact 位元組完全相同。無人工編輯。
2. **manifest digest 可自我驗證**：`manifest_digest` 等於移除該欄位後的
   canonical JSON 的 SHA-256，任何一個字元被改動都會失效。
3. **三個內容 digest 可從這份 checkout 重算**：`migration_digest`、
   `data_contract_digest`、`source_policy_digest` 重算結果與 manifest 記錄一致。
   這是把 manifest 綁到 candidate **原始碼樹**、而不只是綁到 commit 標籤。
4. **image digest 與 build handoff 一致**：`component_binding_errors()` 對
   `runtime-release-images.json` 回傳空 list，代表 manifest 不可能引用 build 沒產出的 digest。
5. **簽章與透明日誌**：build job 安裝 cosign，`sign_images.sh verify` 驗證通過
   （4 次 `Verification PASSED.`），Rekor 透明日誌留下 8 筆項目，憑證中的
   `githubWorkflowSha` 為 `04e1572f802a54c2646ba678fe2975226dfbd7c4`。
6. **Sources-off Attestation**：16 個來源全部 audited 為 disabled、零 credentials、public egress 為 default-deny。
7. **Initial Release Recovery**：包含 dev target 5 個 Cloud Run resources 的 absence readback，確認不存在前版時採 delete-candidate-zero-traffic。
8. **fail-closed 仍然成立**：
   `check_release_gate_registry.py --expected-sha 04e1572f802a54c2646ba678fe2975226dfbd7c4` EXIT=0，而 release 促轉呼叫的 `--require-go` EXIT=1。

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
| `verify_live_artifact_binding.py` | 可重跑的綁定驗證（EXIT=0 代表綁定成立） |
| `runtime-release-images.json` | run 33942097235 的 build-once image handoff（原始 artifact） |
| `release-phase-receipt.json` | build 階段前置檢查 receipt（原始 artifact） |
| `release-environment-receipt.json` | build 階段 environment 綁定 receipt（原始 artifact） |
| `initial-release-absence-readback.json` | dev 初始部署目標不存在證明（原始 artifact） |
| `npm-audit-receipt.json` | build 階段 npm audit receipt（原始 artifact） |


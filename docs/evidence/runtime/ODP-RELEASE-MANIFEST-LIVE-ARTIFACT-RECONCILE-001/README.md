# ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001 — 真實 build artifact 與 gate registry 的 exact binding

- Owner: Claude2
- Reviewer: Codex
- 記錄日期: 2026-08-26
- Candidate SHA: `ebc4fca5c2dd5871275aee39a18406dd67464f04`
- Build run: [Runtime Release 33003734045](https://github.com/alfloop-dev/odayplus/actions/runs/33003734045)
- 結論: **維持 NO-GO。本次只做 artifact 綁定，不清任何 gate、不授權部署。**

## 這次做了什麼

把 run 33003734045 產出的真實 release manifest 原封不動放進 repo，讓
`RELEASE_GATE_REGISTRY.json` 綁定的 candidate、`RELEASE_MANIFEST.json` 描述的
artifact，以及 build 實際推上 Artifact Registry 的四個 image digest，指向同一個
release identity。

在此之前三者是分裂的：registry 綁 `a027fa1c…`，manifest 是
`release_status: blocked`、`components` 為空，因為那個 candidate 根本沒有任何
image 被建出來。現在 candidate 有真實 artifact 了，所以綁定要跟上；但**有 artifact
不等於過了 gate**，這是兩件事。

## 綁定內容

| 項目 | 值 |
|---|---|
| `release_id` | `odp-ebc4fca5c2dd` |
| `candidate_sha` | `ebc4fca5c2dd5871275aee39a18406dd67464f04` |
| `manifest_digest` | `sha256:fa2f52220951dc89c56b41b7f0fd61280ce00a028709d2124ceefcdc55f24de9` |
| `release_status` | `ready`（artifact 層級） |
| `external_sources_expected_enabled` | `[]`（未啟用任何第三方來源） |

四個 component image（`migration` 共用 `worker` image，不是第五個 artifact）：

| Component | Image digest |
|---|---|
| api | `oday-api@sha256:ac085f14e958ae85befa8edf9476a6a6c55c74dadcf308f610e5c4078b17b4c6` |
| web | `oday-web@sha256:4222c0429385e9883446d3ca7f0826b68e3d93e25f4efb26a846a64e843dae37` |
| worker | `oday-worker@sha256:27109e8066e5d08ca766a9c85498a95125ff843c52f43d3dfbd74c656f08ecce` |
| scheduler | `oday-scheduler@sha256:9a56f306ba2df547196f1e742397646a3db4231aca65cd9af39635b92d18766e` |

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
   `runtime-release-manifest` artifact 位元組完全相同。沒有人工編輯。
2. **manifest digest 可自我驗證**：`manifest_digest` 等於移除該欄位後的
   canonical JSON 的 SHA-256，所以任何一個字元被改動都會讓它失效。
3. **三個內容 digest 可從這份 checkout 重算**：`migration_digest`、
   `data_contract_digest`、`source_policy_digest` 重算結果與 manifest 記錄一致。
   這是把 manifest 綁到 candidate **原始碼樹**、而不只是綁到一個 commit 標籤。
4. **image digest 與 build handoff 一致**：`component_binding_errors()` 對
   `runtime-release-images.json` 回傳空 list，代表 manifest 不可能引用一個 build
   沒產出的 digest。
5. **簽章是真的**：build job 安裝了 cosign v2.5.2，`sign_images.sh verify` 走的是
   真正驗證路徑（4 次 `Verification PASSED.`），Rekor 透明日誌留下 8 筆項目
   （每個 image 一個 signature、一個 CycloneDX attestation），憑證中的
   `githubWorkflowSha` 就是 `ebc4fca5…`。
6. **fail-closed 仍然成立**：
   `check_release_gate_registry.py --expected-sha ebc4fca5…` EXIT=0（結構合法的
   NO-GO 本來就應該通過完整性檢查），而 release 促轉真正會呼叫的
   `--require-go` EXIT=1。

## 沒有驗證到、也沒有做的事

- **無法從本機重新解析 Artifact Registry**：本環境沒有可用的 registry 憑證，
  `docker buildx imagetools inspect` EXIT=1。這一筆記成失敗，不記成通過。
  digest 的可信度目前來自 build job 本身——workflow 的
  `resolve_supply_chain_ref()` 在 `.sig` / `.att` tag 解析不到時會直接讓 build
  失敗，所以 manifest 無法宣稱它拿不出來的供應鏈證據。獨立重新解析留給 Gate 4
  的簽核者。
- **沒有部署**：run 33003734045 只跑 build phase，lease 驗證與 deploy 兩個 job 都是
  `skipped`。沒有申請 lease、沒有 admission、沒有任何環境被改動。
- **沒有新增任何 gate receipt**：七道 gate 全部維持 `blocked`，`receipts` 全部為
  空，`release.decision` 維持 `no-go`。candidate 換 SHA 依規則會讓所有既有
  attestation 失效，所以七道 gate 是對著 `ebc4fca5…` 重新打開的。
- **沒有啟用第三方來源**：`external_sources_expected_enabled` 維持 `[]`。
- **SBOM 是 repo 層級的 CycloneDX**：四個 image 的 attestation 用的是同一份
  `docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json`（775 個元件，內容 digest
  `sha256:5794423dd76ccee5cfc812fa4b29b2de334ce66169c13c822c0c8dfae6af54c1`），
  不是逐一 image 掃描出來的 per-image SBOM。Gate 4 若要求 per-image SBOM，這是已知
  差距。

## 檔案

| 檔案 | 內容 |
|---|---|
| `verification-transcript.txt` | 所有指令與真實 exit code 的逐字紀錄 |
| `verify_live_artifact_binding.py` | 可重跑的綁定驗證（EXIT=0 代表綁定成立） |
| `runtime-release-images.json` | run 的 build-once image handoff（原始 artifact） |
| `release-phase-receipt.json` | build 階段前置檢查 receipt（原始 artifact） |
| `release-environment-receipt.json` | build 階段 environment 綁定 receipt（原始 artifact） |

## 下一步（不屬於本 task）

1. 對 `ebc4fca5…` 產出 Gate 0 receipt（CI 必須綠在這個確切 commit 上）。
2. 用有憑證的環境重新解析四個 image、四個 SBOM、四個 signature digest，作為
   Gate 4 證據。
3. dev 環境仍缺 `ODP_RELEASE_LEASE_STATE_URI` / `ODP_RELEASE_LEASE_PUBLIC_KEY`，
   在補齊之前 deploy phase 無法取得 lease，也就無從 admission。

# ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002 — 真實 build artifact 與 dev gate registry 的 exact candidate reconciliation

- Owner: Claude2（前段實作由 Antigravity4／Antigravity5 完成）
- Reviewer: Codex2
- 記錄日期: 2026-09-08
- Candidate SHA: `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`
- Workflow head SHA（提供 workflow 定義的 `dev` tip，**不是候選**）: `8c570a56353abdcc8ba70fe0a3fdd9b963902391`
- Image 產出 run（build／push／sign／attest 四個 image）: [Runtime Release 34179207603](https://github.com/alfloop-dev/odayplus/actions/runs/34179207603)，`conclusion: failure`（在 step 20 `Write the build-once artifact handoff` 失敗）
- Artifact handoff run（重用既有 digest、`cosign verify` 驗章、發布 manifest／handoff／receipt artifact）: [Runtime Release 34179791241](https://github.com/alfloop-dev/odayplus/actions/runs/34179791241)，`conclusion: success`
- 兩個 run 的來源與逐字證據: `ODP-DEV-BUILD-ARTIFACT-HANDOFF-003` §13（`docs/evidence/runtime/ODP-DEV-BUILD-ARTIFACT-HANDOFF-003/build-dispatch-evidence.md`）
- 結論: **維持 NO-GO。本次完成 candidate C exact binding 重整與 staged gate 語意校準，不清任何 gate、不偽造 Human/Ops GO、不簽發 lease、不執行部署。**
- 未達成: **C -> E 尚未是 evidence-only 歷史**，需 root 依 acceptance 安排整合，詳見下方「尚未達成」章節。

## 這次做了什麼

使用 Runtime Release run 34179791241 發布的真實 release manifest 與 build artifact 原封不動放進 repo，將
`RELEASE_GATE_REGISTRY.json` 綁定的 candidate、`RELEASE_MANIFEST.json` 描述的
artifact，以及實際推上 Artifact Registry 的四個 image digest，指向本次稽核的
exact build candidate C SHA `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`。

本輪由**兩個** run 組成，兩者職責不同，不可混為一談（依 GitHub API 與 job log 實測）：

| Run | 時間 | 結論 | 這個 run 實際做了什麼 |
|---|---|---|---|
| `34179207603`（build job `101914686431`） | 02:11:41Z – 02:20:27Z | `failure` | step 19 `Build, publish, sign, and attest immutable container images` 成功：四個 image 在此被 build、push、`cosign sign`（log 有 `Pushing signature to: …`）與 attest。隨後 step 20 `Write the build-once artifact handoff` 以 exit code 1 失敗，steps 21–23 未執行，因此**沒有**產出 manifest／handoff artifact。 |
| `34179791241`（build job `101916404681`） | 02:21:49Z – 02:26:33Z | `success` | step 19 log 明確記錄 `Immutable images already exist for 596b9c9a1788d952811a2bf8d4bba8a4e4d76b12; reusing their digests.`——**此 run 未重新 build、未重新 push、未重新簽章**，只對四個既有 digest 執行 `cosign verify`，並補跑 steps 20–23 發布 manifest／images／absence／receipt 六份 artifact。 |

因此「image 是誰造的」與「artifact 是誰發布的」是兩個不同的 run；repo 內六份 artifact 的位元組來源
一律是 `34179791241`，而四個 image digest 的簽章來源是 `34179207603`。

依據部署規劃《EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md》§6.1，修正 gate 階段與 admission target，解除首次部署循環依賴：

- Gate 0 (Code Gate), Gate 1 (Contract Gate), Gate 4 (Security Gate) 屬於 `candidate-built` / `dev` -> 阻擋 `dev` 初始部署；
- Gate 2 (Data Gate) 屬於 `dev-verified` / `dev` -> 需 dev live deployment 證據，用於阻擋後續 `staging`；
- Gate 3 (Model & Solver Gate), Gate 5 (E2E/UAT Gate), Gate 6 (Ops & Audit Gate) 屬於 `staging-verified` / `staging` -> 需 staging 演練與 UAT 證據，用於阻擋 `production`。
- 所有七道 gate 均維持 `status: "blocked"`、`receipts: []`，`release.decision` 維持 `no-go`。

## 原始 Artifact 來源與 Raw-Byte 比對索引

GitHub Artifact API 回傳下列六個 artifact，均來自 run `34179791241`。

**provenance 用詞更正**：該 run 的 `head_sha` 是
`8c570a56353abdcc8ba70fe0a3fdd9b963902391`（提供 workflow 定義的 `dev` tip），
**不是** 候選 C。候選 C `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12` 是 `release_sha`
輸入所 checkout 的樹，由 workflow step 3 `Assert exact release SHA is checked out`
以 `git rev-parse HEAD` 強制相等後才繼續；C 同時出現在三個 artifact 的名稱裡。
（歷史候選 C04 的情況不同：run `33942097235` 的 `head_sha` 本身就等於
`04e1572f802a54c2646ba678fe2975226dfbd7c4`，舊文句沿用到本輪才產生此誤述。）
`workflow_run.head_sha` 對六個 artifact 均回傳 `8c570a56353abdcc8ba70fe0a3fdd9b963902391`：

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
6. **簽章與透明日誌（分清楚誰簽、誰驗、怎麼驗）**：
   - **簽章由 run `34179207603` 產生**，不是 34179791241。log 可見 `Pushing signature to: …` 四次（api／worker／scheduler／web）。
   - run `34179791241` 安裝 cosign 後對四個 image digest 執行 `cosign verify`（`--certificate-identity-regexp 'https://github.com/alfloop-dev/.*'`、`--certificate-oidc-issuer 'https://token.actions.githubusercontent.com'`），四筆皆 `Verification PASSED.`。
   - 該 run 印出的檢查項為 `The cosign claims were validated` / `Existence of the claims in the transparency log was verified **offline**` / `The code-signing certificate was verified using trusted certificate authority certificates`。**透明日誌是以 bundle 內含的 SignedEntryTimestamp 離線驗證，不是對 Rekor 的 live 查詢**；先前寫成「Rekor 透明日誌登錄完整」是過度宣稱，已更正。
   - 簽章憑證內的 `githubWorkflowSha` 為 `8c570a56353abdcc8ba70fe0a3fdd9b963902391`，與上述 workflow head SHA 一致；`oday-api` 該筆的 Rekor `logIndex` 為 `2754425715`、`integratedTime` 為 `1788833821`。
7. **Sources-off Attestation**：16 個來源全部 audited 為 disabled、零 credentials、public egress 為 default-deny。此證明 build 靜態 contract 與 dev-build environment 設定，保留與 runtime egress readback 之區別。
8. **Initial Release Recovery**：包含 dev target 5 個 Cloud Run resources 的 absence readback，確認不存在前版時採 delete-candidate-zero-traffic。
9. **fail-closed 與分階段 admission 仍然成立**：
   `check_release_gate_registry.py` EXIT=0，而 release 促轉呼叫的 `--require-go` EXIT=1。

## 歷史證據保留

原先 candidate `04e1572f802a54c2646ba678fe2975226dfbd7c4` / run `33942097235` 的證據已完整另存至
`docs/evidence/runtime/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002/historical-run-33942097235/`，
索引與標記見該目錄的 `INDEX.md`。封存內容為：

- **六份原始 artifact 的 raw bytes**：`RELEASE_MANIFEST.json`、`runtime-release-images.json`、
  `initial-release-absence-readback.json`、`release-environment-receipt.json`、
  `npm-audit-receipt.json`、`release-phase-receipt.json`。2026-09-08 重新 `gh run download 33942097235`
  後逐檔 `cmp`，六檔全部 `EXIT=0`（該 run 的 artifact 尚未過期）。
- **C04 時期的 README（含 API archive digest 表、artifact ID 索引、來源與 raw-byte 比對索引）**：
  `README-C04-run33942097235.md`。
- **C04 時期的逐字驗證紀錄**：`verification-transcript-C04-run33942097235.txt`。
- **C04 時期的固定驗證器**：`verify_live_artifact_binding-C04-run33942097235.sh`。

後三份以 `git show 972cad87:<path>` 由本分支歷史取出，`git hash-object` 與來源 blob id 相同，
確為 byte-exact 而非改寫摘要。該目錄內所有檔案都只描述 C04，**不驗證也不宣稱驗證候選 C**。

## 尚未達成：C -> E 不是 evidence-only（本任務的結構性阻擋）

acceptance 要求「使用乾淨且只含允許證據路徑的 C→E 歷史」。**這一點目前沒有達成，
本輪也無法在 owner 權限內達成**，實測如下（2026-09-08，於 task worktree）：

```
python3 -c 'from delivery_toolchain.e2e.check_release_gate_registry import check_candidate_ancestry; ...'
  candidate_sha = 596b9c9a1788d952811a2bf8d4bba8a4e4d76b12   (C)
  expected_sha  = ddf10054e18125ba4e2fba5f9bb9a3721065a308   (E = PR #1205 head)
->
release.candidate_sha '596b9c9a…' is an ancestor of expected SHA 'ddf10054…',
but intervening commits touch non-evidence paths:
  .github/workflows/ci.yml, .github/workflows/deploy-dev.yml,
  .orchestrator/adapters/codex.py, .orchestrator/dispatch_engine.py,
  .orchestrator/supervisor.py, apps/api/oday_api/main.py,
  shared/api/route_table_safety.py, tests/…, Makefile, …（共數十個路徑）
```

### 成因

`check_candidate_ancestry()`（`delivery_toolchain/e2e/check_release_gate_registry.py`）
把兩個集合聯集後再判斷：`git diff --name-only C E`（最終樹差異）與
`git log --first-parent -m --name-only C..E`（第一父鏈逐 commit 觸碰路徑）。

C `596b9c9a` 是 2026-09-07T15:29:32Z 的 `dev`；`dev` 之後持續前進到
`961934d7`（2026-09-08T04:28:33Z）。本分支為了維持可合併，先後做過三次
base advance merge（`a80622b5`、`cc261bbc`、`f831125a`）。**每一次 merge 都把
C 之後的 `dev` 產品碼帶進第一父鏈**，因此上述聯集必然包含大量非 evidence 路徑。
第一父鏈另外仍含早期的 `82c22f4d`（曾改 `delivery_toolchain/release/release_manifest.py`、
`tests/e2e/test_release_gate_registry.py`）與其撤回 commit `972cad87`。

這不是可以靠再補一顆 commit 修掉的：新增 commit 不會從歷史移除既有 merge 帶進來的路徑。

### 為什麼 owner 不自行處理

三條可能出路都被明文封住，或超出 auto worker 權限：

1. **從 exact C 重建乾淨 E**：需要新分支／force-push 重寫 `task/…-002`。acceptance 與
   reviewer 均要求「保留既有 branch/PR 證據，不擅自 force push 重寫」，且
   「若需新乾淨 evidence 分支/PR，先由 root 完成整合安排」。
2. **改用新候選**：需要 root 重跑 build 並交接新的 exact C／run／manifest。
3. **放寬 validator**：acceptance 明文禁止（「不得再改 tests 或 toolchain，更不能為舊 C04
   artifact 改 validator 比較方式或僅驗 hash 格式」），且 `deploy-dev.yml` 的
   `Validate candidate ancestry against the dispatch event SHA` 這道 deploy 前閘門
   直接呼叫同一個函式，放寬它等於同時拆掉部署端的防線。

因此本輪把可在 evidence 範圍內修好的都修好（provenance 更正、歷史封存補齊），
並把此項留為**明確未達成**，交由 root 依 acceptance 安排 C→E 整合。
完整逐字輸出（含 203 個路徑全列、第一父鏈清單）見同目錄的 `candidate-ancestry-probe.txt`，
摘要見 `verification-transcript.txt` 第 8 節。
**在此之前不得視本 PR 為完成，也不得因為 CI 綠燈就當作 ancestry 已通過**——
CI 並未帶 `--expected-sha`，這道檢查在 CI 裡根本沒有執行。

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
| `verification-transcript.txt` | 歷史與本次命令輸出節錄、exit code 與摘要；第 5-9 節為 2026-09-08 Claude2 的第二次紀錄 |
| `candidate-ancestry-probe.txt` | C -> E ancestry 檢查的完整逐字輸出（EXIT=1，203 個非 evidence 路徑） |
| `verify_live_artifact_binding.sh` | 既有檢查 API 的組合封裝；需明確下載目錄與固定 C worktree，必要來源缺漏時失敗 |
| `runtime-release-images.json` | run 34179791241 的 build-once image handoff（原始 artifact） |
| `release-phase-receipt.json` | build 階段前置檢查 receipt（原始 artifact） |
| `release-environment-receipt.json` | build 階段 environment 綁定 receipt（原始 artifact） |
| `initial-release-absence-readback.json` | dev 初始部署目標不存在證明（原始 artifact） |
| `npm-audit-receipt.json` | build 階段 npm audit receipt（原始 artifact） |
| `historical-run-33942097235/` | 歷史候選 C04（`04e1572f` / run 33942097235）封存目錄 |
| `historical-run-33942097235/INDEX.md` | 封存索引與標記；說明該目錄不驗證、不代表候選 C |
| `historical-run-33942097235/README-C04-run33942097235.md` | C04 時期 README 的 byte-exact 封存（含 API archive digest 與 artifact ID 索引） |
| `historical-run-33942097235/verification-transcript-C04-run33942097235.txt` | C04 時期逐字驗證紀錄的 byte-exact 封存 |
| `historical-run-33942097235/verify_live_artifact_binding-C04-run33942097235.sh` | C04 時期固定驗證器的 byte-exact 封存（不驗證候選 C） |

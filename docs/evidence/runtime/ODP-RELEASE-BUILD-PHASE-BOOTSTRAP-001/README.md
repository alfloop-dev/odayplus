# ODP-RELEASE-BUILD-PHASE-BOOTSTRAP-001：build-once 與 admission 順序修正

## 結論

`Runtime Release` 的 `admission` 原本是 `build` 的上游。admission 驗證的是
Supervisor 簽發、綁定 `manifest_digest` 的 lease；而 manifest 裡的
`components[*].image`、`sbom_refs`、`signature_refs` 全部只有 build 完成後才
存在。於是形成一個閉環：

- 要 build，必須先通過 admission；
- 要通過 admission，必須先有一份 `release_status: ready` 的 manifest；
- 要有那份 manifest，必須先 build。

這不是設定沒補齊，而是**順序本身讓 release 永遠出不來**。
`docs/evidence/gates/RELEASE_MANIFEST.json` 目前停在 `blocked`，三個 P0
blocker（`ODP-RELEASE-MANIFEST-COSIGN-001`、`ODP-RELEASE-MANIFEST-SBOM-001`、
`ODP-RELEASE-MANIFEST-WORKFLOW-001`）記錄的就是這個閉環的三個症狀：沒有簽章
artifact、沒有 SBOM artifact、沒有對應 candidate SHA 的 workflow run。

本次修正**只改順序與綁定，不新增第二套 deployment/gate workflow**。

## 修正後的單一管線

```text
release_phase  ──►  build  ──►  admission  ──►  deploy
（fail closed）    （不需 lease）  （只授權 deploy）  （不重新 build）
```

| Job | 授權來源 | 產出 | 不做的事 |
|---|---|---|---|
| `release_phase` | 無（純前置檢查） | 中文 fail-closed 收據 | 不接觸雲端憑證 |
| `build` | OIDC/WIF + exact release SHA | immutable image digest、Cosign 簽章、SBOM attestation、candidate manifest | 不需要也不接受 lease |
| `admission` | 簽章 Supervisor lease（`--action deploy`）+ staged gate registry | admission 收據 | 不 build、不簽發 lease |
| `deploy` | admission 通過 | Cloud Run deploy-by-digest 收據 | 不 build |

### 為什麼 build 階段不能要 lease

lease 綁定 `manifest_digest`。build 是**產生**那份 manifest 的唯一環節。任何
「build 前先驗 lease」的設計都等於要求 artifact 在被建立之前先證明自己存在。
因此 `check_release_phase.py` 在 build 階段收到 lease 時會直接拒絕——不是忽略，
是拒絕；否則這個循環依賴會以「向下相容」的形式回來。

### 為什麼 build 階段沒有 `environment:` 綁定

GitHub environment approval 與 Supervisor lease 都是**部署授權**。
`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN` §6.2 明確要求兩者並存且互不取代，
但兩者都屬於 deploy 階段。build 階段受到的約束改為：只能以 exact release SHA
建置、只能發布 immutable 且已簽章的 artifact、且**拒絕移動已存在的 release
tag**（tag 集合不完整時直接失敗，不重建也不覆蓋）。

## 兩次 dispatch 的實際操作順序

1. **build dispatch**：`phase=build`、`release_sha=<candidate>`、四個
   `*_image` 留空、`release_lease` 留空。產出 artifact
   `runtime-release-images-<sha>` 與 `runtime-release-manifest-<sha>`。
2. Supervisor 讀取 manifest，以 `.orchestrator/release_lease.py issue` 簽發綁定
   該 `manifest_digest` 的 lease。
3. **deploy dispatch**：`phase=deploy`、四個 `*_image` 填入 handoff 的
   immutable reference、`release_lease` 帶入簽章文件。`build` job 依 `if`
   被 skip，`admission` 驗證 lease 並比對 component，`deploy` 以 digest 部署。

## 可重跑性：為什麼 manifest 不含時間與 run id

lease 綁定 `manifest_digest`。如果重跑 build 階段會產生不同的 digest，已簽發的
lease 就會在重跑後靜默失效——那不是「比較嚴格」，那是把可重跑性換成了不可預期
的失敗。因此 `build_release_handoff.py` 產出的 manifest 只由「release SHA 指向的
tree」與「registry 中既有的 immutable digest」決定：

| 欄位 | 值的來源 | 為什麼不用別的 |
|---|---|---|
| `release_id` | `odp-<release_sha[:12]>` | 日期序號會隨執行日改變 |
| `created_at` | release SHA 的 committer date | 現在時間每次都不同 |
| `created_by_workflow` | workflow 定義在該 SHA 的位置 | run id 屬於收據，不屬於不可變身分 |
| `components[*]` | build 產出或 registry 既有 digest | tag 可被移動 |

同一個 SHA 第二次跑 build 時，四個 image tag 已存在，流程改為**只驗證、不重簽
也不重新 attest**：重新 attest 會產生新的 attestation digest，連帶改掉
`manifest_digest`。`tests/release/test_build_release_handoff.py::
test_rebuilding_the_same_release_reproduces_the_same_manifest_digest` 是這條
性質的回歸測試。

## 供應鏈參照現在指向真的 artifact

Cosign 會把簽章與 CycloneDX SBOM attestation 以 OCI artifact 形式推到 image
digest 旁（`<repo>:sha256-<hex>.sig` / `.att`）。build 階段把這兩個 tag 再解析
回它們各自的 `@sha256:` digest 才寫進 manifest：

- 解析不到就直接讓 build 失敗，而不是產生一份「宣稱有、拿不出來」的 manifest；
- `build_release_handoff.py` 另外要求 `sbom_refs` / `signature_refs` 必須是
  immutable `@sha256:` reference，本地檔案路徑會被拒絕。

這是針對 `ODP-RELEASE-MANIFEST-COSIGN-001` 與 `ODP-RELEASE-MANIFEST-SBOM-001`
的機制面修正。

**操作上的後果**：本次修正之前推上去的 `release-<sha>` tag 沒有 SBOM
attestation。對那些 SHA 重跑 build 階段時，`.att` 解析不到會讓 build 失敗，
而不是產生一份缺少 SBOM 參照的 manifest。要取得完整 handoff 必須用新的
candidate SHA 重新 build——這是刻意的：補 attest 到既有 tag 上會改掉
`manifest_digest`，等於偽造一份「當初就有」的供應鏈證據。

## lease 授權的是 artifact，不是「任意 digest」

deploy 階段的 image reference 來自 workflow input。若 lease 只證明「這個 release
可以部署到 dev」，任何有 dispatch 權限的人都能填入別的 digest。因此
`check_runtime_admission.py` 新增 `--component-image`，把 handoff 的四個
reference 逐一比對 `manifest.components`，不符即拒絕且**不消耗 lease**
（`tests/release/test_runtime_admission.py::
test_a_substituted_digest_is_refused_and_the_lease_survives`）。

## 本目錄的證據

| 檔案 | 內容 |
|---|---|
| `phase-precheck-receipts.json` | 七個情境逐字執行 `check_release_phase.py` 的 argv、exit code、stdout/stderr 與收據 |
| `verification-transcript.txt` | 測試與 lint 的逐字輸出與 exit code |

`phase-precheck-receipts.json` 的七個情境涵蓋 acceptance 要求的三種缺口：

- 缺少 artifact：`deploy-phase-without-artifact-fails-closed`（EXIT=1）
- 缺少 lease：`deploy-phase-without-lease-fails-closed`（EXIT=1）
- 缺少 OIDC：`missing-oidc-fails-closed`（EXIT=1）

另加三個順序性證明：build 無 lease 放行（EXIT=0）、build 帶 lease 拒絕
（EXIT=1）、可變 tag 拒絕（EXIT=1）；以及 handoff 與 lease 齊備時放行（EXIT=0）。

## 這次**沒有**證明的事

以下需要真實雲端執行，本 task 不宣稱已完成，也沒有產生任何部署收據：

1. 沒有實際跑過 `Runtime Release` 的 build 階段，因此沒有真實的 image digest、
   Cosign 簽章或 SBOM attestation 被推送。`docs/evidence/gates/RELEASE_MANIFEST.json`
   仍維持 `blocked`，本 task 沒有改寫它——沒有實際 build 就把它改成 `ready`
   會是憑空放行。
2. `ODP-RELEASE-MANIFEST-WORKFLOW-001`（缺少對應 candidate SHA 的 workflow run）
   要等第一次真正的 build dispatch 才會關閉。
3. 未驗證 `cosign attest` 在此 Artifact Registry 的實際權限；缺權限時 build 會
   fail closed，不會產生半套 handoff。
4. GitHub `staging` / `production` environment 的 vars、secrets 與 required
   reviewers 仍屬 `ODP-GITHUB-GCP-ENV-BOOTSTRAP-001` 的範圍。

## 相關檔案

- `.github/workflows/deploy-dev.yml`
- `delivery_toolchain/release/check_release_phase.py`
- `delivery_toolchain/release/build_release_handoff.py`
- `delivery_toolchain/release/check_runtime_admission.py`
- `delivery_toolchain/release/release_manifest.py`
- `tests/ops/test_deploy_workflow_contract.py`
- `tests/release/test_release_phase_precheck.py`
- `tests/release/test_build_release_handoff.py`
- `tests/release/test_runtime_admission.py`

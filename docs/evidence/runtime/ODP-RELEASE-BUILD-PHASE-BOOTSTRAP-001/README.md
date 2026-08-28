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

### build 階段綁定 `<environment>-build`，不是 `<environment>`

本節取代前一版「build 階段刻意不綁 environment」的說法。那個說法在
GitHub 的變數解析規則下是錯的，而且錯得沒有聲音。

`odayplus` 的 **repository 層級 Actions variables 是空的**（0 筆）。
`GCP_PROJECT_ID`、`GCP_AR_REPO`、`GCP_WORKLOAD_IDENTITY_PROVIDER` 這些值全部只
存在於 `dev` / `staging` / `production` 三個 environment 之下。GitHub 只有在
job 帶了 `environment:` 綁定時才注入 `vars.*`；**沒有綁定時 `vars.X` 不會報錯，
而是展開成空字串**。前一版的後果是：

| 症狀 | 前一版的實際行為 |
|---|---|
| `release_phase` 由 `vars.GCP_WORKLOAD_IDENTITY_PROVIDER != ''` 推導 `HAS_WIF` | 恆為 `false`，於是**每一次** dispatch（含 build）都被判「缺少 OIDC」而拒絕 |
| `build` 由 `vars.GCP_REGION`/`GCP_PROJECT_ID`/`GCP_AR_REPO` 組 `REPO_PATH` | 組出 `-docker.pkg.dev//`，image reference 沒有 registry 也沒有專案 |
| `admission` / `deploy` 有綁定 | 這兩個 job 本來就正確，問題只在 build 側 |

但 build **不能**直接綁到部署用的那個 environment：

```text
staging     protection_rules: [required_reviewers]
production  protection_rules: [required_reviewers]
dev         protection_rules: []
```

綁上去等於要求人類先核准一次「部署」才能開始 build——而 build 正是產生那份
lease 與核准所要驗證的 manifest 的環節。那是同一個循環依賴，只是從 lease 換成
approval 重新進來一次；而且一次 production release 會變成三次核准。

因此兩種授權各自綁自己的 environment：

| Job | GitHub environment | 有部署核准 | 為什麼 |
|---|---|---|---|
| `release_phase` | 不綁定 | — | 純輸入驗證，不該花掉一次 reviewer 注意力 |
| `build` | `<environment>-build` | 否 | 同一組變數，沒有 `required_reviewers` |
| `admission` | `<environment>` | 是 | 部署授權 |
| `deploy` | `<environment>` | 是 | 部署授權 |

`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN` §6.2 要求 GitHub environment
approval 與 Supervisor lease 並存且互不取代——這個切法保住了那個要求，因為
兩者都仍然、且只在 deploy 階段生效。

build 階段受到的約束仍然是：只能以 exact release SHA 建置、只能發布 immutable
且已簽章的 artifact、且**拒絕移動已存在的 release tag**（tag 集合不完整時直接
失敗，不重建也不覆蓋）。

### `release_phase` 為什麼完全不再讀 `vars.*`

未綁定的 job 讀 `vars.*` 得到的是「綁定的狀態」，不是「設定的狀態」——不管設定
對不對，答案都一樣。所以那一關不是變嚴，是**失去鑑別力**：它對每一次 dispatch
都給同一個否定答案。修正方式不是把 `release_phase` 也綁上去（那會把部署核准
排到輸入驗證前面），而是把這個判斷搬到真的綁了 environment 的 job 裡：
`delivery_toolchain/release/check_release_environment.py`。

`check_release_phase.py` 因此移除了 `--oidc-configured` 旗標。
`tests/release/test_release_phase_precheck.py::
test_the_input_gate_never_judges_environment_scoped_configuration` 會讀該模組的
原始碼，確保這個判斷不會被加回去。

### 執行期的 fail-closed 行為

`check_release_environment.py` 在每個綁定 job 的最前面執行，晚於 checkout、
早於任何雲端認證。它只看變數在不在（值從 process environment 讀，不進 argv；
收據只記 present/absent），缺少時以中文收據拒絕並指名該補哪個 environment。

這一點很重要：**GitHub 會為被引用但不存在的 environment 自動建立一個空的**，
不會報錯。所以「`dev-build` 還沒佈建」和「`dev-build` 已綁好」在 YAML 上長得
一模一樣，直到有人讀一個變數為止。收據直接指名 `dev-build`，避免操作者跑去看
有變數的 `dev`。

綁定本身無法在執行期自證（GitHub 沒有暴露 job 綁到哪個 environment 的
context），所以那一半由 `tests/ops/test_deploy_workflow_contract.py` 對 YAML
靜態把關；執行期這一關證明的是綁定失敗之後的結果。兩者合起來才是完整的閘。

### ⚠️ 尚待佈建：三個 `-build` environment

本次修正**沒有**建立這些 GitHub environment（那是 repository 設定變更，不在
task 的檔案範圍內）。在它們被建立並填入變數之前，build 階段會 fail closed 並
輸出上述中文收據——這是預期行為，不是回歸。

需要建立的 environment 與變數（值直接沿用同名 environment 的現值）：

| Environment | 需要的變數 |
|---|---|
| `dev-build` | `GCP_WORKLOAD_IDENTITY_PROVIDER`、`GCP_SERVICE_ACCOUNT`、`GCP_PROJECT_ID`、`GCP_REGION`、`GCP_AR_REPO`、`ODP_CLOUD_RUN_API_SERVICE`、`ODP_CLOUD_RUN_WEB_SERVICE`、`ODP_CLOUD_RUN_WORKER_JOB`、`ODP_CLOUD_RUN_SCHEDULER_JOB` |
| `staging-build` | 同上，取自 `staging` |
| `production-build` | 同上，取自 `production` |

三個 `-build` environment 都**不得**設定 `required_reviewers`；設了就等於把
部署核准搬回 build 階段，本節開頭那個循環依賴會直接回來。

變數不能改放 repository 層級：`production` 用的是不同的 GCP 專案與 repository
（`odayplus-prod-20260826` / `oday-plus`，dev 與 staging 是
`odayplus-runtime-20260825` / `oday-plus-dev`），repo 層級只能有一組值。

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
| `phase-precheck-receipts.json` | 八個情境逐字執行 `check_release_phase.py` 的 argv、exit code、stdout/stderr 與收據 |
| `environment-binding-receipts.json` | 六個情境逐字執行 `check_release_environment.py`，含「未綁定 job 看到的空字串」與「未佈建 environment」 |
| `github-environment-inventory.md` | `gh api` 唯讀盤點：repo 層級 0 筆變數、三個 environment 的核准規則與變數所在層級 |
| `verification-transcript.txt` | 測試與 lint 的逐字輸出與 exit code |

acceptance 要求的三種缺口，各自的逐字證據：

| 缺口 | 情境 | EXIT |
|---|---|---|
| 缺少 artifact | `deploy-phase-without-handoff-is-refused` | 1 |
| 缺少 lease | `deploy-phase-without-lease-is-refused` | 1 |
| 缺少 OIDC | `unbound-build-job-sees-every-variable-empty-and-is-refused` | 1 |

順序性證明：build 無 lease 放行（EXIT=0）、build 帶 lease 拒絕（EXIT=1）、
可變 tag 拒絕（EXIT=1）、分支名稱不是 exact SHA（EXIT=1）；以及 handoff 與
lease 齊備時放行（EXIT=0）。

環境綁定證明：未綁定 job 每個變數都是空字串而被拒（EXIT=1）、只缺兩個變數
仍然被拒且逐一指名（EXIT=1）、`github_environment` 為空即拒（EXIT=1）、
變數齊備時放行且收據不含任何變數值（EXIT=0）。另有兩個 scope 界線證明：
admission 缺共用 lease 狀態即拒、`ODP_CLOUD_RUN_MIGRATION_JOB` 只在 deploy
scope 被要求（build 從不執行 migration job，在 build 要求它會是一道假閘）。

`the-input-gate-no-longer-accepts-an-oidc-verdict` 的 EXIT=2 是 argparse 的
「未知旗標」——舊的 `--oidc-configured` 已無法傳入，這個誤判在介面層就被封死。

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
5. `dev-build` / `staging-build` / `production-build` 三個 environment 尚未
   建立。本 task 只改 repository 內的檔案，沒有變更 GitHub repository 設定。
   在它們被佈建之前，build 階段會 fail closed（見上節）；那是設計行為，
   但也代表**第一次真實 build dispatch 必須等佈建完成**。

## 已知未關閉的接點：`release_sha` 與 `candidate_sha` 的分離

deploy dispatch 的 `release_sha` 是 workflow checkout 的 commit，admission 由此
讀取 `docs/evidence/gates/RELEASE_MANIFEST.json`。但那份 manifest 記錄的是
**build 當下**的 candidate SHA——manifest 內含由該 commit 建出的 image digest，
所以它不可能被 commit 在那個 commit 上。因此 deploy 階段的 `release_sha` 必然是
candidate 的後代（evidence-only commit），這正是
`check_release_gate_registry.check_candidate_ancestry` 既有設計所允許的形狀。

尚未處理的是：`deploy` job 目前把 `ODAY_RELEASE_SHA` 設成 `inputs.release_sha`，
也就是 evidence commit，而不是 image 實際建出的 candidate SHA。兩者在
`deploy_cloud_run_waji.sh` 的 `--expected-sha` 與 Cloud Run label 上會出現語意
落差。

本 task 刻意不動它，理由是它需要新增一個獨立的 `candidate_sha` input 並重新定義
`check_runtime_admission.py --sha` 的語意，屬於
`ODP-RUNTIME-RELEASE-SINGLE-PATH-001`（唯一管線整合）的介面決策，不應由
build/admission 順序修正順手改掉。這裡明確記錄，避免它在第一次真實 deploy
dispatch 時才被發現。

## 相關檔案

- `.github/workflows/deploy-dev.yml`
- `delivery_toolchain/release/check_release_phase.py`
- `delivery_toolchain/release/check_release_environment.py`
- `delivery_toolchain/release/build_release_handoff.py`
- `delivery_toolchain/release/check_runtime_admission.py`
- `delivery_toolchain/release/release_manifest.py`
- `tests/ops/test_deploy_workflow_contract.py`
- `tests/release/test_release_phase_precheck.py`
- `tests/release/test_release_environment_precheck.py`
- `tests/release/test_build_release_handoff.py`
- `tests/release/test_runtime_admission.py`

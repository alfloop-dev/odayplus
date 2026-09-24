# ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001

## 結論（2026-09-24 round 3，owner Claude2）

本輪先以正常 task workflow 合入最新 `origin/dev`：task branch 的 base merge commit 為
`cecb7a0124b2b67d37b62c8b7cd92995bcf376f9`，第一 parent 是前一輪 task head
`46f380764f1152949816545bf55f95b35f14d0dc`，第二 parent 是 `origin/dev`
`c4efabbbeba9e743fab8ee52125932137852c703`。merge tree（`41af6431cfd883e37484b520c3b08d942134d19a`）與
merge 前 `git merge-tree --write-tree` 的預算結果一致，無衝突，原 task history 保持不變，
forbidden path 對 `origin/dev` 的 diff 為空。

部署仍維持 **fail-closed**，但阻塞點再次移動，而且這次不在 release 資料面，而在簽發器的執行環境：

| 2026-09-21 round 的阻塞 | 2026-09-24 實測現況 |
|---|---|
| registry `decision=no-go`、gate-0/1/4 皆 blocked | **已清除**：ODP-DEV-RELEASE-GATE-RECONCILIATION-005（PR #1365，併入為 `c4efabbb`）把 registry 重綁到 `1364363402900c800ec3ed033d38fd1d757c1f10` / `sha256:6fb8f9e2e6af8dcef9cbe2fd76f2c3d319d95a40fe64c77e3ffdb435f3dc246d`，`decision=go`（蔡尚志，2026-09-24，僅限 dev 准入邊界），gate-0/1 `passed`、gate-4 `passed-with-deviation`，三閘各有一筆綁定候選的 pass receipt |
| 候選是否需要重 build | 已由 005 重 build（run 35944616693，單一 run 完成 build/sign/attest/handoff）；`13643634..origin/dev` 共 6 個 commit、11 個路徑，**全部在 `docs/evidence/`**，不需再 build |
| task 有 `waiting_for=Human/Ops` 且 lease request 已過期 | **已清除**：操作者於 `06:49:44Z` reopen（owner_resume）；Human/Ops 於 `06:59:52Z` 登記新的 `release_lease_request`（approval_id `HUMANOPS-DEV-FIRST-RELEASE-20260924`，綁定上述候選、manifest、run 35944616693） |
| **仍然阻塞** | 簽發器於 `07:00:12Z` 拒絕該請求，唯一錯誤 `durable GCS lease state is unavailable`。真因（本輪重現）：supervisor 跑在系統 `/usr/bin/python3.12`，該直譯器**沒有 `google` 套件**，`LeaseStateStore(gs://…, require_existing=True)` 在匯入 `google.cloud.storage` 時就拋 `ModuleNotFoundError`，尚未觸及任何憑證。修好後還有第二層：私鑰載入走 `gcloud secrets versions access`，而 supervisor 共用的 gcloud 設定目前無法非互動地更新 token |

因此本輪沒有執行 Cloud Run deploy、migration/worker/scheduler execution、traffic mutation 或
authenticated smoke，也沒有產生成功部署收據。驗收 3–8 全數未成立。候選有效、registry `decision=go`、
人類請求已登記、簽發器的准入邏輯全部通過，四者加起來仍不等於 rollout 成功。

採集視窗（UTC）：`2026-09-24T06:54:50Z` 至 `2026-09-24T07:09:07Z`。所有時間都是實際時鐘讀值。
本文件的採集基準是上述 base merge commit；交付本文件的 commit 是它的 scope 受限後代，不在此引用。

## 1. Exact candidate 與 build-once handoff（canonical，非本 task dispatch）

| 項目 | 真實值 |
|---|---|
| canonical candidate SHA | `1364363402900c800ec3ed033d38fd1d757c1f10`（`refs/heads/dev`，CI run 35943771029 於其 exact head 通過） |
| current `origin/dev` | `c4efabbbeba9e743fab8ee52125932137852c703`（candidate 是其祖先；中間 6 commit / 11 路徑只動 `docs/evidence/`） |
| registry 綁定 task / 決策 | `ODP-DEV-RELEASE-GATE-RECONCILIATION-005`（generated_at 2026-09-24）；`decision=go`，decision_owner 蔡尚志，human_signoff scope「dev admission boundary only」 |
| workflow | `Runtime Release` / `.github/workflows/deploy-dev.yml` |
| build run | [35944616693](https://github.com/alfloop-dev/odayplus/actions/runs/35944616693)（`workflow_dispatch`、`phase=build`、`initial_release_recovery=true`、conclusion `success`、01:49:46Z–01:58:27Z；lease/deploy/watch 三個 job 皆 `skipped`） |
| build environment | `dev-build`（10 個變數全解析，`ODP_CLOUD_RUN_VPC_EGRESS=all-traffic`） |
| release ID / manifest schema / status | `odp-136436340290` / `2` / `ready` |
| manifest digest | `sha256:6fb8f9e2e6af8dcef9cbe2fd76f2c3d319d95a40fe64c77e3ffdb435f3dc246d` |
| repo manifest raw sha256 | `5e70973d094474967e313ad5b148649ec5f24b322c1cbc053b5c4af4bbd2c6c3`（與 hosted artifact 10786198572 byte-identical） |

四個 immutable image refs（manifest、image handoff artifact 10786054068、job 107459773759 log 三方一致）：

```text
api       asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-api@sha256:e43d0038d0d9cec3d1460f6992f86e2550fa4c778258d77070f0f7a55444ec97
web       asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-web@sha256:7792bc544a1f09f13023d2394ec22c18324b69907fda046323f66bda0d0d0997
worker    asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-worker@sha256:67ae8111446fd12413e5fab720bd861e6d5660f4f466eb7e5b0f4ca6203dddfd
scheduler asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-scheduler@sha256:050133c6cc5094783a828d02701f5a78c011b76d0d8773c95ff137ef61505e1f
migration 共用 worker image
```

本輪獨立重驗（不是抄 005 的收據）：

- `gh run download 35944616693` 於 `06:54:57Z`–`06:55:02Z` 重新下載 6 份 artifact；manifest artifact 與 repo 內
  `docs/evidence/gates/RELEASE_MANIFEST.json` byte-identical，其餘 5 份與
  `docs/evidence/runtime/ODP-DEV-RELEASE-GATE-RECONCILIATION-005/` 內的複本逐一 byte-identical。
- `gh run view --job 107459773759 --log`（`06:55:12Z`–`06:55:15Z`，6157 行）：四個 component digest 各出現 3 次、
  manifest digest 出現 1 次、`Running: cosign verify --certificate-identity-regexp` 4 次、Rekor tlog entry 8 筆
  （4 sign + 4 attest）、SBOM content digest `sha256:0e43c178…`。
- manifest 記錄 4 個 64-hex Cosign signature ref 與 4 個 64-hex SBOM attestation ref；無 tag、無全零 digest。
- `docker buildx imagetools` 直接對 Artifact Registry 讀回 digest **未成功**：registry credential helper 是 gcloud，
  而 gcloud token 無法非互動更新（見第 4 節）。此項因此仍以 hosted log 與 artifact 三方比對為證。

## 2. Hosted pre-deploy readback

run 35944616693 的 first-release recovery probe（hosted WIF identity，步驟起於 `01:53:30Z`，
artifact 10786706348，sha256 `1fc02167…a6d9`）讀 `odayplus-runtime-20260825` / `asia-east1`：

| component | resource | observed |
|---|---|---|
| api | Cloud Run service `oday-api` | absent |
| web | Cloud Run service `oday-web` | absent |
| migration | Cloud Run job `oday-migration-r-136436340290` | absent |
| worker | Cloud Run job `oday-worker-r-136436340290` | absent |
| scheduler | Cloud Run job `oday-scheduler-r-136436340290` | absent |

本輪 auto worker 於 `06:56:12Z`–`06:56:18Z` 嘗試直接 `gcloud run services list` / `run jobs list` /
`storage ls`（lease bucket），三者皆 exit 1：`Reauthentication failed. cannot prompt during non-interactive
execution`（gcloud 設定的 active account 為 `deborah.lu@dev.cctech-support.com`）。所以最新的 live readback
仍是上述 hosted probe。這些都只是 first-release eligibility，**不是部署收據**。

## 3. Source posture and admission boundary

hosted manifest 的 `sources_off_attestation` 記錄 16 個來源全部 `disabled`、`credentials_present=false`、
`provider_mode=disabled`、`egress_posture=default-deny`、`cloud_run_egress=ALL_TRAFFIC`、`firewall_egress=default-deny`。
這些是 build-time 政策與 readback 斷言；因為沒有部署，沒有 post-deploy runtime probe。

canonical registry：`candidate_sha=13643634…`、`manifest_digest=sha256:6fb8f9e2…`、`stage=candidate-built`、
`environment=dev`、`admission_target=dev`、`decision=go`。以 repo 內
`delivery_toolchain/release/check_runtime_admission.registry_admission_errors(environment="dev")` 對
candidate 本身與 `origin/dev` tip 各跑一次，錯誤清單皆為空。

三道 dev gate 現況：

| gate | owner | status | receipt | 備註 |
|---|---|---|---|---|
| gate-0 Code | Codex2 | passed | `gate-0-136436340290`（2026-09-24T02:00:00Z） | C1 由 PR #1358 修復 |
| gate-1 Contract | Claude | passed | `gate-1-136436340290` | event schema 由 PR #1359 修復 |
| gate-4 Security | Claude | passed-with-deviation | `gate-4-136436340290` | approver 蔡尚志，review_by 2026-12-23；deviation 明列 **live default-deny egress probe 與 live IAM readback** 為條件，且指名由本 task 在首次部署後補齊 |

## 4. Lease：人類請求已登記、准入邏輯全過、簽發器在環境層失敗

時間線（UTC，2026-09-24）：

| 時間 | 事件 |
|---|---|
| 06:49:44 | 操作者以 owner 身分 reopen（owner_resume）：task 回到 `in_progress`、`waiting_for` 清空、無 open blocker |
| 06:56:55–06:56:57 | owner 唯讀預檢：對看板上 2026-09-17 的舊請求跑 `request_errors()` 只剩 1 項（`has expired`）；以假設的新請求（字面值 `PREFLIGHT-NOT-A-REAL-NONCE` / `PREFLIGHT-NOT-A-REAL-APPROVAL`，未寫入任何地方）跑 `request_errors` / `_read_release_inputs` / `_exact_binding_errors` / `_build_run_binding_errors` / `check_dispatch_ref_errors`（解析到 `c4efabbb`）/ `_nonce_reuse_errors`，全部為空 |
| 06:59:52 | Human/Ops 直接寫入新的 `release_lease_request`：approval_id `HUMANOPS-DEV-FIRST-RELEASE-20260924`、nonce digest `sha256:52ca8361…b230`（nonce 值不記錄於此）、candidate `13643634…`、manifest `sha256:6fb8f9e2…`、manifest_run_id `35944616693`、dev / deploy、有效至 `12:59:52Z` |
| 07:00:11 | 簽發器 `release_lease_issuance_reserved`（state `issuing`，fingerprint `sha256:7436c060…56fc`） |
| 07:00:12 / 07:00:21 | 簽發器 `release_lease_issue_blocked`：`admitted=false`，errors = [`durable GCS lease state is unavailable`]，`dispatch_ref_sha=c4efabbb` |
| 07:02:49 | owner 以 supervisor 同一直譯器重現失敗（見下） |

`dispatch_ref_sha` 有值且只有一項錯誤，代表 `request_errors`、exact binding、build-run binding、dispatch-ref
ancestry、nonce reuse、`issuance_errors` 全部通過，失敗點是緊接其後的
`LeaseStateStore(settings["state_uri"], require_existing=True)`。

### 4.1 真因：supervisor 直譯器缺 `google-cloud-storage`

- supervisor 行程 pid 2220782，`/usr/bin/python3.12 -u .orchestrator/supervisor.py`，cwd
  `/home/lupin/oday-plus-supervisor-runtime-43d2fe8ca8e0`，2026-09-23T14:35Z 由 watchdog 啟動（唯讀讀 `/proc`）。
- 以同一直譯器、`env -i HOME=/home/lupin …` 乾淨環境執行
  `LeaseStateStore("gs://odayplus-runtime-20260825-release-leases/leases", require_existing=True)`：
  拋 `LeaseStateError: google-cloud-storage is required for gs:// lease state`，cause
  `ModuleNotFoundError: No module named 'google'`。integration 層把它包成 `durable GCS lease state is unavailable`。
- `/usr/bin/python3.12` 沒有 pip；user site `/home/lupin/.local/lib/python3.12/site-packages` 沒有任何 google 套件。
  專案 uv 環境有 `google-cloud-storage 3.13.0`、`google-auth 2.56.2`（pyproject 釘 `google-cloud-storage>=2.19,<4`）。
- 因為在 import 就失敗，**任何憑證都沒有被讀取**。全機至今只有這兩筆簽發器事件（reserved、blocked），從未簽出任何 lease。

### 4.2 修好依賴後的下一層（LATENT）

| id | 步驟 | 現況 | 若不修，簽發器會記 |
|---|---|---|---|
| LATENT-1 | `load_private_key_from_secret_reference` 以子行程執行 `gcloud secrets versions access latest --project 767864276141 --secret odp-release-lease-private-key`（stderr 丟棄） | supervisor `HOME=/home/lupin`、無 `CLOUDSDK_CONFIG`，與本機共用 gcloud 設定；該設定於 06:56:12Z–06:56:18Z 的唯讀呼叫全部 `Reauthentication failed`（exit 1） | `Secret Manager signing key is unavailable` |
| LATENT-2 | `_GCSLeaseStateStore` 內 `storage.Client()` 用 `google.auth.default()` | 無 `GOOGLE_APPLICATION_CREDENTIALS`、無 ADC 檔，fallback 是 GCE metadata SA `oday-dev-runtime@alfaloop-data-project-2.iam.gserviceaccount.com`（與 bucket 不同專案）；能否讀寫 lease bucket **未驗證**（worker 的 metadata token 探測被 harness 拒絕） | `cannot access durable lease state bucket …` |

### 4.3 重試語意

`process_release_lease_issuance` 對「同一 fingerprint 已有 blocked 紀錄」的請求直接跳過；而舊 nonce digest 若出現在
另一個 fingerprint 的 issuance 紀錄裡，`_nonce_reuse_errors` 會判為重用。所以環境修好後，Human/Ops 必須以
**新 nonce**（建議也換 approval_id）重新登記，只重啟 supervisor 不會重試 `HUMANOPS-DEV-FIRST-RELEASE-20260924`。

### 4.4 簽發器讀的 registry 來自一個落後且 dirty 的 checkout

簽發器的 root 是 `/home/lupin/odayplus`：HEAD `fc4f7529` 落後 `origin/dev` 160 個 commit，其已提交的 registry 仍是
`596b9c9a` / `no-go`；磁碟上的 registry 與 manifest 是**未提交的本機修改**，內容與 `origin/dev` byte-identical。
今天的准入因此成立，但任何 checkout / stash / reset 都會讓簽發器無聲退回 no-go（fail closed，但難以排查）。

## 5. 修正後的 unblock 順序

1. 操作者讓執行 `.orchestrator/supervisor.py` 的直譯器能 import `google-cloud-storage`（與 `google-auth`）：
   改用專案 uv 環境啟動 supervisor，或把釘住的套件裝進 supervisor 用的直譯器；之後依 fleet 既有程序重啟
   supervisor（user site 只在直譯器啟動時加入 sys.path）。
2. 操作者用同一重現指令驗證：結果不得再是 `ModuleNotFoundError`；若此時變成憑證錯誤，LATENT-2 即為真。
3. 操作者給 supervisor 一個非互動的 Secret Manager 憑證：更新 `/home/lupin` 的 gcloud 登入，或改用具
   `secretmanager.versions.access` 的 service account；以非互動的 `gcloud secrets versions list` 對同一 secret
   確認 exit 0（不印出內容）。
4. 操作者確認 supervisor 內 `storage.Client()` 解析到的主體能讀寫
   `gs://odayplus-runtime-20260825-release-leases/leases`；否則提供 ADC。
5. （選配）把 `/home/lupin/odayplus` fast-forward 到 `origin/dev`，讓簽發器讀的是已提交的 registry。
6. Human/Ops 以新 nonce、新 approval_id 重新登記 `release_lease_request`（candidate `13643634…`、manifest
   `sha256:6fb8f9e2…`、`manifest_run_id=35944616693`、dev / deploy）。task 必須維持 `in_progress`、無 blocker、
   無 `waiting_for`；**在 deploy 跑完之前不要送審**（`submit_review` 會把 status 設成 `review`，簽發器只接受
   `in_progress`）。
7. 簽發器簽出 Ed25519 lease（ttl 600s）並 dispatch 既有 deploy phase；若 deploy job 失敗，依驗收第 10 條 fail closed
   並另建 remediation task。
8. 本 task 收集部署後 Cloud Run URL/revision、migration/worker/scheduler execution、authenticated smoke、
   provider-off/16-source、**live default-deny egress probe 與 live IAM readback**（gate-4 deviation 條件）、
   exact manifest binding 收據（驗收 3–8）。

2026-09-21 與更早的 unblock 清單保留在 audit JSON 的 `superseded_unblock_requirements` 與 `history`，供稽核比對，未被刪除。

## 6. 終態

驗收 3–8 全數未成立。本 task 不得以 `done` 結案，也**不在本輪送審**：送審會把 task 移出 `in_progress`，
直接讓操作者 06:49:44Z 的 reopen 失效。本輪終態是 `in_progress` 加上寫在看板 `next` 的具體交辦；
阻塞在 supervisor 執行環境（缺套件、憑證過期），不在 release 資料面。

歷史 `docs/evidence/runtime/ODP-DEV-ROLLOUT-001/` 七份收據在 base merge 後重算 sha256，與 2026-09-04 audit
記錄完全相符；未被編輯、搬移或刪除。本 evidence 僅明確標示其部署宣稱已被 live reconciliation 推翻。

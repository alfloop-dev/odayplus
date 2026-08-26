# ODP-LIVE-RUNTIME-EVIDENCE-RECONCILE-001：live runtime 與歷史部署收據重新核對

## 結論

以 2026-08-26T15:37:23Z–15:38:33Z 之間對 GCP 與 GitHub 的實際讀取結果核對，
**dev、staging、production 三個環境都沒有任何 ODay Plus 應用工作負載在執行**。
兩個專案的 Cloud Run 服務清單只有 MLflow tracking server，Cloud Run jobs 與
Cloud Scheduler jobs 都是空的。

因此本次核對的 `release_status` 為 **`blocked`**，不產生任何部署成功收據。

歷史收據 `docs/evidence/runtime/ODP-DEV-ROLLOUT-001/` 一個字都沒有被改動。
本目錄只新增獨立的核對結果，讓兩者可以並存比對。

核對的 25 項歷史宣稱中：

| 判定 | 數量 | 意義 |
|---|---|---|
| `contradicted` | 10 | live 讀取直接推翻該宣稱 |
| `match` | 5 | 與現況相符 |
| `mismatch` | 3 | 資源名稱存在但與宣稱的不同 |
| `placeholder` | 2 | 收據寫的是重複字元常數，不是真實 digest |
| `unsupported` | 2 | 沒有任何 workflow run 能支撐 |
| `partial_mismatch` | 1 | 四個 secret 只有三個存在 |
| `superseded` | 1 | 已被後續 manifest 取代 |
| `unresolvable_reference` | 1 | 引用的報告路徑不存在 |

逐項細節在 `live-runtime-reconciliation-audit.json` 的
`historical_receipt_reconciliation`，每一項都帶 `transcript_ref` 指回
`live-readback-transcript.txt` 的具體區段。

## 讀取範圍與身分

| 項目 | 值 |
|---|---|
| 讀取視窗（UTC） | 2026-08-26T15:37:23Z – 2026-08-26T15:38:33Z |
| gcloud 身分 | `deborah.lu@dev.cctech-support.com`（gcloud 580.0.0） |
| dev / staging 專案 | `odayplus-runtime-20260825` |
| production 專案 | `odayplus-prod-20260826` |
| region | `asia-east1` |
| 當時 `origin/dev` | `7f3744ce7413e4134b2c178060b1d919bee2bf0f` |
| 當時 `origin/main` | `574dde52b56992b5088aedc74332e2e90fb40b44` |
| 執行指令數 | 50，全部為 read-only 的 list / describe / view |

只讀取 Secret Manager 的**名稱**，沒有讀任何 secret 內容；沒有執行任何
deploy、traffic switch、建立或刪除資源的指令。50 筆中只有 2 筆非零 exit，
兩筆本身都是結論的一部分：

- `EXIT=1`（transcript `S5`）：production 專案未啟用 Kubernetes Engine API。
- `EXIT=2`（transcript `S8`）：`dev-integration-readback.json` 引用的
  `.odp_data/deployment` 目錄不存在。

transcript 以 `S0`–`S8` 分節；`live-runtime-reconciliation-audit.json` 的
`readback_transcript.section_index` 記錄每一節對應的行號，audit 內每一項
`transcript_ref` 都指向這些節。

## 三環境現況

| | dev | staging | production |
|---|---|---|---|
| GCP 專案 | `odayplus-runtime-20260825` | `odayplus-runtime-20260825` | `odayplus-prod-20260826` |
| Cloud Run 服務 | 只有 `oday-mlflow` | 只有 `oday-staging-mlflow` | 只有 `oday-prod-mlflow` |
| Cloud Run jobs | 0 | 0 | 0 |
| Cloud Scheduler jobs | 0 | 0 | 0 |
| Cloud SQL | `oday-dev-sql` RUNNABLE | `oday-staging-sql` RUNNABLE | `oday-prod-sql` RUNNABLE |
| Artifact Registry | `oday-plus-dev`（2026-08-25 建立） | 同 dev repo | `oday-plus`（2026-08-26 建立） |
| GitHub environment 保護 | 無 | `required_reviewers` | `required_reviewers` |
| 應用工作負載 | **未部署** | **未部署** | **未部署** |

GitHub environment 變數設定的服務名稱（dev 是 `oday-api` / `oday-web` /
`oday-migration` / `oday-worker` / `oday-scheduler`，staging 是
`oday-staging-*`，production 是 `oday-api` / `oday-web` / …）在 live 讀取中
**一個都不存在**。三個環境的基礎設施（Cloud SQL、Artifact Registry、
Secret Manager、service accounts、WIF）都已備妥，缺的是實際部署。

GKE 方面，dev/staging 專案有 `oday-emgi-gke` cluster，其中只有
`oday-emgi-daemon` 與 `oday-emgi-webserver` 兩個 deployment；ephemeral staging
namespace `oday-staging-8c72b54` 存在但是空的（`kubectl get all` 回
`No resources found`）。

## Release 候選漂移

| 候選 SHA | 來源 | 落後 `origin/dev` | Artifact Registry release tag | Deploy Dev run |
|---|---|---|---|---|
| `e496be62…` | ODP-DEV-ROLLOUT-001 收據 | 2145 | 無 | 有一筆，`failure` |
| `2eeb11f2…` | 最後一次成功的 Deploy Dev | 2991 | 無 | `success`（2026-07-15） |
| `8c72b54a…` | AR release tag | 36 | 有（dev 與 prod repo） | 無 |
| `ace4265b…` | `RELEASE_MANIFEST.json` | 9 | 有（dev repo） | 無 |
| `7f3744ce…` | **目前 `origin/dev`** | 0 | **無** | **無** |

目前的 `origin/dev` 沒有任何已建置的 image，也沒有任何 Deploy Dev run。

## Fail-closed blockers

1. **`ODP-LIVE-RECONCILE-NO-DEPLOYED-WORKLOAD-001`（P0）** — 三個環境都沒有
   ODay Plus 的 API、Web、migration、worker 或 scheduler 工作負載。
   （transcript `S2`、`S4`）
2. **`ODP-LIVE-RECONCILE-NO-SUCCESSFUL-RELEASE-RUN-001`（P0）** — Runtime
   Release 管線從未對現在這組專案成功部署過。Deploy Dev 最後一次成功是
   2026-07-15T15:16:54Z，Deploy/Verify Staging 是 2026-06-28T14:15:45Z，
   兩者都早於 AR repo `oday-plus-dev`（2026-08-25 建立）與 production 專案
   （2026-08-26 建立），所以不能拿來當現況證據。最近 200 筆 Deploy Dev run
   是 112 failure、88 cancelled、**0 success**，而且自 2026-08-20T10:36:14Z
   之後就沒有再跑過。（transcript `S6`）
3. **`ODP-LIVE-RECONCILE-STALE-CANDIDATE-001`（P0）** — 沒有任何 release
   artifact 綁定到目前的 `origin/dev`。（transcript `S1`、`S3`、`S6`）
4. **`ODP-LIVE-RECONCILE-UNSIGNED-CANDIDATE-001`（P0）** — 沒有任何
   release-tagged 候選 image 有 Cosign 簽章。dev repo 只有 `oday-data-platform`
   的六組 `.sig` / `.att`（subject digest `0c97ba05`、`22fd0ce1`、`4f603e3a`、
   `51318554`、`95c349ab`、`f1a37010`），沒有一個是 `8c72b54a` 的
   `9089d49f` 或 `ace4265b` 的 `b9aa10b5`；production repo 完全沒有 `.sig` /
   `.att` tag。（transcript `S3`、`S4`）
5. **`ODP-LIVE-RECONCILE-RECEIPT-CONTRADICTED-001`（P0）** — ODP-DEV-ROLLOUT-001
   收據宣稱 2026-08-25T16:45:00Z 完成 dev 驗證，但實際上工作負載不存在、
   收據寫的 Cloud SQL instance 與 runtime service account 不存在、image
   digest 是佔位常數、引用的報告路徑未被追蹤也不存在，而該候選唯一的
   Deploy Dev run 在 2026-07-29 就失敗了。

## 幾個具體對不上的地方

- **Cloud SQL instance**：收據寫 `oday-plus-dev-pg16`，專案裡實際只有
  `oday-dev-sql` 與 `oday-staging-sql`；dev environment 變數
  `GCP_CLOUD_SQL_INSTANCE` 也是指向 `oday-dev-sql`。
- **Runtime service account**：收據寫 `oday-plus-dev-runtime@…`，專案裡是
  `gke-oday-dev-runtime@…`，environment 變數同樣是後者。
- **Image digest**：收據的
  `ghcr.io/alfloop-dev/odayplus-api@sha256:1111…`、`…2222…`、`…4444…`、
  `…5555…`、`…6666…`，以及 data platform 的 `…3333…` 與 cloud-sql-proxy 的
  `sha256:0000…`，都是重複字元常數。全零 digest 不可能是任何內容的雜湊。
  而且這條管線的正式 registry 是 Artifact Registry，不是 ghcr.io
  （見 `GCP_DEPLOY_GUIDE.md` 第 5 節）。
- **Secret**：`oday-plus-dev-web-oidc-client-secret` 不存在，dev environment
  也沒有對應的 `ODP_WEB_OIDC_CLIENT_SECRET_SECRET` 變數；另外三個 secret
  名稱則確實存在。
- **報告路徑**：`dev-integration-readback.json` 引用的五份
  `.odp_data/deployment/*.json`，在工作樹與 `origin/dev` 都不存在
  （`git ls-tree -r origin/dev -- .odp_data` 回空）。
- **GKE**：data platform 收據寫 cluster `non-production-gke`、namespace
  `oday-dev`，實際上 cluster 是 `oday-emgi-gke`，也沒有 `oday-dev` namespace，
  更沒有任何 `oday-data-platform-*` 的 Job 或 CronJob。

## 本次明確沒有做的事

- 沒有修改、搬動或刪除 `docs/evidence/runtime/ODP-DEV-ROLLOUT-001/` 下的
  任何歷史收據。
- 沒有為任何環境寫入部署成功收據。
- 沒有在兩個 GCP 專案執行任何部署或資源異動。
- 沒有讀取任何 secret 內容。
- 沒有改 `docs/evidence/gates/RELEASE_MANIFEST.json`：它現有的
  `release_status: blocked` 與本次結論一致，不需要也不應該由本 task 改寫。

## Verifier commands

```bash
# 1. 三環境的 Cloud Run 現況。預期只看到 MLflow 服務、jobs 為空。
gcloud run services list --project odayplus-runtime-20260825 \
  --format="table(metadata.name,metadata.labels['cloud.googleapis.com/location'])"
gcloud run jobs list --project odayplus-runtime-20260825
gcloud run services list --project odayplus-prod-20260826 \
  --format="table(metadata.name,metadata.labels['cloud.googleapis.com/location'])"
gcloud run jobs list --project odayplus-prod-20260826

# 2. Runtime Release 歷史。預期 success 筆數為 0。
gh run list --workflow deploy-dev.yml --limit 200 --json conclusion \
  --jq '[.[].conclusion]|group_by(.)|map({conclusion:.[0],count:length})'

# 3. ODP-DEV-ROLLOUT-001 宣稱的候選只有一筆 Deploy Dev run，且為 failure。
gh run view 30459596361 --json headSha,conclusion,jobs \
  --jq '{sha:.headSha,conclusion:.conclusion,jobs:[.jobs[]|{name,conclusion}]}'

# 4. 目前 origin/dev 沒有任何 release image。預期輸出為空。
gcloud artifacts docker images list \
  asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev \
  --include-tags --format="value(package,tags)" \
  | grep 'release-7f3744ce7413e4134b2c178060b1d919bee2bf0f'

# 5. 收據引用的報告路徑不存在。預期 exit 非 0 / 輸出為空。
ls .odp_data/deployment
git ls-tree -r --name-only origin/dev -- .odp_data

# 6. 本目錄的 audit 是合法 JSON 且維持 fail-closed。
python3 -c "import json;d=json.load(open('docs/evidence/runtime/ODP-LIVE-RUNTIME-EVIDENCE-RECONCILE-001/live-runtime-reconciliation-audit.json'));assert d['release_status']=='blocked';assert d['deployment_success_claimed'] is False;assert d['historical_receipts_modified'] is False;print('fail-closed OK')"
```

## Transcript 完整性

| 檔案 | SHA-256 |
|---|---|
| `live-readback-transcript.txt` | `sha256:70cdc7ad02dfb06945495aef59b82d36c62697d64d4e8f634ec8b85c80b23290` |

`live-runtime-reconciliation-audit.json` 的 `readback_transcript.sha256`
必須等於上表的值；不相等就代表 transcript 被改過，該 audit 不可採信。

## 解除 blocker 的條件

1. 從確切的 candidate SHA 執行唯一的 Runtime Release workflow，讓 SBOM 與
   Cosign 簽章由 hosted OIDC 產生，而不是用文件宣稱。
2. 對 `odayplus-runtime-20260825` 跑出一次成功的 Deploy Dev，並以當下的
   Cloud Run services / jobs / scheduler readback 作為收據。
3. 以上兩點都成立之後，才重新建立 release manifest，並用 live readback
   而不是樣板寫出各環境的部署收據。

在那之前，任何宣稱三環境已部署成功的收據都與本目錄的 transcript 直接衝突。

# ODP-GITHUB-GCP-ENV-LIVE-DRIFT-REMEDIATION-001

## 結果

本任務已實際修復 GitHub environments，不以預填期望值冒充 live 收據。2026-08-27 UTC 的分頁 readback 結果如下：

| Environment | Live variables | Build twin | 端點政策 |
|---|---:|---|---|
| `dev` | 38 | `dev-build` 的 9 個 build vars 完全一致 | Web 使用 Cloud Run 平台網址，不建立 DNS、自訂網域或憑證 |
| `staging` | 44 | `staging-build` 的 9 個 build vars 完全一致 | Web 使用 `console-staging.oday-plus.com.tw`；IAM API 使用 Cloud Run service URL |
| `production` | 41 | `production-build` 的 9 個 build vars 完全一致 | Web 使用 `console.oday-plus.com.tw`；IAM API 使用 Cloud Run service URL |

官方網站 `www.oday-plus.com.tw` 未修改。

## 實際修正

- 以 GitHub API 寫回三個 deploy environments 的 Runtime Release 非秘密變數與 Secret Manager references。
- 將 staging 與 production 的 API URL 從不存在的自訂 API 網域改為 Cloud Run service URL；自訂網域只留給 Web console。
- 確認 `ODP_AUTH_AUDIENCES` 與各環境 Cloud Run API audience 一致。
- 移除 9 個在所有 GitHub workflows 都無 caller 的舊變數：
  - dev：`ODP_AUTH_SUBJECT_ROLE_BINDINGS`、`ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS`、`ODP_OPERATOR_SMOKE_SUBJECT`
  - staging／production：`ODP_AUTH_SUBJECT_ROLE_BINDINGS`、`ODP_EXTERNAL_PROVIDER_MODE`、`ODP_OPERATOR_SMOKE_SUBJECT`
- 第三方 provider 的唯一權威來源仍是 Runtime Release 內的 `ODP_EXTERNAL_PROVIDER_MODE: disabled`，未建立第二個開關。
- staging 與 production 的 required reviewers 均維持 2 人，未更動保護規則。

## Live GCP 驗證

以下項目皆以 metadata readback 驗證，未讀取任何 secret value：

- dev、staging、production 的 project、region、Artifact Registry、RUNNABLE Cloud SQL。
- deploy service account、runtime service account、scheduler service account、operator smoke service account。
- Workload Identity Federation provider。
- snapshot bucket 與 durable release lease bucket。
- staging VPC connector 為 `READY`。
- database URL、principal map、Web session 三類非 OAuth secret reference 都指向 `ENABLED` version。
- 三個 OAuth secret containers 都存在但仍為 0 versions。
- 三個 environments 都仍沒有 `ODP_WEB_OIDC_CLIENT_ID`。

Cloud Run API、Web、migration、worker 與 scheduler 目前均是正確的部署目標名稱，但服務／工作本身尚未建立；這是 rollout 前狀態，不宣稱它們已部署。

## Redaction

[`github-environments-audit.json`](github-environments-audit.json) 對每個 live variable 只保存：

- `name`
- `present`
- `value_sha256`
- `classification`

收據不保存 variable raw value、tenant ID、role bindings、public key 內容或任何 secret value。Secret Manager 只保存 reference metadata、version 狀態與 OAuth 0-version gate。

## 尚未完成的外部 gate

- Human/Ops 建立三個 Google Web OAuth clients。
- 將 client IDs 寫入對應 GitHub environment variables。
- 將 client secrets 寫入既有 Secret Manager containers。
- 實際執行 dev → ephemeral staging → production rollout。
- ODay Web 部署後再建立 staging／production console DNS 與 TLS；dev 不建立 DNS。

## 驗證方式

GitHub variables 必須使用分頁／slurp，避免只讀到第一頁：

```bash
gh api --paginate --slurp "repos/alfloop-dev/odayplus/environments/dev/variables?per_page=100"
gh api --paginate --slurp "repos/alfloop-dev/odayplus/environments/staging/variables?per_page=100"
gh api --paginate --slurp "repos/alfloop-dev/odayplus/environments/production/variables?per_page=100"
```

驗收以 GitHub API 與 GCP metadata 的即時 readback 為準，不以本文件或歷史 transcript 取代 live state。

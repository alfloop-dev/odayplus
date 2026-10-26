# ODP-GCP-STAGING-EXECUTION-PREFLIGHT-002 修復表

- 綁定 commit：`596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`（origin/dev）
- 實測時間：2026-09-07T15:56Z – 2026-09-07T16:05Z（UTC，逐筆見 `command-log.jsonl`）
- 執行者：Claude（helper execution lease；canonical owner Antigravity4、reviewer Codex）
- 本表只指派修復歸屬，**不解除任何 task 的 blocked 狀態，不構成部署驗收**。

## 0. 歸屬原則

| 缺口性質 | 交付對象 |
|---|---|
| 缺 code / IaC | 原工程 owner，走既有 task 與 owned paths |
| 缺登入 / 憑證 | 人類操作者，附精確可執行命令 |
| 缺發布批准 | 留在原 release gate，不在此處放行 |

## 1. 缺登入（交人類，唯一阻擋本次實測的項目）

| 項次 | 事實 | 精確命令 |
|---|---|---|
| H-1 | gcloud ACTIVE 帳號 `deborah.lu@dev.cctech-support.com` 的憑證需重新驗證。12 次唯讀查詢全部 exit 1，錯誤一致為 `Reauthentication failed. cannot prompt during non-interactive execution.`；憑證庫最後更新 2026-09-01T12:44:38Z。 | `gcloud auth login deborah.lu@dev.cctech-support.com` |
| H-2 | 無 Application Default Credentials（`~/.config/gcloud/application_default_credentials.json` 不存在），本機 Terraform / google provider 亦無法取得憑證。 | `gcloud auth application-default login`（僅在需要本機 terraform plan 時） |

完成 H-1 後可重跑 `command-log.jsonl` 內記錄的 12 條唯讀命令取得 live readback。
**在 H-1 完成前，不得把任何 GCP 資源判定為「不存在」。**

## 2. 缺 code / IaC（交原工程 owner）

| 項次 | 對應舊 task | 事實（HEAD readback） | 交付內容 |
|---|---|---|---|
| C-1 | `ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001`（owner Antigravity2 / reviewer Codex2） | `infra/terraform/bootstrap/`、`infra/terraform/modules/runtime_foundation/`、`docs/deployment/STAGING_FOUNDATION_BOOTSTRAP.md` 在 dev 上皆不存在 | 該 task 宣告的 artifacts 仍全數未合併。6 項 staging foundation 變數在 IaC 落地前沒有可指向的 live resource，**修復順序必須是先 IaC/bootstrap，再由人設定變數**。 |
| C-2 | `ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001` | Terraform state bucket 與 recovery bundle bucket 兩個變數皆未設定，workflow 的 identical-bucket 分離檢查在取值前即 fail-closed | 需新建兩個各自獨立的受保護 bucket（CMEK、versioning、retention、public-access-prevention、最小權限）。staging 現有的 `ODP_SNAPSHOT_BUCKET` 與 `ODP_RELEASE_LEASE_STATE_URI` 依 GCP_DEPLOY_GUIDE §5 **嚴禁挪用**。 |
| C-3 | `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`（owner Codex / reviewer Claude2）+ workflow owner | `ODP_WEB_BASE_URL` 為 `REQUIRED_PUBLIC_CONFIG`，三環境皆不存在且無 fallback（`deploy-dev.yml:1042`、`deploy_cloud_run_waji.sh:863` 以 `os.environ[...]` 硬取）。三環境另有 `ODP_DEV_DEPLOY_URL` / `ODP_STAGING_DEPLOY_URL` / `ODP_PROD_DEPLOY_URL`，但程式不讀這些名稱 | 需 code owner 先裁決：新增 `ODP_WEB_BASE_URL` 變數，或改綁既有 `*_DEPLOY_URL`。**本 preflight 任務不代為決定。** |

## 3. 缺變數設定（交人類，需在 §2 對應項落地後才有正確值）

以 HEAD 已合併契約逐項重算的實測缺口。詳表見 `preflight-result.json` 的 `required_variable_matrix`。

| 環境 | 缺項數 | 缺少的變數 | 對應舊 task |
|---|---|---|---|
| `staging` | 10 | `ODP_STAGING_VPC_NETWORK`、`ODP_STAGING_VPC_SUBNETWORK`、`ODP_STAGING_KMS_KEY_ID`、`ODP_STAGING_DEPLOYER_SERVICE_ACCOUNT`、`ODP_STAGING_TERRAFORM_STATE_BUCKET`、`ODP_STAGING_RECOVERY_BUNDLE_BUCKET`、`ODP_PRODUCTION_WATCH_CLOSEOUT_URI`、`ODP_WEB_BASE_URL`、`ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT`、`ODP_IDENTITY_TOKEN_SIGNING_KEY_SECRET` | `ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001`、`ODP-EPHEMERAL-STAGING-ROLLOUT-001` |
| `dev` | 2 | `ODP_WEB_BASE_URL`、`ODP_IDENTITY_TOKEN_SIGNING_KEY_SECRET` | `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` |
| `production` | 4 | `ODP_WEB_BASE_URL`、`ODP_IDENTITY_TOKEN_SIGNING_KEY_SECRET`、`ODP_CLOUD_RUN_VPC_CONNECTOR`、`ODP_CLOUD_RUN_VPC_EGRESS` | 留在 production release gate，本表不放行 |

### 舊結論「staging 僅缺 5 項 vars」已不成立

以 `596b9c9a` 的 contract 逐項核實，staging 實測缺 10 項；且 dev 與 production 也各自有會在任何 Cloud Run 變更前 fail-closed 的缺口。舊結論低估數量且僅涵蓋 staging。

## 4. 缺發布批准（不在此處放行）

| 項次 | 事實 |
|---|---|
| A-1 | `staging` 與 `production` 環境皆有 `required_reviewers`（`Alien-alfaloop`、`ajoe734`）。本任務未觸及、未請求、未偽造任何批准。 |
| A-2 | `ODP-EPHEMERAL-STAGING-ROLLOUT-001`、`ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`、`ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001` 維持 `blocked`；本任務不變更其狀態。 |

## 5. 建議修復順序

1. H-1 人類重新登入 gcloud → 重跑 12 條唯讀命令，取得真正的 live readback。
2. C-3 code owner 裁決 `ODP_WEB_BASE_URL` 綁定方式（阻擋 dev 與 production 兩條路徑）。
3. C-1 / C-2 foundation IaC 與 bootstrap 落地並取得 live resource。
4. 人類依 §3 補齊各環境變數，值取自 §3 落地後的實際 resource identity。
5. 回到各自原 task 的既有驗收流程；staging/production 仍受 required reviewers 與 release gate 管制。

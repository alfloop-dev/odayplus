# GitHub environment 與變數盤點（ODP-RELEASE-BUILD-PHASE-BOOTSTRAP-001）

查詢時間 (UTC): 2026-08-26T16:53:39Z
查詢對象: `alfloop-dev/odayplus`

這份盤點是本次修正的**前提**：build 階段之所以必須綁 environment，是因為
repository 層級一個變數都沒有；之所以不能綁部署用的那個 environment，是因為
其中兩個帶了 `required_reviewers`。以下是逐字查詢結果，不是敘述。

只記錄變數**名稱**；除了用來證明「各環境值不同、無法上收到 repo 層級」的兩個
座標值（`GCP_PROJECT_ID` / `GCP_AR_REPO`）之外，不記錄任何值。
secrets 完全未查詢。

## 1. repository 層級變數：0 筆

```console
$ gh api repos/alfloop-dev/odayplus/actions/variables --paginate -q '.variables[].name' | wc -l
0
```

沒有 repository 層級變數，代表未綁定 environment 的 job 讀 `vars.X` 一律得到
空字串——而 GitHub 不會為此報錯。

## 2. 存在哪些 environment

```console
$ gh api repos/alfloop-dev/odayplus/environments -q '.environments[].name'
dev
production
staging
```

沒有任何 `-build` environment。它們需要被建立——見 README 的「尚待佈建」一節。

## 3. 部署核准規則

```console
$ gh api repos/alfloop-dev/odayplus/environments/dev -q '[.protection_rules[].type]'
[]
$ gh api repos/alfloop-dev/odayplus/environments/staging -q '[.protection_rules[].type]'
["required_reviewers"]
$ gh api repos/alfloop-dev/odayplus/environments/production -q '[.protection_rules[].type]'
["required_reviewers"]
```

`staging` 與 `production` 帶 `required_reviewers`。把 build 綁上去，就是要求
人類先核准一次部署，才能開始產生那次核准所要驗證的 manifest。

## 4. build 階段需要的變數在哪一層

```console
$ gh api repos/alfloop-dev/odayplus/environments/dev/variables?per_page=30 --paginate -q '.variables[].name' | grep -E '^(GCP_|ODP_CLOUD_RUN_)' | sort | tr '\n' ' '
GCP_AR_REPO GCP_CLOUD_SQL_INSTANCE GCP_PROJECT_ID GCP_REGION GCP_SERVICE_ACCOUNT GCP_WORKLOAD_IDENTITY_PROVIDER ODP_CLOUD_RUN_API_SERVICE ODP_CLOUD_RUN_MIGRATION_JOB ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT ODP_CLOUD_RUN_SCHEDULER_JOB ODP_CLOUD_RUN_WEB_SERVICE ODP_CLOUD_RUN_WORKER_JOB 
$ gh api repos/alfloop-dev/odayplus/environments/staging/variables?per_page=30 --paginate -q '.variables[].name' | grep -E '^(GCP_|ODP_CLOUD_RUN_)' | sort | tr '\n' ' '
GCP_AR_REPO GCP_CLOUD_SQL_INSTANCE GCP_PROJECT_ID GCP_REGION GCP_SERVICE_ACCOUNT GCP_WORKLOAD_IDENTITY_PROVIDER ODP_CLOUD_RUN_API_SERVICE ODP_CLOUD_RUN_MIGRATION_JOB ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT ODP_CLOUD_RUN_SCHEDULER_JOB ODP_CLOUD_RUN_VPC_CONNECTOR ODP_CLOUD_RUN_VPC_EGRESS ODP_CLOUD_RUN_WEB_SERVICE ODP_CLOUD_RUN_WORKER_JOB 
$ gh api repos/alfloop-dev/odayplus/environments/production/variables?per_page=30 --paginate -q '.variables[].name' | grep -E '^(GCP_|ODP_CLOUD_RUN_)' | sort | tr '\n' ' '
GCP_AR_REPO GCP_CLOUD_SQL_INSTANCE GCP_PROJECT_ID GCP_REGION GCP_SERVICE_ACCOUNT GCP_WORKLOAD_IDENTITY_PROVIDER ODP_CLOUD_RUN_API_SERVICE ODP_CLOUD_RUN_MIGRATION_JOB ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT ODP_CLOUD_RUN_SCHEDULER_JOB ODP_CLOUD_RUN_WEB_SERVICE ODP_CLOUD_RUN_WORKER_JOB 
```

以上是 `GCP_` 與 `ODP_CLOUD_RUN_` 前綴的全部變數（比 build 需要的九個多）。
重點是：build 需要的九個——`GCP_WORKLOAD_IDENTITY_PROVIDER`、
`GCP_SERVICE_ACCOUNT`、`GCP_PROJECT_ID`、`GCP_REGION`、`GCP_AR_REPO`、
`ODP_CLOUD_RUN_API_SERVICE`、`ODP_CLOUD_RUN_WEB_SERVICE`、
`ODP_CLOUD_RUN_WORKER_JOB`、`ODP_CLOUD_RUN_SCHEDULER_JOB`——
在三個環境都齊備，而且**只**存在於 environment 層級（見第 1 節）。

## 5. 為什麼不能改放 repository 層級

```console
$ gh api repos/alfloop-dev/odayplus/environments/dev/variables?per_page=30 --paginate -q '<GCP_PROJECT_ID / GCP_AR_REPO>'
GCP_AR_REPO=oday-plus-dev GCP_PROJECT_ID=odayplus-runtime-20260825 
$ gh api repos/alfloop-dev/odayplus/environments/staging/variables?per_page=30 --paginate -q '<GCP_PROJECT_ID / GCP_AR_REPO>'
GCP_AR_REPO=oday-plus-dev GCP_PROJECT_ID=odayplus-runtime-20260825 
$ gh api repos/alfloop-dev/odayplus/environments/production/variables?per_page=30 --paginate -q '<GCP_PROJECT_ID / GCP_AR_REPO>'
GCP_AR_REPO=oday-plus GCP_PROJECT_ID=odayplus-prod-20260826 
```

`production` 用的是不同的 GCP 專案與 Artifact Registry repository。repository
層級只能有一組值，所以「把變數上收到 repo 層級」不是這題的解——build 必須綁到
對應環境的那一組。

## 這份盤點沒有做的事

- 沒有建立、修改或刪除任何 environment、變數或 secret；全部是唯讀查詢。
- 沒有查詢任何 secret 名稱或值。
- 沒有觸及 GCP；`odayplus` 的 GCP 專案目前停權，與本盤點無關。

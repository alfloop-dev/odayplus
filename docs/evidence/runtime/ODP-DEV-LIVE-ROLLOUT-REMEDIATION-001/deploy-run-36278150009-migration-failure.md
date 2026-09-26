# Deploy Dev run 36278150009 — migration job 被未知 alembic revision 擋下

- 量測者：Claude2（auto worker），量測時間 2026-09-26T23:06Z–23:10Z（UTC）
- Lease：`lease-322b6270a446287adfff6251d5417109`，approval `HUMANOPS-DEV-FIRST-RELEASE-20260926T230105Z`，
  2026-09-26T23:01:37Z 簽發，由 run 36278150009 消耗（lease 本身的 `expires_at` 是 23:11:37Z）
- Candidate `419e6bf4958269c5b9e94efcb80770e28cd54dda`、manifest `sha256:134cc712132155b0003d68063298d3044d5400d91244b8024b4448268c4fc678`
  （manifest run 36080312679）、dispatch ref `dev`@`c8d26f020e0f6a065757beeac88b02f011884894`

## 結果：failure（fail closed，零流量）

來源：`gh api repos/alfloop-dev/odayplus/actions/runs/36278150009/jobs`，以及 job 108504945939 的 log

| Job | 結論 |
|---|---|
| Validate release phase inputs | success |
| Verify the Supervisor lease authorises this deploy | success |
| Build once and publish the immutable artifact handoff | skipped（deploy-by-digest） |
| Deploy the admitted artifact by immutable digest | **failure**（step 14 `Deploy Cloud Run by immutable digest`） |

Step 7–13 全部 success（environment binding、live runtime preflight、WIF、Cloud SDK、Cosign）。

## 跟上一輪（run 36252020646）比，這次多走了哪幾步

上一輪死在 `gcloud run jobs deploy`，原因是 SA `gke-oday-dev-runtime` 缺
`oday-plus-dev-identity-token-signing-key` 的 secretAccessor。這一輪同一步成功了：

```
23:04:16Z Deploying container to Cloud Run job [oday-migration-r-419e6bf49582] ... Creating job...
23:04:19Z Job [oday-migration-r-419e6bf49582] has successfully been deployed.
23:04:19Z Executing migration Cloud Run Job...
23:05:35Z ERROR: (gcloud.run.jobs.execute) The execution failed.   (execution oday-migration-r-419e6bf49582-2llfm)
23:05:39Z Error: migration Cloud Run Job failed; deployment stopped.
23:05:39Z Deployment failed on the first release into this target.
23:05:39Z There is no previous release to roll back to; recovery is deleting the candidate and holding zero traffic.
23:05:46Z Deleted job [oday-migration-r-419e6bf49582].
```

所以 IAM 缺口已經確認修好；新的死點是 migration execution 本身。

## 根因量測（Cloud Logging，唯讀）

`gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="oday-migration-r-419e6bf49582"' --project odayplus-runtime-20260825`：

```
23:05:30.164620Z stdout  FAILED: Can't locate revision identified by '29b539ebc72a'
23:05:30.246731Z stdout  {"cloud_run_execution": "oday-migration-r-419e6bf49582-2llfm", "environment": "dev",
                          "error": "migration or runtime schema verification failed", "error_class": "OpsPlanError",
                          "receipt_kind": "migration", "release_sha": "419e6bf4958269c5b9e94efcb80770e28cd54dda", "status": "failed"}
23:05:30.711568Z varlog  Container called exit(1).
23:05:34.888908Z audit   Execution oday-migration-r-419e6bf49582-2llfm has failed to complete, 0/1 tasks were a success.
```

Alembic 的 `Can't locate revision` 代表目標資料庫的 `alembic_version` 表記著一個 revision，
而 candidate image 內的 migration 目錄（`infra/db/migrations/`，version table 用預設的 `alembic_version`）找不到它。

`29b539ebc72a` 的來源，逐項排除：

| 搜尋範圍 | 結果 |
|---|---|
| `git grep 29b539ebc72a 419e6bf4`（candidate） | 無 |
| `git grep 29b539ebc72a origin/dev`（`c8d26f02` 之後的 dev tip） | 無 |
| `git log --all -S29b539ebc72a`（odayplus、oday-data-platform、odp-foundation 本機 clone） | 無 |
| `gh search code 29b539ebc72a --owner alfloop-dev` | 0 筆 |
| MLflow 3.16.0（`uv.lock` 釘的版本）的 `mlflow/store/db_migrations/versions/*.py`，共 66 檔 | 無 |

Cloud SQL `oday-dev-sql` 上的資料庫：`postgres`、`oday_plus`、`mlflow`。MLflow 服務的 backend
走另一個 secret（`oday-plus-dev-mlflow-backend-uri`）；app 的 migration 走
`oday-plus-dev-api-database-url-pg16:latest`。**我沒有讀 secret 的值**，所以還不能確定 app 的 URL
實際指到哪個資料庫，也不能確定 `alembic_version` 裡的值是誰寫的。

## 判定

- 這不是 workflow 或 deploy 程式的缺陷：migration 碰到無法識別的 schema 狀態時 fail closed，
  回收 candidate job 並保持零流量，這正是預期的行為。
- 死點在 dev 資料庫的狀態。要讀 DB 內容，還可能要 `alembic stamp` 或重建 schema，
  這是對 live 資料庫的破壞性動作，屬於 Human/Ops 閘；worker 既不能也不該做。
- 這次 lease 與 approval 已經被 run 36278150009 消耗。修好之後要以新的 approval／nonce 重新登記
  lease request（同一個 candidate，不用重 build）。

## 給操作者的交辦

1. 讀 `oday-plus-dev-api-database-url-pg16:latest` 的 database 名稱（不必公開密碼），確認它是不是 `oday_plus`。
2. 在那個資料庫查 `SELECT version_num FROM alembic_version;` 與既有的表清單，判斷 `29b539ebc72a` 是誰寫進去的
   （可能是另一個 alembic 專案共用了同一個資料庫與 `alembic_version` 表，或是某次手動操作）。
3. 依判斷結果處理：URL 指錯資料庫就改 secret；如果是殘留的首發前狀態，就清空或 stamp 回 candidate 的 base。
4. 以新的 `approval_id`／`nonce` 重新登記 `release_lease_request`，再把 task 轉回 in_progress。

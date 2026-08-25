# ODay Plus 新 GCP Project Bootstrap 證據

日期：2026-08-25（UTC）

## 結果

部署目標已從舊的 `alfaloop-data-project` 切換為全新 project：

| 項目 | 值／狀態 |
|---|---|
| Project ID | `odayplus-runtime-20260825` |
| Project number | `767864276141` |
| Organization | `1064164192528` |
| Billing | enabled；`018A56-78F63D-CEDE92` |
| Region | `asia-east1` |
| Artifact Registry | `oday-plus-dev` |
| Cloud SQL | `oday-dev-sql`；PostgreSQL 16；RUNNABLE |
| Application database | `oday_plus`；獨立 user；密碼僅存 Secret Manager |
| MLflow database | `mlflow`；獨立 user；密碼僅存 Secret Manager |
| Snapshot bucket | `oday-dev-source-snapshots-odayplus-runtime-20260825`；versioning enabled |
| Model artifact bucket | `odayplus-runtime-20260825-model-artifacts`；versioning enabled |
| GitHub WIF | `github-actions/odayplus`；只接受 `alfloop-dev/odayplus` repository assertion |

## Identity 邊界

- `github-deployer@odayplus-runtime-20260825.iam.gserviceaccount.com`：WIF deployer。
- `gke-oday-dev-runtime@odayplus-runtime-20260825.iam.gserviceaccount.com`：runtime。
- `oday-dev-scheduler@odayplus-runtime-20260825.iam.gserviceaccount.com`：只用於呼叫核准的 Cloud Run Jobs。
- `oday-dev-smoke-operator@odayplus-runtime-20260825.iam.gserviceaccount.com`：短效 smoke identity。
- 沒有建立或下載 service-account key。

## Secret 與外部來源

已建立的 secrets 只有 database、MLflow backend、auth principal map 與 Web
session secret。secret payload 未寫入 repository、GitHub variables 或本文件。

新 project 沒有建立 geocode、POI、admin-boundary、listing 或其他第三方 provider
credential；GitHub `dev` environment 的舊 provider URL、credential reference 與
production provider allowlist 已移除。第三方來源維持 disabled。

## GitHub environment readback

`dev` environment 已指向：

- `GCP_PROJECT_ID=odayplus-runtime-20260825`
- `GCP_CLOUD_SQL_INSTANCE=odayplus-runtime-20260825:asia-east1:oday-dev-sql`
- `GCP_WORKLOAD_IDENTITY_PROVIDER=projects/767864276141/locations/global/workloadIdentityPools/github-actions/providers/odayplus`
- 新 project 的 deployer、runtime、scheduler、smoke service accounts。
- 新 snapshot bucket 與新 API audience。

readback 未發現 `alfaloop-data-project`、project number `1067163562451` 或舊
Cloud Run URL。

## 首次部署管線修補

原 Runtime Release 只支援既有 API/Web 的升級：在 build 前強制 describe 舊服務，
因此空 project 首次部署必定失敗。修補仍使用同一條 Runtime Release：

1. traffic snapshot 可以明確記錄 service absent。
2. API/Web 同時 absent 時，migration compatibility receipt 記為 bootstrap
   not-applicable；只有一邊 absent 時仍 fail closed。
3. bootstrap 部署失敗時，rollback 刪除本次建立、部署前不存在的 service；既有
   service 仍依原 traffic snapshot 精確回復。
4. 沒有新增第二個 deploy workflow 或旁路 entrypoint。

## 尚未完成、不可宣稱已部署

1. Google Auth Platform Web OAuth client 必須由人類在 Console 建立；官方不允許
   一般 Web OAuth client 由 CLI 自動建立。redirect URI 為
   `https://oday-web-767864276141.asia-east1.run.app/auth/callback`。
2. MLflow 必須以新 database/bucket 部署，且在公開前補齊服務對服務認證；不得把
   未驗證的 public MLflow 當作完成。
3. 完成上述依賴後，仍須由 Supervisor 簽發有效 release lease，執行唯一 Runtime
   Release 並取得 migration、API/Web、worker、scheduler、smoke 與 rollback receipts。

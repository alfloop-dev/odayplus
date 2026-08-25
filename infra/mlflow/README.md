# ODay Plus Production MLflow

This image runs the OSS MLflow tracking and model-registry service. It is
production-only: startup fails unless the backend is remote PostgreSQL and the
artifact root is a dedicated GCS prefix. The server never proxies artifact
bytes through its HTTP process.

## Build

```bash
docker build -f infra/mlflow/Dockerfile -t oday-plus-mlflow:3.14.0 .
```

## Runtime inputs

| Variable | Requirement |
|---|---|
| `MLFLOW_BACKEND_STORE_URI` | Secret-injected remote `postgresql://.../database` URI |
| `MLFLOW_DEFAULT_ARTIFACT_ROOT` | `gs://<approved-bucket>/<dedicated-prefix>` |
| `MLFLOW_TRACKING_URI` | HTTPS service URL used by readiness and clients |
| `MLFLOW_ALLOWED_HOSTS` | Explicit trusted Host headers; wildcard is rejected |
| `PORT` | Container port, default `5000` |
| `MLFLOW_WORKERS` | Worker count, default `2`, allowed `1..32` |
| Application Default Credentials | Workload identity with least-privilege GCS access |

Do not place database passwords, service-account JSON, access tokens, or signed
URLs in the image, command line, repository, or artifact root.

```bash
docker run --rm -p 5000:5000 \
  -e MLFLOW_BACKEND_STORE_URI \
  -e MLFLOW_DEFAULT_ARTIFACT_ROOT \
  -e MLFLOW_TRACKING_URI \
  -e MLFLOW_ALLOWED_HOSTS \
  oday-plus-mlflow:3.14.0
```

Localhost, SQLite, `file://`, bare GCS buckets, and placeholder values are
rejected. Liveness checks `/health`; the stronger readiness command also
queries the registry and verifies access to the configured GCS bucket:

```bash
python -m infra.mlflow.healthcheck --mode readiness
```

## Cloud Run 容量下限

使用預設的兩個 worker 時，Cloud Run 記憶體不得低於 `1Gi`。首次建立資料表時，
兩個 MLflow worker 的實測尖峰會超過 `512Mi`；低於此值會在開始監聽連接埠後被
平台終止。若要降低容量，必須先以壓力測試證明單 worker 能承受工作量，不能只
縮小記憶體限制。

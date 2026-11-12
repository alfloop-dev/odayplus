"""Dev-only driver: verify the installed model-ready views and train the
governed forecastops candidate with the REAL trainer
(``product_ops.modeling.release``), registering it in the dev MLflow.

It never promotes: release aliases need a separately signed approval document
(approver != training actor), which this driver does not create.

The only adaptation is identity plumbing. The trainer's MLflow auth mints a
Cloud Run ID token through ``google.oauth2.id_token.fetch_id_token``, which
supports a metadata server or a service-account key but not the Workload
Identity Federation credentials a GitHub runner has. The job's auth step mints
an ID token for the same deployer service account with the MLflow origin as
audience; this driver hands that token to the trainer's fetcher. MLflow's
Cloud Run IAM still decides access.

Env: RAW_DB_URL, CLOUD_SQL_INSTANCE, MLFLOW_ID_TOKEN, MLFLOW_TRACKING_URI,
ODP_MLFLOW_CLOUD_RUN_AUDIENCE, ODP_MODEL_ARTIFACT_ROOT, ODP_RELEASE_COMMIT_SHA,
ODP_MODEL_TRAINING_ACTOR, MODEL_VERSION. No secrets are printed.
"""

from __future__ import annotations

import io
import json
import os
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime


def _socket_dsn(raw: str, instance: str) -> str:
    u = urllib.parse.urlparse(raw)
    user = urllib.parse.quote(urllib.parse.unquote(u.username or ""), safe="")
    pw = urllib.parse.quote(urllib.parse.unquote(u.password or ""), safe="")
    db = (u.path or "/").lstrip("/") or "postgres"
    host = urllib.parse.quote(f"/cloudsql/{instance}", safe="/:")
    return f"postgresql://{user}:{pw}@/{db}?host={host}"


def _run(label: str, fn, argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fn(argv)
    print(f"{label} rc={rc}", flush=True)
    return rc, out.getvalue(), err.getvalue()


def main() -> int:
    os.environ["ODAY_DATABASE_URL"] = _socket_dsn(
        os.environ["RAW_DB_URL"], os.environ["CLOUD_SQL_INSTANCE"]
    )
    token = os.environ.pop("MLFLOW_ID_TOKEN").strip()
    if not token:
        print("RESULT: FAILED (no MLflow ID token)", flush=True)
        return 1

    from modules.learninghub.infrastructure import mlflow_cloud_run_auth

    audience = os.environ["ODP_MLFLOW_CLOUD_RUN_AUDIENCE"].rstrip("/")

    def _wif_minted(_request, requested_audience: str) -> str:
        if requested_audience.rstrip("/") != audience:
            raise RuntimeError("ID token requested for an unexpected audience")
        return token

    mlflow_cloud_run_auth.fetch_id_token = _wif_minted

    from product_ops.modeling import install_views, release

    # The versioned views are already installed in dev (probe 43215309998);
    # only re-verify them read-only instead of re-applying the SQL.
    rc, out, err = _run("INSTALL_VIEWS inventory", install_views.main, ["inventory"])
    if rc != 0:
        print(out[:3000], err[-2000:], flush=True)
        print("RESULT: FAILED (model-ready views not ready)", flush=True)
        return 1

    rc, out, err = _run("INVENTORY forecastops", release.main, ["inventory", "--model", "forecastops"])
    try:
        inv = json.loads(out)
        for m in inv.get("models", []):
            print("INVENTORY", json.dumps(m, sort_keys=True, default=str)[:3000], flush=True)
    except ValueError:
        print(out[:2000], err[-2000:], flush=True)

    version = os.environ["MODEL_VERSION"].strip()
    end = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rc, out, err = _run(
        "TRAIN forecastops",
        release.main,
        ["train", "--model", "forecastops", "--version", version,
         "--start", "2020-01-01T00:00:00Z", "--end", end],
    )
    if rc != 0:
        print("TRAIN stderr:", err[-3000:], flush=True)
        print("RESULT: FAILED (training refused or failed)", flush=True)
        return 1
    result = json.loads(out)
    print("TRAIN result:", json.dumps(result, sort_keys=True, default=str)[:6000], flush=True)
    print(f"RESULT: OK (registered forecast_revenue_interval version {version}; NOT promoted)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

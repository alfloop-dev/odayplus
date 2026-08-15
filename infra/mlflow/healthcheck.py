from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from infra.mlflow.runtime import MlflowServerSettings


def _http_health(port: int) -> None:
    try:
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
            if response.status != 200:
                raise RuntimeError(f"MLflow health endpoint returned {response.status}")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError("MLflow health endpoint is unavailable") from exc


def _registry_ready(tracking_uri: str) -> None:
    try:
        from mlflow.tracking import MlflowClient

        MlflowClient(tracking_uri=tracking_uri).search_experiments(max_results=1)
    except Exception as exc:
        raise RuntimeError("MLflow PostgreSQL-backed registry is unavailable") from exc


def _artifact_bucket_ready(artifact_root: str) -> None:
    try:
        from google.cloud import storage

        bucket_name = artifact_root.removeprefix("gs://").split("/", 1)[0]
        storage.Client().bucket(bucket_name).reload()
    except Exception as exc:
        raise RuntimeError("MLflow GCS artifact bucket is unavailable") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("health", "readiness"), default="health")
    args = parser.parse_args(argv)
    try:
        settings = MlflowServerSettings.from_environment()
        _http_health(settings.port)
        if args.mode == "readiness":
            tracking_uri = os.getenv(
                "MLFLOW_TRACKING_URI",
                f"http://127.0.0.1:{settings.port}",
            )
            _registry_ready(tracking_uri)
            _artifact_bucket_ready(settings.default_artifact_root)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "not-ready",
                    "mode": args.mode,
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"status": "ready", "mode": args.mode}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

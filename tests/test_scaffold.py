from __future__ import annotations

from pathlib import Path

from apps.api.oday_api.main import health_payload
from apps.scheduler.oday_scheduler.main import scheduler_health
from apps.worker.oday_worker.main import worker_health

ROOT = Path(__file__).resolve().parents[1]


def test_odp_sd04_top_level_scaffold_exists() -> None:
    expected_paths = [
        "apps/api",
        "apps/web",
        "apps/worker",
        "apps/scheduler",
        "apps/cli",
        "modules/integration",
        "modules/external_data",
        "modules/heatzone",
        "modules/listing",
        "modules/sitescore",
        "modules/forecastops",
        "modules/intervention",
        "modules/priceops",
        "modules/adlift",
        "modules/avm",
        "modules/netplan",
        "modules/learninghub",
        "modules/opsboard",
        "shared/domain",
        "shared/application",
        "shared/infrastructure",
        "shared/auth",
        "shared/audit",
        "shared/jobs",
        "shared/workflow",
        "shared/notification",
        "shared/observability",
        "packages/openapi-client",
        "packages/ui",
        "packages/schemas",
        "packages/testkit",
        "pipelines/dbt",
        "pipelines/airflow_or_dagster",
        "pipelines/data_quality",
        "models/sitescore",
        "models/forecastops",
        "models/priceops",
        "models/adlift",
        "models/avm",
        "models/shared_ml",
        "solver/pricing",
        "solver/netplan",
        "infra/terraform",
        "infra/docker",
        "infra/k8s_optional",
        "infra/cloudbuild",
        "tests/contract",
        "tests/integration",
        "tests/e2e",
        "tests/performance",
        "tests/security",
    ]

    missing = [path for path in expected_paths if not (ROOT / path).exists()]
    assert missing == []


def test_app_health_payloads_are_importable() -> None:
    assert health_payload() == {"status": "ok", "service": "oday-api"}
    assert worker_health() == {"status": "ok", "service": "oday-worker"}
    assert scheduler_health() == {"status": "ok", "service": "oday-scheduler"}

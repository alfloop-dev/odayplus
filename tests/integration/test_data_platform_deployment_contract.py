from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[2]
K8S = ROOT / "infra/k8s/data-platform"
DOCKERFILE = ROOT / "infra/docker/data-platform.Dockerfile"
RUNTIME = K8S / "runtime/deployment_runtime.py"
STATUS_MAPPING = K8S / "status_mapping.prod.json"
RELEASE_SHA = "a" * 40
DATA_IMAGE = "asia-east1-docker.pkg.dev/project/repo/data@sha256:" + "b" * 64
PROXY_IMAGE = "gcr.io/cloud-sql-connectors/cloud-sql-proxy@sha256:" + "c" * 64


def _render_module():
    spec = importlib.util.spec_from_file_location("data_platform_render", K8S / "render.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runtime_module():
    spec = importlib.util.spec_from_file_location("data_platform_runtime", RUNTIME)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _documents() -> list[dict]:
    rendered = _render_module().render(
        release_sha=RELEASE_SHA,
        data_image=DATA_IMAGE,
        cloud_sql_proxy_image=PROXY_IMAGE,
        cloud_sql_instance="alfaloop-data-project:asia-east1:oday-plus-dev-postgres",
        postgres_user="postgres",
        postgres_database="postgres",
        manual_start="2026-07-23T00:00:00Z",
        manual_end="2026-07-24T00:00:00Z",
    )
    return list(yaml.safe_load_all(rendered))


def _pod_spec(document: dict) -> dict:
    if document["kind"] == "CronJob":
        return document["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    return document["spec"]["template"]["spec"]


def _main_container(document: dict) -> dict:
    return _pod_spec(document)["containers"][0]


def _environment(container: dict) -> dict[str, dict]:
    return {entry["name"]: entry for entry in container["env"]}


def test_image_build_requires_immutable_base_and_full_release_sha() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "ARG PYTHON_BASE_IMAGE" in dockerfile
    assert "FROM ${PYTHON_BASE_IMAGE}" in dockerfile
    assert "ARG ODP_RELEASE_SHA" in dockerfile
    assert 'org.opencontainers.image.revision="${ODP_RELEASE_SHA}"' in dockerfile
    assert "USER 65532:65532" in dockerfile
    assert "data-platform.Dockerfile" not in dockerfile


def test_alembic_database_url_accepts_encoded_production_passwords() -> None:
    url = "postgresql://oday_app:encoded%3D@127.0.0.1:5432/oday_plus"
    config = Config()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    assert config.get_main_option("sqlalchemy.url") == url
    migration_env = (ROOT / "infra/db/migrations/env.py").read_text(encoding="utf-8")
    assert 'url.replace("%", "%%")' in migration_env


def test_manifest_has_migration_then_bounded_and_manual_workloads() -> None:
    documents = _documents()
    assert [(doc["kind"], doc["metadata"]["name"]) for doc in documents] == [
        ("Job", f"oday-data-platform-migrate-{RELEASE_SHA[:12]}"),
        ("CronJob", "oday-data-platform-bounded-daily"),
        ("Job", f"oday-data-platform-orders-history-{RELEASE_SHA[:12]}"),
        ("Job", f"oday-data-platform-trade-manual-{RELEASE_SHA[:12]}"),
        ("Job", f"oday-data-platform-device-log-manual-{RELEASE_SHA[:12]}"),
    ]
    assert documents[0]["metadata"]["annotations"]["oday.plus/execution-order"] == (
        "00-migration"
    )
    assert documents[1]["metadata"]["annotations"][
        "oday.plus/requires-migration-receipt"
    ] == "true"
    assert documents[2]["spec"]["suspend"] is True
    assert documents[3]["spec"]["suspend"] is True
    assert documents[4]["spec"]["suspend"] is True


def test_all_workloads_share_immutable_release_image_and_cloud_sql_sidecar() -> None:
    for document in _documents():
        assert document["metadata"]["namespace"] == "oday-dev"
        assert document["metadata"]["annotations"]["oday.plus/release-sha"] == RELEASE_SHA
        assert document["metadata"]["annotations"]["oday.plus/image-reference"] == DATA_IMAGE
        pod = _pod_spec(document)
        assert pod["serviceAccountName"] == "oday-data-platform"
        assert pod["restartPolicy"] == "Never"
        assert pod["securityContext"]["runAsNonRoot"] is True
        assert _main_container(document)["image"] == DATA_IMAGE
        assert _main_container(document)["securityContext"]["readOnlyRootFilesystem"] is True
        proxy = pod["initContainers"][0]
        assert proxy["name"] == "cloud-sql-auth-proxy"
        assert proxy["restartPolicy"] == "Always"
        assert proxy["image"] == PROXY_IMAGE
        assert "--address=0.0.0.0" in proxy["args"]
        assert proxy["startupProbe"]["tcpSocket"]["port"] == 5432
        assert proxy["resources"]["requests"]
        assert proxy["resources"]["limits"]


def test_workloads_fit_the_observed_gke_scheduling_budget() -> None:
    for document in _documents():
        pod = _pod_spec(document)
        main_request = _main_container(document)["resources"]["requests"]
        proxy_request = pod["initContainers"][0]["resources"]["requests"]
        assert main_request["cpu"] == "100m"
        assert proxy_request["cpu"] == "25m"
        assert main_request["memory"] == "1Gi"
        assert proxy_request["memory"] == "64Mi"


def test_migration_job_is_independent_and_produces_verified_receipt() -> None:
    migration = _documents()[0]
    container = _main_container(migration)
    assert container["args"] == ["migrate"]
    env = _environment(container)
    assert env["ODP_RELEASE_SHA"]["value"] == RELEASE_SHA
    assert env["ODP_IMAGE_REFERENCE"]["value"] == DATA_IMAGE
    assert env["ODP_DATA_CLOUD_SQL_PROXY"]["value"] == "true"
    assert env["ODP_DATA_CLOUD_SQL_CONNECTOR_EVIDENCE"]["value"] == (
        "cloud-sql-auth-proxy-sidecar"
    )
    assert env["ODP_POSTGRES_PASSWORD"]["valueFrom"]["secretKeyRef"] == {
        "name": "oday-data-platform-runtime",
        "key": "postgres-password",
    }
    source = RUNTIME.read_text(encoding="utf-8")
    for required in (
        '"alembic"',
        '"upgrade"',
        '"head"',
        "_apply_assisted_intake_upgrade",
        "000008_postgresql_runtime_persistence.sql",
        "validate_assisted_listing_intake_schema.sql",
        "control_schema.sql",
        "deployment_migration_receipts",
        "schema_verification_status",
    ):
        assert required in source


def test_backfill_is_receipt_gated_and_uses_real_secret_inputs() -> None:
    cron = _documents()[1]
    container = _main_container(cron)
    assert container["args"] == ["scheduled"]
    env = _environment(container)
    assert env["ODP_DATA_MONGO_URI"]["valueFrom"]["secretKeyRef"] == {
        "name": "oday-data-platform-runtime",
        "key": "mongodb-uri",
    }
    assert env["ODP_DATA_MONGO_DATABASE"]["value"] == "fongniao_prod"
    assert env["ODP_DATA_MAX_RECORDS_PER_RUN"]["value"] == "250000"
    assert "mock" not in str(cron).lower()
    assert "fixture" not in str(cron).lower()
    source = RUNTIME.read_text(encoding="utf-8")
    assert "_migration_receipt(dsn)" in source
    assert "No PASSED migration receipt exists" in source
    for kind in (
        "merchant",
        "place",
        "device",
        "orders",
        "ai_revenue_stats",
        "campaign",
        "product",
        "products",
        "promotions",
        "ai_consumer_kmeans_v1",
    ):
        assert f'"{kind}"' in source


def test_committed_status_mapping_covers_observed_scheduled_dimension_codes() -> None:
    import json

    payload = json.loads(STATUS_MAPPING.read_text(encoding="utf-8"))
    assert payload["version"] == "fongniao-prod-observed-v1"
    assert payload["trade_paid_amount_rule"] is None
    assert payload["mappings"]["merchant_operation"] == {
        "0": "inactive",
        "1": "active",
    }
    assert payload["mappings"]["place_operation"] == {
        "1": "open",
        "99": "closed",
    }
    assert set(payload["mappings"]["place_type"]) == {
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "99",
    }
    assert "2" not in payload["mappings"]["merchant_operation"]
    assert "transaction" not in payload["mappings"]
    assert "trade" not in payload["mappings"]


def test_orders_history_is_receipt_gated_bounded_and_real(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = _documents()[2]
    assert history["metadata"]["annotations"]["oday.plus/manual-only"] == "true"
    assert history["metadata"]["annotations"]["oday.plus/hard-limit"] == (
        "62-days,one-day-partitions,max-250000-per-partition"
    )
    container = _main_container(history)
    assert container["args"] == ["orders-history"]
    env = _environment(container)
    assert env["ODP_ORDERS_HISTORY_START"]["value"] == "2026-05-23T00:00:00Z"
    assert env["ODP_ORDERS_HISTORY_END"]["value"] == "2026-07-24T00:00:00Z"
    assert env["ODP_DATA_MONGO_DATABASE"]["value"] == "fongniao_prod"

    runtime = _runtime_module()
    monkeypatch.setenv("ODP_ORDERS_HISTORY_START", "2026-05-23T00:00:00Z")
    monkeypatch.setenv("ODP_ORDERS_HISTORY_END", "2026-07-24T00:00:00Z")
    command = runtime._backfill_command("orders-history")
    assert command.count("--kind") == 1
    assert command[command.index("--kind") + 1] == "orders"
    assert command[command.index("--max-partitions") + 1] == "62"
    assert command[command.index("--partition-days") + 1] == "1"
    assert "--allow-trade" not in command
    assert "--allow-device-log" not in command

    monkeypatch.setenv("ODP_ORDERS_HISTORY_START", "2026-05-22T00:00:00Z")
    with pytest.raises(runtime.DeploymentContractError, match="<= 62 days"):
        runtime._backfill_command("orders-history")


def test_trade_and_device_log_are_manual_one_day_hard_limited_jobs() -> None:
    trade, device_log = _documents()[3:]
    for document, command in ((trade, "trade"), (device_log, "device-log")):
        assert document["metadata"]["annotations"]["oday.plus/manual-only"] == "true"
        assert document["metadata"]["annotations"]["oday.plus/hard-limit"] == (
            "one-day,max-100000"
        )
        assert document["spec"]["suspend"] is True
        assert document["spec"]["backoffLimit"] == 0
        container = _main_container(document)
        assert container["args"] == [command]
        env = _environment(container)
        assert env["ODP_DATA_MAX_RECORDS_PER_RUN"]["value"] == "100000"
        assert env["ODP_MANUAL_START"]["value"] == "2026-07-23T00:00:00Z"
        assert env["ODP_MANUAL_END"]["value"] == "2026-07-24T00:00:00Z"


def test_no_manifest_contains_secret_material_and_receipts_are_capturable() -> None:
    for document in _documents():
        assert "data" not in document
        assert "stringData" not in document
        container = _main_container(document)
        assert container["terminationMessagePolicy"] == "File"
        assert container["terminationMessagePath"] == "/var/run/oday/termination.log"
        assert container["resources"]["requests"]
        assert container["resources"]["limits"]


def test_renderer_rejects_tags_short_sha_and_unbounded_manual_windows() -> None:
    renderer = _render_module()
    valid = {
        "release_sha": RELEASE_SHA,
        "data_image": DATA_IMAGE,
        "cloud_sql_proxy_image": PROXY_IMAGE,
        "cloud_sql_instance": "alfaloop-data-project:asia-east1:oday-plus-dev-postgres",
        "postgres_user": "postgres",
        "postgres_database": "postgres",
        "manual_start": "2026-07-23T00:00:00Z",
        "manual_end": "2026-07-24T00:00:00Z",
    }
    with pytest.raises(ValueError, match="40-character"):
        renderer.render(**{**valid, "release_sha": "abc123"})
    with pytest.raises(ValueError, match="immutable"):
        renderer.render(**{**valid, "data_image": "repo/data:latest"})
    with pytest.raises(ValueError, match="at most one day"):
        renderer.render(
            **{
                **valid,
                "manual_end": "2026-07-25T00:00:00Z",
            }
        )

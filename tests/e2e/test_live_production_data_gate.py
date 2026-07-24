from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/e2e/check_live_production_data.py"
NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
EXPECTED_SHA = "a" * 40
API_URL = "https://oday-api.prod.alfloop.internal"


def load_checker():
    spec = importlib.util.spec_from_file_location("check_live_production_data", CHECKER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeHttp:
    def __init__(self, payloads: dict[str, dict[str, Any]]) -> None:
        self.payloads = payloads
        self.requested: list[str] = []

    def get_json(self, path: str) -> dict[str, Any]:
        self.requested.append(path)
        if path not in self.payloads:
            raise RuntimeError(f"missing injected HTTP payload for {path}")
        return deepcopy(self.payloads[path])


class FakeDatabase:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        error: BaseException | None = None,
    ) -> None:
        self.payload = payload or {}
        self.error = error

    def collect(self) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        return deepcopy(self.payload)


def dataset(kind: str) -> dict[str, Any]:
    suffix = {"merchant": "1", "place": "2", "transaction": "3"}[kind]
    return {
        "source_kind": kind,
        "run_id": f"00000000-0000-4000-8000-00000000000{suffix}",
        "source_database": "fongniao_prod",
        "partition_key": "2026-07-24",
        "status": "SUCCEEDED",
        "source_count": 12,
        "valid_loaded": 11,
        "quarantined_count": 1,
        "source_checksum": f"sha256-source-{kind}",
        "raw_checksum": f"sha256-source-{kind}",
        "raw_computed_checksum": f"sha256-source-{kind}",
        "valid_computed_checksum": f"sha256-canonical-{kind}",
        "canonical_checksum": f"sha256-canonical-{kind}",
        "canonical_computed_checksum": f"sha256-canonical-{kind}",
        "started_at": "2026-07-24T10:00:00+00:00",
        "finished_at": "2026-07-24T10:05:00+00:00",
        "raw_count": 12,
        "latest_observed_at": "2026-07-24T10:04:00+00:00",
        "surrogate_marker_count": 0,
        "canonical_count": 11,
        "lineage_count": 11,
        "latest_projected_at": "2026-07-24T10:05:00+00:00",
        "active_quarantine_count": 1,
    }


def model_binding(service: str) -> dict[str, str]:
    return {
        "model_name": f"oday-{service}",
        "model_version": "2026.07.24",
        "artifact_uri": f"gs://oday-model-registry/{service}/model.bin",
        "artifact_sha256": "b" * 64,
        "registry_run_id": f"mlflow-{service}-20260724",
        "feature_schema_version": f"{service}-features-v1",
        "dataset_snapshot_id": f"snapshot-{service}-20260724",
    }


def receipt(service: str) -> dict[str, Any]:
    item: dict[str, Any] = {
        "receipt_id": f"receipt-{service}-20260724",
        "run_id": f"run-{service}-20260724",
        "status": "SUCCEEDED",
        "occurred_at": "2026-07-24T11:00:00+00:00",
        "data_origin": "live",
        "persistence": "postgresql",
        "source_snapshot_ids": [f"snapshot-source-{service}-20260724"],
        "proof_path": f"/api/v1/live-data/receipts/{service}/receipt-{service}-20260724",
    }
    if service != "operator":
        item["model_binding"] = model_binding(service)
        item["execution"] = {
            "engine": f"{service}-production-runtime",
            "library": "approved-oss-runtime",
            "actual_model_invoked": True,
            "fallback_used": False,
        }
    return item


def evidence() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": "2026-07-24T11:30:00+00:00",
        "release_sha": EXPECTED_SHA,
        "api_url": API_URL,
        "data_mode": "live",
        "persistence": "postgresql",
        "source_database": "fongniao_prod",
        "datasets": {
            kind: {
                key: value
                for key, value in dataset(kind).items()
                if key
                in {
                    "run_id",
                    "source_count",
                    "raw_count",
                    "valid_loaded",
                    "canonical_count",
                    "quarantined_count",
                    "source_checksum",
                    "raw_checksum",
                    "valid_checksum",
                    "canonical_checksum",
                }
            }
            for kind in ("merchant", "place", "transaction")
        },
        "runtime_receipts": {
            service: receipt(service)
            for service in (
                "operator",
                "forecastops",
                "sitescore",
                "avm",
                "netplan",
                "priceops",
                "adlift",
                "learninghub",
            )
        },
    }


def readiness() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "oday-api",
        "details": {
            "requireLiveData": True,
            "deploymentMode": "production",
            "persistence": {
                "configuredMode": "postgresql",
                "runtimeMode": "postgresql",
                "durable": True,
                "reachable": True,
                "production_persistence_supported": True,
            },
            "provider": {
                "mode": "live",
                "configurationValid": True,
                "healthy": True,
                "live": True,
            },
            "models": {
                "mode": "mlflow-production",
                "productionBindingsReady": True,
                "error": None,
                "autoSeeded": False,
            },
            "data": {
                "mode": "live",
                "origin": {
                    "kind": "live",
                    "sourceId": "operator-live-repository",
                    "persistenceMode": "postgresql",
                },
                "operatorRepositoryReady": True,
                "operatorRepositoryProbe": {
                    "ready": True,
                    "repository": "OperatorLiveRepository",
                    "persistenceMode": "postgresql",
                    "errors": [],
                },
                "liveReady": True,
                "blockingReasons": [],
            },
        },
    }


def database() -> dict[str, Any]:
    return {
        "database": {
            "name": "oday_plus",
            "server_version": "16.4",
            "transaction_read_only": "on",
        },
        "datasets": {
            kind: dataset(kind) for kind in ("merchant", "place", "transaction")
        },
    }


def _add_valid_checksums(manifest: dict[str, Any]) -> dict[str, Any]:
    for kind, item in manifest["datasets"].items():
        item["valid_checksum"] = f"sha256-canonical-{kind}"
    return manifest


def http_payloads(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {
        "/readiness": readiness(),
        "/platform/version": {
            "status": "ok",
            "service": "oday-api",
            "release_sha": EXPECTED_SHA,
        },
    }
    for item in manifest["runtime_receipts"].values():
        payloads[item["proof_path"]] = {"receipt": deepcopy(item)}
    return payloads


def gate_config(checker: Any, tmp_path: Path) -> Any:
    evidence_path = tmp_path / "live-evidence.json"
    evidence_path.write_text("{}\n", encoding="utf-8")
    return checker.GateConfig(
        api_url=API_URL,
        postgres_dsn="postgresql://oday_app:super-secret@postgres.internal/oday_plus",
        evidence_path=evidence_path,
        expected_sha=EXPECTED_SHA,
    )


def failed_names(checks: list[Any]) -> set[str]:
    return {check.name for check in checks if not check.ok}


def test_database_checksum_stream_matches_data_plane_contract() -> None:
    checker = load_checker()

    count, digest = checker.PsycopgDatabaseEvidenceClient._query_checksum(
        [
            ("00000000-0000-4000-8000-000000000001", "alpha"),
            ("00000000-0000-4000-8000-000000000002", "beta"),
        ]
    )

    expected = hashlib.sha256(
        b"00000000-0000-4000-8000-000000000001:alpha\n"
        b"00000000-0000-4000-8000-000000000002:beta"
    ).hexdigest()
    assert count == 2
    assert digest == expected


def test_gate_accepts_matching_live_api_database_and_receipts(tmp_path: Path) -> None:
    checker = load_checker()
    manifest = _add_valid_checksums(evidence())
    http = FakeHttp(http_payloads(manifest))

    checks, report = checker.evaluate_gate(
        gate_config(checker, tmp_path),
        http_client=http,
        database_client=FakeDatabase(database()),
        evidence_payload=manifest,
        now=NOW,
    )

    assert all(check.ok for check in checks)
    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["inputs"]["secret_values_redacted"] is True
    assert len(http.requested) == 10


def test_gate_rejects_memory_fixture_and_mock_identifiers(tmp_path: Path) -> None:
    checker = load_checker()
    manifest = _add_valid_checksums(evidence())
    manifest["persistence"] = "memory"
    manifest["runtime_receipts"]["operator"]["receipt_id"] = "mock-operator-001"
    payloads = http_payloads(manifest)
    payloads["/readiness"]["details"]["persistence"]["runtimeMode"] = "memory"
    payloads["/readiness"]["details"]["data"]["origin"]["sourceId"] = "r4-seed"

    checks, report = checker.evaluate_gate(
        gate_config(checker, tmp_path),
        http_client=FakeHttp(payloads),
        database_client=FakeDatabase(database()),
        evidence_payload=manifest,
        now=NOW,
    )

    failures = failed_names(checks)
    assert "evidence:no_surrogate_markers" in failures
    assert "evidence:live_data_contract" in failures
    assert "api:readiness:persistence" in failures
    assert "api:readiness:no_surrogate_markers" in failures
    assert "receipt:operator:no_surrogate_markers" in failures
    assert report["ok"] is False


def test_gate_rejects_empty_or_unreconciled_canonical_data(tmp_path: Path) -> None:
    checker = load_checker()
    manifest = _add_valid_checksums(evidence())
    db_payload = database()
    transaction = db_payload["datasets"]["transaction"]
    transaction.update(
        {
            "canonical_count": 0,
            "lineage_count": 0,
            "quarantined_count": 2,
            "active_quarantine_count": 1,
        }
    )

    checks, _ = checker.evaluate_gate(
        gate_config(checker, tmp_path),
        http_client=FakeHttp(http_payloads(manifest)),
        database_client=FakeDatabase(db_payload),
        evidence_payload=manifest,
        now=NOW,
    )

    failures = failed_names(checks)
    assert "database:transaction:populated" in failures
    assert "database:transaction:reconciliation_arithmetic" in failures
    assert "database:transaction:evidence_agrees" in failures


def test_gate_rejects_stale_lineage_and_missing_service_receipt(tmp_path: Path) -> None:
    checker = load_checker()
    manifest = _add_valid_checksums(evidence())
    manifest["runtime_receipts"].pop("adlift")
    db_payload = database()
    db_payload["datasets"]["place"]["latest_projected_at"] = "2026-07-20T00:00:00+00:00"

    checks, _ = checker.evaluate_gate(
        gate_config(checker, tmp_path),
        http_client=FakeHttp(http_payloads(manifest)),
        database_client=FakeDatabase(db_payload),
        evidence_payload=manifest,
        now=NOW,
    )

    failures = failed_names(checks)
    assert "database:place:freshness" in failures
    assert "receipt:adlift:identity" in failures
    assert "receipt:adlift:model_binding" in failures
    assert "receipt:adlift:runtime_proof" in failures


def test_gate_rejects_model_receipt_without_actual_oss_execution(tmp_path: Path) -> None:
    checker = load_checker()
    manifest = _add_valid_checksums(evidence())
    manifest["runtime_receipts"]["netplan"]["execution"] = {
        "engine": "heuristic",
        "library": "",
        "actual_model_invoked": False,
        "fallback_used": True,
    }

    checks, _ = checker.evaluate_gate(
        gate_config(checker, tmp_path),
        http_client=FakeHttp(http_payloads(manifest)),
        database_client=FakeDatabase(database()),
        evidence_payload=manifest,
        now=NOW,
    )

    assert "receipt:netplan:model_execution" in failed_names(checks)


def test_gate_rejects_external_or_traversal_receipt_proof_path(tmp_path: Path) -> None:
    checker = load_checker()
    manifest = _add_valid_checksums(evidence())
    manifest["runtime_receipts"]["sitescore"]["proof_path"] = (
        "https://attacker.invalid/receipt"
    )

    checks, _ = checker.evaluate_gate(
        gate_config(checker, tmp_path),
        http_client=FakeHttp(http_payloads(manifest)),
        database_client=FakeDatabase(database()),
        evidence_payload=manifest,
        now=NOW,
    )

    assert "receipt:sitescore:runtime_proof" in failed_names(checks)


def test_gate_redacts_dsn_and_bearer_token_from_database_blocker(tmp_path: Path) -> None:
    checker = load_checker()
    manifest = _add_valid_checksums(evidence())
    config = gate_config(checker, tmp_path)
    config = checker.GateConfig(
        **{
            **config.__dict__,
            "bearer_token": "secret-bearer-value",
        }
    )
    error = RuntimeError(
        f"could not connect to {config.postgres_dsn}; Bearer secret-bearer-value"
    )

    _, report = checker.evaluate_gate(
        config,
        http_client=FakeHttp(http_payloads(manifest)),
        database_client=FakeDatabase(error=error),
        evidence_payload=manifest,
        now=NOW,
    )

    serialized = json.dumps(report)
    assert "super-secret" not in serialized
    assert "secret-bearer-value" not in serialized
    assert "<redacted>" in serialized or "<redacted-postgres-dsn>" in serialized


def test_cli_fails_closed_when_live_inputs_are_absent(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    env = os.environ.copy()
    for name in (
        "ODP_LIVE_API_URL",
        "ODP_LIVE_POSTGRES_DSN",
        "ODP_LIVE_DATA_EVIDENCE",
        "ODP_LIVE_EXPECTED_SHA",
        "ODP_LIVE_BEARER_TOKEN",
    ):
        env.pop(name, None)

    result = subprocess.run(
        [sys.executable, str(CHECKER), "--output", str(output)],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report["inputs"]["postgres_dsn_configured"] is False
    assert {item["check"] for item in report["blockers"]} >= {
        "config:api_url",
        "config:postgres_dsn",
        "config:evidence",
        "config:expected_sha",
    }

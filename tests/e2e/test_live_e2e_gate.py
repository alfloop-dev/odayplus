"""Fail-closed behaviour of the post-deploy live E2E gate (ODP-LIVE-E2E-001).

Every test starts from a fully live, fully passing deployment and breaks exactly
one runtime fact, then asserts (a) that the gate fails and (b) that it names the
runtime dependency an operator would have to repair.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/e2e/check_live_e2e_gate.py"
EXPECTED_SHA = "b" * 40
API_URL = "https://oday-api.dev.alfloop.internal"
WEB_URL = "https://oday-web.dev.alfloop.internal"
CORRELATION_ID = "corr-live-e2e-bbbbbbbbbbbb-1"
JOB_ID = "job-01HZZ0000000000000000001"
NOW = "2026-07-26T15:00:00Z"


def load_checker() -> Any:
    spec = importlib.util.spec_from_file_location("check_live_e2e_gate", CHECKER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = load_checker()


# ---------------------------------------------------------------------------
# Live deployment doubles
# ---------------------------------------------------------------------------


class FakeHttp:
    """Routes ``METHOD path`` (prefixed ``anon`` when unauthenticated)."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        body: Any = None,
        headers: Any = None,
        follow_redirects: bool = True,
    ) -> Any:
        key = f"{'' if authenticated else 'anon '}{method.upper()} {path}"
        self.calls.append(key)
        route = self.routes.get(key)
        if route is None:
            raise AssertionError(f"unrouted gate request: {key}")
        if isinstance(route, list):
            route = route[0] if len(route) == 1 else route.pop(0)
        if callable(route):
            route = route(body, headers)
        return deepcopy(route)


class FakeWorkerDriver:
    def __init__(self, *, ok: bool = True, detail: str = "worker job drained") -> None:
        self.ok = ok
        self.detail = detail
        self.calls = 0

    def drain(self) -> tuple[bool, str]:
        self.calls += 1
        return self.ok, self.detail


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


_UNSET = object()


def response(status: int, payload: dict[str, Any] | None = None, **kwargs: Any) -> Any:
    return gate.HttpResponse(status=status, payload=payload or {}, **kwargs)


# ---------------------------------------------------------------------------
# Passing live deployment fixtures
# ---------------------------------------------------------------------------


def schedulable_required_provider_ids() -> tuple[str, ...]:
    """Required providers a real ``ExternalFetchScheduler`` would accept.

    Derived from the *runtime* registry, not from the gate's own constants, so a
    fixture can never fabricate an ingestion run the runtime cannot produce.
    """

    from modules.external_data.connectors.provider_registry import provider_registry
    from modules.external_data.workers.scheduled_fetch import _SCHEDULABLE_CATEGORIES

    schedulable = {
        provider.provider_id
        for provider in provider_registry()
        if provider.category in _SCHEDULABLE_CATEGORIES
    }
    return tuple(
        provider_id
        for provider_id in gate.DEFAULT_REQUIRED_PROVIDER_IDS
        if provider_id in schedulable
    )


def provider_probe(provider_id: str, **overrides: Any) -> dict[str, Any]:
    probe = {
        "provider_id": provider_id,
        "configuration_valid": True,
        "connectivity_healthy": True,
        "authentication_accepted": True,
        "response_valid": True,
        "schema_valid": True,
        "checked_at": "2026-07-26T14:59:30+00:00",
        "expires_at": "2026-07-26T15:00:30+00:00",
        "latency_ms": 42,
        "http_status": 200,
        "reason_code": "ok",
    }
    probe.update(overrides)
    return probe


def provider_probe_evidence(**overrides: Any) -> dict[str, Any]:
    evidence = {
        "status": "healthy",
        "mode": "live",
        "configuration_valid": True,
        "connectivity_healthy": True,
        "correlation_id": "corr-provider-probe-20260726",
        "checked_at": "2026-07-26T14:59:30+00:00",
        "expires_at": "2026-07-26T15:00:30+00:00",
        "required_provider_ids": list(gate.DEFAULT_REQUIRED_PROVIDER_IDS),
        "probes": [
            provider_probe(provider_id)
            for provider_id in gate.DEFAULT_REQUIRED_PROVIDER_IDS
        ],
    }
    evidence.update(overrides)
    return evidence


def readiness_payload() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "oday-api",
        "details": {
            "database": "healthy",
            "requireLiveData": True,
            "deploymentMode": "production",
            "persistence": {
                "configuredMode": "postgresql",
                "runtimeMode": "postgres",
                "durable": True,
                "reachable": True,
                "production_persistence_supported": True,
            },
            "provider": {
                "mode": "live",
                "configurationValid": True,
                "connectivityHealthy": True,
                "healthy": True,
                "live": True,
                "probeEvidence": provider_probe_evidence(),
            },
            "models": {
                "mode": "mlflow-production",
                "productionBindingsReady": True,
                "autoSeeded": False,
                "error": None,
                "requiredServices": ["avm", "forecastops", "heatzone", "sitescore"],
                "capabilities": {
                    service: {
                        "service": service,
                        "available": True,
                        "reasonCode": None,
                        "error": None,
                    }
                    for service in ("avm", "forecastops", "heatzone", "sitescore")
                },
            },
            "data": {
                "mode": "live",
                "liveReady": True,
                "operatorRepositoryReady": True,
                # `/readiness` publishes `OperatorLiveRepository.data_origin`
                # verbatim, and that property spells a healthy origin
                # "authoritative" -- it is never rewritten to "live" on this
                # surface (only the operator envelope's meta.dataOrigin is).
                "origin": {"kind": "authoritative", "persistenceMode": "postgresql"},
                "operatorRepositoryProbe": {"ready": True, "persistenceMode": "postgresql"},
                "blockingReasons": [],
            },
        },
    }


def models_payload() -> dict[str, Any]:
    names = {
        "avm": "dealroom_avm",
        "forecastops": "forecast_revenue_interval",
        "heatzone": "heatzone_priority",
        "sitescore": "sitescore_propensity",
    }
    return {
        "count": len(names),
        "items": [
            {
                "model_name": model_name,
                "version": "7",
                "model_id": f"{model_name}-7",
                "artifact_uri": f"gs://odp-dev-artifacts/{model_name}/7",
                "dataset_snapshot_id": f"snapshot-{service}-20260726",
                "feature_schema_version": "3",
                "label_version": "2",
                "stage": "production",
                "aliases": ["production"],
                "approved_by": "release-officer",
                "approved_at": "2026-07-25T09:00:00+00:00",
                "created_at": "2026-07-25T08:00:00+00:00",
                "metrics": {"rmse": 0.21},
            }
            for service, model_name in sorted(names.items())
        ],
    }


def ingestion_run(provider_id: str, *, total: int = 4, accepted: int = 3) -> dict[str, Any]:
    quarantined = total - accepted
    lineage = [
        {
            "contract_id": f"{provider_id}.v1",
            "source_system": provider_id,
            "source_id": f"{provider_id}:{index}",
            "source_record_id": f"rec-{index}",
            "canonical_target": "canonical.place",
            "mapping_id": f"{provider_id}.map.v1",
            "schema_version": "1",
            "accepted": index < accepted,
            "quarantine_reasons": [] if index < accepted else ["SCHEMA_VIOLATION"],
        }
        for index in range(total)
    ]
    return {
        "run_id": f"run-{provider_id}-20260726",
        "provider_id": provider_id,
        "schedule_id": "hourly",
        "trigger": "scheduled",
        "status": "SUCCEEDED",
        "data_status": "FRESH",
        "completed_at": "2026-07-26T14:00:00+00:00",
        "raw_snapshot_id": f"raw-{provider_id}-20260726",
        "canonical_snapshot_id": f"canonical-{provider_id}-20260726",
        "source_snapshot_id": f"src-{provider_id}-20260726",
        "source_snapshot_ids": [f"src-{provider_id}-20260726"],
        "correlation_id": "corr-ingest-20260726",
        "accepted_count": accepted,
        "quarantined_count": quarantined,
        "total_count": total,
        "lineage": lineage,
    }


def ingestion_payload() -> dict[str, Any]:
    # Only the providers a real scheduler can run. Fabricating a SUCCEEDED run
    # for an enrichment provider (geocode) would make the fixture assert
    # something the runtime is structurally incapable of producing.
    runs = [ingestion_run(provider_id) for provider_id in schedulable_required_provider_ids()]
    return {"items": runs, "count": len(runs)}


def enqueue_payload(*, created: bool = True, status: str = "queued") -> dict[str, Any]:
    return {
        "job_id": JOB_ID,
        "status": status,
        "correlation_id": CORRELATION_ID,
        "idempotency_key": "live-e2e-bbbbbbbbbbbb-corr",
        "created": created,
        "audit_event_id": "evt-0001",
        "job": {
            "job_id": JOB_ID,
            "job_type": "external-fetch",
            "status": status,
            "correlation_id": CORRELATION_ID,
            "attempts": 0,
        },
    }


def audit_payload(*, integrity: bool = True) -> dict[str, Any]:
    def event(index: int, outcome: str) -> dict[str, Any]:
        payload = {
            "event_id": f"evt-000{index}",
            "event_type": "job.enqueue",
            "actor": "system",
            "action": "enqueue",
            "resource": "job/external-fetch",
            "outcome": outcome,
            "result": outcome,
            "correlation_id": CORRELATION_ID,
            "job_id": JOB_ID,
            "metadata": {"created": outcome == "accepted"},
            "occurred_at": "2026-07-26T14:59:00+00:00",
        }
        if integrity:
            payload["integrity"] = {
                "sequence": index,
                "previous_hash": "0" * 64,
                "event_hash": f"{index:064d}",
                "signature_key_id": "audit-signing-1",
            }
        return payload

    return {"events": [event(1, "accepted"), event(2, "idempotent_replay")]}


def live_routes(**overrides: Any) -> dict[str, Any]:
    routes = {
        "anon GET /platform/version": response(
            200, {"release_sha": EXPECTED_SHA, "service": "oday-api"}
        ),
        "anon GET /readiness": response(200, readiness_payload()),
        "anon GET /api/v1/operator/bootstrap": response(
            401, {"error": {"code": "unauthenticated"}}
        ),
        # The real shape of the operator envelope built by
        # OperatorStateService._build_envelope: provenance lives under `meta`,
        # not at the top level.
        "GET /api/v1/operator/bootstrap": response(
            200,
            {
                "meta": {
                    "source": "operator-shell-production",
                    "dataMode": "live",
                    "dataOrigin": {
                        "kind": "live",
                        "sourceId": "operator-live-repository",
                        "persistenceMode": "postgresql",
                    },
                },
                "modules": ["today", "network"],
            },
        ),
        "GET /api/v1/learninghub/models": response(200, models_payload()),
        "GET /api/v1/external-data/ingestion-runs?limit=100": response(
            200, ingestion_payload()
        ),
        "POST /api/v1/jobs": [
            response(202, enqueue_payload(created=True)),
            response(202, enqueue_payload(created=False)),
        ],
        f"GET /api/v1/jobs/{JOB_ID}": response(
            200,
            {
                "job_id": JOB_ID,
                "job_type": "external-fetch",
                "status": "succeeded",
                "attempts": 1,
                "error_message": None,
            },
        ),
        f"GET /api/v1/audit/events?correlation_id={CORRELATION_ID}": response(
            200, audit_payload()
        ),
    }
    routes.update(overrides)
    return routes


def config(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "api_url": API_URL,
        "expected_sha": EXPECTED_SHA,
        "bearer_token": "operator-token-value",
        "operator_role": "ops_admin",
        # The web origin is part of the release contract: the gate now blocks
        # rather than silently skipping the protected-route assertion.
        "web_url": WEB_URL,
        "worker_deadline_seconds": 60.0,
        "poll_interval_seconds": 5.0,
    }
    values.update(overrides)
    return gate.GateConfig(**values)


def passing_web_http() -> Any:
    return FakeHttp(
        {
            "anon GET /operator": response(
                302, location=f"{WEB_URL}/login?returnTo=%2Foperator"
            )
        }
    )


def run_gate(
    routes: dict[str, Any] | None = None,
    *,
    cfg: Any = None,
    worker_driver: Any = None,
    web_http: Any = _UNSET,
) -> tuple[list[Any], dict[str, Any]]:
    clock = FakeClock()
    return gate.evaluate_gate(
        cfg or config(),
        http=FakeHttp(routes if routes is not None else live_routes()),
        worker_driver=worker_driver or FakeWorkerDriver(),
        correlation_id=CORRELATION_ID,
        now=NOW,
        web_http=passing_web_http() if web_http is _UNSET else web_http,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


def blocker_names(report: dict[str, Any]) -> set[str]:
    return {blocker["check"] for blocker in report["blockers"]}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_fully_live_deployment_passes() -> None:
    checks, report = run_gate()

    assert report["ok"] is True, report["blockers"]
    assert report["blockers"] == []
    assert report["blocking_dependencies"] == []
    assert all(check.ok for check in checks)
    assert report["worker"]["terminal_status"] == "succeeded"
    assert report["inputs"]["secret_values_redacted"] is True


def test_report_never_contains_the_bearer_token() -> None:
    routes = live_routes()
    routes["GET /api/v1/operator/bootstrap"] = response(
        500, {"detail": "Bearer operator-token-value rejected"}
    )
    _, report = run_gate(routes)

    serialized = json.dumps(report)
    assert "operator-token-value" not in serialized
    assert "<redacted>" in serialized


def test_every_check_declares_a_known_runtime_dependency() -> None:
    checks, _ = run_gate()

    assert {check.dependency for check in checks} <= set(gate.DEPENDENCY_ACTIONS)


# ---------------------------------------------------------------------------
# Configuration fails closed before any request is made
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "expected_check"),
    [
        ({"api_url": "http://oday-api.dev.alfloop.internal"}, "config:api_url"),
        ({"api_url": "https://localhost"}, "config:api_url"),
        ({"expected_sha": "not-a-sha"}, "config:expected_sha"),
        ({"bearer_token": ""}, "config:operator_credential"),
        ({"operator_role": ""}, "config:operator_role"),
        ({"required_provider_ids": ()}, "config:required_providers"),
    ],
)
def test_missing_or_unsafe_inputs_block_before_any_request(
    overrides: dict[str, Any], expected_check: str
) -> None:
    http = FakeHttp({})  # any request would raise
    clock = FakeClock()

    _, report = gate.evaluate_gate(
        config(**overrides),
        http=http,
        worker_driver=FakeWorkerDriver(),
        correlation_id=CORRELATION_ID,
        now=NOW,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert report["ok"] is False
    assert expected_check in blocker_names(report)
    assert http.calls == []
    assert report["blocking_dependencies"] == ["config"]


# ---------------------------------------------------------------------------
# Release binding
# ---------------------------------------------------------------------------


def test_release_sha_drift_blocks() -> None:
    routes = live_routes(
        **{"anon GET /platform/version": response(200, {"release_sha": "c" * 40})}
    )
    _, report = run_gate(routes)

    assert "release:platform_version" in blocker_names(report)
    assert "release" in report["blocking_dependencies"]


def test_unreachable_version_surface_blocks() -> None:
    routes = live_routes(
        **{"anon GET /platform/version": response(0, error="URLError for /platform/version")}
    )
    _, report = run_gate(routes)

    assert "release:platform_version" in blocker_names(report)


# ---------------------------------------------------------------------------
# Runtime dependencies
# ---------------------------------------------------------------------------


def test_sqlite_persistence_blocks_with_postgresql_next_action() -> None:
    payload = readiness_payload()
    payload["details"]["persistence"]["runtimeMode"] = "sqlite"
    payload["details"]["persistence"]["production_persistence_supported"] = False
    routes = live_routes(**{"anon GET /readiness": response(200, payload)})

    _, report = run_gate(routes)

    assert "runtime:persistence" in blocker_names(report)
    blocker = next(b for b in report["blockers"] if b["check"] == "runtime:persistence")
    assert blocker["dependency"] == "postgresql"
    assert "Cloud SQL" in blocker["next_action"]


def test_provider_not_live_blocks_with_provider_dependency() -> None:
    payload = readiness_payload()
    payload["details"]["provider"]["connectivityHealthy"] = False
    payload["details"]["provider"]["live"] = False
    routes = live_routes(**{"anon GET /readiness": response(200, payload)})

    _, report = run_gate(routes)

    blocker = next(b for b in report["blockers"] if b["check"] == "runtime:provider")
    assert blocker["dependency"] == "provider"


def test_blocking_reasons_are_surfaced_verbatim() -> None:
    payload = readiness_payload()
    payload["details"]["data"]["blockingReasons"] = ["PRODUCTION_MODEL_BINDINGS_UNVERIFIED"]
    routes = live_routes(**{"anon GET /readiness": response(200, payload)})

    _, report = run_gate(routes)

    blocker = next(b for b in report["blockers"] if b["check"] == "runtime:no_blocking_reasons")
    assert "PRODUCTION_MODEL_BINDINGS_UNVERIFIED" in blocker["detail"]


def test_fixture_marker_in_readiness_blocks_as_data_binding() -> None:
    payload = readiness_payload()
    payload["details"]["data"]["origin"]["kind"] = "fixture"
    routes = live_routes(**{"anon GET /readiness": response(200, payload)})

    _, report = run_gate(routes)

    assert "runtime:no_surrogate_markers" in blocker_names(report)
    assert "data-binding" in report["blocking_dependencies"]


def test_degraded_operator_repository_origin_blocks_on_postgresql() -> None:
    """"unavailable" is the readiness spelling for a missing live repository."""
    payload = readiness_payload()
    payload["details"]["data"]["origin"] = {
        "kind": "unavailable",
        "sourceId": None,
        "persistenceMode": "postgresql",
    }
    routes = live_routes(**{"anon GET /readiness": response(200, payload)})

    _, report = run_gate(routes)

    blocker = next(b for b in report["blockers"] if b["check"] == "runtime:data_origin")
    assert blocker["dependency"] == "postgresql"


# ---------------------------------------------------------------------------
# Authentication boundary
# ---------------------------------------------------------------------------


def test_anonymous_operator_access_that_succeeds_blocks_the_release() -> None:
    routes = live_routes(
        **{
            "anon GET /api/v1/operator/bootstrap": response(
                200, {"data_mode": "live", "data_source": "postgresql://operator-live"}
            )
        }
    )
    _, report = run_gate(routes)

    blocker = next(b for b in report["blockers"] if b["check"] == "auth:anonymous_denied")
    assert blocker["dependency"] == "auth"
    assert "expected 401/403" in blocker["detail"]


def test_authenticated_operator_rejection_blocks() -> None:
    routes = live_routes(**{"GET /api/v1/operator/bootstrap": response(403, {})})
    _, report = run_gate(routes)

    assert "auth:operator_bootstrap" in blocker_names(report)


def test_operator_bootstrap_serving_seed_data_blocks() -> None:
    routes = live_routes(
        **{
            "GET /api/v1/operator/bootstrap": response(
                200, {"data_mode": "live", "data_source": "r4-seed"}
            )
        }
    )
    _, report = run_gate(routes)

    assert "auth:operator_bootstrap:provenance" in blocker_names(report)


def test_operator_envelope_declaring_fixture_mode_under_meta_blocks() -> None:
    """The envelope declares its mode at meta.dataMode, and the gate reads it.

    A gate that only knew the readiness shape would read no mode at all here
    and block every release -- including a healthy one -- on a missing field.
    """
    routes = live_routes(
        **{
            "GET /api/v1/operator/bootstrap": response(
                200,
                {
                    "meta": {
                        "source": "operator-shell-api-envelope",
                        "dataMode": "fixture",
                        "dataOrigin": {"kind": "fixture", "sourceId": "r4-seed"},
                    }
                },
            )
        }
    )
    _, report = run_gate(routes)

    blocker = next(
        b for b in report["blockers"] if b["check"] == "auth:operator_bootstrap:provenance"
    )
    assert blocker["dependency"] == "data-binding"
    assert "data_mode=fixture" in blocker["detail"]


def run_gate_with_web(web_response: Any) -> dict[str, Any]:
    _, report = run_gate(web_http=FakeHttp({"anon GET /operator": web_response}))
    return report


def test_protected_web_operator_route_passes() -> None:
    report = run_gate_with_web(
        response(302, location=f"{WEB_URL}/login?returnTo=%2Foperator")
    )

    assert report["ok"] is True, report["blockers"]


def test_web_operator_route_served_without_login_blocks() -> None:
    report = run_gate_with_web(response(200))

    blocker = next(
        b for b in report["blockers"] if b["check"] == "auth:web_operator_requires_login"
    )
    assert blocker["dependency"] == "auth"


# ---------------------------------------------------------------------------
# Model lineage / MLflow aliases
# ---------------------------------------------------------------------------


def test_missing_production_alias_blocks_that_service_only() -> None:
    payload = models_payload()
    for item in payload["items"]:
        if item["model_name"] == "heatzone_priority":
            item["aliases"] = ["challenger"]
    routes = live_routes(**{"GET /api/v1/learninghub/models": response(200, payload)})

    _, report = run_gate(routes)

    assert "models:heatzone:production_alias" in blocker_names(report)
    assert "models:avm:production_alias" not in blocker_names(report)
    blocker = next(b for b in report["blockers"] if b["check"] == "models:heatzone:production_alias")
    assert blocker["dependency"] == "mlflow"


def test_ambiguous_production_alias_blocks() -> None:
    payload = models_payload()
    duplicate = deepcopy(payload["items"][0])
    duplicate["version"] = "8"
    payload["items"].append(duplicate)
    routes = live_routes(**{"GET /api/v1/learninghub/models": response(200, payload)})

    _, report = run_gate(routes)

    assert "models:avm:production_alias" in blocker_names(report)


def test_non_object_store_artifact_uri_blocks() -> None:
    payload = models_payload()
    payload["items"][0]["artifact_uri"] = "/var/local/models/dealroom_avm/7"
    routes = live_routes(**{"GET /api/v1/learninghub/models": response(200, payload)})

    _, report = run_gate(routes)

    blocker = next(b for b in report["blockers"] if b["check"] == "models:avm:artifact_store")
    assert blocker["dependency"] == "object-store"


def test_unapproved_model_version_blocks_lineage() -> None:
    payload = models_payload()
    payload["items"][0]["approved_by"] = None
    payload["items"][0]["approved_at"] = None
    routes = live_routes(**{"GET /api/v1/learninghub/models": response(200, payload)})

    _, report = run_gate(routes)

    assert "models:avm:lineage" in blocker_names(report)


# ---------------------------------------------------------------------------
# Real source rows and lineage
# ---------------------------------------------------------------------------


def test_empty_ingestion_history_blocks() -> None:
    routes = live_routes(
        **{
            "GET /api/v1/external-data/ingestion-runs?limit=100": response(
                200, {"items": [], "count": 0}
            )
        }
    )
    _, report = run_gate(routes)

    assert "data:ingestion_runs" in blocker_names(report)
    assert "external-data" in report["blocking_dependencies"]


def test_missing_required_provider_run_blocks() -> None:
    dropped = schedulable_required_provider_ids()[0]
    payload = ingestion_payload()
    payload["items"] = [item for item in payload["items"] if item["provider_id"] != dropped]
    routes = live_routes(
        **{"GET /api/v1/external-data/ingestion-runs?limit=100": response(200, payload)}
    )

    _, report = run_gate(routes)

    assert f"data:{dropped}:run_exists" in blocker_names(report)
    assert "external-data" in report["blocking_dependencies"]


def test_enrichment_provider_needs_no_ingestion_run() -> None:
    """The regression that made this gate roll back every healthy deploy.

    ``geocode.primary_api`` is required in live mode but is not
    snapshot-schedulable, so no ``SUCCEEDED`` ingestion run for it can exist in
    any environment. Demanding one made the gate unpassable.
    """
    payload = ingestion_payload()

    assert all(item["provider_id"] != "geocode.primary_api" for item in payload["items"])

    _, report = run_gate(
        live_routes(
            **{"GET /api/v1/external-data/ingestion-runs?limit=100": response(200, payload)}
        )
    )

    assert report["ok"] is True, report["blockers"]
    assert report["inputs"]["enrichment_provider_ids"] == ["geocode.primary_api"]


def test_unhealthy_geocode_probe_blocks_on_provider() -> None:
    """Geocode liveness is proven by the surface that actually calls it."""
    readiness = readiness_payload()
    readiness["details"]["provider"]["probeEvidence"]["probes"] = [
        provider_probe(provider_id)
        if provider_id != "geocode.primary_api"
        else provider_probe(
            provider_id,
            connectivity_healthy=False,
            schema_valid=False,
            reason_code="schema_invalid",
        )
        for provider_id in gate.DEFAULT_REQUIRED_PROVIDER_IDS
    ]

    _, report = run_gate(live_routes(**{"anon GET /readiness": response(200, readiness)}))

    blocker = next(
        b
        for b in report["blockers"]
        if b["check"] == "runtime:provider_probe:geocode.primary_api"
    )
    assert blocker["dependency"] == "provider"
    assert "schema_invalid" in blocker["detail"]


def test_readiness_without_probe_evidence_for_a_required_provider_blocks() -> None:
    readiness = readiness_payload()
    readiness["details"]["provider"]["probeEvidence"]["probes"] = [
        probe
        for probe in readiness["details"]["provider"]["probeEvidence"]["probes"]
        if probe["provider_id"] != "geocode.primary_api"
    ]

    _, report = run_gate(live_routes(**{"anon GET /readiness": response(200, readiness)}))

    assert "runtime:provider_probe:geocode.primary_api" in blocker_names(report)
    assert "provider" in report["blocking_dependencies"]


def test_provider_aggregate_health_alone_cannot_carry_a_broken_provider() -> None:
    """The aggregate booleans stay green; only the per-provider probe fails."""
    readiness = readiness_payload()
    readiness["details"]["provider"]["probeEvidence"] = {}

    _, report = run_gate(live_routes(**{"anon GET /readiness": response(200, readiness)}))

    assert "runtime:provider" not in blocker_names(report)
    assert {
        f"runtime:provider_probe:{provider_id}"
        for provider_id in gate.DEFAULT_REQUIRED_PROVIDER_IDS
    } <= blocker_names(report)


def test_worker_probe_provider_must_be_snapshot_schedulable() -> None:
    _, report = run_gate(cfg=config(worker_probe_provider_id="geocode.primary_api"))

    blocker = next(
        b for b in report["blockers"] if b["check"] == "config:worker_probe_provider"
    )
    assert blocker["dependency"] == "config"
    assert "geocode.primary_api" in blocker["detail"]


def test_worker_probe_defaults_to_a_schedulable_provider() -> None:
    _, report = run_gate()

    probe_provider = report["inputs"]["worker_probe_provider_id"]
    assert probe_provider in schedulable_required_provider_ids()
    assert report["inputs"]["snapshot_provider_ids"] == list(
        schedulable_required_provider_ids()
    )


def test_required_set_without_any_schedulable_provider_blocks() -> None:
    _, report = run_gate(
        cfg=config(required_provider_ids=("geocode.primary_api",)),
        routes=live_routes(),
    )

    assert "config:snapshot_providers" in blocker_names(report)
    assert report["ok"] is False


def test_unclassified_required_provider_blocks_as_registry_drift() -> None:
    _, report = run_gate(
        cfg=config(
            required_provider_ids=(
                "admin_boundary.official_dataset",
                "brand_new.provider",
            )
        )
    )

    blocker = next(
        b for b in report["blockers"] if b["check"] == "config:provider_registry_known"
    )
    assert "brand_new.provider" in blocker["detail"]


def test_missing_web_url_blocks_instead_of_skipping_the_route_assertion() -> None:
    _, report = run_gate(cfg=config(web_url=""), web_http=None)

    names = blocker_names(report)
    assert "config:web_url" in names
    assert report["ok"] is False


def test_unusable_web_client_blocks_the_protected_route_check() -> None:
    _, report = run_gate(web_http=None)

    blocker = next(
        b for b in report["blockers"] if b["check"] == "auth:web_operator_requires_login"
    )
    assert WEB_URL in blocker["detail"]


def test_zero_row_ingestion_run_blocks() -> None:
    payload = ingestion_payload()
    payload["items"][0].update(
        {"total_count": 0, "accepted_count": 0, "quarantined_count": 0, "lineage": []}
    )
    routes = live_routes(
        **{"GET /api/v1/external-data/ingestion-runs?limit=100": response(200, payload)}
    )

    _, report = run_gate(routes)

    provider = payload["items"][0]["provider_id"]
    assert f"data:{provider}:row_counts" in blocker_names(report)


def test_lineage_that_does_not_reconcile_with_row_counts_blocks() -> None:
    payload = ingestion_payload()
    payload["items"][0]["lineage"] = payload["items"][0]["lineage"][:1]
    routes = live_routes(
        **{"GET /api/v1/external-data/ingestion-runs?limit=100": response(200, payload)}
    )

    _, report = run_gate(routes)

    provider = payload["items"][0]["provider_id"]
    blocker = next(b for b in report["blockers"] if b["check"] == f"data:{provider}:lineage")
    assert "lineageRows=1" in blocker["detail"]


def test_run_without_canonical_snapshot_blocks_on_object_store() -> None:
    payload = ingestion_payload()
    payload["items"][0]["canonical_snapshot_id"] = ""
    routes = live_routes(
        **{"GET /api/v1/external-data/ingestion-runs?limit=100": response(200, payload)}
    )

    _, report = run_gate(routes)

    provider = payload["items"][0]["provider_id"]
    blocker = next(
        b for b in report["blockers"] if b["check"] == f"data:{provider}:snapshot_binding"
    )
    assert blocker["dependency"] == "object-store"


# ---------------------------------------------------------------------------
# Worker and durable audit receipts
# ---------------------------------------------------------------------------


def test_enqueue_rejection_blocks_on_worker() -> None:
    routes = live_routes(**{"POST /api/v1/jobs": response(503, {})})
    _, report = run_gate(routes)

    blocker = next(b for b in report["blockers"] if b["check"] == "worker:enqueue")
    assert blocker["dependency"] == "worker"
    assert "expected 202" in blocker["detail"]


def test_non_idempotent_replay_blocks() -> None:
    routes = live_routes(
        **{
            "POST /api/v1/jobs": [
                response(202, enqueue_payload(created=True)),
                response(202, {**enqueue_payload(created=True), "job_id": "job-other"}),
            ]
        }
    )
    _, report = run_gate(routes)

    assert "worker:idempotent_replay" in blocker_names(report)


def test_worker_that_never_reaches_terminal_success_blocks() -> None:
    routes = live_routes(
        **{
            f"GET /api/v1/jobs/{JOB_ID}": response(
                200,
                {
                    "job_id": JOB_ID,
                    "status": "queued",
                    "attempts": 0,
                    "error_message": None,
                },
            )
        }
    )
    _, report = run_gate(routes)

    blocker = next(b for b in report["blockers"] if b["check"] == "worker:terminal_success")
    assert blocker["dependency"] == "worker"
    assert "Cloud Scheduler" in blocker["next_action"]


def test_failed_job_blocks_immediately() -> None:
    routes = live_routes(
        **{
            f"GET /api/v1/jobs/{JOB_ID}": response(
                200,
                {
                    "job_id": JOB_ID,
                    "status": "failed",
                    "attempts": 3,
                    "error_message": "provider timeout",
                },
            )
        }
    )
    _, report = run_gate(routes)

    blocker = next(b for b in report["blockers"] if b["check"] == "worker:terminal_success")
    assert "provider timeout" in blocker["detail"]


def test_worker_drain_trigger_failure_blocks() -> None:
    driver = FakeWorkerDriver(ok=False, detail="worker job odp-worker execution exit=1")
    _, report = run_gate(worker_driver=driver)

    assert driver.calls == 1
    assert "worker:drain_trigger" in blocker_names(report)


def test_missing_durable_audit_receipt_blocks() -> None:
    routes = live_routes(
        **{
            f"GET /api/v1/audit/events?correlation_id={CORRELATION_ID}": response(
                200, {"events": []}
            )
        }
    )
    _, report = run_gate(routes)

    blocker = next(b for b in report["blockers"] if b["check"] == "audit:durable_receipt")
    assert blocker["dependency"] == "audit"


def test_audit_receipt_without_integrity_envelope_blocks() -> None:
    routes = live_routes(
        **{
            f"GET /api/v1/audit/events?correlation_id={CORRELATION_ID}": response(
                200, audit_payload(integrity=False)
            )
        }
    )
    _, report = run_gate(routes)

    assert "audit:receipt_integrity" in blocker_names(report)


def test_audit_receipt_for_a_different_job_does_not_count() -> None:
    payload = audit_payload()
    for event in payload["events"]:
        event["job_id"] = "job-someone-else"
    routes = live_routes(
        **{
            f"GET /api/v1/audit/events?correlation_id={CORRELATION_ID}": response(200, payload)
        }
    )
    _, report = run_gate(routes)

    assert "audit:durable_receipt" in blocker_names(report)


# ---------------------------------------------------------------------------
# Worker driver argv contract
# ---------------------------------------------------------------------------


def test_cloud_run_worker_driver_builds_a_shell_free_argv() -> None:
    captured: dict[str, Any] = {}

    def runner(argv: list[str], timeout: float) -> tuple[int, str]:
        captured["argv"] = list(argv)
        captured["timeout"] = timeout
        return 0, "done"

    driver = gate.CloudRunWorkerDriver(
        job="odp-worker-r-abc123",
        region="asia-east1",
        project="odp-dev",
        runner=runner,
    )
    ok, detail = driver.drain()

    assert ok is True
    assert "odp-worker-r-abc123" in detail
    assert captured["argv"][:5] == ["gcloud", "run", "jobs", "execute", "odp-worker-r-abc123"]
    assert "--region=asia-east1" in captured["argv"]
    assert "--project=odp-dev" in captured["argv"]
    assert "--wait" in captured["argv"]


def test_cloud_run_worker_driver_reports_a_failed_execution() -> None:
    driver = gate.CloudRunWorkerDriver(
        job="odp-worker",
        region="asia-east1",
        project="odp-dev",
        runner=lambda argv, timeout: (1, "PERMISSION_DENIED"),
    )
    ok, detail = driver.drain()

    assert ok is False
    assert "PERMISSION_DENIED" in detail


def test_scheduled_worker_driver_is_explicit_about_not_triggering() -> None:
    ok, detail = gate.ScheduledWorkerDriver().drain()

    assert ok is True
    assert "scheduled worker" in detail


# ---------------------------------------------------------------------------
# CLI contract
# ---------------------------------------------------------------------------


def test_cli_fails_closed_without_any_configuration(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(CHECKER),
            "--api-url",
            "",
            "--expected-sha",
            "",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={"PATH": "/usr/bin:/bin"},
    )

    assert completed.returncode == 1
    assert "Live E2E gate failed" in completed.stdout
    assert "config:" in completed.stdout
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report["blocking_dependencies"] == ["config"]
    assert all(blocker["next_action"] for blocker in report["blockers"])


# ---------------------------------------------------------------------------
# Anti-drift: the gate's expectations are pinned to the real runtime code, not
# to the doubles above. Fixtures that merely restate the gate's assumptions can
# stay green while the gate is unable to pass against the deployed runtime.
# ---------------------------------------------------------------------------


def test_readiness_origin_kind_emitted_by_the_runtime_is_accepted() -> None:
    """`/readiness` publishes OperatorLiveRepository.data_origin verbatim.

    That property spells a healthy origin "authoritative". Asserting "live"
    here would make the gate permanently red against a healthy deployment.
    """
    from modules.opsboard.application.operator_live_repository import (
        OperatorLiveRepository,
    )

    class _Persistence:
        mode = "postgresql"

    origin = OperatorLiveRepository(_Persistence()).data_origin

    assert origin["kind"] in gate.LIVE_ORIGIN_KINDS
    assert str(origin["persistenceMode"]).lower() in gate.POSTGRES_MODES


def test_operator_envelope_mode_key_is_the_one_the_gate_reads() -> None:
    """The envelope declares its mode at meta.dataMode; the gate must read it."""
    source = (
        ROOT / "modules/opsboard/application/operator_state.py"
    ).read_text(encoding="utf-8")

    assert '"dataMode": (' in source, "operator envelope no longer emits meta.dataMode"
    assert gate._declared_data_mode({"meta": {"dataMode": "live"}}) == "live"
    assert gate._declared_data_mode({"meta": {"dataMode": "fixture"}}) == "fixture"


def test_every_api_path_the_gate_calls_is_routed_by_the_deployed_api() -> None:
    """A 404 from a renamed route would be reported as a runtime dependency."""
    main = (ROOT / "apps/api/oday_api/main.py").read_text(encoding="utf-8")
    operator = (ROOT / "apps/api/app/routes/operator.py").read_text(encoding="utf-8")
    learninghub = (ROOT / "apps/api/app/routes/learninghub.py").read_text(encoding="utf-8")
    external = (ROOT / "apps/api/app/routes/external_data.py").read_text(encoding="utf-8")

    assert '@api.get("/platform/version"' in main
    assert '@api.get("/readiness"' in main
    assert '@platform_router.post("/jobs"' in main
    assert '@platform_router.get("/jobs/{job_id}"' in main
    assert '@platform_router.get("/audit/events"' in main
    assert '@router.get("/bootstrap"' in operator
    assert 'prefix="/operator"' in operator
    assert '"/models",' in learninghub
    assert 'prefix="/learninghub"' in learninghub
    assert '@router.get("/ingestion-runs"' in external
    assert 'prefix="/external-data"' in external


def test_pinned_provider_categories_match_the_runtime_registry() -> None:
    """The gate's registry mirror must not drift from provider_registry()."""
    from modules.external_data.connectors.provider_registry import provider_registry
    from modules.external_data.workers.scheduled_fetch import _SCHEDULABLE_CATEGORIES

    assert gate.PROVIDER_CATEGORIES == {
        provider.provider_id: provider.category.value for provider in provider_registry()
    }
    assert gate.SNAPSHOT_SCHEDULABLE_CATEGORIES == {
        category.value for category in _SCHEDULABLE_CATEGORIES
    }


def test_required_provider_ids_match_the_runtime_live_required_set() -> None:
    from modules.external_data.connectors.provider_registry import (
        REQUIRED_PRODUCTION_PROVIDER_IDS,
    )

    assert set(gate.DEFAULT_REQUIRED_PROVIDER_IDS) == set(REQUIRED_PRODUCTION_PROVIDER_IDS)


def test_ingestion_run_requirement_is_bound_to_scheduler_schedulability() -> None:
    """The set the gate demands runs for == the set a scheduler would accept.

    This is the binding the previous revision lacked: the gate required a
    persisted SUCCEEDED ingestion run for every required provider, including
    ``geocode.primary_api``, which ``ExternalFetchScheduler`` refuses with
    ``provider_not_schedulable``. The requirement is now derived from the same
    category rule the scheduler enforces.
    """
    from modules.external_data.connectors.provider_registry import provider_registry
    from modules.external_data.workers.scheduled_fetch import _SCHEDULABLE_CATEGORIES

    schedulable = {
        provider.provider_id
        for provider in provider_registry()
        if provider.category in _SCHEDULABLE_CATEGORIES
    }
    cfg = config()

    assert set(cfg.snapshot_provider_ids) == schedulable & set(
        gate.DEFAULT_REQUIRED_PROVIDER_IDS
    )
    assert set(cfg.enrichment_provider_ids).isdisjoint(schedulable)
    assert cfg.probe_provider_id in schedulable


def test_scheduler_really_refuses_every_enrichment_provider_the_gate_exempts() -> None:
    """Behavioural proof, not a restatement of the category constants."""
    from modules.external_data.workers.scheduled_fetch import (
        ExternalFetchProviderConfigurationError,
        ExternalFetchScheduler,
    )

    scheduler = ExternalFetchScheduler(env={})
    for provider_id in config().enrichment_provider_ids:
        with pytest.raises(ExternalFetchProviderConfigurationError) as excinfo:
            scheduler._assert_provider_schedulable_and_selected(provider_id)
        assert excinfo.value.code == "provider_not_schedulable"

    for provider_id in config().snapshot_provider_ids:
        scheduler._assert_provider_schedulable_and_selected(provider_id)


def _live_readiness_details_from_the_real_app(*, healthy: bool) -> dict[str, Any]:
    """Boot the deployed API and read `/readiness` details.provider for real.

    Nothing here is fabricated by this test file: the probe evidence is
    serialized by `ProviderProbeEvidence.to_dict` and published by the real
    readiness handler, so a key rename in either would fail the gate check
    below instead of quietly passing against a fixture.
    """
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from apps.api.oday_api.main import create_app
    from modules.external_data.connectors.provider_connectivity import (
        ProviderConnectivityResult,
        ProviderProbeEvidence,
    )
    from modules.external_data.connectors.provider_registry import ExternalProviderMode

    checked_at = datetime.now(UTC)
    probes = tuple(
        ProviderProbeEvidence(
            provider_id=provider_id,
            configuration_valid=True,
            connectivity_healthy=healthy,
            authentication_accepted=healthy,
            response_valid=healthy,
            schema_valid=healthy,
            checked_at=checked_at,
            expires_at=checked_at + timedelta(seconds=60),
            latency_ms=7,
            http_status=200 if healthy else 401,
            reason_code="ok" if healthy else "unauthorized",
        )
        for provider_id in gate.DEFAULT_REQUIRED_PROVIDER_IDS
    )
    connectivity = ProviderConnectivityResult(
        mode=ExternalProviderMode.LIVE,
        correlation_id="corr-live-e2e-probe",
        configuration_valid=True,
        connectivity_healthy=healthy,
        checked_at=checked_at,
        expires_at=checked_at + timedelta(seconds=60),
        required_provider_ids=gate.DEFAULT_REQUIRED_PROVIDER_IDS,
        probes=probes,
    )
    app = create_app(
        external_provider_validation=SimpleNamespace(
            ok=True, errors=(), mode=SimpleNamespace(value="live")
        ),
        external_provider_connectivity_probe=lambda **_kwargs: connectivity,
    )
    body = TestClient(app).get("/readiness").json()
    return body["details"]["provider"]


def test_gate_reads_probe_evidence_the_real_readiness_endpoint_emits() -> None:
    provider = _live_readiness_details_from_the_real_app(healthy=True)
    checks: list[Any] = []

    gate._check_provider_probe_evidence(provider, config=config(), checks=checks)

    assert checks, "readiness published no probe evidence the gate could read"
    assert {check.name for check in checks} == {
        f"runtime:provider_probe:{provider_id}"
        for provider_id in gate.DEFAULT_REQUIRED_PROVIDER_IDS
    }
    assert all(check.ok for check in checks), [c for c in checks if not c.ok]


def test_gate_rejects_an_unhealthy_probe_the_real_readiness_endpoint_emits() -> None:
    provider = _live_readiness_details_from_the_real_app(healthy=False)
    checks: list[Any] = []

    gate._check_provider_probe_evidence(provider, config=config(), checks=checks)

    failed = {check.name for check in checks if not check.ok}
    assert "runtime:provider_probe:geocode.primary_api" in failed


def test_worker_probe_job_type_is_registered_by_the_deployed_worker() -> None:
    """An unregistered job type would never reach a terminal success."""
    handlers = (ROOT / "apps/worker/oday_worker/handlers.py").read_text(encoding="utf-8")

    assert f'EXTERNAL_FETCH_JOB_TYPE = "{gate.WORKER_PROBE_JOB_TYPE}"' in handlers
    assert "registry.register(EXTERNAL_FETCH_JOB_TYPE, handle_external_fetch)" in handlers


def test_audit_receipt_integrity_envelope_matches_the_runtime_serializer() -> None:
    source = (ROOT / "shared/audit/events.py").read_text(encoding="utf-8")

    assert 'payload["integrity"] = {' in source
    assert '"sequence": self.sequence,' in source
    assert '"event_hash": self.event_hash,' in source


def test_web_login_redirect_contract_matches_the_deployed_middleware() -> None:
    middleware = (ROOT / "apps/web/src/middleware.ts").read_text(encoding="utf-8")

    assert 'new URL("/login", request.url)' in middleware
    assert '"returnTo",' in middleware

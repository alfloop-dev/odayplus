"""Actual factory/deploy admission and owned GCS protocol; no cloud evidence."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from apps.api.oday_api.runtime_mode import deployment_mode
from shared.audit import AuditEvent
from shared.audit.worm import (
    AuditWormSinkError,
    GcsWormEvidenceSink,
    LocalAppendOnlyWormSink,
    build_audit_worm_sink_from_env,
)
from tests.integration._gcs_worm_stub import GcsWormProtocolStub

ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = ROOT / "product_ops/deployment/deploy_cloud_run_waji.sh"
WORKFLOW = ROOT / ".github/workflows/deploy-dev.yml"
MODE_KEYS = ("ODP_DEPLOY_ENV", "ODAY_ENV", "ODP_ENV", "APP_ENV", "ENVIRONMENT")
OWNED_URI = "gs://synthetic-bucket/owned-release-control"
SPEC = importlib.util.spec_from_file_location("audit_worm_release_validator", ROOT / "product_ops/deployment/validate_cloud_run_live_deployment.py")
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)


def _set_mode(monkeypatch, **values):
    for name in MODE_KEYS:
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("ODP_AUDIT_WORM_LOCAL_PATH", raising=False)


@pytest.mark.parametrize("uri", ["", "/tmp/owned-worm", "file:///tmp/owned-worm", "https://owned.invalid/worm", "gs://", "GS://synthetic-bucket/owned", "gs://user@bucket/path", "gs://bucket/path?credential=not-a-real-secret", "gs://bucket#fragment"])
def test_production_factory_refuses_local_or_ambiguous_before_creating_files(monkeypatch, tmp_path, uri):
    _set_mode(monkeypatch, ODP_DEPLOY_ENV="production")
    monkeypatch.setenv("ODP_AUDIT_WORM_SINK_URI", uri)
    local_root = tmp_path / "must-not-be-created"
    with pytest.raises(AuditWormSinkError, match="Production audit WORM requires a configured GCS sink URI"):
        build_audit_worm_sink_from_env(default_root=local_root)
    assert not local_root.exists()


@pytest.mark.parametrize("mode_key", MODE_KEYS)
def test_factory_uses_actual_server_mode_aliases(monkeypatch, tmp_path, mode_key):
    _set_mode(monkeypatch, **{mode_key: "PRODUCTION"})
    assert deployment_mode() == "production"
    monkeypatch.delenv("ODP_AUDIT_WORM_SINK_URI", raising=False)
    with pytest.raises(AuditWormSinkError):
        build_audit_worm_sink_from_env(default_root=tmp_path / "no-local-fallback")
    assert not (tmp_path / "no-local-fallback").exists()


@pytest.mark.parametrize("mode", ["dev", "staging", "stage", "development", "poc"])
def test_explicit_nonproduction_retains_local_engineering_sink(monkeypatch, tmp_path, mode):
    _set_mode(monkeypatch, ODP_DEPLOY_ENV=mode, APP_ENV="production")
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setenv("ODP_PRODUCT_MODE", "production")
    monkeypatch.delenv("ODP_AUDIT_WORM_SINK_URI", raising=False)
    assert deployment_mode() == mode
    target = build_audit_worm_sink_from_env(default_root=tmp_path / "owned-local")
    assert isinstance(target, LocalAppendOnlyWormSink)
    assert target.sink_id == (tmp_path / "owned-local").as_uri()


def _preflight(mode, uri):
    env = {name: "configured-" + name.lower() for name in validator.REQUIRED_PUBLIC_CONFIG}
    env.update({name: "owned-secret:1" for name in validator.REQUIRED_SECRET_REFERENCES})
    env.update(validator.REQUIRED_RUNTIME_VALUES)
    env.update(ODP_DEPLOY_ENV=mode, ODAY_RELEASE_SHA="a" * 40,
               ODP_AUTH_MODE="local", ODP_FORECAST_ENGINE="statsforecast",
               ODP_FORECAST_MODEL="seasonal_naive", ODP_AUDIT_WORM_SINK_URI=uri,
               ODP_SCHEDULED_INGESTION_TENANT_ID="owned-release-tenant")
    return {c.name: c for c in validator.preflight_checks(env=env, expected_environment=mode, expected_sha="a" * 40, root=ROOT)}


@pytest.mark.parametrize("uri", ["", "file:///tmp/owned-audit", "https://owned.invalid/audit", "gs://"])
def test_actual_production_preflight_refuses_local_without_echo(uri):
    check = _preflight("production", uri)["config:ODP_AUDIT_WORM_SINK_URI"]
    assert check.ok is False
    assert check.detail == "production audit WORM requires a configured GCS sink URI"


def test_actual_preflight_admits_address_preserves_dev_staging():
    assert _preflight("production", OWNED_URI)["config:ODP_AUDIT_WORM_SINK_URI"].ok is True
    assert "config:ODP_AUDIT_WORM_SINK_URI" not in _preflight("dev", "")
    assert "config:ODP_AUDIT_WORM_SINK_URI" not in _preflight("staging", "file:///tmp/owned-audit")


def test_actual_workflow_and_api_serializer_forward_exact_uri(tmp_path):
    workflow = yaml.safe_load(WORKFLOW.read_text())
    assert workflow["jobs"]["deploy"]["env"]["ODP_AUDIT_WORM_SINK_URI"] == "${{ vars.ODP_AUDIT_WORM_SINK_URI }}"
    script = DEPLOY_SCRIPT.read_text()
    marker = 'python3 - "${API_ENV_FILE}" <<\'PY\''
    start = script.index("\n", script.index(marker)) + 1
    serializer = script[start:script.index("\nPY\n", start)]
    output = tmp_path / "owned-api-environment.json"
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "ODP_DEPLOY_ENV": "production", "ODAY_RELEASE_SHA": "a" * 40,
           "ODP_AUDIT_WORM_SINK_URI": OWNED_URI, "ODP_SCHEDULED_INGESTION_TENANT_ID": "owned-release-tenant"}
    subprocess.run([sys.executable, "-c", serializer, str(output)], env=env, check=True, capture_output=True, text=True)
    rendered = json.loads(output.read_text())
    assert rendered["ODP_AUDIT_WORM_SINK_URI"] == OWNED_URI
    assert rendered["ODAY_ENV"] == rendered["ODP_ENV"] == "production"
    assert rendered["ODP_TENANT_ID"] == "owned-release-tenant"
    assert script.count('--env-vars-file="${API_ENV_FILE}"') == 4


def test_factory_reaches_real_gcs_create_and_separate_recovery(monkeypatch, tmp_path):
    _set_mode(monkeypatch, ODP_DEPLOY_ENV="production")
    monkeypatch.setenv("ODP_AUDIT_WORM_SINK_URI", OWNED_URI)
    server = GcsWormProtocolStub()
    try:
        monkeypatch.setenv("ODP_AUDIT_WORM_GCS_TOKEN", server.writer_token)
        monkeypatch.setenv("ODP_AUDIT_WORM_GCS_RECOVERY_TOKEN", server.recovery_token)
        target = build_audit_worm_sink_from_env(default_root=tmp_path / "no-local-fallback")
        assert isinstance(target, GcsWormEvidenceSink)
        assert not (tmp_path / "no-local-fallback").exists()
        server.bind(target, monkeypatch)
        event = AuditEvent(event_type="synthetic.production-worm-binding", actor="owned-release-control", action="verify",
                           resource="owned-audit-record", outcome="accepted", correlation_id="owned-production-worm-control")
        first = target.write_audit_event(event)
        replay = target.write_audit_event(event)
        assert replay == first
        assert first.object_uri.startswith(OWNED_URI + "/audit-events/")
        assert server.creations == len(server.objects) == 1
        assert [row[0] for row in server.requests] == ["POST", "POST", "GET"]
        assert [row[2] for row in server.requests] == ["Bearer " + server.writer_token, "Bearer " + server.writer_token, "Bearer " + server.recovery_token]
    finally:
        server.close()
        assert not server.thread.is_alive()

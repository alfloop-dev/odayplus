"""Real default worker/registry/factory/owner with synthetic GCS only.

Claims, original admission, both leases, all journals and terminal writes are
canonical PostgreSQL facts. Telemetry and constructor-injected storage are
engineering seams; no production/staging/UAT or complete Intake claim follows.
"""
import json
from contextlib import nullcontext
from dataclasses import fields
from types import SimpleNamespace

import pytest

from apps.worker.oday_worker.main import ODayWorker, _LeaseHeartbeat
from modules.external_data.application import native_parser_artifacts as artifacts
from modules.external_data.application.native_parser_deployments import DEPLOYMENTS_ENV
from modules.listing.application.native_intake_parser_policy import NativeParserUnavailable
from shared.infrastructure.persistence.factory import PersistenceBundle
from shared.jobs.queue import JobStatus
from tests.integration.test_native_intake_identity_postgresql import defer_audit
from tests.integration.test_native_intake_parser_admission_postgresql import admit, persisted
from tests.integration.test_native_intake_parser_io_postgresql import authority as authority
from tests.integration.test_native_intake_parser_io_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_parser_io_postgresql import identity_case as identity_case
from tests.integration.test_native_intake_parser_io_postgresql import (
    intake_blank_db as intake_blank_db,
)
from tests.integration.test_native_intake_parser_io_postgresql import native_api as native_api
from tests.integration.test_native_intake_parser_io_postgresql import (
    parser_admission_case as parser_admission_case,
)
from tests.integration.test_native_intake_parser_io_postgresql import (
    parser_evidence_fields as parser_evidence_fields,
)
from tests.integration.test_native_intake_parser_io_postgresql import parser_realm as parser_realm
from tests.integration.test_native_intake_parser_io_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_parser_io_postgresql import start_case as start_case
from tests.integration.test_native_intake_parser_recovery_postgresql import settle_seed
from tests.integration.test_native_intake_parser_retry_postgresql import rows

pytestmark = pytest.mark.requires_live_env
_PRIVATE = "private-startup-fault-value-do-not-log"


class _Telemetry:
    """Observation sink only; never supplies an owner, grant or job outcome."""

    def __init__(self):
        self.events = []
        self.logger = SimpleNamespace(info=self.info, error=self.error)
        self.metrics = SimpleNamespace(set=self.metric, observe=self.metric, increment=self.metric)
        self.tracer = SimpleNamespace(start_span=lambda *args, **kwargs: nullcontext())

    def info(self, message, *args, **kwargs):
        self.events.append(("info", message, args, kwargs))

    def error(self, message, *args, **kwargs):
        self.events.append(("error", message, args, kwargs))

    def metric(self, *args, **kwargs):
        pass


def startup(case, monkeypatch):
    deployment = case[0].artifact_registry._deployments[case[4]]
    monkeypatch.setenv(DEPLOYMENTS_ENV, json.dumps({"version": 1, "deployments": [{
        "parser_release_id": deployment.parser_release_id, "source_id": deployment.source_id,
        "artifact_uri": deployment.archive_uri, "archive_path": str(deployment.archive_path)}]}))


def engineering_bundle(case):
    # Real persistence factory and canonical graph construction. The only
    # application override supplies the explicit synthetic storage port.
    seen = []

    class Bundle(PersistenceBundle):
        def canonical_intake_parser(self):
            owner = super().canonical_intake_parser(storage_factory=case[5].factory)
            seen.append(owner)
            return owner

    values = {field.name: None for field in fields(PersistenceBundle)}
    values.update(mode="postgresql", engine=case[0].engine, job_queue=case[0].queue,
        audit_log=SimpleNamespace(_worm_sink=None),
        assisted_intake_store=SimpleNamespace(_canonical_audit_sink=case[0].graph.audit_sink))
    return Bundle(**values), seen


def run_default(case, monkeypatch):
    owner = case[0]
    updates = []
    update = owner.queue.update_status

    def domain_update(*args, **kwargs):
        # Main's generic delivery-only update would enter here without the
        # canonical business transaction. Domain settlement has the real root.
        assert getattr(owner.engine._local, "connection", None) is not None
        updates.append(args[0])
        return update(*args, **kwargs)

    def forbidden_generic_heartbeat(_self):
        pytest.fail("The default heartbeat must not race a parser SERIALIZABLE root")

    monkeypatch.setattr(owner.queue, "update_status", domain_update)
    monkeypatch.setattr(_LeaseHeartbeat, "start", forbidden_generic_heartbeat)
    bundle, seen = engineering_bundle(case)
    telemetry = _Telemetry()
    defer_audit(owner.graph)
    worker = ODayWorker(persistence=bundle, telemetry=telemetry, worker_id="synthetic-native-parser-default-worker")
    assert worker.registry.has("native-intake-parser")
    assert worker.run_once() is True
    assert len(seen) == 1
    assert _PRIVATE not in json.dumps(telemetry.events, default=str)
    return bundle, seen[0], updates


@pytest.mark.parametrize("parser_evidence_fields,status,state", [
    (None, "SUCCEEDED", "MATCHING"), ("partial", "PARTIAL", "NEEDS_REVIEW")],
    indirect=["parser_evidence_fields"])
def test_real_default_worker_uses_startup_registry_and_commits_both_jobs_and_intake(
        parser_admission_case, monkeypatch, status, state):
    case = parser_admission_case
    settle_seed(case)
    startup(case, monkeypatch)
    receipt = admit(case)
    previous = persisted(case)
    bundle, native_owner, updates = run_default(case, monkeypatch)
    job, run, physical = rows(case, receipt["job_id"])
    assert job["status"] == "SUCCEEDED" and physical.status == JobStatus.SUCCEEDED and run["status"] == status
    assert job["attempt"] == physical.attempts == 1
    assert updates and set(updates) == {receipt["job_id"]}
    assert physical.error_message is None and "_retry_count" not in physical.payload
    assert job["result"]["parser_status"] == status
    current = persisted(case)
    intake = next(row for row in current["intake.intakes"] if row["intake_id"] == receipt["intake_id"])
    assert intake["processing_state"] == state and intake["version"] == receipt["admitted_intake_version"] + 1
    for table in ("intake.source_snapshots", "identity.match_cases", "expansion.listings"):
        assert current[table] == previous[table]
    assert len(job["payload"]["attempt_intents"]) == 4
    assert all(item["state"] == "FINISHED" and item["process_state"] == "NO_CHILD_REMAINS"
        for item in job["payload"]["attempt_intents"])
    # Factory construction and actual terminal replay still work when current
    # startup selection is absent or invalid; neither permits another IO.
    calls = list(case[5].calls)
    for raw in (None, _PRIVATE):
        if raw is None:
            monkeypatch.delenv(DEPLOYMENTS_ENV)
        else:
            monkeypatch.setenv(DEPLOYMENTS_ENV, raw)
        historical = bundle.canonical_intake_parser()
        assert historical.replay(physical) == {"terminal": True, "result": job["result"]}
    assert case[5].calls == calls and native_owner.artifact_registry is not None


@pytest.mark.parametrize("loss", ["absent", "malformed", "installed_unavailable"])
def test_real_default_worker_unaccepted_capability_loss_cancels_atomically_without_intake_or_io(
        parser_admission_case, monkeypatch, loss):
    case = parser_admission_case
    settle_seed(case)
    startup(case, monkeypatch)
    receipt = admit(case)
    if loss == "absent":
        monkeypatch.delenv(DEPLOYMENTS_ENV)
    elif loss == "malformed":
        monkeypatch.setenv(DEPLOYMENTS_ENV, _PRIVATE)
    else:
        def unavailable(*args, **kwargs):
            raise artifacts.NativeParserArtifactUnavailable(_PRIVATE)
        monkeypatch.setattr(artifacts, "_read_regular", unavailable)
    previous, calls = persisted(case), list(case[5].calls)
    _, native_owner, updates = run_default(case, monkeypatch)
    job, run, physical = rows(case, receipt["job_id"])
    assert job["status"] == run["status"] == "CANCELLED" and physical.status == JobStatus.CANCELLED
    assert job["attempt"] == 0 and physical.attempts == 1
    assert updates and set(updates) == {receipt["job_id"]}
    assert "_retry_count" not in physical.payload
    current = persisted(case)
    for table in ("intake.intakes", "intake.source_snapshots", "intake.human_corrections",
            "identity.match_cases", "expansion.listings"):
        assert current[table] == previous[table]
    assert case[5].calls == calls
    assert native_owner.replay(physical) == {"terminal": True, "result": None}
    if loss == "malformed":
        with pytest.raises(NativeParserUnavailable, match="^NATIVE_PARSER_DEPLOYMENT_CONFIGURATION_REJECTED$"):
            native_owner.require_deployment()
    elif loss == "installed_unavailable":
        with pytest.raises(artifacts.NativeParserArtifactUnavailable, match="^NATIVE_PARSER_ARTIFACT_READ_UNAVAILABLE$"):
            native_owner.require_deployment()

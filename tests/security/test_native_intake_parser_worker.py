"""MOCK wiring contracts only: no SQL/default-owner acceptance or reaping proof.

All assertions in this module concern local composition and dispatch/control
flow. JobRecords are current delivery fixtures, never historical Job/Intake
rows. Mock replay, acceptance, leases and settlement responses grant nothing.
The real startup parser, routing validator, owner, runtime, default registry and
worker are imported normally; no import stubs or validators patched to pass.
Root must independently review and run these source-only tests and verify SQL.
"""

import json
import socket
import subprocess
import threading
import time
import zipfile
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import MISSING, fields, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, create_autospec, sentinel

import pytest

from apps.worker.oday_worker import handlers
from apps.worker.oday_worker import main as worker_module
from modules.external_data.application import native_manual_json_parser as provider
from modules.external_data.application import native_parser_artifacts as artifact_module
from modules.external_data.application import native_parser_deployments as startup
from modules.external_data.application.native_intake_process import (
    NativeIntakeProcessRunner,
    NativeIORejected,
    NativeIOUnreaped,
)
from modules.external_data.application.native_parser_artifacts import (
    NativeParserArtifactRegistry,
)
from modules.listing.application import native_intake_parser_io as io_module
from modules.listing.application import native_intake_parser_runtime as runtime_module
from modules.listing.application.native_intake_parser import (
    NativeIntakeParserOwner,
    run_native_intake_parser_delivery,
)
from modules.listing.application.native_intake_parser_cancellation import (
    NativeParserUnacceptedCancellation,
)
from modules.listing.application.native_intake_parser_history import NativeParserTerminalHistory
from modules.listing.application.native_intake_parser_leases import NativeParserCompositeLeases
from modules.listing.application.native_intake_parser_negative import (
    NativeParserUnavailableSettlement,
)
from modules.listing.application.native_intake_parser_permanent import (
    NativeParserPermanentSettlement,
)
from modules.listing.application.native_intake_parser_policy import (
    JOB_TYPE,
    SIGNING_KEY_ENV,
    NativeParserConflict,
    NativeParserUnavailable,
)
from modules.listing.application.native_intake_parser_retry import NativeParserRetry
from shared.infrastructure.persistence.factory import PersistenceBundle
from shared.jobs.queue import (
    JobDeliveryState,
    JobFenceRejectedError,
    JobRecord,
    JobStatus,
    NonRetryableJobError,
)
from tests.security.test_native_parser_artifacts import installed_bytes, write_archive

TENANT = "eaa9709e-8a88-41e0-bd39-e01ebd19ce44"
JOB = "cd7f47b3-bde5-498a-afaf-988af7179c12"
RELEASE = "8b1657cd-d8dc-4949-b82f-2189f9d01576"
CONFIG_REJECTED = "NATIVE_PARSER_DEPLOYMENT_CONFIGURATION_REJECTED"
PRIVATE = "MOCK_PRIVATE_SNAPSHOT_CREDENTIAL_SQL_VALUE"
TERMINAL = (JobStatus.SUCCEEDED, JobStatus.PARTIAL, JobStatus.FAILED, JobStatus.CANCELLED)


def _delivery(**changes):
    return replace(JobRecord(
        JOB_TYPE,
        {"job_id": JOB, "tenant_id": TENANT, "attempt_token": "1." + "a" * 64},
        "mock-wiring-correlation",
        job_id=JOB,
        status=JobStatus.RUNNING,
        locked_by="mock-parser-worker",
        attempts=1,
        fence_token=9,
        version=4,
    ), **changes)


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    """Tripwires for future execution by Root, not evidence of real IO safety."""
    blockers = []
    for target, name in (
        (subprocess, "Popen"),
        (socket, "create_connection"),
        (socket.socket, "connect"),
        (threading.Thread, "start"),
        (NativeIntakeProcessRunner, "run"),
        (io_module.NativeManualParserProcess, "parse"),
        (io_module.NativeParserSnapshotTransport, "_run"),
    ):
        blocker = Mock(side_effect=AssertionError("MOCK_WIRING_MUST_NOT_EXECUTE_IO"))
        monkeypatch.setattr(target, name, blocker)
        blockers.append(blocker)
    yield
    for blocker in blockers:
        blocker.assert_not_called()


@pytest.fixture
def composition(monkeypatch):
    engine = Mock(spec_set=["dialect", "query_one", "query_all", "execute", "transaction"])
    engine.dialect = "postgresql"
    queue = Mock(spec_set=["get", "heartbeat", "update_status", "enqueue"])
    graph = SimpleNamespace(
        engine=engine, queue=queue,
        transaction=Mock(side_effect=AssertionError("MOCK_NO_SQL_ROOT")),
        _one=Mock(side_effect=AssertionError("MOCK_NO_SQL_READ")),
    )
    for name in ("query_one", "query_all", "execute", "transaction"):
        getattr(engine, name).side_effect = AssertionError("MOCK_NO_SQL")
    for name in ("get", "heartbeat", "update_status", "enqueue"):
        getattr(queue, name).side_effect = AssertionError("MOCK_NO_QUEUE_IO")
    required = {
        field.name: None for field in fields(PersistenceBundle)
        if field.default is MISSING and field.default_factory is MISSING
    }
    required.update(mode="postgresql", job_queue=queue, engine=engine)
    bundle = PersistenceBundle(**required)
    graph_factory = create_autospec(PersistenceBundle.canonical_identity_graph, return_value=graph)
    monkeypatch.setattr(PersistenceBundle, "canonical_identity_graph", graph_factory)
    yield SimpleNamespace(bundle=bundle, graph=graph, graph_factory=graph_factory)
    assert engine.mock_calls == []
    assert queue.mock_calls == []
    graph.transaction.assert_not_called()
    graph._one.assert_not_called()


def test_mock_absent_startup_constructs_owner_without_rejected_configuration(composition, monkeypatch):
    monkeypatch.delenv(startup.DEPLOYMENTS_ENV, raising=False)
    owner = composition.bundle.canonical_intake_parser()
    assert type(owner) is NativeIntakeParserOwner
    assert owner.graph is composition.graph
    assert owner.artifact_registry is None
    assert owner._deployment_configuration_error is None
    assert owner.require_deployment() is None
    composition.graph_factory.assert_called_once_with(composition.bundle)
    with pytest.raises(NativeParserUnavailable) as raised:
        owner._prerequisites()
    assert str(raised.value) == "NATIVE_PARSER_ARTIFACT_REGISTRY_REQUIRED"


@pytest.mark.parametrize("raw", [
    "", " ", "null", "{", PRIVATE,
    '{"version":1,"deployments":[]}',
    '{"version":true,"deployments":[]}',
    '{"version":1,"version":1,"deployments":[]}',
    json.dumps({"version": 1, "deployments": [], "provider": PRIVATE}),
])
def test_mock_malformed_startup_is_constructible_but_positive_gate_is_distinct(
    composition, monkeypatch, raw,
):
    monkeypatch.setenv(startup.DEPLOYMENTS_ENV, raw)
    owner = composition.bundle.canonical_intake_parser()
    assert type(owner) is NativeIntakeParserOwner
    assert owner.artifact_registry is None
    assert owner._deployment_configuration_error == CONFIG_REJECTED
    for gate in (owner.require_deployment, owner._prerequisites):
        with pytest.raises(NativeParserUnavailable) as raised:
            gate()
        assert str(raised.value) == CONFIG_REJECTED
        assert PRIVATE not in repr(raised.value)
    assert PRIVATE not in repr(vars(owner))


@pytest.mark.parametrize("raw", [None, PRIVATE])
def test_mock_missing_or_rejected_config_keeps_history_and_control_dispatch_callable(
    composition, monkeypatch, raw,
):
    if raw is None:
        monkeypatch.delenv(startup.DEPLOYMENTS_ENV, raising=False)
    else:
        monkeypatch.setenv(startup.DEPLOYMENTS_ENV, raw)
    owner = composition.bundle.canonical_intake_parser()
    delivery = _delivery()
    # Patch the terminal/control boundary itself, not its proof validators.
    # A returned sentinel is only a delegation observation, never a closure.
    for owner_method, target, method, kwargs in (
        ("replay", NativeParserTerminalHistory, "replay", {}),
        ("settle_unavailable", NativeParserUnavailableSettlement, "settle",
         {"business_fence": 7, "leases": None}),
        ("settle_permanent", NativeParserPermanentSettlement, "settle", {"leases": sentinel.leases}),
        ("prepare_retry", NativeParserRetry, "prepare", {"leases": sentinel.leases}),
        ("cancel_unaccepted", NativeParserUnacceptedCancellation, "cancel", {"leases": None}),
        ("recover_expired", NativeParserUnacceptedCancellation, "recover_expired", {"leases": None}),
    ):
        boundary = create_autospec(getattr(target, method), return_value=sentinel.control_response)
        with monkeypatch.context() as patch:
            patch.setattr(target, method, boundary)
            assert getattr(owner, owner_method)(delivery, **kwargs) is sentinel.control_response
        assert boundary.call_count == 1
        control = boundary.call_args.args[0]
        if owner_method == "replay":
            assert control.graph is composition.graph
        else:
            assert control.owner is owner
        assert boundary.call_args.args[1:] == (delivery,)
        assert boundary.call_args.kwargs == kwargs


class _StringCode(str):
    pass


@pytest.mark.parametrize("error", [
    "", PRIVATE, 0, False, {}, RuntimeError(CONFIG_REJECTED), _StringCode(CONFIG_REJECTED),
])
def test_mock_constructor_rejects_arbitrary_error_text_and_types(composition, error):
    with pytest.raises(ValueError) as raised:
        NativeIntakeParserOwner(composition.graph, deployment_configuration_error=error)
    assert str(raised.value) == "NATIVE_PARSER_DEPLOYMENT_ERROR_CODE_REJECTED"
    assert PRIVATE not in str(raised.value)


@pytest.mark.parametrize("name", ["storage_factory", "parser_process_factory"])
@pytest.mark.parametrize("value", [False, 0, "payload.module:provider", {}, object()])
def test_mock_constructor_rejects_noncallable_factory_injection(composition, name, value):
    with pytest.raises(TypeError) as raised:
        NativeIntakeParserOwner(composition.graph, **{name: value})
    assert str(raised.value) == "NATIVE_PARSER_TRUSTED_FACTORY_REQUIRED"


def test_mock_local_startup_archive_builds_real_registry_without_activation(
    composition, monkeypatch, tmp_path,
):
    specs = Path(provider.__file__).with_name("native_parser_specs")
    archive_path = tmp_path / "manual-parser.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("provider.py", Path(provider.__file__).read_bytes())
        for member, filename in (
            ("input-schema.json", "manual_input_v1.json"),
            ("output-schema.json", "manual_output_v1.json"),
            ("corpus.json", "manual_corpus_v1.json"),
        ):
            archive.writestr(member, (specs / filename).read_bytes())
    row = {
        "parser_release_id": RELEASE, "source_id": "mock-worker-local-source",
        "artifact_uri": "https://mock-artifacts.invalid/manual-parser.zip",
        "archive_path": str(archive_path),
    }
    monkeypatch.setenv(startup.DEPLOYMENTS_ENV, json.dumps({"version": 1, "deployments": [row]}))
    owner = composition.bundle.canonical_intake_parser()
    assert type(owner.artifact_registry) is NativeParserArtifactRegistry
    assert owner._deployment_configuration_error is None
    bound = owner.artifact_registry._deployments[RELEASE]
    assert bound.archive_path == archive_path
    assert bound.archive_uri == row["artifact_uri"]
    assert bound.source_id == row["source_id"]
    assert owner._storage_factory is None
    assert owner._parser_process_factory is None
    assert owner._prerequisites().registry is owner.artifact_registry
    # No verify_release call or catalog row: construction is not release acceptance.


def test_mock_default_registry_is_lazy_even_with_rejected_startup(monkeypatch):
    monkeypatch.setenv(startup.DEPLOYMENTS_ENV, PRIVATE)
    parse = Mock(side_effect=AssertionError("MOCK_REGISTRATION_MUST_NOT_PARSE_STARTUP"))
    factory = Mock(side_effect=AssertionError("MOCK_REGISTRATION_MUST_NOT_BUILD_OWNER"))
    execute = Mock(side_effect=AssertionError("MOCK_REGISTRATION_MUST_NOT_EXECUTE"))
    monkeypatch.setattr(startup, "parse_native_parser_deployments", parse)
    monkeypatch.setattr(PersistenceBundle, "canonical_intake_parser", factory)
    monkeypatch.setattr(NativeIntakeParserOwner, "execute", execute)
    registry = handlers.build_default_registry()
    assert registry.get(JOB_TYPE) is handlers.handle_native_intake_parser
    parse.assert_not_called()
    factory.assert_not_called()
    execute.assert_not_called()


def test_mock_default_handler_uses_persistence_factory_then_owner_execute(composition, monkeypatch):
    monkeypatch.delenv(startup.DEPLOYMENTS_ENV, raising=False)
    execute = create_autospec(NativeIntakeParserOwner.execute, return_value=sentinel.owner_return)
    monkeypatch.setattr(NativeIntakeParserOwner, "execute", execute)
    delivery = _delivery()
    handlers.build_default_registry().handle(delivery, composition.bundle)
    composition.graph_factory.assert_called_once_with(composition.bundle)
    assert execute.call_count == 1
    owner, observed = execute.call_args.args
    assert type(owner) is NativeIntakeParserOwner
    assert owner.graph is composition.graph
    assert observed is delivery


@pytest.mark.parametrize("trusted_factories", [False, True])
@pytest.mark.parametrize("entrypoint", ["execute", "delivery"])
def test_mock_owner_and_delivery_enter_existing_runtime_with_exact_trusted_factories(
    composition, monkeypatch, trusted_factories, entrypoint,
):
    storage = Mock() if trusted_factories else None
    parser = Mock() if trusted_factories else None
    owner = NativeIntakeParserOwner(
        composition.graph, storage_factory=storage, parser_process_factory=parser,
    )
    delivery = _delivery()
    replay = Mock(return_value={"terminal": False, "result": None})
    policy = SimpleNamespace(sha256="b" * 64, tenant_id=TENANT)
    accept = Mock(return_value=(7, policy))
    monkeypatch.setattr(owner, "replay", replay)
    monkeypatch.setattr(owner, "_accept", accept)
    leases = Mock(spec=runtime_module.NativeParserCompositeLeases)
    lease_factory = create_autospec(runtime_module.NativeParserCompositeLeases, return_value=leases)
    io = Mock(spec=runtime_module.NativeIntakeParserIO)
    io.read_and_parse.return_value = sentinel.observed
    io_factory = create_autospec(runtime_module.NativeIntakeParserIO, return_value=io)
    commit = create_autospec(runtime_module.commit_parser_output, return_value=sentinel.committed)
    monkeypatch.setattr(runtime_module, "NativeParserCompositeLeases", lease_factory)
    monkeypatch.setattr(runtime_module, "NativeIntakeParserIO", io_factory)
    monkeypatch.setattr(runtime_module, "commit_parser_output", commit)
    timeline = Mock()
    for name, boundary in (("replay", replay), ("accept", accept), ("leases", lease_factory),
                           ("io", io_factory), ("read", io.read_and_parse), ("commit", commit),
                           ("pause", leases.pause)):
        timeline.attach_mock(boundary, name)
    result = (owner.execute(delivery) if entrypoint == "execute"
              else run_native_intake_parser_delivery(delivery, owner))
    assert result is sentinel.committed
    assert timeline.mock_calls == [
        call.replay(delivery), call.accept(delivery, TENANT, 1),
        call.leases(owner, delivery, business_fence=7, policy_sha256=policy.sha256),
        call.io(owner, delivery, leases, policy, storage_factory=storage, parser_process_factory=parser),
        call.read(), call.commit(owner, delivery, leases, sentinel.observed), call.pause(),
    ]
    for factory in (storage, parser):
        if factory is not None:
            factory.assert_not_called()


@pytest.mark.parametrize("key", [
    "storage_factory", "parser_process_factory", "provider", "artifact_uri", "archive_path",
])
def test_mock_payload_cannot_select_provider_or_runtime_factory(composition, monkeypatch, key):
    storage, parser = Mock(), Mock()
    owner = NativeIntakeParserOwner(
        composition.graph, storage_factory=storage, parser_process_factory=parser,
    )
    replay = Mock(side_effect=AssertionError("MOCK_ROUTING_MUST_REJECT_BEFORE_HISTORY"))
    monkeypatch.setattr(owner, "replay", replay)
    delivery = _delivery()
    delivery.payload[key] = PRIVATE
    with pytest.raises(JobFenceRejectedError) as raised:
        run_native_intake_parser_delivery(delivery, owner)
    assert str(raised.value) == "NATIVE_PARSER_ROUTING_IDENTITY_REJECTED"
    replay.assert_not_called()
    storage.assert_not_called()
    parser.assert_not_called()


class _FalseyFactory:
    """A callable accepted by the owner's constructor, despite its truth value."""

    def __init__(self, result):
        self.invoke = Mock(return_value=result)

    def __bool__(self):
        return False

    def __call__(self, *args, **kwargs):
        return self.invoke(*args, **kwargs)


@pytest.mark.parametrize("falsey", [None, "storage", "parser"])
def test_mock_io_construction_honors_every_accepted_constructor_callable(
    composition, monkeypatch, falsey,
):
    storage = (_FalseyFactory(sentinel.storage) if falsey == "storage"
               else Mock(return_value=sentinel.storage))
    parser = (_FalseyFactory(sentinel.parser) if falsey == "parser"
              else Mock(return_value=sentinel.parser))
    owner = NativeIntakeParserOwner(
        composition.graph, storage_factory=storage, parser_process_factory=parser,
    )
    delivery = _delivery()
    policy = SimpleNamespace(sha256="b" * 64, tenant_id=TENANT)
    # Constructor binding only. This Mock is not an accepted/live lease proof.
    leases = Mock(spec=runtime_module.NativeParserCompositeLeases)
    leases.owner, leases.delivery, leases.policy_sha256 = owner, delivery, policy.sha256
    default_storage, default_parser = Mock(), Mock()
    monkeypatch.setattr(io_module, "NativeParserSnapshotTransport", default_storage)
    monkeypatch.setattr(io_module, "NativeManualParserProcess", default_parser)
    io = io_module.NativeIntakeParserIO(
        owner, delivery, leases, policy,
        storage_factory=owner._storage_factory, parser_process_factory=owner._parser_process_factory,
    )
    assert io.storage is sentinel.storage
    assert io.parser is sentinel.parser
    (storage.invoke if falsey == "storage" else storage).assert_called_once_with(guard=leases.guard)
    (parser.invoke if falsey == "parser" else parser).assert_called_once_with()
    default_storage.assert_not_called()
    default_parser.assert_not_called()


@pytest.fixture
def managed_worker(monkeypatch):
    """Real run_once/default handler, mock owner and current queue observations."""
    delivery = _delivery()
    queue = Mock(spec_set=["claim_next", "get", "heartbeat", "update_status", "enqueue"])
    queue.claim_next.return_value = delivery
    queue.get.return_value = delivery
    owner = Mock(spec=NativeIntakeParserOwner)
    owner.execute.return_value = None
    engine = Mock(spec_set=["dialect", "transaction", "query_one", "execute"])
    engine.dialect = "postgresql"
    for name in ("transaction", "query_one", "execute"):
        getattr(engine, name).side_effect = AssertionError("MOCK_NO_SQL")
    persistence = SimpleNamespace(
        engine=engine, job_queue=queue, canonical_intake_parser=Mock(return_value=owner),
    )
    telemetry = SimpleNamespace(
        logger=Mock(spec_set=["info", "error"]),
        metrics=Mock(spec_set=["observe", "increment"]),
        tracer=SimpleNamespace(start_span=Mock(side_effect=lambda *args, **kwargs: nullcontext())),
    )
    # Mock only the generic heartbeat object, not run_once or execute_job.
    # No thread is started; start() remains observable as a forbidden call.
    heartbeat = Mock(spec=worker_module._LeaseHeartbeat)
    heartbeat.stop.return_value = (delivery.version, None)
    heartbeat_factory = create_autospec(worker_module._LeaseHeartbeat, return_value=heartbeat)
    monkeypatch.setattr(worker_module, "_LeaseHeartbeat", heartbeat_factory)
    worker = worker_module.ODayWorker(
        persistence=persistence, telemetry=telemetry, worker_id="mock-parser-worker",
    )
    backlog = Mock()
    monkeypatch.setattr(worker, "sample_audit_backlog", backlog)
    yield SimpleNamespace(
        worker=worker, delivery=delivery, before=deepcopy(delivery.to_dict()),
        queue=queue, owner=owner, persistence=persistence, telemetry=telemetry,
        heartbeat=heartbeat, heartbeat_factory=heartbeat_factory, backlog=backlog,
    )
    assert engine.mock_calls == []


def _assert_parser_managed(flow, *, latest=None, latest_before=None):
    """No independent queue transition, lease renewal or raw error persistence."""
    flow.queue.claim_next.assert_called_once_with(worker_id="mock-parser-worker")
    flow.persistence.canonical_intake_parser.assert_called_once_with()
    flow.owner.execute.assert_called_once_with(flow.delivery)
    flow.heartbeat.start.assert_not_called()
    flow.heartbeat.stop.assert_called()
    flow.queue.heartbeat.assert_not_called()
    flow.queue.update_status.assert_not_called()
    flow.queue.enqueue.assert_not_called()
    assert flow.delivery.to_dict() == flow.before
    if latest is not None:
        assert latest.to_dict() == latest_before
    diagnostics = repr(flow.telemetry.logger.mock_calls) + repr(flow.telemetry.metrics.mock_calls)
    assert PRIVATE not in diagnostics
    assert PRIVATE not in repr(flow.queue.mock_calls)
    assert PRIVATE not in repr(flow.delivery.to_dict())
    assert "_retry_count" not in flow.delivery.payload


@pytest.mark.parametrize("returned", [None, sentinel.unresolved, {"status": "SUCCEEDED"}])
@pytest.mark.parametrize("latest_kind", ["running", "missing", "foreign_fence", "queued_without_retry"])
def test_mock_parser_return_is_never_delivery_success_or_retry(managed_worker, returned, latest_kind):
    flow = managed_worker
    latest = {
        "running": flow.delivery,
        "missing": None,
        "foreign_fence": replace(flow.delivery, fence_token=10, version=5),
        "queued_without_retry": replace(flow.delivery, status=JobStatus.QUEUED, version=5),
    }[latest_kind]
    before = deepcopy(latest.to_dict()) if latest is not None else None
    flow.queue.get.return_value = latest
    flow.owner.execute.return_value = returned
    assert flow.worker.run_once() is True
    _assert_parser_managed(flow, latest=latest, latest_before=before)
    assert any(item.kwargs.get("result") == "rejected" for item in flow.telemetry.logger.error.call_args_list)
    assert not any(item.kwargs.get("result") == "success" for item in flow.telemetry.logger.info.call_args_list)


@pytest.mark.parametrize("status", TERMINAL)
@pytest.mark.parametrize("lost_reply", [False, True])
def test_mock_parser_observes_canonical_terminal_without_resettlement(managed_worker, status, lost_reply):
    flow = managed_worker
    latest = replace(flow.delivery, status=status, version=5, payload={**flow.delivery.payload})
    before = deepcopy(latest.to_dict())
    flow.queue.get.return_value = latest
    if lost_reply:
        flow.owner.execute.side_effect = RuntimeError(PRIVATE)
    assert flow.worker.run_once() is True
    _assert_parser_managed(flow, latest=latest, latest_before=before)
    assert any(status.value in item.args[0] for item in flow.telemetry.logger.info.call_args_list)
    flow.telemetry.logger.error.assert_not_called()
    if not lost_reply:
        assert any(item.kwargs.get("labels", {}).get("status") == status.value
                   for item in flow.telemetry.metrics.observe.call_args_list)


@pytest.mark.parametrize("lost_reply", [False, True])
def test_mock_parser_observes_owned_queued_retry_without_count_or_payload_mutation(managed_worker, lost_reply):
    flow = managed_worker
    # Models a queue observation of an owner's committed retry, not its proof.
    latest = replace(
        flow.delivery, status=JobStatus.QUEUED, delivery_state=JobDeliveryState.RETRYING,
        version=5, payload={**flow.delivery.payload, "_retry_count": 2},
    )
    before = deepcopy(latest.to_dict())
    flow.queue.get.return_value = latest
    if lost_reply:
        flow.owner.execute.side_effect = RuntimeError(PRIVATE)
    assert flow.worker.run_once() is True
    _assert_parser_managed(flow, latest=latest, latest_before=before)
    assert latest.payload["_retry_count"] == 2
    if not lost_reply:
        assert any(item.kwargs.get("action") == "retry" and item.kwargs.get("result") == "queued"
                   for item in flow.telemetry.logger.info.call_args_list)


@pytest.mark.parametrize("failure", [
    "typed_io", "unreaped", "root_required", "root_deadline", "unavailable", "fence",
    "nonretryable", "arbitrary",
])
def test_mock_parser_error_never_grants_generic_retry_deadletter_or_raw_diagnostics(managed_worker, failure):
    flow = managed_worker
    error = {
        "typed_io": NativeIORejected(PRIVATE, status=503),
        "unreaped": NativeIOUnreaped(PRIVATE),
        "root_required": NativeParserUnavailable("NATIVE_PARSER_CURRENT_SQL_ROOT_REQUIRED"),
        "root_deadline": NativeParserConflict("NATIVE_PARSER_ROOT_DEADLINE_EXPIRED"),
        "unavailable": NativeParserUnavailable(PRIVATE),
        "fence": JobFenceRejectedError(PRIVATE),
        "nonretryable": NonRetryableJobError(PRIVATE),
        "arbitrary": RuntimeError(PRIVATE),
    }[failure]
    error.private_detail = PRIVATE
    error.__cause__ = RuntimeError(PRIVATE)
    flow.owner.execute.side_effect = error
    assert flow.worker.run_once() is True
    _assert_parser_managed(flow)
    assert flow.telemetry.logger.error.call_count == 1
    assert flow.telemetry.logger.error.call_args.kwargs["error_code"] == type(error).__name__
    assert str(error) not in repr(flow.telemetry.logger.mock_calls)
    assert str(error) not in repr(flow.queue.mock_calls)


@pytest.mark.parametrize("handler_fails", [False, True])
def test_mock_parser_unknown_outcome_read_preserves_owned_state_and_redacts_values(managed_worker, handler_fails):
    flow = managed_worker
    if handler_fails:
        flow.owner.execute.side_effect = RuntimeError(PRIVATE + "_HANDLER")
    flow.queue.get.side_effect = RuntimeError(PRIVATE + "_OUTCOME_READ")
    assert flow.worker.run_once() is True
    _assert_parser_managed(flow)
    assert flow.queue.get.call_count == (1 if handler_fails else 2)
    assert flow.telemetry.logger.error.call_args.kwargs["result"] == "unresolved"


@pytest.mark.parametrize("status", TERMINAL + (JobStatus.RUNNING,))
def test_mock_parser_second_outcome_read_after_lost_first_read_is_observation_only(managed_worker, status):
    flow = managed_worker
    latest = replace(flow.delivery, status=status, version=5)
    before = deepcopy(latest.to_dict())
    flow.queue.get.side_effect = [RuntimeError(PRIVATE), latest]
    assert flow.worker.run_once() is True
    _assert_parser_managed(flow, latest=latest, latest_before=before)
    assert flow.queue.get.call_count == 2


@pytest.mark.parametrize("handler_fails", [False, True])
def test_mock_parser_heartbeat_failure_hint_cannot_leak_or_settle(managed_worker, handler_fails):
    flow = managed_worker
    flow.heartbeat.stop.return_value = (flow.delivery.version, RuntimeError(PRIVATE + "_HEARTBEAT"))
    if handler_fails:
        flow.owner.execute.side_effect = RuntimeError(PRIVATE + "_HANDLER")
    assert flow.worker.run_once() is True
    _assert_parser_managed(flow)
    assert "NATIVE_PARSER_OUTCOME_UNRESOLVED" in flow.telemetry.logger.error.call_args.args[0]


@pytest.mark.parametrize("scenario", ["success", "retry", "nonretryable", "exhausted"])
def test_mock_non_parser_control_keeps_generic_heartbeat_and_settlement(managed_worker, scenario):
    flow = managed_worker
    payload = {"_retry_count": 3} if scenario == "exhausted" else {}
    delivery = _delivery(job_type="mock-generic-control", payload=payload)
    before = deepcopy(delivery.to_dict())
    flow.queue.claim_next.return_value = delivery
    flow.queue.get.return_value = delivery
    handler = Mock()
    if scenario != "success":
        error_type = NonRetryableJobError if scenario == "nonretryable" else RuntimeError
        handler.side_effect = error_type("MOCK_GENERIC_FAILURE")
    flow.worker.registry.register(delivery.job_type, handler)
    assert flow.worker.run_once() is True
    handler.assert_called_once_with(delivery, flow.persistence)
    flow.persistence.canonical_intake_parser.assert_not_called()
    flow.owner.execute.assert_not_called()
    flow.heartbeat.start.assert_called_once_with()
    flow.queue.update_status.assert_called_once()
    update = flow.queue.update_status.call_args
    expected = (JobStatus.SUCCEEDED if scenario == "success"
                else JobStatus.QUEUED if scenario == "retry" else JobStatus.FAILED)
    assert update.args == (delivery.job_id, expected)
    assert update.kwargs["expected_version"] == delivery.version
    assert update.kwargs["fence_token"] == delivery.fence_token
    if scenario == "retry":
        assert update.kwargs["payload"] == {"_retry_count": 1}
        assert update.kwargs["delivery_state"] == JobDeliveryState.RETRYING
    elif scenario in {"nonretryable", "exhausted"}:
        assert update.kwargs["payload"] is None
        assert update.kwargs["delivery_state"] == JobDeliveryState.DEAD_LETTER
    assert delivery.to_dict() == before


@pytest.mark.parametrize("error_type,kind,code", [
    (artifact_module.NativeParserArtifactRejected, "REJECTED", "NATIVE_PARSER_ARTIFACT_REJECTED"),
    (artifact_module.NativeParserArtifactUnavailable, "UNAVAILABLE", "NATIVE_PARSER_ARTIFACT_READ_UNAVAILABLE"),
])
def test_mock_cold_installed_artifact_loss_keeps_control_owner_and_bounded_positive_error(
        composition, monkeypatch, tmp_path, error_type, kind, code):
    archive = tmp_path / "cold-start.zip"
    write_archive(archive, installed_bytes().items())
    monkeypatch.setenv(startup.DEPLOYMENTS_ENV, json.dumps({"version": 1, "deployments": [{
        "parser_release_id": RELEASE, "source_id": "mock-cold-start-source",
        "artifact_uri": "gs://mock-artifacts/cold-start.zip", "archive_path": str(archive)}]}))

    def failed_read(*args, **kwargs):
        raise error_type(PRIVATE)

    monkeypatch.setattr(artifact_module, "_read_regular", failed_read)
    owner = composition.bundle.canonical_intake_parser()
    assert owner.artifact_registry is None and owner._deployment_configuration_error is None
    assert owner._artifact_startup_error == kind and PRIVATE not in repr(vars(owner))
    for gate in (owner.require_deployment, owner._prerequisites):
        with pytest.raises(error_type, match="^" + code + "$"):
            gate()


@pytest.mark.parametrize("error", ["", PRIVATE, False, 3, {}, _StringCode("REJECTED")])
def test_mock_constructor_rejects_untrusted_artifact_startup_error(composition, error):
    with pytest.raises(ValueError, match="^NATIVE_PARSER_ARTIFACT_STARTUP_ERROR_REJECTED$"):
        NativeIntakeParserOwner(composition.graph, artifact_startup_error=error)


@pytest.mark.parametrize("error_fields", [
    {"deployment_configuration_error": CONFIG_REJECTED},
    {"artifact_startup_error": "REJECTED"}, {"artifact_startup_error": "UNAVAILABLE"},
])
def test_mock_startup_failure_closes_a_synthetic_local_lease_guard(composition, monkeypatch, error_fields):
    # A local bound is not acceptance/renewal evidence. This tests only the
    # rejection guard; no policy, SQL, source, human or IO grant is supplied.
    owner = NativeIntakeParserOwner(composition.graph, artifact_registry=object(), **error_fields)
    monkeypatch.setenv(SIGNING_KEY_ENV, "mock-local-guard-purpose-key-" * 3)
    delivery = _delivery(payload={"job_id": JOB, "tenant_id": TENANT,
        "attempt_token": owner.auth.sign(JOB, TENANT, 1)})
    leases = NativeParserCompositeLeases(owner, delivery, business_fence=7, policy_sha256="b" * 64)
    leases._proof = SimpleNamespace(monotonic_io_bound=time.monotonic() + 5)
    leases._stop.clear()
    with pytest.raises(NativeIORejected, match="^NATIVE_IO_PARSER_CURRENT_CONFIG_LOST$"):
        leases.guard()

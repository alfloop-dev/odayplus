"""Engineering SQLite controls; no production flags or native PG claim."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from shared.audit.integrity import sha256_hex
from shared.audit.platform_flag_delivery import COLLECTION as DELIVERY_COLLECTION
from shared.audit.platform_flag_delivery import PlatformFlagAuditDelivery
from shared.audit.worm import AuditWormReceipt
from shared.auth.feature_flags import (
    ASSISTED_INTAKE_FLAG_SCOPES,
    DEFAULT_FLAGS,
    FeatureFlag,
    FeatureFlagConfigurationConflict,
    FlagScope,
    FlagScopeBinding,
)
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.feature_flags import (
    COLLECTION,
    DOCUMENT,
    DurableFeatureFlagRegistry,
    FeatureFlagAlreadyExists,
    FeatureFlagOperationDisabled,
    FeatureFlagStoreUnavailable,
)
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.jobs.queue import NonRetryableJobError

KEY = "high_risk.priceops.execute"


SCOPED_KEY = "assisted_intake_v1_read"
SCOPED_TENANT_A = "00000000-0000-0000-0000-000000000111"
SCOPED_TENANT_B = "00000000-0000-0000-0000-000000000222"


def _register_scoped_read(registry, audit):
    registry.execute("register", SCOPED_KEY, actor="synthetic-config-owner", correlation_id="synthetic-scope-create",
        audit_log=audit, flag=FeatureFlag(key=SCOPED_KEY, owner="synthetic-scope-owner", high_risk=True,
            scope=FlagScope.TENANT, bindings=(FlagScopeBinding(SCOPED_TENANT_A),)))


def test_old_global_records_read_without_repair_or_new_implicit_intake_defaults(tmp_path):
    engine, owner, _ = _owner(tmp_path / "old-global.sqlite")
    try:
        raw, _ = owner.store.get_versioned(COLLECTION, DOCUMENT)
        for entry in raw["flags"].values():
            for field in ("scope", "bindings", "configuration_version"):
                entry.pop(field)
        owner.store.put(COLLECTION, DOCUMENT, raw)
        before = owner.store.get_versioned(COLLECTION, DOCUMENT)
        assert {flag.key for flag in owner.all()} == set(DEFAULT_FLAGS)
        assert all(flag.scope == FlagScope.GLOBAL for flag in owner.all())
        assert owner.get(SCOPED_KEY) is None
        assert owner.store.get_versioned(COLLECTION, DOCUMENT) == before
    finally:
        engine.close()


@pytest.mark.parametrize("field", ["bindings", "configuration_version", "scope", "high_risk"])
def test_partial_or_downgraded_scoped_record_is_unavailable_and_never_reinitialized(tmp_path, field):
    engine, owner, audit = _owner(tmp_path / "partial-scope.sqlite")
    try:
        _register_scoped_read(owner, audit)
        raw, _ = owner.store.get_versioned(COLLECTION, DOCUMENT)
        raw["flags"][SCOPED_KEY].pop(field)
        owner.store.put(COLLECTION, DOCUMENT, raw)
        before = owner.store.get_versioned(COLLECTION, DOCUMENT)
        with pytest.raises(FeatureFlagStoreUnavailable):
            owner.is_enabled(SCOPED_KEY, tenant_id=SCOPED_TENANT_A)
        with pytest.raises(FeatureFlagStoreUnavailable):
            owner.initialize_defaults()
        assert owner.store.get_versioned(COLLECTION, DOCUMENT) == before
    finally:
        engine.close()


def test_scope_change_between_approval_read_and_cas_does_not_approve_a_new_configuration(tmp_path, monkeypatch):
    path = tmp_path / "scope-cas.sqlite"
    a, first, audit_a = _owner(path)
    b, second, audit_b = _owner(path)
    try:
        _register_scoped_read(first, audit_a)
        original = first.store.compare_and_put
        interleaved = []

        def reconfigure(collection, document, value, *, expected_version):
            if expected_version is not None and not interleaved:
                interleaved.append(True)
                second.execute("configure_scope", SCOPED_KEY, actor="synthetic-b", correlation_id="scope-race",
                    audit_log=audit_b, configuration_version=1, bindings=(FlagScopeBinding(SCOPED_TENANT_B),))
            return original(collection, document, value, expected_version=expected_version)

        monkeypatch.setattr(first.store, "compare_and_put", reconfigure)
        with pytest.raises(FeatureFlagConfigurationConflict):
            first.execute("approve", SCOPED_KEY, actor="synthetic-a", correlation_id="stale-review",
                          audit_log=audit_a, configuration_version=1)
        current = first.get(SCOPED_KEY)
        assert interleaved == [True] and current.configuration_version == 2
        assert current.bindings == (FlagScopeBinding(SCOPED_TENANT_B),)
        assert not current.approved_by and not current.enabled
        assert not any(event.correlation_id == "stale-review" for event in audit_a.list_events())
        assert len(first.store.list_items(COLLECTION)) == 1
    finally:
        a.close()
        b.close()


def test_two_flag_approvals_cannot_substitute_for_governed_release_authority(tmp_path):
    engine, owner, audit = _owner(tmp_path / "pending-intake-activation.sqlite")
    try:
        _register_scoped_read(owner, audit)
        for actor in ("synthetic-a", "synthetic-b"):
            owner.execute("approve", SCOPED_KEY, actor=actor, correlation_id="synthetic-review-" + actor,
                          audit_log=audit, configuration_version=1)
        before, events = owner.read_versioned(), audit.list_events()
        with pytest.raises(PermissionError, match="INTAKE_RELEASE_AUTHORITY_REQUIRED"):
            owner.execute("enable", SCOPED_KEY, actor="synthetic-a", correlation_id="rejected-activation",
                          audit_log=audit, configuration_version=1)
        assert owner.read_versioned() == before and audit.list_events() == events
        assert not owner.is_enabled(SCOPED_KEY, tenant_id=SCOPED_TENANT_A)
    finally:
        engine.close()


@pytest.mark.parametrize("timestamp", ["2026-07-23T00:00:00Z", date(2026, 7, 23), datetime(2026, 7, 23, tzinfo=UTC)])
def test_scoped_activation_uses_the_original_governance_owner_and_records_its_evidence(tmp_path, monkeypatch, timestamp):
    import shutil

    import yaml

    from modules.listing.application import intake_release_governance as governance
    from tests.ops.test_assisted_listing_intake_release import (
        _approve_all_authority,
        _record_live_evidence,
    )

    # These are isolated synthetic attestations, never repository approvals.
    infra = tmp_path / "synthetic-governance"
    shutil.copytree(governance.INFRA_DIR, infra)
    _approve_all_authority(infra)
    _record_live_evidence(infra)
    authority_path = infra / "release_authority.yaml"
    authority = yaml.safe_load(authority_path.read_text())
    for row in authority["gates"]:
        row["approved_at"] = timestamp
    authority_path.write_text(yaml.safe_dump(authority))
    live_path = infra / "live_runtime_evidence.yaml"
    live = yaml.safe_load(live_path.read_text())
    live["recorded_at"] = timestamp
    for row in live["targets"]:
        row["completed_at"] = timestamp
    live_path.write_text(yaml.safe_dump(live))
    monkeypatch.setattr(governance, "INFRA_DIR", infra)
    engine, owner, audit = _owner(tmp_path / "admitted-scope.sqlite")
    try:
        _register_scoped_read(owner, audit)
        for actor in ("synthetic-a", "synthetic-b"):
            owner.execute("approve", SCOPED_KEY, actor=actor, correlation_id="synthetic-review-" + actor,
                          audit_log=audit, configuration_version=1)
        result = owner.execute("enable", SCOPED_KEY, actor="synthetic-a", correlation_id="synthetic-activation",
                               audit_log=audit, configuration_version=1)
        assert result.flag.enabled and owner.is_enabled(SCOPED_KEY, tenant_id=SCOPED_TENANT_A)
        assert not owner.is_enabled(SCOPED_KEY, tenant_id=SCOPED_TENANT_B)
        evidence = audit.list_events()[-1].metadata["release_authority"]
        assert len(evidence["approvals"]) == 9 and len(evidence["live_evidence_digest"]) == 64
        assert evidence["recorded_by"] == "release-authority@example.com"
        assert isinstance(evidence["recorded_at"], str)
        assert all(isinstance(row["approved_at"], str) for row in evidence["approvals"])
    finally:
        engine.close()


@pytest.mark.parametrize("location,field", [
    ("authority", "approver"), ("live", "recorded_at"),
    ("target", "evidence_ref"), ("canary", "completed_at"),
])
def test_malformed_governance_cannot_change_scoped_flag_version_or_audit(tmp_path, monkeypatch, location, field):
    import shutil

    import yaml

    from modules.listing.application import intake_release_governance as governance
    from tests.ops.test_assisted_listing_intake_release import (
        _approve_all_authority,
        _live_unit,
        _record_live_evidence,
    )

    infra = tmp_path / "malformed-synthetic-governance"
    shutil.copytree(governance.INFRA_DIR, infra)
    _approve_all_authority(infra)
    _record_live_evidence(infra, canary_units=[_live_unit(3)])
    path = infra / ("release_authority.yaml" if location == "authority" else "live_runtime_evidence.yaml")
    document = yaml.safe_load(path.read_text())
    target = (document["gates"][0] if location == "authority" else document["targets"][0] if location == "target"
              else document["canary_units"][0] if location == "canary" else document)
    target[field] = True
    path.write_text(yaml.safe_dump(document))
    monkeypatch.setattr(governance, "INFRA_DIR", infra)
    engine, owner, audit = _owner(tmp_path / "malformed-activation.sqlite")
    try:
        _register_scoped_read(owner, audit)
        for actor in ("synthetic-a", "synthetic-b"):
            owner.execute("approve", SCOPED_KEY, actor=actor, correlation_id="synthetic-review-" + actor,
                          audit_log=audit, configuration_version=1)
        before, events = owner.read_versioned(), audit.list_events()
        with pytest.raises(FeatureFlagStoreUnavailable, match="release authority is unavailable"):
            owner.execute("enable", SCOPED_KEY, actor="synthetic-a", correlation_id="synthetic-rejected-malformed",
                          audit_log=audit, configuration_version=1)
        assert owner.read_versioned() == before and audit.list_events() == events
    finally:
        engine.close()


@pytest.mark.parametrize("key", [key for key in ASSISTED_INTAKE_FLAG_SCOPES if key != SCOPED_KEY])
def test_unintegrated_intake_consumers_cannot_be_declared_enabled(tmp_path, key):
    engine, owner, audit = _owner(tmp_path / "unintegrated-consumer.sqlite")
    try:
        scope = ASSISTED_INTAKE_FLAG_SCOPES[key]
        owner.execute("register", key, actor="synthetic-config-owner", correlation_id="synthetic-create",
            audit_log=audit, flag=FeatureFlag(key=key, owner="synthetic-owner", high_risk=True, scope=scope,
                bindings=(FlagScopeBinding(SCOPED_TENANT_A, "synthetic-source" if scope == FlagScope.TENANT_SOURCE else None),)))
        for actor in ("synthetic-a", "synthetic-b"):
            owner.execute("approve", key, actor=actor, correlation_id="synthetic-review-" + actor,
                          audit_log=audit, configuration_version=1)
        before, events = owner.read_versioned(), audit.list_events()
        with pytest.raises(FeatureFlagOperationDisabled, match="INTAKE_FLAG_CONSUMER_NOT_INTEGRATED"):
            owner.execute("enable", key, actor="synthetic-a", correlation_id="synthetic-rejected",
                          audit_log=audit, configuration_version=1)
        assert owner.read_versioned() == before and audit.list_events() == events
    finally:
        engine.close()


COMMAND_PROOF = {"reason": "Synthetic operational command reason", "riskAcknowledged": True}


def _owner(path):
    engine = SqliteEngine(path)
    store = SqliteDocumentStore(engine)
    registry = DurableFeatureFlagRegistry(store)
    registry.initialize_defaults()
    return engine, registry, DurableAuditLog(engine)


def _command(registry, audit, operation, actor="admin-a", **kwargs):
    return registry.execute(operation, KEY, actor=actor,
                            correlation_id="synthetic-flag-command", audit_log=audit, **kwargs)


def test_independent_engines_and_reopen_read_same_global_flags(tmp_path):
    path = tmp_path / "flags.sqlite"
    a, first, audit_a = _owner(path)
    b, second, audit_b = _owner(path)
    try:
        assert {f.key for f in first.all()} == set(DEFAULT_FLAGS)
        assert all(not f.enabled and not f.approved_by for f in first.all())
        _command(first, audit_a, "approve", "admin-a")
        assert second.get(KEY).approved_by == frozenset({"admin-a"})
        _command(second, audit_b, "approve", "admin-b")
        result = _command(first, audit_a, "enable")
        assert result.flag.approved_by == frozenset({"admin-a", "admin-b"})
        assert result.audit_delivery == "not_configured"
        assert second.is_enabled(KEY)
        _command(second, audit_b, "disable")
        assert not first.is_enabled(KEY)
        assert not first.get(KEY).approved_by
        assert all(e.actor in {"admin-a", "admin-b"} for e in audit_a.list_events())
        assert all(e.correlation_id == "synthetic-flag-command" for e in audit_a.list_events())
    finally:
        a.close()
        b.close()
    c, reopened, _ = _owner(path)
    try:
        assert not reopened.is_enabled(KEY)
        assert not reopened.get(KEY).approved_by
    finally:
        c.close()


def test_repeated_actor_duplicate_key_and_expiry_keep_original_policy(tmp_path):
    engine, registry, audit = _owner(tmp_path / "flags.sqlite")
    try:
        _command(registry, audit, "approve")
        _command(registry, audit, "approve")
        before = registry.read_versioned()
        with pytest.raises(PermissionError, match="distinct approvals"):
            _command(registry, audit, "enable")
        assert registry.read_versioned() == before
        with pytest.raises(FeatureFlagAlreadyExists):
            registry.execute("register", KEY, actor="admin-a", correlation_id="register",
                             audit_log=audit, flag=FeatureFlag(key=KEY, owner="downgrade"))
        assert registry.read_versioned() == before
        expired = FeatureFlag(key="synthetic.expired", owner="admin-a",
                              expires_on=date(2020, 1, 1))
        registry.execute("register", expired.key, actor="admin-a", correlation_id="create",
                         audit_log=audit, flag=expired)
        registry.execute("enable", expired.key, actor="admin-a", correlation_id="enable",
                         audit_log=audit)
        assert not registry.is_enabled(expired.key, on=date(2026, 10, 15))
    finally:
        engine.close()


def test_stale_cas_retries_latest_other_engine_approval(tmp_path, monkeypatch):
    path = tmp_path / "flags.sqlite"
    a, first, audit_a = _owner(path)
    b, second, audit_b = _owner(path)
    original = first.store.compare_and_put
    interleaved = []

    def competing(collection, document, value, *, expected_version):
        if expected_version is not None and not interleaved:
            interleaved.append(True)
            _command(second, audit_b, "approve", "admin-b")
        return original(collection, document, value, expected_version=expected_version)

    monkeypatch.setattr(first.store, "compare_and_put", competing)
    try:
        _command(first, audit_a, "approve", "admin-a")
        assert interleaved == [True]
        assert first.get(KEY).approved_by == frozenset({"admin-a", "admin-b"})
        events = audit_a.list_events()
        assert len(events) == 2
        assert {e.actor for e in events} == {"admin-a", "admin-b"}
    finally:
        a.close()
        b.close()


def test_audit_failure_rolls_back_flag_and_version(tmp_path, monkeypatch):
    engine, registry, audit = _owner(tmp_path / "flags.sqlite")
    before = registry.read_versioned()

    def unavailable(_event):
        raise RuntimeError("Synthetic audit unavailable")

    monkeypatch.setattr(audit, "record", unavailable)
    try:
        with pytest.raises(RuntimeError, match="Synthetic audit unavailable"):
            _command(registry, audit, "approve")
        assert registry.read_versioned() == before
        assert audit.list_events() == []
    finally:
        engine.close()


@pytest.mark.parametrize("corruption", ["missing", "boolean", "approvals", "highrisk"])
def test_missing_or_malformed_state_never_defaults_success(tmp_path, corruption):
    engine, registry, _ = _owner(tmp_path / "flags.sqlite")
    store = registry.store
    try:
        if corruption == "missing":
            engine.execute("DELETE FROM durable_documents WHERE collection = ? AND doc_id = ?",
                           (COLLECTION, DOCUMENT))
        else:
            state, _ = store.get_versioned(COLLECTION, DOCUMENT)
            if corruption == "boolean":
                state["flags"][KEY]["enabled"] = "false"
            elif corruption == "approvals":
                state["flags"][KEY]["approved_by"] = ["admin-a", "admin-a"]
            else:
                state["flags"][KEY]["high_risk"] = False
            store.put(COLLECTION, DOCUMENT, state)
        with pytest.raises(FeatureFlagStoreUnavailable):
            registry.is_enabled(KEY)
    finally:
        engine.close()


class _SyntheticSink:
    sink_id = "synthetic-platform-flag-sink"

    def __init__(self):
        self.events = []

    def write_audit_event(self, event):
        self.events.append(event)
        return AuditWormReceipt(self.sink_id, "synthetic://" + event.event_id,
                                "audit-events", event.event_id, sha256_hex(event.to_dict()))


def test_delivery_failure_rolls_back_without_external_write(tmp_path, monkeypatch):
    engine, registry, _ = _owner(tmp_path / "flags.sqlite")
    sink = _SyntheticSink()
    audit = DurableAuditLog(engine, worm_sink=sink)
    queue = DurableJobQueue(engine)
    before = registry.read_versioned()

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("Synthetic delivery reservation unavailable")

    monkeypatch.setattr(queue, "enqueue", unavailable)
    try:
        with pytest.raises(RuntimeError, match="Synthetic delivery reservation unavailable"):
            _command(registry, audit, "approve", queue=queue)
        assert registry.read_versioned() == before
        assert audit.list_events() == []
        assert registry.store.list_all(DELIVERY_COLLECTION) == []
        assert sink.events == []
    finally:
        engine.close()


def test_global_delivery_is_pending_until_committed_event_written(tmp_path):
    engine, registry, _ = _owner(tmp_path / "flags.sqlite")
    sink = _SyntheticSink()
    audit = DurableAuditLog(engine, worm_sink=sink)
    queue = DurableJobQueue(engine)
    delivery = PlatformFlagAuditDelivery(engine, queue, sink)
    try:
        result = _command(registry, audit, "approve", queue=queue)
        assert result.audit_delivery == "pending"
        assert sink.events == []
        intent = registry.store.get(DELIVERY_COLLECTION, result.audit_event_id)
        assert intent["status"] == "pending"
        assert "tenant_id" not in intent["event"].metadata
        job = queue.get_by_idempotency_key("platform-feature-flag-audit:" + result.audit_event_id)
        assert "tenant_id" not in job.payload
        with pytest.raises(NonRetryableJobError):
            delivery.deliver(replace(job, payload={**job.payload, "event_hash": "forged"}))
        assert sink.events == []
        receipt = delivery.deliver(job)
        assert receipt["record_id"] == result.audit_event_id
        assert len(sink.events) == 1
        assert delivery.deliver(job) == receipt
        assert len(sink.events) == 1
        assert registry.store.get(DELIVERY_COLLECTION, result.audit_event_id)["status"] == "delivered"
    finally:
        engine.close()


def test_pending_outbox_receipt_is_not_external_acknowledgement(tmp_path):
    class PendingSink(_SyntheticSink):
        def write_audit_event(self, event):
            return AuditWormReceipt(self.sink_id, "outbox://still-pending", "audit-events",
                                    event.event_id, sha256_hex(event.to_dict()))

    engine, registry, _ = _owner(tmp_path / "flags.sqlite")
    sink = PendingSink()
    audit = DurableAuditLog(engine, worm_sink=sink)
    queue = DurableJobQueue(engine)
    try:
        result = _command(registry, audit, "approve", queue=queue)
        job = queue.get_by_idempotency_key("platform-feature-flag-audit:" + result.audit_event_id)
        with pytest.raises(NonRetryableJobError, match="receipt differs"):
            PlatformFlagAuditDelivery(engine, queue, sink).deliver(job)
        assert registry.store.get(DELIVERY_COLLECTION, result.audit_event_id)["status"] == "pending"
    finally:
        engine.close()


def _enable(registry, audit):
    _command(registry, audit, "approve", "admin-a")
    _command(registry, audit, "approve", "admin-b")
    return _command(registry, audit, "enable")


def test_known_job_type_cannot_substitute_unrelated_enabled_payload_flag():
    from shared.auth.feature_flags import FeatureFlagRegistry
    from shared.jobs.queue import check_job_feature_flag, resolve_job_feature_flag_key

    flags = FeatureFlagRegistry({**DEFAULT_FLAGS, "synthetic.enabled": FeatureFlag(
        key="synthetic.enabled", owner="synthetic", enabled=True,
    )})
    payload = {"feature_flag": "synthetic.enabled"}
    assert resolve_job_feature_flag_key("priceops.execute", payload) == KEY
    with pytest.raises(NonRetryableJobError, match="kill-switch"):
        check_job_feature_flag("priceops.execute", payload, flags=flags)


def test_durable_enqueue_and_claim_observe_other_engine_disable(tmp_path):
    from shared.jobs.queue import JobRequest, JobStatus

    path = tmp_path / "flags.sqlite"
    a, first, audit_a = _owner(path)
    b, second, audit_b = _owner(path)
    queue = DurableJobQueue(a, flags=first)
    try:
        with pytest.raises(NonRetryableJobError):
            queue.enqueue(JobRequest(job_type="priceops.execute", payload={}), correlation_id="disabled")
        _enable(first, audit_a)
        job, _ = queue.enqueue(JobRequest(job_type="priceops.execute", payload={}), correlation_id="enabled")
        _command(second, audit_b, "disable")
        assert queue.claim_next() is None
        refused = queue.get(job.job_id)
        assert refused.status == JobStatus.FAILED
        assert refused.version > job.version
        assert refused.fence_token > job.fence_token
        assert refused.locked_by is None
    finally:
        a.close()
        b.close()


def test_last_worker_dispatch_gate_blocks_disable_after_claim(tmp_path):
    from types import SimpleNamespace

    from apps.worker.oday_worker.main import ODayWorker
    from shared.jobs.queue import JobRequest
    from shared.jobs.registry import JobRegistry

    path = tmp_path / "flags.sqlite"
    a, first, audit_a = _owner(path)
    b, second, audit_b = _owner(path)
    queue = DurableJobQueue(a, flags=first)
    called = []
    handlers = JobRegistry()
    handlers.register("priceops.execute", lambda job, persistence: called.append(job.job_id))
    try:
        _enable(first, audit_a)
        queue.enqueue(JobRequest(job_type="priceops.execute", payload={}), correlation_id="queued")
        claimed = queue.claim_next()
        assert claimed is not None
        _command(second, audit_b, "disable")
        worker = ODayWorker(persistence=SimpleNamespace(
            engine=a, job_queue=queue, feature_flag_registry=first,
        ), registry=handlers)
        with pytest.raises(NonRetryableJobError, match="kill-switch"):
            worker.execute_job(claimed)
        assert called == []
    finally:
        a.close()
        b.close()


def _api(registry, audit, engine):
    from datetime import UTC, datetime, timedelta

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from apps.api.oday_api.routes.feature_flags import create_feature_flags_router
    from apps.api.oday_api.security.dependencies import require_feature_flag
    from modules.opsboard.auth import (
        AuthBoundaryConfig,
        AuthenticationBoundary,
        SigningKey,
        encode_compact_jwt,
    )

    key = SigningKey(kid="flag-composition", algorithm="HS256", secret=b"synthetic-flag-composition-key")
    issuer = "https://synthetic-flag-composition.invalid"
    boundary = AuthenticationBoundary(AuthBoundaryConfig(
        issuer=issuer, audiences=frozenset({"flags-api"}), signing_keys={key.kid: key},
        principal_mappings={actor: {"roles": ["platform_admin"], "scope": {"tenant_id": "tenant-a"}}
                            for actor in ("admin-a", "admin-b")},
    ))
    app = FastAPI()
    app.state.feature_flag_registry = registry
    app.include_router(create_feature_flags_router(
        registry=registry, audit_log=audit, boundary=boundary,
        queue=DurableJobQueue(engine, flags=registry),
    ))

    @app.get("/synthetic-protected", dependencies=[Depends(require_feature_flag(KEY))])
    def protected():
        return {"executed": True}

    def headers(actor):
        now = datetime.now(UTC)
        token = encode_compact_jwt({"sub": actor, "iss": issuer, "aud": "flags-api",
                                    "iat": now.timestamp(), "exp": (now + timedelta(minutes=5)).timestamp()}, key)
        return {"authorization": "Bearer " + token}

    return TestClient(app), headers


def test_api_read_commands_and_common_guard_share_cross_instance_owner(tmp_path):
    path = tmp_path / "flags.sqlite"
    a, first, audit_a = _owner(path)
    b, second, audit_b = _owner(path)
    ca, ha = _api(first, audit_a, a)
    cb, hb = _api(second, audit_b, b)
    route = "/admin/feature-flags/" + KEY
    try:
        assert ca.get("/synthetic-protected").status_code == 403
        assert ca.post(route + "/approve", json={**COMMAND_PROOF, **({})}, headers=ha("admin-a")).status_code == 200
        assert cb.post(route + "/approve", json={**COMMAND_PROOF, **({})}, headers=hb("admin-b")).status_code == 200
        enabled = ca.post(route + "/enable", json={**COMMAND_PROOF, **({})}, headers=ha("admin-a"))
        assert enabled.status_code == 200
        assert cb.get(route, headers=hb("admin-b")).json()["flag"]["is_active"] is True
        assert cb.get("/synthetic-protected").status_code == 200
        disabled = cb.post(route + "/disable", headers=hb("admin-b"), json=COMMAND_PROOF)
        assert disabled.status_code == 200
        assert ca.get("/synthetic-protected").status_code == 403
        public = ca.get("/feature-flags").json()["flags"]
        assert next(row for row in public if row["key"] == KEY)["is_active"] is False
    finally:
        a.close()
        b.close()


def test_independent_app_owners_do_not_rebind_common_auth_guard(tmp_path):
    a, first, audit_a = _owner(tmp_path / "a.sqlite")
    b, second, audit_b = _owner(tmp_path / "b.sqlite")
    try:
        _enable(first, audit_a)
        ca, _ = _api(first, audit_a, a)
        cb, _ = _api(second, audit_b, b)
        assert ca.get("/synthetic-protected").status_code == 200
        assert cb.get("/synthetic-protected").status_code == 403
        assert ca.get("/synthetic-protected").status_code == 200
    finally:
        a.close()
        b.close()


def test_malformed_owner_reports_unavailable_not_fake_disabled_policy(tmp_path):
    engine, registry, audit = _owner(tmp_path / "flags.sqlite")
    client, headers = _api(registry, audit, engine)
    registry.store.put(COLLECTION, DOCUMENT, {"schema_version": 1, "flags": {}})
    try:
        for path in ("/feature-flags", "/admin/feature-flags", "/synthetic-protected"):
            response = client.get(path, headers=headers("admin-a"))
            assert response.status_code == 503
            assert response.json()["detail"] == "FEATURE_FLAG_STORE_UNAVAILABLE"
    finally:
        engine.close()


def test_durable_factory_pins_one_global_owner_into_existing_queue(tmp_path):
    from shared.infrastructure.persistence.factory import _durable_bundle

    bundle = _durable_bundle(str(tmp_path / "factory.sqlite"), worm_sink=_SyntheticSink())
    try:
        owner = bundle.feature_flag_registry
        assert isinstance(owner, DurableFeatureFlagRegistry)
        assert owner.engine is bundle.engine
        assert bundle.job_queue._engine is bundle.engine
        assert bundle.job_queue._flags is owner
        assert bundle.audit_log._engine is bundle.engine
        assert {row.key for row in owner.all()} == set(DEFAULT_FLAGS)
        assert all(not row.enabled for row in owner.all())
    finally:
        bundle.engine.close()


def test_registered_platform_delivery_consumer_uses_global_owner_without_tenant(tmp_path):
    from types import SimpleNamespace

    from apps.worker.consumers.durable_audit import handle_durable_audit_delivery

    engine, registry, _ = _owner(tmp_path / "delivery.sqlite")
    sink = _SyntheticSink()
    audit = DurableAuditLog(engine, worm_sink=sink)
    queue = DurableJobQueue(engine, flags=registry)
    try:
        result = _command(registry, audit, "approve", queue=queue)
        job = queue.get_by_idempotency_key("platform-feature-flag-audit:" + result.audit_event_id)
        assert "tenant_id" not in job.payload
        assert registry.store.get(DELIVERY_COLLECTION, result.audit_event_id)["status"] == "pending"
        receipt = handle_durable_audit_delivery(job, SimpleNamespace(
            engine=engine, audit_log=audit, job_queue=queue,
        ))
        assert receipt["record_id"] == result.audit_event_id
        assert not receipt["object_uri"].startswith("outbox://")
        assert registry.store.get(DELIVERY_COLLECTION, result.audit_event_id)["status"] == "delivered"
    finally:
        engine.close()


@pytest.mark.parametrize("owner_kind", ["missing", "RAM", "other-engine"])
def test_durable_queue_rejects_absent_RAM_or_wrong_engine_owner_even_if_global_enabled(tmp_path, monkeypatch, owner_kind):
    from shared.auth import feature_flags as pure_flags
    from shared.auth.feature_flags import FeatureFlagRegistry
    from shared.jobs.queue import JobRequest

    a, actual, audit = _owner(tmp_path / "owner.sqlite")
    b, other, _ = _owner(tmp_path / "other.sqlite")
    ram = FeatureFlagRegistry({**DEFAULT_FLAGS, KEY: replace(
        DEFAULT_FLAGS[KEY], enabled=True, approved_by=frozenset({"admin-a", "admin-b"}),
    )})
    monkeypatch.setattr(pure_flags, "default_registry", lambda: ram)
    missing = {"missing": None, "RAM": ram, "other-engine": other}[owner_kind]
    invalid = DurableJobQueue(a, flags=missing)
    valid = DurableJobQueue(a, flags=actual)
    try:
        before = a.query("SELECT * FROM durable_jobs")
        with pytest.raises(FeatureFlagStoreUnavailable, match="same-engine"):
            invalid.enqueue(JobRequest(job_type="priceops.execute", payload={}), correlation_id="refused")
        assert a.query("SELECT * FROM durable_jobs") == before
        _enable(actual, audit)
        job, _ = valid.enqueue(JobRequest(job_type="priceops.execute", payload={}), correlation_id="queued")
        before = valid.get(job.job_id)
        with pytest.raises(FeatureFlagStoreUnavailable, match="same-engine"):
            invalid.lease(45)
        assert valid.get(job.job_id) == before
        with pytest.raises(FeatureFlagStoreUnavailable, match="same-engine"):
            invalid.claim_next()
        assert valid.get(job.job_id) == before
        # Absence never blocks unrelated committed audit-delivery work.
        invalid.enqueue(JobRequest(job_type="durable-audit-delivery", payload={}), correlation_id="audit-only")
        assert invalid.claim_next(audit_only=True).job_type == "durable-audit-delivery"
    finally:
        a.close()
        b.close()


def test_temporary_flag_storage_failure_preserves_claim_attempt_version_fence_and_retry(tmp_path, monkeypatch):
    from shared.jobs.queue import JobRequest

    engine, registry, audit = _owner(tmp_path / "temporary.sqlite")
    queue = DurableJobQueue(engine, flags=registry)
    try:
        _enable(registry, audit)
        job, _ = queue.enqueue(JobRequest(job_type="priceops.execute", payload={}), correlation_id="queued")
        original = queue.get(job.job_id)

        def unavailable():
            raise FeatureFlagStoreUnavailable("Synthetic temporary storage failure")

        with monkeypatch.context() as patch:
            patch.setattr(registry, "read_versioned", unavailable)
            with pytest.raises(FeatureFlagStoreUnavailable, match="temporary"):
                queue.claim_next()
            assert queue.get(job.job_id) == original
            with pytest.raises(FeatureFlagStoreUnavailable, match="temporary"):
                queue.lease(45)
            assert queue.get(job.job_id) == original
        claimed = queue.claim_next()
        assert claimed.job_id == original.job_id
        assert claimed.attempts == original.attempts + 1
        assert claimed.fence_token == original.fence_token + 1
        assert claimed.version == original.version + 1
    finally:
        engine.close()


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("operation", ["register", "approve", "enable", "disable"])
def test_unavailable_local_or_live_admin_owner_all_four_commands_are_503_not_500(tmp_path, monkeypatch, live, operation):
    from apps.api.oday_api import runtime_mode
    from shared.infrastructure.persistence.feature_flags import UnavailableFeatureFlagRegistry

    engine, _registry, audit = _owner(tmp_path / "unavailable.sqlite")
    monkeypatch.setattr(runtime_mode, "live_data_required", lambda: live)
    client, actor_headers = _api(UnavailableFeatureFlagRegistry(), audit, engine)
    before = engine.query("SELECT * FROM durable_documents ORDER BY collection, doc_id")
    try:
        if operation == "register":
            path, body = "/admin/feature-flags", {"key": "synthetic.new", "owner": "admin-a"}
        else:
            path = "/admin/feature-flags/" + KEY + "/" + operation
            body = None if operation == "disable" else {}
        response = client.post(path, headers=actor_headers("admin-a"), json=body)
        assert response.status_code == 503, response.text
        assert response.json()["detail"] == "FEATURE_FLAG_STORE_UNAVAILABLE"
        assert engine.query("SELECT * FROM durable_documents ORDER BY collection, doc_id") == before
        assert not [event for event in audit.list_events() if event.event_type.startswith("feature_flag.")]
    finally:
        engine.close()


@pytest.mark.parametrize("mismatch", ["missing", "RAM", "other-engine", "queue-owner", "queue-engine", "missing-queue"])
def test_last_dispatch_refuses_wrong_owner_even_after_correct_claim_and_real_disable(tmp_path, mismatch):
    from types import SimpleNamespace

    from apps.worker.oday_worker.main import ODayWorker
    from shared.auth.feature_flags import FeatureFlagRegistry
    from shared.jobs.queue import JobRequest
    from shared.jobs.registry import JobRegistry

    a, actual, audit_a = _owner(tmp_path / "claimed.sqlite")
    b, other, audit_b = _owner(tmp_path / "other.sqlite")
    queue = DurableJobQueue(a, flags=actual)
    called = []
    handlers = JobRegistry()
    handlers.register("priceops.execute", lambda job, persistence: called.append(job.job_id))
    try:
        _enable(actual, audit_a)
        _enable(other, audit_b)
        queue.enqueue(JobRequest(job_type="priceops.execute", payload={}), correlation_id="before-disable")
        claimed = queue.claim_next()
        _command(actual, audit_a, "disable")
        enabled_ram = FeatureFlagRegistry({**DEFAULT_FLAGS, KEY: replace(
            DEFAULT_FLAGS[KEY], enabled=True, approved_by=frozenset({"admin-a", "admin-b"}),
        )})
        owner = {"missing": None, "RAM": enabled_ram, "other-engine": other}.get(mismatch, actual)
        selected_queue = queue
        if mismatch == "queue-owner":
            selected_queue = DurableJobQueue(a, flags=DurableFeatureFlagRegistry(SqliteDocumentStore(a)))
        elif mismatch == "queue-engine":
            selected_queue = DurableJobQueue(b, flags=other)
        elif mismatch == "missing-queue":
            selected_queue = None
        before = queue.get(claimed.job_id)
        worker = ODayWorker(persistence=SimpleNamespace(
            engine=a, job_queue=selected_queue, feature_flag_registry=owner,
        ), registry=handlers)
        with pytest.raises(FeatureFlagStoreUnavailable, match="same-engine"):
            worker.execute_job(claimed)
        assert called == []
        assert queue.get(claimed.job_id) == before
    finally:
        a.close()
        b.close()


def _global_pending(path):
    engine, registry, _ = _owner(path)
    sink = _SyntheticSink()
    audit = DurableAuditLog(engine, worm_sink=sink)
    queue = DurableJobQueue(engine, flags=registry)
    result = _command(registry, audit, "approve", queue=queue)
    job = queue.get_by_idempotency_key("platform-feature-flag-audit:" + result.audit_event_id)
    return engine, registry, audit, queue, sink, result.audit_event_id, job


def test_global_backlog_counts_real_pending_with_tenant_and_only_actual_receipt_delivered(tmp_path):
    from shared.audit import AuditEvent
    from shared.audit.backlog import AuditBacklogUnavailable, DurableAuditBacklog
    from shared.audit.durable_delivery import DurableAuditDelivery

    engine, registry, audit, queue, sink, event_id, job = _global_pending(tmp_path / "backlog.sqlite")
    checker = DurableAuditBacklog(engine, sink.sink_id)
    try:
        tenant_delivery = DurableAuditDelivery(engine, "tenant-a", queue, sink)
        with engine.transaction():
            # This explicit SQLite fixture needs a real active SQL transaction
            # before installing its same-engine transactional WORM sink.
            engine.execute("BEGIN IMMEDIATE")
            with audit.transactional_worm_sink(tenant_delivery):
                audit.record(AuditEvent(
                    event_type="synthetic.tenant-business", actor="synthetic-tenant-writer",
                    action="test", resource="synthetic:tenant-object", outcome="allow",
                    correlation_id="synthetic-tenant-command", metadata={"tenant_id": "tenant-a"},
                ))
        assert checker.inspect("tenant-a").pending == 1
        assert checker.inspect("tenant-b").pending == 0
        assert checker.all_tenants().pending == 2
        assert sink.events == []  # Reservations were not external acknowledgement.
        receipt = PlatformFlagAuditDelivery(engine, queue, sink).deliver(job)
        assert receipt["record_id"] == event_id
        assert len(sink.events) == 1
        assert checker.all_tenants().pending == 1
        assert checker.inspect("tenant-a").pending == 1
        intent = registry.store.get(DELIVERY_COLLECTION, event_id)
        intent["receipt"]["object_uri"] = "outbox://not-an-external-ack"
        registry.store.put(DELIVERY_COLLECTION, event_id, intent)
        with pytest.raises(AuditBacklogUnavailable):
            checker.all_tenants()
    finally:
        engine.close()


@pytest.mark.parametrize("fault", ["intent", "event", "job", "sink", "hash", "scope", "intent-event", "status", "duplicate-job"])
def test_global_backlog_missing_or_tampered_facts_fail_closed_not_ignored(tmp_path, fault):
    import json

    from shared.audit.backlog import AuditBacklogUnavailable, DurableAuditBacklog
    from shared.jobs.queue import JobRequest

    engine, registry, _audit, queue, sink, event_id, job = _global_pending(tmp_path / "tampered.sqlite")
    checker = DurableAuditBacklog(engine, sink.sink_id)
    try:
        assert checker.all_tenants().pending == 1
        if fault == "intent":
            engine.execute("DELETE FROM durable_documents WHERE collection = ? AND doc_id = ?",
                           (DELIVERY_COLLECTION, event_id))
        elif fault == "event":
            engine.execute("DELETE FROM durable_audit_events WHERE event_id = ?", (event_id,))
        elif fault == "job":
            engine.execute("DELETE FROM durable_jobs WHERE job_id = ?", (job.job_id,))
        elif fault == "sink":
            checker = DurableAuditBacklog(engine, "unrelated-sink")
        elif fault in {"hash", "scope"}:
            payload = dict(job.payload)
            if fault == "scope":
                payload.pop("scope")
            else:
                payload["event_hash"] = "not-the-committed-hash"
            engine.execute("UPDATE durable_jobs SET payload_json = ? WHERE job_id = ?",
                           (json.dumps(payload), job.job_id))
        elif fault in {"intent-event", "status"}:
            intent = registry.store.get(DELIVERY_COLLECTION, event_id)
            if fault == "intent-event":
                intent["event"] = replace(intent["event"], actor="forged-actor")
            else:
                intent["status"] = "synthetic-unverified-success"
            registry.store.put(DELIVERY_COLLECTION, event_id, intent)
        else:
            queue.enqueue(JobRequest(job_type=job.job_type, payload=job.payload,
                                     idempotency_key="unrelated-duplicate-key"), correlation_id=job.correlation_id)
        with pytest.raises(AuditBacklogUnavailable):
            checker.all_tenants()
        assert checker.inspect("tenant-a").pending == 0
    finally:
        engine.close()


def test_platform_global_pending_uses_same_exact_five_minute_boundary(tmp_path):
    from datetime import timedelta

    from shared.audit.backlog import DurableAuditBacklog

    engine, _registry, _audit, queue, sink, _event_id, job = _global_pending(tmp_path / "clock.sqlite")
    try:
        checker = DurableAuditBacklog(engine, sink.sink_id)
        at = job.created_at + timedelta(seconds=300)
        assert checker.all_tenants(now=at).pending == 1
        assert checker.all_tenants(now=at).oldest_seconds == 300
        assert not checker.all_tenants(now=at).blocked
        assert checker.all_tenants(now=at + timedelta(microseconds=1)).blocked
        assert queue.get(job.job_id).status == job.status
    finally:
        engine.close()

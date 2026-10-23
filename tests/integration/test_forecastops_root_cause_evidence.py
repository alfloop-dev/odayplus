"""Stage B SQL/owner controls; synthetic registered inference, not real ML/PG.

The existing Forecast HTTP fixture runs the original Forecast writer and
retains its exact canonical joins. Its observations, snapshot IDs and model
artifact are explicitly engineering values; none becomes verified raw input.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta

import pytest

from modules.forecastops.application.root_cause_evidence import ScopedDiagnosticEvidenceReader
from modules.forecastops.domain.root_cause import (
    CAUSE_CATEGORIES,
    DiagnosticEvidenceConflict,
    DiagnosticEvidenceDenied,
    DiagnosticEvidenceJob,
    DiagnosticEvidenceUnavailable,
)
from modules.forecastops.infrastructure.root_cause_repository import (
    DurableDiagnosticEvidenceRepository,
)
from shared.auth import DataClassification, Principal, Role, Scope
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.repositories import DurableStoreRepository
from tests.integration.test_store_ops_current_forecast import (
    ORIGIN,
    STORE,
    TENANT,
)
from tests.integration.test_store_ops_current_forecast import (
    case as case,
)


@pytest.fixture
def diagnostic(case):
    # Exact owned canonical store rows, not a fake/unbound store repository.
    case.engine.execute("INSERT INTO tenants(tenant_id, tenant_name) VALUES (?, ?)", (TENANT, "Engineering"))
    case.engine.execute(
        "INSERT INTO brands(brand_id, tenant_id, brand_code, brand_name) VALUES (?, ?, ?, ?)",
        ("forecast-brand", TENANT, "engineering-forecast", "Engineering"),
    )
    case.engine.execute(
        "INSERT INTO address_locations(address_id, raw_address) VALUES (?, ?)",
        ("engineering-forecast-address", "Engineering only"),
    )
    stores = DurableStoreRepository(case.engine)
    stores.save_store(replace(case.stores.get_store(STORE), address_id="engineering-forecast-address"))
    evidence = DurableDiagnosticEvidenceRepository(case.documents)
    reader = ScopedDiagnosticEvidenceReader(
        engine=case.engine, forecasts=case.forecasts, issues=case.state,
        stores=stores, evidence=evidence, audit=case.audit,
    )
    principal = Principal(
        subject_id="engineering-diagnostic-reader", roles=frozenset({Role.OPERATIONS_MANAGER}),
        scope=Scope(tenant_id=TENANT, brand_ids=frozenset({"forecast-brand"}),
                    region_ids=frozenset({"north"}), store_ids=frozenset({STORE}),
                    modules=frozenset({"operator_console"}), clearance=DataClassification.CONFIDENTIAL),
    )
    return case, reader, principal


def _read(value, principal=None):
    case, reader, actor = value
    return reader.read_current(principal=principal or actor, issue_id=case.issue_id,
                               correlation_id="engineering-diagnostic-read")


def _rows(case):
    return {
        "documents": [dict(row) for row in case.engine.query(
            "SELECT * FROM durable_documents ORDER BY collection, doc_id")],
        "jobs": [dict(row) for row in case.engine.query("SELECT * FROM durable_jobs ORDER BY job_id")],
    }


def test_actual_saved_forecast_zero_is_recorded_but_no_independent_observation_or_cause(diagnostic):
    case, _, _ = diagnostic
    before, calls = _rows(case), case.inference.calls
    value = _read(diagnostic)
    facts = {row.name: row for row in value.facts}
    assert facts["actualRevenue"].value == 0.0
    assert facts["actualRevenue"].source_id == case.output.forecast_output_id
    assert facts["actualRevenue"].source_kind == "recorded_forecast_result"
    assert value.unknown_categories == CAUSE_CATEGORIES
    data = value.to_dict()
    assert data["cause_candidates"] is data["evidence_strength"] is data["data_confidence"] is None
    assert all(row["verification"] == "unverified_reference"
               for row in data["source_snapshot_references"])
    assert all(row["independent_observation_verified"] is False for row in data["facts"])
    assert value.feature_snapshot_time <= value.prediction_origin_time == ORIGIN
    assert dict(value.versions)["policy_version_id"] == case.alert.policy_version_id
    assert dict(value.versions)["model_id"] == case.output.model_version
    assert _rows(case) == before and case.inference.calls == calls
    with pytest.raises(FrozenInstanceError):
        value.store_id = "caller-store"
    with pytest.raises(FrozenInstanceError):
        value.facts[0].value = 999.0


@pytest.mark.parametrize("axis,value", [
    ("store_ids", frozenset({"different-store"})),
    ("brand_ids", frozenset({"different-brand"})),
    ("region_ids", frozenset({"different-region"})),
    ("modules", frozenset({"avm"})),
    ("clearance", DataClassification.PUBLIC),
])
def test_current_scope_reduction_denies_before_evidence_return_or_business_write(diagnostic, axis, value):
    case, _, principal = diagnostic
    before = _rows(case)
    principal = replace(principal, scope=replace(principal.scope, **{axis: value}))
    with pytest.raises(DiagnosticEvidenceDenied):
        _read(diagnostic, principal)
    assert _rows(case) == before
    denied = [event for event in case.audit.list_events(tenant_id=TENANT)
              if event.event_type == "security.authorization" and event.outcome == "deny"]
    assert len(denied) == 1 and denied[0].actor == principal.subject_id


def test_foreign_tenant_and_unauthenticated_principal_do_not_read_retained_evidence(diagnostic):
    case, reader, principal = diagnostic
    retained = reader.capture(principal=principal, issue_id=case.issue_id, correlation_id="capture")
    before = _rows(case)
    for denied in (replace(principal, authenticated=False),
                   replace(principal, scope=replace(principal.scope, tenant_id="other-tenant"))):
        with pytest.raises(DiagnosticEvidenceDenied):
            reader.read_retained(principal=denied, evidence_id=retained.evidence_id,
                                 correlation_id="denied-retained")
    assert _rows(case) == before


@pytest.mark.parametrize("change", ["future_feature_cutoff", "missing_snapshot_reference", "wrong_policy"])
def test_actual_retained_PIT_and_source_contradictions_are_unavailable(diagnostic, change):
    case, _, _ = diagnostic
    if change == "future_feature_cutoff":
        run = case.forecasts.get_prediction_run(TENANT, case.output.prediction_run_id)
        case.forecasts.save_prediction_run(TENANT, replace(run, feature_snapshot_time=ORIGIN + timedelta(seconds=1)))
    elif change == "missing_snapshot_reference":
        case.documents.put(case.forecasts._collection(case.forecasts._FORECASTS, TENANT),
                           case.output.forecast_output_id, replace(case.output, source_snapshot_ids=()),
                           group_key=STORE, seq=case.output.forecast_version)
    else:
        case.forecasts.save_alert(replace(case.alert, policy_version_id="unrelated-policy"))
    before = _rows(case)
    with pytest.raises(DiagnosticEvidenceUnavailable):
        _read(diagnostic)
    assert _rows(case) == before


def test_current_canonical_store_owner_is_rechecked_for_retained_read(diagnostic):
    case, reader, principal = diagnostic
    retained = reader.capture(principal=principal, issue_id=case.issue_id, correlation_id="capture")
    reader.stores.save_store(replace(reader.stores.get_store(STORE), is_current=False))
    before = _rows(case)
    with pytest.raises(DiagnosticEvidenceDenied):
        reader.read_retained(principal=principal, evidence_id=retained.evidence_id,
                             correlation_id="store-revoked")
    assert _rows(case) == before


def test_immutable_capture_replay_and_reopen_preserve_exact_body(diagnostic):
    case, reader, principal = diagnostic
    first = reader.capture(principal=principal, issue_id=case.issue_id, correlation_id="capture")
    second = reader.capture(principal=principal, issue_id=case.issue_id, correlation_id="capture-replay")
    assert second == first
    events = [row for row in case.audit.list_events(tenant_id=TENANT)
              if row.event_type == "forecastops.diagnostic_evidence.recorded"]
    assert len(events) == 1 and events[0].actor == principal.subject_id
    other = SqliteEngine(case.engine.path)
    try:
        reopened = DurableDiagnosticEvidenceRepository(SqliteDocumentStore(other))
        assert reopened.get(TENANT, first.evidence_id) == first
        assert reopened.get("other-tenant", first.evidence_id) is None
    finally:
        other.close()


def test_actual_audit_failure_rolls_back_new_evidence_without_claiming_WORM_ack(diagnostic, monkeypatch):
    case, reader, principal = diagnostic
    before = _rows(case)
    record = case.audit.record

    def fail(event):
        if event.event_type == "forecastops.diagnostic_evidence.recorded":
            raise RuntimeError("engineering audit failure")
        return record(event)

    monkeypatch.setattr(case.audit, "record", fail)
    with pytest.raises(RuntimeError, match="engineering audit failure"):
        reader.capture(principal=principal, issue_id=case.issue_id, correlation_id="capture-failure")
    assert _rows(case) == before


@pytest.mark.parametrize("owner", ["forecasts", "issues", "stores", "evidence", "audit"])
def test_another_engine_cannot_be_composed_as_same_diagnostic_owner(diagnostic, tmp_path, owner):
    case, reader, _ = diagnostic
    other = SqliteEngine(tmp_path / "other-owner.sqlite")
    try:
        from modules.opsboard.application.store_ops import DurableStoreOpsRepository
        from shared.infrastructure.persistence.audit_log import DurableAuditLog
        from shared.infrastructure.persistence.repositories import DurableForecastOpsRepository

        documents = SqliteDocumentStore(other)
        wrong = {"forecasts": DurableForecastOpsRepository(documents),
                 "issues": DurableStoreOpsRepository(documents),
                 "stores": DurableStoreRepository(other),
                 "evidence": DurableDiagnosticEvidenceRepository(documents),
                 "audit": DurableAuditLog(other)}
        values = {"engine": case.engine, "forecasts": reader.forecasts, "issues": reader.issues,
                  "stores": reader.stores, "evidence": reader.evidence, "audit": reader.audit}
        values[owner] = wrong[owner]
        with pytest.raises(DiagnosticEvidenceUnavailable, match="same SQL owner"):
            ScopedDiagnosticEvidenceReader(**values)
    finally:
        other.close()


def test_two_actual_SQL_owners_cannot_rebind_the_same_saved_source_identity(diagnostic):
    case, reader, principal = diagnostic
    first = reader.capture(principal=principal, issue_id=case.issue_id, correlation_id="capture")
    changed = replace(first, facts=(replace(first.facts[0], value=123.0), *first.facts[1:]))
    assert changed.evidence_id == first.evidence_id
    assert changed.source_sha256 != first.source_sha256
    other = SqliteEngine(case.engine.path)
    try:
        reopened = DurableDiagnosticEvidenceRepository(SqliteDocumentStore(other))
        with pytest.raises(DiagnosticEvidenceConflict):
            reopened.save(changed)
        assert reopened.get(TENANT, first.evidence_id) == first
    finally:
        other.close()


def test_original_factory_wires_actual_SQL_owner_and_memory_does_not_fallback(tmp_path, monkeypatch):
    from shared.infrastructure.persistence.factory import build_persistence

    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "false")
    monkeypatch.setenv("ODP_DEPLOY_ENV", "test")
    monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
    bundle = build_persistence(mode="durable", db_path=tmp_path / "factory-evidence.sqlite")
    try:
        reader = bundle.diagnostic_evidence_reader()
        assert reader.engine is bundle.engine is bundle.diagnostic_evidence_repository.engine
        assert reader.forecasts is bundle.forecastops_repository
        assert reader.issues is bundle.store_ops_repository
        assert reader.stores is bundle.store_repository
        assert reader.audit is bundle.audit_log
    finally:
        bundle.engine.close()
    memory = build_persistence(mode="memory")
    assert memory.diagnostic_evidence_repository is None
    with pytest.raises(DiagnosticEvidenceUnavailable):
        memory.diagnostic_evidence_reader()


def test_worker_identity_contract_resolves_actual_evidence_and_never_accepts_metric_values(diagnostic):
    value = _read(diagnostic)
    payload = {"tenant_id": value.tenant_id, "store_id": value.store_id,
               "issue_id": value.issue_id, "expected_source_sha256": value.source_sha256}
    job = DiagnosticEvidenceJob.from_payload(payload)
    job.require_source(value)
    with pytest.raises(ValueError, match="source identity only"):
        DiagnosticEvidenceJob.from_payload({**payload, "actualRevenue": 999999})
    with pytest.raises(DiagnosticEvidenceConflict):
        job.require_source(replace(value, facts=(replace(value.facts[0], value=999999), *value.facts[1:])))


def test_retained_finite_numeric_tamper_does_not_recompute_its_integrity_claim(diagnostic):
    case, reader, principal = diagnostic
    value = reader.capture(principal=principal, issue_id=case.issue_id, correlation_id="capture")
    collection = reader.evidence._collection(TENANT)
    retained = case.documents.get(collection, value.evidence_id)
    changed = replace(value, facts=(replace(value.facts[0], value=999999), *value.facts[1:]))
    case.documents.put(collection, value.evidence_id, replace(retained, evidence=changed), seq=1)
    with pytest.raises(DiagnosticEvidenceUnavailable):
        reader.read_retained(principal=principal, evidence_id=value.evidence_id,
                             correlation_id="tampered-retained")


def test_workflow_version_progress_does_not_recreate_or_reset_retained_source(diagnostic):
    case, reader, principal = diagnostic
    first = reader.capture(principal=principal, issue_id=case.issue_id, correlation_id="capture")
    state, version = case.state.read_versioned_state()
    assert case.state.compare_and_save_state(state, expected_version=version)
    current = _read(diagnostic)
    assert current.workflow_version > first.workflow_version
    assert current.source_sha256 == first.source_sha256
    assert reader.capture(principal=principal, issue_id=case.issue_id,
                          correlation_id="after-workflow-version") == first
    retained = reader.read_retained(principal=principal, evidence_id=first.evidence_id,
                                    correlation_id="retained-current-owner")
    assert retained == first
    assert len([event for event in case.audit.list_events(tenant_id=TENANT)
                if event.event_type == "forecastops.diagnostic_evidence.recorded"]) == 1


def test_actual_insert_receipt_distinguishes_first_write_from_same_value_replay(diagnostic):
    _, reader, _ = diagnostic
    value = _read(diagnostic)
    first = reader.evidence.save_with_created(value)
    second = reader.evidence.save_with_created(value)
    assert first.created is True and second.created is False
    assert first.evidence == second.evidence == value
    assert reader.evidence.save(value) == value


@pytest.mark.parametrize("axis", ["assigned_area_ids", "heat_zone_ids"])
@pytest.mark.parametrize("operation", ["read_current", "capture", "read_retained"])
def test_explicit_unproven_area_or_heat_zone_scope_denies_all_diagnostic_reads(diagnostic, axis, operation):
    case, reader, principal = diagnostic
    retained = reader.capture(principal=principal, issue_id=case.issue_id, correlation_id="scope-setup")
    before, inference_calls = _rows(case), case.inference.calls
    old_events = {row.event_id: row for row in case.audit.list_events(tenant_id=TENANT)}
    reduced = replace(principal, scope=replace(principal.scope, **{axis: frozenset({"unrelated-restriction"})}))
    arguments = {"principal": reduced, "correlation_id": "unproven-" + axis + "-" + operation}
    arguments.update({"evidence_id": retained.evidence_id} if operation == "read_retained"
                     else {"issue_id": case.issue_id})
    with pytest.raises(DiagnosticEvidenceDenied, match="area or heat-zone scope"):
        getattr(reader, operation)(**arguments)
    assert _rows(case) == before and case.inference.calls == inference_calls
    events = {row.event_id: row for row in case.audit.list_events(tenant_id=TENANT)}
    assert {key: events[key] for key in old_events} == old_events
    added = [row for key, row in events.items() if key not in old_events]
    assert len(added) == 1
    assert (added[0].event_type, added[0].outcome, added[0].actor, added[0].correlation_id) == (
        "security.authorization", "deny", principal.subject_id, arguments["correlation_id"],
    )
    assert reader.evidence.get(TENANT, retained.evidence_id) == retained

"""Actual PG insert winner/audit; original synthetic Forecast, not real ML.

Both owners first observe an absent diagnostic record. The real persistence
boundary used by capture then interleaves two transactions, including rollback
of the first inserter. No source/capture/SQL method is replaced or mocked.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest

from modules.forecastops.application.forecasting import ForecastOpsService
from modules.forecastops.application.production_model import RegisteredEstimatorForecastEngine
from modules.forecastops.domain.forecasting import (
    ForecastInput,
    StoreDayObservation,
    default_forecast_alert_policy,
)
from modules.opsboard.application.store_ops_materializer import materialize_store_ops_state
from shared.auth import DataClassification, Principal, Role, Scope
from shared.domain import Store
from shared.governance import InMemoryDecisionPolicyRepository
from shared.infrastructure.persistence.assisted_listing_intake import apply_upgrade_to_database
from tests.integration.test_intake_registry_calendar_postgresql import _bundle, _upgrade
from tests.integration.test_store_ops_current_forecast import ORIGIN, SyntheticRecordedInference

pytestmark = pytest.mark.requires_live_env


@pytest.fixture
def owners(intake_blank_db, monkeypatch, tmp_path):
    _upgrade(intake_blank_db, "head")
    apply_upgrade_to_database(intake_blank_db.url())
    monkeypatch.setenv("ODP_AUDIT_WORM_SINK_URI", (tmp_path / "owned-diagnostic-worm").as_uri())
    first = _bundle(intake_blank_db, monkeypatch)
    second = None
    try:
        second = _bundle(intake_blank_db, monkeypatch)
        assert first.engine is not second.engine
        assert first.engine.dialect == second.engine.dialect == "postgresql"
        # Storage remains genuine PG; only the declared engineering inference
        # input lane is POC, exactly like the existing native owner controls.
        monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "false")
        monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
        monkeypatch.setenv("ODP_DEPLOY_ENV", "test")
        tenant, brand, store, address = (str(uuid4()) for _ in range(4))
        with intake_blank_db.connect() as connection:
            connection.execute("INSERT INTO core.tenants(tenant_id,tenant_name) VALUES (%s,%s)",
                               (tenant, "Engineering diagnostic race"))
            connection.execute(
                "INSERT INTO core.brands(brand_id,tenant_id,brand_code,brand_name) VALUES (%s,%s,%s,%s)",
                (brand, tenant, brand, "Engineering diagnostic race"),
            )
            connection.execute("INSERT INTO core.address_locations(address_id,raw_address) VALUES (%s,%s)",
                               (address, "Engineering only"))
        first.store_repository.save_store(Store(store_id=store, tenant_id=tenant, brand_id=brand,
                                                address_id=address, region_code="north"))
        producer = ForecastOpsService(
            repository=first.forecastops_repository, runtime_mode="poc",
            engine=RegisteredEstimatorForecastEngine(SyntheticRecordedInference()),
            policy_repository=InMemoryDecisionPolicyRepository([default_forecast_alert_policy(tenant)]),
        )
        produced = producer.forecast([ForecastInput(
            store_id=store, tenant_id=tenant, prediction_origin_time=ORIGIN,
            observations=tuple(StoreDayObservation(
                store_id=store, business_date=(ORIGIN - timedelta(days=60 - index)).date(),
                actual_revenue=0.0, site_score_baseline_p50=100.0, data_quality_score=1.0,
                source_snapshot_ids=(f"engineering-PG-pos-{index}",),
            ) for index in range(60)),
        )], prediction_run_id="engineering-PG-run-" + str(uuid4()), scored_at=ORIGIN)
        materialize_store_ops_state(repository=first.store_ops_repository,
                                    store_repository=first.store_repository,
                                    forecastops_repository=first.forecastops_repository, tenant_id=tenant)
        principal = Principal(subject_id="engineering-first", roles=frozenset({Role.OPERATIONS_MANAGER}),
                              scope=Scope(tenant_id=tenant, brand_ids=frozenset({brand}),
                                          region_ids=frozenset({"north"}), store_ids=frozenset({store}),
                                          modules=frozenset({"operator_console"}),
                                          clearance=DataClassification.CONFIDENTIAL))
        from dataclasses import replace

        actors = (principal, replace(principal, subject_id="engineering-second"))
        readers = (first.diagnostic_evidence_reader(), second.diagnostic_evidence_reader())
        issue_id = "ISS-FC-" + produced.alerts[0].alert_id
        values = tuple(reader.read_current(principal=actor, issue_id=issue_id,
                                            correlation_id="engineering-PG-source-admission")
                       for reader, actor in zip(readers, actors, strict=True))
        assert values[0] == values[1]
        yield intake_blank_db, (first, second), readers, actors, values
    finally:
        if second is not None:
            second.engine.close()
        first.engine.close()


def _wait_for_owned_backend_lock(database, pid):
    deadline = monotonic() + 5
    with database.connect() as connection:
        while monotonic() < deadline:
            row = connection.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", (pid,)).fetchone()
            if row is not None and row[0] == "Lock":
                return True
            sleep(0.01)
    return False


@pytest.mark.parametrize("rollback_first", [False, True])
def test_two_PG_initially_absent_writers_audit_only_actual_committed_insert_winner(owners, rollback_first):
    database, (first, second), (left, right), (actor_left, actor_right), (value_left, value_right) = owners
    assert left.evidence.get(value_left.tenant_id, value_left.evidence_id) is None
    assert right.evidence.get(value_right.tenant_id, value_right.evidence_id) is None
    started = Event()
    observed = {}

    def competing_capture_write():
        with second.engine.transaction():
            observed["pid"] = second.engine.query_one("SELECT pg_backend_pid() AS pid")["pid"]
            started.set()
            # This is the actual write/audit boundary used by capture after
            # actual current-source admission above, not a mirrored algorithm.
            return right._retain(value_right, principal=actor_right, correlation_id="engineering-PG-right")

    with ThreadPoolExecutor(max_workers=1) as executor:
        try:
            with first.engine.transaction():
                left._retain(value_left, principal=actor_left, correlation_id="engineering-PG-left")
                pending = executor.submit(competing_capture_write)
                assert started.wait(5)
                assert _wait_for_owned_backend_lock(database, observed["pid"])
                assert not pending.done()
                if rollback_first:
                    raise RuntimeError("engineering rollback of first inserter")
        except RuntimeError as exc:
            assert rollback_first and str(exc) == "engineering rollback of first inserter"
        result = pending.result(timeout=20)
    assert result == value_left
    assert left.evidence.get(value_left.tenant_id, value_left.evidence_id) == value_left
    events = [event for event in first.audit_log.list_events(tenant_id=value_left.tenant_id)
              if event.event_type == "forecastops.diagnostic_evidence.recorded"]
    assert len(events) == 1
    assert events[0].actor == (actor_right.subject_id if rollback_first else actor_left.subject_id)
    assert events[0].metadata["source_sha256"] == value_left.source_sha256
    assert first.engine.query_one(
        "SELECT COUNT(*) AS count FROM durable_documents WHERE collection = ? AND doc_id = ?",
        (left.evidence._collection(value_left.tenant_id), value_left.evidence_id),
    )["count"] == 1

"""Two actual PostgreSQL engines: native commands read after the row lock.

These are concurrency controls for the durable canonical owner, using the
existing owned PostgreSQL provider and complete migrations. They do not prove
an OSS production model, approval, live marketing action or human acceptance.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest

from modules.netplan.application.planning import NetPlanService
from modules.netplan.domain.planning import compute_solver_problem_hash
from shared.infrastructure.persistence import PostgresDocumentStore
from shared.infrastructure.persistence.assisted_listing_intake import apply_upgrade_to_database
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.repositories import DurableNetPlanRepository
from solver.netplan import NetPlanConstraints
from tests.integration.test_intake_registry_calendar_postgresql import _bundle, _upgrade

pytestmark = pytest.mark.requires_live_env


@pytest.fixture
def owners(intake_blank_db, monkeypatch, tmp_path):
    _upgrade(intake_blank_db, "head")
    apply_upgrade_to_database(intake_blank_db.url())
    monkeypatch.setenv("ODP_AUDIT_WORM_SINK_URI", (tmp_path / "owned-worm").as_uri())
    first = _bundle(intake_blank_db, monkeypatch)
    second = None
    try:
        second = _bundle(intake_blank_db, monkeypatch)
        assert first.engine is not second.engine
        assert first.engine.dialect == second.engine.dialect == "postgresql"
        # _bundle requires live storage while composing each actual PG owner.
        # This fixture exercises native locks with declared synthetic options,
        # not OSS production execution. Reset only its model-mode inputs after
        # both owners exist; monkeypatch restores the caller's environment.
        monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "false")
        monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
        monkeypatch.setenv("ODP_DEPLOY_ENV", "test")
        tenant, scenario_id, store = [str(uuid4()) for _ in range(3)]
        repositories = [
            DurableNetPlanRepository(TenantScopedDocumentStore(PostgresDocumentStore(bundle.engine), tenant))
            for bundle in (first, second)
        ]
        # Explicit engineering solver mode; actual PostgreSQL storage/locks,
        # synthetic options and no fabricated production executor/approval.
        NetPlanService(repository=repositories[0], runtime_mode="poc").create_scenario(
            tenant_id=tenant, scenario_id=scenario_id, scenario_name="Owned concurrency scenario",
            planning_horizon="2026-H2", constraints=NetPlanConstraints(max_budget=100),
            existing_stores=[{"store_id": store, "baseline_gross_margin": 100,
                              "improve_cost": 150, "improve_gross_margin_uplift": 200,
                              "source_snapshot_ids": ["declared-concurrency-input"]}],
            correlation_id="owned-concurrency-source",
        )
        yield intake_blank_db, first, second, repositories, scenario_id
    finally:
        if second is not None:
            second.engine.close()
        first.engine.close()


def _wait_for_owned_backend_lock(database, pid):
    deadline = monotonic() + 5
    with database.connect() as connection:
        while monotonic() < deadline:
            row = connection.execute(
                "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", (pid,)
            ).fetchone()
            if row is not None and row[0] == "Lock":
                return True
            sleep(0.01)
    return False


@pytest.mark.parametrize("operation", ["update", "solve"])
def test_native_command_cannot_read_then_overwrite_a_locked_builder_revision(owners, monkeypatch, operation):
    database, first, second, (builder_owner, native_owner), scenario_id = owners
    started, native_read = Event(), Event()
    observed = {}
    original_get = native_owner.get_scenario

    def observe_real_get(identifier):
        native_read.set()
        return original_get(identifier)

    monkeypatch.setattr(native_owner, "get_scenario", observe_real_get)
    native = NetPlanService(repository=native_owner, runtime_mode="poc")

    def native_command():
        # Bind a real owned backend PID for the observation only. The service's
        # original durable entry is still responsible for the actual row lock.
        with second.engine.transaction():
            observed["pid"] = second.engine.query_one("SELECT pg_backend_pid() AS pid")["pid"]
            started.set()
            if operation == "update":
                return native.update_scenario(scenario_id, scenario_name="Subsequent native name edit")
            return native.solve(scenario_id, actor="declared-native-actor", reason="Owned solver race")

    with ThreadPoolExecutor(max_workers=1) as executor:
        # This is the same physical owner row and SQL lock used by the scoped
        # builder. The second service must wait at the tenant/row owner entry before its first domain read.
        with first.engine.transaction():
            first.engine.query_one(
                "SELECT pg_advisory_xact_lock(hashtextextended(?, 0)) AS locked",
                (f"operator-domain:network-rebalance:{builder_owner._store.tenant_id}",),
            )
            first.engine.query_one(
                "SELECT doc_id FROM durable_documents "
                "WHERE collection = ? AND doc_id = ? FOR UPDATE",
                (builder_owner._store._collection(builder_owner._SCENARIOS), scenario_id),
            )
            current, version = builder_owner._store.get_versioned(builder_owner._SCENARIOS, scenario_id)
            changed = replace(current, constraints=replace(current.constraints, max_budget=300))
            assert builder_owner._store.compare_and_put(
                builder_owner._SCENARIOS, scenario_id, changed, expected_version=version,
            ) is not None
            pending = executor.submit(native_command)
            assert started.wait(5)
            assert _wait_for_owned_backend_lock(database, observed["pid"])
            assert not native_read.is_set(), "native command read stale state before the canonical row lock"
        result = pending.result(timeout=20)
    persisted = builder_owner.get_scenario(scenario_id)
    assert native_read.is_set()
    assert persisted.constraints.max_budget == 300
    if operation == "update":
        assert result.scenario_name == persisted.scenario_name == "Subsequent native name edit"
    else:
        expected = compute_solver_problem_hash(
            changed.options_by_entity, changed.constraints, 100_000.0, 3, changed.model_version,
        )
        assert result.problem_hash == expected
        assert builder_owner.get_solve(scenario_id).problem_hash == expected
        assert persisted.status.value == "solved"

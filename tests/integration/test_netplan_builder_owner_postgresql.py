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
from tests.integration import test_netplan_solver as netplan_inputs
from tests.integration._operation_flag_fixtures import NETPLAN_MOVE_EXIT_FLAG, enable_operation_flag
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


@pytest.fixture
def move_approval_owners(owners, monkeypatch):
    database, first, second, _, _ = owners
    options = netplan_inputs._lifecycle_options()
    options["store-001"] = tuple(option for option in options["store-001"] if option.action.value == "MOVE")
    with monkeypatch.context() as scoped:
        scoped.setattr(netplan_inputs, "_lifecycle_options", lambda: options)
        memory, template, scenario_id, receipt = netplan_inputs._pending_authoritative_service()
    repository = DurableNetPlanRepository(TenantScopedDocumentStore(
        PostgresDocumentStore(first.engine), netplan_inputs.NETPLAN_TENANT_ID,
    ))
    repository.save_scenario(memory.get_scenario(scenario_id))
    repository.save_solve(memory.get_solve(scenario_id))
    for acknowledgement in memory.list_disclosure_acknowledgements(scenario_id):
        repository.save_disclosure_acknowledgement(acknowledgement)
    enable_operation_flag(first.feature_flag_registry, key=NETPLAN_MOVE_EXIT_FLAG,
        tenant_id=netplan_inputs.NETPLAN_TENANT_ID, queue=first.job_queue, audit_log=first.audit_log)
    service = NetPlanService(repository=repository, approval_verifier=template.approval_verifier,
        policy_repository=template.policy_repository, runtime_mode="poc",
        feature_flags=first.feature_flag_registry, job_queue=first.job_queue)
    assert any(action.action.value == "MOVE" for action in repository.get_solve(scenario_id).result.selected_actions)
    return database, first, second, repository, service, scenario_id, receipt


@pytest.mark.parametrize("rollback", [False, True])
def test_netplan_move_flag_lock_lasts_through_business_commit_or_rollback(move_approval_owners, monkeypatch, rollback):
    database, first, second, repository, service, scenario_id, receipt = move_approval_owners
    allowed, admin_started, release_business = Event(), Event(), Event()
    observed = {}
    original_enabled = first.feature_flag_registry.is_enabled
    original_cas = second.feature_flag_registry.store.compare_and_put
    original_approval = repository.save_approval
    before = repository.get_scenario(scenario_id)

    def pause_after_real_flag_read(*args, **kwargs):
        enabled = original_enabled(*args, **kwargs)
        assert enabled
        allowed.set()
        assert release_business.wait(30)
        return enabled

    def observe_admin_cas(*args, **kwargs):
        observed["pid"] = second.engine.query_one("SELECT pg_backend_pid() AS pid")["pid"]
        admin_started.set()
        return original_cas(*args, **kwargs)

    def save_then_rollback(approval):
        original_approval(approval)
        raise RuntimeError("synthetic-business-rollback")

    monkeypatch.setattr(first.feature_flag_registry, "is_enabled", pause_after_real_flag_read)
    monkeypatch.setattr(second.feature_flag_registry.store, "compare_and_put", observe_admin_cas)
    if rollback:
        monkeypatch.setattr(repository, "save_approval", save_then_rollback)
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = executor.submit(service.decide, scenario_id, actor_id=netplan_inputs.APPROVAL_PRINCIPAL,
            reason="Explicit synthetic MOVE approval", approval_receipt_id=receipt.receipt_id,
            decided_at=netplan_inputs.MOMENT)
        try:
            assert allowed.wait(10)
            disable = executor.submit(second.feature_flag_registry.execute, "disable", NETPLAN_MOVE_EXIT_FLAG,
                actor="synthetic-admin", correlation_id="synthetic-disable-during-business", audit_log=second.audit_log,
                queue=second.job_queue)
            assert admin_started.wait(5)
            blocked = _wait_for_owned_backend_lock(database, observed["pid"])
        finally:
            release_business.set()
        if rollback:
            with pytest.raises(RuntimeError, match="synthetic-business-rollback"):
                pending.result(timeout=20)
        else:
            assert pending.result(timeout=20).decision == "approved"
        assert not disable.result(timeout=20).flag.enabled
        assert blocked, "admin disable did not wait for the original business transaction"
    assert not original_enabled(NETPLAN_MOVE_EXIT_FLAG)
    if rollback:
        assert repository.get_scenario(scenario_id) == before
        assert repository.list_approvals(scenario_id) == []
    else:
        assert repository.get_scenario(scenario_id).status.value == "approved"
        assert len(repository.list_approvals(scenario_id)) == 1


def test_netplan_move_waits_for_prior_disable_and_refuses_positive_write(move_approval_owners, monkeypatch):
    database, first, second, repository, service, scenario_id, receipt = move_approval_owners
    disabled_uncommitted, business_started, release_admin = Event(), Event(), Event()
    observed = {}
    original_enabled = first.feature_flag_registry.is_enabled
    original_cas = second.feature_flag_registry.store.compare_and_put
    before = repository.get_scenario(scenario_id)

    def pause_after_admin_cas(*args, **kwargs):
        result = original_cas(*args, **kwargs)
        disabled_uncommitted.set()
        assert release_admin.wait(30)
        return result

    def observe_business_flag(*args, **kwargs):
        observed["pid"] = first.engine.query_one("SELECT pg_backend_pid() AS pid")["pid"]
        business_started.set()
        return original_enabled(*args, **kwargs)

    monkeypatch.setattr(second.feature_flag_registry.store, "compare_and_put", pause_after_admin_cas)
    monkeypatch.setattr(first.feature_flag_registry, "is_enabled", observe_business_flag)
    with ThreadPoolExecutor(max_workers=2) as executor:
        disable = executor.submit(second.feature_flag_registry.execute, "disable", NETPLAN_MOVE_EXIT_FLAG,
            actor="synthetic-admin", correlation_id="synthetic-disable-before-business", audit_log=second.audit_log,
            queue=second.job_queue)
        try:
            assert disabled_uncommitted.wait(10)
            pending = executor.submit(service.decide, scenario_id, actor_id=netplan_inputs.APPROVAL_PRINCIPAL,
                reason="Explicit synthetic MOVE approval", approval_receipt_id=receipt.receipt_id,
                decided_at=netplan_inputs.MOMENT)
            assert business_started.wait(5)
            blocked = _wait_for_owned_backend_lock(database, observed["pid"])
        finally:
            release_admin.set()
        assert not disable.result(timeout=20).flag.enabled
        with pytest.raises(PermissionError, match="FEATURE_FLAG_DISABLED"):
            pending.result(timeout=20)
        assert blocked, "business flag read did not wait for the original admin transaction"
    assert repository.get_scenario(scenario_id) == before
    assert repository.list_approvals(scenario_id) == []

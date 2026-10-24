"""Signed HTTP with real owned SQLite CAS; not native PG/model/UAT evidence."""

import json
from contextlib import contextmanager
from dataclasses import dataclass, replace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules.netplan_builder import create_netplan_builder_router
from apps.api.oday_api.security.dependencies import build_engine, require_operator_permission
from modules.avm.application.valuation import AVMService
from modules.avm.domain.valuation import ValuationInput
from modules.netplan.application.planning import NetPlanService
from modules.opsboard.application.netplan_builder import CanonicalNetPlanBuilder
from modules.opsboard.application.network_rebalance import (
    NetworkRebalanceConflict,
    NetworkRebalanceService,
)
from modules.opsboard.auth import AuthBoundaryConfig, AuthenticationBoundary
from shared.api.errors import install_error_handlers
from shared.audit import AuditEvent
from shared.audit.durable_delivery import COLLECTION as AUDIT_DELIVERY_COLLECTION
from shared.audit.durable_delivery import JOB_TYPE as AUDIT_DELIVERY_JOB_TYPE
from shared.audit.policy import SECURITY_EVENT_TYPE
from shared.auth import Action
from shared.domain.models import AddressLocation, Brand, Store, Tenant
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.infrastructure.persistence.operator_domains import (
    DurableOperatorDomainStateRepository,
    TenantScopedDocumentStore,
)
from shared.infrastructure.persistence.repositories import (
    DurableAVMRepository,
    DurableNetPlanRepository,
)
from solver.netplan import NetPlanConstraints
from tests.security.test_feature_flag_admin_authorization import ISSUER, SIGNING_KEY, _headers

TENANT, STORE, OTHER = "builder-tenant", "builder-store", "builder-other-store"
BASE = f"/operator/network-rebalance/stores/{STORE}/netplan/builder"


@dataclass
class Case:
    bundle: object
    repository: object
    state: object
    client: TestClient
    scenario_id: str
    mappings: dict
    rebalance_factory: object


@contextmanager
def _case(path, *, scope=None, seed=True, runtime_mode="poc", wrong_state=None):
    bundle = _durable_bundle(path)
    try:
        documents = SqliteDocumentStore(bundle.engine)
        scoped = TenantScopedDocumentStore(documents, TENANT)
        repository = DurableNetPlanRepository(scoped)
        avm = DurableAVMRepository(scoped)
        state = DurableOperatorDomainStateRepository(documents, "network-rebalance")
        scenario_id = "builder-scenario"
        if seed:
            bundle.tenant_repository.save_tenant(
                Tenant(tenant_id=TENANT, tenant_name="Synthetic builder tenant")
            )
            bundle.brand_repository.save_brand(Brand(brand_id="builder-brand", tenant_id=TENANT))
            for store in (STORE, OTHER):
                bundle.address_location_repository.save_address(
                    AddressLocation(address_id=store + "-address", tenant_id=TENANT)
                )
                bundle.store_repository.save_store(
                    Store(
                        store_id=store,
                        tenant_id=TENANT,
                        brand_id="builder-brand",
                        address_id=store + "-address",
                        region_code="north",
                    )
                )
                AVMService(repository=avm, runtime_mode="poc").create_case(
                    ValuationInput(
                        store_id=store,
                        gm_ttm=100,
                        forecast_gm_next_12m=110,
                        asset_book_value=10,
                        equipment_fair_value=10,
                        source_snapshot_ids=("synthetic-finance-input",),
                    ),
                    created_by="synthetic-fixture",
                    correlation_id="synthetic-fixture",
                )
            NetPlanService(repository=repository, runtime_mode="poc").create_scenario(
                tenant_id=TENANT,
                scenario_id=scenario_id,
                scenario_name="Synthetic canonical options",
                planning_horizon="2026-H2",
                constraints=NetPlanConstraints(
                    max_budget=100,
                    max_construction_days=20,
                    policy_version="synthetic-policy-version",
                ),
                existing_stores=[
                    {
                        "store_id": store,
                        "baseline_gross_margin": 100,
                        "improve_cost": 10,
                        "improve_gross_margin_uplift": 20,
                        "source_snapshot_ids": ["synthetic-options-input"],
                    }
                    for store in (STORE, OTHER)
                ],
                correlation_id="synthetic-fixture",
            )
            # Explicit synthetic resource observations for the engineering
            # solver; do not silently treat undeclared construction as zero.
            original_scenario = repository.get_scenario(scenario_id)
            repository.save_scenario(
                replace(
                    original_scenario,
                    options_by_entity={
                        entity: tuple(replace(option, construction_days=1) for option in options)
                        for entity, options in original_scenario.options_by_entity.items()
                    },
                )
            )
            state.save(
                TENANT,
                {
                    "stores": [
                        {
                            "id": store,
                            "storeId": store,
                            "storeName": store,
                            "status": "avmready",
                            "canonicalNetPlanScenarioIds": [scenario_id],
                            "evidence": [],
                        }
                        for store in (STORE, OTHER)
                    ],
                    "auditEvents": [],
                    "governApprovals": [],
                },
            )
        mappings = {
            "planner": {"roles": ["executive"], "scope": {"tenant_id": TENANT, **(scope or {})}},
            "viewer": {"roles": ["operator_viewer"], "scope": {"tenant_id": TENANT}},
            "foreign": {"roles": ["executive"], "scope": {"tenant_id": "foreign-tenant"}},
        }
        boundary = AuthenticationBoundary(
            AuthBoundaryConfig(
                issuer=ISSUER,
                audiences=frozenset({"unit-feature-api"}),
                signing_keys={SIGNING_KEY.kid: SIGNING_KEY},
                principal_mappings=mappings,
            ),
            audit_log=bundle.audit_log,
        )
        app = FastAPI()
        install_error_handlers(app)

        @app.middleware("http")
        async def correlation(request: Request, call_next):
            request.state.correlation_id = request.headers.get(
                "x-correlation-id", "builder-server-cid"
            )
            return await call_next(request)

        def service_factory(initial, tenant):
            return NetworkRebalanceService(
                initial_state=initial,
                seed_fixtures=False,
                avm_repository=avm,
                netplan_repository=repository,
                netplan_approval_verifier=getattr(service_factory, "approval_verifier", None),
                netplan_policy_repository=getattr(service_factory, "policy_repository", None),
                require_canonical=True,
                tenant_id=tenant,
                runtime_mode=runtime_mode,
            )

        def factory(request):
            principal = request.state.operator_principal
            actual_repository = (
                repository if principal.tenant_id == TENANT else
                bundle._scoped_repository("netplan_repository", principal.tenant_id)
            )
            return CanonicalNetPlanBuilder(
                principal=principal,
                scenario_repository=actual_repository,
                state_repository=wrong_state or state,
                rebalance_factory=service_factory,
                store_repository=bundle.store_repository,
                brand_repository=bundle.brand_repository,
                listing_snapshot_reader=lambda: {"listings": [], "candidates": []},
                audit_log=bundle.audit_log,
                job_queue=bundle.job_queue, feature_flags=bundle.feature_flag_registry,
                correlation_id=request.state.correlation_id,
                role_id=request.state.operator_role_id,
            )

        engine = build_engine(audit_log=bundle.audit_log)

        def guard(action, **kwargs):
            return require_operator_permission(
                "netplan", action, engine=engine, boundary=boundary, **kwargs
            )

        app.include_router(
            create_netplan_builder_router(
                factory=factory,
                require_view=guard(Action.VIEW, scoped_read_resource="operator_network"),
                require_edit=guard(Action.CREATE),
                require_solve=guard(Action.EXECUTE),
            ),
            prefix="/operator",
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            yield Case(bundle, repository, state, client, scenario_id, mappings, service_factory)
    finally:
        bundle.engine.close()


def _get(case, actor="planner"):
    return case.client.get(BASE, headers=_headers(actor))


def _command(
    case,
    current,
    *,
    operation="save",
    constraints=None,
    key="builder-command",
    actor="planner",
    reason="Synthetic scenario assumption change",
):
    body = {
        "scenarioId": current["scenarioId"],
        "expectedProblemHash": current["problemHash"],
        "reason": reason,
    }
    if operation == "save":
        body["constraints"] = {"max_budget": 200} if constraints is None else constraints
    headers = {**_headers(actor), "Idempotency-Key": key}
    return (
        case.client.put(BASE, json=body, headers=headers)
        if operation == "save"
        else case.client.post(BASE + "/solve", json=body, headers=headers)
    )


def _security_delivery_rows(case):
    """Exclude only deliveries exactly joined to verified authorization facts.

    The real AuditLog verifies its persisted events. Unmatched/corrupt rows,
    other audit types, command receipts and all ordinary jobs remain visible.
    """
    documents = SqliteDocumentStore(case.bundle.engine)
    deliveries = {}
    for event in case.bundle.audit_log.list_events():
        tenant = event.metadata.get("tenant_id")
        if event.event_type != SECURITY_EVENT_TYPE or not isinstance(tenant, str) or not tenant:
            continue
        scoped = TenantScopedDocumentStore(documents, tenant)
        value = scoped.get(AUDIT_DELIVERY_COLLECTION, event.event_id)
        stored = value.get("event") if isinstance(value, dict) else None
        if (not isinstance(stored, AuditEvent) or stored.to_dict() != event.to_dict()
                or value.get("sink_id") != event.worm_sink_id):
            continue
        deliveries[(scoped._collection(AUDIT_DELIVERY_COLLECTION), event.event_id)] = event
    excluded_jobs = set()
    by_event = {event.event_id: event for event in deliveries.values()}
    for row in case.bundle.engine.query("SELECT * FROM durable_jobs ORDER BY job_id"):
        if row["job_type"] != AUDIT_DELIVERY_JOB_TYPE:
            continue
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            continue  # Keep this unknown job in the business snapshot.
        event = by_event.get(payload.get("audit_event_id")) if isinstance(payload, dict) else None
        if event is None:
            continue
        tenant = event.metadata["tenant_id"]
        if (payload == {"tenant_id": tenant, "audit_event_id": event.event_id,
                        "event_hash": event.event_hash, "sink_id": event.worm_sink_id}
                and row["idempotency_key"] == f"durable-audit:{tenant}:{event.event_id}"
                and row["correlation_id"] == event.correlation_id):
            excluded_jobs.add(row["job_id"])
    return set(deliveries), excluded_jobs


def _business(case):
    security_documents, security_jobs = _security_delivery_rows(case)
    return (
        [
            tuple(row)
            for row in case.bundle.engine.query(
                "SELECT * FROM durable_documents ORDER BY collection, doc_id"
            )
            if (row["collection"], row["doc_id"]) not in security_documents
        ],
        [
            tuple(row)
            for row in case.bundle.engine.query("SELECT * FROM durable_jobs ORDER BY job_id")
            if row["job_id"] not in security_jobs
        ],
    )


def test_business_snapshot_preserves_domain_outbox_but_not_repeated_authorization_delivery(tmp_path, monkeypatch):
    monkeypatch.setenv("ODP_AUDIT_WORM_SINK_URI", (tmp_path / "owned-audit-worm").as_uri())
    with _case(tmp_path / "exact-audit-snapshot.sqlite3") as case:
        before = _business(case)
        security_before = {event.event_id for event in case.bundle.audit_log.list_events()
                           if event.event_type == SECURITY_EVENT_TYPE}
        read = _get(case)
        assert read.status_code == 200, read.text
        security_after = {event.event_id for event in case.bundle.audit_log.list_events()
                          if event.event_type == SECURITY_EVENT_TYPE}
        assert security_after > security_before
        assert _business(case) == before
        current = read.json()
        accepted = _command(case, current, key="visible-domain-outbox")
        assert accepted.status_code == 200, accepted.text
        after_save = _business(case)
        assert after_save != before
        identity = accepted.json()["audit"]["event_id"]
        event = case.bundle.audit_log.get_event(identity, tenant_id=TENANT)
        assert event is not None and event.event_type == "operator.netplan.builder.save"
        assert case.state.load(TENANT)["netPlanBuilderReceipts"]
        scoped = TenantScopedDocumentStore(SqliteDocumentStore(case.bundle.engine), TENANT)
        domain_delivery = case.bundle.engine.query_one(
            "SELECT * FROM durable_documents WHERE collection = ? AND doc_id = ?",
            (scoped._collection(AUDIT_DELIVERY_COLLECTION), identity),
        )
        assert domain_delivery is not None and tuple(domain_delivery) in after_save[0]
        domain_jobs = [row for row in case.bundle.engine.query(
            "SELECT * FROM durable_jobs WHERE job_type = ?", (AUDIT_DELIVERY_JOB_TYPE,))
            if json.loads(row["payload_json"]).get("audit_event_id") == identity]
        assert len(domain_jobs) == 1 and tuple(domain_jobs[0]) in after_save[1]
        excluded_before = _security_delivery_rows(case)
        replayed = _command(case, current, key="visible-domain-outbox")
        assert replayed.status_code == 200 and replayed.json()["idempotentReplay"] is True
        assert replayed.json()["audit"] == accepted.json()["audit"]
        excluded_after = _security_delivery_rows(case)
        assert excluded_after[0] > excluded_before[0] and excluded_after[1] > excluded_before[1]
        assert _business(case) == after_save


def test_real_options_policy_and_source_ids_are_read_without_synthetic_defaults(tmp_path):
    with _case(tmp_path / "read.sqlite3") as c:
        response = _get(c)
        assert response.status_code == 200
        value = response.json()
        canonical = c.repository.get_scenario(c.scenario_id)
        assert value["optionsByEntity"] == canonical.to_dict()["options_by_entity"]
        assert value["constraints"] == canonical.constraints.to_dict()
        assert value["policyVersion"] == "synthetic-policy-version" and value["solve"] is None
        assert value["statusHistory"] == [] and c.repository.list_approvals(c.scenario_id) == []


def test_save_invalidates_real_solve_selection_then_explicit_resolve_has_trusted_audit(tmp_path):
    with _case(tmp_path / "save.sqlite3") as c:
        original = _get(c).json()
        solved = _command(c, original, operation="solve", key="first-solve")
        assert solved.status_code == 200, solved.text
        before = solved.json()["builder"]
        assert before["status"] == "solved" and before["solve"]
        old_options = before["optionsByEntity"]
        saved = _command(c, before, constraints={"max_budget": 1}, key="edit")
        assert saved.status_code == 200, saved.text
        draft = saved.json()["builder"]
        assert draft["status"] == "draft" and draft["solve"] is None and draft["isStale"]
        assert (
            draft["problemHash"] != before["problemHash"]
            and draft["optionsByEntity"] == old_options
        )
        assert draft["constraints"]["max_construction_days"] == 20
        assert draft["policyVersion"] == before["policyVersion"]
        state = c.state.load(TENANT)
        row = next(s for s in state["stores"] if s["id"] == STORE)
        assert row["selectedScenarioId"] is None and row["netPlanScenarios"] == []
        event = c.bundle.audit_log.get_event(saved.json()["audit"]["event_id"], tenant_id=TENANT)
        assert event.actor == "planner" and event.correlation_id == "unit-request"
        assert event.metadata["previousProblemHash"] == before["problemHash"]
        again = _command(c, draft, operation="solve", key="second-solve")
        assert again.status_code == 200, again.text
        assert again.json()["builder"]["status"] in {"solved", "infeasible"}
        assert c.repository.list_approvals(c.scenario_id) == []


def test_expected_hash_and_semantic_idempotency_do_not_overwrite_current_draft(tmp_path):
    with _case(tmp_path / "cas.sqlite3") as c:
        original = _get(c).json()
        accepted = _command(c, original)
        assert accepted.status_code == 200
        before = _business(c)
        replay = _command(c, original)
        assert replay.status_code == 200 and replay.json()["idempotentReplay"] is True
        assert replay.json()["audit"] == accepted.json()["audit"]
        assert _business(c) == before
        conflict = _command(c, original, constraints={"max_budget": 201})
        assert conflict.status_code == 409 and _business(c) == before
        stale = _command(c, original, key="different-command")
        assert stale.status_code == 409 and _business(c) == before


@pytest.mark.parametrize(
    "scope",
    [
        {"store_ids": [STORE]},
        {"brand_ids": ["outside"]},
        {"region_ids": ["south"]},
        {"assigned_area_ids": ["unproven-area"]},
        {"heat_zone_ids": ["unproven-zone"]},
    ],
)
def test_every_actual_target_axis_is_rechecked_not_just_panel_store(tmp_path, scope):
    with _case(tmp_path / "scopes.sqlite3", scope=scope) as c:
        before = _business(c)
        assert _get(c).status_code == 403
        assert _business(c) == before
        events = c.bundle.audit_log.list_events(tenant_id=TENANT)
        assert any(e.actor == "planner" and e.outcome == "deny" for e in events)


def test_foreign_and_readonly_principals_cannot_write_or_receive_editor_authority(tmp_path):
    with _case(tmp_path / "roles.sqlite3") as c:
        before = _business(c)
        assert _get(c, "foreign").status_code == 404
        view = _get(c, "viewer")
        assert view.status_code == 200, view.text
        assert view.json()["editable"] is False and view.json()["canSolve"] is False
        assert _command(c, view.json(), actor="viewer").status_code == 403
        assert _business(c) == before


def test_real_business_audit_failure_rolls_back_draft_cas_and_aggregate(tmp_path, monkeypatch):
    with _case(tmp_path / "rollback.sqlite3") as c:
        current = _get(c).json()
        before = _business(c)
        record = c.bundle.audit_log.record

        def fail_business(event):
            if event.event_type == "operator.netplan.builder.save":
                raise RuntimeError("synthetic SQL audit failure")
            return record(event)

        monkeypatch.setattr(c.bundle.audit_log, "record", fail_business)
        result = _command(c, current)
        assert result.status_code == 503 and _business(c) == before
        assert c.repository.get_scenario(c.scenario_id).constraints.max_budget == 100


def test_infeasible_is_preserved_and_edit_creates_a_new_real_draft(tmp_path):
    with _case(tmp_path / "infeasible.sqlite3") as c:
        current = _get(c).json()
        constrained = _command(c, current, constraints={"min_expected_gross_margin": 1000000})
        assert constrained.status_code == 200
        solved = _command(
            c, constrained.json()["builder"], operation="solve", key="infeasible-solve"
        )
        assert solved.status_code == 200 and solved.json()["builder"]["status"] == "infeasible"
        old = c.repository.get_scenario(c.scenario_id)
        replacement = _command(
            c,
            solved.json()["builder"],
            constraints={"min_expected_gross_margin": None},
            key="new-draft",
        )
        assert replacement.status_code == 200
        new = replacement.json()["builder"]
        assert (
            new["scenarioId"] != old.scenario_id
            and new["status"] == "draft"
            and new["solve"] is None
        )
        assert c.repository.get_scenario(old.scenario_id) == old
        assert new["optionsByEntity"] == old.to_dict()["options_by_entity"]


def test_missing_production_executor_never_falls_back_to_local_solver(tmp_path):
    with _case(tmp_path / "production.sqlite3", runtime_mode="production") as c:
        current = _get(c).json()
        before = _business(c)
        result = _command(c, current, operation="solve")
        assert result.status_code == 503 and _business(c) == before
        assert c.repository.get_solve(c.scenario_id) is None


@pytest.mark.parametrize(
    "raw",
    [
        '"constraints":{"max_budget":NaN}',
        '"constraints":{"max_budget":1e309}',
        '"constraints":{"policy_version":"forged"}',
        '"actor":"forged","unrelated":NaN',
    ],
)
def test_raw_invalid_proof_and_model_source_fields_are_constant_safe_422(tmp_path, raw):
    with _case(tmp_path / "raw.sqlite3") as c:
        current = _get(c).json()
        before = _business(c)
        valid = json.dumps(
            {
                "scenarioId": current["scenarioId"],
                "expectedProblemHash": current["problemHash"],
                "reason": "Synthetic valid command prerequisite",
            }
        )
        body = valid[:-1] + "," + raw + "}"
        response = c.client.put(
            BASE,
            content=body,
            headers={
                **_headers("planner"),
                "Content-Type": "application/json",
                "Idempotency-Key": "raw",
            },
        )
        assert response.status_code == 422 and _business(c) == before
        assert (
            "NETPLAN_BUILDER" in response.text
            and "NaN" not in response.text
            and "Infinity" not in response.text
        )


def test_actual_stage_guard_cannot_skip_the_avm_workflow(tmp_path):
    with _case(tmp_path / "stage.sqlite3") as c:
        current = _get(c).json()
        state = c.state.load(TENANT)
        for row in state["stores"]:
            row["status"] = "watching"
        c.state.save(TENANT, state)
        before = _business(c)
        assert _command(c, current).status_code == 409
        assert _business(c) == before


def test_two_real_sql_engines_cannot_use_a_stale_problem_and_reopen_preserves_current(tmp_path):
    path = tmp_path / "two-engines.sqlite3"
    with _case(path) as first, _case(path, seed=False) as second:
        prior = _get(second).json()
        accepted = _command(first, _get(first).json(), constraints={"max_budget": 300})
        assert accepted.status_code == 200
        before = _business(second)
        stale = _command(second, prior, key="other-engine-command")
        assert stale.status_code == 409 and _business(second) == before
    with _case(path, seed=False) as reopened:
        assert _get(reopened).json()["constraints"]["max_budget"] == 300


def test_current_revoked_target_blocks_an_original_request_replay_without_new_write(tmp_path):
    path = tmp_path / "revoked.sqlite3"
    with _case(path) as first:
        prior = _get(first).json()
        assert _command(first, prior).status_code == 200
    with _case(path, seed=False, scope={"store_ids": [STORE]}) as revoked:
        before = _business(revoked)
        assert _command(revoked, prior).status_code == 403
        assert _business(revoked) == before


def test_real_wrong_engine_state_owner_is_unavailable_before_business_change(tmp_path):
    other = _durable_bundle(tmp_path / "other.sqlite3")
    try:
        wrong_state = DurableOperatorDomainStateRepository(
            SqliteDocumentStore(other.engine), "network-rebalance"
        )
        with _case(tmp_path / "owned.sqlite3", wrong_state=wrong_state) as c:
            before = _business(c)
            response = _get(c)
            assert response.status_code == 503 and _business(c) == before
    finally:
        other.engine.close()


def test_edit_invalidates_old_solver_replay_without_erasing_its_committed_receipt(tmp_path):
    with _case(tmp_path / "old-replay.sqlite3") as c:
        original = c.rebalance_factory(c.state.load(TENANT), TENANT)
        original.solve_netplan(
            store_id=STORE,
            actor_role_id="expansion-manager",
            actor_name="planner",
            idempotency_key="original-solve",
            correlation_id="original-command",
        )
        c.state.save(TENANT, original.export_state())
        receipt = original.export_state()["idempotencyCache"][("solve_netplan", "original-solve")]
        response = _command(c, _get(c).json())
        assert response.status_code == 200
        # All stores in the same canonical plan lose the old selected view.
        state = c.state.load(TENANT)
        assert all(
            row["netPlanScenarios"] == [] and row["selectedScenarioId"] is None
            for row in state["stores"]
        )
        assert state["idempotencyCache"][("solve_netplan", "original-solve")] == receipt
        replay = c.rebalance_factory(state, TENANT)
        before = _business(c)
        with pytest.raises(NetworkRebalanceConflict, match="inputs changed"):
            replay.solve_netplan(
                store_id=STORE,
                actor_role_id="expansion-manager",
                actor_name="planner",
                idempotency_key="original-solve",
                correlation_id="original-command",
            )
        assert _business(c) == before


def test_actual_api_schema_declares_save_constraints_and_command_proof_required(tmp_path):
    with _case(tmp_path / "schema.sqlite3") as c:
        response = c.client.get("/openapi.json")
        assert response.status_code == 200
        schemas = response.json()["components"]["schemas"]
        assert set(schemas["NetPlanBuilderSaveCommand"]["required"]) == {"scenarioId", "expectedProblemHash", "reason", "constraints"}
        assert set(schemas["NetPlanBuilderCommand"]["required"]) == {"scenarioId", "expectedProblemHash", "reason"}
        assert "constraints" not in schemas["NetPlanBuilderCommand"]["properties"]


@pytest.mark.parametrize("race", ["save_row", "solve_row", "replay_row", "replay_active"])
def test_authorization_rechecks_exact_locked_row_and_active_replay_target(tmp_path, monkeypatch, race):
    # Two real engines change canonical state after the unlocked preflight and
    # immediately before the actual SQL lock. No permission/solver is mocked.
    path = tmp_path / "locked-authorization.sqlite3"
    with _case(path, scope={"store_ids": [STORE]}) as first, _case(path, seed=False) as writer:
        full = first.repository.get_scenario(first.scenario_id)
        first.repository.save_scenario(
            replace(full, options_by_entity={STORE: full.options_by_entity[STORE]})
        )
        original_request = _get(first).json()
        if race.startswith("replay"):
            assert _command(first, original_request).status_code == 200
        current = dict(original_request)
        if not race.startswith("replay"):
            # The caller knows the other version's hash; hash knowledge is not
            # authority to introduce that version's now-ungranted store.
            current["problemHash"] = CanonicalNetPlanBuilder.problem_hash(full)
        original_transaction = CanonicalNetPlanBuilder._transaction
        observed = {}

        @contextmanager
        def change_before_real_lock(service, scenario_id):
            assert not observed
            replacement = replace(
                writer.repository.get_scenario(first.scenario_id),
                options_by_entity=full.options_by_entity,
            )
            if race == "replay_active":
                replacement = replace(replacement, scenario_id="new-active-builder-scenario")
                writer.repository.save_scenario(replacement)
                state = writer.state.load(TENANT)
                for store in state["stores"]:
                    if store["id"] == STORE:
                        store["netPlanBuilderScenarioId"] = replacement.scenario_id
                writer.state.save(TENANT, state)
            else:
                writer.repository.save_scenario(replacement)
            observed["business"] = _business(first)
            observed["builder_audit_ids"] = {
                event.event_id for event in first.bundle.audit_log.list_events(tenant_id=TENANT)
                if event.event_type.startswith("operator.netplan.builder.")
            }
            with original_transaction(service, scenario_id):
                yield

        monkeypatch.setattr(CanonicalNetPlanBuilder, "_transaction", change_before_real_lock)
        response = _command(first, current, operation="solve" if race == "solve_row" else "save")
        assert response.status_code == 403, response.text
        assert observed and _business(first) == observed["business"]
        assert OTHER not in response.text
        events = first.bundle.audit_log.list_events(tenant_id=TENANT)
        assert {
            event.event_id for event in events
            if event.event_type.startswith("operator.netplan.builder.")
        } == observed["builder_audit_ids"]
        assert any(
            event.event_type == "security.authorization" and event.actor == "planner"
            and event.resource == f"netplan/{OTHER}" and event.outcome == "deny"
            for event in events
        )
        if race == "solve_row":
            assert first.repository.get_solve(first.scenario_id) is None


def test_prepared_candidate_cannot_restore_a_changed_solver_problem():
    # Reuse the existing domain fixture's actual sealed synthetic disclosure
    # receipt and verifier. Do not invent an approval for this builder feature.
    from modules.netplan.application.planning import NetPlanApprovalError
    from tests.integration.test_netplan_constraint_disclosure_approval import (
        APPROVAL_PRINCIPAL,
        MOMENT,
        RECEIPT_ID,
        SCENARIO_ID,
        ConstraintClass,
        _build,
        _fully_modelled_constraints,
    )

    service, repository = _build(_fully_modelled_constraints())
    prepared = service.prepare_unmodelled_constraint_acknowledgement(
        SCENARIO_ID, actor_id=APPROVAL_PRINCIPAL, reason="Declared existing fixture exposure",
        acknowledged_classes=(ConstraintClass.LEASE, ConstraintClass.SEQUENCING),
        approval_receipt_id=RECEIPT_ID, acknowledged_at=MOMENT,
    )
    assert service.pin_prepared_candidate(prepared).selected_candidate_id == prepared.selected_candidate_id
    current = repository.get_scenario(SCENARIO_ID)
    changed = replace(current, constraints=replace(current.constraints, max_budget=current.constraints.max_budget + 1))
    repository.save_scenario(changed)
    with pytest.raises(NetPlanApprovalError, match="scenario changed"):
        service.pin_prepared_candidate(prepared)
    assert repository.get_scenario(SCENARIO_ID) == changed

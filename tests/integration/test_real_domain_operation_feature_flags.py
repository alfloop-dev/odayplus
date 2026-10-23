"""Exact business-entry kill switches with owned SQLite and signed test actors.

Synthetic reports, scenarios and model cards are explicit engineering inputs.
They are not production data admission, human UAT, live PostgreSQL or WORM ack.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.learninghub import create_learninghub_router
from apps.api.app.routes.netplan import create_netplan_router
from apps.api.app.routes.operator_modules.network_reviews import create_network_review_sub_router
from apps.api.app.routes.sitescore import create_sitescore_router
from apps.api.oday_api.routes.feature_flags import create_feature_flags_router
from apps.api.oday_api.security import dependencies
from models.shared_ml import LocalModelArtifactStore, MetricThreshold, ModelAlias
from modules.learninghub import (
    InMemoryLearningHubRepository,
    LearningHubService,
    MlflowRegistryAdapter,
    ReleaseSagaState,
)
from modules.opsboard.auth import AuthBoundaryConfig, AuthenticationBoundary
from shared.audit import InMemoryAuditLog
from shared.auth import Action
from shared.auth.feature_flags import DEFAULT_FLAGS, FeatureFlagRegistry
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.feature_flags import DurableFeatureFlagRegistry
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.repositories import (
    DurableLearningHubRepository,
    DurableNetPlanRepository,
)
from tests.integration import test_netplan_solver as netplan_inputs
from tests.integration._learninghub_fixtures import prepare_candidate
from tests.security.test_feature_flag_admin_authorization import (
    ISSUER,
    SIGNING_KEY,
    _headers,
)
from tests.unit.test_network_review_canonical_action_metadata import (
    TENANT,
    components,
    install_real_related_producers,
    report,
)

PROOF = {"reason": "Explicit synthetic operational confirmation", "riskAcknowledged": True}
ADMIN = "/api/v1/admin/feature-flags/"
SITE_FLAG = "high_risk.sitescore.approve"
NET_FLAG = "high_risk.netplan.approve"
MODEL_FLAG = "high_risk.model.publish"


def _app(owner, audit, queue, monkeypatch, *, tenant):
    boundary = AuthenticationBoundary(AuthBoundaryConfig(
        issuer=ISSUER, audiences=frozenset({"unit-feature-api"}),
        signing_keys={SIGNING_KEY.kid: SIGNING_KEY},
        principal_mappings={
            "admin-a": {"roles": ["platform_admin"], "scope": {"tenant_id": tenant}},
            "admin-b": {"roles": ["platform_admin"], "scope": {"tenant_id": tenant}},
            "site-reviewer": {"roles": ["site_reviewer"], "scope": {"tenant_id": tenant}},
            "ml-owner": {"roles": ["release_owner"], "scope": {"tenant_id": tenant}},
            netplan_inputs.APPROVAL_PRINCIPAL: {
                "roles": ["executive"], "scope": {"tenant_id": tenant},
            },
        },
    ), audit_log=audit)
    monkeypatch.setattr(dependencies, "default_boundary", lambda: boundary)
    app = FastAPI()
    app.state.feature_flag_registry = owner
    app.state.job_queue = queue

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        request.state.correlation_id = request.headers.get("x-correlation-id", "synthetic-domain-flags")
        return await call_next(request)

    app.include_router(create_feature_flags_router(
        registry=owner, audit_log=audit, boundary=boundary, queue=queue,
        engine=dependencies.build_engine(audit_log=audit, flags=owner),
    ), prefix="/api/v1")
    return app


def _enable(client, key):
    # Actual admin HTTP principal ownership, never two body-supplied names.
    for actor in ("admin-a", "admin-a"):
        response = client.post(ADMIN + key + "/approve", json=PROOF, headers=_headers(actor))
        assert response.status_code == 200
        assert response.json()["flag"]["approved_by"] == ["admin-a"]
    denied = client.post(ADMIN + key + "/enable", json=PROOF, headers=_headers("admin-a"))
    assert denied.status_code == 403
    assert client.post(ADMIN + key + "/approve", json=PROOF, headers=_headers("admin-b")).status_code == 200
    enabled = client.post(ADMIN + key + "/enable", json=PROOF, headers=_headers("admin-a"))
    assert enabled.status_code == 200 and enabled.json()["flag"]["enabled"] is True
    assert set(enabled.json()["flag"]["approved_by"]) == {"admin-a", "admin-b"}


def _disable(client, key):
    response = client.post(ADMIN + key + "/disable", json=PROOF, headers=_headers("admin-b"))
    assert response.status_code == 200
    assert response.json()["flag"]["enabled"] is False
    assert response.json()["flag"]["approved_by"] == []


@contextmanager
def _site_case(tmp_path, monkeypatch, channel):
    engine, scores, decisions, audit, workflow, service = components(tmp_path / "site.sqlite")
    try:
        owner = DurableFeatureFlagRegistry(SqliteDocumentStore(engine))
        owner.initialize_defaults()
        queue = DurableJobQueue(engine, flags=owner)
        install_real_related_producers(engine, decisions, workflow, service)
        service._feature_flags = owner
        service._job_queue = queue
        frozen = scores.save_report(report())
        decision = workflow.open_decision(frozen, created_by="synthetic-proposer", tenant_id=TENANT,
                                          verified_creator=True)
        workflow.submit_for_review(decision.decision_id, submitted_by="synthetic-proposer",
                                   verified_submitter=True)
        app = _app(owner, audit, queue, monkeypatch, tenant=TENANT)
        if channel == "operator":
            app.include_router(create_network_review_sub_router(
                service, audit_log=audit,
                require_view_permission_fn=dependencies.require_operator_permission("sitescore", Action.VIEW),
                require_decide_permission_fn=dependencies.require_operator_permission("sitescore", Action.APPROVE),
            ), prefix="/api/v1")
        else:
            app.include_router(create_sitescore_router(
                repository=scores, workflow=workflow, audit_log=audit, job_queue=queue,
                runtime_mode="poc", review_service_resolver=lambda request: service,
            ), prefix="/api/v1")
        with TestClient(app) as client:
            yield SimpleNamespace(engine=engine, client=client, owner=owner, queue=queue,
                                  service=service, workflow=workflow, scores=scores,
                                  decisions=decisions, decision=decision, audit=audit)
    finally:
        engine.close()


def _site_business_state(context):
    decision = context.decisions.get_decision(context.decision.decision_id)
    return {
        "decision": decision,
        "approval": context.workflow._approval_store.get(decision.approval_id),
        "candidates": context.service._candidate_workflow_service._listing_repository.list_candidates(),
        "jobs": context.engine.query("SELECT * FROM durable_jobs"),
    }


def _site_request(context, channel, *, action="GO", key="synthetic-site-go"):
    headers = {**_headers("site-reviewer"), "x-operator-role": "siteReviewer",
               "idempotency-key": key, "if-actor-match": "site-reviewer"}
    if channel == "operator":
        return context.client.post("/api/v1/network-reviews/" + context.decision.decision_id + "/decide",
            headers=headers, json={"decision": action, "reason": "Explicit synthetic review rationale",
                                   "overrideAck": True, "conditions": "Recheck evidence" if action == "WAIT" else "",
                                   "requiredData": ["retained report"] if action == "RETURN" else []})
    return context.client.post("/api/v1/sitescore/decisions/" + context.decision.decision_id + "/decision",
        headers=headers, json={"action": "APPROVE" if action == "GO" else "REJECT", "actor": "site-reviewer",
                               "reason": "Explicit synthetic native review rationale",
                               "expected_version": context.decisions.get_decision(context.decision.decision_id).version,
                               "expected_actor_subject_id": "site-reviewer", "expected_tenant_id": TENANT})


@pytest.mark.parametrize("channel", ["operator", "native"])
def test_actual_sitescore_go_and_native_approve_share_admin_owned_flag(tmp_path, monkeypatch, channel):
    with _site_case(tmp_path, monkeypatch, channel) as case:
        before = case.decisions.get_decision(case.decision.decision_id)
        business_before = _site_business_state(case)
        denied = _site_request(case, channel)
        assert denied.status_code == 403 and "FEATURE_FLAG_DISABLED" in denied.text
        assert case.decisions.get_decision(before.decision_id) == before
        assert _site_business_state(case) == business_before
        _enable(case.client, SITE_FLAG)
        accepted = _site_request(case, channel)
        assert accepted.status_code == 200
        saved = case.decisions.get_decision(before.decision_id)
        assert saved.network_action == "GO" and saved.status.value == "APPROVED"
        assert saved.history[-1].actor == "site-reviewer"
        _disable(case.client, SITE_FLAG)
        if channel == "operator":
            replay = _site_request(case, channel)
            assert replay.status_code == 200 and replay.json()["idempotentReplay"] is True
        # A distinct real candidate proves disable blocks a fresh write without
        # inventing a same-candidate report-round transition in this flag test.
        candidate_service = case.service._candidate_workflow_service
        candidate = candidate_service._listing_repository.list_candidates()[0]
        next_candidate_id = "second-synthetic-flag-candidate"
        candidate_service._listing_repository.save_candidate(replace(candidate,
            candidate_site=replace(candidate.candidate_site, candidate_site_id=next_candidate_id, site_status="scored"),
            review_id=None))
        next_report = case.scores.save_report(replace(report(), candidate_site_id=next_candidate_id))
        next_decision = case.workflow.open_decision(next_report,
            created_by="synthetic-proposer", tenant_id=TENANT, verified_creator=True)
        case.workflow.submit_for_review(next_decision.decision_id, submitted_by="synthetic-proposer",
                                        verified_submitter=True)
        case.decision = next_decision
        denied_again = _site_request(case, channel, key="second-go")
        assert denied_again.status_code == 403
        assert case.decisions.get_decision(next_decision.decision_id).status.value == "PENDING_REVIEW"


@pytest.mark.parametrize("action", ["WAIT", "RETURN", "REJECT"])
def test_sitescore_non_go_keeps_its_original_workflow_with_flag_disabled(tmp_path, monkeypatch, action):
    with _site_case(tmp_path, monkeypatch, "operator") as case:
        assert case.owner.is_enabled(SITE_FLAG) is False
        response = _site_request(case, "operator", action=action)
        assert response.status_code == 200
        assert case.decisions.get_decision(case.decision.decision_id).network_action == action


@pytest.mark.parametrize("binding", ["missing", "RAM-enabled", "other-engine", "queue-owner", "queue-engine"])
def test_actual_sitescore_go_rejects_noncanonical_owner_before_business_change(tmp_path, monkeypatch, binding):
    with _site_case(tmp_path, monkeypatch, "operator") as case:
        other = SqliteEngine(tmp_path / "other.sqlite")
        try:
            _enable(case.client, SITE_FLAG)
            selected = case.owner
            if binding == "missing":
                selected = None
            elif binding == "RAM-enabled":
                selected = FeatureFlagRegistry(dict(DEFAULT_FLAGS))
                selected.add_approval(SITE_FLAG, "synthetic-a")
                selected.add_approval(SITE_FLAG, "synthetic-b")
                selected.enable(SITE_FLAG)
            elif binding == "other-engine":
                selected = DurableFeatureFlagRegistry(SqliteDocumentStore(other))
                selected.initialize_defaults()
            elif binding == "queue-owner":
                case.service._job_queue = DurableJobQueue(case.engine, flags=FeatureFlagRegistry(dict(DEFAULT_FLAGS)))
            else:
                case.service._job_queue = DurableJobQueue(other, flags=case.owner)
            case.service._feature_flags = selected
            before = _site_business_state(case)
            response = _site_request(case, "operator")
            assert response.status_code == 503
            assert _site_business_state(case) == before
        finally:
            other.close()


def _netplan_case(tmp_path, monkeypatch, *, move):
    options = netplan_inputs._lifecycle_options()
    if move:
        options["store-001"] = tuple(option for option in options["store-001"] if option.action.value == "MOVE")
        assert len(options["store-001"]) == 1
    else:
        # Keep a real feasible but unselected MOVE alternative. Only the
        # canonical primary's actions determine whether this decision is risky.
        keeper = next(option for option in options["store-001"] if option.action.value == "KEEP")
        mover = next(option for option in options["store-001"] if option.action.value == "MOVE")
        options["store-001"] = (keeper, replace(mover,
            expected_gross_margin=keeper.expected_gross_margin - 1,
            budget_cost=0.0, risk_score=keeper.risk_score))
        for entity, selected in [("store-002", "KEEP"), ("candidate-a", "OPEN"), ("candidate-b", "KEEP")]:
            options[entity] = tuple(option for option in options[entity] if option.action.value == selected)
    with monkeypatch.context() as scoped:
        scoped.setattr(netplan_inputs, "_lifecycle_options", lambda: options)
        memory, template, scenario_id, receipt = netplan_inputs._pending_authoritative_service()
    engine = SqliteEngine(tmp_path / "netplan.sqlite")
    try:
        store = SqliteDocumentStore(engine)
        repository = DurableNetPlanRepository(store)
        repository.save_scenario(memory.get_scenario(scenario_id))
        repository.save_solve(memory.get_solve(scenario_id))
        for row in memory.list_disclosure_acknowledgements(scenario_id):
            repository.save_disclosure_acknowledgement(row)
        owner = DurableFeatureFlagRegistry(store)
        owner.initialize_defaults()
        queue = DurableJobQueue(engine, flags=owner)
        audit = DurableAuditLog(engine)
        app = _app(owner, audit, queue, monkeypatch, tenant=netplan_inputs.NETPLAN_TENANT_ID)
        app.include_router(create_netplan_router(repository=repository, audit_log=audit,
            approval_verifier=template.approval_verifier, policy_repository=template.policy_repository,
            runtime_mode="poc", feature_flags=owner, job_queue=queue), prefix="/api/v1")
        return SimpleNamespace(engine=engine, repository=repository, owner=owner,
            client=TestClient(app), scenario_id=scenario_id, receipt=receipt)
    except BaseException:
        engine.close()
        raise


def _net_decide(case):
    return case.client.post("/api/v1/netplan/scenarios/" + case.scenario_id + "/decide",
        headers=_headers(netplan_inputs.APPROVAL_PRINCIPAL), json={
            "actor_id": netplan_inputs.APPROVAL_PRINCIPAL, "reason": "Explicit canonical scenario approval",
            "decision": "approved", "approval_receipt_id": case.receipt.receipt_id,
            "decided_at": netplan_inputs.MOMENT.isoformat()})


def test_actual_selected_netplan_move_approval_and_execution_recheck_same_flag(tmp_path, monkeypatch):
    case = _netplan_case(tmp_path, monkeypatch, move=True)
    try:
        before = case.repository.get_scenario(case.scenario_id)
        denied = _net_decide(case)
        assert denied.status_code == 403
        assert case.repository.get_scenario(case.scenario_id) == before
        assert case.repository.list_approvals(case.scenario_id) == []
        _enable(case.client, NET_FLAG)
        assert _net_decide(case).status_code == 200
        _disable(case.client, NET_FLAG)
        path = "/api/v1/netplan/scenarios/" + case.scenario_id + "/execute"
        body = {"executed_by": netplan_inputs.APPROVAL_PRINCIPAL,
                "executed_at": netplan_inputs.MOMENT.isoformat()}
        approved_before_execution = case.repository.get_scenario(case.scenario_id)
        blocked = case.client.post(path, headers=_headers(netplan_inputs.APPROVAL_PRINCIPAL), json=body)
        assert blocked.status_code == 403 and case.repository.get_execution(case.scenario_id) is None
        assert case.repository.get_scenario(case.scenario_id) == approved_before_execution
        _enable(case.client, NET_FLAG)
        executed = case.client.post(path, headers=_headers(netplan_inputs.APPROVAL_PRINCIPAL), json=body)
        assert executed.status_code == 200
        assert "MOVE" in {item.action.value for item in case.repository.get_execution(case.scenario_id).actions}
    finally:
        case.client.close()
        case.engine.close()


def test_selected_netplan_keep_improve_open_only_does_not_require_move_exit_flag(tmp_path, monkeypatch):
    case = _netplan_case(tmp_path, monkeypatch, move=False)
    try:
        assert case.owner.is_enabled(NET_FLAG) is False
        solve = case.repository.get_solve(case.scenario_id)
        assert not any(action.action.value in {"MOVE", "EXIT"} for action in solve.result.selected_actions)
        assert any(action.action.value == "MOVE" for alternative in solve.result.alternatives for action in alternative.actions)
        assert _net_decide(case).status_code == 200
    finally:
        case.client.close()
        case.engine.close()


def _release_body(version, revision, key, *, kind="FULL", rollback=None):
    return {"model_name": "forecast_revenue_interval", "version": version,
            "release_type": kind, "reason": "Explicit engineering release with recorded model card",
            "approval_id": key, "rollback_target": rollback or version, "monitoring_window": "48h",
            "success_criteria": ["known metric within approved range"], "fail_criteria": ["measured breach"],
            "affected_modules": ["ForecastOps"], "approved_by": "reviewer-a",
            "expected_release_revision": revision, "idempotency_key": key}


@pytest.mark.parametrize("release_type", ["FULL", "CANARY"])
def test_model_real_release_requires_flag_but_shadow_and_monitor_do_not(tmp_path, monkeypatch, release_type):
    owner = FeatureFlagRegistry(dict(DEFAULT_FLAGS))
    repository = InMemoryLearningHubRepository()
    audit = InMemoryAuditLog()
    service = LearningHubService(repository=repository, registry=MlflowRegistryAdapter(repository, tracking_uri="sqlite:///" + str(tmp_path / "own-case-mlflow.sqlite3")),
                                 audit_log=audit, artifact_store=LocalModelArtifactStore(tmp_path / "model-artifacts"),
                                 runtime_mode="poc", feature_flags=owner)
    prepare_candidate(service, "1.0.0")
    prepare_candidate(service, "1.1.0")
    app = _app(owner, audit, None, monkeypatch, tenant="model-engineering-tenant")
    app.include_router(create_learninghub_router(repository=repository, registry=service.registry,
        audit_log=audit, artifact_store=service.artifact_store,
        runtime_mode="poc", feature_flags=owner), prefix="/api/v1")
    with TestClient(app) as client:
        body = _release_body("1.0.0", 0, "first-release", kind=release_type)
        before = repository.get_release_revision(body["model_name"])
        denied = client.post("/api/v1/learninghub/releases", json=body, headers=_headers("ml-owner"))
        assert denied.status_code == 403
        assert repository.get_release_revision(body["model_name"]) == before
        assert repository.list_release_sagas(body["model_name"]) == []
        assert repository.get_alias(body["model_name"], ModelAlias.PRODUCTION) is None
        _enable(client, MODEL_FLAG)
        accepted = client.post("/api/v1/learninghub/releases", json=body, headers=_headers("ml-owner"))
        assert accepted.status_code == 201
        _disable(client, MODEL_FLAG)
        replay = client.post("/api/v1/learninghub/releases", json=body, headers=_headers("ml-owner"))
        assert replay.status_code == 201 and replay.json()["release_id"] == accepted.json()["release_id"]
        blocked = client.post("/api/v1/learninghub/releases",
            json=_release_body("1.1.0", 1, "next-release", kind=release_type), headers=_headers("ml-owner"))
        assert blocked.status_code == 403
        shadow = client.post("/api/v1/learninghub/releases",
            json=_release_body("1.1.0", 1, "shadow-release", kind="SHADOW"), headers=_headers("ml-owner"))
        assert shadow.status_code == 201
        assessment = service.monitor_release(release_id=accepted.json()["release_id"],
            observed_metrics={"w4_smape": 0.1}, guardrails=[MetricThreshold("w4_smape", max_value=0.2)],
            baseline_metrics={}, evaluated_by="synthetic-monitor")
        assert assessment.release_id == accepted.json()["release_id"]
        assert owner.is_enabled(MODEL_FLAG) is False


def test_model_disable_between_admission_and_true_execution_keeps_alias_untouched_and_saga_recoverable(tmp_path, monkeypatch):
    engine = SqliteEngine(tmp_path / "release-interleave.sqlite")
    try:
        store = SqliteDocumentStore(engine)
        owner = DurableFeatureFlagRegistry(store)
        owner.initialize_defaults()
        queue = DurableJobQueue(engine, flags=owner)
        repository = DurableLearningHubRepository(store)
        audit = DurableAuditLog(engine)
        service = LearningHubService(repository=repository, registry=MlflowRegistryAdapter(repository, tracking_uri="sqlite:///" + str(tmp_path / "own-case-mlflow.sqlite3")),
            audit_log=audit, artifact_store=LocalModelArtifactStore(tmp_path / "artifacts"),
            runtime_mode="poc", feature_flags=owner, job_queue=queue)
        prepare_candidate(service, "1.0.0")
        app = _app(owner, audit, queue, monkeypatch, tenant="model-engineering-tenant")
        app.include_router(create_learninghub_router(repository=repository, registry=service.registry,
            audit_log=audit, artifact_store=service.artifact_store, runtime_mode="poc",
            feature_flags=owner, job_queue=queue), prefix="/api/v1")
        original_execute = LearningHubService._execute_release_saga
        original_transition = service.registry.transition_stage
        transition_calls = []

        def observed_transition(**kwargs):
            transition_calls.append(kwargs)
            return original_transition(**kwargs)

        monkeypatch.setattr(service.registry, "transition_stage", observed_transition)
        with TestClient(app) as client:
            _enable(client, MODEL_FLAG)

            def disable_after_intent(actual_service, saga):
                assert saga.state is ReleaseSagaState.INTENT_RECORDED
                _disable(client, MODEL_FLAG)
                return original_execute(actual_service, saga)

            # Schedule a real admin mutation at the genuine saga boundary;
            # neither guard nor registry result is mocked to pass/fail.
            monkeypatch.setattr(LearningHubService, "_execute_release_saga", disable_after_intent)
            body = _release_body("1.0.0", 0, "between-preflight-and-execute")
            denied = client.post("/api/v1/learninghub/releases", json=body, headers=_headers("ml-owner"))
            assert denied.status_code == 403
            assert transition_calls == []
            assert repository.get_alias(body["model_name"], ModelAlias.PRODUCTION) is None
            assert repository.get_alias(body["model_name"], ModelAlias.CHAMPION) is None
            assert not repository.list_release_decisions()
            assert not any(event.event_type == "learninghub.model_release.v1" for event in audit.list_events())
            pending = repository.list_release_sagas(body["model_name"], include_terminal=False)
            assert len(pending) == 1 and pending[0].state is ReleaseSagaState.INTENT_RECORDED
            assert owner.is_enabled(MODEL_FLAG) is False
            recovered = service.recover_incomplete_releases(release_id=pending[0].release_id,
                requested_by="synthetic-recovery-owner", take_over_live_leases=True)
            assert len(recovered) == 1 and recovered[0].state is ReleaseSagaState.COMPENSATED
            assert repository.get_alias(body["model_name"], ModelAlias.PRODUCTION) is None
    finally:
        engine.close()


def test_model_rollback_remains_available_after_production_flag_is_disabled(tmp_path, monkeypatch):
    owner = FeatureFlagRegistry(dict(DEFAULT_FLAGS))
    repository = InMemoryLearningHubRepository()
    audit = InMemoryAuditLog()
    service = LearningHubService(repository=repository, registry=MlflowRegistryAdapter(repository, tracking_uri="sqlite:///" + str(tmp_path / "own-case-mlflow.sqlite3")),
        audit_log=audit, artifact_store=LocalModelArtifactStore(tmp_path / "rollback-artifacts"),
        runtime_mode="poc", feature_flags=owner)
    prepare_candidate(service, "1.0.0")
    prepare_candidate(service, "1.1.0")
    app = _app(owner, audit, None, monkeypatch, tenant="model-engineering-tenant")
    app.include_router(create_learninghub_router(repository=repository, registry=service.registry,
        audit_log=audit, artifact_store=service.artifact_store, runtime_mode="poc", feature_flags=owner), prefix="/api/v1")
    with TestClient(app) as client:
        _enable(client, MODEL_FLAG)
        for revision, version in enumerate(["1.0.0", "1.1.0"]):
            response = client.post("/api/v1/learninghub/releases",
                json=_release_body(version, revision, "promote-" + version, rollback="1.0.0"),
                headers=_headers("ml-owner"))
            assert response.status_code == 201
        _disable(client, MODEL_FLAG)
        rollback = client.post("/api/v1/learninghub/releases",
            json=_release_body("1.1.0", 2, "explicit-rollback", kind="ROLLBACK", rollback="1.0.0"),
            headers=_headers("ml-owner"))
        assert rollback.status_code == 201
        assert repository.get_alias("forecast_revenue_interval", ModelAlias.PRODUCTION).version == "1.0.0"
        assert owner.is_enabled(MODEL_FLAG) is False

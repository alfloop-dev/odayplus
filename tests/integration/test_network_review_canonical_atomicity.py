"""Real SQL failures cannot leave canonical decisions ahead of their receipt."""

import pytest
from fastapi import HTTPException

from apps.api.app.routes.operator_modules.live_service import DurableTenantServiceResolver
from modules.opsboard.application.network_reviews import (
    NetworkReviewConflict,
    NetworkReviewRoleError,
    NetworkReviewService,
)
from shared.audit.durable_delivery import DurableAuditDelivery
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.operator_domains import DurableOperatorDomainStateRepository
from tests.integration._operation_flag_fixtures import SITE_GO_FLAG, enable_operation_flag
from tests.unit.test_network_canonical_review_projection import report
from tests.unit.test_network_review_canonical_action_metadata import (
    TENANT,
    components,
    decision_args,
    install_real_related_producers,
)


def durable_bytes(engine):
    return {
        table: [tuple(dict(row).items()) for row in engine.query(f"SELECT * FROM {table} ORDER BY 1, 2")]
        for table in ("durable_documents", "durable_sequences", "durable_audit_events", "durable_jobs")
    }


def business_bytes(engine):
    result = durable_bytes(engine)
    # Every HTTP guard legitimately appends a security authorization audit.
    # Business replay must leave decision/audit/outbox/receipt bytes unchanged.
    result["durable_audit_events"] = [row for row in result["durable_audit_events"]
                                      if dict(row)["event_type"] != "security.authorization"]
    return result


@pytest.mark.parametrize("failure", ["save", "after_save"])
def test_failure_after_real_canonical_sql_writes_rolls_back_every_durable_row(tmp_path, failure):
    engine, scores, decisions, _, workflow, service = components(tmp_path / "atomic.sqlite", go_enabled=True)
    install_real_related_producers(engine, decisions, workflow, service)
    frozen = scores.save_report(report())
    decision = workflow.open_decision(frozen, created_by="offline-author", tenant_id=TENANT)
    repository = DurableOperatorDomainStateRepository(SqliteDocumentStore(engine), "network-reviews")
    repository.save(TENANT, service.export_state())
    original_save = repository.save

    def fail_save(tenant, state):
        original_save(tenant, state)
        raise RuntimeError("offline injected failure after aggregate SQL write")

    def fail_after_save(service, tenant):
        raise RuntimeError("offline injected failure after all SQL writes")

    resolver = DurableTenantServiceResolver(
        repository, factory=lambda state, tenant: service,
        exporter=lambda value: value.export_state(), mutating_methods={"decide_review"},
        after_save=fail_after_save if failure == "after_save" else None,
    )
    # Uses only the existing constructor; old source ignores this opt-in.
    # The baseline failure must be the persisted byte difference, not TypeError.
    resolver._transactional_mutations = True
    if failure == "save":
        repository.save = fail_save
    before = durable_bytes(engine)
    with pytest.raises(HTTPException) as error:
        resolver._invoke(TENANT, "decide_review", **decision_args(decision.decision_id, "GO"))
    assert error.value.status_code == 503
    assert durable_bytes(engine) == before
    engine.close()


@pytest.mark.parametrize("failure", ["missing-approval", "missing-candidate", "scores-engine", "scores-tenant", "candidate-metadata-tenant"])
def test_transaction_scope_refuses_incomplete_or_mismatched_canonical_participants(tmp_path, failure):
    from modules.opsboard.application.network_reviews import NetworkReviewRuntimeUnavailable
    from shared.infrastructure.persistence.engine import SqliteEngine
    from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
    from shared.infrastructure.persistence.repositories import DurableSiteScoreRepository
    engine, scores, decisions, _, workflow, service = components(tmp_path / "binding.sqlite")
    _, _, candidate_service = install_real_related_producers(engine, decisions, workflow, service)
    other = None
    if failure == "missing-approval":
        workflow._approval_store = None
    if failure == "missing-candidate":
        service._candidate_workflow_service = None
    if failure == "scores-engine":
        other = SqliteEngine(tmp_path / "other.sqlite")
        service._sitescore_repository = DurableSiteScoreRepository(TenantScopedDocumentStore(SqliteDocumentStore(other), TENANT))
    if failure == "scores-tenant":
        service._sitescore_repository = DurableSiteScoreRepository(TenantScopedDocumentStore(SqliteDocumentStore(engine), "offline-other-tenant"))
    if failure == "candidate-metadata-tenant":
        candidate_service._intakes._store = TenantScopedDocumentStore(SqliteDocumentStore(engine), "offline-other-tenant")
    before = durable_bytes(engine)
    with pytest.raises(NetworkReviewRuntimeUnavailable):
        service.transaction_scope(engine, TENANT)
    assert durable_bytes(engine) == before
    if other is not None:
        other.close()
    engine.close()


@pytest.mark.parametrize("failure", ["candidate", "approval", "audit", "receipt", "after_save"])
def test_five_record_and_worm_outbox_failure_is_one_real_sql_transaction(tmp_path, monkeypatch, failure):
    engine, scores, decisions, audit, workflow, service = components(tmp_path / "five.sqlite")
    approvals, intake, candidate_service = install_real_related_producers(engine, decisions, workflow, service)
    frozen = scores.save_report(report())
    decision = workflow.open_decision(frozen, created_by="offline-author", tenant_id=TENANT)
    sink_calls = []
    sink = type("OfflineExternalSink", (), {"sink_id": "offline-external", "write_audit_event": lambda self, event: sink_calls.append(event)})()
    audit._worm_sink = sink
    queue = DurableJobQueue(engine)
    repository = DurableOperatorDomainStateRepository(SqliteDocumentStore(engine), "network-reviews")
    repository.save(TENANT, service.export_state())

    def factory(state, tenant):
        return NetworkReviewService(initial_state=state, seed_fixtures=False, require_canonical=True,
                                    decision_repository=decisions, sitescore_repository=scores,
                                    decision_workflow=workflow, candidate_workflow_service=candidate_service,
                                    audit_delivery_factory=lambda scope: DurableAuditDelivery(engine, scope, queue, sink))

    def fail_after_real_write(original):
        def invoke(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("offline injected post-write failure")
        return invoke

    if failure == "candidate":
        monkeypatch.setattr(intake, "save_candidate_metadata", fail_after_real_write(intake.save_candidate_metadata))
    if failure == "approval":
        monkeypatch.setattr(approvals, "save", fail_after_real_write(approvals.save))
    if failure == "audit":
        monkeypatch.setattr(audit, "record", fail_after_real_write(audit.record))
    if failure == "receipt":
        monkeypatch.setattr(repository, "save", fail_after_real_write(repository.save))
    resolver = DurableTenantServiceResolver(repository, factory=factory, exporter=lambda value: value.export_state(),
                                           mutating_methods={"decide_review"}, transactional_mutations=True,
                                           after_save=(lambda service, tenant: (_ for _ in ()).throw(RuntimeError("after save"))) if failure == "after_save" else None)
    before = durable_bytes(engine)
    with pytest.raises((RuntimeError, HTTPException)):
        resolver._invoke(TENANT, "decide_review", **decision_args(decision.decision_id, "RETURN"))
    assert durable_bytes(engine) == before
    assert sink_calls == []
    engine.close()


def test_return_receipt_restart_replay_and_payload_conflict_have_no_duplicate_writes(tmp_path):
    engine, scores, decisions, _, workflow, service = components(tmp_path / "receipt.sqlite")
    install_real_related_producers(engine, decisions, workflow, service)
    frozen = scores.save_report(report())
    decision = workflow.open_decision(frozen, created_by="offline-author", tenant_id=TENANT)
    args = decision_args(decision.decision_id, "RETURN")
    first = service.decide_review(**args)
    restarted = NetworkReviewService(initial_state=service.export_state(), seed_fixtures=False, require_canonical=True,
                                     decision_repository=decisions, sitescore_repository=scores, decision_workflow=workflow)
    before = durable_bytes(engine)
    assert restarted.decide_review(**args)["records"] == first["records"]
    with pytest.raises(NetworkReviewConflict):
        restarted.decide_review(**{**args, "required_data": ["different data"]})
    with pytest.raises(NetworkReviewRoleError):
        restarted.decide_review(**{**args, "actor_role_id": "expansion_user"})
    assert durable_bytes(engine) == before
    engine.close()


@pytest.mark.parametrize("action", ["GO", "WAIT", "RETURN", "REJECT"])
def test_actual_operator_http_review_governance_and_candidate_sql_readback(tmp_path, action):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from shared.workflow.sitescore import SiteScoreDecisionWorkflow
    from tests.integration.test_operator_canonical_wiring import (
        BASE,
        CanonicalHarness,
        _headers,
        _seed_candidate,
    )

    harness = CanonicalHarness(tmp_path / "http.sqlite")
    if action == "GO":
        enable_operation_flag(harness.bundle.feature_flag_registry, key=SITE_GO_FLAG,
                              tenant_id="tenant-a", queue=harness.bundle.job_queue,
                              audit_log=harness.bundle.audit_log)
    candidate_id = _seed_candidate(harness, "tenant-a")
    frozen = harness.sitescore("tenant-a").save_report(replace(report(), candidate_site_id=candidate_id))
    decision = SiteScoreDecisionWorkflow(store=harness.decisions("tenant-a")).open_decision(frozen, created_by="offline-system")
    args = decision_args(decision.decision_id, action)
    payload = {"decision": action, "reason": args["reason"], "conditions": args["conditions"],
               "requiredData": args["required_data"], "overrideAck": True,
               "actorName": "forged-body-actor", "actorRoleId": "expansion_user"}
    headers = _headers("tenant-a", idempotency_key="offline-http-idempotency", subject="offline-http-reviewer")
    with TestClient(harness.app()) as client:
        decided = client.post(f"{BASE}/network-reviews/{decision.decision_id}/decide", headers=headers, json=payload)
        assert decided.status_code == 200, decided.text
        snapshot = client.get(f"{BASE}/network-reviews", headers=headers)
        assert snapshot.status_code == 200, snapshot.text
        row = snapshot.json()["reviews"][0]
        assert row["decision"]["decision"] == action
        assert row["decision"]["decidedBy"] == "offline-http-reviewer"
        approval_id = decided.json()["records"]["approvalId"]
        assert approval_id and approval_id != decision.decision_id
        assert snapshot.json()["approvals"][0]["id"] == approval_id
        governance = client.get(f"{BASE}/governance/snapshot", headers=headers)
        assert governance.status_code == 200, governance.text
        signature = next(item for item in governance.json()["approvals"] if item["id"] == approval_id)
        assert signature["networkAction"] == action
        assert signature["status"] == ("returned" if action == "RETURN" else "rejected" if action == "REJECT" else "approved")
        from modules.opsboard.application.network_listings import NetworkListingService
        from shared.infrastructure.persistence.operator_network_listings import (
            DurableAssistedIntakeRepository,
        )
        # This existing SQLite harness has no PostgreSQL Intake workflow store.
        # Verify the actual candidate read producer; full HTTP listing reads are
        # verified separately using an independently owned PostgreSQL database.
        candidate_service = NetworkListingService(
            listing_repository=harness.listing("tenant-a"),
            intake_repository=DurableAssistedIntakeRepository(harness.scoped("tenant-a")),
            review_decision_repository=harness.decisions("tenant-a"), tenant_id="tenant-a", seed_fixtures=False,
        )
        candidate = next(item for item in candidate_service.snapshot()["candidates"] if item["id"] == candidate_id)
        assert candidate["status"] == {"GO": "approved", "WAIT": "onhold", "RETURN": "needdata", "REJECT": "rejected"}[action]
        assert candidate["reviewWorkflow"]["approvalId"] == approval_id
        if action == "RETURN":
            assert candidate["missingData"] == args["required_data"]
        before = business_bytes(harness.bundle.engine)
        replay = client.post(f"{BASE}/network-reviews/{decision.decision_id}/decide", headers=headers, json=payload)
        assert replay.status_code == 200 and replay.json()["idempotentReplay"] is True
        assert business_bytes(harness.bundle.engine) == before
        denied = client.post(f"{BASE}/network-reviews/{decision.decision_id}/decide",
                             headers={**headers, "x-roles": "expansion_user"}, json=payload)
        assert denied.status_code == 403
        assert business_bytes(harness.bundle.engine) == before
        other = client.get(f"{BASE}/network-reviews", headers=_headers("tenant-b"))
        assert other.status_code == 200 and other.json()["reviews"] == []
    harness.close()

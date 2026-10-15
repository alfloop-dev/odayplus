"""Actual SQL review-owner publication, rollback, restart and stale-writer races."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace

import pytest
from fastapi import HTTPException

from modules.opsboard.application.network_reviews import (
    NetworkReviewConflict,
    NetworkReviewRoleError,
)
from shared.auth import Principal, Role, Scope
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.operator_network_listings import (
    DurableAssistedIntakeRepository,
)
from shared.infrastructure.persistence.repositories import (
    DurableApprovalRepository,
    DurableDecisionStore,
)
from tests.contract.test_operator_network_review_submission_api import seeded
from tests.integration._operation_flag_fixtures import SITE_GO_FLAG, enable_operation_flag
from tests.integration.test_network_review_canonical_atomicity import durable_bytes
from tests.unit.test_network_canonical_review_projection import report

TENANT = "tenant-a"
AUTHOR = Principal(
    subject_id="submit-author",
    roles=frozenset({Role.EXPANSION_USER, Role.SITE_REVIEWER}),
    scope=Scope(tenant_id=TENANT),
)
CHECKER = replace(AUTHOR, subject_id="actual-checker")


def invoke(resolver, name, **kwargs):
    return resolver._invoke(TENANT, name, **kwargs)


def arguments(context, **changes):
    return {
        "candidate_id": context["candidateId"],
        "principal": AUTHOR,
        "target": context["target"],
        "idempotency_key": "same-key",
        "correlation_id": "actual-correlation",
        "submission_summary": "Recorded saved report and evidence are submitted for review",
        "reviewer_group_id": context["reviewerGroups"][0]["id"],
        **changes,
    }


def setup(tmp_path):
    harness, candidate, saved = seeded(tmp_path)
    resolver = harness.bundle.network_review_resolver()
    context = invoke(resolver, "submission_context", candidate_id=candidate, principal=AUTHOR)
    return harness, resolver, candidate, saved, context


def test_initial_submit_real_sql_atomic_receipt_restart_same_key_and_payload_conflict(tmp_path):
    harness, resolver, candidate, saved, context = setup(tmp_path)
    path = harness.bundle.engine.path
    result = invoke(resolver, "submit_review", **arguments(context))
    own = harness.decisions(TENANT).get_decision(result["records"]["decisionId"])
    assert own.created_by == AUTHOR.subject_id and own.submitted_by == AUTHOR.subject_id
    assert own.submission_actor_verified is True and own.version == 2
    assert own.history[-1].submission_summary == arguments(context)["submission_summary"]
    assert own.history[-1].reviewer_group_id == arguments(context)["reviewer_group_id"]
    assert own.report_id == saved.report_id
    store = TenantScopedDocumentStore(SqliteDocumentStore(harness.bundle.engine), TENANT)
    approval = DurableApprovalRepository(store).get(own.approval_id)
    assert approval.approval_status == "pending" and approval.approval_id != own.decision_id
    assert harness.listing(TENANT).list_candidates()[0].review_id == own.decision_id
    before = durable_bytes(harness.bundle.engine)
    harness.close()
    restarted = _durable_bundle(path)
    try:
        replay = invoke(restarted.network_review_resolver(), "submit_review", **arguments(context))
        assert replay["idempotentReplay"] is True and replay["records"] == result["records"]
        assert durable_bytes(restarted.engine) == before
        with pytest.raises(NetworkReviewConflict):
            invoke(
                restarted.network_review_resolver(),
                "submit_review",
                **arguments(context, submission_summary="different summary"),
            )
        assert durable_bytes(restarted.engine) == before
    finally:
        restarted.engine.close()


@pytest.mark.parametrize(
    "failure",
    ["retained", "decision", "approval", "candidate", "metadata", "audit", "receipt", "after_save"],
)
def test_each_actual_write_fault_rolls_back_reports_approval_candidate_audit_outbox_receipt(
    tmp_path, monkeypatch, failure
):
    harness, resolver, _, _, context = setup(tmp_path)

    def injected(original):
        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("synthetic post-write fault")

        return fail

    if failure == "retained":
        monkeypatch.setattr(
            DurableDecisionStore,
            "retain_report_once",
            injected(DurableDecisionStore.retain_report_once),
        )
    elif failure == "decision":
        monkeypatch.setattr(
            DurableDecisionStore, "save_decision", injected(DurableDecisionStore.save_decision)
        )
    elif failure == "approval":
        monkeypatch.setattr(
            DurableApprovalRepository, "save", injected(DurableApprovalRepository.save)
        )
    elif failure == "candidate":
        from shared.infrastructure.persistence.repositories import DurableListingRepository

        monkeypatch.setattr(
            DurableListingRepository,
            "save_review_candidate",
            injected(DurableListingRepository.save_review_candidate),
        )
    elif failure == "metadata":
        monkeypatch.setattr(
            DurableAssistedIntakeRepository,
            "save_candidate_metadata",
            injected(DurableAssistedIntakeRepository.save_candidate_metadata),
        )
    elif failure == "audit":
        monkeypatch.setattr(
            harness.bundle.audit_log, "record", injected(harness.bundle.audit_log.record)
        )
    elif failure == "receipt":
        monkeypatch.setattr(resolver._repository, "save", injected(resolver._repository.save))
    else:
        resolver._after_save = lambda *args: (_ for _ in ()).throw(
            RuntimeError("after all durable writes")
        )
    before = durable_bytes(harness.bundle.engine)
    try:
        with pytest.raises((RuntimeError, HTTPException)):
            invoke(resolver, "submit_review", **arguments(context))
        assert durable_bytes(harness.bundle.engine) == before
        assert harness.runtime.calls == []
    finally:
        harness.close()


def test_return_resubmits_same_decision_frozen_v1_preserving_approval_and_latest_v2(tmp_path):
    harness, resolver, candidate, first, context = setup(tmp_path)
    initial = invoke(resolver, "submit_review", **arguments(context))
    decision_id = initial["records"]["decisionId"]
    returned = invoke(
        resolver,
        "decide_review",
        review_id=decision_id,
        decision="RETURN",
        reason="Please supply the original lease and photographs",
        required_data=["lease", "photos"],
        actor_role_id="site_reviewer",
        actor_name=CHECKER.subject_id,
        conditions=None,
        override_ack=True,
        idempotency_key="return-key",
        correlation_id="return-correlation",
        principal=CHECKER,
    )
    old_approval = returned["records"]["approvalId"]
    second = harness.sitescore(TENANT).save_report(
        replace(report(), candidate_site_id=candidate, model_version="later-synthetic-model")
    )
    next_context = invoke(resolver, "submission_context", candidate_id=candidate, principal=AUTHOR)
    assert next_context["target"]["expectedReportId"] == first.report_id
    assert next_context["target"]["expectedLatestReportId"] == second.report_id
    resubmitted = invoke(
        resolver, "submit_review", **arguments(next_context, idempotency_key="new-submit-round")
    )
    own = harness.decisions(TENANT).get_decision(decision_id)
    store = TenantScopedDocumentStore(SqliteDocumentStore(harness.bundle.engine), TENANT)
    assert own.decision_id == decision_id and own.report_id == first.report_id
    assert own.status.value == "PENDING_REVIEW" and own.version == 4
    assert resubmitted["records"]["approvalId"] != old_approval
    assert DurableApprovalRepository(store).get(old_approval).approval_status == "returned"
    assert own.history[-2].required_data == ("lease", "photos")
    assert harness.sitescore(TENANT).latest(candidate).report_id == second.report_id
    producer = resolver._factory(
        resolver._repository.load(TENANT), TENANT
    )._candidate_workflow_service
    row = producer.snapshot()["candidates"][0]
    assert row["reviewSubmission"]["decisionVersion"] == 4
    assert row["reviewSubmission"]["reportId"] == first.report_id
    assert harness.runtime.calls == []
    harness.close()


@pytest.mark.parametrize("change", ["subject", "tenant", "role", "scope"])
def test_current_authority_and_object_scope_are_rechecked_before_receipt_replay(tmp_path, change):
    harness, resolver, _, _, context = setup(tmp_path)
    invoke(resolver, "submit_review", **arguments(context))
    principal = {
        "subject": replace(AUTHOR, subject_id="foreign-sender"),
        "tenant": replace(AUTHOR, scope=Scope(tenant_id="tenant-b")),
        "role": replace(AUTHOR, roles=frozenset({Role.OPERATOR_VIEWER})),
        "scope": replace(
            AUTHOR, scope=Scope(tenant_id=TENANT, assigned_area_ids=frozenset({"elsewhere"}))
        ),
    }[change]
    before = durable_bytes(harness.bundle.engine)
    try:
        with pytest.raises((NetworkReviewConflict, NetworkReviewRoleError)):
            invoke(resolver, "submit_review", **arguments(context, principal=principal))
        assert durable_bytes(harness.bundle.engine) == before
    finally:
        harness.close()


def test_true_verified_proposer_cannot_approve_but_separate_current_checker_can(tmp_path):
    harness, resolver, _, _, context = setup(tmp_path)
    enable_operation_flag(harness.bundle.feature_flag_registry, key=SITE_GO_FLAG,
                          tenant_id=TENANT, queue=harness.bundle.job_queue,
                          audit_log=harness.bundle.audit_log)
    result = invoke(resolver, "submit_review", **arguments(context))
    args = dict(
        review_id=result["records"]["decisionId"],
        decision="GO",
        reason="Review all retained inputs and approve recorded evidence",
        actor_role_id="site_reviewer",
        actor_name=AUTHOR.subject_id,
        conditions=None,
        required_data=[],
        override_ack=True,
        idempotency_key="approval-key",
        correlation_id="approval-correlation",
        principal=AUTHOR,
    )
    before = durable_bytes(harness.bundle.engine)
    with pytest.raises(NetworkReviewRoleError):
        invoke(resolver, "decide_review", **args)
    assert durable_bytes(harness.bundle.engine) == before
    outcome = invoke(
        resolver,
        "decide_review",
        **{**args, "principal": CHECKER, "actor_name": CHECKER.subject_id},
    )
    assert outcome["review"]["decision_status"] == "APPROVED"
    assert outcome["review"]["submitted_by"] == AUTHOR.subject_id
    harness.close()


def test_stale_candidate_and_metadata_writers_do_not_erase_pending_or_human_owned_fields(tmp_path):
    harness, resolver, candidate, _, context = setup(tmp_path)
    enable_operation_flag(harness.bundle.feature_flag_registry, key=SITE_GO_FLAG,
                          tenant_id=TENANT, queue=harness.bundle.job_queue,
                          audit_log=harness.bundle.audit_log)
    old = deepcopy(harness.listing(TENANT).list_candidates()[0])
    result = invoke(resolver, "submit_review", **arguments(context))
    repository = harness.listing(TENANT)
    repository.save_candidate(old)
    metadata = DurableAssistedIntakeRepository(harness.scoped(TENANT))
    metadata.save_candidate_metadata(candidate, {"title": "stale presentation row"})
    assert repository.list_candidates()[0].review_id == result["records"]["decisionId"]
    assert (
        metadata.get_candidate_metadata(candidate)["reviewSubmission"]["decisionId"]
        == result["records"]["decisionId"]
    )
    invoke(
        resolver,
        "decide_review",
        review_id=result["records"]["decisionId"],
        decision="GO",
        reason="Separate checker approves all retained evidence and scope",
        actor_role_id="site_reviewer",
        actor_name=CHECKER.subject_id,
        principal=CHECKER,
        conditions=None,
        required_data=[],
        override_ack=True,
        idempotency_key="go-key",
        correlation_id="go",
    )
    repository.save_candidate(old)
    metadata.save_candidate_metadata(candidate, {"title": "another stale row"})
    assert repository.list_candidates()[0].candidate_site.site_status == "approved"
    assert metadata.get_candidate_metadata(candidate)["reviewWorkflow"]["networkAction"] == "GO"
    harness.close()


def test_two_real_sqlite_connections_different_keys_publish_only_one_pending_relation(tmp_path):
    harness, resolver, _, _, context = setup(tmp_path)
    other = _durable_bundle(harness.bundle.engine.path)

    def submit(pair):
        own, key = pair
        try:
            return invoke(own, "submit_review", **arguments(context, idempotency_key=key))
        except NetworkReviewConflict:
            return None

    try:
        with ThreadPoolExecutor(2) as pool:
            results = list(
                pool.map(submit, [(resolver, "first"), (other.network_review_resolver(), "second")])
            )
        assert sum(item is not None for item in results) == 1
        assert len(harness.decisions(TENANT).list_decisions()) == 1
        assert harness.runtime.calls == []
    finally:
        other.engine.close()
        harness.close()


def test_actual_decision_version_cas_and_immutable_retained_insert(tmp_path):
    harness, resolver, _, saved, context = setup(tmp_path)
    receipt = invoke(resolver, "submit_review", **arguments(context))
    repository = harness.decisions(TENANT)
    decision = repository.get_decision(receipt["records"]["decisionId"])
    before = durable_bytes(harness.bundle.engine)
    with pytest.raises(ValueError, match="VERSION_CONFLICT"):
        repository.save_decision_version(replace(decision, version=3), expected_version=1)
    with pytest.raises(ValueError, match="IMMUTABLE"):
        repository.retain_report_once(decision.decision_id, replace(saved, model_version="another"))
    assert durable_bytes(harness.bundle.engine) == before
    harness.close()


@pytest.fixture
def submit_author_pg(monkeypatch):
    """Create only a new authorized scratch database, never open the source DB."""
    import os
    import shlex
    from pathlib import Path
    from uuid import uuid4

    import psycopg
    from psycopg import sql

    from tests.conftest import IntakeDatabase, IntakePgServer
    from tests.intake_registry_fixture import TENANT as PG_TENANT
    from tests.integration.test_intake_registry_calendar_postgresql import (
        _bundle,
        _seed_identities,
        _upgrade,
    )
    from tests.integration.test_operator_canonical_wiring import CanonicalHarness, _seed_candidate

    path = os.environ.get("ODP_SUBMIT_AUTHOR_ADMIN_DSN_FILE")
    if path is None:
        pytest.skip("Own local PostgreSQL scratch authority not requested")
    raw = Path(path).read_text().strip()
    if raw.startswith("{"):
        import json

        values = json.loads(raw)
        dsn = values.get("dsn") or values.get("database_url") or values.get("ODAY_DATABASE_URL")
    elif raw.startswith(("postgresql://", "postgres://", "dbname=")):
        dsn = raw
    else:
        candidates = [
            line.split("=", 1)[1]
            for line in raw.splitlines()
            if line.lstrip().startswith(
                ("ODAY_DATABASE_URL=", "DATABASE_URL=", "export ODAY_DATABASE_URL=")
            )
        ]
        if len(candidates) != 1:
            raise RuntimeError("Private local admin DSN format unavailable")
        dsn = shlex.split(candidates[0])[0]
    assert isinstance(dsn, str)
    admin = psycopg.conninfo.conninfo_to_dict(dsn)
    if not (
        str(admin.get("host", "localhost")) in {"localhost", "127.0.0.1", "::1"}
        or str(admin.get("host", "")).startswith("/tmp/")
    ):
        raise RuntimeError("Only explicitly owned localhost scratch authority is permitted")
    # The DSN is used solely for localhost admin parameters. Its database is
    # deliberately discarded; neither reset nor connection to the original DB.
    admin["dbname"] = "postgres"
    name = "odp_submit_author_" + uuid4().hex[:16]
    server = IntakePgServer(psycopg=psycopg, admin_params=admin)
    with server.connect("postgres") as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    database = IntakeDatabase(server=server, dbname=name)
    bundle = None
    try:
        _upgrade(database, "head")
        from shared.infrastructure.persistence.assisted_listing_intake import (
            apply_upgrade_to_database,
        )

        apply_upgrade_to_database(database.url())
        with database.connect() as connection:
            _seed_identities(connection)
        bundle = _bundle(database, monkeypatch)
        harness = CanonicalHarness(None, bundle=bundle)
        candidate = _seed_candidate(harness, PG_TENANT)
        saved = harness.sitescore(PG_TENANT).save_report(
            replace(report(), candidate_site_id=candidate)
        )
        yield database, bundle, harness, PG_TENANT, candidate, saved
    finally:
        if bundle is not None:
            bundle.engine.close()
        # Drop only the exact new owned prefix; do not terminate/reset any
        # other database's clients or use fixture's generic database allocator.
        assert name.startswith("odp_submit_author_")
        with server.connect("postgres") as connection:
            connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()",
                (name,),
            )
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def pg_principal(tenant, subject="00000000-0000-0000-0000-000000000002"):
    return replace(AUTHOR, subject_id=subject, scope=Scope(tenant_id=tenant))


def pg_invoke(owner, tenant, method, **kwargs):
    return owner._invoke(tenant, method, **kwargs)


def pg_args(context, principal, **changes):
    return {**arguments(context), "principal": principal, **changes}


@pytest.mark.requires_live_env
def test_own_pg_two_engines_different_keys_single_pending_approval_and_report_binding(
    submit_author_pg, monkeypatch
):
    from tests.integration.test_intake_registry_calendar_postgresql import _bundle

    database, bundle, harness, tenant, candidate, saved = submit_author_pg
    principal = pg_principal(tenant)
    one = bundle.network_review_resolver()
    other_bundle = _bundle(database, monkeypatch)
    two = other_bundle.network_review_resolver()
    context = pg_invoke(
        one, tenant, "submission_context", candidate_id=candidate, principal=principal
    )

    def run(pair):
        owner, key = pair
        try:
            return pg_invoke(
                owner, tenant, "submit_review", **pg_args(context, principal, idempotency_key=key)
            )
        except NetworkReviewConflict:
            return None

    try:
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(run, [(one, "pg-key-a"), (two, "pg-key-b")]))
        assert sum(value is not None for value in results) == 1
        decisions = harness.decisions(tenant).list_decisions()
        assert len(decisions) == 1 and decisions[0].report_id == saved.report_id
        assert len(harness.scoped(tenant).list_all("workflow.approvals")) == 1
        assert harness.runtime.calls == []
    finally:
        other_bundle.engine.close()


@pytest.mark.requires_live_env
@pytest.mark.parametrize("failure", ["metadata", "receipt"])
def test_own_pg_real_afterwrite_fault_rolls_back_all_publication_and_outbox(
    submit_author_pg, monkeypatch, failure
):
    _, bundle, _, tenant, candidate, _ = submit_author_pg
    principal = pg_principal(tenant)
    owner = bundle.network_review_resolver()
    context = pg_invoke(
        owner, tenant, "submission_context", candidate_id=candidate, principal=principal
    )
    original = (
        DurableAssistedIntakeRepository.save_candidate_metadata
        if failure == "metadata"
        else owner._repository.save
    )
    reached = []

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        reached.append(True)
        raise RuntimeError("actual PG afterwrite fault")

    if failure == "metadata":
        monkeypatch.setattr(DurableAssistedIntakeRepository, "save_candidate_metadata", fail)
    else:
        monkeypatch.setattr(owner._repository, "save", fail)
    before = durable_bytes(bundle.engine)
    with pytest.raises((RuntimeError, HTTPException)):
        pg_invoke(owner, tenant, "submit_review", **pg_args(context, principal))
    assert reached == [True]
    assert durable_bytes(bundle.engine) == before


@pytest.mark.requires_live_env
def test_own_pg_new_report_publication_invalidates_initial_context_without_a_review_write(
    submit_author_pg,
):
    _, bundle, harness, tenant, candidate, _ = submit_author_pg
    principal = pg_principal(tenant)
    owner = bundle.network_review_resolver()
    context = pg_invoke(
        owner, tenant, "submission_context", candidate_id=candidate, principal=principal
    )
    latest = harness.sitescore(tenant).save_report(
        replace(report(), candidate_site_id=candidate, model_version="new-recorded-model")
    )
    before = durable_bytes(bundle.engine)
    with pytest.raises(NetworkReviewConflict, match="SUBMISSION_CONTEXT_CONFLICT"):
        pg_invoke(owner, tenant, "submit_review", **pg_args(context, principal))
    assert durable_bytes(bundle.engine) == before
    assert harness.sitescore(tenant).latest(candidate).report_id == latest.report_id


@pytest.mark.requires_live_env
@pytest.mark.parametrize("surface", ["operator", "native"])
def test_own_pg_signed_sql_accounts_sessions_proposer_selfdeny_bodyspoof_checker_positive(
    submit_author_pg, monkeypatch, surface
):
    """JWT crypto + actual SQL identities/session; not an external IdP/password login proof."""
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from fastapi.testclient import TestClient

    from apps.api.app.routes.sitescore import create_sitescore_router
    from apps.api.oday_api.security import dependencies
    from modules.opsboard.auth import (
        AuthBoundaryConfig,
        AuthenticationBoundary,
        SigningKey,
        encode_compact_jwt,
    )
    from tests.intake_registry_fixture import MANAGER

    database, bundle, harness, tenant, candidate, saved = submit_author_pg
    enable_operation_flag(bundle.feature_flag_registry, key=SITE_GO_FLAG, tenant_id=tenant,
                          queue=bundle.job_queue, audit_log=bundle.audit_log)
    checker = "00000000-0000-0000-0000-000000000299"
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO identity.accounts(account_id,tenant_id,username,email,status,created_by) VALUES(%s,%s,%s,%s,'active','synthetic-submit-test')",
            (checker, tenant, checker, checker + "@example.invalid"),
        )
        connection.execute(
            "INSERT INTO identity.account_roles(account_id,role,granted_by) VALUES(%s,'site_reviewer','synthetic-submit-test')",
            (checker,),
        )
        connection.execute("INSERT INTO identity.account_scopes(account_id) VALUES(%s)", (checker,))
    signing_key = SigningKey(
        kid="synthetic-submit-local",
        algorithm="HS256",
        secret=b"explicit-isolated-local-submit-signing-key",
    )
    config = AuthBoundaryConfig(
        local_issuer="urn:odp:identity:local",
        local_audiences=frozenset({"isolated-submit"}),
        local_signing_keys={signing_key.kid: signing_key},
        identity_store=bundle.identity_store,
        session_service=bundle.session_service,
    )
    boundary = AuthenticationBoundary(config, audit_log=bundle.audit_log)
    monkeypatch.setattr(dependencies, "_default_boundary", boundary)

    def headers(subject):
        session = bundle.session_service.create_session(
            account_id=UUID(subject), provider="local_password"
        )
        now = datetime.now(UTC)
        token = encode_compact_jwt(
            {
                "sub": subject,
                "sid": str(session.session_id),
                "tenant_id": tenant,
                "iss": "urn:odp:identity:local",
                "aud": "isolated-submit",
                "iat": now.timestamp(),
                "exp": (now + timedelta(minutes=20)).timestamp(),
            },
            signing_key,
        )
        return {
            "Authorization": "Bearer " + token,
            "If-Actor-Match": subject,
            "Idempotency-Key": "signed-submit-key",
            "X-Correlation-Id": "signed-submit-correlation",
        }

    proposer_headers, checker_headers = headers(MANAGER), headers(checker)
    app = harness.app()
    app.include_router(
        create_sitescore_router(
            repository=harness.sitescore(tenant),
            audit_log=bundle.audit_log,
            sitescore_decision_repository_for_tenant=harness.decisions,
            review_service_resolver=bundle.network_review_resolver(),
            runtime_mode="production",
            require_production_model=True,
        ),
        prefix="/api/v1",
    )

    def business():
        result = durable_bytes(bundle.engine)
        result["durable_audit_events"] = [
            row
            for row in result["durable_audit_events"]
            if not str(dict(row)["event_type"]).startswith("security.")
        ]
        return result

    with TestClient(app) as client:
        context = client.get(
            f"/api/v1/operator/network-reviews/candidates/{candidate}/submission-context",
            headers=proposer_headers,
        )
        assert context.status_code == 200, context.text
        body = {
            **context.json()["target"],
            "expectedActorSubjectId": MANAGER,
            "expectedTenantId": tenant,
            "submissionSummary": "Actual saved report submitted by signed scoped SQL account",
            "reviewerGroupId": context.json()["reviewerGroups"][0]["id"],
        }
        if surface == "operator":
            posted = client.post(
                f"/api/v1/operator/network-reviews/candidates/{candidate}/submit",
                json=body,
                headers=proposer_headers,
            )
            assert posted.status_code == 200, posted.text
            decision_id = posted.json()["records"]["decisionId"]
        else:
            posted = client.post(
                "/api/v1/sitescore/decisions",
                headers=proposer_headers,
                json={
                    "report_id": saved.report_id,
                    "created_by": checker,
                    "submission_summary": body["submissionSummary"],
                    "reviewer_group_id": body["reviewerGroupId"],
                    "expected_actor_subject_id": MANAGER,
                    "expected_tenant_id": tenant,
                },
            )
            assert posted.status_code == 201, posted.text
            decision_id = posted.json()["decision_id"]
            assert posted.json()["created_by"] == MANAGER  # forged legacy body is not authority
            before_replay = business()
            replay = client.post(
                "/api/v1/sitescore/decisions",
                headers=proposer_headers,
                json={
                    "report_id": saved.report_id,
                    "created_by": checker,
                    "submission_summary": body["submissionSummary"],
                    "reviewer_group_id": body["reviewerGroupId"],
                    "expected_actor_subject_id": MANAGER,
                    "expected_tenant_id": tenant,
                },
            )
            assert (
                replay.status_code == 201
                and replay.json()["submission_receipt"]["idempotentReplay"] is True
            )
            assert replay.json()["decision_id"] == decision_id
            assert business() == before_replay
        before = business()
        if surface == "operator":
            path = f"/api/v1/operator/network-reviews/{decision_id}/decide"
            payload = {
                "decision": "GO",
                "reason": "Separate reviewer examines retained report and evidence",
                "overrideAck": True,
                "actorName": checker,
                "actorRoleId": "executive",
            }
        else:
            path = f"/api/v1/sitescore/decisions/{decision_id}/decision"
            payload = {
                "action": "APPROVE",
                "reason": "Separate reviewer examines retained report and evidence",
                "actor": checker,
                "expected_version": 2,
                "expected_actor_subject_id": MANAGER,
                "expected_tenant_id": tenant,
            }
        denied = client.post(path, headers=proposer_headers, json=payload)
        assert denied.status_code == 403, denied.text
        assert business() == before
        payload = (
            {**payload, "expected_actor_subject_id": checker}
            if surface == "native"
            else {**payload, "actorName": MANAGER}
        )
        accepted = client.post(path, headers=checker_headers, json=payload)
        assert accepted.status_code == 200, accepted.text
        actual = harness.decisions(tenant).get_decision(decision_id)
        assert actual.status.value == "APPROVED" and actual.created_by == MANAGER
        assert actual.submitted_by == MANAGER and actual.history[-1].actor == checker
        assert actual.report_id == saved.report_id
        assert harness.runtime.calls == []
        assert (
            bundle.engine.query_one(
                "SELECT count(*) AS n FROM durable_audit_events WHERE event_type='security.authentication'"
            )["n"]
            > 0
        )
        assert (
            bundle.engine.query_one(
                "SELECT count(*) AS n FROM durable_audit_events WHERE event_type='security.authorization' AND outcome='deny'"
            )["n"]
            > 0
        )


@pytest.mark.parametrize("draft", [False, True])
def test_existing_eligible_system_or_native_draft_reuses_own_decision_without_trusting_legacy_creator(
    tmp_path, draft
):
    from shared.workflow.sitescore import DecisionAction, SiteScoreDecisionWorkflow

    harness, resolver, candidate, saved, _ = setup(tmp_path)
    store = harness.scoped(TENANT)
    workflow = SiteScoreDecisionWorkflow(
        store=harness.decisions(TENANT),
        audit_log=harness.bundle.audit_log,
        approval_store=DurableApprovalRepository(store),
    )
    old = workflow.open_decision(saved, created_by="unverified-legacy-body-name")
    previous_approval = None
    if draft:
        pending = workflow.submit_for_review(
            old.decision_id, submitted_by="unverified-old-submit-name"
        )
        previous_approval = pending.approval_id
        workflow.decide(
            old.decision_id, action=DecisionAction.REQUEST_REVISION, actor="unverified-old-checker"
        )
    context = invoke(resolver, "submission_context", candidate_id=candidate, principal=AUTHOR)
    assert context["target"]["decisionId"] == old.decision_id
    receipt = invoke(resolver, "submit_review", **arguments(context))
    current = harness.decisions(TENANT).get_decision(old.decision_id)
    assert (
        current.created_by == "unverified-legacy-body-name" and current.created_by_verified is False
    )
    assert current.submitted_by == AUTHOR.subject_id and current.submission_actor_verified is True
    assert current.decision_id == old.decision_id and current.report_id == saved.report_id
    assert len(harness.decisions(TENANT).list_decisions()) == 1
    assert receipt["commandReceipt"]["originalCreatorVerified"] is False
    if previous_approval:
        assert DurableApprovalRepository(store).get(previous_approval).approval_status == "returned"
        assert previous_approval != current.approval_id
    harness.close()


def test_actual_legacy_version_unknown_remains_unverified_no_default_one(tmp_path):
    from shared.workflow.sitescore import SiteScoreDecisionWorkflow

    harness, resolver, candidate, saved, _ = setup(tmp_path)
    repository = harness.decisions(TENANT)
    old = SiteScoreDecisionWorkflow(
        store=repository, audit_log=harness.bundle.audit_log
    ).open_decision(saved, created_by="legacy-record")
    repository.save_decision(
        replace(old, version=0)
    )  # explicit historical fixture, not production backfill
    before = durable_bytes(harness.bundle.engine)
    with pytest.raises(NetworkReviewConflict, match="ACTUAL_DECISION_VERSION_REQUIRED"):
        invoke(resolver, "submission_context", candidate_id=candidate, principal=AUTHOR)
    assert repository.get_decision(old.decision_id).version == 0
    assert durable_bytes(harness.bundle.engine) == before
    harness.close()


def test_approved_v1_newer_v2_opens_separate_round_preserving_original_sql_graph(tmp_path):
    from shared.infrastructure.persistence.repositories import DurableRealizedSiteStore
    from shared.workflow.sitescore import CandidateSiteRealizationHook, DecisionStatus

    harness, _, candidate, first, context = setup(tmp_path)
    enable_operation_flag(harness.bundle.feature_flag_registry, key=SITE_GO_FLAG,
                          tenant_id=TENANT, queue=harness.bundle.job_queue,
                          audit_log=harness.bundle.audit_log)
    decisions = harness.decisions(TENANT)
    approvals = DurableApprovalRepository(harness.scoped(TENANT))
    realized = DurableRealizedSiteStore(harness.scoped(TENANT))
    # Explicit same-engine real producer composition, not a fabricated row or
    # a claim that the production realization callback is already wired.
    resolver = harness.bundle.network_review_resolver(
        hooks=(CandidateSiteRealizationHook(realized),)
    )
    receipt = invoke(resolver, "submit_review", **arguments(context))
    original_id = receipt["records"]["decisionId"]
    invoke(
        resolver,
        "decide_review",
        review_id=original_id,
        decision="GO",
        reason="Separate actor verifies the retained human approval inputs",
        actor_role_id="site_reviewer",
        actor_name=CHECKER.subject_id,
        principal=CHECKER,
        conditions=None,
        required_data=[],
        override_ack=True,
        idempotency_key="terminal-key",
        correlation_id="terminal-correlation",
    )
    original = decisions.get_decision(original_id)
    assert original.status is DecisionStatus.APPROVED
    assert approvals.get(original.approval_id).approval_status == "approved"
    assert realized.get(candidate).decision_id == original_id

    def original_graph():
        physical = deepcopy(harness.listing(TENANT).list_candidates()[0])
        physical.review_id = None
        return deepcopy({
            "decision": decisions.get_decision(original_id),
            "approval": approvals.get(original.approval_id),
            "retainedV1": decisions.get_report(original_id),
            "candidateExceptCurrentReviewPointer": physical,
            "realizedSite": realized.get(candidate),
        })

    preserved = original_graph()
    # The same saved report still cannot invent a second terminal review round.
    before_same_report = durable_bytes(harness.bundle.engine)
    with pytest.raises(NetworkReviewConflict, match="^NEWER_SAVED_REPORT_REQUIRED$"):
        invoke(resolver, "submission_context", candidate_id=candidate, principal=AUTHOR)
    assert durable_bytes(harness.bundle.engine) == before_same_report

    second = harness.sitescore(TENANT).save_report(
        replace(report(), candidate_site_id=candidate, model_version="later-report-model")
    )
    assert second.report_id != first.report_id and second.report_version == 2
    before = durable_bytes(harness.bundle.engine)
    next_context = invoke(
        resolver, "submission_context", candidate_id=candidate, principal=AUTHOR
    )
    assert durable_bytes(harness.bundle.engine) == before
    assert original_graph() == preserved
    assert next_context["source"] == "canonical" and next_context["canSubmit"] is True
    assert next_context["pendingReview"] is None
    assert next_context["actorSubjectId"] == AUTHOR.subject_id
    assert next_context["tenantId"] == TENANT
    assert next_context["previousReview"] == {
        "decisionId": original_id, "decisionVersion": original.version,
        "approvalId": original.approval_id, "approvalStatus": "approved",
        "reportId": first.report_id, "reportVersion": first.report_version,
        "modelVersion": first.model_version, "tenantId": TENANT,
    }
    assert next_context["target"]["decisionId"] is None
    assert next_context["target"]["expectedDecisionVersion"] is None
    assert next_context["target"]["expectedCandidateReviewId"] == original_id
    assert next_context["target"]["expectedCandidateState"] == "approved"
    assert next_context["target"]["expectedReportId"] == second.report_id
    assert next_context["target"]["expectedReportVersion"] == second.report_version

    next_arguments = arguments(next_context, idempotency_key="new-report-round-key")
    new_receipt = invoke(resolver, "submit_review", **next_arguments)
    new_id = new_receipt["records"]["decisionId"]
    new = decisions.get_decision(new_id)
    assert new_id != original_id and new.approval_id != original.approval_id
    assert new.previous_decision_id == original_id
    assert new.status is DecisionStatus.PENDING_REVIEW and new.version == 2
    assert new.created_by == AUTHOR.subject_id and new.submitted_by == AUTHOR.subject_id
    assert new.created_by_verified is True and new.submission_actor_verified is True
    assert new.tenant_id == TENANT and new.candidate_site_id == candidate
    assert new.report_id == second.report_id and new.report_version == second.report_version
    assert new.model_version == second.model_version
    assert decisions.get_report(new_id) == second
    assert decisions.get_report(original_id) == first
    new_approval = approvals.get(new.approval_id)
    assert new_approval.decision_id == new_id
    assert new_approval.approval_status == "pending"
    assert new_receipt["commandReceipt"]["previousDecisionId"] == original_id
    assert new_receipt["commandReceipt"]["candidateState"] == "approved"
    physical = harness.listing(TENANT).list_candidates()[0]
    assert physical.review_id == new_id and physical.candidate_site.site_status == "approved"
    assert len(decisions.list_decisions()) == 2
    assert original_graph() == preserved

    before_replay = durable_bytes(harness.bundle.engine)
    replay = invoke(resolver, "submit_review", **next_arguments)
    assert replay["idempotentReplay"] is True and replay["records"] == new_receipt["records"]
    assert durable_bytes(harness.bundle.engine) == before_replay
    assert original_graph() == preserved and harness.runtime.calls == []
    harness.close()


def test_actual_duplicate_pending_is_conflict_and_not_first_row_context(tmp_path):
    from shared.workflow.sitescore import SiteScoreDecisionWorkflow

    harness, resolver, candidate, saved, context = setup(tmp_path)
    invoke(resolver, "submit_review", **arguments(context))
    workflow = SiteScoreDecisionWorkflow(
        store=harness.decisions(TENANT),
        audit_log=harness.bundle.audit_log,
        approval_store=DurableApprovalRepository(harness.scoped(TENANT)),
    )
    duplicate = workflow.open_decision(saved, created_by="explicit-corrupt-import-fixture")
    workflow.submit_for_review(
        duplicate.decision_id, submitted_by="explicit-corrupt-import-fixture"
    )
    before = durable_bytes(harness.bundle.engine)
    with pytest.raises(NetworkReviewConflict, match="AMBIGUOUS_PENDING_DECISION"):
        invoke(resolver, "submission_context", candidate_id=candidate, principal=AUTHOR)
    assert durable_bytes(harness.bundle.engine) == before
    harness.close()


@pytest.mark.parametrize("conflict", ["site-address", "draft-address", "tenant"])
def test_actual_current_listing_address_or_known_tenant_contradiction_cannot_authorize_submit_context(
    tmp_path, conflict
):
    harness, resolver, candidate, _, _ = setup(tmp_path)
    current = deepcopy(harness.listing(TENANT).list_candidates()[0])
    if conflict == "site-address":
        current.candidate_site = replace(current.candidate_site, address_id="foreign-address")
    elif conflict == "draft-address":
        current.address = replace(current.address, address_id="foreign-address")
    else:
        object.__setattr__(current.listing, "tenant_id", "different-known-tenant")
    # Explicit owned raw historical/storage corruption, no production updater.
    repository = harness.listing(TENANT)
    repository._store.put(repository._CANDIDATES, candidate, current)
    before = durable_bytes(harness.bundle.engine)
    with pytest.raises(NetworkReviewConflict, match="RELATIONSHIP"):
        invoke(resolver, "submission_context", candidate_id=candidate, principal=AUTHOR)
    assert durable_bytes(harness.bundle.engine) == before
    assert harness.decisions(TENANT).list_decisions() == []
    harness.close()


def test_existing_confidential_classification_is_enforced_by_owner_not_only_operator_dependency(
    tmp_path,
):
    from shared.auth import DataClassification

    harness, resolver, candidate, _, _ = setup(tmp_path)
    actor = replace(AUTHOR, scope=Scope(tenant_id=TENANT, clearance=DataClassification.PUBLIC))
    before = durable_bytes(harness.bundle.engine)
    with pytest.raises(NetworkReviewRoleError):
        invoke(resolver, "submission_context", candidate_id=candidate, principal=actor)
    assert durable_bytes(harness.bundle.engine) == before
    harness.close()

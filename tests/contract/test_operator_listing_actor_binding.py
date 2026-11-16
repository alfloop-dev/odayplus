"""Signed-token attribution and actor-bound replay at the real HTTP guard."""

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules.network_listings import create_network_listings_sub_router
from apps.api.oday_api.security.dependencies import (
    require_operator_permission,
    reset_bound_persistence,
)
from modules.opsboard.application.network_listings import (
    InMemoryAssistedIntakeRepository,
    IntakeIdempotencyRecord,
    NetworkListingConflict,
    NetworkListingPolicyError,
    NetworkListingService,
)
from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    SigningKey,
    encode_compact_jwt,
)
from shared.auth import Action, Role, Scope

TENANT = "00000000-0000-0000-0000-000000000001"
OWNER = "00000000-0000-0000-0000-000000000101"
OTHER = "00000000-0000-0000-0000-000000000102"
MANAGER = "00000000-0000-0000-0000-000000000103"
KEY = SigningKey(kid="synthetic-operator", algorithm="HS256", secret=b"isolated-test-key")
BASE = "/network-listings"


def _headers(subject=OWNER, *, key=None):
    now = datetime.now(UTC)
    token = encode_compact_jwt({
        "sub":subject, "iss":"https://operator-idp.example.invalid", "aud":"operator-test",
        "iat":now.timestamp(), "exp":(now+timedelta(hours=1)).timestamp(),
        "roles":["platform_admin", "site_reviewer"],
    }, KEY)
    return {"Authorization":"Bearer "+token,"X-Subject-Id":OTHER,"X-Roles":"site_reviewer",
            "Idempotency-Key":key or "synthetic-command-"+str(uuid4()),"X-Correlation-Id":"synthetic-correlation"}


@pytest.fixture
def api():
    reset_bound_persistence()
    repository = InMemoryAssistedIntakeRepository()
    service = NetworkListingService(intake_repository=repository, tenant_id=TENANT)
    boundary = AuthenticationBoundary(AuthBoundaryConfig(
        issuer="https://operator-idp.example.invalid", audiences=frozenset({"operator-test"}),
        signing_keys={KEY.kid:KEY}, principal_mappings={
            subject:{"roles":roles,"scope":{"tenant_id":TENANT}}
            for subject,roles in [(OWNER,["expansion_user"]),(OTHER,["expansion_user"]),
                                  (MANAGER,["site_reviewer","expansion_user"])]
        },
    ))
    app = FastAPI()
    app.include_router(create_network_listings_sub_router(
        service, require_view_permission_fn=require_operator_permission("listing",Action.VIEW,boundary=boundary),
        require_write_permission_fn=require_operator_permission("listing",Action.UPDATE,boundary=boundary),
    ))
    with TestClient(app) as client:
        yield client,service,repository
    reset_bound_persistence()


def _submit(api, subject=OWNER, *, key=None, suffix=None, actor_name=OTHER):
    client,_,_ = api
    return client.post(BASE+"/intake/submit",json={
        "url":"https://www.synthetic.example/detail-"+(suffix or str(uuid4()))+".html",
        "heatZoneId":"HZ-01","actorName":actor_name,"actorRoleId":"expansionManager",
    },headers=_headers(subject,key=key))


def _seed_submission(repository, *, actor=OWNER, key, url, heat_zone_id="HZ-01"):
    """Install an explicit historical row and its original actor-bound receipt.

    This is preparation of retained legacy data, not a call to either HTTP
    fresh writer. Keep the original submit fingerprint and receipt namespace.
    """
    record = {
        "id": str(uuid4()), "tenantId": TENANT,
        "owner": actor, "submitter": actor, "submittedBy": actor,
        "originalUrl": url, "canonicalUrl": url, "heatZoneId": heat_zone_id,
        "stage": "FAILED", "intakeMethod": "URL", "version": 1,
        "parsedFields": {}, "matchResult": None,
        "auditEvents": [{"action": "intake.create", "actorName": actor,
                         "actorRoleId": Role.EXPANSION_USER.value}],
    }
    fingerprint = hashlib.sha256(json.dumps(
        {"url": url.strip(), "heat_zone_id": heat_zone_id, "async_intake": False},
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()
    repository.save_intake(record)
    repository.save_idempotency_record(IntakeIdempotencyRecord(
        action="submit_intake", key=hashlib.sha256(actor.encode()).hexdigest() + ":" + key,
        response=deepcopy(record), actor_subject_id=actor, request_sha256=fingerprint,
    ))
    return record


def test_body_claims_cannot_transfer_historical_replay_and_real_bearer_wins(api):
    client,service,repository = api
    key = "synthetic-body-claims-key"
    original = _seed_submission(repository, key=key,
        url="https://www.synthetic.example/detail-owned-body.html")
    result = _submit(api, key=key, suffix="owned-body")
    assert result.status_code == 200, result.text
    intake = result.json()
    assert intake == original
    assert intake["owner"] == intake["submitter"] == OWNER
    assert intake["auditEvents"][-1]["actorName"] == OWNER
    assert intake["auditEvents"][-1]["actorRoleId"] == Role.EXPANSION_USER.value
    path = BASE+"/intake/"+intake["id"]
    assert client.get(path,headers=_headers(OWNER)).status_code == 200
    assert client.get(path,headers=_headers(OTHER)).status_code == 403
    assert len(service._state["assistedIntakes"]) == 1


def test_same_key_is_scoped_to_actor_and_request_changes_conflict(api):
    _,service,repository = api
    key = "synthetic-identical-key"
    _seed_submission(repository, key=key, url="https://www.synthetic.example/detail-owned-a.html")
    _seed_submission(repository, actor=OTHER, key=key,
        url="https://www.synthetic.example/detail-owned-b.html")
    first = _submit(api,key=key,suffix="owned-a")
    second = _submit(api,OTHER,key=key,suffix="owned-b")
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] != second.json()["id"]
    before = deepcopy((service._state,repository.idempotency))
    replay = _submit(api,key=key,suffix="owned-a",actor_name="another display claim")
    assert replay.json() == first.json()
    conflict = _submit(api,key=key,suffix="changed-url")
    assert conflict.status_code == 409 and conflict.json()["detail"] == "IDEMPOTENCY_CONFLICT"
    assert (service._state,repository.idempotency) == before


def test_historical_receipt_cannot_reveal_current_private_intake_or_save_replay(api):
    _,service,repository = api
    key = "synthetic-private-replay-key"
    # OTHER once submitted it. The current row is now private to OWNER, while
    # OTHER's historical receipt must not preserve the old access grant.
    record = _seed_submission(repository, actor=OTHER, key=key,
        url="https://www.synthetic.example/detail-private-url.html")
    record.update(owner=OWNER, submitter=OWNER, submittedBy=OWNER)
    repository.save_intake(record)
    service.get_intake(record["id"])
    before = deepcopy((service._state,repository.idempotency))
    denied = _submit(api,OTHER,key=key,suffix="private-url")
    assert denied.status_code == 403 and denied.json()["detail"] == "OWNERSHIP_REQUIRED"
    assert record["id"] not in denied.text
    assert (service._state,repository.idempotency) == before


@pytest.mark.parametrize("stage", [None, "SUBMITTED", "READY", "NEEDS_REVIEW", "QUARANTINED", "FAILED", "AWAITING_ASSISTED_ENTRY"])
@pytest.mark.parametrize("subject", [OWNER, OTHER])
def test_fresh_operator_url_is_not_a_duplicate_or_foreign_receipt(api, monkeypatch, stage, subject):
    _, service, repository = api
    url = "https://www.synthetic.example/detail-retired-fresh.html"
    if stage is not None:
        record = _seed_submission(repository, key="synthetic-existing-history", url=url)
        record["stage"] = stage
        repository.save_intake(record)
    service._load_intakes()
    service._load_idempotency_cache()
    before = deepcopy((service.export_state(), repository.list_intakes(), repository.list_idempotency_records()))

    def no_effect(*_args, **_kwargs):
        raise AssertionError("Fresh legacy URL must not resolve policy, retrieve or persist")

    monkeypatch.setattr("modules.external_data.application.assisted_intake.resolve_source_policy", no_effect)
    monkeypatch.setattr(service, "_get_snapshot_service", no_effect)
    monkeypatch.setattr(repository, "save_intake", no_effect)
    monkeypatch.setattr(repository, "save_idempotency_record", no_effect)
    response = _submit(api, subject, key="synthetic-unrecorded-command", suffix="retired-fresh")
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": (
        "LEGACY_URL_CREATE_RETIRED: use POST /api/v1/canonical-intakes "
        "with an explicit reason and scope"
    )}
    assert "location" not in response.headers
    assert "idempotency-replayed" not in response.headers
    assert (service.export_state(), repository.list_intakes(), repository.list_idempotency_records()) == before


@pytest.mark.parametrize("seed_fixtures", [False, True])
@pytest.mark.parametrize("async_intake", [False, True])
def test_direct_service_retires_fresh_url_even_in_fixture_or_async_mode(seed_fixtures, async_intake):
    repository = InMemoryAssistedIntakeRepository()
    service = NetworkListingService(intake_repository=repository, tenant_id=TENANT, seed_fixtures=seed_fixtures)
    before = deepcopy((service.export_state(), repository.list_intakes(), repository.list_idempotency_records()))

    class NoQueue:
        def enqueue(self, *_args, **_kwargs):
            raise AssertionError("Fresh legacy URL must not enqueue retrieval")

    with pytest.raises(NetworkListingPolicyError, match="LEGACY_URL_CREATE_RETIRED: use POST /api/v1/canonical-intakes"):
        service.submit_intake(url="https://example.invalid/fresh-retired", heat_zone_id=None,
            actor_name=OWNER, actor_role_id="expansion_user", tenant_id=TENANT,
            idempotency_key="synthetic-retired-direct", correlation_id="synthetic-correlation",
            job_queue=NoQueue(), async_intake=async_intake, authorize_existing=lambda _record: None)
    assert (service.export_state(), repository.list_intakes(), repository.list_idempotency_records()) == before


def test_staff_cannot_use_body_manager_role_to_execute_decision(api):
    client,service,repository = api
    record = _seed_submission(repository, key="synthetic-staff-decision-setup",
        url="https://www.synthetic.example/detail-staff-decision.html")
    iid = record["id"]
    service.get_intake(iid)
    before = deepcopy(service._state)
    result = client.post(BASE+"/intake/"+iid+"/decide",headers=_headers(),json={
        "action":"NEW","reason":"Synthetic proposal","riskSummary":"Synthetic risk",
        "riskAcknowledged":True,"actorRoleId":"expansionManager","actorName":MANAGER,
    })
    assert result.status_code == 422
    assert "expansion_user" in result.json()["detail"]
    assert service._state == before


@pytest.mark.parametrize("actor,tenant,role", [
    (None,TENANT,"site_reviewer"),("display-name",TENANT,"site_reviewer"),
    (OWNER,None,"site_reviewer"),(OWNER,"invented-tenant","site_reviewer"),
    (OWNER,TENANT,"expansionManager"),
])
def test_production_rejects_missing_or_noncanonical_identity_before_mutation(actor,tenant,role):
    service = NetworkListingService(seed_fixtures=False,tenant_id=tenant)
    before = deepcopy(service.export_state())
    with pytest.raises(NetworkListingPolicyError):
        service.submit_intake(url="https://example.invalid/property",heat_zone_id=None,
                              actor_name=actor,actor_role_id=role,tenant_id=tenant,
                              idempotency_key="synthetic-production-key",correlation_id="synthetic-correlation")
    assert service.export_state() == before


def test_missing_bearer_cannot_use_headers_or_body_identity_claim(api):
    client,service,_ = api
    before = deepcopy(service._state)
    response = client.post(BASE+"/intake/submit",json={"url":"https://example.invalid/property","actorName":OWNER},
                           headers={"X-Subject-Id":OWNER,"X-Tenant-Id":TENANT,"X-Roles":"site_reviewer"})
    assert response.status_code == 401
    assert service._state == before


@pytest.mark.parametrize("method,path,body", [
    ("submit_intake", "/intake/submit", {"url":"https://example.invalid/actor-probe"}),
    ("convert_listing", "/listings/L-2024/convert", {}),
    ("merge_listing", "/listings/L-2029/merge", {"targetListingId":"L-2025"}),
    ("archive_listing", "/listings/L-2030/archive", {}),
    ("correct_intake", "/intake/actor-probe/correct", {"fields":{"floor":"1F"}}),
    ("decide_intake", "/intake/actor-probe/decide", {"action":"NEW"}),
    ("retry_intake", "/intake/actor-probe/retry", {}),
    ("promote_intake", "/intake/actor-probe/promote", {}),
])
def test_every_write_passes_verified_actor_and_role_to_service(api,monkeypatch,method,path,body):
    # This probe covers HTTP attribution, not the stubbed business effect.
    client,service,repository = api
    intake={"id":"actor-probe","tenantId":TENANT,"owner":OWNER,"submitter":OWNER,
            "stage":"FAILED","parsedFields":{},"auditEvents":[]}
    repository.save_intake(intake)
    service._load_intakes()
    service._listing("L-2029")["proposer"] = OWNER
    captured=[]
    def effect(**kwargs):
        captured.append(kwargs)
        return deepcopy(intake)
    monkeypatch.setattr(service,method,effect)
    response=client.post(BASE+path,headers=_headers(MANAGER),json={**body,
        "actorName":OTHER,"actorRoleId":"platform_admin","reason":"Synthetic operation",
        "riskSummary":"Synthetic disclosed risk","riskAcknowledged":True})
    assert response.status_code == 200,response.text
    assert len(captured)==1
    assert captured[0]["actor_name"] == MANAGER
    assert captured[0]["actor_role_id"] == Role.SITE_REVIEWER.value


def test_production_uuid_ownership_and_bound_receipt_survive_repository_restart(tmp_path):
    from shared.infrastructure.persistence.document_store import SqliteDocumentStore
    from shared.infrastructure.persistence.engine import SqliteEngine
    from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
    from shared.infrastructure.persistence.operator_network_listings import (
        DurableAssistedIntakeRepository,
    )
    path=tmp_path/"operator-actor-replay.sqlite3"
    request={"url":"https://example.invalid/private-owned-intake","heat_zone_id":None,
             "actor_role_id":"expansion_user","actor_name":OWNER,"tenant_id":TENANT,
             "idempotency_key":"synthetic-restart-key","correlation_id":"synthetic-correlation",
             "authorize_existing":lambda record:None}
    engine=SqliteEngine(path)
    try:
        repo=DurableAssistedIntakeRepository(TenantScopedDocumentStore(SqliteDocumentStore(engine),TENANT))
        first=_seed_submission(repo, key=request["idempotency_key"], url=request["url"], heat_zone_id=None)
        service=NetworkListingService(intake_repository=repo,seed_fixtures=False,tenant_id=TENANT)
        assert service.submit_intake(**request) == first
        assert first["owner"] == first["submitter"] == OWNER
        stored=repo.list_idempotency_records()
        assert len(stored)==1 and stored[0].actor_subject_id == OWNER and stored[0].request_sha256
    finally:
        engine.close()

    engine=SqliteEngine(path)
    try:
        repo=DurableAssistedIntakeRepository(TenantScopedDocumentStore(SqliteDocumentStore(engine),TENANT))
        service=NetworkListingService(intake_repository=repo,seed_fixtures=False,tenant_id=TENANT)
        assert service.get_intake(first["id"]) == first
        assert service.submit_intake(**request) == first
        assert len(repo.list_intakes()) == len(repo.list_idempotency_records()) == 1
    finally:
        engine.close()

def test_first_actor_header_cannot_override_persisted_proposer(api):
    client,service,repository=api
    service._listing("L-2029")["proposer"]=MANAGER
    before=deepcopy((service._state,repository.idempotency))
    response=client.post(BASE+"/listings/L-2029/merge",headers={**_headers(MANAGER),"X-First-Actor-Id":OTHER},
                         json={"targetListingId":"L-2025","reason":"Synthetic duplicate","riskSummary":"Synthetic risk",
                               "riskAcknowledged":True,"actorName":OTHER,"actorRoleId":"expansionManager"})
    assert response.status_code==409 and response.json()["detail"]=="SECOND_ACTOR_REQUIRED"
    assert (service._state,repository.idempotency)==before


def test_local_account_session_and_current_roles_control_cached_receipt():
    from shared.identity import (
        Account,
        InMemoryIdentityStore,
        InMemorySessionRepository,
        SessionService,
    )

    reset_bound_persistence()

    identities=InMemoryIdentityStore()
    identities.save_account(Account(account_id=UUID(OWNER),tenant_id=UUID(TENANT),
                                    username="verified-local-owner",email="owner@example.invalid"))
    identities.set_account_roles(OWNER,{Role.EXPANSION_USER})
    identities.set_account_scope(OWNER,Scope(tenant_id=TENANT,heat_zone_ids=frozenset({"HZ-01"})))
    sessions=SessionService(InMemorySessionRepository())
    session=sessions.create_session(account_id=UUID(OWNER),provider="local_password")
    boundary=AuthenticationBoundary(AuthBoundaryConfig(
        local_issuer="urn:odp:identity:local",local_signing_keys={KEY.kid:KEY},
        local_audiences=frozenset({"operator-test"}),identity_store=identities,session_service=sessions,
    ))
    repository=InMemoryAssistedIntakeRepository()
    service=NetworkListingService(intake_repository=repository,seed_fixtures=False,tenant_id=TENANT)
    app=FastAPI()
    app.include_router(create_network_listings_sub_router(
        service,require_view_permission_fn=require_operator_permission("listing",Action.VIEW,boundary=boundary),
        require_write_permission_fn=require_operator_permission("listing",Action.UPDATE,boundary=boundary),
    ))
    now=datetime.now(UTC)
    token=encode_compact_jwt({"sub":OWNER,"sid":str(session.session_id),"tenant_id":TENANT,
        "iss":"urn:odp:identity:local","aud":"operator-test","iat":now.timestamp(),
        "exp":(now+timedelta(hours=1)).timestamp(),"roles":["site_reviewer"]},KEY)
    headers={"Authorization":"Bearer "+token,"Idempotency-Key":"synthetic-local-replay-key", "X-Correlation-Id":"synthetic-correlation"}
    payload={"url":"https://example.invalid/local-owner","heatZoneId":"HZ-01","actorName":OTHER,"actorRoleId":"expansionManager"}
    original=_seed_submission(repository, key=headers["Idempotency-Key"], url=payload["url"])
    with TestClient(app) as client:
        first=client.post(BASE+"/intake/submit",headers=headers,json=payload)
        assert first.status_code==200,first.text
        assert first.json()==original
        assert first.json()["owner"]==first.json()["submitter"]==OWNER
        before=deepcopy((service._state,repository.idempotency))
        assert client.post(BASE+"/intake/submit",headers=headers,json=payload).json()==first.json()
        # Controlled server scope change; not an admitted real business write.
        record=service._listing_intake(first.json()["id"])
        record["heatZoneId"]="HZ-02"
        repository.save_intake(record)
        service.get_intake(record["id"])
        shifted=deepcopy((service._state,repository.idempotency))
        scoped_denial=client.post(BASE+"/intake/submit",headers=headers,json=payload)
        assert scoped_denial.status_code==403
        assert (service._state,repository.idempotency)==shifted
        record["heatZoneId"]="HZ-01"
        repository.save_intake(record)
        service.get_intake(record["id"])
        identities.set_account_roles(OWNER,{Role.FRANCHISEE})
        denied=client.post(BASE+"/intake/submit",headers=headers,json=payload)
        assert denied.status_code==403
        assert (service._state,repository.idempotency)==before
    reset_bound_persistence()

def test_unbound_legacy_receipt_is_preserved_without_replay_or_reapplication():
    repository=InMemoryAssistedIntakeRepository()
    legacy=IntakeIdempotencyRecord(action="submit_intake",key="synthetic-legacy-key",
                                  response={"id":"legacy-intake","owner":"historical-display-name"})
    repository.save_idempotency_record(legacy)
    service=NetworkListingService(intake_repository=repository,seed_fixtures=False,tenant_id=TENANT)
    before=deepcopy((service.export_state(),repository.idempotency))
    with pytest.raises(NetworkListingConflict,match="UNBOUND_LEGACY_REPLAY"):
        service.submit_intake(url="https://example.invalid/legacy-context",heat_zone_id=None,
                              actor_name=OWNER,actor_role_id="expansion_user",tenant_id=TENANT,
                              idempotency_key=legacy.key,correlation_id="synthetic-correlation")
    assert (service.export_state(),repository.idempotency)==before

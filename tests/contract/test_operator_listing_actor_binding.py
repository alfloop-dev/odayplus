"""Signed-token attribution and actor-bound replay at the real HTTP guard."""

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


def test_body_claims_cannot_transfer_ownership_and_real_bearer_wins(api):
    client,service,_ = api
    result = _submit(api)
    assert result.status_code == 200, result.text
    intake = result.json()
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


def test_exact_duplicate_cannot_reveal_private_intake_or_save_replay(api):
    _,service,repository = api
    first = _submit(api,suffix="private-url")
    assert first.status_code == 200
    before = deepcopy((service._state,repository.idempotency))
    denied = _submit(api,OTHER,suffix="private-url")
    assert denied.status_code == 403 and denied.json()["detail"] == "OWNERSHIP_REQUIRED"
    assert first.json()["id"] not in denied.text
    assert (service._state,repository.idempotency) == before


def test_staff_cannot_use_body_manager_role_to_execute_decision(api):
    client,service,_ = api
    iid = _submit(api).json()["id"]
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
    ("convert_listing", "/listings/L-2024/convert", {}),
    ("merge_listing", "/listings/L-2029/merge", {"targetListingId":"L-2025"}),
    ("archive_listing", "/listings/L-2030/archive", {}),
    ("correct_intake", "/intake/actor-probe/correct", {"fields":{"floor":"1F"}}),
    ("decide_intake", "/intake/actor-probe/decide", {"action":"NEW"}),
    ("retry_intake", "/intake/actor-probe/retry", {}),
    ("promote_intake", "/intake/actor-probe/promote", {}),
])
def test_every_other_write_passes_verified_actor_and_role_to_service(api,monkeypatch,method,path,body):
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
        service=NetworkListingService(intake_repository=repo,seed_fixtures=False,tenant_id=TENANT)
        first=service.submit_intake(**request)
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
    with TestClient(app) as client:
        first=client.post(BASE+"/intake/submit",headers=headers,json=payload)
        assert first.status_code==200,first.text
        assert first.json()["owner"]==first.json()["submitter"]==OWNER
        before=deepcopy((service._state,repository.idempotency))
        assert client.post(BASE+"/intake/submit",headers=headers,json=payload).json()==first.json()
        # Controlled server scope change; not an admitted real business write.
        record=service._listing_intake(first.json()["id"])
        record["heatZoneId"]="HZ-02"
        shifted=deepcopy((service._state,repository.idempotency))
        scoped_denial=client.post(BASE+"/intake/submit",headers=headers,json=payload)
        assert scoped_denial.status_code==403
        assert (service._state,repository.idempotency)==shifted
        record["heatZoneId"]="HZ-01"
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

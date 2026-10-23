"""Signed HTTP/current SQL identity/native Survey HeatZone engineering controls.

Uses unchanged owned PostgreSQL/migration and signed JWT/session fixtures. Scores,
accounts and candidate metadata are explicit synthetic inputs, not media or UAT.
No product projection, authorization, factory or repository is replaced here.
"""
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.heatzone.workers import HeatZoneBatchScoreResult
from modules.opsboard.application.operator_live_repository import OperatorLiveRepository
from shared.auth import DataClassification, Role, Scope
from shared.domain import AddressLocation, Brand, Store
from shared.identity.store import Account
from tests.integration.test_canonical_survey_assignments_postgresql import owners as owners
from tests.integration.test_network_review_submission_sod import _record, _signed_client
from tests.integration.test_operator_canonical_wiring import CanonicalHarness
from tests.unit.test_operator_canonical_heat_zones import zone_result

pytestmark = pytest.mark.requires_live_env
TASKS = "/api/v1/operator/shell/tasks"
ASSIGNMENTS = "/api/v1/market-survey/assignments"
ZONE = "zone-owned"
DECOY = "zone-default-not-the-assignment-parent"


def _publish(bundle, tenant, scores):
    bundle.heatzone_store_for_tenant(tenant).put(HeatZoneBatchScoreResult(
        job_id=str(uuid4()), status="succeeded", scores=tuple(scores),
        completed_at=datetime.now(UTC), tenant_id=tenant,
    ))


def _items(client, signed_headers):
    response = client.get(TASKS, headers=signed_headers, params={"view": "all"})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _task(client, signed_headers, identity):
    matches = [row for row in _items(client, signed_headers)
               if row["taskId"] == "market-survey.assignment:" + identity]
    assert len(matches) == 1, matches
    return matches[0]


def _assert_exact(row, identity, assignee):
    assert row["sourceId"] == identity and row["sourceKind"] == "market-survey.assignment"
    assert row["assigneeId"] == assignee
    assert row["sourceDetailAvailable"] is True
    assert row["target"] == {"workspace": "network", "tab": "areas",
                             "entityId": ZONE, "heatZoneId": ZONE}


@pytest.fixture
def native_http(owners, monkeypatch):
    # Native scores do not assert brand/store/area ownership. Use the real
    # identity store to retain only a supported, explicit HeatZone restriction.
    scope = Scope(tenant_id=owners.tenant, modules=frozenset({"operator_console"}),
                  heat_zone_ids=frozenset({ZONE}), clearance=DataClassification.CONFIDENTIAL)
    for subject in (owners.actor, owners.assignee):
        owners.first.identity_store.set_account_scope(subject, scope)
    _publish(owners.first, owners.tenant,
             [zone_result(DECOY, rank=1), zone_result(ZONE, rank=2)])
    # Reuse original signing/current identity/session plumbing unchanged. The
    # harness's unrelated SiteScore router is not used by these requests.
    harness = CanonicalHarness(None, bundle=owners.first)
    pg = (owners.database, owners.first, harness, owners.tenant, None, None)
    # create_app legitimately resets the cached boundary when it binds its
    # persistence/audit owners. Configure the same original synthetic signing
    # fixture inputs so normal construction captures a real SQL-backed boundary.
    monkeypatch.setenv("ODP_AUTH_LOCAL_ISSUER", "urn:odp:identity:local")
    monkeypatch.setenv("ODP_AUTH_LOCAL_AUDIENCES", "isolated-submit-sod")
    monkeypatch.setenv("ODP_AUTH_LOCAL_HS256_KEYS",
                       "synthetic-sod-local:isolated-local-sod-regression-key")
    with _signed_client(pg, monkeypatch) as (_, headers, _, _):
        with TestClient(create_app(persistence=owners.first)) as client:
            signed = headers(owners.actor, "heat-zone-create")
            result = client.post(ASSIGNMENTS, headers=signed, json={
                "targetKind": "HEAT_ZONE", "targetId": ZONE,
                "assignedTo": owners.assignee,
                "expiresAt": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
                "reason": "Explicit synthetic native HeatZone task HTTP control",
            })
            assert result.status_code == 201, result.text
            assignment = result.json()["assignment"]
            identity = assignment["assignment_id"]
            assert assignment["target_entity_kind"] == "HEAT_ZONE"
            assert assignment["target_entity_id"] == ZONE
            assert assignment["tenant_id"] == owners.tenant
            assert assignment["created_by"] == owners.actor
            # Independent real engine proves persistence, not just POST echo.
            persisted = owners.second.survey_assignment_repository_for_tenant(
                owners.tenant).get_assignment(identity)
            assert persisted.target_entity_id == ZONE and persisted.created_by == owners.actor
            # Reuse this exact token after SQL changes: token/header claims
            # cannot preserve a revoked current role or scope.
            read_headers = headers(owners.assignee, "heat-zone-current-read")
            baseline = _task(client, read_headers, identity)
            _assert_exact(baseline, identity, owners.assignee)
            yield SimpleNamespace(owners=owners, bundle=owners.first, client=client,
                                  headers=headers, signed=read_headers, scope=scope,
                                  identity=identity, pg=pg, assignment=assignment)


def test_signed_http_exact_native_parent_and_persisted_lifecycle(native_http):
    ctx = native_http
    row = _task(ctx.client, ctx.signed, ctx.identity)
    _assert_exact(row, ctx.identity, ctx.owners.assignee)
    assert row["completed"] is False and row["assignedToMe"] is True
    assert row["createdBy"] == ctx.owners.actor
    assert row["expiresAt"] == ctx.assignment["expires_at"]
    assert row["slaDueAt"] is None
    unauthenticated = ctx.client.get(TASKS, headers={
        "x-subject-id": ctx.owners.assignee, "x-tenant-id": ctx.owners.tenant,
        "x-roles": "expansion_user",
    })
    assert unauthenticated.status_code == 401
    # A higher-ranked native zone and a candidate linked to zone-owned already
    # exist. Neither may replace the actual HEAT_ZONE source/target identity.
    assert row["target"]["entityId"] not in {DECOY, "candidate-owned"}
    cancelled = ctx.client.post(ASSIGNMENTS + "/" + ctx.identity + "/cancel",
        headers=ctx.headers(ctx.owners.actor, "heat-zone-cancel"),
        json={"expectedRevision": 1, "reason": "Cancel the actual persisted native assignment"})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["assignment"]["status"] == "CANCELLED"
    row = _task(ctx.client, ctx.signed, ctx.identity)
    _assert_exact(row, ctx.identity, ctx.owners.assignee)
    assert row["completed"] is True
    restored, revision = ctx.owners.second.survey_assignment_repository_for_tenant(
        ctx.owners.tenant).get_versioned(ctx.identity)
    assert restored.status.value == "CANCELLED" and revision == 2


@pytest.mark.parametrize("restriction", [
    "missing_heatzone_view", "wrong_current_zone_scope", "missing_restricted_axis",
    "duplicate_native_owner", "missing_current_native_owner", "candidate_or_default_substitution",
])
def test_current_admission_withholds_task_after_real_database_change(native_http, restriction):
    ctx = native_http
    before = ctx.owners.second.survey_assignment_repository_for_tenant(
        ctx.owners.tenant).get_assignment(ctx.identity).to_dict()
    if restriction == "missing_heatzone_view":
        # This role keeps operator_console.VIEW but has no heatzone.VIEW.
        ctx.bundle.identity_store.set_account_roles(ctx.owners.assignee, [Role.OPERATIONS_MANAGER])
        assert ctx.owners.second.identity_store.get_account_roles(ctx.owners.assignee) == frozenset({Role.OPERATIONS_MANAGER})
    elif restriction == "wrong_current_zone_scope":
        changed = replace(ctx.scope, heat_zone_ids=frozenset({DECOY}))
        ctx.bundle.identity_store.set_account_scope(ctx.owners.assignee, changed)
        assert ctx.owners.second.identity_store.get_account_scope(
            ctx.owners.assignee, require_explicit=True) == changed
    elif restriction == "missing_restricted_axis":
        # The live owner's SQL store filter is UUID-typed. Supply real canonical
        # masters, not the candidate metadata's unrelated "store-owned" label.
        # This follows the existing PG owners/_bundle + canonical repository
        # fixture pattern; no source, projection, auth or SQL method is replaced.
        brand_id, store_id, address_id = (str(uuid4()) for _ in range(3))
        ctx.bundle.brand_repository.save_brand(Brand(
            brand_id=brand_id, tenant_id=ctx.owners.tenant, brand_code=brand_id,
            brand_name="Synthetic HeatZone scope prerequisite"))
        ctx.bundle.address_location_repository.save_address(AddressLocation(
            address_id=address_id, tenant_id=ctx.owners.tenant,
            raw_address="Synthetic HeatZone scope prerequisite"))
        ctx.bundle.store_repository.save_store(Store(
            store_id=store_id, tenant_id=ctx.owners.tenant, brand_id=brand_id,
            address_id=address_id, store_name="Synthetic HeatZone scope prerequisite",
            store_status="open", region_code="north"))
        second = ctx.owners.second
        brand = second.brand_repository.get_brand(brand_id)
        assert brand.brand_id == brand_id and brand.tenant_id == ctx.owners.tenant
        stores = second.store_repository.list_stores(
            tenant_id=ctx.owners.tenant, store_ids=(store_id,))
        assert len(stores) == 1
        assert stores[0].store_id == store_id and stores[0].brand_id == brand_id
        assert stores[0].tenant_id == ctx.owners.tenant and stores[0].is_current
        changed = replace(ctx.scope, store_ids=frozenset({store_id}))
        ctx.bundle.identity_store.set_account_scope(ctx.owners.assignee, changed)
        assert second.identity_store.get_account_scope(
            ctx.owners.assignee, require_explicit=True) == changed
        assert second.identity_store.get_account_roles(
            ctx.owners.assignee) == frozenset({Role.EXPANSION_USER})
        # Independently prove healthy SQL sources and the actual unadmitted
        # native row. An unavailable owner or absent source row is not a pass.
        state = OperatorLiveRepository(second).load_state(
            tenant_id=ctx.owners.tenant, store_ids=(store_id,))
        sections = state["_meta"]["sections"]
        for section in ("stores", "interventions", "approvals"):
            assert sections[section]["state"] == "available", sections[section]
        assert sections["stores"]["recordCount"] == 1
        raw = [row for row in state["taskCenterRecords"]["records"]
               if row["taskId"] == "market-survey.assignment:" + ctx.identity]
        assert len(raw) == 1 and raw[0]["sourceId"] == ctx.identity
        parent = raw[0]["_surveyParent"]
        assert parent["kind"] == "HEAT_ZONE" and parent["id"] == ZONE
        assert parent["tenantId"] == ctx.owners.tenant
        evidence = parent["scope"]
        assert evidence["heat_zone_id"] == evidence["heatZoneId"] == ZONE
        assert not evidence.get("storeId") and not evidence.get("store_id")
        _record("heat-zone-missing-axis-source", {
            "healthySections": {key: sections[key] for key in ("stores", "interventions", "approvals")},
            "canonicalStoreUUID": store_id, "canonicalBrandUUID": brand_id,
            "secondEngineReadback": True, "unadmittedNativeRows": len(raw),
            "exactNativeParent": ZONE, "nativeStoreEvidenceAbsent": True,
            "sameSignedTokenRetained": True, "currentRoleUnchanged": True,
        })
    else:
        scores = ([zone_result(ZONE), zone_result(ZONE, rank=2)]
                  if restriction == "duplicate_native_owner" else []
                  if restriction == "missing_current_native_owner" else
                  [zone_result(DECOY), zone_result("candidate-owned", rank=2)])
        _publish(ctx.bundle, ctx.owners.tenant, scores)
        actual = ctx.owners.second.heatzone_store_for_tenant(ctx.owners.tenant).list_scores()
        assert [row["heat_zone_id"] for row in actual] == [score.heat_zone_id for score in scores]
        # The retained candidate still references this zone. It is not a native
        # HeatZone owner and cannot rescue an absent/ambiguous current target.
        candidate = ctx.owners.second.listing_repository_for_tenant(
            ctx.owners.tenant).list_candidates()[0]
        assert candidate.candidate_site.candidate_site_id == "candidate-owned"
    rows = _items(ctx.client, ctx.signed)
    assert not any(row["sourceId"] == ctx.identity for row in rows)
    if restriction == "missing_restricted_axis":
        # No native task, exact target or href may leak after real row admission.
        assert ctx.identity not in json.dumps(rows)
        assert not any((row.get("target") or {}).get("heatZoneId") == ZONE for row in rows)
    assert ctx.owners.second.survey_assignment_repository_for_tenant(
        ctx.owners.tenant).get_assignment(ctx.identity).to_dict() == before
    if restriction == "missing_restricted_axis":
        _record("heat-zone-missing-axis-http", {
            "status": 200, "nativeTaskAbsent": True, "nativeTargetAbsent": True,
            "assignmentIdentityAbsentIncludingHref": True, "assignmentUnchanged": True,
            "projectionAuthSQLMocked": False,
        })


def test_valid_signed_foreign_tenant_cannot_read_assignment_or_reuse_native_zone(native_http, monkeypatch):
    ctx = native_http
    foreign, actor = str(uuid4()), str(uuid4())
    with ctx.owners.database.connect() as connection:
        connection.execute("INSERT INTO core.tenants(tenant_id,tenant_name) VALUES (%s,%s)",
                           (foreign, "Explicit synthetic foreign HeatZone tenant"))
    ctx.bundle.identity_store.save_account(Account(account_id=UUID(actor), tenant_id=UUID(foreign),
        username=actor, email=actor + "@example.invalid", display_name="Synthetic foreign reader"))
    ctx.bundle.identity_store.set_account_roles(actor, [Role.EXPANSION_USER])
    ctx.bundle.identity_store.set_account_scope(actor, replace(ctx.scope, tenant_id=foreign))
    # Even the exact same zone ID in another real tenant partition must not
    # admit an assignment owned by the original tenant.
    _publish(ctx.bundle, foreign, [zone_result(ZONE)])
    foreign_pg = (*ctx.pg[:3], foreign, None, None)
    with _signed_client(foreign_pg, monkeypatch) as (_, headers, _, _):
        signed = headers(actor, "heat-zone-foreign-read")
        rows = _items(ctx.client, {**signed, "x-tenant-id": ctx.owners.tenant})
        assert not any(row["sourceId"] == ctx.identity for row in rows)
        response = ctx.client.get(ASSIGNMENTS + "/" + ctx.identity, headers=signed)
        assert response.status_code == 404, response.text
    _assert_exact(_task(ctx.client, ctx.signed, ctx.identity), ctx.identity, ctx.owners.assignee)

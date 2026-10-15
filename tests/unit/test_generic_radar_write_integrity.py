"""Synthetic legacy Listing Radar safeguards; not physical-graph execution/CAS."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules.network_listings import create_network_listings_sub_router
from apps.api.oday_api.security.dependencies import (
    require_operator_permission,
    reset_bound_persistence,
)
from modules.opsboard.application.network_listings import (
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
from shared.auth import Action


def service():
    result = NetworkListingService()
    fixture_stepper_anchor = deepcopy(result._listing("L-2024"))
    result._state["listings"] = [
        {
            "id": "source-uuid",
            "address": "Source",
            "status": "duplicate",
            "heatZoneId": "HZ-01",
            "duplicateOfId": "target-uuid",
            "hardRuleFailures": [],
            "sourceEvidence": ["source-evidence"],
            "hasLegalHold": False,
        },
        {
            "id": "target-uuid",
            "address": "Target",
            "status": "new",
            "heatZoneId": "HZ-01",
            "hardRuleFailures": [],
            "sourceEvidence": ["target-evidence"],
            "hasLegalHold": False,
        },
        {
            "id": "archive-uuid",
            "address": "Archive",
            "status": "hardfail",
            "heatZoneId": "HZ-01",
            "hardRuleFailures": ["recorded_failure"],
            "sourceEvidence": [],
            "hasLegalHold": False,
        },
    ]
    result._state["listings"].append(fixture_stepper_anchor)
    result._state["candidates"] = []
    result._state["auditEvents"] = []
    return result


def merge(subject, key="merge-key", **overrides):
    values = {
        "source_listing_id": "source-uuid",
        "target_listing_id": "target-uuid",
        "reason": "Recorded explicit duplicate relation",
        "risk_summary": "Retain source evidence",
        "risk_acknowledged": True,
        "actor_role_id": "expansion-manager",
        "actor_name": "synthetic-actor",
        "idempotency_key": key,
        "correlation_id": "synthetic-correlation",
    }
    values.update(overrides)
    return subject.merge_listing(**values)


def archive(subject, key="archive-key", **overrides):
    values = {
        "listing_id": "archive-uuid",
        "reason": "Operator entered actual reason",
        "actor_role_id": "expansion-manager",
        "actor_name": "synthetic-actor",
        "idempotency_key": key,
        "correlation_id": "synthetic-correlation",
    }
    values.update(overrides)
    return subject.archive_listing(**values)


def test_generic_fixture_positive_and_exact_actor_bound_replay_retains_evidence():
    subject = service()
    result = merge(subject)
    assert result["source"]["mergedIntoId"] == "target-uuid"
    assert result["target"]["sourceEvidence"] == ["target-evidence", "source-evidence"]
    assert merge(subject) == result
    assert len(subject._state["auditEvents"]) == 1
    with pytest.raises(NetworkListingConflict):
        merge(subject, reason="Changed logical request")
    with pytest.raises(NetworkListingConflict, match="IDEMPOTENCY_CONFLICT"):
        merge(subject, target_listing_id="missing-changed-target")


@pytest.mark.parametrize(
    "case",
    [
        "archived-source",
        "archived-target",
        "merged-target",
        "cycle",
        "undeclared-target",
        "legal-hold",
        "source-candidate",
        "target-candidate",
        "multiple-candidates",
        "duplicate-source-id",
        "malformed-evidence",
        "unknown-candidates",
    ],
)
def test_merge_rejects_unresolved_current_resource_state_without_a_successful_audit(case):
    subject = service()
    source, target = subject._state["listings"][:2]
    if case == "archived-source":
        source["status"] = "archived"
    if case == "archived-target":
        target["status"] = "archived"
    if case == "merged-target":
        target["mergedIntoId"] = "other-record"
    if case == "cycle":
        target["duplicateOfId"] = source["id"]
    if case == "undeclared-target":
        source["duplicateOfId"] = "other-record"
    if case == "legal-hold":
        target["hasLegalHold"] = True
    if case == "source-candidate":
        source["candidateId"] = "retained-candidate"
    if case == "target-candidate":
        subject._state["candidates"] = [{"id": "candidate", "listingId": target["id"]}]
    if case == "multiple-candidates":
        subject._state["candidates"] = [
            {"id": "one", "listingId": source["id"]},
            {"id": "two", "listingId": source["id"]},
        ]
    if case == "duplicate-source-id":
        subject._state["listings"].append(deepcopy(source))
    if case == "malformed-evidence":
        source["sourceEvidence"] = None
    if case == "unknown-candidates":
        subject._state["candidates"] = None
    before = deepcopy(subject._state)
    with pytest.raises((NetworkListingConflict, NetworkListingPolicyError)):
        merge(subject)
    assert subject._state == before


@pytest.mark.parametrize(
    "case", ["terminal", "merged", "legal-hold", "retained-candidate", "unknown-hold"]
)
def test_archive_preserves_terminal_reason_and_hold_or_candidate_retention(case):
    subject = service()
    record = subject._state["listings"][2]
    if case == "terminal":
        record.update(status="archived", archivedReason="Previous real reason")
    if case == "merged":
        record["mergedIntoId"] = "target-uuid"
    if case == "legal-hold":
        record["hasLegalHold"] = True
    if case == "retained-candidate":
        record["candidateId"] = "retained-candidate"
    if case == "unknown-hold":
        record["hasLegalHold"] = "unknown"
    before = deepcopy(subject._state)
    with pytest.raises((NetworkListingConflict, NetworkListingPolicyError)):
        archive(subject)
    assert subject._state == before


def test_archive_exact_replay_returns_first_receipt_and_new_key_cannot_rewrite_reason():
    subject = service()
    first = archive(subject)
    assert archive(subject) == first
    with pytest.raises(NetworkListingConflict):
        archive(subject, key="new-key", reason="Second reason must not overwrite first")
    assert subject._state["listings"][2]["archivedReason"] == "Operator entered actual reason"
    assert len(subject._state["auditEvents"]) == 1


@pytest.mark.parametrize(
    "case",
    ["missing-source", "missing-target", "stale-source", "stale-target", "malformed-version"],
)
def test_actual_published_revisions_are_required_and_checked_for_both_merge_resources(case):
    subject = service()
    subject._state["listings"][0]["version"] = 7
    subject._state["listings"][1]["version"] = 9
    args = {"if_match": 'W/"7"', "target_if_match": 'W/"9"'}
    if case == "missing-source":
        args["if_match"] = None
    if case == "missing-target":
        args["target_if_match"] = None
    if case == "stale-source":
        args["if_match"] = 'W/"6"'
    if case == "stale-target":
        args["target_if_match"] = 'W/"8"'
    if case == "malformed-version":
        subject._state["listings"][0]["version"] = True
    before = deepcopy(subject._state)
    with pytest.raises((NetworkListingConflict, NetworkListingPolicyError)):
        merge(subject, **args)
    assert subject._state == before


def test_exact_versions_are_fingerprinted_and_replay_reauthorizes_current_both_resources():
    subject = service()
    subject._state["listings"][0]["version"] = 7
    subject._state["listings"][1]["version"] = 9
    observed = []
    args = {
        "if_match": 'W/"7"',
        "target_if_match": 'W/"9"',
        "authorize_existing": lambda row: observed.append((row["id"], row.get("mergedIntoId"))),
    }
    first = merge(subject, **args)
    assert first["actorSubjectId"] == first["auditEvent"]["actorName"] == "synthetic-actor"
    assert first["actorRoleId"] == first["auditEvent"]["actorRoleId"] == "expansion-manager"
    assert first["auditEvent"]["metadata"]["before"]["sourceVersion"] == 7
    assert first["auditEvent"]["metadata"]["before"]["targetVersion"] == 9
    assert first["auditEvent"]["metadata"]["after"]["sourceVersion"] == 7
    assert first["auditEvent"]["metadata"]["after"]["targetVersion"] == 9
    assert observed == [("source-uuid", None), ("target-uuid", None)]
    assert merge(subject, **args) == first
    assert observed[-2:] == [("source-uuid", "target-uuid"), ("target-uuid", None)]
    with pytest.raises(NetworkListingConflict, match="IDEMPOTENCY_CONFLICT"):
        merge(subject, **{**args, "target_if_match": 'W/"10"'})
    # This adapter validates a supplied revision, it does not invent a producer
    # counter or assert that the legacy repositories implement SQL CAS.
    assert first["source"]["version"] == 7 and first["target"]["version"] == 9


@pytest.mark.parametrize("denied_id", ["source-uuid", "target-uuid"])
def test_mutation_owner_rechecks_current_authorized_resources_before_mutation_and_replay(denied_id):
    subject = service()
    before = deepcopy(subject._state)

    def authorize(row):
        if row["id"] == denied_id:
            raise PermissionError("resource scope withdrawn")

    with pytest.raises(PermissionError):
        merge(subject, authorize_existing=authorize)
    assert subject._state == before
    first = merge(subject)
    before = deepcopy(subject._state)
    with pytest.raises(PermissionError):
        merge(subject, authorize_existing=authorize)
    assert subject._state == before and first["source"]["mergedIntoId"] == "target-uuid"


def test_canonical_missing_version_does_not_become_version_one_or_success():
    subject = service()
    subject._seed_fixtures = False
    subject._tenant_id = "00000000-0000-0000-0000-000000000001"
    before = deepcopy(subject._state)
    with pytest.raises(NetworkListingPolicyError, match="LISTING_VERSION_UNAVAILABLE"):
        archive(
            subject,
            actor_role_id="data_owner",
            actor_name="00000000-0000-0000-0000-000000000101",
            if_match='W/"1"',
            authorize_existing=lambda row: None,
        )
    assert subject._state == before and "version" not in subject._state["listings"][2]


def test_archive_revision_and_bound_request_replay_preserve_reason():
    subject = service()
    subject._state["listings"][2]["version"] = 13
    first = archive(subject, if_match='W/"13"')
    assert first["actorSubjectId"] == first["auditEvent"]["actorName"] == "synthetic-actor"
    assert first["auditEvent"]["metadata"]["before"]["version"] == 13
    assert first["auditEvent"]["metadata"]["after"]["version"] == 13
    assert archive(subject, if_match='W/"13"') == first
    for changes in ({"reason": "Changed reason"}, {"if_match": 'W/"14"'}):
        with pytest.raises(NetworkListingConflict, match="IDEMPOTENCY_CONFLICT"):
            archive(subject, **{"if_match": 'W/"13"', **changes})
    assert len(subject._state["auditEvents"]) == 1


@pytest.fixture
def signed_router():
    # Actual auth boundary and actual listing service, isolated synthetic data.
    # No full application wiring, MLflow, source retrieval, or model inference.
    tenant = "00000000-0000-0000-0000-000000000001"
    manager = "00000000-0000-0000-0000-000000000101"
    staff = "00000000-0000-0000-0000-000000000102"
    key = SigningKey(kid="synthetic-nav02", algorithm="HS256", secret=b"isolated-nav02-test-key")
    subject = service()
    subject._tenant_id = tenant
    for record in subject._state["listings"]:
        record.update(tenantId=tenant, brandId="recorded-brand")
    subject._state["listings"][0]["version"] = 7
    subject._state["listings"][1]["version"] = 9
    subject._state["listings"][2]["version"] = 13
    boundary = AuthenticationBoundary(
        AuthBoundaryConfig(
            issuer="https://nav02-idp.example.invalid",
            audiences=frozenset({"synthetic-nav02"}),
            signing_keys={key.kid: key},
            principal_mappings={
                manager: {
                    "roles": ["site_reviewer", "expansion_user"],
                    "scope": {"tenant_id": tenant, "brand_ids": ["recorded-brand"]},
                },
                staff: {
                    "roles": ["expansion_user"],
                    "scope": {"tenant_id": tenant, "brand_ids": ["recorded-brand"]},
                },
                "00000000-0000-0000-0000-000000000103": {
                    "roles": ["site_reviewer", "expansion_user"],
                    "scope": {"tenant_id": tenant, "brand_ids": ["recorded-brand"]},
                },
            },
        )
    )
    reset_bound_persistence()
    app = FastAPI()
    app.include_router(
        create_network_listings_sub_router(
            subject,
            require_view_permission_fn=require_operator_permission(
                "listing", Action.VIEW, boundary=boundary
            ),
            require_write_permission_fn=require_operator_permission(
                "listing", Action.UPDATE, boundary=boundary
            ),
        )
    )

    def headers(actor=manager, **changes):
        now = datetime.now(UTC)
        token = encode_compact_jwt(
            {
                "sub": actor,
                "iss": "https://nav02-idp.example.invalid",
                "aud": "synthetic-nav02",
                "iat": now.timestamp(),
                "exp": (now + timedelta(hours=1)).timestamp(),
            },
            key,
        )
        return {
            "Authorization": "Bearer " + token,
            "Idempotency-Key": "synthetic-api-key",
            "X-Correlation-Id": "synthetic-api-correlation",
            "If-Match": 'W/"7"',
            **changes,
        }

    with TestClient(app) as client:
        yield client, subject, headers, staff, manager
    reset_bound_persistence()


def api_merge(client, headers, extra=None):
    return client.post(
        "/network-listings/listings/source-uuid/merge",
        headers=headers,
        json={
            "targetListingId": "target-uuid",
            "targetIfMatch": 'W/"9"',
            "reason": "Actual operator entered reason",
            "riskSummary": "Actually rendered disclosure",
            "riskAcknowledged": True,
            "actorRoleId": "platform_admin",
            "actorName": "forged-body-actor",
            **(extra or {}),
        },
    )


def test_actual_api_trusted_actor_and_both_versions_positive_replay(signed_router):
    client, subject, headers, _, manager = signed_router
    first = api_merge(client, headers())
    assert first.status_code == 200, first.text
    assert first.json()["auditEvent"]["actorName"] == manager
    assert first.json()["auditEvent"]["actorRoleId"] == "site_reviewer"
    assert (
        first.json()["actorSubjectId"] == manager and first.json()["actorRoleId"] == "site_reviewer"
    )
    metadata = first.json()["auditEvent"]["metadata"]
    assert (
        metadata["sourceListingId"] == "source-uuid"
        and metadata["targetListingId"] == "target-uuid"
    )
    assert (
        metadata["riskSummary"] == "Actually rendered disclosure"
        and metadata["riskAcknowledged"] is True
    )
    assert metadata["before"]["sourceVersion"] == metadata["after"]["sourceVersion"] == 7
    assert metadata["before"]["targetVersion"] == metadata["after"]["targetVersion"] == 9
    assert api_merge(client, headers()).json() == first.json()
    assert len(subject._state["auditEvents"]) == 1


@pytest.mark.parametrize(
    "case", ["source-tenant", "target-tenant", "source-scope", "target-scope", "staff", "hold"]
)
def test_actual_api_denies_current_either_resource_or_role_and_preserves_state(signed_router, case):
    client, subject, headers, staff, _ = signed_router
    source, target = subject._state["listings"][:2]
    actor_headers = headers()
    if case == "source-tenant":
        source["tenantId"] = "00000000-0000-0000-0000-000000000002"
    if case == "target-tenant":
        target["tenantId"] = "00000000-0000-0000-0000-000000000002"
    if case == "source-scope":
        source["brandId"] = "excluded-brand"
    if case == "target-scope":
        target["brandId"] = "excluded-brand"
    if case == "staff":
        actor_headers = headers(staff)
    if case == "hold":
        target["hasLegalHold"] = True
    before = deepcopy(subject._state)
    response = api_merge(client, actor_headers)
    assert response.status_code == (409 if case == "hold" else 403), response.text
    assert subject._state == before


def test_actual_api_cached_receipt_rechecks_target_scope(signed_router):
    client, subject, headers, _, _ = signed_router
    assert api_merge(client, headers()).status_code == 200
    subject._state["listings"][1]["brandId"] = "excluded-brand"
    before = deepcopy(subject._state)
    response = api_merge(client, headers())
    assert response.status_code == 403, response.text
    assert subject._state == before


@pytest.mark.parametrize("action", ["merge", "archive"])
@pytest.mark.parametrize("transport", ["header", "body"])
def test_actual_principal_b_cannot_write_a_stale_authoritative_subject_a_intent(
    signed_router, action, transport
):
    client, subject, headers, _, actor_a = signed_router
    actor_b = "00000000-0000-0000-0000-000000000103"
    before = deepcopy(subject._state)
    stale = headers(actor_b, **({"If-Actor-Match": actor_a} if transport == "header" else {}))
    extra = {"expectedActorSubjectId": actor_a} if transport == "body" else {}
    if action == "merge":
        response = api_merge(client, stale, extra)
    else:
        stale["If-Match"] = 'W/"13"'
        response = client.post(
            "/network-listings/listings/archive-uuid/archive",
            headers=stale,
            json={"reason": "Actual archive reason", "actorName": actor_a, **extra},
        )
    assert response.status_code == 409 and response.json()["detail"] == "ACTOR_CONTEXT_CONFLICT", (
        response.text
    )
    assert subject._state == before


def test_actual_actor_precondition_is_a_constraint_and_never_authenticates_or_grants_role(
    signed_router,
):
    client, subject, headers, staff, actor = signed_router
    before = deepcopy(subject._state)
    refused = api_merge(client, headers(staff, **{"If-Actor-Match": staff}))
    assert refused.status_code == 403 and subject._state == before
    allowed = api_merge(client, headers(**{"If-Actor-Match": actor}))
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["actorSubjectId"] == actor


@pytest.mark.parametrize("case", ["missing", "stale", "hold", "producer-unavailable"])
def test_actual_archive_api_requires_actual_precondition_and_local_retention_guard(
    signed_router, case
):
    client, subject, headers, _, _ = signed_router
    values = headers(**{"If-Match": 'W/"13"'})
    if case == "missing":
        values.pop("If-Match")
    if case == "stale":
        values["If-Match"] = 'W/"12"'
    if case == "hold":
        subject._state["listings"][2]["hasLegalHold"] = True
    if case == "producer-unavailable":
        subject._state["listings"][2]["version"] = None
    before = deepcopy(subject._state)
    response = client.post(
        "/network-listings/listings/archive-uuid/archive",
        headers=values,
        json={"reason": "Actual archive reason"},
    )
    assert (
        response.status_code
        == {"missing": 428, "stale": 409, "hold": 409, "producer-unavailable": 503}[case]
    ), response.text
    assert subject._state == before

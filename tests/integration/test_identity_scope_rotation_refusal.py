"""Actual PG authoritative scope decoding and mounted rotation-denial audit."""

import json
import time
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.security import dependencies as deps
from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    Credentials,
    encode_compact_jwt,
)
from shared.jobs.queue import JobRecord, JobStatus
from tests.identity.test_identity_user_role_management import (
    AUDIENCE,
    TENANT,
    _bootstrap_admin,
    _q,
    _rotate_password,
    _sign_in,
)
from tests.identity.test_identity_user_role_management import stack as stack
from tests.integration.test_oidc_admin_session_bridge import OIDC_ISSUER, OIDC_KEY
from tests.security._offline_job_app import job_app as job_app
from tests.security._offline_job_app import offline_job_app as offline_job_app
from tests.security.test_auth_composition_extension import isolation as isolation


def test_unrelated_admin_edit_never_converts_invalid_scope_to_unrestricted(stack):
    from tests.identity.test_identity_user_role_management import _invited_account

    s = stack
    admin = _bootstrap_admin(s)
    _rotate_password(s, admin)
    target = _invited_account(s, "projection-target", "data_owner")
    _q(
        s,
        "INSERT INTO identity.account_scopes(account_id) VALUES (%s) RETURNING account_id",
        (target,),
    )
    target_headers = _sign_in(s, target)
    admin_headers = _sign_in(s, admin)
    axes = ("modules", "brand_ids", "region_ids", "store_ids", "assigned_area_ids", "heat_zone_ids")
    for axis in axes:
        for malformed in ({"forecastops": True}, "[]", '["integration"]'):
            _q(
                s,
                f"UPDATE identity.account_scopes SET {axis}=%s::jsonb WHERE account_id=%s RETURNING account_id",
                (json.dumps(malformed), target),
            )
            assert not s.boundary.authenticate(
                Credentials.from_headers(target_headers)
            ).authenticated
            before = _q(
                s,
                "SELECT display_name,sc.* FROM identity.accounts a JOIN identity.account_scopes sc USING(account_id) WHERE account_id=%s",
                (target,),
            )
            response = s.client.post(
                "/api/v1/operator/users",
                headers=admin_headers,
                json={"subjectId": target, "roles": ["data_owner"], "name": "unrelated-edit"},
            )
            assert response.status_code == 422
            assert (
                _q(
                    s,
                    "SELECT display_name,sc.* FROM identity.accounts a JOIN identity.account_scopes sc USING(account_id) WHERE account_id=%s",
                    (target,),
                )
                == before
            )
            assert not s.boundary.authenticate(
                Credentials.from_headers(target_headers)
            ).authenticated
        # Each following axis has its own valid before-state, not cached authority.
        _q(
            s,
            f"UPDATE identity.account_scopes SET {axis}='[]'::jsonb WHERE account_id=%s RETURNING account_id",
            (target,),
        )
    _q(
        s,
        "UPDATE identity.account_scopes SET modules=%s::jsonb WHERE account_id=%s RETURNING account_id",
        (json.dumps("[]"), target),
    )
    # Explicit valid replacement is allowed, not a silently inferred empty grant.
    response = s.client.post(
        "/api/v1/operator/users",
        headers=_sign_in(s, admin),
        json={"subjectId": target, "roles": ["data_owner"], "scope": {"modules": ["forecastops"]}},
    )
    assert response.status_code == 200
    outcome = s.boundary.authenticate(Credentials.from_headers(target_headers))
    assert outcome.authenticated and not outcome.principal.scope.permits_module("integration")


@pytest.mark.parametrize("provider", ["local", "oidc"])
def test_sql_malformed_scope_is_authentication_refusal_not_unrestricted(
    stack, provider, monkeypatch, job_app
):
    s = stack
    actor = _bootstrap_admin(s)
    _rotate_password(s, actor)
    _q(
        s,
        "INSERT INTO identity.account_roles(account_id,role,granted_by) VALUES (%s,'data_owner','fixture') RETURNING account_id",
        (actor,),
    )
    _q(
        s,
        "INSERT INTO identity.account_scopes(account_id) VALUES (%s) RETURNING account_id",
        (actor,),
    )
    if provider == "local":
        boundary = s.boundary
        headers = _sign_in(s, actor)
    else:
        _q(
            s,
            "INSERT INTO identity.federated_identities(account_id,issuer,subject,linked_by) VALUES (%s,%s,'fixture-sub','fixture') RETURNING account_id",
            (actor, OIDC_ISSUER),
        )
        boundary = AuthenticationBoundary(
            AuthBoundaryConfig(
                oidc_issuer=OIDC_ISSUER,
                oidc_audiences=frozenset({AUDIENCE}),
                oidc_signing_keys={OIDC_KEY.kid: OIDC_KEY},
                identity_store=s.identity,
            ),
            audit_log=s.audit,
        )
        now = int(time.time())
        headers = {
            "authorization": "Bearer "
            + encode_compact_jwt(
                {
                    "iss": OIDC_ISSUER,
                    "sub": "fixture-sub",
                    "aud": AUDIENCE,
                    "iat": now,
                    "exp": now + 300,
                },
                OIDC_KEY,
            )
        }
    _, bundle, app = job_app
    monkeypatch.setattr(deps, "default_boundary", lambda: boundary)
    failures = []
    client = TestClient(app)
    try:
        # Missing-row and explicit empty axes retain documented semantics.
        assert boundary.authenticate(Credentials.from_headers(headers)).authenticated
        bad = [
            ("modules", {"forecastops": True}),
            ("modules", [""]),
            ("modules", ["integration", 7]),
            ("brand_ids", {}),
            ("region_ids", False),
            ("store_ids", [None]),
            ("assigned_area_ids", 1),
            ("heat_zone_ids", ["", "zone"]),
            ("clearance", "unrecognized"),
        ]
        for axis, value in bad:
            assignments = ",".join(
                f"{a}='[]'::jsonb"
                for a in [
                    "modules",
                    "brand_ids",
                    "region_ids",
                    "store_ids",
                    "assigned_area_ids",
                    "heat_zone_ids",
                ]
            )
            _q(
                s,
                f"UPDATE identity.account_scopes SET {assignments},clearance='CONFIDENTIAL' WHERE account_id=%s RETURNING account_id",
                (actor,),
            )
            if axis == "clearance":
                _q(
                    s,
                    "UPDATE identity.account_scopes SET clearance=%s WHERE account_id=%s RETURNING account_id",
                    (value, actor),
                )
            else:
                _q(
                    s,
                    f"UPDATE identity.account_scopes SET {axis}=%s::jsonb WHERE account_id=%s RETURNING account_id",
                    (json.dumps(value), actor),
                )
            outcome = boundary.authenticate(Credentials.from_headers(headers))
            if outcome.authenticated:
                failures.append(f"{axis}:{type(value).__name__} authenticated")
            before = deepcopy(bundle.job_queue._jobs)
            response = client.post(
                "/api/v1/jobs",
                headers=headers,
                json={"job_type": "external-fetch", "payload": {"provider_id": "fixture-only"}},
            )
            if response.status_code not in {401, 403} or bundle.job_queue._jobs != before:
                failures.append(f"{axis} enqueue={response.status_code}/mutated")
        _q(
            s,
            "DELETE FROM identity.account_scopes WHERE account_id=%s RETURNING account_id",
            (actor,),
        )
        assert boundary.authenticate(Credentials.from_headers(headers)).authenticated
        assert (
            client.post(
                "/api/v1/jobs",
                headers=headers,
                json={"job_type": "external-fetch", "payload": {"provider_id": "fixture-only"}},
            ).status_code
            == 202
        )
        assert not failures, failures
    finally:
        client.close()


@pytest.mark.parametrize("alias", ["/jobs", "/api/v1/jobs"])
def test_rotation_required_jobs_are_denied_once_with_verified_actor_and_domain_audit(
    stack, monkeypatch, alias, job_app, offline_job_app
):
    s = stack
    actor = _bootstrap_admin(s)
    headers = {**_sign_in(s, actor), "x-correlation-id": "rotation-job-denial"}
    assert s.boundary.authenticate(Credentials.from_headers(headers)).authenticated
    _, bundle, app = job_app
    audit = offline_job_app[3]
    monkeypatch.setattr(deps, "default_boundary", lambda: s.boundary)
    record = JobRecord(
        job_type="batch-listing-intake",
        status=JobStatus.PARTIAL,
        payload={"tenant_id": TENANT, "items": []},
        correlation_id="offline",
    )
    bundle.job_queue._jobs[record.job_id] = record
    idem = "rotation-replay"
    bundle.job_queue._idempotency_index[
        f"batch-listing-intake:v2:{len(TENANT)}:{TENANT}:{len(idem)}:{idem}"
    ] = record.job_id
    before = deepcopy(bundle.job_queue._jobs)
    errors = []
    client = TestClient(app)
    try:
        for method, path, extra in [
            ("GET", f"{alias}/{record.job_id}", {}),
            ("POST", alias, {}),
            ("POST", alias, {"Idempotency-Key": idem}),
            ("POST", f"{alias}/{record.job_id}/retries", {}),
        ]:
            prior = len(audit.list_events())
            kwargs = (
                {}
                if method == "GET"
                else {
                    "json": {"job_type": "batch-listing-intake", "payload": {"items": []}}
                    if path == alias
                    else {}
                }
            )
            response = client.request(method, path, headers={**headers, **extra}, **kwargs)
            assert response.status_code == 403 and "PASSWORD_CHANGE_REQUIRED" in response.text
            assert bundle.job_queue._jobs == before
            events = audit.list_events()[prior:]
            denials = [
                e
                for e in events
                if e.event_type == "security.authorization" and e.outcome == "deny"
            ]
            if len(denials) != 1:
                errors.append(f"{method} {path} canonical={len(denials)}")
            else:
                e = denials[0]
                assert (
                    e.actor == actor
                    and e.correlation_id == "rotation-job-denial"
                    and e.metadata["source_ip"]
                    and e.metadata["reason"] == "PASSWORD_CHANGE_REQUIRED"
                )
            if path == alias:
                domain = [
                    e for e in events if e.event_type == "job.enqueue" and e.outcome == "denied"
                ]
                if len(domain) != 1:
                    errors.append(f"enqueue domain={len(domain)}")
        assert not errors, errors
    finally:
        client.close()

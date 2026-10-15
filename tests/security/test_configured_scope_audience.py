"""Real signed service tokens through config_from_env, no live credentials."""

import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.security import dependencies as deps
from modules.opsboard.auth import (
    AuthenticationBoundary,
    Credentials,
    config_from_env,
    encode_compact_jwt,
)
from shared.jobs.queue import JobRecord
from tests.security._offline_job_app import job_app as job_app
from tests.security._offline_job_app import offline_job_app as offline_job_app
from tests.security.test_auth_composition_extension import TENANT
from tests.security.test_auth_composition_extension import isolation as isolation


def configured(mapping, audience="123"):
    return config_from_env(
        {
            "ODP_AUTH_MODE": "local",
            "ODP_AUTH_SERVICE_ISSUER": "https://offline.invalid",
            "ODP_AUTH_SERVICE_AUDIENCES": audience,
            "ODP_AUTH_HS256_KEYS": "offline:fixture-only-signing-secret",
            "ODP_AUTH_PRINCIPAL_MAP": json.dumps({"offline-user": mapping}),
        }
    )


@pytest.mark.parametrize(
    "declaration",
    [None, False, 17, [], {"roles": ["data_owner"], "scope": {"modules": {"integration": True}}}],
)
def test_invalid_declaration_cannot_fall_through_secondary_bound_roles(declaration):
    env = {
        "ODP_AUTH_MODE": "local",
        "ODP_AUTH_SERVICE_ISSUER": "https://offline.invalid",
        "ODP_AUTH_SERVICE_AUDIENCES": "api",
        "ODP_AUTH_HS256_KEYS": "offline:fixture-only-signing-secret",
        "ODP_AUTH_PRINCIPAL_MAP": json.dumps({"offline-user": declaration}),
        "ODP_AUTH_SUBJECT_ROLE_BINDINGS": json.dumps({"offline-user": ["data_owner"]}),
    }
    config = config_from_env(env)
    result = AuthenticationBoundary(config).authenticate(
        Credentials(bearer_token=signed(config, "api"))
    )
    assert not result.authenticated and result.principal.roles == frozenset()


def signed(config, aud):
    now = datetime.now(UTC).timestamp()
    return encode_compact_jwt(
        {
            "iss": "https://offline.invalid",
            "sub": "offline-user",
            "aud": aud,
            "iat": now,
            "exp": now + 300,
        },
        config.service_signing_keys["offline"],
    )


@pytest.mark.parametrize("aud", [[123], ["123", 7], ["123", None], ["123", True], ["123", {}]])
def test_signed_audience_members_are_runtime_strings_not_coercible_values(aud):
    config = configured({"roles": ["auditor"], "scope": {"tenant_id": TENANT}})
    boundary = AuthenticationBoundary(config)
    for healthy in ["123", ["123"], ["other", "123"]]:
        assert boundary.authenticate(
            Credentials(bearer_token=signed(config, healthy))
        ).authenticated
    result = boundary.authenticate(Credentials(bearer_token=signed(config, aud)))
    assert not result.authenticated and result.reason.value == "audience_mismatch"


@pytest.mark.parametrize("alias", ["/jobs", "/api/v1/jobs"])
def test_malformed_deployment_scope_never_becomes_empty_unrestricted_or_falls_back(
    monkeypatch, alias, job_app
):
    _, bundle, app = job_app
    boundary = None
    errors = []
    monkeypatch.setattr(deps, "default_boundary", lambda: boundary)
    bad = [
        ("modules", {"forecastops": True}),
        ("modules", 17),
        ("modules", True),
        ("modules", ["integration", {}]),
        ("modules", [""]),
        ("modules", ","),
        ("store_ids", {"store-A": True}),
        ("brand_ids", ["brand-A", 7]),
        ("region_ids", False),
        ("assigned_area_ids", [None]),
        ("heat_zone_ids", {}),
        ("clearance", {}),
        ("scope", "broken"),
    ]
    client = TestClient(app)
    try:
        for axis, value in bad + [
            ("modules", ["forecastops"]),
            ("modules", ["integration"]),
            ("modules", []),
            ("modules", None),
            ("modules", ""),
            ("modules", "integration"),
        ]:
            invalid = (axis, value) in bad
            mapping = {
                "roles": ["data_owner"],
                "scope": {"tenant_id": TENANT, "modules": ["integration"]},
            }
            if axis == "scope":
                mapping = {"roles": ["data_owner"], "tenant_id": TENANT, "scope": value}
            else:
                mapping["scope"][axis] = value
            config = configured(mapping, "api")
            boundary = AuthenticationBoundary(config)
            token = signed(config, "api")
            headers = {"authorization": "Bearer " + token}
            outcome = boundary.authenticate(Credentials(bearer_token=token))
            if invalid and outcome.authenticated:
                errors.append(f"{axis}:{type(value).__name__} authenticated")
            allowed = not invalid and value != ["forecastops"]
            record = JobRecord(
                job_type="external-fetch",
                payload={"tenant_id": TENANT, "provider_id": "private-provider-marker"},
                correlation_id="offline",
            )
            bundle.job_queue._jobs[record.job_id] = record
            before = deepcopy(bundle.job_queue._jobs)
            read = client.get(f"{alias}/{record.job_id}", headers=headers)
            create = client.post(
                alias,
                headers=headers,
                json={"job_type": "external-fetch", "payload": {"provider_id": "offline-source"}},
            )
            idem = f"scope-replay-{len(bundle.job_queue._jobs)}"
            bundle.job_queue._idempotency_index[
                f"external-fetch:v2:{len(TENANT)}:{TENANT}:{len(idem)}:{idem}"
            ] = record.job_id
            replay = client.post(
                alias,
                headers={**headers, "Idempotency-Key": idem},
                json={"job_type": "external-fetch", "payload": {"provider_id": "offline-source"}},
            )
            if allowed:
                assert read.status_code == 200 and create.status_code == 202
                assert (
                    replay.status_code == 202
                    and replay.json()["created"] is False
                    and replay.json()["job_id"] == record.job_id
                )
            else:
                if read.status_code not in {401, 403} or "private-provider-marker" in read.text:
                    errors.append(f"{axis} read={read.status_code}")
                if create.status_code not in {401, 403} or bundle.job_queue._jobs != before:
                    errors.append(f"{axis} create={create.status_code}/mutated")
                if replay.status_code not in {401, 403} or "private-provider-marker" in replay.text:
                    errors.append(f"{axis} replay={replay.status_code}/exposed")
        assert not errors, errors
    finally:
        client.close()

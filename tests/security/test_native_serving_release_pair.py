"""Real native wrapper, synthetic HTTP only; source/profile identity is not a receipt flag."""

from __future__ import annotations

import time
from typing import Any

import pytest

from delivery_toolchain.release import consume_dev_smoke as native


@pytest.mark.parametrize(
    "field",
    [
        None,
        "release_sha",
        "web_release_sha",
        "manifest_digest",
        "web_manifest_digest",
        "release_profile",
        "web_release_profile",
        "release_profile_valid",
        "timeout",
        "extra-cookie",
    ],
)
def test_all_serving_pair_fields_and_unknown_custody_refuse_before_more_gate_transport(
    field: str | None,
) -> None:
    sha = "a" * 40
    digest = "sha256:" + "b" * 64
    payload = {
        "release_sha": sha,
        "web_release_sha": sha,
        "manifest_digest": digest,
        "web_manifest_digest": digest,
        "release_profile": "dev-admin",
        "web_release_profile": "dev-admin",
        "release_profile_valid": True,
    }
    if field in payload:
        payload[field] = False if field == "release_profile_valid" else "wrong"
    calls = []

    class Client:
        def request(self, method: str, path: str, **kwargs: Any) -> Any:
            calls.append((method, path, (kwargs.get("headers") or {}).get("cookie")))
            if path == "/login":
                return native.gate.HttpResponse(
                    status=200,
                    payload={"ok": True, "subject": "offline-user"},
                    cookies={native._SESSION_COOKIE: "offline-session-one"},
                )
            if path == "/api/v1/platform/release-identity":
                assert (
                    (kwargs["headers"]["cookie"]) == f"{native._SESSION_COOKIE}=offline-session-one"
                )
                if field == "timeout":
                    raise TimeoutError("OFFLINE")
                return native.gate.HttpResponse(
                    status=200,
                    payload=payload,
                    cookies={native._SESSION_COOKIE: "offline-session-two"}
                    if field == "extra-cookie"
                    else {},
                )
            if path == "/auth/logout":
                return native.gate.HttpResponse(
                    status=200, payload={"ok": True}, cookies={native._SESSION_COOKIE: ""}
                )
            return native.gate.HttpResponse(status=401)

    web = native._ObservedWeb(
        Client(),
        execution_id="offline",
        deadline=time.monotonic() + 30,
        cleanup_deadline=time.monotonic() + 60,
        login_pair=("offline-user", "offline-password"),
        expected_release=(sha, digest),
    )
    result = web.request(
        "POST",
        "/login",
        body={
            "username": "offline-user",
            "password": "offline-password",
            "returnTo": native._FORM_RETURN_TO,
        },
        headers={"content-type": "application/json", "origin": native.WEB_ORIGIN},
    )
    assert result.status == (200 if field is None else 0)
    assert web.release_identity_verified == (field is None)
    if field is not None:
        assert web.uncertainty_latched
        before = len(calls)
        web.request(
            "POST",
            "/login",
            body={
                "username": "offline-user",
                "password": "offline-password",
                "returnTo": native._FORM_RETURN_TO,
            },
        )
        assert len(calls) == before
    assert web.cleanup_owned_sessions() == (field is None)
    cookies = [
        cookie for method, path, cookie in calls if method == "POST" and path == "/auth/logout"
    ]
    assert len(cookies) == len(set(cookies)) == (2 if field == "extra-cookie" else 1)

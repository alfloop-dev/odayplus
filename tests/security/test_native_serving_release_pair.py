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


@pytest.mark.parametrize("body_failed", [False, True])
def test_serving_extra_cookie_at_final_deadline_keeps_once_only_cleanup(
    monkeypatch: Any, body_failed: bool,
) -> None:
    ticks = [100.0]
    monkeypatch.setattr(native.time, "monotonic", lambda: ticks[0])
    calls = []

    class Client:
        def request(self, method: str, path: str, **kwargs: Any) -> Any:
            calls.append((method, path, (kwargs.get("headers") or {}).get("cookie")))
            if path == "/login":
                return native.gate.HttpResponse(status=200, payload={"ok": True, "subject": "offline-user"},
                    cookies={native._SESSION_COOKIE: "offline-session-one"})
            if path == "/api/v1/platform/release-identity":
                ticks[0] = 110.0  # EXACT work deadline; response cookie already safely known
                return native.gate.HttpResponse(status=0 if body_failed else 200,
                    error="sanitized body failure" if body_failed else None,
                    cookies={native._SESSION_COOKIE: "offline-session-two"})
            if path == "/auth/logout":
                return native.gate.HttpResponse(status=200, payload={"ok": True}, cookies={native._SESSION_COOKIE: ""})
            return native.gate.HttpResponse(status=401)

    web = native._ObservedWeb(Client(), execution_id="offline", deadline=110.0, cleanup_deadline=130.0,
        login_pair=("offline-user", "offline-password"), expected_release=("a" * 40, "sha256:" + "b" * 64))
    result = web.request("POST", "/login", body={"username": "offline-user", "password": "offline-password", "returnTo": native._FORM_RETURN_TO},
        headers={"content-type": "application/json", "origin": native.WEB_ORIGIN})
    assert result.status == 0 and web.uncertainty_latched and not web.release_identity_verified
    before = len(calls)
    web.request("GET", "/api/v1/platform/release-identity")
    assert len(calls) == before
    assert not web.cleanup_owned_sessions()
    cookies = [cookie for method, path, cookie in calls if method == "POST" and path == "/auth/logout"]
    assert sorted(cookies) == sorted([f"{native._SESSION_COOKIE}=offline-session-one", f"{native._SESSION_COOKIE}=offline-session-two"])
    assert len(cookies) == len(set(cookies)) == 2
    assert not web.cleanup_owned_sessions()
    assert len([x for x in calls if x[:2] == ("POST", "/auth/logout")]) == 2


@pytest.mark.parametrize("read", ["/auth/session", "/api/v1/auth/principal"])
def test_cookie_first_observed_during_cleanup_is_owned_and_drained_once(read: str) -> None:
    calls = []
    issued = []
    class Client:
        def request(self, method: str, path: str, **kwargs: Any) -> Any:
            cookie = (kwargs.get("headers") or {}).get("cookie", "")
            calls.append((method, path, cookie))
            if path == "/login":
                return native.gate.HttpResponse(status=200, payload={"ok": True, "subject": "offline-user"},
                    cookies={native._SESSION_COOKIE: "owned-a"})
            if path == "/auth/logout":
                return native.gate.HttpResponse(status=200, payload={"ok": True}, cookies={native._SESSION_COOKIE: ""})
            if path == read and not issued:
                issued.append(True)
                return native.gate.HttpResponse(status=401, cookies={native._SESSION_COOKIE: "owned-b"})
            return native.gate.HttpResponse(status=401)
    now = time.monotonic()
    web = native._ObservedWeb(Client(), execution_id="offline", deadline=now+30, cleanup_deadline=now+60,
        login_pair=("offline-user", "offline-password"))
    web.request("POST", "/login", body={"username":"offline-user", "password":"offline-password", "returnTo":native._FORM_RETURN_TO})
    assert not web.cleanup_owned_sessions() and web.uncertainty_latched
    assert [cookie for method,path,cookie in calls if path=="/auth/logout"] == [f"{native._SESSION_COOKIE}=owned-a", f"{native._SESSION_COOKIE}=owned-b"]
    before = len(calls)
    assert not web.cleanup_owned_sessions() and len(calls) == before

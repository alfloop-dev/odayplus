"""Real local TLS header custody + production owners, synthetic authority only."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from delivery_toolchain.release import recover_dev_smoke as r
from tests.security.test_dev_smoke_recovery import owned_sessions as owned_sessions
from tests.security.test_dev_smoke_recovery import recovery_http_reply
from tests.security.test_dev_smoke_recovery_gate import foreground_gate as foreground_gate
from tests.security.test_dev_smoke_recovery_gate import tls_recovery_server as tls_recovery_server

_ACTUAL_GATE_ROUNDTRIP = r._ForegroundGateHTTPSClient._roundtrip
COOKIE = "offline-new-response-cookie"
HEADER = (
    f"Set-Cookie: __Host-oday_web_session={COOKIE}; Path=/; Secure; HttpOnly; SameSite=Lax\r\n"
).encode()


@pytest.mark.parametrize("path", ["/auth/session", "/auth/logout"])
def test_actual_foreground_gate_tls_retains_non_login_cookie_before_refusal(
    tls_recovery_server, path
):
    retained = []
    with tls_recovery_server(
        recovery_http_reply(b'{"ok":true,"subject":"odp-dev-smoke"}', extra=HEADER)
    ) as (origin, ca, requests):
        owner = SimpleNamespace(
            _config=SimpleNamespace(web_url=origin),
            _correlation="corr-offline",
            _budget=SimpleNamespace(_active=("gate",), call_deadline=time.monotonic() + 2),
            _bytes=0,
            _retain_cookies=retained.append,
            _cookies={},
            _unknown_login=False,
        )
        client = r._ForegroundGateHTTPSClient(
            owner, api=False, origin=origin, address="127.0.0.1", ca_pem=ca
        )
        with pytest.raises(r.RecoveryRefused):
            client._roundtrip(
                "POST" if path.endswith("logout") else "GET",
                path,
                authenticated=False,
                body={} if path.endswith("logout") else None,
                headers={"cookie": "__Host-oday_web_session=owned-a"},
            )
        assert retained == [{"__Host-oday_web_session": COOKIE}] and owner._unknown_login
        assert len(requests) == 1


def test_owner_cleanup_logout_new_cookie_is_retained_and_cleaned_once(
    owned_sessions, tls_recovery_server, monkeypatch
):
    actual = r.OwnedSessionHTTPSAttempt.execute_session
    with owned_sessions() as (owner, budget, clock, receipts, p, calls, original, target):
        budget.begin("preflight")
        owner.login("original")
        budget.finish(outcome="completed")
        budget.begin("quarantine")
        budget.finish(outcome="completed")  # synthetic no-server quarantine, NOT authority
        mocked = r.OwnedSessionHTTPSAttempt.execute_session
        with tls_recovery_server(recovery_http_reply(b'{"ok":true}', extra=HEADER)) as (
            origin,
            ca,
            requests,
        ):
            owner._origin, owner._address, owner._ca = origin, "127.0.0.1", ca

            def execute(self, operation, **kwargs):
                cookie = kwargs["cookie"]
                if operation == "logout" and cookie == "offline-original-cookie":
                    calls.append((operation, cookie, kwargs["deadline"], "", ""))
                    return actual(self, operation, **kwargs)
                if cookie == COOKIE:
                    calls.append((operation, cookie, kwargs["deadline"], "", ""))
                    return (
                        r.RecoveryHTTPResult(200, {"ok": True})
                        if operation == "logout"
                        else r.RecoveryHTTPResult(401, {"error": {"code": "WEB_SESSION_REQUIRED"}})
                    )
                return mocked(self, operation, **kwargs)

            monkeypatch.setattr(r.OwnedSessionHTTPSAttempt, "execute_session", execute)
            with pytest.raises(r.RecoveryActionRefused) as caught:
                owner.cleanup()
            assert caught.value.outcome == "unknown" and len(requests) == 1
        logouts = [cookie for operation, cookie, *_ in calls if operation == "logout"]
        assert logouts == ["offline-original-cookie", COOKIE] and len(logouts) == len(set(logouts))


@pytest.mark.parametrize(
    "attributes,value,retained",
    [
        ("Path=/; Secure; SameSite=Lax", COOKIE, True),
        ("Path=/; Secure; HttpOnly; SameSite=Strict", COOKIE, True),
        ("Path=/; Secure; HttpOnly; SameSite=Lax", "unsafe,cookie", False),
    ],
)
def test_actual_owned_observation_bad_attributes_keep_custody_unknown(
    owned_sessions, tls_recovery_server, monkeypatch, attributes, value, retained
):
    with owned_sessions() as (owner, budget, clock, receipts, p, calls, original, target):
        budget.begin("preflight")
        owner.login("original")
        header = f"Set-Cookie: __Host-oday_web_session={value}; {attributes}\r\n".encode()
        with tls_recovery_server(recovery_http_reply(b'{"ok":true}', extra=header)) as (
            origin,
            ca,
            requests,
        ):
            owner._origin, owner._address, owner._ca = origin, "127.0.0.1", ca
            with pytest.raises(r.RecoveryActionRefused):
                owner.observe_accounts(p)  # Actual observation HTTPS, first release read.
            assert len(requests) == 1
        mocked = r.OwnedSessionHTTPSAttempt.execute_session

        def execute(self, operation, **kwargs):
            if kwargs["cookie"] == COOKIE:
                calls.append((operation, COOKIE, kwargs["deadline"], "", ""))
                return (
                    r.RecoveryHTTPResult(200, {"ok": True})
                    if operation == "logout"
                    else r.RecoveryHTTPResult(401, {"error": {"code": "WEB_SESSION_REQUIRED"}})
                )
            return mocked(self, operation, **kwargs)

        monkeypatch.setattr(r.OwnedSessionHTTPSAttempt, "execute_session", execute)
        budget.begin("quarantine")
        budget.finish(outcome="completed")  # Fixture-only phase progress, no authority/effect.
        with pytest.raises(r.RecoveryActionRefused) as caught:
            owner.cleanup()
        assert caught.value.outcome == "unknown" and owner._unknown_login
        logouts = [cookie for operation, cookie, *_ in calls if operation == "logout"]
        assert logouts == ["offline-original-cookie", *([COOKIE] if retained else [])]
        before = list(calls)
        with pytest.raises(r.RecoveryRefused):
            owner.cleanup()
        assert calls == before


@pytest.mark.parametrize("mode", ["raised-cap", "returned-defect"])
def test_actual_observation_header_parse_loss_never_claims_completed_cleanup(
    owned_sessions, tls_recovery_server, monkeypatch, mode
):
    with owned_sessions() as (owner, budget, clock, receipts, p, calls, original, target):
        budget.begin("preflight")
        owner.login("original")
        # B precedes the aggregate cap violation, but getresponse never returns
        # parsed headers. Its value is NOT claimed recovered or revocable.
        extra = (
            b"Malformed header line\r\n" + HEADER
            if mode == "returned-defect"
            else HEADER + (b"X-Large: " + b"x" * 33000 + b"\r\n") * 2
        )
        with tls_recovery_server(recovery_http_reply(b'{"ok":true}', extra=extra)) as (
            origin,
            ca,
            requests,
        ):
            owner._origin, owner._address, owner._ca = origin, "127.0.0.1", ca
            with pytest.raises(r.RecoveryActionRefused) as observation:
                owner.observe_accounts(p)
            assert len(requests) == 1
        budget.begin("quarantine")
        budget.finish(outcome="completed")  # Synthetic fixture progress only.
        with pytest.raises(r.RecoveryActionRefused) as cleanup:
            owner.cleanup()
        assert observation.value.outcome == cleanup.value.outcome == "unknown"
        assert owner._unknown_login
        logouts = [cookie for operation, cookie, *_ in calls if operation == "logout"]
        assert logouts == ["offline-original-cookie"]
        before = list(calls)
        with pytest.raises(r.RecoveryRefused):
            owner.cleanup()
        assert calls == before


@pytest.mark.parametrize("mode", ["raised-cap", "returned-defect"])
def test_foreground_gate_actual_header_parser_loss_keeps_cleanup_unknown(
    foreground_gate, tls_recovery_server, monkeypatch, mode
):
    with foreground_gate() as (
        driver,
        actions,
        owner,
        budget,
        p,
        calls,
        sessions,
        original,
        target,
        events,
    ):
        fake = r._ForegroundGateHTTPSClient._roundtrip
        triggered, gate_logouts, known = [], [], set()
        extra = (
            b"Malformed header line\r\n" + HEADER
            if mode == "returned-defect"
            else HEADER + (b"X-Large: " + b"x" * 60000 + b"\r\n") * 9
        )
        with tls_recovery_server(recovery_http_reply(b'{"ok":true}', extra=extra)) as (
            origin,
            ca,
            requests,
        ):

            def route(client, method, path, **kwargs):
                cookie = (kwargs.get("headers") or {}).get("cookie", "").partition("=")[2]
                if not client._api and path == "/auth/logout":
                    gate_logouts.append(cookie)
                if (
                    not client._api
                    and path == "/auth/session"
                    and cookie
                    and not triggered
                    and budget._active[0] == "gate"
                ):
                    triggered.append(True)
                    known.update(driver._cookies)
                    known.update(value for value in owner._cookies.values() if value)
                    actual = r._ForegroundGateHTTPSClient(
                        driver, api=False, origin=origin, address="127.0.0.1", ca_pem=ca
                    )
                    return _ACTUAL_GATE_ROUNDTRIP(actual, method, path, **kwargs)
                return fake(client, method, path, **kwargs)

            monkeypatch.setattr(r._ForegroundGateHTTPSClient, "_roundtrip", route)
            with pytest.raises(r.RecoveryRefused):
                driver.execute(actions)
            assert triggered and len(requests) == 1
        result = driver.stop_and_cleanup(actions)
        assert result["own_session_cleanup"] == "unknown" and driver._unknown_login
        owner_logouts = [cookie for operation, cookie, *_ in sessions if operation == "logout"]
        logouts = gate_logouts + owner_logouts
        assert known and set(logouts) == known and len(logouts) == len(set(logouts))
        assert COOKIE not in logouts  # Hidden B was never safely recovered.
        before = list(calls), list(sessions)
        driver.stop_and_cleanup(actions)
        assert (calls, sessions) == before


@pytest.mark.parametrize("phase", ["ordinary", "diagnostic-login"])
def test_actual_foreground_single_malformed_cookie_refuses_without_http_defects(
    foreground_gate, tls_recovery_server, monkeypatch, phase
):
    import http.client
    import io

    header = b"Set-Cookie: __Host-oday_web_session\r\n"
    parsed = http.client.parse_headers(io.BytesIO(header + b"\r\n"))
    assert not parsed.defects
    from delivery_toolchain.e2e import check_live_e2e_gate as gate

    assert gate._set_cookie_pairs(parsed) == (("", ""),)
    with foreground_gate() as (
        driver,
        actions,
        owner,
        budget,
        p,
        calls,
        sessions,
        original,
        target,
        events,
    ):
        fake = r._ForegroundGateHTTPSClient._roundtrip
        triggered, known, gate_logouts = [], set(), []
        status = 401 if phase == "diagnostic-login" else 200
        body = b'{"error":{"code":"AUTH_INVALID_CREDENTIALS"}}' if status == 401 else b'{"ok":true}'
        with tls_recovery_server(recovery_http_reply(body, status=status, extra=header)) as (
            origin,
            ca,
            requests,
        ):

            def route(client, method, path, **kwargs):
                cookie = (kwargs.get("headers") or {}).get("cookie", "").partition("=")[2]
                if not client._api and path == "/auth/logout":
                    gate_logouts.append(cookie)
                diagnostic = path == "/login" and (kwargs.get("body") or {}).get(
                    "password"
                ) not in {original.password, target.password}
                ordinary = path == "/auth/session" and cookie
                if (
                    not client._api
                    and not triggered
                    and budget._active[0] == "gate"
                    and (diagnostic if phase == "diagnostic-login" else ordinary)
                ):
                    triggered.append(len(calls))
                    known.update(driver._cookies)
                    known.update(v for v in owner._cookies.values() if v)
                    actual = r._ForegroundGateHTTPSClient(
                        driver, api=False, origin=origin, address="127.0.0.1", ca_pem=ca
                    )
                    return _ACTUAL_GATE_ROUNDTRIP(actual, method, path, **kwargs)
                return fake(client, method, path, **kwargs)

            monkeypatch.setattr(r._ForegroundGateHTTPSClient, "_roundtrip", route)
            with pytest.raises(r.RecoveryRefused):
                driver.execute(actions)
            assert triggered and len(requests) == 1
        result = driver.stop_and_cleanup(actions)
        assert driver._unknown_login and result["own_session_cleanup"] == "unknown"
        assert not any(c[2] == "/login" for c in calls[triggered[0] :])
        logouts = gate_logouts + [
            cookie for operation, cookie, *_ in sessions if operation == "logout"
        ]
        assert known and set(logouts) == known and len(logouts) == len(set(logouts))
        before = list(calls), list(sessions)
        driver.stop_and_cleanup(actions)
        assert (calls, sessions) == before


@pytest.mark.parametrize("phase", ["ordinary", "cleanup"])
def test_foreground_canonical_or_cleanup_read_drains_new_cookie_once(
    foreground_gate, monkeypatch, phase
):
    from delivery_toolchain.e2e import check_live_e2e_gate as gate

    with foreground_gate() as (
        driver,
        actions,
        owner,
        budget,
        p,
        calls,
        sessions,
        original,
        target,
        events,
    ):
        transport = r._ForegroundGateHTTPSClient._roundtrip
        issued = []
        logouts = []

        def reply(client, method, path, **kwargs):
            cookie = (kwargs.get("headers") or {}).get("cookie", "").partition("=")[2]
            if not client._api and path == "/auth/logout":
                logouts.append(cookie)
            if cookie == COOKIE:
                return (
                    gate.HttpResponse(200, {"ok": True}, cookies={"__Host-oday_web_session": ""})
                    if path == "/auth/logout"
                    else gate.HttpResponse(401, {})
                )
            result = transport(client, method, path, **kwargs)
            if (
                not client._api
                and path == "/auth/session"
                and cookie
                and not issued
                and (
                    (phase == "ordinary" and budget._active[0] == "gate" and result.status == 200)
                    or (phase == "cleanup" and budget._active[0] == "cleanup")
                )
            ):
                issued.append(True)
                return gate.HttpResponse(
                    result.status, result.payload, cookies={"__Host-oday_web_session": COOKIE}
                )
            return result

        monkeypatch.setattr(r._ForegroundGateHTTPSClient, "_roundtrip", reply)
        if phase == "ordinary":
            with pytest.raises(r.RecoveryRefused):
                driver.execute(actions)
            result = driver.stop_and_cleanup(actions)
            assert result["own_session_cleanup"] == "unknown"
        else:
            # Seed one actual owner inventory in synthetic completed progress;
            # no source/gate/credential effect authority claimed.
            driver._retain_cookies({"__Host-oday_web_session": "offline-cleanup-base"})
            budget.begin("quarantine")
            budget.finish(outcome="completed")
            with pytest.raises(r.RecoveryActionRefused):
                owner.cleanup(gate_sessions=driver)
        assert issued and COOKIE in logouts and len(logouts) == len(set(logouts))

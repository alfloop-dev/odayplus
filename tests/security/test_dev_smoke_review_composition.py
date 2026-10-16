"""Advisory-review regressions over synthetic HTTP/isolated TLS, never live custody."""
from __future__ import annotations

import json
import time
from typing import Any

import pytest

from delivery_toolchain.release import consume_dev_smoke as c
from delivery_toolchain.release import recover_dev_smoke as r
from tests.security.test_dev_smoke_recovery import owned_sessions as owned_sessions
from tests.security.test_dev_smoke_recovery import recovery_http_reply
from tests.security.test_dev_smoke_recovery import tls_recovery_server as tls_recovery_server


@pytest.mark.parametrize("form", [False, True])
def test_diagnostic_session_reply_latches_before_next_canonical_login(monkeypatch: Any, form: bool) -> None:
    from tests.e2e import test_live_e2e_gate_dev_admin as f

    monkeypatch.setattr(f.base, "WEB_URL", c.WEB_ORIGIN)

    class Web(f.AdminWeb):
        bad_index: int | None = None

        def request(self, method: str, path: str, **kwargs: Any):
            response = super().request(method, path, **kwargs)
            body = kwargs.get("body") or {}
            is_form = (kwargs.get("headers") or {}).get("content-type") == "application/x-www-form-urlencoded"
            if method == "POST" and path == "/login" and is_form == form and body.get("password") != f.PASSWORD:
                self.bad_index = len(self.headers_seen) - 1
                self.logged_out = False  # synthetic faulty server created an unexpected session
                value = f.FRESH_SESSION_VALUE if form else f.SESSION_VALUE
                return c.gate.HttpResponse(303 if form else 200,
                    {"ok": True, "subject": body["username"]},
                    location=c.WEB_ORIGIN + "/operator?view=admin" if form else "",
                    cookies={f.SESSION_COOKIE: value})
            return response

    client = Web(f.web_routes())
    now = time.monotonic()
    observed = c._ObservedWeb(client, execution_id="offline", deadline=now + 20, cleanup_deadline=now + 25)
    # Explicit synthetic expected pair. The rejected version ignores this field;
    # the production repair also binds it through consume()'s constructor.
    observed._login_pair = (f.USERNAME, f.PASSWORD)
    clock = f.base.FakeClock()
    _, report = c.gate.evaluate_gate(f.dev_admin_config(), http=f.base.FakeHttp(f.api_routes()),
        worker_driver=c.gate.ScheduledWorkerDriver(), correlation_id=f.base.CORRELATION_ID,
        now=f.base.NOW, web_http=observed, monotonic=clock.monotonic, sleep=clock.sleep)
    assert client.bad_index is not None
    assert observed.uncertainty_latched, "unexpected diagnostic session was treated as positive login"
    after = client.headers_seen[client.bad_index + 1:]
    assert not any(path == "POST /login" for path, _ in after), "later credential submission reached transport"
    assert report["ok"] is False
    assert observed.cleanup_owned_sessions() is False
    logouts = [headers.get("cookie") for path, headers in client.headers_seen if path == "POST /auth/logout"]
    assert len(logouts) == len(set(logouts))
    value = f.FRESH_SESSION_VALUE if form else f.SESSION_VALUE
    assert f"{f.SESSION_COOKIE}={value}" in logouts


@pytest.mark.parametrize("kind", ["identical", "distinct", "routing"])
@pytest.mark.parametrize("role", ["original", "target"])
@pytest.mark.parametrize("lost_cleanup", [False, True])
def test_real_foreground_duplicate_login_headers_retain_every_known_cookie_for_cleanup(
        owned_sessions: Any, tls_recovery_server: Any, monkeypatch: Any, tmp_path: Any,
        kind: str, role: str, lost_cleanup: bool) -> None:
    actual_execute = r.OwnedSessionHTTPSAttempt.execute_session
    first = "offline-original-cookie" if role == "original" else "offline-target-cookie"
    second = first if kind == "identical" else "offline-target-cookie" if role == "original" else "offline-original-cookie"
    def header(value: str) -> bytes:
        return (f"Set-Cookie: __Host-oday_web_session={value}; Path=/; Secure; HttpOnly; SameSite=Lax\r\n").encode()
    raw = header(first) + (b"Set-Cookie: routing=offline\r\n" if kind == "routing" else header(second))
    with owned_sessions("target_logout" if lost_cleanup else "") as (owner, budget, clock, receipts, p, calls, original, target):
        if role == "target":
            budget.begin("preflight")
            owner.login("original")
            budget.finish(outcome="completed")
            for phase in ("reserve", "issue", "rotate"):
                budget.begin(phase)
                budget.finish(outcome="completed")
        mocked_execute = r.OwnedSessionHTTPSAttempt.execute_session
        with tls_recovery_server(recovery_http_reply(extra=raw)) as (origin, ca, requests):
            owner._origin, owner._address, owner._ca = origin, "127.0.0.1", ca
            def execute(self, operation, **kwargs):
                if operation == "login":
                    calls.append((operation, kwargs["cookie"], kwargs["deadline"], kwargs["username"], kwargs["password"]))
                    return actual_execute(self, operation, **kwargs)
                return mocked_execute(self, operation, **kwargs)
            monkeypatch.setattr(r.OwnedSessionHTTPSAttempt, "execute_session", execute)
            budget.begin("preflight" if role == "original" else "reconcile")
            with pytest.raises(r.RecoveryActionRefused):
                owner.login(role)
            assert len(requests) == 1 and owner._unknown_login
            before = len(calls)
            with pytest.raises(r.RecoveryActionRefused):
                owner.login(role)
            assert len(calls) == before
            with pytest.raises(r.RecoveryActionRefused):
                owner.cleanup()
        expected = {first, second} if kind == "distinct" else {first}
        if role == "target":
            expected.add("offline-original-cookie")
        logouts = [cookie for operation, cookie, *_ in calls if operation == "logout"]
        assert set(logouts) == expected, "safely known duplicate-header custody was dropped"
        assert len(logouts) == len(set(logouts)), "same cookie logout retried across owner roles"
        assert all(cookie in expected for operation, cookie, *_ in calls if operation in {"logout", "revoked"})
        for record in (tmp_path / p["execution_id"]).glob("*.json"):
            safe = record.read_text()
            assert all(cookie not in safe for cookie in expected)
            assert original.password not in safe and target.password not in safe
        records = [json.loads(record.read_text()) for record in (tmp_path / p["execution_id"]).glob("*.json")]
        cleanup = [record for record in records if record["phase"] == "cleanup" and not record["operation"] and record["stage"] == "terminal"]
        assert cleanup[-1]["outcome"] == "unknown"


@pytest.mark.parametrize("mode", ["unbound", "malformed-body", "positive-refused", "diagnostic-control"])
def test_native_login_intent_is_trusted_and_never_inferred_from_reply(mode: str) -> None:
    sent = []
    class Client:
        def request(self, method, path, **kwargs):
            sent.append((method, path))
            return c.gate.HttpResponse(401, {"error": {"code": "AUTH_INVALID_CREDENTIALS"}})
    pair = ("offline", "offline-password")
    body = {"username": pair[0], "password": pair[1], "returnTo": "/operator?view=admin"}
    if mode == "malformed-body":
        body.pop("returnTo")
    elif mode == "diagnostic-control":
        body["password"] += "-live-gate-invalid"
    now = time.monotonic()
    web = c._ObservedWeb(Client(), execution_id="offline", deadline=now + 5, cleanup_deadline=now + 6,
                         login_pair=None if mode == "unbound" else pair)
    web.request("POST", "/login", body=body, headers={})
    assert sent == ([] if mode in {"unbound", "malformed-body"} else [("POST", "/login")])
    assert web.uncertainty_latched is (mode != "diagnostic-control")
    assert web.cleanup_owned_sessions() is (mode == "diagnostic-control")
    assert web._login_pair is None  # best-effort credential reference release, not zeroization

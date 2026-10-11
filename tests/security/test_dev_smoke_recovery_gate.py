"""Canonical same-pair gate: offline evaluator/transport, never live authority."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from tests.security import test_dev_smoke_recovery as shared_tests
from tests.security.test_dev_smoke_recovery import MANIFEST, SHA, recovery_consumer_inputs


@pytest.fixture
def owned_sessions(tmp_path: Any, monkeypatch: Any):
    return shared_tests.owned_sessions.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def tls_recovery_server(tmp_path: Any):
    return shared_tests.tls_recovery_server.__wrapped__(tmp_path)


@pytest.fixture
def foreground_gate(owned_sessions: Any, monkeypatch: Any):
    from contextlib import contextmanager

    from delivery_toolchain.e2e import check_live_e2e_gate as gate
    from delivery_toolchain.release import recover_dev_smoke as r
    from shared.identity.dev_smoke_recovery import RecoveryReservation
    from tests.e2e import test_live_e2e_gate_dev_admin as f

    @contextmanager
    def setup(failure=""):
        with owned_sessions() as (owner, budget, clock, receipts, p, session_calls, original, target):
            raw, events, user = recovery_consumer_inputs()
            # Relocate the historical synthetic packet to this plan's clock;
            # otherwise the strict root <=1h expiry correctly rejects it.
            delta = datetime.now(UTC) - datetime(2026, 10, 11, 5, tzinfo=UTC) - timedelta(seconds=60)
            for event in events:
                event["timestamp"] = (datetime.fromisoformat(event["timestamp"]) + delta).isoformat()
                for key in ("expires_at", "capability_expires_at"):
                    if key in event["metadata"]:
                        event["metadata"][key] = (datetime.fromisoformat(event["metadata"][key]) + delta).isoformat()
            digest = receipts._digest
            # Synthetic progress ONLY; not source/rotation/staging/session proof.
            budget.begin("preflight")
            owner.login("original")
            owner._baseline, owner._observation_plan = {}, dict(p)
            budget.finish(outcome="completed")
            actions = owner.recovery_actions(p)
            actions._root = RecoveryReservation(p["execution_id"], digest, events[4]["event_id"], "reserved").to_receipt()
            actions._capability_id, actions._issue_event_id = events[5]["metadata"]["capability_id"], events[5]["event_id"]
            actions._rotation_event_id = events[6]["event_id"]
            for phase in ("reserve", "issue", "rotate"):
                budget.begin(phase)
                budget.finish(outcome="completed")
            budget.begin("reconcile")
            owner.login("target")
            budget.finish(outcome="completed")
            for phase in ("intent", "put", "ack"):
                budget.begin(phase)
                budget.finish(outcome="completed", http_status=201 if phase == "put" else None)
            actions._intent = {"audit_event_id": events[7]["event_id"]}
            actions._put_status, actions._acknowledged = 201, True
            for event in events[4:]:
                event["metadata"].update(p, plan_digest=digest)
            # Capability's issue_event_id is its own lifecycle reference, NOT
            # the same-named original invitation plan field.
            events[5]["metadata"]["issue_event_id"] = ""
            events[6]["metadata"]["issue_event_id"] = events[5]["event_id"]
            raw.update(execution_id=p["execution_id"], password=target.password)
            raw["recovery_lineage"]["plan_digest"] = digest
            if failure == "":
                import json

                from delivery_toolchain.release.provision_dev_smoke import (
                    credential_bundle_acknowledged,
                    read_dev_credential_bundle,
                )
                parsed = read_dev_credential_bundle(json.dumps(actions._bundle()), environment="dev", release_profile="dev-admin")
                assert credential_bundle_acknowledged(parsed, events, target_account=user)
            config = gate.GateConfig(api_url=f.base.API_URL, web_url=owner._origin, expected_sha=SHA,
                expected_manifest_digest=MANIFEST, expected_deployment="dev", release_profile="dev-admin",
                bearer_token="OFFLINE-APP-BEARER", api_transport_token="OFFLINE-API-TRANSPORT",
                operator_role="platform_admin", required_provider_ids=(), external_provider_mode="disabled",
                dev_admin_denied_role="cs-lead", worker_deadline_seconds=1, poll_interval_seconds=0.01)
            driver = r.ForegroundSamePairGate(p, sessions=owner, gate_config=config, api_address="127.0.0.1")
            for module, name, value in ((f, "USERNAME", raw["username"]), (f, "PASSWORD", target.password),
                    (f, "ADMIN_ACCOUNT_ID", raw["account_id"]), (f, "ADMIN_TENANT_ID", raw["tenant_id"]),
                    (f.base, "WEB_URL", owner._origin), (f.base, "EXPECTED_SHA", SHA),
                    (f.base, "CORRELATION_ID", driver._correlation)):
                monkeypatch.setattr(module, name, value)
            api = f.base.FakeHttp(f.api_routes())
            web = f.AdminWeb(f.web_routes(**{
                "GET /api/v1/operator/users [session]": f.base.response(200, {"users": [user]}),
                "GET /api/v1/operator/users/audit-trail [session]": f.base.response(200, {"events": events}),
                "GET /api/v1/auth/principal [session]": f.base.response(200, {
                    "account_id": raw["account_id"], "tenant_id": raw["tenant_id"], "roles": ["platform_admin"]}),
            }))
            calls = []
            barrier = driver._before_cleanup
            def inspected_barrier(checks):
                driver._failed_checks = [c.name for c in checks if c.ok is not True]
                barrier(checks)
            monkeypatch.setattr(driver, "_before_cleanup", inspected_barrier)
            evaluate = gate.evaluate_gate
            def inspected_evaluate(*args, **kwargs):
                checks, report = evaluate(*args, **kwargs)
                driver._failed_checks = [c.name for c in checks if c.ok is not True]
                return checks, report
            monkeypatch.setattr(gate, "evaluate_gate", inspected_evaluate)
            def roundtrip(client, method, path, *, authenticated, body, headers):
                calls.append(("api" if client._api else "web", method, path, budget._active[0]))
                if failure == "transport" and path == "/api/v1/operator/users/audit-trail":
                    raise OSError("OFFLINE-PRIVATE-MARKER")
                if client._api:
                    return api.request(method, path, authenticated=authenticated, body=body, headers=headers,
                                       follow_redirects=False)
                if method == "POST" and path == "/login" and body.get("password") == target.password:
                    if headers.get("content-type") == "application/x-www-form-urlencoded":
                        monkeypatch.setattr(f, "SESSION_VALUE", "offline-gate-form-cookie")
                result = web.request(method, path, authenticated=False, body=body, headers=headers, follow_redirects=False)
                if method == "POST" and path == "/login" and result.cookies:
                    driver._retain_cookies(result.cookies)
                if failure == "lost_login" and method == "POST" and path == "/login" and result.cookies:
                    raise OSError("OFFLINE-PRIVATE-MARKER")
                if failure == "business" and path == "/api/v1/operator/bootstrap" and headers.get("cookie"):
                    result = f.base.response(200, {})
                if failure == "logout" and method == "POST" and path == "/auth/logout":
                    raise OSError("OFFLINE-PRIVATE-MARKER")
                return result
            monkeypatch.setattr(r._ForegroundGateHTTPSClient, "_roundtrip", roundtrip)
            def quarantine(attempt, *, method, cookie, deadline, body):
                assert body["action"] == "quarantine" and cookie == "offline-original-cookie"
                calls.append(("original", method, "quarantine", budget._active[0]))
                if failure == "quarantine":
                    raise r.RecoveryHTTPRefused(outcome="unknown", http_status=None)
                return r.RecoveryHTTPResult(200, {"reservation": {**actions._root,
                    "event_id": str(uuid4()), "stage": "recovery-required"}})
            monkeypatch.setattr(r.RecoveryHTTPSAttempt, "execute", quarantine)
            try:
                yield driver, actions, owner, budget, p, calls, session_calls, original, target, events
            finally:
                driver._cookies.clear()
                actions.close()
    return setup


def test_parent_full_samepair_actual_canonical_evaluator(foreground_gate: Any, tmp_path: Any) -> None:
    import json
    import pickle

    from shared.identity.dev_smoke_recovery import RecoveryRefused
    with foreground_gate() as (driver, actions, owner, budget, p, calls, sessions, original, target, events):
        try:
            receipt = driver.execute(actions)
        except RecoveryRefused:
            pytest.fail(f"canonical gate stopped; failed checks={getattr(driver, '_failed_checks', [])}; calls={calls}")
        assert receipt["gate_passed"] is True and receipt["checks_count"] > 60
        assert receipt["credential_binding_verified"] is False and receipt["full_product_acceptance"] is False
        assert receipt["original_incident_session_cleanup"] == "unknown"
        assert not any(c[2] == "quarantine" for c in calls)
        assert sum(c[2] == "/auth/logout" for c in calls) == 2
        assert all(state["verified"] for state in driver._cookies.values())
        assert driver._config.dev_admin_bundle_schema_version == 2
        assert driver._config.dev_admin_bundle_recovery_lineage.plan_digest == actions._digest
        assert budget._next == 9  # fresh consumer STILL required
        text = "".join(path.read_text() for path in (tmp_path / p["execution_id"]).glob("*.json"))
        assert all(s not in text + json.dumps(receipt) + repr(driver)
                   for s in (original.password, target.password, "offline-original-cookie", "offline-gate-form-cookie"))
        with pytest.raises(RecoveryRefused):
            pickle.dumps(driver)
        budget.begin("fresh_consumer")
        budget.finish(outcome="completed")  # SYNTHETIC, not a fresh-job proof
        owner.cleanup(gate_sessions=driver)


@pytest.mark.parametrize("failure", ["transport", "lost_login", "business", "logout", "missing_ack", "quarantine"])
def test_gate_quarantine_before_remaining_owned_cleanup(foreground_gate: Any, failure: str) -> None:
    from delivery_toolchain.release import recover_dev_smoke as r
    with foreground_gate(failure) as (driver, actions, owner, budget, p, calls, sessions, original, target, events):
        if failure in {"missing_ack", "quarantine"}:
            events.pop()
        with pytest.raises(r.RecoveryActionRefused) as caught:
            driver.execute(actions)
        assert "OFFLINE-PRIVATE-MARKER" not in repr(caught.value)
        assert budget._stopped and actions._stopped and owner._closed
        quarantine = next(i for i, c in enumerate(calls) if c[2] == "quarantine")
        cleanup = [i for i, c in enumerate(calls) if c[2] == "/auth/logout" and c[3] == "cleanup"]
        assert all(quarantine < i for i in cleanup)
        assert len([c for c in calls if c[2] == "quarantine"]) == 1
        assert len([c for c in sessions if c[0] == "logout"]) == 2
        if failure == "quarantine":
            assert driver._failure_receipt()["quarantine"] == "unknown"
        if failure in {"lost_login", "logout"}:
            assert driver._failure_receipt()["own_session_cleanup"] == "unknown"
        before = deepcopy(calls)
        driver.stop_and_cleanup(actions)
        assert calls == before
        assert driver._failure_receipt()["original_incident_session_cleanup"] == "unknown"


@pytest.mark.parametrize("field,value", [("bearer_token", ""), ("api_transport_token", ""),
    ("release_profile", "full"), ("expected_deployment", "staging"), ("expected_sha", "c" * 40),
    ("expected_manifest_digest", ""), ("external_provider_mode", "live"), ("allow_http", True),
    ("dev_admin_initial_password", "OFFLINE-PRIVATE-MARKER"), ("dev_admin_bundle_account_id", str(uuid4()))])
def test_gate_prevalidates_config_without_io(foreground_gate: Any, field: str, value: Any) -> None:
    from dataclasses import replace

    from delivery_toolchain.release import recover_dev_smoke as r
    from shared.identity.dev_smoke_recovery import RecoveryRefused
    with foreground_gate() as (driver, actions, owner, budget, p, calls, sessions, original, target, events):
        with pytest.raises(RecoveryRefused, match="GATE_CONFIG_INVALID") as caught:
            r.ForegroundSamePairGate(p, sessions=owner, gate_config=replace(driver._config, **{field: value}),
                                     api_address="127.0.0.1")
        assert not calls and "OFFLINE-PRIVATE-MARKER" not in repr(caught.value)


@pytest.mark.parametrize("bad", ["none", "duplicate", "truncated", "redirect", "dribble", "insecure_cookie"])
def test_actual_gate_tls_fixed_origin_bounded_framing_no_forwarding(tls_recovery_server: Any,
        monkeypatch: Any, bad: str) -> None:
    import socket
    import time
    from types import SimpleNamespace

    from delivery_toolchain.release import recover_dev_smoke as r
    from shared.identity.dev_smoke_recovery import RecoveryRefused
    body = b'{"ok":true,"ok":false}' if bad == "duplicate" else b'{"ok":true}'
    extra = b"Location: https://other.invalid/secret\r\n" if bad == "redirect" else b""
    if bad == "insecure_cookie":
        extra = b"Set-Cookie: __Host-oday_web_session=offline-cookie; Path=/; SameSite=Lax\r\n"
    reply = shared_tests.recovery_http_reply(body, status=303 if bad == "redirect" else 200, extra=extra)
    if bad == "truncated":
        reply = reply[:-3]
    retained = []
    with tls_recovery_server(reply, dribble=bad == "dribble") as (origin, ca, observed):
        def no_dns(*args, **kwargs):
            raise AssertionError("gate DNS forbidden")
        monkeypatch.setattr(socket, "getaddrinfo", no_dns)
        monkeypatch.setenv("HTTPS_PROXY", "http://not-a-proxy.invalid")
        owner = SimpleNamespace(_config=SimpleNamespace(web_url=origin), _correlation="corr-offline",
                    _budget=SimpleNamespace(_active=("gate",), call_deadline=time.monotonic() + (0.15 if bad == "dribble" else 2)),
                    _bytes=0, _retain_cookies=retained.append)
        client = r._ForegroundGateHTTPSClient(owner, api=False, origin=origin, address="127.0.0.1", ca_pem=ca)
        kwargs = dict(authenticated=False, body={}, headers={"origin": origin})
        if bad in {"duplicate", "truncated", "dribble", "insecure_cookie"}:
            with pytest.raises(RecoveryRefused) as caught:
                client._roundtrip("POST", "/login", **kwargs)
            assert "offline-cookie" not in repr(caught.value)
        else:
            result = client._roundtrip("POST", "/login", **kwargs)
            assert result.status == (303 if bad == "redirect" else 200)
        assert len(observed) == 1  # no redirect, proxy, DNS, retry or reconnect
        header, request_body = observed[0]
        assert b"authorization:" not in header.lower() and request_body == b"{}"
        if bad == "insecure_cookie":
            assert retained == [{"__Host-oday_web_session": "offline-cookie"}]  # cleanup only


@pytest.mark.parametrize("failure", ["start_receipt", "terminal_receipt", "absolute_timer"])
def test_gate_receipt_loss_and_absolute_timer_never_resume(foreground_gate: Any, monkeypatch: Any,
                                                         failure: str) -> None:
    import time

    from delivery_toolchain.release import recover_dev_smoke as r
    with foreground_gate() as (driver, actions, owner, budget, p, calls, sessions, original, target, events):
        append = r.ExclusivePhaseReceipts.append
        if failure.endswith("receipt"):
            def uncertain_receipt(self, **kwargs):
                if (kwargs["phase"] == "gate" and kwargs.get("operation", "") == ""
                        and kwargs["stage"] == ("start" if failure == "start_receipt" else "terminal")):
                    raise OSError("OFFLINE-PRIVATE-MARKER")
                return append(self, **kwargs)
            monkeypatch.setattr(r.ExclusivePhaseReceipts, "append", uncertain_receipt)
        else:
            monkeypatch.setitem(r._CEILINGS, "gate", 0.1)
            roundtrip = r._ForegroundGateHTTPSClient._roundtrip
            def stalled(client, method, path, **kwargs):
                if path == "/api/v1/operator/users/audit-trail":
                    time.sleep(0.5)  # actual parent timer, NOT a synthetic timeout receipt
                return roundtrip(client, method, path, **kwargs)
            monkeypatch.setattr(r._ForegroundGateHTTPSClient, "_roundtrip", stalled)
        started = time.monotonic()
        with pytest.raises(r.RecoveryActionRefused):
            driver.execute(actions)
        if failure == "absolute_timer":
            assert time.monotonic() - started < 0.45
        assert budget._stopped and owner._closed
        assert driver._failure_receipt()["quarantine"] == "completed"
        assert sum(c[2] == "quarantine" for c in calls) == 1
        assert len([c for c in sessions if c[0] == "logout"]) == 2
        if failure == "start_receipt":
            assert not any(c[3] == "gate" for c in calls)


def test_exhausted_gate_quota_preserves_separate_known_own_cleanup(foreground_gate: Any) -> None:
    from delivery_toolchain.release import recover_dev_smoke as r
    from tests.e2e import test_live_e2e_gate_dev_admin as f
    with foreground_gate() as (driver, actions, owner, budget, p, calls, sessions, original, target, events):
        # Synthetic previously owned diagnostic session, not a live login proof.
        driver._retain_cookies({"__Host-oday_web_session": f.SESSION_VALUE})
        driver._requests = 256
        driver._bytes = 4194304
        with pytest.raises(r.RecoveryActionRefused):
            driver.execute(actions)
        assert not any(c[3] == "gate" for c in calls)
        assert driver._failure_receipt()["quarantine"] == "completed"
        assert driver._failure_receipt()["own_session_cleanup"] == "completed"
        assert sum(c[2] == "/auth/logout" for c in calls) == 1
        assert len([c for c in sessions if c[0] == "logout"]) == 2

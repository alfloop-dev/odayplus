"""Canonical same-pair gate: offline evaluator/transport, never live authority."""
from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

import pytest

from tests.security.test_dev_smoke_recovery import (
    MANIFEST,
    SHA,
    owned_sessions,  # noqa: F401 - shared pytest fixture
    recovery_consumer_inputs,
)


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
            raw.update(execution_id=p["execution_id"], password=target.password)
            raw["recovery_lineage"]["plan_digest"] = digest
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
        receipt = driver.execute(actions)
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

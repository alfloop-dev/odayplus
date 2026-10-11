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


@pytest.fixture
def preflight(owned_sessions: Any, monkeypatch: Any):
    """Actual composition with synthetic Web/GitHub records; NO live authority.

    The local checkout/manifest child is mocked here; its actual original-handle
    cancellation and canonical validation have separate existing regressions.
    Remote source/admission selectors, strict account observation, phase receipts
    and session ownership are the actual implementation, not supplied pass flags.
    """
    from contextlib import contextmanager
    from pathlib import Path

    from delivery_toolchain.e2e import check_live_e2e_gate as gate
    from delivery_toolchain.release import recover_dev_smoke as r
    from tests.e2e import test_live_e2e_gate_dev_admin as f

    @contextmanager
    def setup(failure=""):
        from contextlib import ExitStack, contextmanager
        with ExitStack() as stack:
            source = stack.enter_context(contextmanager(shared_tests.bounded_remote_source.__wrapped__)())
            admission = stack.enter_context(contextmanager(shared_tests.bounded_remote_admission.__wrapped__)(monkeypatch))
            store_setup = shared_tests.recovery_secret_store.__wrapped__(monkeypatch)
            store, key, metadata_calls = stack.enter_context(store_setup(fault="exists" if failure == "metadata" else ""))
            owner, budget, clock, receipts, p, session_calls, original, target = stack.enter_context(
                owned_sessions(failure if failure in {"body_lost", "login_lost", "logout"} else ""))
            calls = []
            payloads = shared_tests.account_observer_payloads(p)
            if failure == "account":
                payloads["old-journal"]["binding"] = {"stage": "binding-intent"}
            shared_tests.install_account_observer(monkeypatch, payloads, calls)
            checkout = r.TrustedCheckoutObserver(Path(r.__file__).resolve().parents[2],
                                                release_sha=SHA, reviewed_tree="e" * 40)
            config = gate.GateConfig(api_url=f.base.API_URL, web_url=owner._origin, expected_sha=SHA,
                expected_manifest_digest=MANIFEST, expected_deployment="dev", release_profile="dev-admin",
                bearer_token="OFFLINE-APP-BEARER", api_transport_token="OFFLINE-API-TRANSPORT",
                operator_role="platform_admin", required_provider_ids=(), external_provider_mode="disabled",
                dev_admin_denied_role="cs-lead", worker_deadline_seconds=1, poll_interval_seconds=0.01)
            driver = r.ForegroundSamePairGate(p, sessions=owner, gate_config=config, api_address="127.0.0.1")
            source_observe, admission_observe = source.observer.observe, admission.observer.observe
            counts = {"checkout": 0, "source": 0, "admission": 0}
            def checkout_observe(self, *, deadline):
                counts["checkout"] += 1
                calls.append(("checkout", deadline))
                if failure == "checkout":
                    raise r.ObserverRefused(outcome="refused", exit_code=7)
                return {"release_sha": SHA, "reviewed_tree": "e" * 40}
            def source_observed(*, release_sha, deadline):
                counts["source"] += 1
                calls.append(("source", deadline))
                if failure == "source" or (failure == "source_recheck" and counts["source"] == 2):
                    raise RuntimeError("OFFLINE-PRIVATE-MARKER")
                return source_observe(release_sha=release_sha, deadline=deadline)
            def admission_observed(**kwargs):
                counts["admission"] += 1
                calls.append(("admission", kwargs["deadline"]))
                if failure == "admission_recheck" and counts["admission"] == 2:
                    admission.docs["actions/runs/90/attempts/1/jobs?per_page=100"]["jobs"][0]["steps"][-1]["conclusion"] = "failure"
                result = admission_observe(**kwargs)
                if failure == "admission_replaced" and counts["admission"] == 2:
                    result["run_id"] = 91
                return result
            def manifest_observed(self, manifest, *, release_sha, manifest_digest):
                assert release_sha == SHA and manifest_digest == MANIFEST
                assert self._deadline == budget.call_deadline
            monkeypatch.setattr(r.TrustedCheckoutObserver, "observe", checkout_observe)
            monkeypatch.setattr(source.observer, "observe", source_observed)
            monkeypatch.setattr(admission.observer, "observe", admission_observed)
            monkeypatch.setattr(r.TrustedGitHubAdmissionObserver, "_validate_manifest", manifest_observed)
            def forbidden_effect(*args, **kwargs):
                raise AssertionError("preflight must NEVER construct/execute recovery or gate/PUT")
            monkeypatch.setattr(r.ForegroundOwnedWebSessions, "recovery_actions", forbidden_effect)
            monkeypatch.setattr(r.RecoveryHTTPSAttempt, "execute", forbidden_effect)
            monkeypatch.setattr(r.RecoveryGitHubSecretAttempt, "put", forbidden_effect)
            monkeypatch.setattr(r.ForegroundSamePairGate, "execute", forbidden_effect)
            coordinator = r.ForegroundRecoveryPreflight(p, checkout=checkout, source=source.observer,
                admission=admission.observer, secret=store, gate=driver)
            yield coordinator, owner, budget, p, calls, session_calls, metadata_calls, original, target, counts
    return setup


def test_actual_preflight_fresh_observers_hold_before_every_recovery_effect(preflight: Any, tmp_path: Any) -> None:
    import json
    import pickle

    from delivery_toolchain.release import recover_dev_smoke as r
    with preflight() as (coordinator, owner, budget, p, calls, session_calls, metadata, original, target, counts):
        secrets = (original.password, target.password, "offline-original-cookie", "offline-gh-token", "OFFLINE-PRIVATE-TOKEN")
        receipt = coordinator.observe_and_hold()
        assert receipt["stage"] == "preflight-held" and receipt["preflight"] == "completed"
        assert receipt["own_session_cleanup"] == "completed"
        assert receipt["original_incident_session_cleanup"] == "unknown"
        assert "external-writer-exclusion-unavailable" in receipt["blocking_gaps"]
        assert receipt["execution_authorized"] is receipt["credential_binding_verified"] is False
        assert counts == {"checkout": 2, "source": 2, "admission": 2}
        assert len({call[1] for call in calls}) == 1  # all observers inherit ONE absolute clock
        assert [c[0] for c in session_calls] == ["login", "session", "principal", "logout", "revoked"]
        assert all(c[0] == "GET" for c in metadata) and len(metadata) == 4
        records = [json.loads(path.read_text()) for path in sorted((tmp_path / p["execution_id"]).glob("*.json"))]
        assert {record["phase"] for record in records} == {"preflight", "cleanup"}
        assert len([v for v in records if v["operation"] == "preflight-source"]) == 4
        text = json.dumps(records) + json.dumps(receipt) + repr(coordinator)
        assert not any(value in text for value in secrets)
        assert budget._stopped and owner._closed and original._closed and target._closed
        with pytest.raises(r.RecoveryRefused):
            budget.begin("reserve")
        with pytest.raises(r.RecoveryRefused):
            coordinator.observe_and_hold()
        with pytest.raises(r.RecoveryRefused):
            pickle.dumps(coordinator)


@pytest.mark.parametrize("failure", ["checkout", "source", "body_lost", "login_lost", "account",
    "metadata", "source_recheck", "admission_recheck", "admission_replaced", "logout"])
def test_preflight_uncertainty_never_reserves_or_replays(preflight: Any, failure: str) -> None:
    from delivery_toolchain.release import recover_dev_smoke as r
    with preflight(failure) as (coordinator, owner, budget, p, calls, session_calls, metadata, original, target, counts):
        receipt = coordinator.observe_and_hold()
        assert receipt["preflight"] == ("completed" if failure == "logout" else "unknown")
        assert receipt["own_session_cleanup"] == ("unknown" if failure in {"body_lost", "login_lost", "logout"} else "completed")
        assert "OFFLINE-PRIVATE-MARKER" not in repr(receipt)
        assert not budget._used & {"reserve", "issue", "rotate", "intent", "put", "ack", "gate", "fresh_consumer", "quarantine"}
        assert budget._stopped and owner._closed and original._closed and target._closed
        assert len([c for c in session_calls if c[0] == "login"]) <= 1
        assert len([c for c in session_calls if c[0] == "logout"]) <= 1
        before = deepcopy(calls), deepcopy(session_calls), deepcopy(metadata)
        with pytest.raises(r.RecoveryRefused):
            coordinator.observe_and_hold()
        assert before == (calls, session_calls, metadata)


@pytest.mark.parametrize("failure", ["start", "terminal", "observer_terminal"])
def test_preflight_receipt_loss_no_effect_or_fake_completion(preflight: Any, monkeypatch: Any, failure: str) -> None:
    from delivery_toolchain.release import recover_dev_smoke as r
    with preflight() as (coordinator, owner, budget, p, calls, session_calls, metadata, original, target, counts):
        append = r.ExclusivePhaseReceipts.append
        def fail(self, **kwargs):
            if kwargs["phase"] == "preflight" and (
                    (failure == "observer_terminal" and kwargs.get("operation") == "preflight-source" and kwargs["stage"] == "terminal")
                    or (failure != "observer_terminal" and not kwargs.get("operation") and kwargs["stage"] == failure)):
                self._poisoned = True
                raise OSError("OFFLINE-PRIVATE-MARKER")
            return append(self, **kwargs)
        monkeypatch.setattr(r.ExclusivePhaseReceipts, "append", fail)
        receipt = coordinator.observe_and_hold()
        assert receipt["preflight"] == receipt["own_session_cleanup"] == "unknown"
        assert not budget._used & {"reserve", "rotate", "put"}
        assert not any(c[0] == "logout" for c in session_calls)  # no effect without durable start
        if failure == "start":
            assert not calls and not session_calls and not metadata


@pytest.mark.parametrize("pin", ["callback", "wrong_tree", "wrong_checkout", "wrong_custodian", "already_used"])
def test_preflight_constructor_no_callbacks_or_unmatched_pins(preflight: Any, pin: str) -> None:
    from pathlib import Path

    from delivery_toolchain.release import recover_dev_smoke as r
    with preflight() as (coordinator, owner, budget, p, calls, session_calls, metadata, original, target, counts):
        if pin == "wrong_tree":
            coordinator._checkout._tree = "c" * 40
        elif pin == "wrong_checkout":
            coordinator._checkout._checkout = Path("/tmp/not-the-candidate")
        elif pin == "wrong_custodian":
            coordinator._secret._login = "another-owner"
        elif pin == "already_used":
            budget._used.add("preflight")
        with pytest.raises(r.RecoveryRefused, match="PREFLIGHT_CONFIG_INVALID"):
            r.ForegroundRecoveryPreflight(p, checkout=coordinator._checkout,
                source=(lambda: True) if pin == "callback" else coordinator._source,
                admission=coordinator._admission, secret=coordinator._secret, gate=coordinator._gate)
        assert not calls and not session_calls and not metadata

"""Synthetic/offline native consumer contracts, NOT decrypted/live CI proof."""
from __future__ import annotations

import base64
import json
import pickle
import time
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from delivery_toolchain.release import consume_dev_smoke as c
from tests.security.test_dev_smoke_recovery import recovery_consumer_inputs

SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
NOW = datetime(2026, 10, 11, 7, tzinfo=UTC)


def native_environment() -> dict[str, str]:
    return {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": c.REPOSITORY,
            "GITHUB_REF": "refs/heads/dev", "GITHUB_SHA": SHA,
            "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ID": "123",
            "GITHUB_ACTOR": c.REVIEW_WRITER, "GITHUB_RUN_ATTEMPT": "1"}


def run_record() -> dict:
    return {"id": 123, "repository": {"full_name": c.REPOSITORY},
            "head_repository": {"full_name": c.REPOSITORY}, "head_sha": SHA,
            "head_branch": "dev", "event": "workflow_dispatch", "path": c.WORKFLOW,
            "actor": {"login": c.REVIEW_WRITER, "id": c.REVIEW_WRITER_ID},
            "status": "in_progress", "conclusion": None, "run_attempt": 1,
            "created_at": (NOW - timedelta(seconds=5)).isoformat()}


def raw_inputs() -> dict[str, str]:
    raw, _, _ = recovery_consumer_inputs()
    return {c.SECRET_INPUTS[0]: json.dumps(raw), c.SECRET_INPUTS[1]: "offline-native-github-read-token",
            c.SECRET_INPUTS[2]: "offline-native-oidc-request-token",
            c.SECRET_INPUTS[3]: "https://pipelines.actions.githubusercontent.com/offline/oidctoken"}


def passing_gate_report() -> dict:
    names = ["admin:credential_bundle_account_bound", "admin:credential_bundle_durable_acknowledgement",
             "session:logout", "session:revoked_session_refused", "admin:logout_revokes_admin_api",
             "session:html_form_logout", "session:html_form_revoked_session_refused"]
    return {"ok": True, "checks": [{"name": name, "ok": True, "detail": "synthetic"} for name in names]}


def test_native_pair_is_individually_masked_and_removed_before_any_child() -> None:
    env = {**native_environment(), **raw_inputs()}
    masks = []
    inputs = c.take_native_inputs(env, masker=masks.append)
    assert inputs.bundle.schema_version == 2
    assert masks == [inputs.bundle.username, inputs.bundle.password, inputs.github_token,
                     inputs.oidc_request_token, inputs.oidc_request_url]
    assert not inputs.invoker_token and not inputs.application_token
    assert not set(c.SECRET_INPUTS + c.LEGACY_INPUTS) & set(env)
    assert inputs.bundle.password not in repr(inputs)
    with pytest.raises(c.ConsumerRefused):
        pickle.dumps(inputs)


@pytest.mark.parametrize("legacy", c.LEGACY_INPUTS)
def test_mixed_legacy_never_falls_back_and_all_credentials_are_popped(legacy: str) -> None:
    env = {**raw_inputs(), legacy: "offline-legacy"}
    with pytest.raises(c.ConsumerRefused, match="^DEV_SMOKE_NATIVE_CONSUMER_REFUSED$"):
        c.take_native_inputs(env, masker=lambda _: pytest.fail("must refuse before mask/gate"))
    assert not env


@pytest.mark.parametrize("failure", ["empty", "invalid", "duplicate", "schema1", "no-oidc", "no-gh"])
def test_invalid_native_input_has_static_refusal_and_no_retained_environment(failure: str) -> None:
    env = raw_inputs()
    if failure == "empty":
        env[c.SECRET_INPUTS[0]] = ""
    elif failure == "invalid":
        env[c.SECRET_INPUTS[0]] = '{"password":"do-not-echo"'
    elif failure == "duplicate":
        env[c.SECRET_INPUTS[0]] = '{"schema_version":2,' + env[c.SECRET_INPUTS[0]][1:]
    elif failure == "schema1":
        raw = json.loads(env[c.SECRET_INPUTS[0]])
        raw["schema_version"] = 1
        env[c.SECRET_INPUTS[0]] = json.dumps(raw)
    else:
        env.pop(c.SECRET_INPUTS[2 if failure == "no-oidc" else 1])
    with pytest.raises(c.ConsumerRefused) as exc:
        c.take_native_inputs(env, masker=lambda _: None)
    assert str(exc.value) == "DEV_SMOKE_NATIVE_CONSUMER_REFUSED"
    assert not env


def test_authenticated_manual_native_run_is_exact_dev_source_not_a_passing_boolean() -> None:
    assert c.validate_run(run_record(), native_environment(), expected_sha=SHA, now=NOW) == NOW - timedelta(seconds=5)


@pytest.mark.parametrize("field,value", [
    ("path", ".github/workflows/deploy-dev.yml"), ("head_sha", "c" * 40),
    ("head_branch", "other"), ("event", "push"), ("id", 124),
    ("status", "completed"), ("conclusion", "success"), ("run_attempt", True),
    ("created_at", (NOW + timedelta(seconds=1)).isoformat()),
    ("created_at", (NOW - timedelta(minutes=21)).isoformat()),
])
def test_wrong_run_cannot_supply_consumer_authority(field: str, value) -> None:
    run = run_record()
    run[field] = value
    with pytest.raises(c.ConsumerRefused):
        c.validate_run(run, native_environment(), expected_sha=SHA, now=NOW)


@pytest.mark.parametrize("field,value", [
    ("GITHUB_ACTIONS", "false"), ("GITHUB_REPOSITORY", "other/repo"),
    ("GITHUB_REF", "refs/heads/other"), ("GITHUB_SHA", "c" * 40),
    ("GITHUB_RUN_ID", "124"), ("GITHUB_ACTOR", "other"),
])
def test_substituted_native_context_refuses(field: str, value: str) -> None:
    env = native_environment()
    env[field] = value
    with pytest.raises(c.ConsumerRefused):
        c.validate_run(run_record(), env, expected_sha=SHA, now=NOW)


@pytest.mark.parametrize("field,value", [
    ("actor", {"login": "other", "id": c.REVIEW_WRITER_ID}),
    ("actor", {"login": c.REVIEW_WRITER, "id": 1}),
    ("repository", {"full_name": "other/repo"}),
    ("head_repository", {"full_name": "other/repo"}),
])
def test_authenticated_repository_actor_id_not_just_display_name(field: str, value) -> None:
    run = run_record()
    run[field] = value
    with pytest.raises(c.ConsumerRefused):
        c.validate_run(run, native_environment(), expected_sha=SHA, now=NOW)


def test_safe_result_never_persists_arbitrary_gate_details_or_claims_full_acceptance() -> None:
    report = passing_gate_report()
    report["checks"][0]["detail"] = "PRIVATE_PASSWORD PHC BEARER COOKIE DO_NOT_PERSIST"
    report["audit"] = "DO_NOT_PERSIST"
    safe = c.safe_consumption_report(report, ack_times=[NOW - timedelta(seconds=10)], job_created=NOW,
                                    sha=SHA, digest=DIGEST, run_id="123")
    assert "DO_NOT_PERSIST" not in json.dumps(safe)
    assert safe["full_product_acceptance"] is False
    assert safe["recovery_rotation_performed"] is False and safe["secret_write_performed"] is False


@pytest.mark.parametrize("acks", [[], [NOW], [NOW + timedelta(seconds=1)], [NOW, NOW]])
def test_before_ack_or_duplicate_ack_job_is_never_actual_fresh_consumption(acks: list[datetime]) -> None:
    with pytest.raises(c.ConsumerRefused):
        c.safe_consumption_report(passing_gate_report(), ack_times=acks, job_created=NOW,
                                  sha=SHA, digest=DIGEST, run_id="123")


@pytest.mark.parametrize("failure", ["gate", "missing-binding", "logout", "html-logout", "bad-extra"])
def test_incomplete_canonical_gate_or_cleanup_never_records_consumption(failure: str) -> None:
    report = passing_gate_report()
    if failure == "gate":
        report["ok"] = False
    elif failure == "missing-binding":
        report["checks"] = report["checks"][1:]
    elif failure == "bad-extra":
        report["checks"].append({"name": "runtime-failure", "ok": False})
    else:
        report["checks"][2 if failure == "logout" else 5]["ok"] = False
    with pytest.raises(c.ConsumerRefused):
        c.safe_consumption_report(report, ack_times=[NOW - timedelta(seconds=1)], job_created=NOW,
                                  sha=SHA, digest=DIGEST, run_id="123")


def test_real_absolute_signal_cancels_slow_request_without_logging_credentials() -> None:
    class Slow:
        def request(self, *args, **kwargs):
            time.sleep(0.2)
            pytest.fail("late request must not return success")
    start = time.monotonic()
    web = c._ObservedWeb(Slow(), execution_id="offline", deadline=start + 0.03, cleanup_deadline=start + 0.09)
    response = web.request("GET", "/api/v1/operator/users/audit-trail")
    assert response.status == 0 and response.failed
    assert time.monotonic() - start < 0.15


def test_cleanup_uses_reserved_absolute_deadline_not_refreshed_work_budget() -> None:
    class Cleanup:
        def request(self, method, path, **kwargs):
            return c.gate.HttpResponse(200, {"ok": True})
    now = time.monotonic()
    web = c._ObservedWeb(Cleanup(), execution_id="offline", deadline=now - 1, cleanup_deadline=now + 0.2)
    assert web.request("POST", "/auth/logout").status == 200
    assert web.request("POST", "/login").status == 0


_COOKIE = "__Host-oday_web_session"


@pytest.mark.parametrize("uncertain", ["lost-login", "malformed-login-ok-false", "malformed-login-500-cookie",
                                       "login-bad-cookie", "lost-logout", "logout-500", "logout-uncleared"])
def test_uncertain_session_effect_latches_before_any_later_login_transport(uncertain: str) -> None:
    sent: list[tuple[str, str, str]] = []

    class Client:
        def request(self, method, path, **kwargs):
            cookie = (kwargs.get("headers") or {}).get("cookie", "")
            sent.append((method, path, cookie))
            if path == "/login":
                if uncertain == "lost-login":
                    raise TimeoutError("offline lost reply after possible commit")
                if uncertain == "malformed-login-ok-false":
                    return c.gate.HttpResponse(200, {"ok": False}, cookies={_COOKIE: "owned-a"})
                if uncertain == "malformed-login-500-cookie":
                    return c.gate.HttpResponse(500, {}, cookies={_COOKIE: "owned-a"})
                if uncertain == "login-bad-cookie":
                    return c.gate.HttpResponse(200, {"ok": True}, cookies={_COOKIE: "bad;cookie"})
                return c.gate.HttpResponse(200, {"ok": True}, cookies={_COOKIE: "owned-a"})
            if path == "/auth/logout":
                if uncertain == "lost-logout":
                    raise TimeoutError("offline lost logout reply")
                if uncertain == "logout-500":
                    return c.gate.HttpResponse(500, {})
                if uncertain == "logout-uncleared":
                    return c.gate.HttpResponse(200, {"ok": True})
                return c.gate.HttpResponse(200, {"ok": True}, cookies={_COOKIE: ""})
            return c.gate.HttpResponse(401, {})

    class Api:
        def request(self, method, path, **kwargs):
            sent.append(("API " + method, path, ""))
            return c.gate.HttpResponse(200, {})

    now = time.monotonic()
    latch = c._UncertaintyLatch()
    web = c._ObservedWeb(Client(), execution_id="offline", deadline=now + 5, cleanup_deadline=now + 6, latch=latch)
    api = c._ObservedWeb(Api(), execution_id="offline", deadline=now + 5, cleanup_deadline=now + 6, latch=latch)
    owned = {"cookie": f"{_COOKIE}=owned-a"}
    web.request("POST", "/login", headers={})
    if uncertain in {"lost-logout", "logout-500", "logout-uncleared"}:
        assert not web.uncertainty_latched
        assert api.request("GET", "/api/v1/operator/users").status == 200  # unlatched work continues
        web.request("POST", "/auth/logout", headers=owned)
    assert web.uncertainty_latched and api.uncertainty_latched
    before = list(sent)
    # Canonical evaluator continuation: negative + successful form logins, other reads/API.
    for method, path, headers in (("POST", "/login", {}), ("POST", "/login", {"content-type": "form"}),
                                  ("GET", "/api/v1/operator/users", owned), ("POST", "/auth/password", owned)):
        assert web.request(method, path, headers=headers).status == 0
    assert api.request("POST", "/api/v1/jobs").status == 0
    assert sent == before, "latched request reached transport"
    assert web.cleanup_owned_sessions() is False  # UNKNOWN is preserved, never cleared
    cleanup = sent[len(before):]
    assert all(path in {"/auth/logout", "/auth/session", "/api/v1/auth/principal"} for _, path, _ in cleanup)
    assert all(cookie == owned["cookie"] for _, _, cookie in cleanup)
    assert len([p for m, p, _ in sent if (m, p) == ("POST", "/auth/logout")]) <= 1  # never retried
    assert all(path != "/login" for _, path, _ in cleanup)


@pytest.fixture(scope="module")
def offline_signing_key():
    # Ephemeral isolated fixture, never an operational JWT key or persisted key.
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def oidc_claims() -> dict:
    now = int(time.time())
    return {"iss": c.OIDC_ISSUER, "aud": c.NATIVE_AUDIENCE, "iat": now - 1, "nbf": now - 1,
            "exp": now + 120, "sub": f"repo:{c.REPOSITORY}:environment:dev",
            "repository": c.REPOSITORY, "ref": "refs/heads/dev", "environment": "dev",
            "event_name": "workflow_dispatch", "actor": c.REVIEW_WRITER, "actor_id": str(c.REVIEW_WRITER_ID),
            "sha": SHA, "workflow_sha": SHA, "workflow_ref": f"{c.REPOSITORY}/{c.WORKFLOW}@refs/heads/dev",
            "run_id": "123", "run_attempt": "1", "runner_environment": "github-hosted"}


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def signed_offline_token(key, claims):
    header = {"kid": "offline-only", "alg": "RS256", "typ": "JWT"}
    message = _encode(json.dumps(header).encode()) + "." + _encode(json.dumps(claims).encode())
    signature = key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
    numbers = key.public_key().public_numbers()
    public = {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": "offline-only",
              "e": _encode(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8)),
              "n": _encode(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8))}
    return message + "." + _encode(signature), {"keys": [public]}


def test_native_identity_requires_real_signature_and_job_source_claims(offline_signing_key):
    token, jwks = signed_offline_token(offline_signing_key, oidc_claims())
    c.verify_native_oidc(token, jwks, native_environment(), now=datetime.now(UTC))
    replacement = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _, wrong_jwks = signed_offline_token(replacement, oidc_claims())
    with pytest.raises(c.ConsumerRefused):
        c.verify_native_oidc(token, wrong_jwks, native_environment(), now=datetime.now(UTC))


@pytest.mark.parametrize("key,value", [
    ("repository", "other/repo"), ("ref", "refs/heads/other"), ("environment", "production"),
    ("event_name", "push"), ("actor_id", "1"), ("run_id", "124"), ("run_attempt", "2"),
    ("sha", "c" * 40), ("workflow_sha", "c" * 40), ("workflow_ref", "other/workflow"),
    ("runner_environment", "self-hosted"), ("aud", "other-audience"),
    ("sub", "repo:alfloop-dev/odayplus:ref:refs/heads/dev"),
])
def test_other_signed_native_context_cannot_attest_this_consumer(offline_signing_key, key, value):
    claims = oidc_claims()
    claims[key] = value
    token, jwks = signed_offline_token(offline_signing_key, claims)
    with pytest.raises(c.ConsumerRefused):
        c.verify_native_oidc(token, jwks, native_environment(), now=datetime.now(UTC))


def test_plain_env_flags_or_unsigned_jwt_are_not_native_proof(offline_signing_key):
    _, jwks = signed_offline_token(offline_signing_key, oidc_claims())
    token = _encode(b'{"alg":"none","typ":"JWT"}') + "." + _encode(json.dumps(oidc_claims()).encode()) + "."
    with pytest.raises(c.ConsumerRefused):
        c.verify_native_oidc(token, jwks, native_environment(), now=datetime.now(UTC))


@pytest.mark.parametrize("failure", [None, "oidc", "source", "admission", "gate", "canonical",
    "canonical-users", "canonical-audit", "canonical-ack", "canonical-principal",
    "canonical-malformed-login", "canonical-lost-login", "canonical-logout-unknown"])
def test_composed_consumer_pipeline_is_fenced_and_drops_native_gh_env(monkeypatch, failure):
    # Entirely MOCKED authority/runtime; no network/live receipt. Proves wiring.
    import httpx
    env = native_environment()
    raw, events, user = recovery_consumer_inputs()
    ingress = raw_inputs()
    ingress[c.SECRET_INPUTS[0]] = json.dumps(raw)
    inputs = c.take_native_inputs(ingress, masker=lambda _: None)
    run = run_record()
    run["created_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    calls = []

    class Github:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def stream(self, method, path, **kwargs):
            from contextlib import contextmanager
            assert method == "GET" and path.endswith("/actions/runs/123")
            @contextmanager
            def response():
                yield httpx.Response(200, json=run)
            return response()

    def observe(kind):
        calls.append(kind)
        isolated = tuple(key for key in c.SECRET_INPUTS if key != "GH_TOKEN") + c.LEGACY_INPUTS
        assert not set(isolated) & set(env)
        assert "GH_TOKEN" not in env  # direct HTTP never opens the helper exception
        if failure == kind:
            raise c.ConsumerRefused()

    class Source:
        def __init__(self, **kwargs):
            pass
        def observe(self, **kwargs):
            observe("source")
        def close(self):
            pass

    def mocked_gate(config, **kwargs):
        observe("gate")
        assert config.dev_admin_bundle_schema_version == 2
        assert all(check.ok for check in c.gate.validate_config(config))
        assert config.bearer_token == "offline-generated-application"
        assert config.api_transport_token == "offline-generated-invoker"
        assert kwargs["http"].client._bearer_token == config.bearer_token
        assert config.dev_admin_password == inputs.bundle.password
        assert not config.bootstrap_admin_password and not config.dev_admin_initial_password
        # Mimic actual audit observation; NOT an authenticated/live receipt.
        kwargs["web_http"].ack_times.append(datetime.now(UTC) - timedelta(seconds=10))
        return [], passing_gate_report()

    monkeypatch.setattr(httpx, "Client", Github)
    monkeypatch.setattr(c, "_native_oidc", lambda *args: observe("oidc"))
    monkeypatch.setattr(c, "NativeSourceApprovalObserver", Source)
    monkeypatch.setattr(c, "_current_admission", lambda **kwargs: observe("admission"))
    monkeypatch.setattr(c, "_native_gate_identity", lambda *args: ("offline-generated-invoker", "offline-generated-application"))
    if failure and failure.startswith("canonical"):
        # REAL canonical evaluator, synthetic HTTP and authority only. This
        # catches config:operator_credential without a passing-report mock.
        from tests.e2e import test_live_e2e_gate_dev_admin as f
        for module, name, value in ((f, "USERNAME", inputs.bundle.username),
                (f, "PASSWORD", inputs.bundle.password), (f, "ADMIN_ACCOUNT_ID", inputs.bundle.account_id),
                (f, "ADMIN_TENANT_ID", inputs.bundle.tenant_id), (f.base, "WEB_URL", c.WEB_ORIGIN),
                (f.base, "API_URL", c.API_ORIGIN), (f.base, "EXPECTED_SHA", SHA),
                (f.base, "CORRELATION_ID", "corr-native-consumer-123")):
            monkeypatch.setattr(module, name, value)
        api = f.base.FakeHttp(f.api_routes())
        api._bearer_token = "offline-generated-application"
        routes = f.web_routes(**{
            "GET /api/v1/operator/users [session]": f.base.response(200, {"users": [user]}),
            "GET /api/v1/operator/users/audit-trail [session]": f.base.response(200, {"events": events}),
            "GET /api/v1/auth/principal [session]": f.base.response(200, {
                "account_id": raw["account_id"], "tenant_id": raw["tenant_id"], "roles": ["platform_admin"]}),
        })
        paths = {"canonical-users": "GET /api/v1/operator/users [session]",
                 "canonical-audit": "GET /api/v1/operator/users/audit-trail [session]",
                 "canonical-principal": "GET /api/v1/auth/principal [session]"}
        if failure in paths:
            routes[paths[failure]] = f.base.response(503, {})
        if failure == "canonical-ack":
            routes["GET /api/v1/operator/users/audit-trail [session]"] = f.base.response(200, {"events": []})
        if failure in {"canonical-malformed-login", "canonical-lost-login"}:
            def login(body, headers):
                if body.get("password") != inputs.bundle.password:
                    return f.base.response(401, {"error": {"code": "AUTH_INVALID_CREDENTIALS"}})
                if failure == "canonical-lost-login":
                    raise TimeoutError("offline lost reply after possible commit")
                return f.base.response(200, {"ok": False}, cookies={f.SESSION_COOKIE: f.SESSION_VALUE})
            routes["anon POST /login"] = login
        if failure == "canonical-logout-unknown":
            routes["POST /auth/logout [session]"] = f.base.response(0, error="offline lost logout reply")
        def form_login(body, headers):
            if body.get("username") == inputs.bundle.username and body.get("password") == inputs.bundle.password:
                web.logged_out = False
                return f.base.response(303, location=c.WEB_ORIGIN + "/operator?view=admin",
                                       cookies={f.SESSION_COOKIE: f.FRESH_SESSION_VALUE})
            return f.base.response(303, location=c.WEB_ORIGIN + "/login?error=AUTH_INVALID_CREDENTIALS&returnTo=%2Foperator%3Fview%3Dadmin")
        routes["form POST /login"] = form_login
        web = f.AdminWeb(routes)
        def api_client(*args, **kwargs):
            assert kwargs["bearer_token"] == api._bearer_token
            assert kwargs["transport_token"] == "offline-generated-invoker"
            return api
        monkeypatch.setattr(c.gate, "UrllibHttpClient", api_client)
        monkeypatch.setattr(c.gate, "_web_client", lambda *args: web)
        canonical_evaluate = c.gate.evaluate_gate
        def inspected_evaluate(*args, **kwargs):
            checks, report = canonical_evaluate(*args, **kwargs)
            failed = [check.name for check in checks if check.ok is not True]
            if failure == "canonical" and failed:
                pytest.fail(f"OFFLINE canonical evaluator failed checks: {failed}")
            return checks, report
        monkeypatch.setattr(c.gate, "evaluate_gate", inspected_evaluate)
    else:
        monkeypatch.setattr(c.gate, "evaluate_gate", mocked_gate)
    if failure and failure != "canonical":
        with pytest.raises(c.ConsumerRefused) as refused:
            c.consume(inputs=inputs, environment=env, sha=SHA, digest=DIGEST, pull_number=1, reviewed_head=SHA)
        if failure.startswith("canonical"):
            logout_headers = [headers for path, headers in web.headers_seen if path == "POST /auth/logout"]
            cookies = [headers["cookie"] for headers in logout_headers]
            assert len(cookies) == len(set(cookies))  # no retry, even lost logout
            if failure == "canonical-lost-login":
                assert not cookies and refused.value.cleanup_state == "unknown"
            else:
                assert f"{f.SESSION_COOKIE}={f.SESSION_VALUE}" in cookies
                assert web.logged_out
                for cookie in cookies:
                    assert any(path == "GET /auth/session" and headers.get("cookie") == cookie
                               for path, headers in web.headers_seen)
                    assert any(path == "GET /api/v1/auth/principal" and headers.get("cookie") == cookie
                               for path, headers in web.headers_seen)
            uncertain = {"canonical-malformed-login", "canonical-lost-login", "canonical-logout-unknown"}
            if failure in uncertain:
                # Latch at the FIRST uncertain effect: afterwards only bounded
                # owned-cookie cleanup reads/unattempted logout reach transport.
                seen = [path for path, _ in web.headers_seen]
                latch_at = (seen.index("POST /auth/logout") if failure == "canonical-logout-unknown"
                            else [i for i, p in enumerate(seen) if p == "POST /login"][1])
                after = web.headers_seen[latch_at + 1:]
                assert all(path in {"GET /auth/session", "GET /api/v1/auth/principal", "POST /auth/logout"}
                           for path, _ in after), [path for path, _ in after]
                assert all(headers.get("cookie") == f"{f.SESSION_COOKIE}={f.SESSION_VALUE}" for _, headers in after)
                assert "POST /login" not in [path for path, _ in after]
                assert refused.value.cleanup_state == "unknown"
        else:
            assert calls[-1] == failure
    else:
        result = c.consume(inputs=inputs, environment=env, sha=SHA, digest=DIGEST, pull_number=1, reviewed_head=SHA)
        assert result["gate_passed"] is True and result["full_product_acceptance"] is False
        assert calls == (["oidc", "source", "admission"] if failure == "canonical" else
                         ["oidc", "source", "admission", "gate"])
    assert "GH_TOKEN" not in env


@pytest.mark.parametrize("claim,value", [
    ("exp", 1), ("iat", True), ("nbf", int(time.time()) + 3600),
    ("iat", int(time.time()) - 3600),
], ids=["expired", "boolean-numeric-date", "future-not-before", "stale-issued-at"])
def test_native_token_lifetime_and_numeric_dates_cannot_be_forged(offline_signing_key, claim, value):
    claims = oidc_claims()
    claims[claim] = value
    token, keys = signed_offline_token(offline_signing_key, claims)
    with pytest.raises(c.ConsumerRefused):
        c.verify_native_oidc(token, keys, native_environment(), now=datetime.now(UTC))


def test_ambiguous_jwks_and_duplicate_json_refuse(offline_signing_key):
    token, keys = signed_offline_token(offline_signing_key, oidc_claims())
    keys["keys"].append(dict(keys["keys"][0]))
    with pytest.raises(c.ConsumerRefused):
        c.verify_native_oidc(token, keys, native_environment(), now=datetime.now(UTC))
    with pytest.raises(c.ConsumerRefused):
        c._unique_json(b'{"field":1,"field":2}')
    with pytest.raises(c.ConsumerRefused):
        c._unique_json(b'{"field":NaN}')


@pytest.mark.parametrize("body,status,mime", [
    (b'{"keys":[]}', 200, "application/json"),
    (b'{"keys":[],"keys":[]}', 200, "application/json"),
    (b"x" * 70_000, 200, "application/json"),
    (b'{"keys":[]}', 302, "application/json"),
    (b'{"keys":[]}', 200, "text/html"),
])
def test_native_oidc_documents_have_real_stream_bounds(body, status, mime):
    import httpx
    transport = httpx.MockTransport(lambda _: httpx.Response(status, content=body, headers={"content-type": mime}))
    with httpx.Client(transport=transport) as client:
        if body == b'{"keys":[]}' and status == 200 and mime == "application/json":
            assert c._bounded_json(client, "https://offline.invalid") == {"keys": []}
        else:
            with pytest.raises(c.ConsumerRefused):
                c._bounded_json(client, "https://offline.invalid")


@pytest.mark.parametrize("failure", [None, "provider", "sa", "sts", "iam", "oidc-host",
    "missing-smoke", "other-project", "deployer-as-smoke", "audience", "application-denied", "application-empty",
    "application-header", "application-oversized", "same-project-other-smoke", "transport-audience",
    "delegation-denied", "delegation-empty", "delegation-header", "delegation-expired", "delegation-overlong"])
def test_existing_google_wif_is_memory_only_and_exact_identity(monkeypatch, failure):
    from urllib.parse import parse_qs

    import httpx
    inputs = c.take_native_inputs(raw_inputs(), masker=lambda _: None)
    smoke = c.SMOKE_SERVICE_ACCOUNT
    # Actual observed protected-dev values, NOT a synthetic same-origin audience.
    assert smoke == "oday-dev-smoke-operator@odayplus-runtime-20260825.iam.gserviceaccount.com"
    assert c.APPLICATION_AUDIENCE == "https://oday-api-767864276141.asia-east1.run.app"
    assert c.APPLICATION_AUDIENCE != c.API_ORIGIN
    env = {"GOOGLE_WIF_PROVIDER": c.WIF_PROVIDER, "GOOGLE_SERVICE_ACCOUNT": c.GOOGLE_SERVICE_ACCOUNT,
           "ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT": smoke, "ODP_AUTH_AUDIENCES": c.APPLICATION_AUDIENCE}
    calls = []
    if failure == "provider":
        env["GOOGLE_WIF_PROVIDER"] = "projects/other/provider"
    if failure == "sa":
        env["GOOGLE_SERVICE_ACCOUNT"] = "other@other.iam.gserviceaccount.com"
    if failure == "oidc-host":
        inputs.oidc_request_url = "https://untrusted.invalid/oidc"
    if failure == "missing-smoke":
        env.pop("ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT")
    if failure == "other-project":
        env["ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT"] = "offline-smoke@other.iam.gserviceaccount.com"
    if failure == "deployer-as-smoke":
        env["ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT"] = c.GOOGLE_SERVICE_ACCOUNT
    if failure == "audience":
        env["ODP_AUTH_AUDIENCES"] = "https://untrusted.invalid," + c.APPLICATION_AUDIENCE
    if failure == "transport-audience":
        env["ODP_AUTH_AUDIENCES"] = c.API_ORIGIN
    if failure == "same-project-other-smoke":
        env["ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT"] = "other-smoke@odayplus-runtime-20260825.iam.gserviceaccount.com"

    def responder(request):
        calls.append(str(request.url).split("?")[0])
        if request.url.host == "pipelines.actions.githubusercontent.com":
            assert parse_qs(request.url.query.decode())["audience"] == [c.WIF_AUDIENCE]
            assert c.WIF_AUDIENCE == "https://iam.googleapis.com/" + c.WIF_PROVIDER
            assert request.headers["Authorization"] == "Bearer offline-native-oidc-request-token"
            return httpx.Response(200, json={"value": "offline-subject-jwt"})
        if request.url.host == "sts.googleapis.com":
            body = json.loads(request.content)
            assert body["audience"] == "//iam.googleapis.com/" + c.WIF_PROVIDER
            assert body["subjectToken"] == "offline-subject-jwt"
            if failure == "sts":
                return httpx.Response(403, json={"error": "synthetic denied"})
            return httpx.Response(200, json={"access_token": "offline-federated-token", "token_type": "Bearer", "expires_in": 300})
        if str(request.url) == f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{c.GOOGLE_SERVICE_ACCOUNT}:generateIdToken":
            assert request.headers["Authorization"] == "Bearer offline-federated-token"
            assert json.loads(request.content) == {"audience": c.API_ORIGIN, "includeEmail": True}
            return httpx.Response(403 if failure == "iam" else 200, json={"token": "offline-generated-invoker"})
        if str(request.url) == f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{c.GOOGLE_SERVICE_ACCOUNT}:generateAccessToken":
            assert request.headers["Authorization"] == "Bearer offline-federated-token"
            assert json.loads(request.content) == {"scope": ["https://www.googleapis.com/auth/cloud-platform"], "lifetime": "600s"}
            expiry = datetime.now(UTC) + timedelta(seconds={"delegation-expired": -1, "delegation-overlong": 3600}.get(failure, 600))
            value = {"delegation-empty": "", "delegation-header": "private\r\nmarker"}.get(failure, "offline-deployer-access-token")
            return httpx.Response(403 if failure == "delegation-denied" else 200,
                json={"accessToken": value, "expireTime": expiry.isoformat()})
        # Real smoke policy accepts ONLY deployer, never the federated principal.
        assert request.headers["Authorization"] == "Bearer offline-deployer-access-token"
        assert json.loads(request.content) == {"audience": c.APPLICATION_AUDIENCE, "includeEmail": True}
        assert str(request.url) == f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{smoke}:generateIdToken"
        application = {"application-empty": "", "application-header": "private\r\nmarker",
                       "application-oversized": "x" * 16_385}.get(failure, "offline-generated-application")
        return httpx.Response(403 if failure == "application-denied" else 200, json={"token": application})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(responder), **kwargs))
    monkeypatch.setattr(c, "_mask", lambda _: None)
    if failure:
        with pytest.raises(c.ConsumerRefused):
            c._native_gate_identity(inputs, env)
        if failure in {"provider", "sa", "oidc-host", "missing-smoke", "other-project", "deployer-as-smoke", "audience", "same-project-other-smoke", "transport-audience"}:
            assert not calls
    else:
        assert c._native_gate_identity(inputs, env) == ("offline-generated-invoker", "offline-generated-application")
        assert len(calls) == 5
    assert not set(c.SECRET_INPUTS + c.LEGACY_INPUTS) & set(env)


def source_observer_options():
    return dict(token="offline-native-github-read-token", pull_number=1, reviewed_head=SHA,
                review_writer_login=c.REVIEW_WRITER, review_writer_id=c.REVIEW_WRITER_ID,
                workflow_id=c.CI_WORKFLOW_ID, checks_app_id=c.CHECKS_APP_ID,
                required_checks=c.REQUIRED_CHECKS)


@pytest.mark.parametrize("body,status,headers", [
    (b'[{"state":"success"}]', 200, {}),
    (b'{"status":"ok"}', 200, {}),
    (b'{"status":"ok","status":"bad"}', 200, {}),
    (b'{"count":NaN}', 200, {}),
    (b'{}', 403, {}),
    (b'{}', 302, {"location": "https://untrusted.invalid/private"}),
    (b'{}', 200, {"link": '<https://api.github.com/next>; rel="next"'}),
    (b'"not-a-record"', 200, {}),
    (b'{"large":"' + b'x' * (2 * 1024 * 1024) + b'"}', 200, {}),
])
def test_native_source_stream_is_bounded_unambiguous_and_never_follows_pages(body, status, headers):
    import httpx
    calls = []
    def responder(request):
        calls.append(str(request.url))
        assert request.headers["Authorization"] == "Bearer offline-native-github-read-token"
        return httpx.Response(status, content=body, headers={"content-type": "application/json", **headers})
    observer = c.NativeSourceApprovalObserver(**source_observer_options(),
                                              transport=httpx.MockTransport(responder))
    try:
        if body in (b'[{"state":"success"}]', b'{"status":"ok"}'):
            assert observer._get("pulls/1") == json.loads(body)
        else:
            with pytest.raises(c.ConsumerRefused):
                observer._get("pulls/1")
        assert calls == [f"https://api.github.com/repos/{c.REPOSITORY}/pulls/1"]
    finally:
        observer.close()


def test_native_source_actual_client_has_no_proxy_trust(monkeypatch):
    import httpx
    real_client = httpx.Client
    options = []
    def client(**kwargs):
        options.append(kwargs)
        return real_client(**kwargs)
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted.invalid:3128")
    monkeypatch.setattr(httpx, "Client", client)
    observer = c.NativeSourceApprovalObserver(**source_observer_options())
    try:
        assert options[-1]["trust_env"] is False
        assert options[-1]["follow_redirects"] is False
        assert options[-1]["base_url"] == "https://api.github.com"
    finally:
        observer.close()


def test_native_source_still_uses_canonical_whole_tree_approval_predicates():
    assert c.NativeSourceApprovalObserver.observe is c.GitHubSourceApprovalObserver.observe
    assert c.NativeSourceApprovalObserver._ci is c.GitHubSourceApprovalObserver._ci


@pytest.mark.parametrize("failure", [None, "helper", "validator"])
def test_native_gh_env_exception_is_only_canonical_metadata_not_manifest_git(monkeypatch, failure):
    from delivery_toolchain.release import automatic_dev, recover_dev_smoke
    inputs = c.take_native_inputs(raw_inputs(), masker=lambda _: None)
    env = native_environment()
    calls = []
    deadline = time.monotonic() + 30
    manifest = {"candidate_sha": SHA, "release_profile": "dev-admin"}
    run = {"path": automatic_dev.WORKFLOW, "event": "workflow_dispatch",
           "repository": {"full_name": c.REPOSITORY}, "head_repository": {"full_name": c.REPOSITORY}}
    def metadata():
        calls.append("metadata")
        assert env["GH_TOKEN"] == inputs.github_token
        assert not set(c.LEGACY_INPUTS) & set(env)
        if failure == "helper":
            raise ValueError("offline metadata failure")
        return manifest, run
    def validator(args, **kwargs):
        calls.append("validator")
        assert "GH_TOKEN" not in env
        projected = kwargs["input_bytes"]
        assert json.loads(projected) == manifest
        assert kwargs["deadline"] == deadline
        assert args[0] == c.sys.executable and args[-2:] == (SHA, DIGEST)
        for private in (inputs.bundle.password, inputs.github_token, inputs.oidc_request_token,
                        inputs.oidc_request_url):
            assert private not in repr(args) and private.encode() not in projected
        if failure == "validator":
            raise ValueError("offline validator failure")
        return b""
    monkeypatch.setattr(automatic_dev, "previous_deployment_metadata", metadata)
    monkeypatch.setattr(recover_dev_smoke, "_bounded_observer_child", validator)
    if failure:
        with pytest.raises(ValueError):
            c._current_admission(sha=SHA, digest=DIGEST, inputs=inputs, environment=env, deadline=deadline)
    else:
        c._current_admission(sha=SHA, digest=DIGEST, inputs=inputs, environment=env, deadline=deadline)
    assert calls == (["metadata"] if failure == "helper" else ["metadata", "validator"])
    assert "GH_TOKEN" not in env


def test_native_manifest_actual_child_never_inherits_credentials_and_missing_git_never_fetches(monkeypatch):
    # Genuine local subprocess boundary. Synthetic missing source is NOT admission.
    import subprocess

    from delivery_toolchain.release import automatic_dev, recover_dev_smoke
    inputs = c.take_native_inputs(raw_inputs(), masker=lambda _: None)
    monkeypatch.setenv("GH_TOKEN", "offline-parent-secret-must-not-reach-git")
    monkeypatch.setenv("ODP_DEV_ADMIN_PASSWORD", "offline-parent-password-must-not-reach-git")
    run = {"path": automatic_dev.WORKFLOW, "event": "workflow_dispatch",
           "repository": {"full_name": c.REPOSITORY}, "head_repository": {"full_name": c.REPOSITORY}}
    monkeypatch.setattr(automatic_dev, "previous_deployment_metadata", lambda: ({"candidate_sha": SHA}, run))
    actual_popen = subprocess.Popen
    launches = []
    def popen(args, **kwargs):
        launches.append((args, kwargs))
        assert not set(c.SECRET_INPUTS + c.LEGACY_INPUTS) & set(kwargs["env"])
        assert kwargs["close_fds"] is True and kwargs["start_new_session"] is True
        assert "shell" not in kwargs
        assert "fetch" not in args[2]  # fixed cat-file refusal before validator can fetch
        return actual_popen(args, **kwargs)
    monkeypatch.setattr(subprocess, "Popen", popen)
    with pytest.raises(recover_dev_smoke.ObserverRefused) as exc:
        c._current_admission(sha=SHA, digest=DIGEST, inputs=inputs, environment=c.os.environ,
                             deadline=time.monotonic() + 20)
    assert exc.value.exit_code is not None and exc.value.exit_code != 0
    assert len(launches) == 1
    assert "GH_TOKEN" not in c.os.environ


def test_real_deployment_history_artifact_chain_never_runs_manifest_git_with_token(monkeypatch):
    """Actual history/ZIP/validator chain; only gh HTTP is synthetic, no admission proof."""
    import subprocess

    from delivery_toolchain.release import automatic_dev, recover_dev_smoke
    from tests.release import test_automatic_dev_deployment as f

    inputs = c.take_native_inputs(raw_inputs(), masker=lambda _: None)
    monkeypatch.setenv("GITHUB_REPOSITORY", c.REPOSITORY)
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    manifest = f.sources_off_manifest(
        components={name: {"image": f"registry.example.invalid/{name}@sha256:" + str(i) * 64}
                    for i, name in enumerate(automatic_dev.COMPONENTS, 1)},
        release_profile={"name": "dev-admin", "target_environment": "dev", "model_readiness": "not_claimed"},
    )
    metadata_calls = []
    fake_api = f.fake_api(manifest, seen=metadata_calls)
    actual_run = subprocess.run
    def run(args, **kwargs):
        if args[:2] == ["gh", "api"]:
            assert c.os.environ["GH_TOKEN"] == inputs.github_token
            path = args[2].removeprefix(f"repos/{c.REPOSITORY}/")
            data = fake_api(path, raw=path.endswith("/zip"))
            return subprocess.CompletedProcess(args, 0, data if isinstance(data, bytes) else json.dumps(data).encode(), b"")
        assert "GH_TOKEN" not in c.os.environ  # ordinary default validation is also token-free
        return actual_run(args, **kwargs)
    actual_child = recover_dev_smoke._bounded_observer_child
    children = []
    def child(args, **kwargs):
        assert "GH_TOKEN" not in c.os.environ
        assert json.loads(kwargs["input_bytes"]) == manifest
        children.append(args)
        return actual_child(args, **kwargs)
    actual_validate = automatic_dev.validate_release_admission
    def validate(*args, **kwargs):
        assert "GH_TOKEN" not in c.os.environ
        return actual_validate(*args, **kwargs)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(recover_dev_smoke, "_bounded_observer_child", child)
    monkeypatch.setattr(automatic_dev, "validate_release_admission", validate)
    c._current_admission(sha=manifest["candidate_sha"], digest=manifest["manifest_digest"],
                         inputs=inputs, environment=c.os.environ, deadline=time.monotonic() + 30)
    assert len(children) == 1
    assert any(path.endswith("/zip") for path in metadata_calls)
    assert any("attempts/1/jobs" in path for path in metadata_calls)
    assert "GH_TOKEN" not in c.os.environ
    # Ordinary previous_deployment retains the canonical validation; metadata
    # extraction is NOT a new admission bypass for the existing release caller.
    monkeypatch.setattr(automatic_dev, "api", fake_api)
    assert automatic_dev.previous_deployment()[0] == manifest

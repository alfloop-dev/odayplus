"""Fresh, read-only dev bundle consumer. Native CI ingress is an explicit proposal.

Not a foreground recovery/writer entrypoint. Native runner environment is a
trusted credential ingress ONLY after independent approval of this exact source
and its documented boundary; no worker/offline test may dispatch it. It performs
no rotation, secret PUT/ACK, rollout, provider or business action. All credential
values stay in this process after the native ingress; reports are allowlisted.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import signal
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from delivery_toolchain.e2e import check_live_e2e_gate as gate
from delivery_toolchain.release.provision_dev_smoke import (
    GitHubSourceApprovalObserver,
    read_dev_credential_bundle,
)
from shared.identity.dev_smoke_recovery import RECOVERY_BINDING_TYPE

REPOSITORY = "alfloop-dev/odayplus"
WORKFLOW = ".github/workflows/dev-smoke-credential-consumer.yml"
API_ORIGIN = "https://oday-api-2l6wuyl67q-de.a.run.app"
WEB_ORIGIN = "https://oday-web-767864276141.asia-east1.run.app"
REVIEW_WRITER = "ajoe734"
REVIEW_WRITER_ID = 169176954
CI_WORKFLOW_ID = 302984635
CHECKS_APP_ID = 15368
REQUIRED_CHECKS = (
    "change-scope", "orchestrator", "product-lint-unit", "product-db",
    "product-api-contract", "product-security", "product-node",
    "performance-gate", "product-e2e-gate", "product",
)
NATIVE_AUDIENCE = "odayplus.dev-smoke.native-consumer.v1"
OIDC_ISSUER = "https://token.actions.githubusercontent.com"
WIF_PROVIDER = "projects/767864276141/locations/global/workloadIdentityPools/github-actions/providers/odayplus"
# Existing provider allowedAudiences is empty: Google requires the canonical
# //iam.googleapis.com resource or its HTTPS form, not the bare projects/... id.
WIF_AUDIENCE = f"https://iam.googleapis.com/{WIF_PROVIDER}"
GOOGLE_SERVICE_ACCOUNT = "github-deployer@odayplus-runtime-20260825.iam.gserviceaccount.com"
SECRET_INPUTS = ("ODP_DEV_ADMIN_CREDENTIAL_BUNDLE", "GH_TOKEN",
                 "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL")
LEGACY_INPUTS = (
    "ODP_DEV_ADMIN_USERNAME", "ODP_DEV_ADMIN_PASSWORD", "ODP_DEV_ADMIN_INITIAL_PASSWORD",
    "ODP_DEV_BOOTSTRAP_ADMIN_USERNAME", "ODP_DEV_BOOTSTRAP_ADMIN_PASSWORD",
    "ODP_OPERATOR_SMOKE_BEARER_TOKEN", "ODP_API_INVOKER_TOKEN",
)
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ConsumerRefused(RuntimeError):
    def __init__(self, *, cleanup_state: str = "unknown") -> None:
        self.cleanup_state = cleanup_state
        super().__init__("DEV_SMOKE_NATIVE_CONSUMER_REFUSED")


@dataclass(repr=False)
class NativeInputs:
    bundle: Any = field(repr=False)
    github_token: str = field(repr=False)
    oidc_request_token: str = field(repr=False)
    oidc_request_url: str = field(repr=False)
    invoker_token: str = field(default="", repr=False)
    application_token: str = field(default="", repr=False)

    def __reduce__(self) -> Any:
        raise ConsumerRefused()


def _mask(value: str) -> None:
    # The trusted runner interprets this control command and excludes its value
    # from retained logs. Never log the raw envelope or use a password hash.
    escaped = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::add-mask::{escaped}", flush=True)


def take_native_inputs(environment: dict[str, str], *, masker: Any = _mask) -> NativeInputs:
    """Destructively remove ALL ingress/fallback credentials before network/children."""
    values = {key: environment.pop(key, "") for key in SECRET_INPUTS}
    legacy = [environment.pop(key, "") for key in LEGACY_INPUTS]
    try:
        raw = values[SECRET_INPUTS[0]]
        if (any(legacy) or type(raw) is not str or not 1 <= len(raw.encode()) <= 16_384
                or any(not values[key] for key in SECRET_INPUTS[1:])):
            raise ConsumerRefused()
        bundle = read_dev_credential_bundle(raw, environment="dev", release_profile="dev-admin")
        if bundle is None or bundle.schema_version != 2 or bundle.recovery_lineage is None:
            raise ConsumerRefused()
        for value in (bundle.username, bundle.password, *[values[key] for key in SECRET_INPUTS[1:]]):
            masker(value)
        return NativeInputs(bundle, *[values[key] for key in SECRET_INPUTS[1:]])
    except Exception:
        raise ConsumerRefused() from None


def _aware(value: Any) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConsumerRefused()
    return parsed


def validate_run(run: Any, environment: dict[str, str], *, expected_sha: str, now: datetime) -> datetime:
    """Authenticated GitHub run record, not a caller-supplied native approval flag."""
    try:
        created = _aware(run["created_at"])
        if (type(run) is not dict or not _SHA.fullmatch(expected_sha)
                or environment.get("GITHUB_REPOSITORY") != REPOSITORY
                or environment.get("GITHUB_REF") != "refs/heads/dev"
                or environment.get("GITHUB_SHA") != expected_sha
                or environment.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
                or environment.get("GITHUB_ACTIONS") != "true"
                or environment.get("GITHUB_ACTOR") != REVIEW_WRITER
                or environment.get("GITHUB_RUN_ATTEMPT") != "1"
                or type(run.get("run_attempt")) is not int or run["run_attempt"] != 1
                or str(run["id"]) != environment.get("GITHUB_RUN_ID")
                or run["repository"]["full_name"] != REPOSITORY
                or run["head_repository"]["full_name"] != REPOSITORY
                or run["head_sha"] != expected_sha or run["head_branch"] != "dev"
                or run["event"] != "workflow_dispatch" or run["path"] != WORKFLOW
                or run["actor"]["login"] != REVIEW_WRITER or run["actor"]["id"] != REVIEW_WRITER_ID
                or run["status"] != "in_progress" or run.get("conclusion") is not None
                or created > now or created < now - timedelta(minutes=20)):
            raise ConsumerRefused()
        return created
    except Exception:
        raise ConsumerRefused() from None


def _b64url(value: str) -> bytes:
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ConsumerRefused()
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _unique_json(data: bytes, *, allow_list: bool = False) -> Any:
    def pairs(items: Any) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ConsumerRefused()
            result[key] = value
        return result
    def constant(_value: str) -> Any:
        raise ConsumerRefused()
    parsed = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    if type(parsed) not in ((dict, list) if allow_list else (dict,)):
        raise ConsumerRefused()
    return parsed


def verify_native_oidc(token: str, jwks: Any, environment: dict[str, str], *, now: datetime) -> None:
    """Fixed RS256 JWS via existing frozen cryptography, issuer-pinned JWKS.

    No token minting/custom signature algorithm. A claimed GITHUB_ACTIONS env
    or public run id alone cannot authenticate a native runner.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    try:
        if type(token) is not str or len(token) > 16_384:
            raise ConsumerRefused()
        encoded_header, encoded_payload, signature = token.split(".")
        header = _unique_json(_b64url(encoded_header))
        claims = _unique_json(_b64url(encoded_payload))
        keys = [key for key in jwks["keys"] if key.get("kid") == header.get("kid")]
        if (len(keys) != 1 or header.get("alg") != "RS256" or header.get("typ") != "JWT"
                or "crit" in header or keys[0].get("kty") != "RSA"
                or keys[0].get("use") != "sig" or keys[0].get("alg") != "RS256"):
            raise ConsumerRefused()
        key = keys[0]
        numbers = rsa.RSAPublicNumbers(int.from_bytes(_b64url(key["e"])), int.from_bytes(_b64url(key["n"])))
        public = numbers.public_key()
        if not 2048 <= public.key_size <= 8192:
            raise ConsumerRefused()
        public.verify(_b64url(signature), f"{encoded_header}.{encoded_payload}".encode("ascii"),
                      padding.PKCS1v15(), hashes.SHA256())
        if (claims.get("iss") != OIDC_ISSUER or claims.get("aud") != NATIVE_AUDIENCE
                or any(type(claims.get(key)) is not int for key in ("exp", "iat", "nbf"))
                or claims["nbf"] > now.timestamp() or claims["exp"] <= now.timestamp()):
            raise ConsumerRefused()
        expected = {
            "repository": REPOSITORY, "ref": "refs/heads/dev", "environment": "dev",
            "event_name": "workflow_dispatch", "actor": REVIEW_WRITER, "actor_id": str(REVIEW_WRITER_ID),
            "sha": environment["GITHUB_SHA"], "workflow_sha": environment["GITHUB_SHA"],
            "workflow_ref": f"{REPOSITORY}/{WORKFLOW}@refs/heads/dev",
            "run_id": environment["GITHUB_RUN_ID"], "run_attempt": "1",
            "runner_environment": "github-hosted", "sub": f"repo:{REPOSITORY}:environment:dev",
        }
        if (any(claims.get(key) != value for key, value in expected.items())
                or claims["iat"] > now.timestamp() or now.timestamp() - claims["iat"] > 300):
            raise ConsumerRefused()
    except Exception:
        raise ConsumerRefused() from None


def _bounded_json(client: Any, url: str, *, headers: Any = None,
                  method: str = "GET", json_payload: Any = None,
                  max_bytes: int = 65_536, allow_list: bool = False) -> Any:
    with client.stream(method, url, headers=headers, json=json_payload) as response:
        if (response.status_code != 200 or 'rel="next"' in response.headers.get("link", "")
                or response.headers.get("content-type", "").split(";")[0]
                not in {"application/json", "application/jwk-set+json"}):
            raise ConsumerRefused()
        body = bytearray()
        for chunk in response.iter_bytes(chunk_size=4096):
            if len(body) + len(chunk) > max_bytes:
                raise ConsumerRefused()
            body.extend(chunk)
        return _unique_json(bytes(body), allow_list=allow_list)


class NativeSourceApprovalObserver(GitHubSourceApprovalObserver):
    """Unchanged canonical approval predicates, bounded no-proxy native ingress.

    A single page is bounded to2MiB; incomplete/duplicate evidence refuses.
    No public unauthenticated/PAT fallback, retry, credential environment or
    substitute approval. Canonical exact reviewed/candidate trees and latest CI
    are still enforced by the inherited observer, not by this transport.
    """
    def __init__(self, **kwargs: Any) -> None:
        import httpx

        super().__init__(**kwargs)  # Retain canonical config validation.
        self._client.close()
        self._client = httpx.Client(
            base_url="https://api.github.com", transport=kwargs.get("transport"), timeout=20,
            follow_redirects=False, trust_env=False, headers={
                "Authorization": f"Bearer {kwargs['token']}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def _get(self, path: str) -> Any:
        return _bounded_json(self._client, f"/repos/{REPOSITORY}/{path}",
                             max_bytes=2 * 1024 * 1024, allow_list=True)


def _runner_jwt(client: Any, inputs: NativeInputs, *, audience: str) -> str:
    url = urlsplit(inputs.oidc_request_url)
    if (url.scheme != "https" or url.hostname != "pipelines.actions.githubusercontent.com"
            or url.username or url.password or url.port not in {None, 443} or url.fragment):
        raise ConsumerRefused()
    query = [(key, value) for key, value in parse_qsl(url.query) if key != "audience"]
    request_url = urlunsplit((url.scheme, url.netloc, url.path, urlencode([*query, ("audience", audience)]), ""))
    token = _bounded_json(client, request_url,
                          headers={"Authorization": f"Bearer {inputs.oidc_request_token}"})["value"]
    if type(token) is not str or not token or len(token) > 16_384:
        raise ConsumerRefused()
    _mask(token)
    return token


def _native_oidc(inputs: NativeInputs, environment: dict[str, str]) -> None:
    import httpx

    try:
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
            token = _runner_jwt(client, inputs, audience=NATIVE_AUDIENCE)
            keys = _bounded_json(client, f"{OIDC_ISSUER}/.well-known/jwks")
            verify_native_oidc(token, keys, environment, now=datetime.now(UTC))
    except Exception:
        raise ConsumerRefused() from None


def _native_gate_identity(inputs: NativeInputs, environment: dict[str, str]) -> tuple[str, str]:
    """Existing deploy identities via memory-only HTTPS; never grants IAM/roles.

    The protected dev variables select the SAME smoke SA and first application
    audience as deploy_cloud_run_waji.sh. These non-secret configuration inputs
    require fresh reconciliation in the independently reviewed execution plan;
    matching syntax is not evidence of an existing impersonation/RBAC grant.
    """
    import httpx

    try:
        if (environment.get("GOOGLE_WIF_PROVIDER") != WIF_PROVIDER
                or environment.get("GOOGLE_SERVICE_ACCOUNT") != GOOGLE_SERVICE_ACCOUNT):
            raise ConsumerRefused()
        smoke_account = environment.get("ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT", "")
        audience = environment.get("ODP_AUTH_AUDIENCES", "").split(",")[0]
        # No arbitrary project/URL/header injection or fallback to the deployer
        # token. Missing ordinary config refuses BEFORE federation/network.
        if (not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]@odayplus-runtime-20260825\.iam\.gserviceaccount\.com", smoke_account)
                or smoke_account == GOOGLE_SERVICE_ACCOUNT or audience != API_ORIGIN):
            raise ConsumerRefused()
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
            subject = _runner_jwt(client, inputs, audience=WIF_AUDIENCE)
            sts = _bounded_json(client, "https://sts.googleapis.com/v1/token", method="POST", json_payload={
                "audience": f"//iam.googleapis.com/{WIF_PROVIDER}",
                "grantType": "urn:ietf:params:oauth:grant-type:token-exchange",
                "requestedTokenType": "urn:ietf:params:oauth:token-type:access_token",
                "subjectTokenType": "urn:ietf:params:oauth:token-type:jwt", "subjectToken": subject,
                "scope": "https://www.googleapis.com/auth/cloud-platform",
            })
            access = sts["access_token"]
            if (type(access) is not str or not access or sts.get("token_type") != "Bearer"
                    or type(sts.get("expires_in")) is not int or sts["expires_in"] <= 0):
                raise ConsumerRefused()
            _mask(access)
            result = _bounded_json(client,
                f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{GOOGLE_SERVICE_ACCOUNT}:generateIdToken",
                method="POST", headers={"Authorization": f"Bearer {access}"},
                json_payload={"audience": API_ORIGIN, "includeEmail": True})
            token = result["token"]
            if type(token) is not str or not token or len(token) > 16_384:
                raise ConsumerRefused()
            _mask(token)
            application = _bounded_json(client,
                f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{smoke_account}:generateIdToken",
                method="POST", headers={"Authorization": f"Bearer {access}"},
                json_payload={"audience": audience, "includeEmail": True})["token"]
            if (type(application) is not str or not application or len(application) > 16_384
                    or any(ch in application for ch in "\r\n")):
                raise ConsumerRefused()
            _mask(application)
            return token, application
    except Exception:
        raise ConsumerRefused() from None


@contextmanager
def _absolute_deadline(deadline: float):
    """Linux main-thread bounded work; late/stuck termination is never success."""
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "setitimer"):
        raise ConsumerRefused()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ConsumerRefused()
    previous = signal.getsignal(signal.SIGALRM)
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise ConsumerRefused()

    def expired(_signum: int, _frame: Any) -> None:
        raise ConsumerRefused()

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        yield
        if time.monotonic() >= deadline:
            raise ConsumerRefused()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class _ObservedWeb:
    """Memory-only owned cookies plus safe ACK times; no account-wide cleanup.

    A login may commit even when its reply is lost or malformed. Such custody
    loss is UNKNOWN, never successful cleanup, and cannot authorize another login.
    """
    def __init__(self, client: Any, *, execution_id: str, deadline: float, cleanup_deadline: float) -> None:
        self.client = client
        self.execution_id = execution_id
        self.deadline = deadline
        self.cleanup_deadline = cleanup_deadline
        self.ack_times: list[datetime] = []
        self._owned: dict[str, dict[str, bool]] = {}
        self._cleanup_unknown = False
        self._cleaning = False

    def cleanup_owned_sessions(self) -> bool:
        """One logout per owned cookie, even after evaluator early return/exception.

        Never retry a lost logout; later 401 cannot erase its uncertainty. The
        absolute reserve bounds every request and the whole finite inventory.
        """
        self._cleaning = True
        try:
            for cookie, state in self._owned.items():
                headers = {"accept": "application/json", "origin": WEB_ORIGIN,
                           "cookie": f"__Host-oday_web_session={cookie}"}
                if not state["logout_attempted"]:
                    self.request("POST", "/auth/logout", authenticated=False,
                                 headers=headers, follow_redirects=False)
                # Always verify using the ORIGINAL cookie, not a cleared jar.
                session = self.request("GET", "/auth/session", authenticated=False,
                                       headers=headers, follow_redirects=False)
                principal = self.request("GET", "/api/v1/auth/principal", authenticated=False,
                                         headers=headers, follow_redirects=False)
                if (not state["logout_acknowledged"] or session.failed or session.status != 401
                        or principal.failed or principal.status not in {401, 403}):
                    self._cleanup_unknown = True
            return not self._cleanup_unknown
        finally:
            self._owned.clear()  # Best effort reference release, not zeroization.
            self._cleaning = False

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        header = (kwargs.get("headers") or {}).get("cookie", "")
        cookie = header.removeprefix("__Host-oday_web_session=")
        state = self._owned.get(cookie)
        if method == "POST" and path == "/auth/logout" and state is not None:
            if state["logout_attempted"]:
                self._cleanup_unknown = True
                return gate.HttpResponse(status=0, error="native consumer duplicate logout refused")
            state["logout_attempted"] = True  # Intent before the single request.
        cleanup = path in {"/auth/logout", "/auth/session", "/api/v1/auth/principal"} and (self._cleaning or time.monotonic() >= self.deadline)
        limit = self.cleanup_deadline if cleanup else self.deadline
        try:
            with _absolute_deadline(limit):
                response = self.client.request(method, path, **kwargs)
            if method == "POST" and path == "/login":
                issued = response.cookies.get("__Host-oday_web_session", "")
                if isinstance(issued, str) and issued and len(issued) <= 8192 and not any(ch in issued for ch in ";\r\n"):
                    if len(self._owned) >= 4 or issued in self._owned:
                        self._cleanup_unknown = True
                    else:
                        self._owned[issued] = {"logout_attempted": False, "logout_acknowledged": False}
                elif response.failed or response.status not in {401, 303} or any(response.cookies.values()):
                    self._cleanup_unknown = True
                elif response.status == 303 and "error=AUTH_INVALID_CREDENTIALS" not in response.location:
                    self._cleanup_unknown = True
            if method == "POST" and path == "/auth/logout" and state is not None:
                state["logout_acknowledged"] = (not response.failed and response.status == 200
                    and response.payload.get("ok") is True
                    and response.cookies.get("__Host-oday_web_session") == "")
            if method == "GET" and path == "/api/v1/operator/users/audit-trail" and response.status == 200:
                events = response.payload.get("events", [])
                for event in events:
                    meta = event.get("metadata", {})
                    if (event.get("event_type") == RECOVERY_BINDING_TYPE
                            and meta.get("execution_id") == self.execution_id
                            and meta.get("stage") == "binding-acknowledged" and meta.get("put_status") == 201):
                        self.ack_times.append(_aware(event["timestamp"]))
            return response
        except Exception:
            if method == "POST" and path == "/login":
                self._cleanup_unknown = True
            return gate.HttpResponse(status=0, error="native consumer bounded request refused")


def _current_admission(*, sha: str, digest: str, inputs: NativeInputs,
                       environment: dict[str, str], deadline: float) -> None:
    # Deliberately NOT DeployedDevReleaseObserver: its foreground-only guard is
    # unchanged. Here the authenticated own run is proven a different, NO-DEPLOY
    # workflow. Excluding its id from deployment history cannot hide a deploy.
    from delivery_toolchain.release import automatic_dev
    from delivery_toolchain.release.recover_dev_smoke import _bounded_observer_child

    # Sole native GH_TOKEN exception: only the existing GitHub metadata helper
    # receives it. Canonical manifest predicates spawn Git internally, so run
    # those separately with manifest-only stdin and the existing sanitized,
    # absolute-deadline process-group adapter. Exec/fork is NOT memory isolation.
    environment["GH_TOKEN"] = inputs.github_token
    try:
        found = automatic_dev.previous_deployment_metadata()
    finally:
        environment.pop("GH_TOKEN", None)
    if found is None:
        raise ConsumerRefused()
    manifest, run = found
    if (run.get("path") != automatic_dev.WORKFLOW or run.get("event") != "workflow_dispatch"
            or run.get("repository", {}).get("full_name") != REPOSITORY
            or run.get("head_repository", {}).get("full_name") != REPOSITORY):
        raise ConsumerRefused()
    code = (
        "import json, subprocess, sys\n"
        "from delivery_toolchain.release.release_manifest import "
        "validate_manifest, validate_release_admission, manifest_release_profile\n"
        "m=json.loads(sys.stdin.buffer.read(2000001))\n"
        "sha,digest=sys.argv[1:]\n"
        "subprocess.run(['/usr/bin/git','cat-file','-e',sha+'^{commit}'],check=True,"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        "errors=validate_manifest(m,expected_candidate_sha=sha,expected_digest=digest)\n"
        "errors+=validate_release_admission(m,environment='dev')\n"
        "sys.exit(1 if errors or manifest_release_profile(m)!='dev-admin' or "
        "m.get('external_sources_expected_enabled')!=[] else 0)\n"
    )
    _bounded_observer_child((sys.executable, "-c", code, sha, digest),
        cwd=Path(__file__).resolve().parents[2], deadline=deadline,
        input_bytes=json.dumps(manifest, separators=(",", ":"), allow_nan=False).encode())


def safe_consumption_report(report: Any, *, ack_times: list[datetime], job_created: datetime,
                            sha: str, digest: str, run_id: str) -> dict[str, Any]:
    """Never persist arbitrary report details, audit payloads, tokens or PHCs."""
    try:
        checks = report["checks"]
        required = {"admin:credential_bundle_account_bound", "admin:credential_bundle_durable_acknowledgement",
                    "session:logout", "session:revoked_session_refused", "admin:logout_revokes_admin_api",
                    "session:html_form_logout", "session:html_form_revoked_session_refused"}
        successful = {check["name"] for check in checks if check["ok"] is True}
        if (report.get("ok") is not True or any(check.get("ok") is not True for check in checks)
                or not required <= successful
                or len(ack_times) != 1 or not ack_times[0] < job_created
                or not _SHA.fullmatch(sha) or not _DIGEST.fullmatch(digest)
                or not run_id.isdecimal() or int(run_id) <= 0):
            raise ConsumerRefused()
        return {"schema": "odp.dev-smoke.native-consumption.v1", "credential_source": "github-dev-encrypted-matched-bundle",
                "bundle_schema_version": 2, "release_sha": sha, "manifest_digest": digest,
                "run_id": run_id, "run_created_at": job_created.isoformat(),
                "acknowledged_before_run_creation": True, "gate_passed": True, "own_session_cleanup_verified": True,
                "recovery_rotation_performed": False, "secret_write_performed": False,
                "full_product_acceptance": False, "secret_values_in_report": False}
    except Exception:
        raise ConsumerRefused() from None


def consume(*, inputs: NativeInputs, environment: dict[str, str], sha: str, digest: str,
            pull_number: int, reviewed_head: str) -> dict[str, Any]:
    import httpx

    started = time.monotonic()
    web = None
    # Direct HTTP observers need no credential environment. GH_TOKEN is scoped
    # only inside the existing canonical metadata helper below.
    try:
        with _absolute_deadline(started + 180), httpx.Client(
            base_url="https://api.github.com", timeout=20, follow_redirects=False, trust_env=False,
            headers={"Authorization": f"Bearer {inputs.github_token}", "Accept": "application/vnd.github+json"},
        ) as github:
            run_id = environment.get("GITHUB_RUN_ID", "")
            if not run_id.isdecimal() or int(run_id) <= 0:
                raise ConsumerRefused()
            record = _bounded_json(github, f"/repos/{REPOSITORY}/actions/runs/{run_id}")
            created = validate_run(record, environment, expected_sha=sha, now=datetime.now(UTC))
            _native_oidc(inputs, environment)
            observer = NativeSourceApprovalObserver(
                token=inputs.github_token, pull_number=pull_number, reviewed_head=reviewed_head,
                review_writer_login=REVIEW_WRITER, review_writer_id=REVIEW_WRITER_ID,
                workflow_id=CI_WORKFLOW_ID, checks_app_id=CHECKS_APP_ID, required_checks=REQUIRED_CHECKS,
            )
            try:
                observer.observe(release_sha=sha, now=datetime.now(UTC))
            finally:
                observer.close()
            _current_admission(sha=sha, digest=digest, inputs=inputs,
                               environment=environment, deadline=started + 180)
            inputs.invoker_token, inputs.application_token = _native_gate_identity(inputs, environment)
        correlation = f"corr-native-consumer-{run_id}"
        config = gate.GateConfig(
            api_url=API_ORIGIN, expected_sha=sha, bearer_token=inputs.application_token, operator_role="platform_admin",
            web_url=WEB_ORIGIN, expected_deployment="dev", api_transport_token=inputs.invoker_token,
            required_provider_ids=(), external_provider_mode="disabled", release_profile="dev-admin",
            expected_manifest_digest=digest, dev_admin_username=inputs.bundle.username,
            dev_admin_password=inputs.bundle.password, dev_admin_initial_password="",
            dev_admin_bundle_account_id=inputs.bundle.account_id, dev_admin_bundle_tenant_id=inputs.bundle.tenant_id,
            dev_admin_bundle_execution_id=inputs.bundle.execution_id,
            dev_admin_bundle_schema_version=2, dev_admin_bundle_recovery_lineage=inputs.bundle.recovery_lineage,
            dev_admin_denied_role="cs-lead", timeout=15,
        )
        if not all(check.ok is True for check in gate.validate_config(config)):
            raise ConsumerRefused()
        api = _ObservedWeb(gate.UrllibHttpClient(API_ORIGIN, timeout=15, bearer_token=inputs.application_token, operator_role="platform_admin",
            operator_subject="live-e2e-gate", operator_tenant="", correlation_id=correlation,
            transport_token=inputs.invoker_token), execution_id=inputs.bundle.execution_id,
            deadline=started + 480, cleanup_deadline=started + 540)
        web = _ObservedWeb(gate._web_client(config, correlation), execution_id=inputs.bundle.execution_id,
                           deadline=started + 480, cleanup_deadline=started + 540)
        # Canonical checks are unchanged; this wrapper also owns unconditional
        # cleanup on every evaluator exit, including early provenance refusal.
        # Never spawn the CLI or populate credential env/per-field fallback.
        _, report = gate.evaluate_gate(config, http=api, web_http=web,
            worker_driver=gate.ScheduledWorkerDriver(), correlation_id=correlation,
            now=datetime.now(UTC).isoformat())
        if time.monotonic() >= started + 540:
            raise ConsumerRefused()
        return safe_consumption_report(report, ack_times=web.ack_times, job_created=created,
                                       sha=sha, digest=digest, run_id=run_id)
    except Exception:
        raise ConsumerRefused() from None
    finally:
        environment.pop("GH_TOKEN", None)
        if web is not None and not web.cleanup_owned_sessions():
            raise ConsumerRefused(cleanup_state="unknown") from None


def main() -> int:
    parser = argparse.ArgumentParser(description="Consume the already-staged dev matched bundle; no release/writes")
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--manifest-digest", required=True)
    parser.add_argument("--source-pull", type=int, required=True)
    parser.add_argument("--reviewed-head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        inputs = take_native_inputs(os.environ)
        report = consume(inputs=inputs, environment=os.environ, sha=args.release_sha, digest=args.manifest_digest,
                         pull_number=args.source_pull, reviewed_head=args.reviewed_head)
        args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(report, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        print("Dev matched bundle consumed; own cleanup verified; full acceptance NOT claimed")
        return 0
    except Exception:
        print("DEV_SMOKE_NATIVE_CONSUMER_REFUSED", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

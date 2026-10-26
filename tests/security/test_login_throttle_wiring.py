"""Login throttle is wired into the production login path and is the only one.

Task: ODP-WEB-LOGIN-THROTTLE-REMEDIATION-001
Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §2.2, §6.4

ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-001 recorded two blockers against §6.4 as
``xfail(strict=True)`` guards:

- **B1** the throttle service had no production call site, so ``/login`` was
  not throttled at all;
- **B2** the throttle layer had no durable repository over
  ``identity.login_attempts``, so state could not be shared between Cloud Run
  instances.

Those guards asserted the blockers against a Python service. The production
login path is the TypeScript ``/login`` route, so the remediation is a
TypeScript throttle reading ``identity.login_attempts`` directly and the
retirement of the Python prototype. These tests are the passing form of the
same two guards, restated against the shipped architecture, plus a third that
keeps the "one mechanism" property from regressing.

The behavioural coverage lives with the implementation
(``apps/web/src/lib/auth/__tests__/loginThrottle*.test.ts``); what is checked
here is the wiring those tests cannot observe from inside the module.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

LOGIN_ROUTE = ROOT / "apps/web/src/app/login/route.ts"
THROTTLE_MODULE = ROOT / "apps/web/src/lib/auth/loginThrottle.ts"
DEPLOY_SCRIPT = ROOT / "product_ops/deployment/deploy_cloud_run_waji.sh"


def _git_grep(pattern: str, *paths: str) -> set[str]:
    """Files matching ``pattern``, using the checked-in tree only."""
    completed = subprocess.run(
        ["git", "grep", "-lE", pattern, "--", *paths],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return {line for line in completed.stdout.splitlines() if line}


def test_b1_login_route_drives_the_throttle_before_verifying_credentials() -> None:
    """B1: the production /login route is the throttle's call site."""
    source = LOGIN_ROUTE.read_text(encoding="utf-8")

    assert "getDefaultLoginThrottle" in source
    assert "beginAttempt" in source
    assert "recordFailure" in source
    assert "recordSuccess" in source

    # Ordering is the security property: the gate has to run before the
    # credential is verified, otherwise a locked account still gets password
    # attempts and the counter can be skipped entirely.
    assert source.index("beginAttempt") < source.index(
        "authenticateLocalCredentials(username, password)"
    )


def test_b2_throttle_state_is_durable_in_identity_login_attempts() -> None:
    """B2: the throttle has a repository over identity.login_attempts."""
    source = THROTTLE_MODULE.read_text(encoding="utf-8")

    assert "class PostgresLoginThrottleStore" in source
    assert "identity.login_attempts" in source
    # Concurrent Cloud Run instances must not lose an increment.
    assert "FOR UPDATE" in source

    # The in-memory store must never be reachable in a production runtime.
    assert "isProductionWebRuntime(environment)) return null" in source


def test_no_parallel_throttle_mechanism_remains() -> None:
    """The retired Python prototype must not come back as a second mechanism."""
    assert _git_grep("LoginThrottleService", "apps", "shared", "modules", "product_ops") == set()
    assert not (ROOT / "shared/identity/login_throttle.py").exists()

    # Exactly one module issues statements against the table, so there is no
    # second store to drift away from the first.
    writers = _git_grep(
        r"(INTO|FROM|UPDATE) identity\.login_attempts",
        "apps",
        "shared",
        "modules",
        "product_ops",
    )
    assert writers == {"apps/web/src/lib/auth/loginThrottle.ts"}


def _without_comments(source: str) -> str:
    """Drop ``//`` lines so a guard reads emitted code, not the prose about it."""
    return "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("//")
    )


def test_pre_verification_refusals_never_claim_the_account_is_locked() -> None:
    """423 must be reachable only after a password has been proven correct.

    The throttle gate runs before ``authenticateLocalCredentials``. If that gate
    answered ``AUTH_ACCOUNT_LOCKED`` it would put an account-shaped statement on
    a response an attacker can trigger with any password, so both throttle
    dimensions answer ``AUTH_RATE_LIMITED`` instead.
    """
    source = _without_comments(LOGIN_ROUTE.read_text(encoding="utf-8"))

    gate = source.index("if (!gate.allowed)")
    verify = source.index("authenticateLocalCredentials(username, password)")
    pre_verification = source[gate:verify]

    assert "AUTH_RATE_LIMITED" in pre_verification
    assert "AUTH_ACCOUNT_LOCKED" not in pre_verification
    assert "423" not in pre_verification

    # After verification, a lock is the only thing that may answer 423.
    post_verification = source[verify:]
    assert 'AUTH_ACCOUNT_LOCKED"' in post_verification
    assert post_verification.count("423") == 1


def test_throttle_fails_closed_without_a_pepper_in_production() -> None:
    """An unpeppered attempt key is reversible, so production refuses to write one.

    Both attempt-key inputs are enumerable offline: the IPv4 space is 2^32 and
    usernames come from a dictionary. A production runtime that has a database
    but neither ``ODP_WEB_LOGIN_THROTTLE_PEPPER`` nor ``ODP_WEB_SESSION_SECRET``
    therefore resolves no throttle at all, which the route turns into a 503.
    """
    source = _without_comments(THROTTLE_MODULE.read_text(encoding="utf-8"))

    factory = source[source.index("export function getDefaultLoginThrottle") :]
    guard = factory[: factory.index("_defaultStore = new PostgresLoginThrottleStore()")]

    assert "isProductionWebRuntime(environment)" in guard
    assert "!resolveThrottlePepper(environment)" in guard
    assert "return null" in guard


def test_web_deployment_binds_database_secret_for_cross_instance_throttle() -> None:
    """The Cloud Run Web service binds ODAY_DATABASE_URL to share throttle state.

    PostgresLoginThrottleStore connects via ODAY_DATABASE_URL to write
    identity.login_attempts. The deployment script must inject this secret
    into the Web revision so replicas share one persistent store rather
    than falling back or failing closed with 503.
    """
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert 'WEB_SECRET_BINDINGS="ODAY_DATABASE_URL=${ODAY_DATABASE_URL_SECRET}"' in text


def test_web_deployment_mounts_cloudsql_instance_for_database_socket() -> None:
    """The Web gcloud run deploy must mount the Cloud SQL Auth Proxy.

    ODAY_DATABASE_URL uses a Unix socket connection string
    (?host=/cloudsql/<instance>). Without --add-cloudsql-instances the
    proxy sidecar is not started and pg connect fails with ENOENT, causing
    every POST /login to 503.

    This is the invariant regression test for the mount — the secret
    binding alone (tested above) is necessary but not sufficient.
    """
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    # Isolate the Web deploy block so we don't accidentally match the API block.
    marker = "Deploying immutable Web candidate"
    assert marker in text, "Web deploy block marker not found"
    web_block = text[text.index(marker) :]
    # Trim to the next gcloud invocation (the describe that follows).
    next_gcloud = web_block.index("gcloud run services describe")
    web_deploy_cmd = web_block[:next_gcloud]

    assert "--add-cloudsql-instances" in web_deploy_cmd, (
        "Web gcloud run deploy is missing --add-cloudsql-instances; "
        "the database URL secret will have no socket to connect to."
    )
    assert "GCP_CLOUD_SQL_INSTANCE" in web_deploy_cmd


CLOUD_RUN_TF = ROOT / "infra/terraform/cloud_run.tf"
IAM_TF = ROOT / "infra/terraform/iam.tf"


def test_terraform_web_service_has_cloudsql_volume_and_mount() -> None:
    """The Terraform web service resource must declare CloudSQL volume and mount.

    Without the volume the Cloud Run revision has no /cloudsql mount point,
    so the Unix socket path in ODAY_DATABASE_URL resolves to ENOENT.
    """
    source = CLOUD_RUN_TF.read_text(encoding="utf-8")

    # Find the web service resource block.
    assert 'resource "google_cloud_run_v2_service" "web"' in source

    web_block_start = source.index('resource "google_cloud_run_v2_service" "web"')
    # Find end of the resource block — the next top-level resource.
    rest = source[web_block_start:]
    # Look for the next resource declaration after this one.
    next_resource = rest.find('\nresource "', 1)
    web_block = rest[:next_resource] if next_resource > 0 else rest

    assert "cloud_sql_instance" in web_block, (
        "Web service Terraform is missing CloudSQL volume declaration."
    )
    assert 'mount_path = "/cloudsql"' in web_block, (
        "Web service Terraform is missing /cloudsql volume mount."
    )
    assert "ODAY_DATABASE_URL" in web_block, (
        "Web service Terraform is missing ODAY_DATABASE_URL env binding."
    )


def test_terraform_web_sa_has_cloudsql_client_and_database_url_accessor() -> None:
    """The web service account must have CloudSQL client and database_url accessor.

    Without cloudsql.client the Cloud SQL Auth Proxy cannot connect to the
    instance. Without the secretAccessor binding the revision cannot read
    the ODAY_DATABASE_URL secret value.
    """
    source = IAM_TF.read_text(encoding="utf-8")

    assert 'resource "google_project_iam_member" "web_cloud_sql_client"' in source, (
        "iam.tf is missing web_cloud_sql_client IAM binding."
    )
    assert 'resource "google_secret_manager_secret_iam_member" "web_database_url"' in source, (
        "iam.tf is missing web_database_url secret accessor binding."
    )

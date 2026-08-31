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

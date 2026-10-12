"""Production Web session migrations/handlers against throwaway actual PG."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from shared.identity.dev_smoke_recovery import PRESERVED_ACCOUNT_ID
from tests.integration.test_dev_smoke_recovery import _q
from tests.integration.test_dev_smoke_recovery import identity_stack as identity_stack
from tests.integration.test_dev_smoke_recovery import recovery as recovery


@pytest.mark.parametrize("selection", ["concurrent pools/restart", "real deferred commit failure", "lost postcommit validation", "bounded absolute lifetime"])
def test_production_web_legacy_session_commit_and_revocation(recovery: Any, selection: str) -> None:
    s = recovery
    root = Path(__file__).resolve().parents[2]
    binary = root / "node_modules/.bin/vitest"
    assert binary.is_file(), "actual PG Web proof requires the existing frozen-lock Node test environment"
    protected = [
        _q(s, "SELECT row_to_json(t)::text FROM identity.sessions t WHERE account_id = %s ORDER BY session_id", (PRESERVED_ACCOUNT_ID,)),
        _q(s, "SELECT row_to_json(t)::text FROM identity.password_credentials t WHERE account_id = %s", (PRESERVED_ACCOUNT_ID,)),
    ]
    env = {k: v for k, v in os.environ.items() if not any(word in k.upper()
        for word in ("TOKEN", "PASSWORD", "SECRET", "COOKIE", "CREDENTIAL", "DATABASE_URL"))}
    env.update(NODE_ENV="test", ODP_TEST_SESSION_BOUNDARY_DATABASE_URL=s.db.url())
    result = subprocess.run([str(binary), "run", "src/lib/auth/__tests__/legacySessionMigration.test.ts", "-t", selection],
                            cwd=root / "apps/web", env=env, timeout=60, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _q(s, "SELECT row_to_json(t)::text FROM identity.sessions t WHERE account_id = %s ORDER BY session_id", (PRESERVED_ACCOUNT_ID,)) == protected[0]
    assert _q(s, "SELECT row_to_json(t)::text FROM identity.password_credentials t WHERE account_id = %s", (PRESERVED_ACCOUNT_ID,)) == protected[1]

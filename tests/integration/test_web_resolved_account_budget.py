"""Real PG Web handler/account-isolated durable password budgets, no live credentials."""

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


@pytest.mark.parametrize("selection", ["password-change", "login"])
def test_production_pg_same_username_does_not_reset_other_tenant_budget(
    recovery: Any, selection: str
) -> None:
    s = recovery
    root = Path(__file__).resolve().parents[2]
    binary = root / "node_modules/.bin/vitest"
    assert binary.is_file(), (
        "actual PG Web proof requires the existing frozen-lock Node test environment"
    )
    snapshots = [
        _q(
            s,
            "SELECT row_to_json(t)::text FROM identity.sessions t WHERE account_id=%s ORDER BY session_id",
            (PRESERVED_ACCOUNT_ID,),
        ),
        _q(
            s,
            "SELECT row_to_json(t)::text FROM identity.password_credentials t WHERE account_id=%s",
            (PRESERVED_ACCOUNT_ID,),
        ),
    ]
    env = {
        k: v
        for k, v in os.environ.items()
        if not any(
            word in k.upper()
            for word in ("TOKEN", "PASSWORD", "SECRET", "COOKIE", "CREDENTIAL", "DATABASE_URL")
        )
    }
    env.update(NODE_ENV="test", ODP_TEST_SESSION_BOUNDARY_DATABASE_URL=s.db.url())
    child = subprocess.run(
        [
            str(binary),
            "run",
            "src/lib/auth/__tests__/resolvedAccountBudgetPg.test.ts",
            "-t",
            selection,
        ],
        cwd=root / "apps/web",
        env=env,
        timeout=60,
        capture_output=True,
        text=True,
        check=False,
    )
    assert child.returncode == 0, child.stdout + child.stderr
    assert (
        _q(
            s,
            "SELECT row_to_json(t)::text FROM identity.sessions t WHERE account_id=%s ORDER BY session_id",
            (PRESERVED_ACCOUNT_ID,),
        )
        == snapshots[0]
    )
    assert (
        _q(
            s,
            "SELECT row_to_json(t)::text FROM identity.password_credentials t WHERE account_id=%s",
            (PRESERVED_ACCOUNT_ID,),
        )
        == snapshots[1]
    )

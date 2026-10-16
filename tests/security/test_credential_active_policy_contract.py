"""Active crypto floor versus explicit offline version-0 migration compatibility."""
import ast
from pathlib import Path

import pytest
from argon2 import extract_parameters
from argon2.exceptions import HashingError

from shared.identity.credential_service import CURRENT_POLICY, Argon2Policy, CredentialService


@pytest.mark.parametrize("version", [1, 2, 999])
@pytest.mark.parametrize("change", [
    {"memory_cost": 32768}, {"time_cost": 2}, {"parallelism": 2},
])
def test_every_active_version_enforces_floor(version, change):
    with pytest.raises(ValueError, match="cryptographic floor"):
        Argon2Policy(version=version, **change)


@pytest.mark.parametrize("change", [
    {"version": -1}, {"version": True}, {"version": "1"}, {"version": None},
    {"memory_cost": "65536"}, {"time_cost": 3.0}, {"parallelism": True},
    {"hash_len": 0}, {"salt_len": 8}, {"memory_cost": 0}, {"time_cost": 0},
])
def test_malformed_policy_cannot_initialize(change):
    with pytest.raises((ValueError, HashingError)):
        Argon2Policy(**change)


def test_explicit_legacy_hash_really_verifies_and_upgrades_to_active_policy():
    legacy = CredentialService(Argon2Policy(version=0, memory_cost=32768, time_cost=2))
    old_hash = legacy.hash_password("test-only-legacy-password")
    assert extract_parameters(old_hash).memory_cost == 32768
    active = CredentialService()
    ok, upgraded = active.verify_and_rehash(old_hash, "test-only-legacy-password")
    assert ok is True and upgraded is not None
    params = extract_parameters(upgraded)
    assert (params.memory_cost, params.time_cost, params.parallelism) == (65536, 3, 1)
    assert active.needs_rehash(upgraded) is False
    assert active.verify_password(upgraded, "test-only-legacy-password") is True


def test_default_production_composition_uses_current_policy_not_legacy_input():
    root = Path(__file__).resolve().parents[2]
    # Actual production callsites found by the recorded repository-wide static
    # audit. These owning services construct defaults, not request/config policy.
    for path in ("shared/identity/bootstrap.py", "shared/identity/dev_smoke_recovery.py",
                 "shared/identity/invitation_service.py"):
        tree = ast.parse((root / path).read_text())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "CredentialService"]
        assert calls
        assert all(not call.args and not call.keywords for call in calls)
    service = CredentialService()
    assert service.policy is CURRENT_POLICY
    assert service.policy.version == 1
    assert (service.policy.memory_cost, service.policy.time_cost, service.policy.parallelism) == (65536, 3, 1)
    service.dummy_verify()

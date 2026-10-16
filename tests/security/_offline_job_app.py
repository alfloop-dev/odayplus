"""Reuse costly offline app construction, NOT authorization decisions or records."""

import os

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from apps.api.oday_api.main import create_app
from apps.api.oday_api.security import dependencies as deps
from shared.audit import InMemoryAuditLog
from shared.infrastructure.persistence import build_persistence
from tests.security.test_auth_composition_extension import MODE_KEYS


@pytest.fixture(scope="module")
def offline_job_app():
    # Same established offline environment as each test's isolation fixture.
    # Module initialization precedes function fixtures, so bind it explicitly.
    with pytest.MonkeyPatch.context() as patch:
        for name in tuple(os.environ):
            if name in MODE_KEYS or name.startswith("ODP_AUTH_"):
                patch.delenv(name, raising=False)
        patch.setenv("NODE_ENV", "test")
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        bundle = build_persistence(mode="memory")
        audit = InMemoryAuditLog()
        app = create_app(
            persistence=bundle, audit_log=audit, external_provider_validation=lambda: None
        )
    return key, bundle, app, audit


@pytest.fixture
def job_app(offline_job_app):
    key, bundle, app, audit = offline_job_app
    # Each test still owns fresh records, idempotency state and a newly built
    # verified principal boundary. No worker/lifespan or sessions are reused.
    with bundle.job_queue._reservation_lock:
        bundle.job_queue._jobs.clear()
        bundle.job_queue._idempotency_index.clear()
    deps.bind_persistence(bundle)
    deps.bind_audit_log(audit)
    return key, bundle, app

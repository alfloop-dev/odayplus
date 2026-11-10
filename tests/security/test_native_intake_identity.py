"""Synthetic URL identities; these tests grant no source or live authority."""

import hashlib
import json
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from apps.worker.oday_worker.main import ODayWorker
from modules.listing.application import native_intake_url_identity as rules
from modules.listing.application.native_intake_identity import (
    NATIVE_INTAKE_IDENTITY_JOB_TYPE,
    POLICY_ENV,
    SIGNING_KEY_ENV,
    NativeIntakeIdentityAuthenticator,
    NativeIntakeIdentityOwner,
    NativeIntakeIdentityUnavailable,
    native_intake_identity_policy,
)
from modules.listing.application.native_intake_start import NativeIntakeStartAuthenticator
from shared.infrastructure.persistence.factory import build_persistence
from shared.jobs.queue import JobFenceRejectedError, JobRecord, JobRequest, JobStatus
from shared.jobs.registry import JobRegistry
from tests.security.test_native_intake_start import policy


@pytest.mark.parametrize(("original", "expected"), [
    ("HTTPS://SYNTHETIC.EXAMPLE:443/Case?A=1#Section", "https://synthetic.example/Case?A=1#Section"),
    ("http://Synthetic.Example:80", "http://synthetic.example"),
    ("https://synthetic.example:8443/", "https://synthetic.example:8443/"),
    ("https://synthetic.example/Path?", "https://synthetic.example/Path?"),
    ("https://synthetic.example/Path#", "https://synthetic.example/Path#"),
    ("https://synthetic.example/Path?#", "https://synthetic.example/Path?#"),
    ("https://xn--r8jz45g.jp/item?a=%2f&b=2#view?public=ok", "https://xn--r8jz45g.jp/item?a=%2f&b=2#view?public=ok"),
])
def test_compiled_rule_preserves_evidence_and_binds_actual_semantics(original, expected):
    identity = rules.normalize_native_intake_url(original)
    assert identity.canonical_url == expected
    assert identity.canonical_url_sha256 == hashlib.sha256(expected.encode()).hexdigest()
    assert identity.rule_version == "native-url-exact-v2"
    assert identity.rule_source_sha256 == hashlib.sha256(Path(rules.__file__).read_bytes()).hexdigest()
    assert identity.source_host == expected.split("//", 1)[1].split("/", 1)[0].split(":", 1)[0]


@pytest.mark.parametrize("tail", ["?tracking=one", "?tracking=two", "#one", "#two", "?a=1&b=2", "?b=2&a=1"])
def test_unknown_query_fragment_and_order_are_not_lossy_aliases(tail):
    original = "https://synthetic.example/Case" + tail
    actual = rules.normalize_native_intake_url(original)
    assert actual.canonical_url == original
    assert actual.canonical_url_sha256 != rules.normalize_native_intake_url("https://synthetic.example/Case").canonical_url_sha256


@pytest.mark.parametrize("url", [
    "https://synthetic.example./item", "https://例え.jp/item", "https://8.8.8.8/item",
    "https://[2606:4700:4700::1111]/item", "https://synthetic.example:/item",
    "https://synthetic.example:0443/item", "https://synthetic.example:0/item",
    "https://synthetic.example:65536/item", "https://synthetic.example:+443/item",
    "https://%73ynthetic.example/item", "https://-synthetic.example/item",
    "https://synthetic..example/item", "https://synthetic.example\\other/item",
    "\x00https://synthetic.example/item", "https://user:synthetic@synthetic.example/item",
    "https://synthetic.example/item?access_token=synthetic",
    "https://synthetic.example/item#access_token=synthetic?public=control",
    "https://synthetic.example/item#/view?access_token=synthetic",
])
def test_unsupported_or_credential_urls_cannot_become_identity_proofs(url):
    with pytest.raises(ValueError):
        rules.normalize_native_intake_url(url)


@pytest.mark.parametrize("patch", [{"max_attempts": 4, "backoff_seconds": [1, 2, 3]},
                                  {"max_attempts": True}, {"deadline_seconds": 31},
                                  {"deadline_seconds": True}, {"version": True},
                                  {"tenant_ids": []}, {"extra_grant": "identity.resolve"}])
def test_identity_policy_has_no_default_or_unbounded_lease_grant(monkeypatch, patch):
    monkeypatch.setenv(POLICY_ENV, json.dumps({**policy(str(uuid4())), **patch}))
    with pytest.raises(NativeIntakeIdentityUnavailable):
        native_intake_identity_policy()


def test_start_key_and_missing_policy_do_not_create_identity_authority(monkeypatch):
    from modules.listing.application.native_intake_start import SIGNING_KEY_ENV as START_KEY

    monkeypatch.delenv(POLICY_ENV, raising=False)
    monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)
    monkeypatch.setenv(START_KEY, "synthetic-start-key-only-" * 3)
    with pytest.raises(NativeIntakeIdentityUnavailable):
        native_intake_identity_policy()
    with pytest.raises(NativeIntakeIdentityUnavailable):
        NativeIntakeIdentityAuthenticator.sign(str(uuid4()), str(uuid4()), 1)


@pytest.mark.parametrize("change", ["tenant", "job", "purpose", "type", "generation", "extra"])
def test_identity_capability_is_purpose_bound_without_a_database(monkeypatch, change):
    from modules.listing.application.native_intake_start import SIGNING_KEY_ENV as START_KEY

    key, tenant, job_id = "synthetic-independent-identity-key-" * 2, str(uuid4()), str(uuid4())
    monkeypatch.setenv(SIGNING_KEY_ENV, key)
    # Even identical key bytes do not make another job purpose authoritative.
    monkeypatch.setenv(START_KEY, key)
    auth = NativeIntakeIdentityAuthenticator()
    token = auth.sign(job_id, tenant, 1)
    delivery = JobRecord(NATIVE_INTAKE_IDENTITY_JOB_TYPE,
        {"job_id": job_id, "tenant_id": tenant, "attempt_token": token}, "synthetic-correlation", job_id=job_id)
    assert auth.decode(delivery) == (tenant, 1)
    payload = dict(delivery.payload)
    if change == "tenant":
        payload["tenant_id"] = str(uuid4())
    elif change == "job":
        payload["job_id"] = str(uuid4())
    elif change == "purpose":
        payload["attempt_token"] = NativeIntakeStartAuthenticator.sign(job_id, tenant, 1)
    elif change == "type":
        delivery = replace(delivery, job_type="identity-execution")
    elif change == "generation":
        payload["attempt_token"] = "01." + token.split(".", 1)[1]
    else:
        payload["actor_role"] = "SVC_INTAKE"
    with pytest.raises(JobFenceRejectedError):
        auth.decode(replace(delivery, payload=payload))


@pytest.mark.parametrize("raises", [False, True])
def test_default_worker_never_settles_an_unresolved_native_identity_business_attempt(monkeypatch, raises):
    bundle, registry, renewed = build_persistence(mode="memory"), JobRegistry(), threading.Event()
    original = bundle.job_queue.heartbeat

    def generic_heartbeat(*args, **kwargs):
        version = original(*args, **kwargs)
        renewed.set()
        return version

    monkeypatch.setattr(bundle.job_queue, "heartbeat", generic_heartbeat)

    def unresolved(_job, _bundle):
        assert renewed.wait(3), "The native path must use a physical heartbeat"
        if raises:
            raise ValueError("Synthetic unresolved native business checkpoint")

    registry.register(NATIVE_INTAKE_IDENTITY_JOB_TYPE, unresolved)
    job, _ = bundle.job_queue.enqueue(JobRequest(NATIVE_INTAKE_IDENTITY_JOB_TYPE, {}),
                                     correlation_id="synthetic-worker-routing")
    worker = ODayWorker(persistence=bundle, registry=registry, heartbeat_interval_seconds=0.01)
    assert worker.run_once() is True
    current = bundle.job_queue.get(job.job_id)
    assert current.status == JobStatus.RUNNING and current.payload == {} and current.delivery_state is None


@pytest.mark.parametrize("kind", ["invalid_signature", "unconfigured_tenant"])
def test_capability_and_service_partition_precede_tenant_binding_or_sql(monkeypatch, kind):
    tenant, foreign, job_id = str(uuid4()), str(uuid4()), str(uuid4())
    monkeypatch.setenv(POLICY_ENV, json.dumps(policy(tenant)))
    monkeypatch.setenv(SIGNING_KEY_ENV, "synthetic-native-identity-capability-" * 2)
    touched = []

    def forbidden(*_args, **_kwargs):
        touched.append(True)
        raise AssertionError("No tenant binding or SQL is allowed for this capability")

    graph = SimpleNamespace(engine=SimpleNamespace(dialect="postgresql", query_one=forbidden),
                            queue=SimpleNamespace(), transaction=forbidden)
    owner = NativeIntakeIdentityOwner(graph)
    token = owner.auth.sign(job_id, foreign, 1)
    if kind == "invalid_signature":
        token += "0"
    delivery = JobRecord(NATIVE_INTAKE_IDENTITY_JOB_TYPE,
        {"job_id": job_id, "tenant_id": foreign, "attempt_token": token}, "synthetic-pre-SQL", job_id=job_id)
    with pytest.raises(JobFenceRejectedError if kind == "invalid_signature" else PermissionError):
        owner.execute(delivery)
    assert touched == []

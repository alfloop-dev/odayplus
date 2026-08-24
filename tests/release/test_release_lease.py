"""Negative-first tests for the signed, durable Supervisor release lease.

Every test here asks the same question: can something that is not a genuine,
current, unused Supervisor authorisation get admitted? The answer has to be no
for a forged signature, a lease signed by the wrong key, an expired or
not-yet-valid window, a lease bound to a different SHA / manifest / environment
/ action / task, a lease the Supervisor never recorded, one already consumed,
and one revoked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key

from delivery_toolchain.release.release_lease import (
    MAX_TTL_SECONDS,
    NOT_BEFORE_SKEW_SECONDS,
    STATE_CONSUMED,
    STATE_ISSUED,
    STATE_REVOKED,
    LeaseIssuanceError,
    LeaseKeyError,
    LeaseStateError,
    LeaseStateStore,
    admit_and_consume,
    build_lease,
    canonical_payload,
    generate_keypair,
    load_lease,
    load_private_key,
    load_public_key,
    public_key_id,
    sign_lease,
    verify_lease,
)

CANDIDATE_SHA = "a" * 40
MANIFEST_DIGEST = "sha256:" + "b" * 64
RELEASE_ID = "odp-20260824-001"
TASK_ID = "ODP-RELEASE-ADMISSION-AUTHORITY-001"
NOW = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def keys() -> tuple:
    private_pem, public_pem = generate_keypair()
    return load_pem_private_key(private_pem, password=None), load_pem_public_key(public_pem)


@pytest.fixture
def store(tmp_path: Path) -> LeaseStateStore:
    return LeaseStateStore(tmp_path / "release-leases")


def make_lease(private_key, *, issued_at: datetime = NOW, **overrides) -> dict:
    kwargs = {
        "task_id": TASK_ID,
        "release_id": RELEASE_ID,
        "candidate_sha": CANDIDATE_SHA,
        "manifest_digest": MANIFEST_DIGEST,
        "target_environment": "dev",
        "allowed_action": "deploy",
        "ttl_seconds": 3600,
    }
    kwargs.update(overrides)
    return build_lease(private_key=private_key, issued_at=issued_at, **kwargs)


def issued(private_key, store: LeaseStateStore, **overrides) -> dict:
    lease = make_lease(private_key, **overrides)
    store.record_issued(lease)
    return lease


def expectations(**overrides) -> dict:
    base = {
        "expected_task_id": TASK_ID,
        "expected_candidate_sha": CANDIDATE_SHA,
        "expected_manifest_digest": MANIFEST_DIGEST,
        "expected_environment": "dev",
        "expected_action": "deploy",
        "now": NOW,
    }
    base.update(overrides)
    return base


def test_a_genuine_unused_lease_verifies(keys, store) -> None:
    private_key, public_key = keys
    lease = issued(private_key, store)
    assert verify_lease(lease, public_key=public_key, state_store=store, **expectations()) == []


def test_lease_binds_every_required_field(keys, store) -> None:
    private_key, _ = keys
    lease = issued(private_key, store)
    for field in (
        "lease_id",
        "task_id",
        "release_id",
        "candidate_sha",
        "manifest_digest",
        "target_environment",
        "allowed_action",
        "issued_at",
        "expires_at",
        "nonce",
    ):
        assert field in canonical_payload(lease), f"{field} is not covered by the signature"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_id", "SOME-OTHER-TASK-001"),
        ("candidate_sha", "c" * 40),
        ("manifest_digest", "sha256:" + "d" * 64),
        ("target_environment", "staging"),
        ("allowed_action", "destroy"),
        ("release_id", "odp-20260824-999"),
        ("nonce", "e" * 32),
        ("lease_id", "lease-" + "f" * 32),
    ],
)
def test_tampering_with_any_signed_field_fails_verification(keys, store, field, value) -> None:
    private_key, public_key = keys
    lease = issued(private_key, store)
    lease[field] = value
    errors = verify_lease(lease, public_key=public_key, state_store=store, **expectations())
    assert any("signature does not verify" in error for error in errors)


def test_a_lease_signed_by_another_key_is_rejected(keys, store) -> None:
    _, public_key = keys
    other_private_pem, _ = generate_keypair()
    other_private = load_pem_private_key(other_private_pem, password=None)
    lease = issued(other_private, store)
    errors = verify_lease(lease, public_key=public_key, state_store=store, **expectations())
    assert any("was not issued by the configured verification key" in e for e in errors)


def test_a_re_signed_lease_still_fails_the_durable_binding(keys, store) -> None:
    """Holding the private key is the whole authority; not holding it is fatal.

    Even the Supervisor cannot swap a field after the fact: the state store
    compares the presented lease against the bytes it recorded at issuance.
    """

    private_key, public_key = keys
    lease = issued(private_key, store)
    lease["candidate_sha"] = "c" * 40
    lease["signature"] = sign_lease(lease, private_key=private_key)

    errors = verify_lease(
        lease, public_key=public_key, state_store=store, **expectations(expected_candidate_sha=None)
    )
    assert any("does not match the lease the Supervisor recorded" in e for e in errors)


def test_extra_unsigned_fields_are_not_part_of_the_attested_payload(keys, store) -> None:
    private_key, _ = keys
    lease = issued(private_key, store)
    lease["admission_override"] = True
    assert "admission_override" not in canonical_payload(lease)


@pytest.mark.parametrize(
    ("expectation", "fragment"),
    [
        ({"expected_task_id": "OTHER-TASK-001"}, "lease.task_id"),
        ({"expected_candidate_sha": "c" * 40}, "lease.candidate_sha"),
        ({"expected_manifest_digest": "sha256:" + "d" * 64}, "lease.manifest_digest"),
        ({"expected_environment": "staging"}, "lease.target_environment"),
        ({"expected_action": "destroy"}, "lease.allowed_action"),
    ],
)
def test_a_lease_for_a_different_request_is_rejected(keys, store, expectation, fragment) -> None:
    private_key, public_key = keys
    lease = issued(private_key, store)
    errors = verify_lease(
        lease, public_key=public_key, state_store=store, **expectations(**expectation)
    )
    assert any(error.startswith(fragment) for error in errors)


def test_an_expired_lease_is_rejected(keys, store) -> None:
    private_key, public_key = keys
    lease = issued(private_key, store, ttl_seconds=60)
    errors = verify_lease(
        lease,
        public_key=public_key,
        state_store=store,
        **expectations(now=NOW + timedelta(seconds=61)),
    )
    assert any("lease expired at" in error for error in errors)


def test_a_lease_presented_before_it_is_valid_is_rejected(keys, store) -> None:
    private_key, public_key = keys
    lease = issued(private_key, store)
    errors = verify_lease(
        lease,
        public_key=public_key,
        state_store=store,
        **expectations(now=NOW - timedelta(seconds=NOT_BEFORE_SKEW_SECONDS + 1)),
    )
    assert any("is not valid until" in error for error in errors)


def test_clock_skew_within_tolerance_does_not_reject_a_fresh_lease(keys, store) -> None:
    """A runner a few seconds behind the Supervisor is drift, not an attack."""

    private_key, public_key = keys
    lease = issued(private_key, store)
    assert verify_lease(
        lease,
        public_key=public_key,
        state_store=store,
        **expectations(now=NOW - timedelta(seconds=NOT_BEFORE_SKEW_SECONDS - 1)),
    ) == []


def test_skew_tolerance_does_not_extend_expiry(keys, store) -> None:
    private_key, public_key = keys
    lease = issued(private_key, store, ttl_seconds=60)
    errors = verify_lease(
        lease,
        public_key=public_key,
        state_store=store,
        **expectations(now=NOW + timedelta(seconds=61)),
    )
    assert any("lease expired at" in error for error in errors)


def test_ttl_beyond_the_maximum_is_refused_at_issuance(keys) -> None:
    private_key, _ = keys
    with pytest.raises(LeaseIssuanceError) as excinfo:
        make_lease(private_key, ttl_seconds=MAX_TTL_SECONDS + 1)
    assert any("exceeds the maximum" in error for error in excinfo.value.errors)


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"candidate_sha": "not-a-sha"}, "candidate_sha"),
        ({"manifest_digest": "deadbeef"}, "manifest_digest"),
        ({"target_environment": "wherever"}, "target_environment"),
        ({"allowed_action": "NOT AN ACTION"}, "allowed_action"),
        ({"release_id": "x"}, "release_id"),
        ({"ttl_seconds": 0}, "ttl_seconds"),
    ],
)
def test_malformed_issuance_input_is_refused(keys, overrides, fragment) -> None:
    private_key, _ = keys
    with pytest.raises(LeaseIssuanceError) as excinfo:
        make_lease(private_key, **overrides)
    assert any(fragment in error for error in excinfo.value.errors)


def test_a_lease_the_supervisor_never_recorded_is_rejected(keys, store) -> None:
    private_key, public_key = keys
    lease = make_lease(private_key)  # correctly signed, never recorded
    errors = verify_lease(lease, public_key=public_key, state_store=store, **expectations())
    assert any("has no record in the durable Supervisor state store" in e for e in errors)


def test_a_missing_field_is_reported_without_pretending_to_verify(keys, store) -> None:
    private_key, public_key = keys
    lease = issued(private_key, store)
    del lease["expires_at"]
    errors = verify_lease(lease, public_key=public_key, state_store=store, **expectations())
    assert errors == ["lease missing required field: expires_at"]


def test_a_lease_is_consumed_exactly_once(keys, store) -> None:
    private_key, public_key = keys
    lease = issued(private_key, store)

    admitted, errors, receipt = admit_and_consume(
        lease, public_key=public_key, state_store=store, consumed_by="run-1", **expectations()
    )
    assert admitted is True
    assert errors == []
    assert receipt["admitted"] is True
    assert store.get(lease["lease_id"])["state"] == STATE_CONSUMED

    replayed, replay_errors, replay_receipt = admit_and_consume(
        lease, public_key=public_key, state_store=store, consumed_by="run-2", **expectations()
    )
    assert replayed is False
    assert replay_receipt["admitted"] is False
    assert any("already used, or revoked" in error for error in replay_errors)


def test_a_revoked_lease_can_never_be_consumed(keys, store) -> None:
    private_key, public_key = keys
    lease = issued(private_key, store)
    store.revoke(lease, reason="candidate withdrawn")
    assert store.get(lease["lease_id"])["state"] == STATE_REVOKED

    admitted, errors, _ = admit_and_consume(
        lease, public_key=public_key, state_store=store, consumed_by="run-1", **expectations()
    )
    assert admitted is False
    assert any(STATE_REVOKED in error for error in errors)


def test_a_revoked_lease_cannot_be_revoked_or_consumed_again(keys, store) -> None:
    private_key, _ = keys
    lease = issued(private_key, store)
    store.revoke(lease, reason="first")
    with pytest.raises(LeaseStateError):
        store.revoke(lease, reason="second")
    with pytest.raises(LeaseStateError):
        store.consume(lease, consumed_by="run-1")


def test_the_same_lease_id_cannot_be_recorded_twice(keys, store) -> None:
    private_key, _ = keys
    lease = issued(private_key, store)
    with pytest.raises(LeaseStateError) as excinfo:
        store.record_issued(lease)
    assert "already exists in the durable state store" in str(excinfo.value)


def test_the_store_refuses_to_consume_a_lease_it_did_not_record(keys, store, tmp_path) -> None:
    private_key, _ = keys
    lease = issued(private_key, store)
    other = LeaseStateStore(tmp_path / "other-store")
    with pytest.raises(LeaseStateError) as excinfo:
        other.consume(lease, consumed_by="run-1")
    assert "not present in the durable state store" in str(excinfo.value)


def test_admission_refuses_a_state_directory_that_does_not_exist(tmp_path: Path) -> None:
    with pytest.raises(LeaseStateError) as excinfo:
        LeaseStateStore(tmp_path / "absent", require_existing=True)
    assert "refusing to create a throwaway store" in str(excinfo.value)


def test_a_blocked_release_does_not_burn_the_lease(keys, store) -> None:
    """Gate failures must not consume a lease that could never have admitted."""

    private_key, public_key = keys
    lease = issued(private_key, store)
    admitted, errors, receipt = admit_and_consume(
        lease,
        public_key=public_key,
        state_store=store,
        consumed_by="run-1",
        extra_errors=["registry decision is 'no-go', expected 'go'"],
        **expectations(),
    )
    assert admitted is False
    assert "registry decision is 'no-go', expected 'go'" in errors
    assert receipt["consumed_at"] is None
    assert store.get(lease["lease_id"])["state"] == STATE_ISSUED


def test_the_receipt_never_carries_the_bearer_parts_of_the_lease(keys, store) -> None:
    private_key, public_key = keys
    lease = issued(private_key, store)
    _, _, receipt = admit_and_consume(
        lease, public_key=public_key, state_store=store, consumed_by="run-1", **expectations()
    )

    serialised = str(receipt)
    assert lease["nonce"] not in serialised
    assert lease["signature"]["value"] not in serialised
    assert receipt["nonce_digest"].startswith("sha256:")
    assert receipt["signature_digest"].startswith("sha256:")
    assert receipt["signature_key_id"] == public_key_id(public_key)
    assert receipt["consumed_at"] is not None


def test_a_failed_receipt_names_every_reason(keys, store) -> None:
    private_key, public_key = keys
    lease = issued(private_key, store, ttl_seconds=60)
    _, _, receipt = admit_and_consume(
        lease,
        public_key=public_key,
        state_store=store,
        consumed_by="run-1",
        **expectations(now=NOW + timedelta(seconds=61), expected_environment="staging"),
    )
    assert receipt["admitted"] is False
    assert any("lease expired at" in error for error in receipt["errors"])
    assert any("lease.target_environment" in error for error in receipt["errors"])


def test_keys_load_from_pem_files_and_the_public_key_cannot_sign(tmp_path: Path) -> None:
    private_pem, public_pem = generate_keypair()
    private_path = tmp_path / "lease.key"
    public_path = tmp_path / "lease.pub"
    private_path.write_bytes(private_pem)
    public_path.write_bytes(public_pem)

    private_key = load_private_key(key_path=private_path)
    public_key = load_public_key(key_path=public_path)
    assert public_key_id(private_key.public_key()) == public_key_id(public_key)
    assert not hasattr(public_key, "sign")


def test_a_public_key_is_not_accepted_as_a_signing_key(tmp_path: Path) -> None:
    _, public_pem = generate_keypair()
    public_path = tmp_path / "lease.pub"
    public_path.write_bytes(public_pem)
    with pytest.raises(LeaseKeyError):
        load_private_key(key_path=public_path)


def test_missing_key_material_fails_closed(monkeypatch) -> None:
    monkeypatch.delenv("ODP_RELEASE_LEASE_PUBLIC_KEY", raising=False)
    with pytest.raises(LeaseKeyError) as excinfo:
        load_public_key()
    assert "ODP_RELEASE_LEASE_PUBLIC_KEY" in str(excinfo.value)


def test_an_unreadable_lease_document_is_reported_not_guessed(tmp_path: Path) -> None:
    missing = tmp_path / "absent.json"
    assert load_lease(missing) == (None, [f"lease file does not exist: {missing}"])

    malformed = tmp_path / "lease.json"
    malformed.write_text("{not json", encoding="utf-8")
    lease, errors = load_lease(malformed)
    assert lease is None
    assert any("cannot be read as JSON" in error for error in errors)

    wrong_type = tmp_path / "list.json"
    wrong_type.write_text("[]", encoding="utf-8")
    assert load_lease(wrong_type) == (None, ["lease document must be a JSON object"])


def test_a_non_object_lease_is_rejected(keys, store) -> None:
    _, public_key = keys
    assert verify_lease(
        "lease-0001", public_key=public_key, state_store=store, **expectations()
    ) == ["lease must be a JSON object"]

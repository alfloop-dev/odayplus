"""Synthetic signed leases/local durable CAS; intercepted clocks, no live admission."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from delivery_toolchain.release import check_runtime_admission as admission
from delivery_toolchain.release import release_lease as leases
from delivery_toolchain.release.release_manifest import compute_manifest_digest
from tests.release.test_runtime_admission import SHA, TASK_ID, build_manifest, build_registry


def inputs(tmp_path: Any, *, environment: str = "dev") -> tuple[Any, Any, Any, Any, Any]:
    private, _ = leases.generate_keypair()
    key = load_pem_private_key(private, password=None)
    manifest = build_manifest()
    stage = {"dev": "candidate-built", "staging": "dev-verified", "production": "staging-verified"}[
        environment
    ]
    registry = build_registry(
        stage=stage,
        environment={"dev": "dev", "staging": "dev", "production": "staging"}[environment],
        admission_target=environment,
    )
    registry["release"]["manifest_digest"] = manifest["manifest_digest"]
    store = leases.LeaseStateStore(tmp_path / "state")
    lease = leases.build_lease(
        task_id=TASK_ID,
        release_id=manifest["release_id"],
        candidate_sha=SHA,
        manifest_digest=manifest["manifest_digest"],
        target_environment=environment,
        private_key=key,
    )
    store.record_issued(lease)
    return key, manifest, registry, store, lease


def decide(values: Any) -> tuple[Any, Any, Any]:
    key, manifest, registry, store, lease = values
    return admission.admit_release(
        registry,
        lease,
        release_sha=SHA,
        environment=lease["target_environment"],
        task_id=TASK_ID,
        public_key=key.public_key(),
        state_store=store,
        manifest=manifest,
        manifest_digest=manifest["manifest_digest"],
        consumed_by="offline-test",
    )


@pytest.mark.parametrize("contradiction", ["failed-receipt", "open-blocker"])
def test_contradictory_target_gate_refuses_without_consuming_issued_lease(
    tmp_path: Any, contradiction: str
) -> None:
    values = inputs(tmp_path)
    gate = values[2]["gates"][0]
    if contradiction == "failed-receipt":
        gate["receipts"].append(
            {**gate["receipts"][0], "receipt_id": "later-failure", "result": "fail"}
        )
    else:
        gate["blockers"] = ["new observed failure not cleared"]
    admitted, errors, _ = decide(values)
    assert not admitted and errors
    assert values[3].get(values[4]["lease_id"])["state"] == "issued"


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_initial_dev_recovery_in_full_manifest_refuses_other_target_before_consume(
    tmp_path: Any, environment: str
) -> None:
    values = inputs(tmp_path, environment=environment)
    key, manifest, registry, store, old = values
    # Existing full-profile admission intentionally accepts complete bootstrap
    # recovery only for dev; use actual contract's valid fixture.
    from tests.release.test_release_manifest import first_release_manifest

    manifest = first_release_manifest()
    manifest["candidate_sha"] = SHA
    manifest["manifest_digest"] = compute_manifest_digest(manifest)
    registry["release"]["manifest_digest"] = manifest["manifest_digest"]
    lease = leases.build_lease(
        task_id=TASK_ID,
        release_id=manifest["release_id"],
        candidate_sha=SHA,
        manifest_digest=manifest["manifest_digest"],
        target_environment=environment,
        private_key=key,
    )
    store.record_issued(lease)
    admitted, errors, _ = decide((key, manifest, registry, store, lease))
    assert not admitted and any("initial" in error or "dev" in error for error in errors)
    assert store.get(lease["lease_id"])["state"] == "issued"


@pytest.mark.parametrize("edge", ["lookup", "transition", "completion", "exact-expiry"])
def test_expiry_during_verification_or_consume_never_admits_or_reopens(
    tmp_path: Any, monkeypatch: Any, edge: str
) -> None:
    start = datetime.now(UTC).replace(microsecond=0)
    ticks = [start]
    monkeypatch.setattr(leases, "_utc_now", lambda: ticks[0])
    key, manifest, registry, store, _ = inputs(tmp_path)
    lease = leases.build_lease(
        task_id=TASK_ID,
        release_id=manifest["release_id"],
        candidate_sha=SHA,
        manifest_digest=manifest["manifest_digest"],
        target_environment="dev",
        private_key=key,
        ttl_seconds=60,
        issued_at=start,
    )
    store.record_issued(lease)
    expired = start + timedelta(seconds=60)
    original_get = store.get
    if edge == "lookup":

        def get(lease_id: str) -> Any:
            record = original_get(lease_id)
            ticks[0] = expired
            return record

        monkeypatch.setattr(store, "get", get)
    elif edge in {"transition", "completion"}:
        original_consume = store.consume

        def consume(*args: Any, **kwargs: Any) -> Any:
            if edge == "transition":
                ticks[0] = expired
            result = original_consume(*args, **kwargs)
            ticks[0] = expired
            return result

        monkeypatch.setattr(store, "consume", consume)
    else:
        ticks[0] = expired
    admitted, errors, receipt = leases.admit_and_consume(
        lease, public_key=key.public_key(), state_store=store, consumed_by="offline"
    )
    assert not admitted and errors and receipt["admitted"] is False
    assert original_get(lease["lease_id"])["state"] == (
        "consumed" if edge == "completion" else "issued"
    )


@pytest.mark.parametrize("edge", ["before-write", "lost-time-in-write"])
def test_intercepted_gcs_cas_clock_is_fresh_and_postwrite_claim_stays_consumed(
    tmp_path: Any, monkeypatch: Any, edge: str
) -> None:
    import json

    start = datetime.now(UTC).replace(microsecond=0)
    ticks = [start]
    monkeypatch.setattr(leases, "_utc_now", lambda: ticks[0])
    key, _, _, store, _ = inputs(tmp_path)
    lease = leases.build_lease(
        task_id=TASK_ID,
        release_id="offline-gcs-test",
        candidate_sha=SHA,
        manifest_digest="sha256:" + "a" * 64,
        target_environment="dev",
        private_key=key,
        issued_at=start,
        ttl_seconds=60,
    )
    persisted = {"state": "issued", "lease": lease}
    remote = leases._GCSLeaseStateStore.__new__(leases._GCSLeaseStateStore)
    remote._uri = "gs://offline-fixture/leases"

    def read(_: str) -> Any:
        if edge == "before-write":
            ticks[0] = start + timedelta(seconds=60)
        return dict(persisted), 41

    remote._read = read
    writes = []

    class Blob:
        def upload_from_string(self, payload: str, **kwargs: Any) -> None:
            assert kwargs["if_generation_match"] == 41
            writes.append(payload)
            persisted.update(json.loads(payload))
            ticks[0] = start + timedelta(seconds=60)

    remote._blob = lambda _: Blob()
    store._remote = remote  # intercepted transport, no ADC/bucket/network/key lookup
    admitted, errors, _ = leases.admit_and_consume(
        lease, public_key=key.public_key(), state_store=store, consumed_by="offline"
    )
    assert not admitted and errors
    assert persisted["state"] == ("issued" if edge == "before-write" else "consumed")
    assert len(writes) == (0 if edge == "before-write" else 1)


def test_real_local_flock_wait_rechecks_expiry_after_acquiring_lock(
    tmp_path: Any, monkeypatch: Any
) -> None:
    import fcntl
    import threading
    from concurrent.futures import ThreadPoolExecutor

    start = datetime.now(UTC).replace(microsecond=0)
    ticks = [start]
    monkeypatch.setattr(leases, "_utc_now", lambda: ticks[0])
    key, _, _, store, _ = inputs(tmp_path)
    lease = leases.build_lease(
        task_id=TASK_ID,
        release_id="offline-flock-test",
        candidate_sha=SHA,
        manifest_digest="sha256:" + "b" * 64,
        target_environment="dev",
        private_key=key,
        issued_at=start,
        ttl_seconds=60,
    )
    store.record_issued(lease)
    reached = threading.Event()
    original = fcntl.flock

    def lock(fd: int, operation: int) -> None:
        if operation == fcntl.LOCK_EX:
            reached.set()
        original(fd, operation)

    with store._path(lease["lease_id"]).open("r+") as owner:
        original(owner.fileno(), fcntl.LOCK_EX)
        monkeypatch.setattr(leases.fcntl, "flock", lock)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(store.consume, lease, consumed_by="offline")
            assert reached.wait(5)
            ticks[0] = start + timedelta(seconds=60)
            original(owner.fileno(), fcntl.LOCK_UN)
            with pytest.raises(leases.LeaseStateError):
                future.result(timeout=5)
    assert store.get(lease["lease_id"])["state"] == "issued"


def test_consistent_admission_positive_control_does_consume(tmp_path: Any) -> None:
    values = inputs(tmp_path)
    admitted, errors, _ = decide(values)
    assert admitted and not errors
    assert values[3].get(values[4]["lease_id"])["state"] == "consumed"

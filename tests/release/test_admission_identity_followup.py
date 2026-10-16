"""Direct decision, real synthetic signed/local leases; no operational admission."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from delivery_toolchain.release import check_runtime_admission as admission
from delivery_toolchain.release.release_lease import build_lease
from delivery_toolchain.release.release_manifest import (
    compute_manifest_digest,
    validate_release_admission,
)
from tests.release.test_admission_composition_boundary import decide, inputs
from tests.release.test_runtime_admission import SHA, TASK_ID


@pytest.mark.parametrize("contradiction", ["failed-receipt", "open-blocker"])
def test_inconsistent_target_cannot_hide_same_stage_failed_gate(
    tmp_path: Any, contradiction: str
) -> None:
    values = inputs(tmp_path)
    gate = values[2]["gates"][0]
    gate["admission_target"] = "staging"  # stage/environment STILL candidate-built/dev
    if contradiction == "failed-receipt":
        gate["receipts"].append(
            {**gate["receipts"][0], "result": "fail", "receipt_id": "hidden-failure"}
        )
    else:
        gate["blockers"] = ["hidden open blocker"]
    admitted, errors, _ = decide(values)
    assert not admitted and errors
    assert values[3].get(values[4]["lease_id"])["state"] == "issued"


@pytest.mark.parametrize("expected", ["old-digest", "omitted", "different-candidate"])
def test_direct_manifest_cannot_consume_another_manifest_lease(
    tmp_path: Any, expected: str
) -> None:
    key, original, registry, store, lease = inputs(tmp_path)
    other = deepcopy(original)
    other["components"]["api"]["image"] = "ghcr.io/example/api@sha256:" + "9" * 64
    if expected == "different-candidate":
        other["candidate_sha"] = "d" * 40
    other["manifest_digest"] = compute_manifest_digest(other)
    admitted, errors, _ = admission.admit_release(
        registry,
        lease,
        release_sha=SHA,
        environment="dev",
        task_id=TASK_ID,
        public_key=key.public_key(),
        state_store=store,
        manifest=other,
        manifest_digest=None if expected == "omitted" else original["manifest_digest"],
        component_images={"api": other["components"]["api"]["image"]},
        consumed_by="offline",
    )
    assert not admitted and errors
    assert store.get(lease["lease_id"])["state"] == "issued"


def test_missing_optional_expected_digest_derives_actual_valid_identity(tmp_path: Any) -> None:
    key, manifest, registry, store, lease = inputs(tmp_path)
    admitted, errors, _ = admission.admit_release(
        registry,
        lease,
        release_sha=SHA,
        environment="dev",
        task_id=TASK_ID,
        public_key=key.public_key(),
        state_store=store,
        manifest=manifest,
        component_images={"api": manifest["components"]["api"]["image"]},
        consumed_by="offline",
    )
    assert admitted and not errors
    assert store.get(lease["lease_id"])["state"] == "consumed"


def test_candidate_identity_is_bound_even_when_every_digest_matches(tmp_path: Any) -> None:
    key, original, registry, store, _ = inputs(tmp_path)
    other = deepcopy(original)
    other["candidate_sha"] = "d" * 40
    other["manifest_digest"] = compute_manifest_digest(other)
    assert not validate_release_admission(other, environment="dev")
    registry["release"]["manifest_digest"] = other["manifest_digest"]
    lease = build_lease(
        task_id=TASK_ID,
        release_id=other["release_id"],
        candidate_sha=SHA,
        manifest_digest=other["manifest_digest"],
        target_environment="dev",
        private_key=key,
    )
    store.record_issued(lease)
    admitted, errors, _ = admission.admit_release(
        registry,
        lease,
        release_sha=SHA,
        environment="dev",
        task_id=TASK_ID,
        public_key=key.public_key(),
        state_store=store,
        manifest=other,
        manifest_digest=other["manifest_digest"],
        consumed_by="offline",
    )
    assert not admitted and any("candidate_sha" in e for e in errors)
    assert store.get(lease["lease_id"])["state"] == "issued"


def test_legitimate_other_stage_open_gate_does_not_require_future_clearance(tmp_path: Any) -> None:
    values = inputs(tmp_path)
    gate = values[2]["gates"][0]
    gate.update(
        stage="dev-verified",
        environment="dev",
        admission_target="staging",
        status="blocked",
        blockers=["future staging evidence"],
    )
    admitted, errors, _ = decide(values)
    assert admitted and not errors

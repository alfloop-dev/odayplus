"""Synthetic signed local leases: signoff-shape consistency, NOT real approval."""

import pytest

from delivery_toolchain.e2e.check_release_gate_registry import validate_release
from tests.release.test_admission_composition_boundary import decide, inputs


@pytest.mark.parametrize(
    "bad",
    [
        "missing",
        None,
        "not-an-object",
        {},
        {"approver": "", "date": "2026-08-24"},
        {"approver": True, "date": "2026-08-24"},
        {"approver": "fixture-human", "date": "not-a-date"},
        {"approver": "fixture-human"},
        {"date": "2026-08-24"},
    ],
)
def test_direct_admission_missing_or_malformed_signoff_refuses_without_consuming(tmp_path, bad):
    values = inputs(tmp_path)
    release = values[2]["release"]
    release["human_signoff"] = {
        "approver": "fixture-human-not-operational-approval",
        "date": "2026-08-24",
    }
    if bad == "missing":
        release.pop("human_signoff")
    else:
        release["human_signoff"] = bad
    canonical = []
    validate_release(release, canonical)
    assert any("human_signoff" in error for error in canonical)
    admitted, errors, _ = decide(values)
    assert not admitted and any("human_signoff" in error for error in errors)
    assert values[3].get(values[4]["lease_id"])["state"] == "issued"


def test_valid_synthetic_signoff_keeps_staged_admission_and_future_open_gates_legal(tmp_path):
    values = inputs(tmp_path)
    values[2]["release"]["human_signoff"] = {
        "approver": "fixture-human-not-operational-approval",
        "date": "2026-08-24",
    }
    gate = values[2]["gates"][0]
    gate.update(
        stage="dev-verified",
        environment="dev",
        admission_target="staging",
        status="blocked",
        blockers=["future staging proof"],
    )
    admitted, errors, _ = decide(values)
    assert admitted and not errors and values[3].get(values[4]["lease_id"])["state"] == "consumed"

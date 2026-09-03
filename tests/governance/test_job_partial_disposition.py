"""Governance and disposition verification tests for ODP-FR-SHARED-001 / PARTIAL.

Task: ODP-JOB-PARTIAL-DISPOSITION-001
Verifies:
1. ODP-FR-SHARED-001 member completeness (6 members: 5 satisfied, 1 absent).
2. Five satisfied members resolve to shared/governance/vocabularies.py::JobStatus with VERIFIED state.
3. PARTIAL member remains absent with BLOCKED_BY_EVIDENCE disposition state.
4. PARTIAL disposition carries complete statutory metadata, evidence reference, and handback package ID.
5. Handback document docs/evidence/ODP_JOB_PARTIAL_DISPOSITION_2026-09-03.md exists with required contracts.
6. Clean separation of JobStatus (business outcome) and JobDeliveryState (queue mechanics).
7. Absolute refusal of AI self-signed waivers or fake human signatures.
8. check_requirement_members validator passes cleanly.
"""

from __future__ import annotations

import json

from delivery_toolchain.governance.check_requirement_members import (
    MANIFEST_PATH,
    REPO_ROOT,
    check,
    resolve,
)
from shared.governance.vocabularies import JobDeliveryState, JobStatus


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _get_requirement(manifest: dict, req_id: str) -> dict:
    for req in manifest.get("requirements", []):
        if req.get("id") == req_id:
            return req
    raise AssertionError(f"Requirement {req_id!r} not found in manifest")


def test_shared001_member_list_and_counts() -> None:
    manifest = _load_manifest()
    shared001 = _get_requirement(manifest, "ODP-FR-SHARED-001")

    assert shared001["member_count"] == 6
    members = shared001["members"]
    assert len(members) == 6

    by_name = {m["name"]: m for m in members}
    assert set(by_name.keys()) == {
        "QUEUED",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "CANCELLED",
        "PARTIAL",
    }

    # Satisfied members must resolve to actual implementation symbols
    for name in ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"):
        m = by_name[name]
        assert m["status"] == "satisfied"
        assert m["disposition"]["state"] == "VERIFIED"
        assert resolve(REPO_ROOT, m["evidence"]) is None


def test_shared001_partial_disposition_state_and_handback_metadata() -> None:
    manifest = _load_manifest()
    shared001 = _get_requirement(manifest, "ODP-FR-SHARED-001")
    by_name = {m["name"]: m for m in shared001["members"]}

    partial = by_name["PARTIAL"]
    assert partial["status"] == "absent"

    disp = partial["disposition"]
    assert disp["state"] == "BLOCKED_BY_EVIDENCE"
    assert disp["evidence_owner"] == "Platform Infrastructure Lead"
    assert disp["next_review_date"] == "2026-10-01"
    assert "HB-SHARED001-PARTIAL-001" in partial["note"]
    assert disp.get("handback_id") == "HB-SHARED001-PARTIAL-001"
    assert "ODP_JOB_PARTIAL_DISPOSITION_2026-09-03.md" in disp["formal_handback_ref"]
    assert disp.get("reopen_trigger")
    assert disp.get("evidence_needed")
    assert disp.get("rationale")

    # PARTIAL may NOT be claimed as DECIDED, IMPLEMENTATION_READY, or VERIFIED without human sign-off / real producer
    assert disp["state"] != "DECIDED"
    assert disp["state"] != "IMPLEMENTATION_READY"
    assert disp["state"] != "VERIFIED"


def test_shared001_handback_document_exists_and_covers_contracts() -> None:
    handback_doc = REPO_ROOT / "docs" / "evidence" / "ODP_JOB_PARTIAL_DISPOSITION_2026-09-03.md"
    assert handback_doc.is_file(), "Handback document must exist"

    content = handback_doc.read_text(encoding="utf-8")
    assert "HB-SHARED001-PARTIAL-001" in content
    assert "ODP-FR-SHARED-001" in content
    assert "BLOCKED_BY_EVIDENCE" in content
    assert "pathway_a_implementation" in content
    assert "pathway_b_formal_amendment_or_waiver" in content

    # Verify key design contracts are established in the handback document
    assert "狀態轉移與業務結果模型契約" in content
    assert "明細收據與成員識別架構契約" in content
    assert "重試契約（不重做成功項）" in content
    assert "型別與概念邊界分離" in content or "型別分離" in content


def test_job_status_and_delivery_state_type_separation() -> None:
    # Verify JobStatus outcomes
    outcome_values = {s.value for s in JobStatus}
    assert outcome_values == {"queued", "running", "succeeded", "failed", "cancelled", "partial"}

    # Verify JobDeliveryState mechanics
    delivery_values = {d.value for d in JobDeliveryState}
    assert delivery_values == {"retrying", "dead_letter"}

    # Outcomes and delivery states must be disjoint sets
    assert outcome_values.isdisjoint(delivery_values)


def test_check_requirement_members_passes_with_zero_failures() -> None:
    failures, tally = check(REPO_ROOT, MANIFEST_PATH, reference_date=None)
    assert failures == [], f"check_requirement_members returned failures: {failures}"
    assert tally["requirements"] == 6
    assert tally["members"] == 32
    assert tally["satisfied"] == 24
    assert tally["absent"] == 8

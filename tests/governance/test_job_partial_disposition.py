"""Governance and disposition verification tests for ODP-FR-SHARED-001 / PARTIAL.

Task: ODP-JOB-PARTIAL-PRODUCER-RECONCILIATION-001
Verifies:
1. ODP-FR-SHARED-001 member completeness (6 members: all 6 satisfied with real implementation symbols).
2. Five satisfied members resolve to shared/governance/vocabularies.py::JobStatus with VERIFIED state.
3. PARTIAL member is satisfied and resolves to apps/worker/oday_worker/handlers.py::handle_batch_listing_intake with BLOCKED_BY_EVIDENCE disposition state.
4. PARTIAL disposition carries complete statutory metadata, evidence reference, and handback package ID.
5. In-tree producer symbols resolve and are registered in build_default_registry, with derive_batch_status_and_summary producing PARTIAL.
6. Anti-counterfeiting assertions:
   - Refusal of nonexistent producer symbols (cannot declare satisfied without valid resolvable symbol).
   - Refusal of premature gate clearing (cannot mark VERIFIED or DECIDED without live production evidence / human sign-off).
   - Refusal of AI self-signed waivers or fake human signatures.
7. Handback document docs/evidence/ODP_JOB_PARTIAL_DISPOSITION_2026-09-03.md exists with required contracts.
8. Reconciliation evidence document docs/evidence/human-decisions/ODP-JOB-PARTIAL-PRODUCER-RECONCILIATION-001/README.md exists.
9. Clean separation of JobStatus (business outcome) and JobDeliveryState (queue mechanics).
10. check_requirement_members validator passes cleanly.
"""

from __future__ import annotations

import json

from apps.worker.oday_worker.handlers import (
    BATCH_LISTING_INTAKE_JOB_TYPE,
    build_default_registry,
    handle_batch_listing_intake,
)
from delivery_toolchain.governance.check_requirement_members import (
    MANIFEST_PATH,
    REPO_ROOT,
    check,
    is_ai_decider,
    resolve,
)
from shared.governance.vocabularies import JobDeliveryState, JobStatus
from shared.jobs.receipts import (
    ItemError,
    ItemReceipt,
    ItemStatus,
    derive_batch_status_and_summary,
)


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

    # All 6 members must be satisfied with resolvable symbols
    for name in ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"):
        m = by_name[name]
        assert m["status"] == "satisfied"
        assert m["disposition"]["state"] == "VERIFIED"
        assert resolve(REPO_ROOT, m["evidence"]) is None

    partial = by_name["PARTIAL"]
    assert partial["status"] == "satisfied"
    assert resolve(REPO_ROOT, partial["evidence"]) is None
    assert partial["disposition"]["state"] == "BLOCKED_BY_EVIDENCE"


def test_shared001_partial_disposition_state_and_handback_metadata() -> None:
    manifest = _load_manifest()
    shared001 = _get_requirement(manifest, "ODP-FR-SHARED-001")
    by_name = {m["name"]: m for m in shared001["members"]}

    partial = by_name["PARTIAL"]
    assert partial["status"] == "satisfied"
    assert partial["evidence"] == "apps/worker/oday_worker/handlers.py::handle_batch_listing_intake"
    assert resolve(REPO_ROOT, partial["evidence"]) is None

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
    assert "b19513a1419497dce2ddd5054df5bbe0bd732a69" in disp["rationale"] or "b19513a1419497dce2ddd5054df5bbe0bd732a69" in partial["note"]

    # PARTIAL may NOT be claimed as DECIDED, IMPLEMENTATION_READY, or VERIFIED without human sign-off / live production evidence
    assert disp["state"] != "DECIDED"
    assert disp["state"] != "IMPLEMENTATION_READY"
    assert disp["state"] != "VERIFIED"


def test_shared001_partial_producer_symbol_resolution_and_default_registry() -> None:
    # 1. Verify symbol resolution through governance check resolve()
    assert resolve(REPO_ROOT, "apps/worker/oday_worker/handlers.py::handle_batch_listing_intake") is None
    assert resolve(REPO_ROOT, "apps/worker/oday_worker/handlers.py::build_default_registry") is None
    assert resolve(REPO_ROOT, "shared/jobs/receipts.py::derive_batch_status_and_summary") is None

    # 2. Verify handler registration in default runtime registry
    registry = build_default_registry()
    handler = registry.get(BATCH_LISTING_INTAKE_JOB_TYPE)
    assert handler is handle_batch_listing_intake

    # 3. Verify that derive_batch_status_and_summary genuinely computes JobStatus.PARTIAL
    receipts = [
        ItemReceipt(
            item_id="row-001",
            item_status=ItemStatus.SUCCEEDED.value,
            attempt=1,
            result_ref="intake-001",
        ),
        ItemReceipt(
            item_id="row-002",
            item_status=ItemStatus.FAILED.value,
            attempt=1,
            error=ItemError(code="MISSING_DATA", message="Missing field", retryable=False),
        ),
    ]
    derived_status, summary = derive_batch_status_and_summary(receipts)
    assert derived_status == JobStatus.PARTIAL
    assert summary.total_count == 2
    assert summary.succeeded_count == 1
    assert summary.failed_count == 1

    # 4. Anti-counterfeiting: fake symbols must fail resolution
    fake_resolution = resolve(REPO_ROOT, "apps/worker/oday_worker/handlers.py::NonExistentBatchHandler")
    assert fake_resolution is not None
    assert "defines no 'NonExistentBatchHandler'" in fake_resolution


def test_shared001_partial_gate_protection_and_anti_counterfeiting() -> None:
    # 1. Verify AI decider detection blocks AI agents from self-signing requirement waivers
    assert is_ai_decider("Antigravity") is True
    assert is_ai_decider("Codex") is True
    assert is_ai_decider("Claude") is True
    assert is_ai_decider("Gemini") is True
    assert is_ai_decider("Platform Infrastructure Lead") is False
    assert is_ai_decider("Human/Ops (Architecture Board)") is False

    # 2. Verify that manifest PARTIAL member does not have AI decider or DECIDED state
    manifest = _load_manifest()
    shared001 = _get_requirement(manifest, "ODP-FR-SHARED-001")
    partial = next(m for m in shared001["members"] if m["name"] == "PARTIAL")
    disp = partial["disposition"]
    assert disp["state"] == "BLOCKED_BY_EVIDENCE"
    assert "decider" not in disp


def test_shared001_reconciliation_evidence_document() -> None:
    doc_path = REPO_ROOT / "docs" / "evidence" / "human-decisions" / "ODP-JOB-PARTIAL-PRODUCER-RECONCILIATION-001" / "README.md"
    assert doc_path.is_file(), f"Reconciliation README must exist at {doc_path}"

    content = doc_path.read_text(encoding="utf-8")
    assert "ODP-JOB-PARTIAL-PRODUCER-RECONCILIATION-001" in content
    assert "b19513a1419497dce2ddd5054df5bbe0bd732a69" in content
    assert "handle_batch_listing_intake" in content
    assert "build_default_registry" in content
    assert "derive_batch_status_and_summary" in content
    assert "HB-SHARED001-PARTIAL-001" in content
    assert "BLOCKED_BY_EVIDENCE" in content
    assert "test_durable_partial_batch.py" in content


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
    manifest = _load_manifest()
    total_reqs = len(manifest.get("requirements", []))
    total_members = sum(len(r.get("members", [])) for r in manifest.get("requirements", []))
    assert tally["requirements"] == total_reqs
    assert tally["requirements"] >= 6
    assert tally["members"] == total_members
    assert tally["dispositions"]["BLOCKED_BY_EVIDENCE"] >= 1

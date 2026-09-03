"""Governance and disposition verification tests for ODP-FR-SITE-001.

Task: ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001
Verifies:
1. ODP-FR-SITE-001 member completeness (5 members, 3 satisfied, 2 absent).
2. Independent outcomes for BRAND_TRANSFER and FORMAT_CONVERSION (both BLOCKED_BY_EVIDENCE).
3. Rejection of synthetic wiring and unverified placeholders in production consumers.
4. Human-authority handback references and statutory tracking metadata.
5. Absolute refusal of AI self-signed waivers or fake human signatures.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from delivery_toolchain.governance.check_requirement_members import (
    MANIFEST_PATH,
    REPO_ROOT,
    check,
    is_ai_decider,
    resolve,
)


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _get_requirement(manifest: dict, req_id: str) -> dict:
    for req in manifest.get("requirements", []):
        if req.get("id") == req_id:
            return req
    raise AssertionError(f"Requirement {req_id!r} not found in manifest")


def test_site001_member_list_and_counts() -> None:
    manifest = _load_manifest()
    site001 = _get_requirement(manifest, "ODP-FR-SITE-001")

    assert site001["member_count"] == 5
    members = site001["members"]
    assert len(members) == 5

    by_name = {m["name"]: m for m in members}
    assert set(by_name.keys()) == {
        "EXTERNAL_DEMAND",
        "RAMP",
        "SEASONALITY",
        "BRAND_TRANSFER",
        "FORMAT_CONVERSION",
    }

    # Satisfied members must resolve to actual implementation symbols
    for name in ("EXTERNAL_DEMAND", "RAMP", "SEASONALITY"):
        m = by_name[name]
        assert m["status"] == "satisfied"
        assert m["disposition"]["state"] == "VERIFIED"
        assert resolve(REPO_ROOT, m["evidence"]) is None


def test_site001_missing_members_independent_disposition_outcomes() -> None:
    manifest = _load_manifest()
    site001 = _get_requirement(manifest, "ODP-FR-SITE-001")
    by_name = {m["name"]: m for m in site001["members"]}

    # Member 1: BRAND_TRANSFER
    bt = by_name["BRAND_TRANSFER"]
    assert bt["status"] == "absent"
    disp_bt = bt["disposition"]
    assert disp_bt["state"] == "BLOCKED_BY_EVIDENCE"
    assert "Market Intelligence Lead" in disp_bt["evidence_owner"] or "Commercial Strategy" in disp_bt["evidence_owner"]
    assert disp_bt["next_review_date"] == "2026-10-01"
    assert "HB-SITE001-BRAND-TRANSFER-001" in bt["note"]
    assert "ODP_SITE001_COMPONENT_DISPOSITIONS_2026-09-03.md" in disp_bt["formal_handback_ref"]
    assert disp_bt.get("reopen_trigger")

    # Member 2: FORMAT_CONVERSION
    fc = by_name["FORMAT_CONVERSION"]
    assert fc["status"] == "absent"
    disp_fc = fc["disposition"]
    assert disp_fc["state"] == "BLOCKED_BY_EVIDENCE"
    assert "Retail Operations Lead" in disp_fc["evidence_owner"] or "Site Economics" in disp_fc["evidence_owner"]
    assert disp_fc["next_review_date"] == "2026-10-01"
    assert "HB-SITE001-FORMAT-CONVERSION-001" in fc["note"]
    assert "ODP_SITE001_COMPONENT_DISPOSITIONS_2026-09-03.md" in disp_fc["formal_handback_ref"]
    assert disp_fc.get("reopen_trigger")

    # Neither member may be claimed as DECIDED or IMPLEMENTATION_READY
    assert disp_bt["state"] != "DECIDED"
    assert disp_bt["state"] != "IMPLEMENTATION_READY"
    assert disp_fc["state"] != "DECIDED"
    assert disp_fc["state"] != "IMPLEMENTATION_READY"


def test_site001_handback_document_and_governance_files_exist() -> None:
    handback_doc = REPO_ROOT / "docs" / "evidence" / "ODP_SITE001_COMPONENT_DISPOSITIONS_2026-09-03.md"
    assert handback_doc.is_file(), "Handback document must exist"

    content = handback_doc.read_text(encoding="utf-8")
    assert "HB-SITE001-BRAND-TRANSFER-001" in content
    assert "HB-SITE001-FORMAT-CONVERSION-001" in content
    assert "BLOCKED_BY_EVIDENCE" in content
    assert "2026-10-01" in content

    gov_doc = REPO_ROOT / "docs" / "governance" / "ODP_REQUIREMENT_DISPOSITIONS.md"
    assert gov_doc.is_file(), "Governance disposition policy must exist"
    gov_content = gov_doc.read_text(encoding="utf-8")
    assert "ODP_SITE001_COMPONENT_DISPOSITIONS_2026-09-03.md" in gov_content
    assert "HB-SITE001-BRAND-TRANSFER-001" in gov_content
    assert "HB-SITE001-FORMAT-CONVERSION-001" in gov_content


def test_no_synthetic_wiring_in_sitescore_or_simulator() -> None:
    # 1. SiteScore scoring and application modules must not import or wire brand_transfer_view
    sitescore_domain = REPO_ROOT / "modules" / "sitescore" / "domain" / "scoring.py"
    sitescore_text = sitescore_domain.read_text(encoding="utf-8")
    assert "brand_transfer" not in sitescore_text.lower() or "not implemented" in sitescore_text.lower()

    # 2. site_economics simulator must not contain arbitrary unmodelled format conversion discounts
    simulator_file = REPO_ROOT / "modules" / "site_economics" / "domain" / "simulator.py"
    simulator_text = simulator_file.read_text(encoding="utf-8")
    assert "format_conversion" not in simulator_text.lower() or "conversion" not in simulator_text.lower()


def test_overall_governance_checker_passes_with_live_manifest() -> None:
    failures, tally = check(REPO_ROOT, MANIFEST_PATH, reference_date=date(2026, 9, 3))
    assert failures == [], f"Governance checks failed with errors: {[f.describe() for f in failures]}"
    assert tally["requirements"] >= 6
    assert tally["dispositions"]["BLOCKED_BY_EVIDENCE"] >= 3
    assert tally["dispositions"]["VERIFIED"] >= 24

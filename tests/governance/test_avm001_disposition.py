"""What `ODP-FR-AVM-001`'s `DEPRECIATION` member is allowed to claim.

The member is the only absent one of the six. A contract now exists for it --
`docs/design/ODP_AVM_DEPRECIATION_CONTRACT_2026-09-03.md` -- and the temptation
a written contract creates is to let the writing stand in for the work: mark the
member satisfied, or `VERIFIED`, or rule it out with a sentence. Each of those
would make the manifest say the depreciation gap is closed while
`modules/avm/domain/valuation.py` still has no depreciation in it.

So this module pins the honest shape and, in three negative tests, pins that the
dishonest ones actually fail. A disposition gate that happens not to look at this
member is indistinguishable from a green one, and the whole point of registering
`ODP-FR-AVM-001` was to make the absence machine-checkable rather than prose.
"""

from __future__ import annotations

import ast
import json
from datetime import date
from pathlib import Path

from delivery_toolchain.governance.check_requirement_members import (
    MANIFEST_PATH,
    REPO_ROOT,
    WAIVER_SIGNAL_FIELDS,
    check,
    resolve,
)

REQUIREMENT = "ODP-FR-AVM-001"
CONTRACT_DOC = REPO_ROOT / "docs" / "design" / "ODP_AVM_DEPRECIATION_CONTRACT_2026-09-03.md"
DISPOSITION_DOC = (
    REPO_ROOT / "docs" / "evidence" / "ODP_AVM001_DEPRECIATION_DISPOSITION_2026-09-04.md"
)
GOVERNANCE_DOC = REPO_ROOT / "docs" / "governance" / "ODP_REQUIREMENT_DISPOSITIONS.md"
SPEC_FILE = REPO_ROOT / "modules" / "avm" / "tests" / "test_avm_depreciation_contract.py"

CONTRACT_SPECS = (
    "test_two_inputs_differing_only_in_depreciation_produce_different_valuation",
    "test_valuation_input_carries_the_depreciation_contract_fields",
    "test_the_asset_lens_publishes_its_depreciation_evidence",
    "test_missing_depreciation_inputs_do_not_yield_a_complete_card",
    "test_an_appraised_basis_is_not_depreciated_twice",
    "test_a_legacy_card_keeps_its_legacy_version_and_is_not_recomputed",
    "test_a_v0_pin_reproduces_the_pre_cutover_numbers",
    "test_calibration_does_not_silently_mix_depreciation_versions",
)


def _strict_xfail_specs(path: Path) -> set[str]:
    """Names of the tests in *path* marked `xfail(strict=True)`.

    Read from the syntax tree rather than by grepping: `strict=True` also
    appears in the module docstring explaining why the markers are strict, and
    a count that includes prose would pass while a marker silently loses its
    strictness -- the one edit that would let an implementation land without
    anyone returning to this manifest.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not (
                isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "xfail"
            ):
                continue
            strict = next(
                (kw.value for kw in decorator.keywords if kw.arg == "strict"), None
            )
            if isinstance(strict, ast.Constant) and strict.value is True:
                names.add(node.name)
    return names


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _members(manifest: dict) -> dict[str, dict]:
    for req in manifest.get("requirements", []):
        if req.get("id") == REQUIREMENT:
            return {m["name"]: m for m in req["members"]}
    raise AssertionError(f"Requirement {REQUIREMENT!r} not found in manifest")


def _requirement(manifest: dict) -> dict:
    for req in manifest.get("requirements", []):
        if req.get("id") == REQUIREMENT:
            return req
    raise AssertionError(f"Requirement {REQUIREMENT!r} not found in manifest")


def _write_manifest(tmp_path: Path, manifest: dict) -> Path:
    path = tmp_path / "set_valued_requirements.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path


def test_the_six_members_are_registered_and_five_of_them_resolve() -> None:
    """Five satisfied members must name code that exists; the sixth is the gap."""
    manifest = _load_manifest()
    requirement = _requirement(manifest)

    assert requirement["member_count"] == 6
    assert len(requirement["members"]) == 6

    by_name = _members(manifest)
    assert set(by_name) == {
        "GM_TTM",
        "GM_FWD",
        "DEPRECIATION",
        "ASSET",
        "LEASE",
        "NORMALIZATION",
    }

    for name in ("GM_TTM", "GM_FWD", "ASSET", "LEASE", "NORMALIZATION"):
        member = by_name[name]
        assert member["status"] == "satisfied"
        assert member["disposition"]["state"] == "VERIFIED"
        assert resolve(REPO_ROOT, member["evidence"]) is None, name

    assert by_name["DEPRECIATION"]["status"] == "absent"


def test_depreciation_is_implementation_ready_with_an_owner_and_a_batch() -> None:
    disposition = _members(_load_manifest())["DEPRECIATION"]["disposition"]

    assert disposition["state"] == "IMPLEMENTATION_READY"
    assert disposition["assigned_to"].strip()
    assert disposition["target_phase"].strip()
    assert disposition["acceptance_criteria"].strip()
    assert disposition["rationale"].strip()

    # The state names a scheduled implementation, not a closed gap or a ruling.
    assert disposition["state"] not in {"VERIFIED", "DECIDED"}


def test_the_disposition_carries_no_decision_fields() -> None:
    """`IMPLEMENTATION_READY` must not be a waiver wearing another state's name.

    §3.5 of the policy judges statutory fields wherever they sit. Carrying one
    here would drag the member into the `DECIDED` gate, whose only exit is a
    signed decider -- and the only signature available to an autoworker is its
    own, which §3.2 forbids. The honest reading is that nobody ruled on this.
    """
    member = _members(_load_manifest())["DEPRECIATION"]
    disposition = member["disposition"]

    for field in WAIVER_SIGNAL_FIELDS:
        assert not disposition.get(field), f"{field} present on a non-DECIDED disposition"
    assert "formal_handback_ref" not in disposition


def test_the_disposition_points_at_documents_that_exist() -> None:
    member = _members(_load_manifest())["DEPRECIATION"]

    for doc in (CONTRACT_DOC, DISPOSITION_DOC, GOVERNANCE_DOC, SPEC_FILE):
        assert doc.is_file(), f"{doc} referenced by the disposition does not exist"

    note = member["note"]
    assert "ODP_AVM_DEPRECIATION_CONTRACT_2026-09-03.md" in note
    assert "ODP_AVM001_DEPRECIATION_DISPOSITION_2026-09-04.md" in note
    assert "ODP_REQUIREMENT_DISPOSITIONS.md" in note

    governance = GOVERNANCE_DOC.read_text(encoding="utf-8")
    assert "### 4.9 `ODP-FR-AVM-001`" in governance
    assert "IMPLEMENTATION_READY" in governance
    assert "ODP_AVM001_DEPRECIATION_DISPOSITION_2026-09-04.md" in governance

    evidence = DISPOSITION_DOC.read_text(encoding="utf-8")
    assert "IMPLEMENTATION_READY" in evidence
    assert "test_avm_depreciation_contract.py" in evidence


def test_the_acceptance_criteria_name_specs_that_exist_and_still_fail() -> None:
    """The criteria are executable, and `strict=True` is what makes them expire.

    Without `strict`, an implementation would turn the specs into silent XPASSes
    and the member could sit at `IMPLEMENTATION_READY` forever. With it, the
    same event turns the suite red and forces someone back to this manifest.
    """
    disposition = _members(_load_manifest())["DEPRECIATION"]["disposition"]
    assert "modules/avm/tests/test_avm_depreciation_contract.py" in disposition["acceptance_criteria"]

    strict_xfails = _strict_xfail_specs(SPEC_FILE)
    assert strict_xfails == set(CONTRACT_SPECS), (
        "the acceptance criteria and the strict xfail specs have drifted apart: "
        f"only in the spec file {sorted(strict_xfails - set(CONTRACT_SPECS))}, "
        f"only in the criteria {sorted(set(CONTRACT_SPECS) - strict_xfails)}"
    )


def test_the_live_manifest_passes_the_governance_checker() -> None:
    failures, tally = check(REPO_ROOT, MANIFEST_PATH, reference_date=date(2026, 9, 3))
    assert failures == [], "\n".join(f.describe() for f in failures)
    assert tally["dispositions"]["IMPLEMENTATION_READY"] >= 3


def test_claiming_the_gap_verified_is_refused(tmp_path: Path) -> None:
    manifest = _load_manifest()
    _members(manifest)["DEPRECIATION"]["disposition"]["state"] = "VERIFIED"

    failures, _ = check(REPO_ROOT, _write_manifest(tmp_path, manifest), reference_date=date(2026, 9, 3))
    assert any(
        f.requirement == REQUIREMENT and f.member == "DEPRECIATION" and "VERIFIED" in f.problem
        for f in failures
    ), "an absent member claiming VERIFIED must be refused"


def test_an_ai_signed_ruling_on_the_gap_is_refused(tmp_path: Path) -> None:
    """The cheapest way past this member is to rule it out and sign it here."""
    manifest = _load_manifest()
    _members(manifest)["DEPRECIATION"]["disposition"] = {
        "state": "DECIDED",
        "formal_decision_ref": "docs/design/ODP_AVM_DEPRECIATION_CONTRACT_2026-09-03.md",
        "decider": "Claude2",
        "decision_date": "2026-09-04",
        "scope": "AVM asset depreciation",
        "risk_owner": "AVM Domain Lead",
        "expiry": "2027-09-01",
        "reopen_trigger": "When a finance owner requests depreciation in the asset lens.",
    }

    failures, _ = check(REPO_ROOT, _write_manifest(tmp_path, manifest), reference_date=date(2026, 9, 4))
    assert any(
        f.requirement == REQUIREMENT and f.member == "DEPRECIATION" and "AI decider" in f.problem
        for f in failures
    ), "an AI-signed waiver on the depreciation gap must be refused"


def test_dropping_the_disposition_block_is_refused(tmp_path: Path) -> None:
    """The failure this task was reopened for: an absent member with only a note."""
    manifest = _load_manifest()
    del _members(manifest)["DEPRECIATION"]["disposition"]

    failures, _ = check(REPO_ROOT, _write_manifest(tmp_path, manifest), reference_date=date(2026, 9, 3))
    assert any(
        f.requirement == REQUIREMENT and f.member == "DEPRECIATION" and "disposition" in f.problem
        for f in failures
    ), "an un-dispositioned gap must be refused"

"""Hold the 2026-10-03 truth reconciliation of the requirement manifest.

`ODP-REMEDIATION-TRUTH-RECONCILIATION-001` corrected four member records that
still said "absent / to be implemented" after the implementation had merged,
and corrected the `ODP-SA-06` provenance record that said the source bytes
could not be found while they sat in a ZIP at the repository root.

Each correction can go wrong in two directions, and this module pins both:

* **Back to stale.** A member flipped back to ``absent`` or to its old
  ``IMPLEMENTATION_READY`` / ``OPEN`` state, or the provenance record set back to
  "not found", would re-publish a claim the merged tree contradicts.
* **Past the evidence.** A symbol existing is code delivery, not run-time
  acceptance. None of the four members has a live receipt, so none may be
  ``VERIFIED``; the found SA-06 bytes are a ``draft-for-review`` document, so
  finding them is not ratification.

The expectations below are written independently of the manifest -- merge
SHAs, symbols, the ZIP member's SHA-256 recomputed from the bytes -- so the
test cannot pass merely by comparing the JSON with a copy of itself. The
negative tests mutate a copy of the live manifest (or the bytes) and require
:func:`reconciliation_violations` to refuse it.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import subprocess
import zipfile
from datetime import date
from typing import Any

import pytest

from delivery_toolchain.governance.check_requirement_members import (
    MANIFEST_PATH,
    REPO_ROOT,
    WAIVER_SIGNAL_FIELDS,
    check,
    resolve,
)

ZIP_PATH = REPO_ROOT / "oday_plus_batch_02_sa_documents.zip"
SA06_MEMBER = "ODP-SA-06_FUNCTIONAL_REQUIREMENTS_SPECIFICATION.md"
SA06_SHA256 = "43dad7bf171a5e80511a01fd289bf2132c060e08c27799dcd8f91f86fb2073ec"
AVM001_LINE = 104
AVM001_ROW_PREFIX = "| `ODP-FR-AVM-001` | 系統必須整合 GM_TTM、GM_FWD、折舊、資產、租約與正常化調整。"
HISTORICAL_OBSERVED_REF = "75d25f653aa12c21a3f9627f29af2ed4def73153"
GOVERNANCE_DOC = REPO_ROOT / "docs" / "governance" / "ODP_REQUIREMENT_DISPOSITIONS.md"
PROVENANCE_DOC = REPO_ROOT / "docs" / "evidence" / "ODP_SPEC_SOURCE_PROVENANCE_2026-09-03.md"

# (requirement, member) -> what the merged tree actually delivers.
#   symbol: importable dotted path, then attribute chain.
#   evidence: the manifest reference the checker resolves.
#   merge: the merge commit that delivered it, which must be an ancestor of HEAD.
#   stale_state: the state the record carried before the correction.
RECONCILED: dict[tuple[str, str], dict[str, Any]] = {
    ("ODP-FR-LH-005", "PREDICTION_DRIFT"): {
        "module": "modules.learninghub.application.release",
        "attrs": ("LearningHubService", "monitor_prediction_drift"),
        "evidence": "modules/learninghub/application/release.py::LearningHubService.monitor_prediction_drift",
        "merge": "0cbc5330f6a076344d2dff32370dedae5b82da4a",
        "stale_state": "IMPLEMENTATION_READY",
    },
    ("ODP-FR-AVM-001", "DEPRECIATION"): {
        "module": "modules.avm.domain.valuation",
        "attrs": ("calculate_depreciation",),
        "evidence": "modules/avm/domain/valuation.py::calculate_depreciation",
        "merge": "898c192d59b0e39d51844b8596d952104771b6d8",
        "stale_state": "IMPLEMENTATION_READY",
    },
    ("ODP-FR-INT-001", "EVENT"): {
        "module": "apps.data_platform.definitions",
        "attrs": ("scoped_cdc_device_log_sensor",),
        "evidence": "apps/data_platform/definitions.py::scoped_cdc_device_log_sensor",
        "merge": "dc0eb370b29e50f2fc916e008bdab3d08e0a3ddc",
        "stale_state": "OPEN",
    },
    ("ODP-FR-INT-001", "CDC"): {
        "module": "apps.data_platform.cdc",
        "attrs": ("ScopedCdcAdapter",),
        "evidence": "apps/data_platform/cdc.py::ScopedCdcAdapter",
        "merge": "dc0eb370b29e50f2fc916e008bdab3d08e0a3ddc",
        "stale_state": "OPEN",
    },
}

# Real human/live gaps the reconciliation must not have touched, with the shape
# each must keep. A green offline test or a decided H07/H08 does not close them.
PRESERVED_GAPS: dict[tuple[str, str], tuple[str, str]] = {
    ("ODP-FR-SHARED-001", "PARTIAL"): ("satisfied", "BLOCKED_BY_EVIDENCE"),
    ("ODP-FR-INTV-006", "ADJUST"): ("satisfied", "BLOCKED_BY_EVIDENCE"),
    ("ODP-FR-SITE-001", "BRAND_TRANSFER"): ("absent", "BLOCKED_BY_EVIDENCE"),
    ("ODP-FR-SITE-001", "FORMAT_CONVERSION"): ("absent", "BLOCKED_BY_EVIDENCE"),
    ("ODP-FR-NET-002", "LEASE"): ("absent", "BLOCKED_BY_EVIDENCE"),
    ("ODP-FR-FCT-004", "ROOT_CAUSE_CANDIDATE"): ("absent", "IMPLEMENTATION_READY"),
    ("ODP-FR-NET-002", "SEQUENCING"): ("absent", "DECIDED"),
}


def _load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _sa06_bytes() -> bytes:
    with zipfile.ZipFile(ZIP_PATH) as archive:
        return archive.read(SA06_MEMBER)


def _member(manifest: dict[str, Any], requirement: str, name: str) -> dict[str, Any]:
    for entry in manifest["requirements"]:
        if entry["id"] == requirement:
            for member in entry["members"]:
                if member["name"] == name:
                    return member
    raise AssertionError(f"{requirement}::{name} missing from manifest")


def reconciliation_violations(manifest: dict[str, Any], sa06_bytes: bytes) -> list[str]:
    """Every way *manifest* contradicts the merged tree or overstates the evidence."""
    problems: list[str] = []

    for (requirement, name), expected in RECONCILED.items():
        where = f"{requirement}::{name}"
        try:
            member = _member(manifest, requirement, name)
        except AssertionError as exc:
            problems.append(str(exc))
            continue
        if member.get("status") != "satisfied":
            problems.append(f"{where} is {member.get('status')!r} but its implementation merged")
        if member.get("evidence") != expected["evidence"]:
            problems.append(f"{where} evidence {member.get('evidence')!r} is not the merged symbol")
        disposition = member.get("disposition") or {}
        state = disposition.get("state")
        if state == expected["stale_state"] and disposition.get("previous_state") != expected["stale_state"]:
            problems.append(f"{where} still carries its stale {state} disposition")
        if state != "BLOCKED_BY_EVIDENCE":
            problems.append(
                f"{where} disposition is {state!r}; code without a live receipt must stay BLOCKED_BY_EVIDENCE"
            )
        if not str(disposition.get("evidence_needed", "")).strip():
            problems.append(f"{where} names no remaining live evidence")
        if any(str(disposition.get(f, "")).strip() for f in WAIVER_SIGNAL_FIELDS):
            problems.append(f"{where} carries decision fields; nobody ruled on it")
        history = disposition.get("history") or []
        if expected["stale_state"] not in {h.get("state") for h in history}:
            problems.append(f"{where} history lost its {expected['stale_state']} entry")
        if expected["merge"] not in json.dumps(member, ensure_ascii=False):
            problems.append(f"{where} does not cite merge {expected['merge']}")

    for (requirement, name), (status, state) in PRESERVED_GAPS.items():
        member = _member(manifest, requirement, name)
        actual = (member.get("status"), (member.get("disposition") or {}).get("state"))
        if actual != (status, state):
            problems.append(f"{requirement}::{name} gap changed to {actual}, expected {(status, state)}")

    actual_sha = hashlib.sha256(sa06_bytes).hexdigest()
    if actual_sha != SA06_SHA256:
        problems.append(f"SA-06 bytes hash {actual_sha} differs from the located artifact {SA06_SHA256}")
    lines = sa06_bytes.split(b"\n")
    row = lines[AVM001_LINE - 1] if len(lines) >= AVM001_LINE else b""

    records = manifest.get("_source_provenance", {}).get("records", {})
    sa06 = records.get("ODP-SA-06", {})
    artifact = sa06.get("source_artifact") or {}
    if artifact.get("content_sha256") != actual_sha:
        problems.append("ODP-SA-06 source_artifact.content_sha256 does not match the ZIP member bytes")
    if artifact.get("member_path") != SA06_MEMBER or artifact.get("container_path") != ZIP_PATH.name:
        problems.append("ODP-SA-06 source_artifact does not name the ZIP and member")
    location = str(artifact.get("location", ""))
    if not location.endswith(f"/{ZIP_PATH.name}!{SA06_MEMBER}"):
        problems.append(f"ODP-SA-06 location {location!r} is not the ZIP member")
    if (artifact.get("front_matter") or {}).get("version") != "0.1.0":
        problems.append("ODP-SA-06 version is not the 0.1.0 read from the bytes")

    avm = records.get("ODP-FR-AVM-001", {})
    avm_artifact = avm.get("source_artifact") or {}
    if avm_artifact.get("line") != AVM001_LINE:
        problems.append("ODP-FR-AVM-001 row is not pinned to its line")
    if avm_artifact.get("row_text", "").encode("utf-8") != row:
        problems.append("ODP-FR-AVM-001 row_text differs from the source bytes")
    if avm_artifact.get("row_sha256") != hashlib.sha256(row).hexdigest():
        problems.append("ODP-FR-AVM-001 row_sha256 does not match the source row")
    if avm_artifact.get("document_content_sha256") != actual_sha:
        problems.append("ODP-FR-AVM-001 is not bound to the SA-06 document hash")

    for record_id, record in (("ODP-SA-06", sa06), ("ODP-FR-AVM-001", avm)):
        canonical = record.get("canonical_source") or {}
        if record.get("status") != "BLOCKED_BY_EVIDENCE" or canonical.get("ratified") is not False:
            problems.append(f"{record_id} claims ratification that no authority record supports")
        if canonical.get("ratification_evidence") is not None:
            problems.append(f"{record_id} cites ratification evidence")
        if any(canonical.get(k) is None for k in ("version", "location", "content_sha256") if k in canonical):
            problems.append(f"{record_id} still records the bytes as not found")
        if "not found" in str(canonical.get("blocked_reason", "")).lower() or "neither expected path" in str(
            canonical.get("blocked_reason", "")
        ).lower():
            problems.append(f"{record_id} blocked_reason still says the bytes were not found")
        history = record.get("history") or []
        if not any(h.get("observed_ref") == HISTORICAL_OBSERVED_REF for h in history):
            problems.append(f"{record_id} lost its 2026-09-03 observation")

    return problems


# --------------------------------------------------------------------- positive


def test_the_live_manifest_has_no_reconciliation_violation() -> None:
    assert reconciliation_violations(_load_manifest(), _sa06_bytes()) == []


def test_the_live_manifest_passes_the_governance_checker() -> None:
    failures, _ = check(REPO_ROOT, MANIFEST_PATH, reference_date=date(2026, 10, 3))
    assert failures == [], "\n".join(f.describe() for f in failures)


@pytest.mark.parametrize("key", sorted(RECONCILED), ids=lambda k: f"{k[0]}::{k[1]}")
def test_each_reconciled_symbol_imports_and_resolves(key: tuple[str, str]) -> None:
    expected = RECONCILED[key]
    target: Any = importlib.import_module(expected["module"])
    for attr in expected["attrs"]:
        target = getattr(target, attr)
    assert callable(target) or isinstance(target, type) or target is not None
    assert resolve(REPO_ROOT, expected["evidence"]) is None


@pytest.mark.parametrize(
    "merge", sorted({e["merge"] for e in RECONCILED.values()}), ids=lambda sha: sha[:8]
)
def test_each_cited_merge_is_in_this_history(merge: str) -> None:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", merge, "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode == 128:
        pytest.skip(f"git history unavailable for {merge}: {result.stderr.decode(errors='replace')}")
    assert result.returncode == 0, f"{merge} is not an ancestor of HEAD"


def test_the_zip_member_is_the_recorded_sa06_bytes() -> None:
    data = _sa06_bytes()
    assert hashlib.sha256(data).hexdigest() == SA06_SHA256
    text = data.decode("utf-8")
    assert "doc_id: ODP-SA-06" in text
    assert "version: 0.1.0" in text
    # The status that keeps ratification open is in the bytes, not inferred.
    assert "status: draft-for-review" in text
    assert text.split("\n")[AVM001_LINE - 1].startswith(AVM001_ROW_PREFIX)


def test_the_strict_xfail_markers_on_the_depreciation_contract_are_gone() -> None:
    """The old disposition promised the markers would be removed by an implementation."""
    source = (REPO_ROOT / "modules" / "avm" / "tests" / "test_avm_depreciation_contract.py").read_text(
        encoding="utf-8"
    )
    assert "@pytest.mark.xfail" not in source


def test_the_docs_mirror_the_corrected_states() -> None:
    governance = GOVERNANCE_DOC.read_text(encoding="utf-8")
    for heading in ("#### 成員：`PREDICTION_DRIFT`", "#### 成員：`EVENT`", "#### 成員：`CDC`", "#### 成員：`DEPRECIATION`"):
        section = governance.split(heading, 1)[1].split("#### ", 1)[0]
        assert "- **處置狀態**：`BLOCKED_BY_EVIDENCE`" in section, heading
        assert "`status: satisfied`" in section, heading

    provenance = PROVENANCE_DOC.read_text(encoding="utf-8")
    assert SA06_SHA256 in provenance
    assert f"observed_ref: {HISTORICAL_OBSERVED_REF}" in provenance
    assert "## 2026-10-03 更正" in provenance
    # The 2026-09-03 record is kept, not rewritten.
    assert "### `ODP-SA-06`\n\n- **Status:** `BLOCKED_BY_EVIDENCE`" in provenance


# --------------------------------------------------------------------- negative


@pytest.mark.parametrize("key", sorted(RECONCILED), ids=lambda k: f"{k[0]}::{k[1]}")
def test_reverting_a_member_to_its_stale_absent_record_is_refused(key: tuple[str, str]) -> None:
    manifest = _load_manifest()
    member = _member(manifest, *key)
    stale = RECONCILED[key]["stale_state"]
    member["status"] = "absent"
    member.pop("evidence")
    member["disposition"] = {
        "state": stale,
        "assigned_to": "Owner",
        "target_phase": "Batch 4a",
        "rationale": "Scheduled for implementation.",
        "next_review_date": "2026-10-01",
    }

    problems = reconciliation_violations(manifest, _sa06_bytes())
    where = f"{key[0]}::{key[1]}"
    assert any(p.startswith(where) and "merged" in p for p in problems), problems
    assert any(p.startswith(where) and "stale" in p for p in problems), problems


@pytest.mark.parametrize("key", sorted(RECONCILED), ids=lambda k: f"{k[0]}::{k[1]}")
def test_promoting_a_member_to_verified_without_a_live_receipt_is_refused(key: tuple[str, str]) -> None:
    """The checker accepts satisfied+VERIFIED, so this module must be the one that refuses it."""
    manifest = _load_manifest()
    _member(manifest, *key)["disposition"] = {"state": "VERIFIED"}

    problems = reconciliation_violations(manifest, _sa06_bytes())
    assert any(f"{key[0]}::{key[1]}" in p and "BLOCKED_BY_EVIDENCE" in p for p in problems), problems


@pytest.mark.parametrize("key", sorted(PRESERVED_GAPS), ids=lambda k: f"{k[0]}::{k[1]}")
def test_closing_a_preserved_human_or_live_gap_is_refused(key: tuple[str, str]) -> None:
    manifest = _load_manifest()
    member = _member(manifest, *key)
    member["status"] = "satisfied"
    member["disposition"] = {"state": "VERIFIED"}

    problems = reconciliation_violations(manifest, _sa06_bytes())
    assert any(p.startswith(f"{key[0]}::{key[1]} gap changed") for p in problems), problems


def test_a_tampered_recorded_hash_is_refused() -> None:
    manifest = _load_manifest()
    manifest["_source_provenance"]["records"]["ODP-SA-06"]["source_artifact"]["content_sha256"] = "0" * 64

    problems = reconciliation_violations(manifest, _sa06_bytes())
    assert any("content_sha256 does not match" in p for p in problems), problems


def test_tampered_source_bytes_are_refused() -> None:
    data = _sa06_bytes().replace("正常化調整".encode(), "正常化".encode(), 1)

    problems = reconciliation_violations(_load_manifest(), data)
    assert any("SA-06 bytes hash" in p for p in problems), problems
    assert any("row_text differs" in p for p in problems), problems


def test_a_tampered_avm_row_hash_is_refused() -> None:
    manifest = _load_manifest()
    manifest["_source_provenance"]["records"]["ODP-FR-AVM-001"]["source_artifact"]["row_sha256"] = "f" * 64

    problems = reconciliation_violations(manifest, _sa06_bytes())
    assert any("row_sha256" in p for p in problems), problems


def test_reverting_provenance_to_not_found_is_refused() -> None:
    manifest = _load_manifest()
    record = manifest["_source_provenance"]["records"]["ODP-SA-06"]
    old = copy.deepcopy(record["history"][0]["canonical_source"])
    record["canonical_source"] = old
    record.pop("source_artifact")

    problems = reconciliation_violations(manifest, _sa06_bytes())
    assert any("still records the bytes as not found" in p for p in problems), problems
    assert any("blocked_reason still says" in p for p in problems), problems


def test_treating_found_bytes_as_ratified_is_refused() -> None:
    manifest = _load_manifest()
    record = manifest["_source_provenance"]["records"]["ODP-SA-06"]
    record["status"] = "VERIFIED"
    record["canonical_source"]["ratified"] = True

    problems = reconciliation_violations(manifest, _sa06_bytes())
    assert any("ODP-SA-06 claims ratification" in p for p in problems), problems


def test_dropping_the_historical_observation_is_refused() -> None:
    manifest = _load_manifest()
    record = manifest["_source_provenance"]["records"]["ODP-FR-AVM-001"]
    record["history"] = [h for h in record["history"] if h.get("observed_ref") != HISTORICAL_OBSERVED_REF]

    problems = reconciliation_violations(manifest, _sa06_bytes())
    assert any("lost its 2026-09-03 observation" in p for p in problems), problems

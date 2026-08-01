"""Fail-closed tests for the Gate 0-6 release gate registry and its validator.

Two things are pinned here. First, the committed registry is internally
consistent *and* still records NO-GO: no gate may be cleared while it has no
receipt bound to the release candidate SHA. Second, every fail-closed rule in
``scripts/e2e/check_release_gate_registry.py`` actually rejects the mutation it
is supposed to reject -- a validator that silently accepts a missing owner, a
stale receipt, or an evidence-free "passed" gate is worse than no validator.

Mutation cases start from the committed registry and change exactly one thing,
so a rule that stops working shows up as a test failure rather than as a green
release.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/e2e/check_release_gate_registry.py"
PRODUCT_GATE = ROOT / "scripts/e2e/check_product_release_gate.py"
REGISTRY = ROOT / "docs/evidence/gates/RELEASE_GATE_REGISTRY.json"
REGISTRY_README = ROOT / "docs/evidence/gates/README.md"

CANDIDATE_SHA = "e496be62c47c45d758681b8a4d3abfae16f1c96d"
OTHER_SHA = "0123456789abcdef0123456789abcdef01234567"


def load_checker_module():
    spec = importlib.util.spec_from_file_location("check_release_gate_registry", CHECKER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_committed_registry() -> dict[str, Any]:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def mutated(mutate) -> dict[str, Any]:
    registry = copy.deepcopy(load_committed_registry())
    mutate(registry)
    return registry


def errors_for(registry: dict[str, Any]) -> list[str]:
    module = load_checker_module()
    return module.validate_registry(registry, ROOT)


def clear_gate(gate: dict[str, Any], *, artifact: str = "docs/evidence/gates/README.md") -> None:
    """Turn a gate into a fully attested passing gate."""
    gate["status"] = "passed"
    gate["blockers"] = []
    gate["receipts"] = [
        {
            "receipt_id": f"{gate['id']}-receipt-001",
            "release_sha": CANDIDATE_SHA,
            "result": "pass",
            "recorded_at": "2026-07-30T12:00:00Z",
            "recorded_by": "Human/Ops",
            "artifact": artifact,
        }
    ]


def clear_all_gates(registry: dict[str, Any]) -> None:
    for gate in registry["gates"]:
        clear_gate(gate)


def run_checker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def run_product_gate(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PRODUCT_GATE), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


# --- committed state -------------------------------------------------------


def test_committed_registry_is_internally_consistent() -> None:
    assert errors_for(load_committed_registry()) == []


def test_committed_registry_declares_gate_0_through_gate_6() -> None:
    registry = load_committed_registry()
    ids = [gate["id"] for gate in registry["gates"]]

    assert ids == [f"gate-{index}" for index in range(7)]


def test_every_gate_records_status_owner_reviewer_date_evidence_and_release_sha() -> None:
    registry = load_committed_registry()
    module = load_checker_module()

    for gate in registry["gates"]:
        assert gate["status"] in module.ALLOWED_STATUSES
        assert gate["owner"].strip()
        assert gate["reviewer"].strip()
        assert gate["reviewer"] != gate["owner"]
        assert module.is_valid_date(gate["status_date"])
        assert gate["evidence"], f"{gate['id']} must cite evidence"
        assert gate["release_sha"] == registry["release"]["candidate_sha"]


def test_committed_registry_still_records_no_go_with_zero_receipts() -> None:
    registry = load_committed_registry()
    module = load_checker_module()

    assert registry["release"]["decision"] == "no-go"
    assert module.blocking_gates(registry) == [f"gate-{index}" for index in range(7)]
    for gate in registry["gates"]:
        assert gate["receipts"] == [], f"{gate['id']} must not claim a receipt yet"
        assert gate["blockers"], f"{gate['id']} must name what blocks it"


def test_committed_registry_is_bound_to_an_exact_release_sha() -> None:
    module = load_checker_module()
    candidate_sha = load_committed_registry()["release"]["candidate_sha"]

    assert module.SHA_PATTERN.fullmatch(candidate_sha)


# --- release decision consistency -----------------------------------------


def test_go_decision_without_cleared_gates_is_rejected() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        registry["release"]["decision"] = "go"
        registry["release"]["human_signoff"] = {"approver": "Human/Ops", "date": "2026-07-30"}

    assert any("not cleared" in error for error in errors_for(mutated(mutate)))


def test_go_decision_requires_human_signoff() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        clear_all_gates(registry)
        registry["release"]["decision"] = "go"

    errors = errors_for(mutated(mutate))

    assert any("human_signoff" in error for error in errors)


def test_fully_attested_go_registry_is_accepted() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        clear_all_gates(registry)
        registry["release"]["decision"] = "go"
        registry["release"]["human_signoff"] = {"approver": "Human/Ops", "date": "2026-07-30"}

    assert errors_for(mutated(mutate)) == []


def test_unknown_decision_value_is_rejected() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        registry["release"]["decision"] = "conditional-go"

    assert any("release.decision" in error for error in errors_for(mutated(mutate)))


# --- gate field integrity --------------------------------------------------


def test_missing_owner_is_rejected() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        registry["gates"][0]["owner"] = ""

    assert any("gate-0.owner" in error for error in errors_for(mutated(mutate)))


def test_reviewer_equal_to_owner_is_rejected() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        registry["gates"][2]["reviewer"] = registry["gates"][2]["owner"]

    assert any("gate-2.reviewer" in error for error in errors_for(mutated(mutate)))


def test_dropped_status_date_is_rejected() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        del registry["gates"][1]["status_date"]

    errors = errors_for(mutated(mutate))

    assert any("gate-1 missing required field: status_date" in error for error in errors)


def test_non_iso_status_date_is_rejected() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        registry["gates"][1]["status_date"] = "30/07/2026"

    assert any("gate-1.status_date" in error for error in errors_for(mutated(mutate)))


def test_unknown_gate_status_is_rejected() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        registry["gates"][3]["status"] = "probably-fine"

    assert any("gate-3.status" in error for error in errors_for(mutated(mutate)))


def test_short_or_uppercase_release_sha_is_rejected() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        registry["release"]["candidate_sha"] = CANDIDATE_SHA[:7]

    assert any("candidate_sha" in error for error in errors_for(mutated(mutate)))

    def mutate_case(registry: dict[str, Any]) -> None:
        registry["release"]["candidate_sha"] = CANDIDATE_SHA.upper()

    assert any("candidate_sha" in error for error in errors_for(mutated(mutate_case)))


def test_gate_pinned_to_a_different_sha_than_the_candidate_is_rejected() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        registry["gates"][4]["release_sha"] = OTHER_SHA

    errors = errors_for(mutated(mutate))

    assert any("does not match the release candidate" in error for error in errors)


def test_wrong_gate_count_is_rejected() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        registry["gates"] = registry["gates"][:6]

    assert any("exactly 7 gates" in error for error in errors_for(mutated(mutate)))


def test_renumbered_gate_is_rejected() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        registry["gates"][5]["id"] = "gate-7"

    errors = errors_for(mutated(mutate))

    assert any("gates[5].id must be 'gate-5'" in error for error in errors)


def test_open_gate_without_blockers_is_rejected() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        registry["gates"][0]["blockers"] = []

    errors = errors_for(mutated(mutate))

    assert any("must name at least one blocker" in error for error in errors)


def test_empty_required_checks_is_rejected() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        registry["gates"][6]["required_checks"] = []

    assert any("required_checks" in error for error in errors_for(mutated(mutate)))


# --- evidence and receipt integrity ---------------------------------------


def test_evidence_pointing_at_a_missing_path_is_rejected() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        registry["gates"][0]["evidence"].append(
            {
                "kind": "doc",
                "ref": "docs/evidence/gates/DOES_NOT_EXIST.md",
                "description": "imaginary evidence",
            }
        )

    errors = errors_for(mutated(mutate))

    assert any("references a path that does not exist" in error for error in errors)


def test_passed_gate_without_evidence_or_receipt_is_rejected() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        gate = registry["gates"][0]
        gate["status"] = "passed"
        gate["blockers"] = []
        gate["evidence"] = []
        gate["receipts"] = []

    errors = errors_for(mutated(mutate))

    assert any("requires at least one evidence entry" in error for error in errors)
    assert any("no passing receipt bound to release SHA" in error for error in errors)


def test_passed_gate_with_a_stale_receipt_is_rejected() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        gate = registry["gates"][1]
        clear_gate(gate)
        gate["receipts"][0]["release_sha"] = OTHER_SHA

    errors = errors_for(mutated(mutate))

    assert any("is stale: bound to" in error for error in errors)
    assert any("no passing receipt bound to release SHA" in error for error in errors)


def test_passed_gate_whose_receipt_artifact_is_missing_is_rejected() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        clear_gate(registry["gates"][2], artifact="docs/evidence/gates/no-such-receipt.json")

    errors = errors_for(mutated(mutate))

    assert any("artifact does not exist" in error for error in errors)


def test_passed_gate_with_a_failing_receipt_is_rejected() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        gate = registry["gates"][3]
        clear_gate(gate)
        gate["receipts"][0]["result"] = "fail"

    errors = errors_for(mutated(mutate))

    assert any("carries a failing receipt" in error for error in errors)


def test_receipt_missing_recorded_by_or_timestamp_is_rejected() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        gate = registry["gates"][4]
        clear_gate(gate)
        gate["receipts"][0]["recorded_by"] = ""
        gate["receipts"][0]["recorded_at"] = "yesterday"

    errors = errors_for(mutated(mutate))

    assert any("recorded_by" in error for error in errors)
    assert any("recorded_at" in error for error in errors)


def test_passed_gate_still_carrying_blockers_is_rejected() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        gate = registry["gates"][5]
        clear_gate(gate)
        gate["blockers"] = ["live staging proof still missing"]

    errors = errors_for(mutated(mutate))

    assert any("must not carry open blockers" in error for error in errors)


def test_not_applicable_gate_requires_a_justification() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        gate = registry["gates"][3]
        gate["status"] = "not-applicable"
        gate["blockers"] = []

    errors = errors_for(mutated(mutate))

    assert any("requires a non-empty justification" in error for error in errors)


def test_passed_with_deviation_requires_an_approved_deviation() -> None:
    def mutate(registry: dict[str, Any]) -> None:
        gate = registry["gates"][6]
        clear_gate(gate)
        gate["status"] = "passed-with-deviation"

    errors = errors_for(mutated(mutate))

    assert any("requires a deviation object" in error for error in errors)

    def mutate_partial(registry: dict[str, Any]) -> None:
        gate = registry["gates"][6]
        clear_gate(gate)
        gate["status"] = "passed-with-deviation"
        gate["deviation"] = {"description": "watch window shortened", "approver": "Human/Ops"}

    errors = errors_for(mutated(mutate_partial))

    assert any("deviation missing required field: review_by" in error for error in errors)


# --- loader and CLI --------------------------------------------------------


def test_missing_registry_file_fails_closed(tmp_path: Path) -> None:
    module = load_checker_module()
    missing = tmp_path / "RELEASE_GATE_REGISTRY.json"

    assert module.main(["--registry", str(missing)]) == 1


def test_malformed_registry_json_fails_closed(tmp_path: Path) -> None:
    module = load_checker_module()
    broken = tmp_path / "RELEASE_GATE_REGISTRY.json"
    broken.write_text("{ not json", encoding="utf-8")

    assert module.main(["--registry", str(broken)]) == 1


def test_registry_that_is_a_json_list_fails_closed(tmp_path: Path) -> None:
    module = load_checker_module()
    wrong_shape = tmp_path / "RELEASE_GATE_REGISTRY.json"
    wrong_shape.write_text("[]", encoding="utf-8")

    assert module.main(["--registry", str(wrong_shape)]) == 1


def test_cli_accepts_the_committed_no_go_registry() -> None:
    result = run_checker()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "RELEASE STATE: NO-GO" in result.stdout


def test_cli_require_go_blocks_the_current_release() -> None:
    result = run_checker("--require-go")

    assert result.returncode == 1
    assert "NO-GO" in result.stdout


def test_cli_expected_sha_mismatch_fails_closed() -> None:
    result = run_checker("--expected-sha", OTHER_SHA)

    assert result.returncode == 1
    assert "--expected-sha" in result.stdout


def test_cli_expected_sha_match_passes() -> None:
    result = run_checker("--expected-sha", CANDIDATE_SHA)

    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_json_report_lists_blocking_gates() -> None:
    result = run_checker("--json")
    report = json.loads(result.stdout)

    assert result.returncode == 0, result.stdout + result.stderr
    assert report["release_state"] == "NO-GO"
    assert report["cleared_gates"] == []
    assert report["blocking_gates"] == [f"gate-{index}" for index in range(7)]
    assert report["candidate_sha"] == CANDIDATE_SHA


def test_cli_reports_integrity_errors_for_a_tampered_registry(tmp_path: Path) -> None:
    registry = mutated(lambda reg: reg["gates"][0].pop("owner"))
    tampered = tmp_path / "RELEASE_GATE_REGISTRY.json"
    tampered.write_text(json.dumps(registry), encoding="utf-8")

    result = run_checker("--registry", str(tampered))

    assert result.returncode == 1
    assert "gate-0 missing required field: owner" in result.stdout


# --- wiring ----------------------------------------------------------------


def test_registry_is_documented_and_wired_into_the_release_gate() -> None:
    readme = REGISTRY_README.read_text(encoding="utf-8")
    release_gate = (ROOT / "scripts/e2e/check_product_release_gate.py").read_text(
        encoding="utf-8"
    )
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    for token in (
        "docs/evidence/gates/RELEASE_GATE_REGISTRY.json",
        "scripts/e2e/check_release_gate_registry.py",
    ):
        assert token in readme
        assert token in release_gate

    assert "check_release_gate_registry.py" in makefile


def test_dev_merge_gate_accepts_valid_no_go_but_release_gate_fails_closed() -> None:
    dev_merge = run_product_gate("--dev-merge")
    assert dev_merge.returncode == 0, dev_merge.stdout + dev_merge.stderr
    assert "dev merge gate static checks passed" in dev_merge.stdout

    production_release = run_product_gate("--require-go")
    assert production_release.returncode == 1
    assert "NO-GO" in production_release.stdout


def test_ci_and_promotion_workflows_use_separate_gate_modes() -> None:
    ci_workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    promotion_workflow = (ROOT / ".github/workflows/promote-dev-to-main.yml").read_text(
        encoding="utf-8"
    )
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "run: make product-e2e-gate" in ci_workflow
    assert "run: make product-release-gate" not in ci_workflow
    assert "run: make product-release-gate" in promotion_workflow
    assert "github.event.workflow_run.head_sha" in promotion_workflow
    assert "check_product_release_gate.py --dev-merge" in makefile
    assert "check_product_release_gate.py --require-go" in makefile


def test_registry_does_not_report_archived_done_tasks_as_open() -> None:
    blockers = "\n".join(
        blocker
        for gate in load_committed_registry()["gates"]
        for blocker in gate["blockers"]
    )
    for task_id in (
        "ODP-PLAN-SOLVER-RUNTIME-COMPAT-001",
        "ODP-PLAN-HEATZONE-OUTCOME-001",
        "ODP-PLAN-NETPLAN-ACCEPTANCE-001",
        "ODP-PLAN-OSS-LICENSE-GATE-001",
        "ODP-PLAN-DEFERRED-OSS-ADR-001",
        "ODP-PLAN-ACCEPTANCE-REAL-EXEC-001",
        "ODP-PLAN-CANONICAL-SHELL-LIVE-001",
    ):
        assert f"{task_id} is open" not in blockers
    assert "archived done" in blockers
    assert load_committed_registry()["release"]["decision"] == "no-go"

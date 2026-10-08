from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "delivery_toolchain/e2e/check_product_grade_ci_gates.py"
SPEC = importlib.util.spec_from_file_location("product_grade_ci_gates", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)


def valid_remote_visual_payload() -> dict[str, object]:
    release_sha = "a" * 40
    return {
        "status": "approved",
        "authenticated": True,
        "canonical_html_sha256": GATE.EXPECTED_HTML_SHA,
        "release_sha": release_sha,
        "web_release_sha": release_sha,
        "api_release_sha": release_sha,
        "production_fixture_count": 0,
        "viewports": sorted(GATE.REQUIRED_VISUAL_VIEWPORTS),
        "routes": sorted(GATE.REQUIRED_VISUAL_ROUTES),
    }


def test_remote_visual_routes_exclude_retired_expansion_page() -> None:
    assert "/w/expansion/listings" not in GATE.REQUIRED_VISUAL_ROUTES
    assert "/operator?ws=network&tab=listings" in GATE.REQUIRED_VISUAL_ROUTES


def test_remote_visual_approval_requires_exact_live_release_evidence(tmp_path: Path) -> None:
    approval = tmp_path / "approval.json"
    approval.write_text(
        json.dumps(valid_remote_visual_payload()),
        encoding="utf-8",
    )

    assert GATE.validate_remote_visual_approval(approval) == []


def test_remote_visual_approval_rejects_local_or_incomplete_evidence(
    tmp_path: Path,
) -> None:
    payload = valid_remote_visual_payload()
    payload.update(
        {
            "authenticated": False,
            "release_sha": "a13a1075",
            "api_release_sha": "different",
            "production_fixture_count": 1,
            "viewports": [1440],
            "routes": ["/operator"],
        }
    )
    approval = tmp_path / "approval.json"
    approval.write_text(json.dumps(payload), encoding="utf-8")

    errors = GATE.validate_remote_visual_approval(approval)

    assert any("authenticated" in error for error in errors)
    assert any("40-character SHA" in error for error in errors)
    assert any("api_release_sha" in error for error in errors)
    assert any("zero production fixtures" in error for error in errors)
    assert any("required viewport" in error for error in errors)
    assert any("required route" in error for error in errors)


def _go_record(status: str, final: str) -> str:
    return (
        "# Product Release Go/No-Go\n\n"
        f"Decision status: {status}  \n"
        "Decision owner: Human/Ops  \n\n"
        "| Item | Requirement | Status |\n|---|---|---|\n"
        f"| Final decision recorded | Human/Ops writes approved / approved-with-actions / rejected | {final} |\n"
    )


def test_release_go_requires_structured_unconditional_human_approval() -> None:
    authorized, reason = GATE.evaluate_release_go_decision(_go_record("go", "approved"))
    assert authorized, reason
    authorized, _ = GATE.evaluate_release_go_decision(
        _go_record("GO for production", "approved-with-actions")
    )
    assert authorized


def test_release_go_rejects_go_substrings_and_non_final_decisions() -> None:
    rejected = [
        _go_record("NO-GO", "rejected"),
        _go_record("no go", "approved"),
        _go_record("conditional go for deterministic product E2E", "pending-human"),
        _go_record("go", "pending-human"),
        _go_record("blocked pending sign-off", "approved"),
        _go_record("go pending production authorization", "approved-with-actions"),
        _go_record("go, subject to Human/Ops sign-off", "approved"),
        _go_record("approved except staging", "approved"),
        "# Product Release Go/No-Go\n\nThis document mentions go many times but records nothing.\n",
    ]
    for text in rejected:
        authorized, reason = GATE.evaluate_release_go_decision(text)
        assert not authorized, (text, reason)


def test_current_release_record_does_not_authorize_release() -> None:
    authorized, reason = GATE.evaluate_release_go_decision(
        GATE.RELEASE_GO_PATH.read_text(encoding="utf-8")
    )
    assert not authorized
    assert "conditional" in reason

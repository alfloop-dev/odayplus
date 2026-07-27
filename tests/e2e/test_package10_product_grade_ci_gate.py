from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts/e2e/check_product_grade_ci_gates.py"
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

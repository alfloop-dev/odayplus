import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.security.test_pr_merge_eligibility import temp_env


def import_module_from_path(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_auto_merge_green_prs_dry_run(temp_env, monkeypatch) -> None:
    # Set up mocks for gh command runs in auto_merge_green_prs
    mock_gh_calls = []

    def mock_gh(*args, **kwargs):
        mock_gh_calls.append(args)
        if args[0] == "pr" and args[1] == "list":
            # Mock open PR list
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 82,
                            "headRefName": "task/ODP-OC-R5-012",
                            "baseRefName": "dev",
                            "isDraft": False,
                            "mergeable": "MERGEABLE",
                        },
                        {
                            "number": 83,
                            "headRefName": "task/ODP-OC-R5-011",
                            "baseRefName": "dev",
                            "isDraft": True,
                            "mergeable": "MERGEABLE",
                        },
                    ]
                ),
                "",
            )
        elif args[0] == "api" and "reviews" in args[3]:
            # Mock reviews response (approved for 82, empty for 83)
            if "82" in args[3]:
                return (
                    0,
                    json.dumps([{"user": {"login": "codex-bot"}, "state": "APPROVED"}]),
                    "",
                )
            return (0, "[]", "")
        elif args[0] == "pr" and args[1] == "view":
            # Mock check rollup response
            return (
                0,
                json.dumps(
                    {
                        "statusCheckRollup": [
                            {"name": "orchestrator", "status": "COMPLETED", "conclusion": "SUCCESS"},
                            {"name": "product", "status": "COMPLETED", "conclusion": "SUCCESS"},
                            {"name": "product-e2e-gate", "status": "COMPLETED", "conclusion": "SUCCESS"},
                            {"name": "task-review-gate", "status": "COMPLETED", "conclusion": "SUCCESS"},
                        ]
                    }
                ),
                "",
            )
        return (0, "", "")

    script_path = Path(__file__).resolve().parents[2] / ".orchestrator" / "auto_merge_green_prs.py"
    auto_merge_mod = import_module_from_path("auto_merge_green_prs", script_path)

    # Patch modules and run
    monkeypatch.setattr(auto_merge_mod, "_gh", mock_gh)
    monkeypatch.setattr(auto_merge_mod, "ROOT", temp_env["status"].parent)

    # Use sys.argv patch to specify dry-run
    with patch("sys.argv", ["auto_merge_green_prs.py", "--dry-run"]):
        exit_code = auto_merge_mod.main()

    assert exit_code == 0
    # Verify that it decided to merge 82 (dry-run) but not 83 (since 83 is task/ODP-OC-R5-011 which is status 'review', not 'review_approved')
    # Let's check what was logged or what _gh calls happened.
    # It shouldn't call "pr merge" because it's dry-run.
    merge_calls = [c for c in mock_gh_calls if "merge" in c]
    assert len(merge_calls) == 0

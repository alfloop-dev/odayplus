import importlib.util
import json
import sys
from pathlib import Path

# Load module
script_path = Path(__file__).resolve().parents[2] / ".orchestrator" / "auto_merge_green_prs.py"
spec = importlib.util.spec_from_file_location("auto_merge_green_prs", script_path)
auto_merge_mod = importlib.util.module_from_spec(spec)
sys.modules["auto_merge_green_prs"] = auto_merge_mod
spec.loader.exec_module(auto_merge_mod)


def test_auto_merge_green_prs_dry_run(temp_env, monkeypatch) -> None:
    mock_gh_calls = []

    def mock_gh(*args, **kwargs):
        mock_gh_calls.append(args)
        if args[0] == "pr" and args[1] == "list":
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
                        }
                    ]
                ),
                "",
            )
        return (0, "", "")

    monkeypatch.setattr(auto_merge_mod, "_gh", mock_gh)
    monkeypatch.setattr(auto_merge_mod, "ROOT", temp_env["status"].parent)

    # Eligible check returns True
    def mock_eligible(*args, **kwargs):
        return True, []

    exit_code = auto_merge_mod.main(argv=["--dry-run"], check_eligibility_func=mock_eligible)
    assert exit_code == 0

    # In dry-run, no pr merge should be called
    merge_calls = [c for c in mock_gh_calls if "merge" in c]
    assert len(merge_calls) == 0


def test_auto_merge_green_prs_positive_not_draft(temp_env, monkeypatch) -> None:
    mock_gh_calls = []

    def mock_gh(*args, **kwargs):
        mock_gh_calls.append(args)
        if args[0] == "pr" and args[1] == "list":
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
                        }
                    ]
                ),
                "",
            )
        return (0, "", "")

    monkeypatch.setattr(auto_merge_mod, "_gh", mock_gh)
    monkeypatch.setattr(auto_merge_mod, "ROOT", temp_env["status"].parent)

    def mock_eligible(*args, **kwargs):
        return True, []

    exit_code = auto_merge_mod.main(argv=[], check_eligibility_func=mock_eligible)
    assert exit_code == 0

    # pr ready should NOT be called (isDraft=False)
    ready_calls = [c for c in mock_gh_calls if "ready" in c]
    assert len(ready_calls) == 0

    # pr merge should be called
    merge_calls = [c for c in mock_gh_calls if "merge" in c and "list" not in c]
    assert len(merge_calls) == 1
    assert merge_calls[0] == ("pr", "merge", "82", "--merge", "--repo", "alfloop-dev/odayplus")


def test_auto_merge_green_prs_positive_draft(temp_env, monkeypatch) -> None:
    mock_gh_calls = []

    def mock_gh(*args, **kwargs):
        mock_gh_calls.append(args)
        if args[0] == "pr" and args[1] == "list":
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 83,
                            "headRefName": "task/ODP-OC-R5-012",
                            "baseRefName": "dev",
                            "isDraft": True,
                            "mergeable": "MERGEABLE",
                        }
                    ]
                ),
                "",
            )
        return (0, "", "")

    monkeypatch.setattr(auto_merge_mod, "_gh", mock_gh)
    monkeypatch.setattr(auto_merge_mod, "ROOT", temp_env["status"].parent)

    def mock_eligible(*args, **kwargs):
        return True, []

    exit_code = auto_merge_mod.main(argv=[], check_eligibility_func=mock_eligible)
    assert exit_code == 0

    # pr ready SHOULD be called (isDraft=True)
    ready_calls = [c for c in mock_gh_calls if "ready" in c]
    assert len(ready_calls) == 1
    assert ready_calls[0] == ("pr", "ready", "83", "--repo", "alfloop-dev/odayplus")

    # pr merge should be called
    merge_calls = [c for c in mock_gh_calls if "merge" in c and "list" not in c]
    assert len(merge_calls) == 1
    assert merge_calls[0] == ("pr", "merge", "83", "--merge", "--repo", "alfloop-dev/odayplus")


def test_auto_merge_green_prs_negative_not_eligible(temp_env, monkeypatch) -> None:
    mock_gh_calls = []

    def mock_gh(*args, **kwargs):
        mock_gh_calls.append(args)
        if args[0] == "pr" and args[1] == "list":
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
                        }
                    ]
                ),
                "",
            )
        return (0, "", "")

    monkeypatch.setattr(auto_merge_mod, "_gh", mock_gh)
    monkeypatch.setattr(auto_merge_mod, "ROOT", temp_env["status"].parent)

    def mock_eligible(*args, **kwargs):
        return False, ["Review status is not review_approved"]

    exit_code = auto_merge_mod.main(argv=[], check_eligibility_func=mock_eligible)
    assert exit_code == 0

    # Neither ready nor merge should be called
    ready_calls = [c for c in mock_gh_calls if "ready" in c]
    assert len(ready_calls) == 0
    merge_calls = [c for c in mock_gh_calls if "merge" in c and "list" not in c]
    assert len(merge_calls) == 0


def test_auto_merge_green_prs_negative_draft_not_eligible(temp_env, monkeypatch) -> None:
    mock_gh_calls = []

    def mock_gh(*args, **kwargs):
        mock_gh_calls.append(args)
        if args[0] == "pr" and args[1] == "list":
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 83,
                            "headRefName": "task/ODP-OC-R5-012",
                            "baseRefName": "dev",
                            "isDraft": True,
                            "mergeable": "MERGEABLE",
                        }
                    ]
                ),
                "",
            )
        return (0, "", "")

    monkeypatch.setattr(auto_merge_mod, "_gh", mock_gh)
    monkeypatch.setattr(auto_merge_mod, "ROOT", temp_env["status"].parent)

    def mock_eligible(*args, **kwargs):
        return False, ["Required check failed"]

    exit_code = auto_merge_mod.main(argv=[], check_eligibility_func=mock_eligible)
    assert exit_code == 0

    # Drafts are never promoted unless fully eligible - verify no ready call!
    ready_calls = [c for c in mock_gh_calls if "ready" in c]
    assert len(ready_calls) == 0
    merge_calls = [c for c in mock_gh_calls if "merge" in c and "list" not in c]
    assert len(merge_calls) == 0


def test_auto_merge_green_prs_exception_handled(temp_env, monkeypatch) -> None:
    mock_gh_calls = []

    def mock_gh(*args, **kwargs):
        mock_gh_calls.append(args)
        if args[0] == "pr" and args[1] == "list":
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 83,
                            "headRefName": "task/ODP-OC-R5-012",
                            "baseRefName": "dev",
                            "isDraft": True,
                            "mergeable": "MERGEABLE",
                        }
                    ]
                ),
                "",
            )
        return (0, "", "")

    monkeypatch.setattr(auto_merge_mod, "_gh", mock_gh)
    monkeypatch.setattr(auto_merge_mod, "ROOT", temp_env["status"].parent)

    def mock_eligible(*args, **kwargs):
        raise ValueError("Simulated validation crash")

    exit_code = auto_merge_mod.main(argv=[], check_eligibility_func=mock_eligible)
    assert exit_code == 0

    # Fail closed on exceptions - verify no ready and no merge call
    ready_calls = [c for c in mock_gh_calls if "ready" in c]
    assert len(ready_calls) == 0
    merge_calls = [c for c in mock_gh_calls if "merge" in c and "list" not in c]
    assert len(merge_calls) == 0


def test_auto_merge_green_prs_draft_ready_failure(temp_env, monkeypatch) -> None:
    mock_gh_calls = []

    def mock_gh(*args, **kwargs):
        mock_gh_calls.append(args)
        if args[0] == "pr" and args[1] == "list":
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 83,
                            "headRefName": "task/ODP-OC-R5-012",
                            "baseRefName": "dev",
                            "isDraft": True,
                            "mergeable": "MERGEABLE",
                        }
                    ]
                ),
                "",
            )
        if args[0] == "pr" and args[1] == "ready":
            return (1, "", "Simulated ready command error")
        return (0, "", "")

    monkeypatch.setattr(auto_merge_mod, "_gh", mock_gh)
    monkeypatch.setattr(auto_merge_mod, "ROOT", temp_env["status"].parent)

    def mock_eligible(*args, **kwargs):
        return True, []

    exit_code = auto_merge_mod.main(argv=[], check_eligibility_func=mock_eligible)
    assert exit_code == 0

    # pr ready WAS called
    ready_calls = [c for c in mock_gh_calls if "ready" in c]
    assert len(ready_calls) == 1

    # pr merge was NOT called
    merge_calls = [c for c in mock_gh_calls if "merge" in c and "list" not in c]
    assert len(merge_calls) == 0


def test_auto_merge_green_prs_list_failure(temp_env, monkeypatch) -> None:
    def mock_gh(*args, **kwargs):
        if args[0] == "pr" and args[1] == "list":
            return (2, "", "API list failed")
        return (0, "", "")

    monkeypatch.setattr(auto_merge_mod, "_gh", mock_gh)
    monkeypatch.setattr(auto_merge_mod, "ROOT", temp_env["status"].parent)

    exit_code = auto_merge_mod.main(argv=[])
    assert exit_code != 0


def test_auto_merge_green_prs_list_json_invalid(temp_env, monkeypatch) -> None:
    def mock_gh(*args, **kwargs):
        if args[0] == "pr" and args[1] == "list":
            return (0, "invalid-json-output", "")
        return (0, "", "")

    monkeypatch.setattr(auto_merge_mod, "_gh", mock_gh)
    monkeypatch.setattr(auto_merge_mod, "ROOT", temp_env["status"].parent)

    exit_code = auto_merge_mod.main(argv=[])
    assert exit_code != 0

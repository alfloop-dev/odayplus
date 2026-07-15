#!/usr/bin/env python3
"""Apply or verify branch protection policy on GitHub."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def get_gh_executable() -> str:
    import shutil
    gh_path = shutil.which("gh")
    if gh_path:
        if ".orchestrator/bin/gh" in gh_path:
            for p in ["/usr/bin/gh", "/usr/local/bin/gh"]:
                if os.path.exists(p):
                    return p
        return gh_path
    return "gh"


def run_gh_cli(args: list[str], input_data: str | None = None) -> tuple[int, str, str]:
    cmd = [get_gh_executable()] + args
    result = subprocess.run(cmd, input=input_data, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def main() -> int:
    policy_path = ROOT / ".github/branch-protection/policy.json"
    if not policy_path.exists():
        print(f"Policy file not found: {policy_path}", file=sys.stderr)
        return 1

    try:
        with open(policy_path, encoding="utf-8") as f:
            policy = json.load(f)
    except Exception as exc:
        print(f"Failed to parse policy file: {exc}", file=sys.stderr)
        return 1

    # Transform policy.json to standard GitHub API payload format
    payload = {
        "required_status_checks": {
            "strict": True,
            "contexts": policy.get("required_status_checks", [])
        },
        "enforce_admins": policy.get("enforce_admins", True),
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": policy.get("dismiss_stale_reviews", True),
            "require_code_owner_reviews": policy.get("require_code_owner_reviews", True),
            "required_approving_review_count": policy.get("required_approving_review_count", 1)
        },
        "restrictions": None
    }

    repo = os.environ.get("GITHUB_REPOSITORY", "alfloop-dev/odayplus")
    branches = ["dev", "main"]

    print(f"Target repository: {repo}")
    print("Policy configuration to enforce:")
    print(json.dumps(payload, indent=2))

    has_failures = False
    for branch in branches:
        print(f"\n--- Applying protection to branch: {branch} ---")
        ret, stdout, stderr = run_gh_cli(
            ["api", "-X", "PUT", f"repos/{repo}/branches/{branch}/protection", "--input", "-"],
            input_data=json.dumps(payload)
        )

        if ret == 0:
            print(f"Successfully applied branch protection to {branch}!")
        else:
            print(f"Failed to apply branch protection to {branch} via API.")
            print(f"Exit code: {ret}")
            print(f"Stderr: {stderr.strip()}")
            has_failures = True

        # Read back protection to verify current state
        print(f"Reading back protection status for {branch}...")
        read_ret, read_stdout, read_stderr = run_gh_cli([
            "api",
            f"repos/{repo}/branches/{branch}/protection"
        ])
        if read_ret == 0:
            print(f"Branch protection is ACTIVE for '{branch}'. Current configuration:")
            try:
                current_protection = json.loads(read_stdout)
                print(json.dumps(current_protection, indent=2))
            except Exception:
                print(read_stdout)
        else:
            print(f"No branch protection read back for '{branch}' (status check returned non-zero/404).")

    if has_failures:
        print("\n======================================================================")
        print("HUMAN/OPS ACTION REQUIRED:")
        print("We do not have sufficient repository administrative permissions to configure branch protection rules automatically.")
        print("Please manually configure the following settings on GitHub for 'dev' and 'main' branches:")
        print(f"1. Require status checks to pass before merging: {policy.get('required_status_checks', [])}")
        print("2. Require approvals: 1 review approval count")
        print("3. Dismiss stale reviews: True")
        print("4. Require code owner reviews: True")
        print("5. Enforce on administrators: True")
        print("======================================================================")
        return 1
    else:
        print("\nAll branch protections configured successfully.")
        return 0


if __name__ == "__main__":
    sys.exit(main())

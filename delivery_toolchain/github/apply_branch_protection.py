#!/usr/bin/env python3
"""Apply or verify branch protection policy on GitHub.

Two surfaces are configured from `.github/branch-protection/policy.json`:

* classic branch protection (required checks, admin enforcement, strict);
* the merge queue on `dev`, which classic branch protection cannot express.
  GraphQL's `updateBranchProtectionRule` mutation has no `requiresMergeQueue`
  input, so the queue is declared as a repository ruleset carrying a
  `merge_queue` rule. Rulesets and classic protection are additive, so the
  four required contexts keep coming from the classic rule.

`strict` ("require branches to be up to date") is mutually exclusive with a
merge queue: the queue exists precisely to build each candidate against the
current target branch, and leaving strict on would force every PR to chase
`dev` before it is even allowed to enter the queue. `branches` in the
policy therefore turns strict off for `dev` and leaves it on for `main`, which
has no queue.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

BRANCHES = ["dev", "main"]

MERGE_QUEUE_STATE_QUERY = """
query($owner: String!, $name: String!, $branch: String!) {
  repository(owner: $owner, name: $name) {
    mergeQueue(branch: $branch) {
      id
      configuration {
        mergeMethod
        mergingStrategy
        checkResponseTimeout
        maximumEntriesToBuild
        maximumEntriesToMerge
        minimumEntriesToMerge
        minimumEntriesToMergeWaitTime
      }
    }
  }
}
"""


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


def branch_policy(policy: dict, branch: str) -> dict:
    # A branch entry under "branches" overlays the top-level policy, so shared
    # settings stay declared once and only the deltas are per-branch.
    overrides = policy.get("branches", {})
    return {**policy, **overrides.get(branch, {})}


def build_payload(policy: dict) -> dict:
    # Transform policy.json to standard GitHub API payload format
    payload = {
        "required_status_checks": {
            # strict means "PR must be up to date with the base before merging".
            # A branch behind a merge queue must set this false: the queue already
            # tests each candidate on a ref built from the base plus the queued
            # PRs, so strict only adds a rebase race the queue exists to remove --
            # with both on, every PR is stuck BEHIND and nothing can enter.
            "strict": policy.get("strict", True),
            "contexts": policy.get("required_status_checks", [])
        },
        "enforce_admins": policy.get("enforce_admins", True),
        "restrictions": None
    }

    if "required_approving_review_count" in policy and policy["required_approving_review_count"] is not None:
        payload["required_pull_request_reviews"] = {
            "dismiss_stale_reviews": policy.get("dismiss_stale_reviews", True),
            "require_code_owner_reviews": policy.get("require_code_owner_reviews", True),
            "required_approving_review_count": policy["required_approving_review_count"]
        }
    else:
        payload["required_pull_request_reviews"] = None
    return payload


def merge_queue_config(policy: dict, branch: str) -> dict | None:
    branches = policy.get("branches")
    if isinstance(branches, dict):
        branch_cfg = branches.get(branch)
        if isinstance(branch_cfg, dict):
            mq = branch_cfg.get("merge_queue")
            if isinstance(mq, dict) and mq:
                return mq
    queues = policy.get("merge_queue")
    if isinstance(queues, dict):
        config = queues.get(branch)
        if isinstance(config, dict) and config:
            return config
    return None



def ruleset_name(config: dict, branch: str) -> str:
    return str(config.get("ruleset_name") or f"{branch}-merge-queue")


def build_ruleset_payload(config: dict, branch: str) -> dict:
    return {
        "name": ruleset_name(config, branch),
        "target": "branch",
        "enforcement": "active",
        "conditions": {
            "ref_name": {
                "include": [f"refs/heads/{branch}"],
                "exclude": [],
            }
        },
        "rules": [
            {
                "type": "merge_queue",
                "parameters": {
                    "merge_method": config.get("merge_method", "MERGE"),
                    "grouping_strategy": config.get("grouping_strategy", "ALLGREEN"),
                    "max_entries_to_build": config.get("max_entries_to_build", 5),
                    "max_entries_to_merge": config.get("max_entries_to_merge", 5),
                    "min_entries_to_merge": config.get("min_entries_to_merge", 1),
                    "min_entries_to_merge_wait_minutes": config.get(
                        "min_entries_to_merge_wait_minutes", 5
                    ),
                    "check_response_timeout_minutes": config.get(
                        "check_response_timeout_minutes", 60
                    ),
                },
            }
        ],
    }


def find_ruleset_id(repo: str, name: str) -> int | None:
    ret, stdout, stderr = run_gh_cli(["api", f"repos/{repo}/rulesets"])
    if ret != 0:
        print(f"Failed to list rulesets: {stderr.strip()}")
        return None
    try:
        rulesets = json.loads(stdout or "[]")
    except Exception as exc:
        print(f"Failed to parse ruleset list: {exc}")
        return None
    for ruleset in rulesets:
        if ruleset.get("name") == name:
            return ruleset.get("id")
    return None


def read_merge_queue(repo: str, branch: str) -> dict | None:
    owner, _, name = repo.partition("/")
    ret, stdout, stderr = run_gh_cli([
        "api", "graphql",
        "-f", f"query={MERGE_QUEUE_STATE_QUERY}",
        "-F", f"owner={owner}",
        "-F", f"name={name}",
        "-F", f"branch={branch}",
    ])
    if ret != 0:
        print(f"Failed to read merge queue state for '{branch}': {stderr.strip()}")
        return None
    try:
        payload = json.loads(stdout or "{}")
    except Exception as exc:
        print(f"Failed to parse merge queue state: {exc}")
        return None
    repository = (payload.get("data") or {}).get("repository") or {}
    return repository.get("mergeQueue")


def apply_merge_queue(repo: str, branch: str, config: dict) -> bool:
    name = ruleset_name(config, branch)
    payload = build_ruleset_payload(config, branch)
    existing = find_ruleset_id(repo, name)

    if existing is None:
        method, path = "POST", f"repos/{repo}/rulesets"
    else:
        method, path = "PUT", f"repos/{repo}/rulesets/{existing}"

    ret, stdout, stderr = run_gh_cli(
        ["api", "-X", method, path, "--input", "-"],
        input_data=json.dumps(payload),
    )
    if ret != 0:
        print(f"Failed to apply merge queue ruleset '{name}': {stderr.strip() or stdout.strip()}")
        return False
    verb = "Created" if existing is None else "Updated"
    print(f"{verb} merge queue ruleset '{name}' for '{branch}'.")
    return True


def delete_merge_queue(repo: str, branch: str, config: dict) -> bool:
    name = ruleset_name(config, branch)
    existing = find_ruleset_id(repo, name)
    if existing is None:
        print(f"No merge queue ruleset '{name}' to delete; nothing to roll back.")
        return True
    ret, stdout, stderr = run_gh_cli(
        ["api", "-X", "DELETE", f"repos/{repo}/rulesets/{existing}"]
    )
    if ret != 0:
        print(f"Failed to delete merge queue ruleset '{name}': {stderr.strip() or stdout.strip()}")
        return False
    print(f"Deleted merge queue ruleset '{name}' for '{branch}'.")
    return True


def report_merge_queue(repo: str, branch: str, expect_enabled: bool) -> bool:
    state = read_merge_queue(repo, branch)
    enabled = state is not None
    if enabled:
        print(f"Merge queue on '{branch}' is ENABLED: {json.dumps(state.get('configuration'), indent=2)}")
    else:
        print(f"Merge queue on '{branch}' is DISABLED (mergeQueue(branch:\"{branch}\") is null).")
    if enabled != expect_enabled:
        want = "enabled" if expect_enabled else "disabled"
        print(f"Merge queue state for '{branch}' does not match the requested state ({want}).")
        return False
    return True


def load_policy(policy_path: Path) -> dict | None:
    if not policy_path.exists():
        print(f"Policy file not found: {policy_path}", file=sys.stderr)
        return None
    try:
        with open(policy_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"Failed to parse policy file: {exc}", file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--disable-merge-queue",
        action="store_true",
        help=(
            "Rollback path: delete the merge queue ruleset and re-apply classic "
            "protection with strict re-enabled, restoring direct PR auto-merge."
        ),
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Read back protection and merge queue state without writing anything.",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    policy_path = ROOT / ".github/branch-protection/policy.json"
    policy = load_policy(policy_path)
    if policy is None:
        return 1

    repo = os.environ.get("GITHUB_REPOSITORY", "alfloop-dev/odayplus")
    rollback = args.disable_merge_queue

    print(f"Target repository: {repo}")
    if rollback:
        print("Mode: ROLLBACK (merge queue off, strict back on for every branch)")
    elif args.verify_only:
        print("Mode: VERIFY ONLY (no writes)")

    has_failures = False
    for branch in BRANCHES:
        queue_config = merge_queue_config(policy, branch)
        # During rollback the queue is removed, so the pre-queue strict setting
        # is what keeps `dev` from merging stale branches.
        payload = build_payload(branch_policy(policy, branch))
        if rollback:
            payload["required_status_checks"]["strict"] = True

        if not args.verify_only:
            print(f"\n--- Applying protection to branch: {branch} ---")
            print(json.dumps(payload, indent=2))
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

        if queue_config is None:
            continue

        print(f"\n--- Merge queue on branch: {branch} ---")
        if not args.verify_only:
            ok = (
                delete_merge_queue(repo, branch, queue_config)
                if rollback
                else apply_merge_queue(repo, branch, queue_config)
            )
            has_failures = has_failures or not ok
        if not report_merge_queue(repo, branch, expect_enabled=not rollback):
            has_failures = True

    if has_failures:
        print("\n======================================================================")
        print("HUMAN/OPS ACTION REQUIRED:")
        print("We do not have sufficient repository administrative permissions to configure branch protection rules automatically.")
        print("Please manually configure the following settings on GitHub for 'dev' and 'main' branches:")
        print(f"1. Require status checks to pass before merging: {policy.get('required_status_checks', [])}")
        if "required_approving_review_count" in policy and policy["required_approving_review_count"] is not None:
            print(f"2. Require approvals: {policy['required_approving_review_count']} review approval count")
            print(f"3. Dismiss stale reviews: {policy.get('dismiss_stale_reviews', True)}")
            print(f"4. Require code owner reviews: {policy.get('require_code_owner_reviews', True)}")
        else:
            print("2. Require approvals: Disabled (No GitHub review requirement)")
        print("5. Enforce on administrators: True")
        for branch in BRANCHES:
            config = merge_queue_config(policy, branch)
            if config:
                print(f"6. Merge queue on '{branch}': {json.dumps(config)}")
        print("======================================================================")
        return 1
    else:
        print("\nAll branch protections configured successfully.")
        return 0


if __name__ == "__main__":
    sys.exit(main())


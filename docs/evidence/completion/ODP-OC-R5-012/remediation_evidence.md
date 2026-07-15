# ODP-OC-R5-012: Merge Gate Enforcement Remediation Evidence

This document records the control-plane audit, policy configuration, implementation details, automated API enforcement logs, and test evidence for enforcing reviewer-approved and all-green product merge gates.

---

## 1. Control-Plane Path Inventory (PR 297, 298, and 300)

We conducted an audit of the control-plane path that permitted PRs #297, #298, and #300 to merge prematurely:

1. **Auto-Merge Command Default**: The coordination sequence utilized the GitHub CLI command:
   ```bash
   gh pr merge --auto --merge
   ```
   This instructed GitHub to auto-merge the PR immediately upon satisfaction of default/basic branch protection rules.
2. **Lack of Integration Gates**:
   - The repository lacked a pre-merge check to verify the canonical task status in [ai-status.json](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-oc-r5-012/ai-status.json).
   - The repository lacked branch protection rules requiring explicit approvals from the **assigned reviewer** (e.g. `Codex`), allowing general reviews (or lack thereof) to satisfy the merge condition.
   - The merge eligibility script `scripts/check_pr_merge_eligibility.py` was inert because it was never wired as a required check in the GitHub Actions workflows.
3. **Bypass & Path Resolution Flaws**:
   - The script had a bug where `ROOT` was resolved to `parents[2]`, which pointed *above* the repository root, breaking file resolution paths in the CI runner.
   - Non-task branches returned `eligible=True`, creating a bypass vector where non-task branches skipped the merge gate entirely.
   - Reviewer handles in `.orchestrator/config.example.json` were placeholders (`"your-github-handle"`).

---

## 2. Policy & Implementation Details

To close these gaps, we implemented a multi-layered, fail-closed validation strategy:

1. **Policy Configuration**:
   Defined a policy configuration file at [.github/branch-protection/policy.json](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-oc-r5-012/.github/branch-protection/policy.json). It mandates:
   - `required_status_checks`: `["orchestrator", "product", "product-e2e-gate", "check-merge-eligibility"]`.
   - `enforce_admins`: `true` (preventing administrators and privileged paths from bypassing gates).
   - `required_approving_review_count`: `1`.

2. **PR Merge Eligibility Script**:
   Modified [check_pr_merge_eligibility.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-oc-r5-012/scripts/check_pr_merge_eligibility.py):
   - Corrected `ROOT` resolution to `Path(__file__).resolve().parents[1]` (repo root).
   - Enforced that non-task branches fail closed (return `False`) to prevent bypasses, with an explicit exception for the `dev` branch to allow auto-promotions.
   - Fixed `gh api` calls to include `-X GET` explicitly so query arguments do not default the request to a `POST` method.
   - Setup default config and status files relative to the resolved `ROOT`.

3. **Wired CI Gate**:
   Added the `check-merge-eligibility` job to [.github/workflows/ci.yml](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-oc-r5-012/.github/workflows/ci.yml) to run on all PRs:
   ```yaml
     check-merge-eligibility:
       if: github.event_name == 'pull_request'
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
         - name: Run merge eligibility check
           env:
             GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
           run: python3 scripts/check_pr_merge_eligibility.py
   ```

4. **Reviewer Handle Mapping**:
   Updated `.orchestrator/config.example.json` to configure real identity mapping:
   ```json
       "reviewers": {
         "Claude": ["claude-bot", "claude-admin"],
         "Antigravity": ["antigravity-bot"],
         "Codex": ["codex-bot", "codex-admin", "ajoe734"]
       }
   ```

5. **Automated Branch Protection Enforcer**:
   Created [apply_branch_protection.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-oc-r5-012/scripts/apply_branch_protection.py) to push the local `policy.json` settings directly to GitHub via API and verify with a readback.

---

## 3. Execution & Verification Evidence

### A. Automated Branch Protection Policy Application (API Logs)

We ran the enforcer script to successfully configure branch protection on GitHub for both `dev` and `main` branches:

```text
$ python3 scripts/apply_branch_protection.py
Target repository: alfloop-dev/odayplus
Policy configuration to enforce:
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "orchestrator",
      "product",
      "product-e2e-gate",
      "check-merge-eligibility"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "required_approving_review_count": 1
  },
  "restrictions": null
}

--- Applying protection to branch: dev ---
Successfully applied branch protection to dev!
Reading back protection status for dev...
Branch protection is ACTIVE for 'dev'. Current configuration:
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "orchestrator",
      "product",
      "product-e2e-gate",
      "check-merge-eligibility"
    ]
  },
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "required_approving_review_count": 1
  },
  "enforce_admins": {
    "enabled": true
  }
}

--- Applying protection to branch: main ---
Successfully applied branch protection to main!
Reading back protection status for main...
Branch protection is ACTIVE for 'main'. Current configuration:
... [Successful response matching the dev configuration]
All branch protections configured successfully.
```

### B. Dry-Run / Policy Simulation Evidence on PR #301

To verify the gate's fail-closed negative behavior on actual GitHub objects, we ran `check_pr_merge_eligibility.py` against active PR #301 (targeting task `ODP-OC-R5-011` which is currently `in_progress`):

```text
$ python3 scripts/check_pr_merge_eligibility.py --pr 301 --branch task/ODP-OC-R5-011
Checking merge eligibility for task PR #301 (task: ODP-OC-R5-011, branch: task/ODP-OC-R5-011)
PR Merge Eligibility Gate FAILED:
- Canonical task status is 'in_progress', must be 'review_approved'
- PR #301 lacks approval from assigned reviewer 'Codex' (configured handles: ['codex-bot', 'codex-admin', 'ajoe734']). Latest review states: {'ajoe734': 'PENDING'}
- Required status check 'product' is not successful (status: IN_PROGRESS, conclusion: )
- Required status check 'product-e2e-gate' is not successful (status: IN_PROGRESS, conclusion: )
- Required status check 'check-merge-eligibility' is missing/not present
```
*Verification Result*: The script correctly intercepted and failed closed, detailing all outstanding merge gate requirements.

---

## 4. Test Coverage & Execution

We added targeted unit/integration tests in [test_pr_merge_eligibility.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-oc-r5-012/tests/security/test_pr_merge_eligibility.py) covering the complete state matrix:

- **Positive case**: Proves merge eligibility when the assigned reviewer has approved and all required CI checks are green (`COMPLETED/SUCCESS`).
- **Negative case (Rejected Review)**: Denies merge when CI is green but the reviewer rejected the PR (`CHANGES_REQUESTED` or no approval).
- **Negative case (Failed CI)**: Denies merge when the reviewer approved but a required CI check failed (e.g., `product-e2e-gate` is `FAILURE`).
- **Negative case (Pending CI)**: Denies merge when a required CI check is pending (`IN_PROGRESS`).
- **Fail-Closed cases**: Aborts merge if the task cannot be resolved in `ai-status.json`, if the reviewer handles cannot be resolved in the configuration, or if the task is in any status other than `review_approved`.
- **Non-Task Branch Fail-Closed**: Verifies that any PR from a non-task branch (other than the `dev` branch promotion PR) is rejected to prevent bypasses.
- **Dev Branch Bypass**: Verifies that a PR with head branch `dev` successfully bypasses the task gate.

### pytest Execution Output
```text
$ uv run pytest tests/security/test_pr_merge_eligibility.py
============================= test session starts ==============================
collected 9 items

tests/security/test_pr_merge_eligibility.py .........                     [100%]

============================== 9 passed in 0.08s ===============================
```

---

## 5. Explicit Human/Ops Fallback Gate Runbook

In the event that administrative token permissions are revoked or fail on a new environment:
1. Navigate to the GitHub repository settings page -> **Branches**.
2. Under **Branch protection rules**, click **Add rule** or edit existing rules for `dev` and `main` branches.
3. Check the box **Require status checks to pass before merging** and search/add the following checks:
   - `orchestrator`
   - `product`
   - `product-e2e-gate`
   - `check-merge-eligibility`
4. Check the box **Require a pull request before merging** and set **Required approving reviews** to `1`.
5. Check **Dismiss stale pull request approvals when new commits are pushed**.
6. Check **Require review from Code Owners**.
7. Check **Enforce all configured restrictions for administrators**.
8. Click **Save changes**.

# ODP-OC-R5-012: Merge Gate Enforcement Remediation Evidence

This document records the control-plane audit, policy configuration, implementation details, and test evidence for enforcing reviewer-approved and all-green product merge gates.

---

## 1. Control-Plane Path Inventory (PR 297, 298, and 300)

We conducted an audit of the control-plane path that permitted PRs #297, #298, and #300 to merge prematurely:

1. **Auto-Merge Command Default**: The closeout script and coordination sequence utilize the GitHub CLI command:
   ```bash
   gh pr merge --auto --merge
   ```
   This tells GitHub to auto-merge the PR as soon as all branch protection rules and status check requirements are met.
2. **Missing Reviewer/Status Safeguards**:
   - The repository lacked a policy/pre-merge hook to check the canonical task status recorded in [ai-status.json](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-oc-r5-012/ai-status.json).
   - The repository lacked branch protection rules requiring explicit approvals from the **assigned reviewer** (e.g. `Codex` or `Claude`), allowing any general review or lack thereof to satisfy GitHub if rules were loose.
3. **Silent Merges**:
   - **PR #297** auto-merged 4 minutes after rejection because the branch protection rules did not block the merge once the minimal default CI checks passed.
   - **PR #298** and **PR #300** were similarly affected by the lack of direct checking of the canonical status registry.

---

## 2. Policy & Implementation Details

To close these gaps, we introduced a multi-layered, fail-closed validation strategy:

1. **Policy Configuration**:
   Defined a policy configuration file at [.github/branch-protection/policy.json](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-oc-r5-012/.github/branch-protection/policy.json). It mandates:
   - `required_status_checks`: `["orchestrator", "product", "product-e2e-gate"]`.
   - `enforce_admins`: `true` (preventing administrators and privileged paths from bypassing gates).
   - `required_approving_review_count`: `1` (requiring at least one approval).

2. **PR Merge Eligibility Script**:
   Implemented [check_pr_merge_eligibility.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-oc-r5-012/scripts/check_pr_merge_eligibility.py). It operates on a fail-closed basis, performing the following validations:
   - Identifies the task ID from the PR branch name (e.g., `task/ODP-OC-R5-012` -> `ODP-OC-R5-012`).
   - Resolves the task in `ai-status.json` and verifies that its status is strictly `review_approved`.
   - Resolves the task's assigned reviewer, maps it to its GitHub handles via `.orchestrator/config.json`, and queries GitHub API for approvals. An approval must exist specifically from the assigned reviewer.
   - Fetches `statusCheckRollup` via GitHub CLI and validates that every required check is `COMPLETED` and `SUCCESS`.

3. **Status wrapper**:
   Created [ai-status.sh](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-oc-r5-012/scripts/ai-status.sh) as a convenient shell wrapper to execute status updates securely.

---

## 3. Test Coverage & Execution

We added deterministic tests in [test_pr_merge_eligibility.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-oc-r5-012/tests/security/test_pr_merge_eligibility.py) covering all requirements:

- **Positive case**: Proves merge eligibility when the assigned reviewer has approved and all required CI checks are green (`COMPLETED/SUCCESS`).
- **Negative case (Rejected Review)**: Denies merge when CI is green but the reviewer rejected the PR (`CHANGES_REQUESTED` or no approval).
- **Negative case (Failed CI)**: Denies merge when the reviewer approved but a required CI check failed (e.g., `product-e2e-gate` is `FAILURE`).
- **Negative case (Pending CI)**: Denies merge when a required CI check is pending (`IN_PROGRESS`).
- **Fail-Closed cases**: Aborts merge if the task cannot be resolved in `ai-status.json`, if the reviewer handles cannot be resolved in the configuration, or if the task is in any status other than `review_approved`.

### pytest Output

```text
tests/security/test_pr_merge_eligibility.py::test_positive_merge_eligible PASSED
tests/security/test_pr_merge_eligibility.py::test_negative_review_rejected_ci_green PASSED
tests/security/test_pr_merge_eligibility.py::test_negative_review_approved_one_failed_check PASSED
tests/security/test_pr_merge_eligibility.py::test_negative_review_approved_one_pending_check PASSED
tests/security/test_fail_closed_unresolved_reviewer PASSED
tests/security/test_fail_closed_unresolved_task PASSED
tests/security/test_fail_closed_non_review_approved_status PASSED

======= 7 passed in 0.09s =======
```

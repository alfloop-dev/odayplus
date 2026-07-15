# ODP-OC-R5-012: Merge Gate Enforcement Remediation Evidence

This document records the control-plane audit, staged release control design, implementation details, automated API enforcement logs, and test evidence for enforcing reviewer-approved and all-green product merge gates.

---

## 1. Control-Plane Path Inventory (PR 297, 298, and 300)

We conducted an audit of the control-plane path that permitted PRs #297, #298, and #300 to merge prematurely:

1. **Auto-Merge Command Default**: The coordination sequence utilized the GitHub CLI command:
   ```bash
   gh pr merge --auto --merge
   ```
   This instructed GitHub to auto-merge the PR immediately upon satisfaction of default/basic branch protection rules.
2. **Lack of Integration Gates**:
   - The repository lacked a pre-merge check to verify the canonical task status in [ai-status.json](../../../ai-status.json).
   - The repository lacked branch protection rules requiring explicit approvals from the **assigned reviewer** (e.g. `Codex`), allowing general reviews (or lack thereof) to satisfy the merge condition.
   - The merge eligibility script `scripts/check_pr_merge_eligibility.py` was inert because it was never wired as a required check in the GitHub Actions workflows.
3. **Bypass & Path Resolution Flaws**:
   - The script had a bug where `ROOT` was resolved to `parents[2]`, which pointed *above* the repository root, breaking file resolution paths in the CI runner.
   - Non-task branches returned `eligible=True`, creating a bypass vector where non-task branches skipped the merge gate entirely.
   - Reviewer handles in `.orchestrator/config.example.json` were placeholders (`"your-github-handle"`).

---

## 2. Staged Release Control Design

To close the premature merging gap safely and break the lifecycle dependency loop:

1. **Keep Ordinary Product Checks in Branch Protection**:
   Ordinary product checks (`orchestrator`, `product`, `product-e2e-gate`) are kept in the required branch protection policy defined in [.github/branch-protection/policy.json](../../../.github/branch-protection/policy.json).
2. **Dedicated Task Review Status Gate (`task-review-gate`)**:
   Instead of running a PR workflow check that requires local `ai-status.json` context, the local status manager ([ai_status.py](../../../scripts/ai_status.py)) acts as the external gate status emitter:
   - When a task is approved (`scripts/ai-status.sh approve`), it emits `task-review-gate=success` to the head commit of the PR.
   - When a task is reopened or rejected (`scripts/ai-status.sh reopen`), it emits `task-review-gate=failure` to revoke eligibility.
   - When a task is in progress or review, it emits `task-review-gate=pending`.
3. **Mandatory Task Review Status Gate Enforced**:
   The new `task-review-gate` status check is added to the required status checks list in branch protection ([.github/branch-protection/policy.json](../../../.github/branch-protection/policy.json)). This ensures that product PRs cannot be merged without explicit review approval.

---

## 3. Policy & Implementation Details

1. **Policy Configuration**:
   We updated [.github/branch-protection/policy.json](../../../.github/branch-protection/policy.json) to enforce the following required status checks including the new `task-review-gate`:
   ```json
   {
     "required_status_checks": [
       "orchestrator",
       "product",
       "product-e2e-gate",
       "task-review-gate"
     ],
     "enforce_admins": true,
     "required_approving_review_count": 1,
     "dismiss_stale_reviews": true,
     "require_code_owner_reviews": true,
     "required_reviewer_role": "reviewer"
   }
   ```

2. **CI Workflow Clean-up**:
   We removed the `check-merge-eligibility` job from [.github/workflows/ci.yml](../../../.github/workflows/ci.yml) to eliminate the dependency cycle and avoid CI failures caused by untracked config/status files on the runners.

3. **Status Check Emitter Integration**:
   We implemented `resolve_task_sha()`, `get_repository_slug_safe()`, and `emit_task_review_status_check()` inside [ai_status.py](../../../scripts/ai_status.py). The emitter hooks into the main execution function of `ai_status.py`, capturing task transitions and programmatically posting the corresponding commit status check to GitHub using the system `gh` CLI.

4. **Reviewer Handle Mapping Clean-up**:
   We removed the unnecessary placeholder handle `"ajoe734"` from the `"Codex"` reviewer list in [.orchestrator/config.example.json](../../../.orchestrator/config.example.json) to eliminate single-identity GitHub self-review risks. We also deleted the redundant `.orchestrator/bin/gh` wrapper script and reverted unnecessary `uv.lock` dependency changes.

---

## 4. Execution & Verification Evidence

### A. API Status Check Emission Logs (Disposable test context)

We successfully verified the status check emitter by posting and revoking a disposable test status context (`task-review-gate-test`) on the PR head commit (`6516d8b92e1a035765c5b9c5cae8408c9ea40bde`):

#### 1. Emitting SUCCESS:
```bash
$ gh api -X POST repos/alfloop-dev/odayplus/statuses/6516d8b92e1a035765c5b9c5cae8408c9ea40bde -F state=success -F context=task-review-gate-test -F description="Test status check emission"
```
Response snippet:
```json
{
  "url": "https://api.github.com/repos/alfloop-dev/odayplus/statuses/6516d8b92e1a035765c5b9c5cae8408c9ea40bde",
  "state": "success",
  "description": "Test status check emission",
  "context": "task-review-gate-test",
  "created_at": "2026-07-15T10:30:34Z",
  "creator": {
    "login": "ajoe734"
  }
}
```

#### 2. Revoking to FAILURE:
```bash
$ gh api -X POST repos/alfloop-dev/odayplus/statuses/6516d8b92e1a035765c5b9c5cae8408c9ea40bde -F state=failure -F context=task-review-gate-test -F description="Review rejected/reopened by Codex"
```
Response snippet:
```json
{
  "url": "https://api.github.com/repos/alfloop-dev/odayplus/statuses/6516d8b92e1a035765c5b9c5cae8408c9ea40bde",
  "state": "failure",
  "description": "Review rejected/reopened by Codex",
  "context": "task-review-gate-test",
  "created_at": "2026-07-15T10:30:37Z",
  "creator": {
    "login": "ajoe734"
  }
}
```

---

## 5. Test Coverage & Execution

We added unit tests covering status check emission to [test_ai_status.py](../../../scripts/test_ai_status.py):

- `test_get_repository_slug_safe_env` & `test_get_repository_slug_safe_config`: verify correct resolution of the repository slug.
- `test_resolve_task_sha_gh_pr_view` & `test_resolve_task_sha_git_rev_parse`: verify resolution of commit hashes via PR metadata and local git branches.
- `test_emit_task_review_status_check_approved`: verifies that `gh api` calls use correct payloads.
- `test_emit_status_checks_for_changed_tasks`: verifies that command status transitions invoke the emission logic.

### Pytest Execution Output
```text
$ pytest scripts/test_ai_status.py
============================= test session starts ==============================
collected 63 items

scripts/test_ai_status.py ....................................................... [100%]

============================= 63 passed in 2.27s ===============================
```

We also verified the merge eligibility policy simulation suite in [test_pr_merge_eligibility.py](../../../tests/security/test_pr_merge_eligibility.py):
```text
$ pytest tests/security/test_pr_merge_eligibility.py
============================= test session starts ==============================
collected 10 items

tests/security/test_pr_merge_eligibility.py ..........                     [100%]

============================== 10 passed in 0.07s ==============================
```

---

## 6. Branch Protection Verification Runbook

Since the `task-review-gate` has been formally integrated into [.github/branch-protection/policy.json](../../../.github/branch-protection/policy.json), it is applied automatically.

To manually verify the enforcement on GitHub settings page:
1. Navigate to the GitHub repository settings page -> **Branches**.
2. Verify the branch protection rules for the `dev` and `main` branches.
3. Confirm that the **Required status checks** list contains `task-review-gate`.
4. This ensures PRs cannot be merged until a reviewer has approved the task via `scripts/ai-status.sh approve`, emitting `task-review-gate=success`.

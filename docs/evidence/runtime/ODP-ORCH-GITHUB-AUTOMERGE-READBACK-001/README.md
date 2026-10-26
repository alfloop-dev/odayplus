# GitHubBus auto-merge readback evidence

Task: `ODP-ORCH-GITHUB-AUTOMERGE-READBACK-001`
Owner: `Codex`
Reviewer: `Antigravity6`

## Contract repaired

`enable_review_pr_auto_merge()` now performs the existing
`gh pr merge <number> --auto --merge` mutation and then reads back that exact
PR number with `gh pr view`. The bus records `auto_merge.state=enabled` only
when the readback contains an object-shaped `autoMergeRequest` or reports
`state=MERGED`.

A zero exit from the mutation without either proof is recorded as:

```json
{
  "state": "failed",
  "retryable": true,
  "failure_code": "auto_merge_unconfirmed_readback"
}
```

The existing outbound poll path does not treat that state as armed, so a later
poll retries the mutation. No manual merge path or second merge owner was
introduced.

## Verification

Focused command:

```text
python3 -m unittest discover -s .orchestrator -p 'test_github_bus.py' -t .orchestrator
........................................................................................................
Ran 104 tests in 1.247s
OK
```

Additional checks:

```text
python3 -m py_compile .orchestrator/github_bus.py .orchestrator/test_github_bus.py
git diff --check
```

The focused regression coverage verifies both post-mutation readback calls use
the same PR number, rejects a successful mutation whose remote state remains
unarmed, retries on the next call, and accepts a readback that observes the PR
already merged.

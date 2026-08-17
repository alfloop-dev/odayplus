# ODP-ORCH-REVIEW-PR-ORIGIN-REF-PROVENANCE-001 owner closeout

- Owner: Codex
- Reviewer: Antigravity
- Reviewer-approved task HEAD: `43bc8a3d8cc041519862355b83470806e8b3dfba`
- Base composed from: `origin/dev` at `a5f24d59180ed8d9b2767fe81faacc58fbd9f42f`
- Base-compose commit: `298d1984bcbaa0af8df3643a655245e03fbe83f6`
- Closeout date: 2026-08-10 UTC

## Delivered scope

Review PR discovery resolves the exact task-scoped origin branch independently
of the canonical status-root checkout and the owner's agent branch. The remote
branch head SHA is recorded as provenance and local diff comparison is only
used when the resolved ref matches that exact SHA. An unavailable local object
is treated as unknown instead of being persisted as `skipped_no_commits`.

The implementation remains limited to:

- `.orchestrator/github_bus.py`
- `.orchestrator/test_github_bus.py`

Composing the current `origin/dev` changed neither file relative to the
reviewer-approved task HEAD. It brought in support-only sidecar records from
the current base and preserved the published task history.

## Verification after base compose

The owner reran the focused checks from the task worktree:

```text
python3 -m pytest -q .orchestrator/test_github_bus.py
# 73 passed

ruff check .orchestrator/github_bus.py .orchestrator/test_github_bus.py
# All checks passed

python3 -m py_compile .orchestrator/github_bus.py .orchestrator/test_github_bus.py
git diff --check origin/dev...HEAD
git status --short --branch
```

The worktree was clean after verification. The historical acceptance packet in
this directory remains unchanged because it records an earlier rejected gate
SHA. The later reviewer approval in the task brief is the current review-state
authority for owner closeout.

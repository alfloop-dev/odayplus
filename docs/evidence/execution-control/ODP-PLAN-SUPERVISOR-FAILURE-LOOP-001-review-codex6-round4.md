# Independent Review Round 4 — ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001

- Reviewer: `Codex6`
- Reviewed owner head: `e7d39e3cdeadd1408a22bfb539041ae7f57d09a6`
- Review time: `2026-07-31T20:07:15Z`
- Disposition: **CHANGES_REQUESTED**
- Runtime rollout: **not performed**

The reviewed head exactly matched
`origin/task/ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001` before verification. The
no-PR, no-merge, no-restart, and no-deployment boundary remains in force.

## Passing checks

The declared suites and static checks pass under the dispatched reviewer
identity:

```text
AI_NAME=Codex6 PYTHONPATH=.orchestrator \
  python3 -m unittest test_supervisor test_model_rotation \
    test_adapter_fallback_policy test_runtime_state
Ran 288 tests in 1.913s
OK

AI_NAME=Codex6 python3 -m unittest scripts.test_ai_status
Ran 101 tests in 0.546s
OK

AI_NAME=Codex6 python3 .orchestrator/doctor.py
exit 0

AI_NAME=Codex6 python3 -m ruff check \
  .orchestrator/common.py .orchestrator/runtime_state.py \
  .orchestrator/supervisor.py .orchestrator/test_adapter_fallback_policy.py \
  .orchestrator/test_runtime_state.py .orchestrator/test_supervisor.py \
  scripts/ai_status.py scripts/test_ai_status.py
All checks passed

git diff --check
clean
```

The seeded coordination files and concurrent-test path set were unchanged:

```text
ai-status.json before/after:
68eb8216f8205b93bd51d0ff5163f3dadb2f20274bde8797ff5f28161950e66c

ai-activity-log.jsonl before/after:
bf7052d41dfe6207b8f7c834e6bbb9a1b0cbb48ca2f37a8c415fc0e023d06382

ODP-CONC-* path-set before/after:
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

R14 and R15 therefore remain closed. The agent/fallback matrix, review task
branch selection, owner-self-review rejection, and the older-disk/newer-memory
R18 happy path also remain green.

## Blocking finding

### R19 — P0: second-resolution cursor timestamps are not a monotonic version

R18 says concurrent merge selects the most recently updated weighted cursor,
but `utc_now()` discards microseconds and `merge_runtime_states()` resolves an
equal timestamp in favor of disk. Two legitimate cursor advances in the same
second therefore receive the same version. A newer in-memory cursor is then
deterministically rolled back:

```text
equal_timestamp_disk {'weighted_cursor': 17,
  'weighted_cursor_updated_at': '2026-07-31T12:00:00Z'}
equal_timestamp_newer_memory {'weighted_cursor': 18,
  'weighted_cursor_updated_at': '2026-07-31T12:00:00Z'}
equal_timestamp_merged {'weighted_cursor': 17,
  'weighted_cursor_updated_at': '2026-07-31T12:00:00Z'}
```

This is reachable through authorized fast-poll incident operation or repeated
`--once` cycles. It also makes the outcome depend on second-level clock
collisions rather than write order. Under a sub-second authorized cadence the
cursor can repeatedly fail to advance, recreating the reviewer-starvation
condition R17 is intended to eliminate.

Migration compounds the problem by accepting every non-empty string as a
timestamp. A malformed or future-valued disk timestamp sorts after normal UTC
values and can freeze the cursor indefinitely:

```text
malformed_disk_merged {'weighted_cursor': 17,
  'weighted_cursor_updated_at': 'not-a-timestamp'}
migrated {'weighted_cursor': 17,
  'weighted_cursor_updated_at': 'not-a-timestamp'}
```

The existing R18 regression covers only strictly newer disk time versus
strictly older memory time, so it cannot detect either failure.

## Required next handoff

Replace the second-resolution timestamp comparator with a durable monotonic
cursor revision (or another collision-free ordering primitive). Only the
singleton dispatch path may advance that revision; an auxiliary
`--claim-agent` save must carry its loaded revision without advancing it. State
migration must sanitize malformed revision/timestamp data so it cannot outrank
future valid writes.

Add deterministic tests for all of these cases:

1. newer in-memory cursor with the same wall-clock second wins;
2. stale auxiliary snapshot cannot roll back a newer disk cursor;
3. malformed/future legacy ordering data cannot permanently pin the cursor;
4. worker and queue records from the auxiliary writer still compose with the
   winning cursor;
5. save/reload and repeated fast dispatch cycles continue rotating reviewers.

Return one exact pushed head with the full 288/101 suites (plus the new tests),
Ruff, doctor, diff-check, and unchanged coordination-file hashes. Do not open a
PR, merge, restart the live Supervisor, or deploy during this remediation.

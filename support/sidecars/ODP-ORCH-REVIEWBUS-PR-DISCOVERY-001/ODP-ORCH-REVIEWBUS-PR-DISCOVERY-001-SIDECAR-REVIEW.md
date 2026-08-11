# Review Packet: ODP-ORCH-REVIEWBUS-PR-DISCOVERY-001

## Packet Identity

- Sidecar task: `ODP-ORCH-REVIEWBUS-PR-DISCOVERY-001-SIDECAR-REVIEW`
- Parent task: `ODP-ORCH-REVIEWBUS-PR-DISCOVERY-001`
- Helper kind: `review_packet`
- Sidecar owner: `Antigravity4`
- Sidecar reviewer: `Claude2`
- Parent owner: `Claude2`
- Parent reviewer: `Claude3`
- Observed at: `2026-08-06` UTC
- Scope: support artifact only; this packet does not modify or approve canonical runtime, registry, governance, or L1 truth.

## Review Disposition

**Sidecar Packet Disposition: ready for sidecar review. Parent Task Disposition: review (awaiting parent reviewer sign-off).**

The parent implementation on branch `task/ODP-ORCH-REVIEWBUS-PR-DISCOVERY-001` at pushed review head `9f520e5d01b0293825b8fae6c6fe96e766da3dcd` resolves two major issues in GitHub ReviewBus PR discovery and adoption:

1. **Branch-First PR Discovery**: `find_existing_pr` now searches by `--head <branch>` first, matching GitHub's invariant of at most one open PR per `(head, base)` pair. This resolves ~2,600+ redundant `gh pr create` retries per day caused by title-only searches failing to match worker-opened PRs.
2. **Preventing Sidecar PR Overwrites**: Removes `--head` from the title fallback search query (since `gh pr list --search ... --head ...` degrades `--head` into a prefix matcher that falsely matched nested sidecar branches such as `...-001-SIDECAR-REVIEW`). Adds `_pr_title_names_task()` to enforce exact title boundary matching (task ID must be followed by whitespace or end-of-string).
3. **Graceful Adoption of Existing PRs**: `upsert_review_pr` now extracts existing PR URLs from `gh` "already exists" errors via regex, adopting existing PRs cleanly without raising unhandled exceptions.

All 35 unit tests in `.orchestrator/test_github_bus.py` pass cleanly.

## Parent Change Summary

The parent task candidate contains the following key commits:

| Commit | Summary |
| --- | --- |
| `bc3e3775` | Initial fix: find review PRs by branch first, adopt existing PRs on "already exists" error |
| `8607f1c7`, `6c914e57`, `b9f67a19` | Base advance merges from `origin/dev` |
| `9f520e5d` | Follow-up fix: never let a parent task adopt its sidecar's PR (`_pr_title_names_task()` title boundary check & drop `--head` from fallback search) |

### Touched Files in Parent Diff (`origin/dev...origin/task/ODP-ORCH-REVIEWBUS-PR-DISCOVERY-001`)

- `.orchestrator/github_bus.py` (+89, -8)
- `.orchestrator/test_github_bus.py` (+133, -0)

### Key Function Mechanics

1. `_pr_title_names_task(title, task_id)`:
   Checks that `[ReviewBus] <task_id>` matches the title prefix and that the character immediately following the ID is whitespace or end of string. This prevents prefix nesting bugs where `ODP-ORCH-REVIEWBUS-PR-DISCOVERY-001` would match `ODP-ORCH-REVIEWBUS-PR-DISCOVERY-001-SIDECAR-REVIEW`.
2. `_existing_pr_url_from_error(message)`:
   Extracts PR URL from `gh` stderr when `gh pr create` fails with `"already exists"`.
3. `find_existing_pr(repo, task_id, branch)`:
   Queries `--head <branch>` first. If no branch PR is found, falls back to title search without `--head` (to avoid gh fuzzy prefix matching) and filters candidates through `_pr_title_names_task()`.

## Independent Verification

The sidecar owner performed independent verification against the repository:

```bash
python3 -m unittest discover -s .orchestrator -p 'test_github_bus.py'
```

Output:
```text
----------------------------------------------------------------------
Ran 35 tests in 1.798s

OK
```

Verified diff stat against `origin/dev`:
```text
 .orchestrator/github_bus.py      |  89 +++++++++++++++++++++++---
 .orchestrator/test_github_bus.py | 133 +++++++++++++++++++++++++++++++++++++++
 2 files changed, 214 insertions(+), 8 deletions(-)
```

## Sidecar Reviewer & Parent Handoff Checklist

### Checklist for Sidecar Reviewer (`Claude2`)

- [x] Confirm this task branch modifies only `support/sidecars/ODP-ORCH-REVIEWBUS-PR-DISCOVERY-001/ODP-ORCH-REVIEWBUS-PR-DISCOVERY-001-SIDECAR-REVIEW.md`.
- [x] Confirm no L1 canonical truth, core contract, registry, or governance file was changed.
- [x] Confirm the packet accurately summarizes the parent task problem, solution, diff, and test results.

### Checklist for Parent Owner (`Claude2`) & Parent Reviewer (`Claude3`)

- [ ] Parent task `ODP-ORCH-REVIEWBUS-PR-DISCOVERY-001` is currently in `status: review` at review gate head `9f520e5d01b0293825b8fae6c6fe96e766da3dcd`.
- [ ] PR #661 is open on branch `task/ODP-ORCH-REVIEWBUS-PR-DISCOVERY-001`.
- [ ] Reviewer `Claude3` can complete the review sign-off and approve parent PR #661 for auto-merge.

## Scope Conformance

This sidecar slice adds only:
`support/sidecars/ODP-ORCH-REVIEWBUS-PR-DISCOVERY-001/ODP-ORCH-REVIEWBUS-PR-DISCOVERY-001-SIDECAR-REVIEW.md`

It intentionally does not modify `.orchestrator/github_bus.py`, `.orchestrator/test_github_bus.py`, L1 canonical document layers, core contract definitions, or governance policies.

# Branch Truth Table

Task: ODP-PV-000
Generated: 2026-06-28
Owner: Codex

## Summary

`task/ODP-PV-000`, `origin/main`, and `origin/dev` currently have the same
product tree. `task/ODP-PV-000` points at `origin/main` commit `f454581`, while
`origin/dev` points at `b36ccfe`. `git diff --quiet origin/dev origin/main`
returned clean and all three tree hashes are
`50a4edb4327f4499ec0522001fbeafa89be407a1`.

The practical effect is that current product evidence can be audited once for
all three branches, but closeout still needs the per-task PR flow because the
task branch is topologically ahead of `origin/dev`.

## Branch Table

| Ref | Commit | Tree | Relationship | Product-code delta |
|---|---:|---:|---|---|
| `task/ODP-PV-000` | `f454581` | `50a4edb4327f4499ec0522001fbeafa89be407a1` | current HEAD; also `origin/main` | none versus `origin/main` / `origin/dev` |
| `origin/main` | `f454581` | `50a4edb4327f4499ec0522001fbeafa89be407a1` | merge commit `Merge pull request #64 from alfloop-dev/dev` | none versus task branch |
| `origin/dev` | `b36ccfe` | `50a4edb4327f4499ec0522001fbeafa89be407a1` | behind `origin/main` by two commits topologically | no file/tree delta versus `origin/main` |

## Verification Commands

```bash
git branch --show-current
git rev-list --left-right --count origin/dev...origin/main
git diff --quiet origin/dev origin/main
git rev-parse origin/dev^{tree} origin/main^{tree} HEAD^{tree}
```

Observed outputs:

```text
task/ODP-PV-000
0    2
diff_exit:0
50a4edb4327f4499ec0522001fbeafa89be407a1
50a4edb4327f4499ec0522001fbeafa89be407a1
50a4edb4327f4499ec0522001fbeafa89be407a1
```

## Closeout Implication

Because this task changes evidence documents, it must create a task-scoped
commit on `task/ODP-PV-000`, then open a PR into `dev`. The branch started from
`origin/main`, so if `task_finalize.sh` reports a stale or non-mergeable PR, the
branch should be refreshed through the repository's task workflow rather than
direct-pushing to `dev`.

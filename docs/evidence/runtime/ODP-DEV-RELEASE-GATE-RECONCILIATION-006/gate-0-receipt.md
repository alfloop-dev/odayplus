# Gate 0 (Code Gate) — receipt for candidate 419e6bf4958269c5b9e94efcb80770e28cd54dda

- **Task**: `ODP-DEV-RELEASE-GATE-RECONCILIATION-006`
- **Recorded by**: Antigravity7 (reconciliation owner; initially recorded by Claude)
- **Gate owner of record**: Codex2 / reviewer Claude
- **Result**: pass
- **Candidate**: `419e6bf4958269c5b9e94efcb80770e28cd54dda`
- **CI run**: [36034118291](https://github.com/alfloop-dev/odayplus/actions/runs/36034118291) on that exact head

## Why this gate was blocked, and what changed

The gate's blocker on candidate `39ae43f6` recorded one unmet criterion, C1 (zero build
warnings):

> CI run 35491368925 job 106026784688 (product-node) at 2026-09-20T05:20:29Z reported
> 'Compiled with warnings in 1574ms' — pg-connection-string process.emitWarning and
> pg-pool process.nextTick unsupported in Edge Runtime (import path:
> apps/web/src/middleware.ts -> apps/web/src/lib/auth/sessionStore.ts -> session.ts).

That was still true on 2026-09-22: PR #1357's product-node job (106751427837) emitted
**28** Edge Runtime warnings — not the two the blocker names, but 28, all tracing to the
same import chain into `pg`, `pg-pool`, `pg-connection-string` and `pgpass`.

The warnings were not cosmetic. `middleware.ts` resolves every request through
`readWebSession()`, which in production goes to `PostgresSessionStore` and loads `pg`
dynamically. `pg` cannot load in the Edge Runtime, so on Edge the dynamic import throws,
`readWebSession` rejects, `middleware.ts`'s `.catch(() => null)` swallows it, and every
authenticated request is redirected to `/login`. PR #1358 pinned the middleware to the
Node.js runtime, which both makes the durable session lookup work and removes the reason
for the warnings.

## Measurement on this candidate

`product-node` job [107749936449](https://github.com/alfloop-dev/odayplus/actions/runs/36034118291/job/107749936449):

```
✔ No ESLint warnings or errors
 ✓ Compiled successfully in 19.2s
```

Occurrences of `not supported in the Edge Runtime` in the job log: **0** (was 28).

## Required checks

| Criterion | Evidence on this candidate |
|---|---|
| lint and format checks pass | `product-node`: `✔ No ESLint warnings or errors`; `product-lint-unit`: success |
| static/type checks pass | `npm run typecheck` inside `make node-check`: success |
| unit tests pass for changed backend and domain logic | `product-lint-unit`: success |
| component tests pass for changed frontend surfaces | `npm run test --workspace=apps/web` inside `make node-check`: success |
| build artifacts immutable and traceable to the candidate SHA | Runtime Release run 36080312679 published `RELEASE_MANIFEST.json` with `candidate_sha` = this candidate and four image digests; see `runtime-release-images.json` |
| **C1: zero build warnings** | **0 Edge Runtime warnings, `✓ Compiled successfully`** |

## What this receipt does not claim

It covers the dev admission boundary only. It says nothing about staging or production
admission, and it is not a deployment receipt — nothing was deployed by the run it cites.

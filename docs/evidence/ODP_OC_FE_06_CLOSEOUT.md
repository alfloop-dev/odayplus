# ODP-OC-FE-06 Closeout Evidence

## Scope

ODP-OC-FE-06 delivered Operator Console shell polish so that the global
search field, the reset control, and the notification tray are all
functional rather than inert placeholders.

- **Working global search**: `searchValue` is now consumed. Typing in the
  top-bar search field drives an operator search request
  (`GET /api/v1/operator/search?q=...`, debounced) and renders the matched
  stores / issues / listings in a popover, each row navigating to its owning
  workspace. This closes the original defect where `searchValue` was set but
  never used, so the field did nothing.
- **Reset control**: `handleReset` clears the persisted role/workspace
  selection and the search field, returns the console to the default
  營運主管 Today view, and surfaces a confirmation toast.
- **Notifications**: the tray renders `liveNotifications`, seeded from the
  console notification set and hydrated from the operator bootstrap payload
  (`GET /api/v1/operator/bootstrap`), with the unread count shown on the bell.

The delivered implementation is API-bound (operator bootstrap + search),
which supersedes the earlier fixture-only prototype for search and
notifications; the fixture seed still backs the initial render.

## Delivery

- PR **#202** (`task/ODP-OC-FE-06` → `dev`) merged on 2026-07-13
  (merge commit `3c12dc55`). The Operator Console deliverable is durable on
  `dev`.
- The PR was previously blocked by a stale base and a webpack build failure
  (`Identifier 'ISSUE_FIXTURES' has already been declared`, plus a duplicate
  `searchResults` declaration) introduced while reconciling the task branch
  against dev's later live-API Operator Console. The merge that landed
  resolves the conflict onto dev's live-API implementation with a single
  `ISSUE_FIXTURES` import and a single `searchResults` binding, so the
  duplicate-declaration build break is gone.

## Review Approval

Task status `review_approved`; reviewer of record: Codex. Acceptance:

- Search narrows / navigates to matching operator work.
- Reset restores the default operator view and clears search.
- Notifications reflect the operator notification set with an unread count.

## Artifact Mapping

- Operator Console component: `apps/web/features/operator/OperatorConsole.tsx`
- Operator Console styling: `apps/web/features/operator/operator.module.css`

## Verification

Commands run in `/tmp/pantheon-worker-worktrees/oday-plus/odp-oc-fe-06`
against the merged `dev` tip (`3c12dc55`) on 2026-07-13:

```bash
npm run typecheck --workspace=@oday-plus/web
npm run lint --workspace=@oday-plus/web
npm run build --workspace=@oday-plus/web
```

Result:

- `typecheck`: passed with no errors.
- `lint`: passed with no ESLint warnings or errors.
- `build`: passed; the `/operator` route compiles (no duplicate-declaration
  error).

## Closeout Notes

- This corrective evidence commit carries the finalize trailers matching the
  current task owner (Claude) and reviewer of record (Codex); the earlier
  evidence commit `c66472f5` carried stale `LLM-Agent`/`Reviewer` trailers.
- The only task-owned change in this commit is this evidence artifact; the
  runtime deliverable already lives on `dev` via PR #202.

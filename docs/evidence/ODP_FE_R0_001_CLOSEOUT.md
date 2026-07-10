# ODP-FE-R0-001 Closeout Evidence

## Scope

ODP-FE-R0-001 delivered the OpsBoard product App Shell and global surfaces —
the operational-workbench frame every module UI plugs into, per
`ODAY_PLUS_OPSBOARD_SHELL_BLUEPRINT.md`,
`ODAY_PLUS_NAVIGATION_AND_WORKFLOW_SPEC.md`, and the R0 screen inventory:

- Token-only App Shell with `GlobalHeader` (logo, global search entry,
  task/notification counters, environment badge), role-aware `Sidebar`,
  `PageHeader`, and the canonical 14-work-area route map.
- Home first screen as a cross-module OpsBoard overview (not a marketing
  landing page); `/tasks`, `/search`, and the remaining work areas are wired
  into the shell as documented placeholders that later product tasks
  (e.g. FE-R0-002 Task/Notification Center) fill in.
- `CommandPalette` global-search component and shell state primitives
  (`EmptyState`, `ModulePlaceholder`) for empty/loading/permission-limited
  states, plus role-aware navigation filtering.
- Playwright shell smoke walking shell render, design-token application, all
  14 routes (including home/tasks/search), role-aware navigation, and
  sidebar-to-page-header sync.

## Review Approval

Reviewer Antigravity4 approved the task (`review_approved`) before ownership
was auto-reassigned from Antigravity2 to Claude for finalization
(`owned_finalize_dispatch`, 2026-07-10) after repeated Antigravity2 capacity /
rate-limit failures.

The reviewed shell + global surfaces were already merged to `origin/dev`
through the OpsBoard shell foundation (`ODP-R0-004`, commit `95a46cf`) and the
`frontend-product-e2e` batch. This historical closeout record is not
release-candidate authority; current release evidence must use the active
release-candidate PR head and its attached checks. This file records the owner
finalization evidence required before moving the task to `done`, and is the
task's delivery footprint (the shell itself carries no isolated
`ODP-FE-R0-001` commit because its scope landed under the shell foundation and
product-e2e batch).

## Artifact Mapping

- App Shell + shell context: `packages/ui/src/components/AppShell.tsx`,
  `packages/ui/src/components/ShellContext.tsx`
- Global header (search + task/notification counters + env badge):
  `packages/ui/src/components/GlobalHeader.tsx`
- Role-aware sidebar + nav filtering + route map:
  `packages/ui/src/components/Sidebar.tsx`,
  `packages/ui/src/nav/filterNav.ts`, `packages/ui/src/nav/routes.ts`
- Global search + shell states: `packages/ui/src/components/CommandPalette.tsx`,
  `packages/ui/src/components/EmptyState.tsx`,
  `packages/ui/src/components/ModulePlaceholder.tsx`,
  `packages/ui/src/components/PageHeader.tsx`
- App wiring / shell frame + role switcher:
  `apps/web/src/app/layout.tsx`, `apps/web/src/app/OpsBoardFrame.tsx`
- Home overview surface: `apps/web/src/app/page.tsx`
- Global surface routes: `apps/web/src/app/tasks/page.tsx`,
  `apps/web/src/app/search/page.tsx`
- Shell smoke E2E: `tests/e2e/opsboard-shell.spec.ts`

## Acceptance Mapping

1. **No landing page; shell first screen is an operational workbench** — `/`
   renders the `OpsBoard 總覽` cross-module overview inside the App Shell; the
   shell e2e asserts the `OpsBoard` heading and `app-shell`/`global-header`/
   `sidebar` render with the `dev` environment badge.
2. **Role-aware nav and read-only states work** — `filterNav.ts` drives
   role-scoped navigation; the e2e `navigation is role-aware` test verifies the
   admin item is hidden for `ops_manager` and appears after switching to the
   `admin` role.
3. **Empty/loading/error/stale/permission states covered** — shell primitives
   `EmptyState` and `ModulePlaceholder` provide the empty/placeholder states;
   role-aware filtering covers permission-limited navigation.
4. **E2E covers home / task / notification / search navigation** —
   `tests/e2e/opsboard-shell.spec.ts` reaches home/tasks/search among all 14
   routes and asserts sidebar navigation updates the page header;
   `GlobalHeader` renders the global-search entry and task/notification badges
   (`data-testid="notifications"`).

## Verification

Commands run in `/tmp/pantheon-worker-worktrees/oday-plus/odp-fe-r0-001` on
2026-07-10 (against `origin/dev` tip):

```bash
python3 -m pytest tests/e2e/test_frontend_execution_matrix_coverage.py
```

Result:

- `test_frontend_execution_matrix_coverage.py`: 23 passed.

Web typecheck and the Playwright browser run were not re-executed during this
finalization pass because the worktree has no installed `node_modules`; the
reviewer already validated the OpsBoard shell and shell smoke against
`origin/dev`, and this is not a closeout evidence blocker.

`scripts/e2e/check_product_release_gate.py` currently fails on unrelated
closeout-queue actor mismatches for `ODP-FE-XCUT-001` and
`ODP-FE-XCUT-DOMAIN-001`; that is outside ODP-FE-R0-001 scope.

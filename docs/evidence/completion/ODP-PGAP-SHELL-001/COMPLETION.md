# ODP-PGAP-SHELL-001 — Complete the product shell

- **Owner:** Claude · **Reviewer:** Codex2
- **Phase:** Product Platform P1 Closure
- **Branch:** `task/ODP-PGAP-SHELL-001` → `dev`
- **Date:** 2026-07-15

Runtime evidence only. No acceptance criterion below is claimed on the basis of
a mock, a fixture, or a static document.

## What the gap actually was

The R0 shell was navigation with nothing behind it:

| Route | Before | After |
| --- | --- | --- |
| `/` | static grid of links, `lastUpdated="—（尚無資料來源）"` | aggregated from `GET /operator/shell/home` |
| `/tasks` | `ModulePlaceholder` | Task Center: durable assignment, SLA/assignee filters, deep links |
| `/search` | `ModulePlaceholder` | authorized cross-domain search + keyboard commands |
| `/admin` | `ModulePlaceholder` | role/workspace grants as audited server writes |
| `/franchisee` | `ModulePlaceholder` | mobile-first portal: view, acknowledge, report |
| `/notifications` | did not exist | durable inbox: severity, acknowledgement, preferences |
| `/settings` | did not exist | governed, audited settings |
| `error/global-error/not-found/loading` | did not exist | full state vocabulary + recovery |

The global header also hardcoded `taskCount={7} notificationCount={3}`.

## Acceptance → evidence

| # | Criterion | Where it is proven |
| --- | --- | --- |
| 1 | Home aggregates status, tasks, approvals, decisions, freshness, role entry points | `test_home_aggregates_every_first_screen_region`, `test_home_entry_points_are_role_relevant`; E2E `home aggregates API-backed status…`, `home entry points are role-relevant and reachable` |
| 2 | Task Center: durable assignment, SLA filtering, deep links, permission-aware actions | `test_task_assignment_is_durable_and_idempotent`, `test_tasks_filter_by_sla_assignee_and_status`, `test_tasks_expose_deep_links_and_permission_aware_actions`; E2E `assigns durably and the assignment survives a reload`, `filters by SLA and assignee via shareable URLs`, `deep link resolves a single task` |
| 3 | Notifications: durable inbox state, severity, acknowledgement, preferences, source links | `test_notifications_carry_severity_and_source_links`, `test_notification_acknowledgement_is_durable_and_idempotent`, `test_notification_acknowledgement_is_scoped_per_user`, `test_notification_preferences_round_trip`; E2E `acknowledge durably…`, `preferences persist as a server write` |
| 4 | Global Search: authorized cross-domain results + keyboard command navigation, no leakage | `test_search_returns_authorized_results_and_commands`, `test_search_does_not_leak_unauthorized_workspaces`; E2E `returns authorized cross-domain results`, `supports keyboard command navigation`, `does not leak unauthorized workspaces` |
| 5 | Role/workspace administration + settings use governed audited server writes | `test_role_workspace_override_is_audited_and_changes_authorization`, `test_role_workspace_override_guards_against_lockout`, `test_settings_round_trip_and_validation`; E2E `admin workspace grant is an audited server write`, `admin refuses a lockout…`, `settings persist as a governed server write` |
| 6 | Franchisee mobile: approved viewing, acknowledgement, reporting, no operator-only data | `test_franchisee_view_excludes_operator_only_data`, `test_franchisee_acknowledgement_and_report_are_durable`, `test_franchisee_cannot_acknowledge_an_operator_only_notification`; E2E (Pixel 7) `franchisee portal supports viewing, acknowledgement and reporting`, `shows no operator-only data` |
| 7 | 403/404/500/offline/maintenance/loading/recovery pass desktop + mobile E2E | E2E `admin is forbidden for a non-admin role` (real 403), `404 surface offers a way onward`, `offline is announced and recovers…`, mobile `404 surface renders and offers a way onward`; `shell-resource-binding.spec.ts` for 401/403/500/503/transport/unconfigured classification |
| 8 | Placeholder routes and POC-only fixture copy absent from production mode | E2E `no shell route renders placeholder or POC copy`, `header counts come from the API, not a hardcoded fixture`; `product mode is explicit and defaults production fail-closed` |

## Verification (as run)

```
npm run typecheck --workspace=@oday-plus/web          # clean
npm run build     --workspace=@oday-plus/web          # ✓ Compiled successfully
npm run lint      --workspace=@oday-plus/web          # ✔ No ESLint warnings or errors
git diff --check origin/dev...HEAD                    # clean

pytest tests/contract/test_operator_shell_api.py \
       tests/security/test_operator_shell_security.py \
       tests/integration/test_operator_shell_persistence.py \
       tests/contract/test_operator_api.py \
       tests/security/test_operator_security_platform.py \
       tests/security/test_opsboard_auth_boundary.py   # 105 passed

npx playwright test tests/e2e --grep ODP-PGAP-SHELL-001   # 31 passed (x3 consecutive)
```

Backend split: 25 contract + 23 security + 8 restart-durability = 56 new tests;
the remaining 49 are the pre-existing operator suites, re-run for regressions.

## Design decisions worth reviewing

**Durability means "survives a restart", so the tests prove that.**
`tests/integration/test_operator_shell_persistence.py` boots two `create_app()`
instances over one sqlite file: writes go to the first, reads to the second.
In-memory state would have passed a single-process test and lost everything on
deploy. Idempotent replay is proven across the restart boundary too.

**Franchisee isolation is a projection, not a redaction.**
`get_franchisee_view` builds an allow-list (`FRANCHISEE_TASK_FIELDS`,
`FRANCHISEE_WORKSPACE`) rather than deleting operator keys, and drops any row
with no declared target workspace. A future seed field cannot leak by
defaulting open. Acknowledging an operator-only notification returns **404, not
403** — a 403 would confirm it exists.

**Search leakage is asserted on the payload, not the render.**
Both the contract and E2E tests assert the unauthorized entity is absent from
the response/body. A client-side filter would still have shipped the title.

**`franchisee_portal` is a new RBAC resource.**
`Role.FRANCHISEE` had no write grant and no `operator_console` grant, so
criterion 6 was unreachable. Franchisee gets VIEW+CREATE on its own portal;
operations gets VIEW (support) but **not** CREATE, so an operator cannot
acknowledge on a franchisee's behalf (`test_operator_cannot_write_on_a_franchisees_behalf`).

**RBAC is necessary but not sufficient.**
`field-lead` holds `operator_console` UPDATE yet is not a shell admin, so the
service answers 403 where RBAC alone would pass
(`test_non_admin_operator_role_is_refused_the_admin_product_rule`).

## Bugs found and fixed while proving this

1. **`getServerApiClient` dropped `x-operator-role`** (`apps/web/src/lib/api/client.ts`).
   Role-scoped reads resolved to the principal's default role while the matching
   write applied to the selected one: a preference saved as one role read back
   as another, and the role switcher did nothing on server-rendered surfaces.
   Forwarding is safe — the server re-checks the requested role against the
   principal's own and denies at `operator.role_scope`.

2. **Personal state was keyed by role.** Acknowledgement, preferences and
   settings were role-keyed, so one ops-lead acknowledging a critical SLA alert
   cleared it from every other ops-lead's inbox. Now keyed by subject; role
   remains the visibility filter. Admin grants stay role-keyed — those are role
   authorization.

3. **The root layout fetched the home aggregate**, putting an API round-trip on
   every route in the app (including `/expansion`) and regressing
   `e2e-api-bound-ui` + `e2e-operator-console`. Header counts now load after
   mount; those suites are back to parity with `origin/dev` (7 passed).

4. **`playwright.config.ts` sent no `x-tenant-id`**, so every `/operator` route
   403'd at `operator.tenant_isolation` — the existing suite never actually
   exercised an operator read.

## Handoffs and known gaps

- **→ ODP-PGAP-UX-001 (responsive frame).** `packages/ui/src/styles/shell.css`
  has **no responsive rules at all** — it is a fixed desktop grid
  (`grid-template-columns: auto 1fr`), so at a 412px viewport the sidebar leaves
  **~115px** of `.odp-main` and the document scrolls to ~1130px on every route.
  Not fixed here: that frame is UX-001's owned layer. This task worked around it
  only for `/franchisee`, which is now rendered outside the OpsBoard chrome —
  which is independently correct, since the operator sidebar was showing a
  franchisee the operator navigation. The operator surfaces' mobile specs
  therefore assert what this task owns (surfaces render, controls ≥44px) and
  leave the frame to UX-001.
- **→ ODP-PGAP-OBS-001.** Notification *preferences* are stored and audited
  here; actual out-of-band delivery (email/push honouring `severityFloor` and
  `digest`) belongs to the observability/notification-delivery lane.
- **Pre-existing, not introduced:** `opsboard-shell.spec.ts` "all 14 work-area
  routes are reachable" times out under parallel dev-compile. Verified failing
  identically on a clean `origin/dev` worktree; passes with `--workers=1`. Marked
  `test.slow()`. `/franchisee` is exempted from that spec's `app-shell`
  assertion because it is now deliberately frameless.

## Files

**Backend** — `modules/opsboard/application/shell.py` (ShellService + repository
contract), `shared/infrastructure/persistence/operator_shell.py`
(DurableShellRepository), `apps/api/app/routes/operator_modules/shell.py`
(`/operator/shell/*`), `apps/api/app/routes/operator.py` (wiring),
`shared/auth/rbac.py` (`franchisee_portal`).

**Web** — `apps/web/features/shell/*` (7 workspaces, forms, state surfaces,
product mode, resource binding), `apps/web/src/app/*` (routes + error/404/
global-error/loading), `packages/openapi-client/src/index.ts` (typed methods).

**Tests** — `tests/contract/test_operator_shell_api.py`,
`tests/security/test_operator_shell_security.py`,
`tests/integration/test_operator_shell_persistence.py`,
`tests/e2e/shell-product.spec.ts`, `tests/e2e/shell-product-mobile.spec.ts`,
`tests/e2e/shell-resource-binding.spec.ts`.

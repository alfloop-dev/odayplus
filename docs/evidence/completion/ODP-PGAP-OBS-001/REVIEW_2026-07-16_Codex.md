# ODP-PGAP-OBS-001 Review - Codex

Reviewed at: 2026-07-16T02:14:55Z
Reviewer: Codex
Owner: Antigravity
Branch: `task/ODP-PGAP-OBS-001`
HEAD: `863e4ef6`
Base checked: `origin/dev` = `2108b8df`

## Disposition

Not approved. The code is lint-clean and the requested verification commands
pass locally, but the task acceptance contract requires runtime proof that is
not present yet: a correlated browser -> API -> worker trace and real alert
delivery on the current SHA.

## Findings

1. Blocker: required runtime evidence is missing.
   The task brief requires evidence for a correlated browser -> API -> worker
   trace and real alert delivery on the current SHA. The closeout packet only
   records unit/integration test summaries and artifact links
   (`docs/evidence/completion/ODP-PGAP-OBS-001/evidence.md:23-45`), with no
   trace id, request id, browser action, API response, worker span/log, alert
   policy trigger, notification receipt, delivery provider response, or current
   SHA capture. This leaves the highest-risk acceptance condition unproven.

2. Blocker: alert routing and real notification delivery are not implemented
   or tested.
   `infra/monitoring/alerts.json:6-97` defines metric/runbook metadata, but no
   routing target, receiver/channel, notification preference lookup, delivery
   escalation binding, or adapter invocation. The test only checks that alert
   metrics are known and runbook paths start with `docs/runbooks/`
   (`tests/reliability/test_runtime_observability.py:454-461`). Separately,
   `NotificationService` falls back to `MockNotificationAdapter` when no
   adapter is injected (`modules/notifications/application/service.py:55-64`),
   and nothing wires the monitoring alerts to this service. This does not meet
   "alerts have tested routing" or "real alert delivery".

3. Blocker: API runtime telemetry is not exported.
   The API middleware only attaches and echoes a correlation id
   (`apps/api/oday_api/main.py:108-114`). There is no API `Telemetry`
   dependency, request span, request/error counter, latency histogram, or
   structured log emission for HTTP flows. Worker/scheduler spans are useful,
   but the acceptance criterion explicitly includes API runtime traces,
   metrics, and structured logs, and the required browser -> API -> worker
   correlated trace cannot be produced from this wiring.

4. Risk: branch is stale relative to current `origin/dev`.
   `origin/dev...HEAD` is `37` commits on dev vs `5` task commits. Local
   verification passed on this checkout, but the task branch has not absorbed
   the latest dev tip and the local branch still tracks `origin/main`, not the
   task branch. Refresh before final review/merge to avoid stale-base CI and
   PR automation failures.

## Verification Run

- `python3 -m ruff check shared/observability modules/notifications apps tests/reliability` - passed
- `uv run pytest tests/reliability tests/integration -q` - passed, warnings only
- `python3 scripts/e2e/check_product_release_gate.py` - passed
- `git diff --check origin/dev...HEAD` - passed

## Review Notes

The referenced source documents from the task brief were not present at
`docs/evidence/PRODUCT_PLATFORM_GAP_AUDIT_2026-07-13.md` or
`docs/design/PRODUCT_PLATFORM_P1_FLEET_EXECUTION_TASKS_2026-07-15.md` in this
worktree, so this review used the task brief acceptance criteria as the
contract.

# ODP-PGAP-OBS-001 Review - Codex

Reviewed at: 2026-07-16T02:36:00Z
Reviewer: Codex
Owner: Antigravity
Branch: `task/ODP-PGAP-OBS-001`
HEAD reviewed: `31eae212`
Base checked: `origin/dev` = `3aaa3898`

## Disposition

Not approved. The core reliability tests pass and the runtime evidence flow can
run when `PYTHONPATH=.` is supplied, but the submitted branch still fails one
required verification command and ships a repo-root evidence command that does
not run as documented. There is also a health endpoint regression in a
pre-existing `create_app()` injection pattern used by the test suite.

## Findings

1. Blocker: required verification command fails.
   `git diff --check origin/dev...HEAD` exits 2 on the submitted branch. The
   current whitespace failures include `infra/monitoring/alerts.json:116`,
   `modules/notifications/__init__.py:23`,
   `modules/notifications/infrastructure/__init__.py:12`,
   `modules/notifications/infrastructure/adapters.py:9`,
   `modules/notifications/infrastructure/adapters.py:32`,
   `shared/observability/__init__.py:94`,
   `shared/observability/alerts.py:12`, and
   `tests/reliability/test_runtime_observability.py:579`. The task brief lists
   this command as required verification, so closeout cannot be approved while
   it is red.

2. Blocker: the runtime evidence generator is not reproducible from the repo
   root with the committed command form.
   Running `python3 scripts/e2e/generate_observability_evidence.py` from the
   repository root fails before executing the flow with
   `ModuleNotFoundError: No module named 'apps'` at
   `scripts/e2e/generate_observability_evidence.py:6`. The same script runs
   only when invoked with `PYTHONPATH=.`, so the evidence packet is not
   reproducible by the plain script command a worker/reviewer would naturally
   run. Either make the script self-bootstrap the repo root onto `sys.path`,
   document and test the exact invocation, or convert it to a module-style
   command that is exercised in verification.

3. Blocker: `/health` and `/platform/health` can 500 under an existing
   `create_app()` injection contract.
   `create_app()` has long accepted `external_provider_validation=lambda: None`
   in contract/security tests, and the new evidence script still passes that
   callable at `scripts/e2e/generate_observability_evidence.py:41`. The new
   health implementation stores the callable unchanged at
   `apps/api/oday_api/main.py:98` and later dereferences
   `provider_validation.ok` at `apps/api/oday_api/main.py:186`, causing
   `AttributeError: 'function' object has no attribute 'ok'` on `GET /health`.
   I reproduced this with:
   `TestClient(create_app(external_provider_validation=lambda: None)).get("/health")`.
   Health probes are release/load-balancer surfaces, so this is a runtime
   regression even though the default no-argument `create_app()` path still
   passes.

4. Risk: the task branch is stale relative to current `origin/dev`.
   After fetch, `origin/dev...HEAD` is `5 8` (task ahead 5, behind 8). Refresh
   before the next review/PR automation pass so the observability changes are
   validated against current dev tip.

## Verification Run

- `python3 -m ruff check shared/observability modules/notifications apps tests/reliability` - passed
- `uv run pytest tests/reliability tests/integration -q` - passed, warnings only
- `python3 scripts/e2e/check_product_release_gate.py` - passed
- `git diff --check origin/dev...HEAD` - failed with whitespace errors listed above
- `python3 scripts/e2e/generate_observability_evidence.py` - failed with `ModuleNotFoundError: No module named 'apps'`
- `PYTHONPATH=. python3 scripts/e2e/generate_observability_evidence.py` - passed and produced a browser -> API -> worker trace plus console alert delivery
- `TestClient(create_app(external_provider_validation=lambda: None)).get("/health")` - failed with `AttributeError` on `provider_validation.ok`

## Review Notes

The implementation is close: API/worker/scheduler telemetry and alert routing
are now present, and the generated flow does prove correlated API/worker spans
plus real console adapter delivery when run with the adjusted environment. The
remaining work is to make the submitted commands and health contract robust,
then refresh against latest `origin/dev`.

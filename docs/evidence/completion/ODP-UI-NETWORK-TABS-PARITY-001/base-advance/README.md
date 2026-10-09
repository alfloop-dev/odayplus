# Base advance before further parity work

Pi · reviewer Codex2 · `ODP-UI-NETWORK-TABS-PARITY-001`

**Integration checkpoint only; task remains in progress, not ready for review or
merge.** This does not supersede the limitations or next work in
`../sla-action-authority/README.md` or other screen evidence.

## Composition

- Resumed clean leased branch `task/ODP-UI-NETWORK-TABS-PARITY-001` at
  `30c74d60c2db9419c30bc533a3d2d4a0e5683e83`.
- Fetched canonical `origin/dev` at
  `10eb6224fa31010fbf3c6f27612ca7d17ba4401e` (PR #1443).
- Pre-merge divergence: 84 task-side commits / 2 base-side commits.
- Used `git merge --no-ff --no-commit origin/dev` on the task branch. Automatic
  merge succeeded without conflicts. Complete through scoped `worker_commit.py`
  and a normal task-branch push; no rebase, reset of history or force push.
- The entire incoming base delta is
  `docs/evidence/runtime/ODP-DEV-SMOKE-PRINCIPAL-ISOLATION-001/README.md`.
  Preserved it byte-for-byte, SHA256
  `92df0b8004017146ee2bfaf98236408ea8c34ce7f2fc58165f7b485618605377`,
  matching `git show origin/dev:<path>`.
- No change to product code, test code, API contracts, routing, workflow or
  boundary configuration. Existing task implementation and all task history are
  retained. No smoke credentials, principal isolation or remote approval repaired
  or certified by this composition.

## Verification on the composed tree

Synchronous terminal executions; original shell exit codes retained in
`logs/*.exit`. JUnit is read after completion; no summary-based waiting or
reruns to count tests. `artifacts.sha256` binds the records.

| Command | Result |
| --- | --- |
| `.venv/bin/python -m pytest tests/security/test_assisted_sla_resource_scope.py tests/contract/test_assisted_listing_operations.py -q --junitxml=<evidence>/logs/focused.xml` | exit 0; JUnit 92 tests, 0 failures/errors/skips |
| `.venv/bin/python -m ruff check apps/api/app/routes/listings.py tests/security/test_assisted_sla_resource_scope.py tests/integration/test_assisted_listing_postgresql_runtime.py` | exit 0 |
| `.venv/bin/python delivery_toolchain/governance/check_code_boundaries.py` | exit 0; 1212 files |
| `npx playwright test --list` | exit 0; 125 tests / 18 files; inventory only |
| `git diff --check` | exit 0 |
| `git diff --exit-code HEAD -- apps modules packages tests delivery_toolchain .orchestrator/skills` (before merge commit) | exit 0; existing implementation unchanged |

This docs-only base advance does **not** rerun or claim visual E2E, PostgreSQL,
full API/web suites, build, independent VDC review, cloud runtime or remote CI.
Existing PR #1440 is not atomically resubmitted for this partial checkpoint.

## Next

Continue genuine Operator Assignment/SLA provisioning, shared authoritative
resource reads and identity-backed tenant/resource-scoped transfer-target
validation. Then verify actual Transfer/Pause UI writes and durable reload,
finish Spatial split/permission and remaining negative tab/dialog pairs,
density/independent VDC and final-scope checks. Publish the complete verified
head via `task_finalize.sh` only when the entire task is ready for review.

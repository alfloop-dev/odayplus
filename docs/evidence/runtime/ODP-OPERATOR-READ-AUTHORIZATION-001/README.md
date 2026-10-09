# ODP-OPERATOR-READ-AUTHORIZATION-001 — engineering evidence

Owner: Pi · Reviewer: Codex2 · 2026-10-09

## Scope and limits

Engineering only; no deployment, production identity mutation, source enablement,
backfill, training, model publication or admission/F11 approval was performed.
The previous live `auditor` grant is not evidence of complete operator authorization.
StoreOps missing-materialization 503, truthful empty data, source/model/data and
admission holds remain in force. Required CI and independent exact-PR-head review
are still required. No live/full-product acceptance or task completion is claimed.

Baseline: `origin/dev` / deployed source `36c94d7156011b41d71a8f6774558f35b1b45b83`.
Task branch: `task/ODP-OPERATOR-READ-AUTHORIZATION-001`.
Implementation anchors: `6ddba98038dc`, `c33a2425d699`, `76d101f40e89`.

## Implemented contract

- Explicit persisted `operator_viewer` role: finite VIEW on `operator_console`,
  `listing`, `sitescore`, `heatzone`; no wildcard, export or business mutations.
  It does not grant user administration or widen pure `platform_admin` grants.
- Existing durable identity boundary, session and audited role-management API
  remain authoritative. Existing `pm-audit` persona is selectable by the new
  role; no caller header can manufacture the role. Tenant-less/foreign object
  authorization is refused. No waiver bypass is introduced.
- Intake VIEW composes with explicit read-enabled administration only. Existing
  masks remain, collection filtering precedes pagination/counts, and store scope
  joins existing brand/region/assigned-area/heat-zone checks. Detail reads still
  require object-derived tenant/scope evidence.
- Network reads and writes retain the active console persona rather than
  impersonating expansion/reviewer roles. Production unavailable bindings no
  longer show a false bundled-fixture label.
- Canonical GovernanceService `statusBoard` is **a list of persisted counts**,
  not five readiness panels. Web accepts that real DTO and renders the counts
  separately, including zero, without inventing model/connector availability.
  Existing complete grouped boards remain supported; incomplete/malformed boards
  fail closed before row-length access. Missing required arrays are not defaulted
  into apparently valid envelopes; refreshed empty evidence history clears old rows.
- The pure-admin release journey/bootstrap/gate is unchanged. A read-enabled
  account must not be substituted for its pure-admin subject.

## Verification receipts

Final implementation checks ran on clean committed head
`76d101f40e89218822c3a28d204d3280569ce9eb` (all runtime/test changes committed).
The evidence/inventory-only commit after this head does not change those inputs.
Actual original terminal exit codes, not a grep-based inference, determined success.
The Python count is read from the existing JUnit receipt, not from another test run.

Commands (repository cwd unless noted):

```sh
# Existing locked CI setup; not a new dependency specification.
npm ci --no-audit --no-fund

# PATH has no uv / system pytest; existing canonical environment is used solely
# as interpreter/dependencies. Imports and test targets are this task worktree.
"$PANTHEON_STATUS_ROOT/.venv/bin/python" -m pytest -q \
  tests/security/test_operator_read_authorization.py \
  tests/security/test_rbac_abac.py \
  tests/security/test_operator_shell_security.py \
  tests/security/test_assisted_listing_intake_authorization_matrix.py \
  tests/security/test_assisted_listing_intake_privacy.py \
  tests/identity/test_identity_user_role_management.py \
  --junitxml="$ORCH_SCRATCH_DIR/pytest-final.xml"

npm test --workspace=@oday-plus/web -- \
  features/operator/__tests__/GovernanceWorkspace.test.tsx \
  features/operator/__tests__/GovernanceDelayedEnvelope.test.tsx \
  features/operator/__tests__/productionWorkspaceData.test.tsx \
  features/operator/network/__tests__/NetworkFindAreasWorkspace.route-gate.test.tsx
npm run typecheck --workspace=@oday-plus/web

"$PANTHEON_STATUS_ROOT/.venv/bin/python" -m ruff check \
  shared/auth/identity.py shared/auth/rbac.py shared/auth/abac.py \
  apps/api/oday_api/security/dependencies.py \
  modules/listing/application/intake_authorization.py \
  apps/api/app/routes/operator_modules/network_listings.py \
  tests/security/test_operator_read_authorization.py \
  tests/identity/test_identity_user_role_management.py
```

| Check | Original exit | Result |
|---|---:|---|
| npm ci | 0 | 486 locked packages installed; lockfile unchanged |
| Focused Python (360-second shell bound, tool bound 390 seconds) | 0 | 80 tests, 0 errors/failures/skips; PostgreSQL identity included |
| Focused Web | 0 | 4 files / 52 tests passed |
| Web typecheck | 0 | No diagnostics |
| Touched Python ruff | 0 | All checks passed |

Logs and JUnit are alongside this README. Covered positives/negatives: VIEW-only
finite grants; pure-admin remains denied; admin+viewer still administrates;
unauthorized persona/tenant-less/foreign reads denied and audited; brand/region/
store/module/classification restrictions; collection counts and private-field
masks; business POSTs refused; PostgreSQL API grant immediately visible through
real auth boundary with unchanged full scope/status and fresh role audit; another
pure admin unaffected; active Network persona across role changes; canonical
Governance count DTO/zero records, malformed boards/missing arrays, source seed
refusal and unchanged StoreOps limitations.

Earlier attempts are **not** passing receipts: `uv` exit 127; system Python lacked
pytest; first Python invocation lost its terminal receipt at the 240-second tool
bound and its log contained a new-test envelope assertion failure. That assertion
was corrected against the actual user GET DTO. Initial Web/typecheck runs exited
1/2; a strict-board regression expectation, an empty-board test expectation and a
union-array type error were repaired. Final successful runs above followed those
code changes, not a rerun merely to count tests. Existing Starlette deprecation
warnings remain warnings, not failures.

Boundary inventory initially failed as stale after the new test module. It was
regenerated with `python3 delivery_toolchain/governance/check_code_boundaries.py
--write-inventory`; only task-owned new paths are added. Finalization must verify
that inventory and lint before publishing the PR.

## CI repair dispatch — 2026-10-09

PR #1435 at `17bc7a1c` failed CI run `37885795073`: unit/security had
exactly two obsolete `18`-role assertions; E2E had two Network review decision
failures. Other required lanes reported success; the aggregate product failure
was downstream of unit/security. This was not a transient-infrastructure retry.

Repair anchors: `1dbe790e005e`, `b132e56c6577`, `f0e2daf408a4`.

- Role catalog tests require exactly the canonical enum set, including
  `operator_viewer`, not just a replacement magic count.
- Review UI uses canonical `expansion-manager` (verified reviewer/executive)
  rather than impersonating a reviewer as `ops-lead`. The API retains its
  APPROVE guard and derives role/name from its authenticated principal. Body
  actor fields cannot promote an operations manager, expansion staff, or
  admin+viewer, and cannot forge audit attribution. No RBAC grants changed.
- Browser positives explicitly select the reviewer persona; the read-only
  presentation negative uses operations manager. The Network smoke requires a
  200 API listing response for that active persona before inspecting rows.
  Expansion-user API denial remains tested with the actual expansion_user
  role (the old contract fixture incorrectly declared site_reviewer).

Original tool completion receipts for the focused repairs:

| Check | Tested implementation head | Exit | Result |
|---|---|---:|---|
| Python role management, Network review contract, operator read authorization | `b132e56c6577` | 0 | 40 tests; JUnit: no failures/errors/skips |
| Network route/persona Web tests | `1dbe790e005e` | 0 | 16 tests |
| Web typecheck | `1dbe790e005e` | 0 | No diagnostics |
| Changed Python ruff | `b132e56c6577` | 0 | All checks passed |
| Browser Network review + six-tab smoke | `f0e2daf408a4` | 0 | 9 tests passed (real local Next/API + Chromium, fixture runtime only) |

The earlier `b132e56c` commit trailer's Web count of 22 was a transcription
error; the original Vitest receipt records **16**, as above. Python's initial
exit 1 concerned two new assertions using `auditEvent.actor` instead of the
actual `auditEvent.actorName`; the corrected DTO assertions passed. First
browser attempt passed 8 review tests but failed the smoke on local fallback
rows, with cold Next JSON parsing errors. One bounded retry still failed the
same smoke. The explicit active-persona API response wait fixed that race; the
smoke and final combined run exited 0 after that test change. None of those
failed attempts is claimed as a passing receipt.

Commands (logs/JUnit copied to `ci-repair-*` alongside this README):

```sh
timeout 360 "$PANTHEON_STATUS_ROOT/.venv/bin/python" -m pytest -q \
  tests/security/test_user_role_management.py \
  tests/contract/test_operator_network_review_api.py \
  tests/security/test_operator_read_authorization.py \
  --junitxml="$ORCH_SCRATCH_DIR/pytest-ci-repair-final.xml"
npm test --workspace=@oday-plus/web -- \
  features/operator/network/__tests__/NetworkFindAreasWorkspace.route-gate.test.tsx
npm run typecheck --workspace=@oday-plus/web
"$PANTHEON_STATUS_ROOT/.venv/bin/python" -m ruff check \
  apps/api/app/routes/operator_modules/network_reviews.py \
  tests/contract/test_operator_network_review_api.py \
  tests/security/test_user_role_management.py
timeout 300 env NODE_PATH="$PWD/node_modules" \
  ODP_API_BASE_URL=http://127.0.0.1:8217 npx playwright test \
  --config /tmp/odp-read-auth-playwright.config.ts \
  tests/e2e/operator-network-review.spec.ts tests/e2e/e2e-operator-console.spec.ts \
  --grep 'Network Review decision|Network workspace exposes all six'
```

The temporary Playwright config (preserved as `ci-repair-playwright-config.txt`)
imports the **existing repository config** and only relocates ports, interpreter,
output directory and server cwd for this isolated worker. It uses the existing
fixture-mode test runtime; no parallel authentication harness or live data
claim is introduced. The final combined browser run used clean committed head
`f0e2daf408a4`; subsequent documentation/receipt copies do not change test inputs.
Required remote CI on the resubmitted head and independent exact-head review
remain outstanding; these local checks are not the full product E2E gate.

## Outstanding live acceptance (not completed here)

After required CI, independent exact-head review, merge and admitted deployment,
the coordinator must use the existing authenticated Web/BFF `/operator/users`
API to grant `operator_viewer` to the requested existing account. Read the complete
authoritative account first; preserve admin, status and every scope axis. Do not
silently remove the already granted auditor role; any role removal requires
explicit authorization. Capture a fresh role-change audit and account/session
readback, then exercise Network/Governance real DTOs and negative write/persona/
foreign-tenant probes on the exact deployed candidate. Keep StoreOps/source/model
limitations visible. Record sanitized remote receipts without cookies, bearers,
passwords or signing material. F11/live acceptance belongs to independent review
and the authorized human/coordinator, never this worker's self-approval.

# Assignment recipient authority prerequisite increment

Pi · reviewer Codex2 · `ODP-UI-NETWORK-TABS-PARITY-001` · existing PR #1440

**Backend increment only; NOT full Package 10 acceptance or review submission.**
No UI layout changed or new screenshots were produced. Actual Operator resource
provisioning/shared reads, target-directory integration and Transfer/Pause browser
success pairs remain open. Earlier fixture/modal evidence is not upgraded here.

## Source and scope

Resumed clean `ca871b6b643acabe070c511ccf2b61d93a0f15de`; product/test anchor
`2dd6b26ad1c9`. Canonical `origin/dev` stayed at
`10eb6224fa31010fbf3c6f27612ca7d17ba4401e` through final fetch. Verified its
Assignment/SLA state and authorization contracts, identity store/primitives,
persistence factory, identity migration and boundary policy. No base advance,
history rewrite, identity configuration/account provisioning or cloud action.
`source.sha256` binds final tested files, including the small test-harness repair.

- Assign and transfer now query the app's authentication identity store on each
  command, **before replay**. Targets require an existing active account in the
  same tenant, a matching canonical business role grant, same-tenant scope and
  all five resource axes (brand/region/store/assigned-area/heat-zone).
- Unknown/display-only queue roles, missing store/account, disabled/locked/invited
  accounts, foreign account/scope tenant, admin/auditor-only or mismatched role,
  missing/outside constrained resource metadata deny `ASSIGNMENT_SCOPE_DENIED`.
  Existing reviewer/steward and Operator role aliases map to real canonical grants;
  a requested role alone is never authority. No account/contact metadata leaks
  in these denial responses.
- Claim/transfer/complete now share the SLA linked-resource scope boundary.
  Dangling or foreign-tenant Intake links cannot fall back to child tenant data;
  restricted historical standalone rows need their own resource scope envelope.
- Transfer reason and handoff note trim whitespace and each require three
  nonblank characters. Keep separate Assignment/Intake concurrency tokens,
  current workflow/ownership rules, immutable authorized replay and parent-owner
  update semantics. Revoked target or actor resource scope denies retry without
  changing business/replay state.
- This is **not** a complete assignment lifecycle, calendar-derived due-time,
  WORM/global event publication, atomic identity-revocation transaction or
  independent security approval. Identity reads are fresh command-boundary reads;
  no new cross-store locking protocol is claimed.

## Evidence boundaries

The 102 security cases use explicit synthetic in-memory identity accounts and
real HTTP Intake submission/assignment creation/actions/detail reads. No child
Assignment is seeded to make the tested commands succeed. Linked-resource defect
cases deliberately mutate test metadata. The positive scope/token case creates
work for one identity and transfers to another; Intake remains v2 while the
Assignment reaches v4. Existing contract tests now explicitly seed recipients
rather than assuming a UUID confers membership.

The existing PostgreSQL runtime probe installs the **unchanged real identity
migration**, explicitly saves test recipients using SqlIdentityStore, performs
assignment plus an actual owner transfer, restarts the app and reads the retained
owner/token. Disabling the target through the restarted SQL identity store denies
lost-response replay without changing DB-backed business state; restoring it
returns the exact original receipt/ETag. A separately opened persistence bundle
reads the transferred Assignment and parent owner. Existing pause/resume retained
interval proof still passes, but its SLA remains **explicitly seeded**. The local
PostgreSQL fixture still uses canonical relation stubs/provider validation helper
and excludes PostGIS. None of this proves cloud rollout, credential/login
acceptance, Operator read integration, directory listing or genuine SLA creation.

## Original terminal receipts

Every completed invocation was synchronous with its original shell exit saved in
`logs/*.exit`; counts below come from the existing JUnit files, not reruns for
statistics. The initial combined focused invocation timed out at 600 seconds:
`focused.log` has partial output and **no exit receipt/success claim**. Tests were
split into bounded independent commands to obtain completion receipts. The two
observed initial failures were a test harness attempting to mutate a frozen
PersistenceBundle; diagnostic receipt is retained. Fixed by replacing the app's
bundle (no product/security relaxation). Original before run contains those two
harness failures; corrected before run proves all 35 failures are response
assertions against the original product. Before replay used an EXIT trap to
restore the anchored route and SHA256 verified restoration.

| Check | Original result |
| --- | --- |
| Corrected original-product negative replay (`final-before.xml`) | exit 1; 35 failures / 35 tests, expected |
| Final Assignment security (`final-assignment.xml`) | exit 0; 102 tests, no skips |
| Contract operations + v1 runtime (`final-contract.xml`) | exit 0; 70 tests, no skips |
| Existing SLA security (`final-sla.xml`) | exit 0; 32 tests, no skips |
| Local PostgreSQL runtime (`postgresql.xml`) | exit 0; 4 tests, no skips |
| Final Ruff / boundaries / diff | exit 0 each; boundaries 1213 files |
| Business E2E inventory only | exit 0; unchanged 125 tests / 18 files |

```sh
.venv/bin/python -m pytest tests/security/test_assisted_assignment_authority.py -q
.venv/bin/python -m pytest tests/contract/test_assisted_listing_operations.py \
  tests/contract/test_assisted_listing_v1_runtime.py -q
.venv/bin/python -m pytest tests/security/test_assisted_sla_resource_scope.py -q
.venv/bin/python -m pytest tests/integration/test_assisted_listing_postgresql_runtime.py \
  -m 'requires_live_env and not requires_postgis' -q
.venv/bin/python delivery_toolchain/governance/check_code_boundaries.py
npx playwright test --list
```

No full API/web suite, build, browser execution, visual/VDC approval, remote CI or
live acceptance was run/claimed. Boundary inventory adds only the new test row.

## Next owner work

Implement the identity-backed tenant/resource-scoped target directory and connect
it to the existing fail-closed UI; reuse these fresh server checks on writes.
Complete genuine Operator Assignment/SLA provisioning and shared authoritative
read-model before actual Transfer/Pause UI mutation/reload success pairs. Preserve
calendar/policy and resource-token authority; do not manufacture SLA IDs/times or
people. Then complete Spatial split/permission, remaining negative screen pairs,
density/independent VDC and final-scope checks. **Task stays in_progress; this
increment is not ready for task_finalize/handoff/re_review/done.**

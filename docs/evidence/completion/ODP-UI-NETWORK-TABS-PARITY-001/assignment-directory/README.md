# Assignment recipient directory / typed UI binding increment

Pi · reviewer Codex2 · task `ODP-UI-NETWORK-TABS-PARITY-001` · existing PR #1440

**Increment only; not full Package 10 acceptance or formal review submission.**
No browser screenshots/geometry, independent VDC approval or cloud acceptance were
produced here. Genuine Operator resource provisioning/shared read authority and
Transfer/Pause browser mutation/reload pairs remain open. Earlier fixture/modal
screenshots are not upgraded by these unit/API results.

## Source and owned boundary

Resumed clean `b3a923f8d546b1c3a9339de72064d10d72074867`. Backend anchor
`2890a31b32b1`, UI/SQL-test anchor `9c29cae2ca6d`. Canonical `origin/dev` remained
`10eb6224fa31010fbf3c6f27612ca7d17ba4401e` at the final fetch. Compared canonical
identity store, API, client/toolchain and web/test configuration before editing.
No rebase/base merge, account provisioning, identity migration, SLA policy,
permission expansion, cloud action or status-file editing.

- Additive `GET /api/v1/assignments/{assignment_id}/transfer-targets` is a
  resource-specific action directory, **not a general identity inventory**.
  Enforce actor tenant and all linked resource axes, transfer business role,
  existing staff ownership rule and ASSIGNED/CLAIMED workflow before querying
  accounts. Dangling/foreign Intake links deny. Missing identity store returns
  unavailable, not a successful empty directory.
- Query the existing identity store for active same-tenant accounts (SQL query
  includes both filters). Reuse the fresh identity principal resolver used by
  writes, filter target account/scope tenant and all five resource axes, and
  select one deterministic canonical business role per account. Exclude the
  current owner, unsupported grants and blank display-name/username labels.
  Return only UUID/name/canonical role plus exact Assignment ID/version;
  never email, scopes, contacts, credentials or unrelated tenant identities.
  Successful responses are `Cache-Control: no-store`.
- The main runtime OpenAPI artifact/generated types declare this additive
  operation. The separately approved Intake v1.1.3 bundle was **not** altered;
  this directory is a later-spec UI integration extension, not a claim that
  the old bundle already specified identity enumeration.
- Typed client/intake transport validate resource binding, positive integer
  version, array shape, unique UUID recipients, nonblank labels and the four
  supported canonical business roles. Malformed/denied/unavailable responses
  close submission instead of falling back to demo actors/queues.
- The live container no longer accepts an injected static `transferTargets`
  prop. Read directory authority only while the authorized transfer dialog is
  open. Bind results to exact client, Assignment ID, version and read generation.
  Changing resource/version/role-client, closing, or reloading drops old results
  immediately; cancelled/late responses cannot restore authority. Version
  mismatch requires owner/resource refresh; it never upgrades If-Match from
  the directory alone. Successful conflict refresh reloads directory authority.
- Keep the Package 10 dialog shell, required handoff and risk acknowledgment.
  Add loading/error/no-recipient/reload feedback using existing note/button
  styling. Asynchronous results require explicit target selection. Directory
  invalidation revokes consent but retains the handoff draft. The server still
  resolves fresh grants before mutation/replay; directory reads are not grants.

This is not a cross-store atomic revocation protocol, paginated large-tenant
performance acceptance, identity provisioning/login proof or a completed
Assignment/SLA lifecycle. No new SLA IDs, calendar times or recipient accounts
are manufactured by product code.

## Verification boundary and original receipts

All completed invocations were synchronous; their original shell exit codes are
saved in `logs/*.exit`. Counts below come from existing JUnit/terminal output,
not reruns for statistics. The first all-security invocation timed out after
600s with partial dots and **no exit receipt**. It is unknown/incomplete, not
passed. Split directory and write checks obtained independent completion receipts.
Initial Web/typecheck runs found a misnamed guard, nullable-ID parameter and
mock-call tuple type; repaired without weakening assertions. Both original
failure logs/exit receipts are retained. Final Web logs include happy-dom
`ECONNREFUSED :3000` resource diagnostics despite successful suite exit; they are
not a browser/backend availability or network acceptance result.

| Check | Original result |
| --- | --- |
| Directory HTTP security | exit 0; 41 tests, no skips |
| Existing Assignment write/replay security | exit 0; 102 tests, no skips |
| Contracts: operations/runtime/OpenAPI | exit 0; 75 tests, no skips |
| Local PostgreSQL runtime | exit 0; 4 tests, no skips |
| Mounted Web/typed lifecycle focused tests | exit 0; 51 tests / 2 files |
| Web typecheck / focused ESLint / Ruff | exit 0 each |
| Main OpenAPI freshness (`--skip-diff`) | exit 0; artifact/client match |
| Boundaries / diff check | exit 0; unchanged 1213-file inventory |

The security tests use explicit synthetic identity accounts and real HTTP
submission/assignment/actions; no child Assignment is seeded for success.
Directory tests cover each resource axis, invalid account/role/scope/name,
actor permissions/ownership, linked-resource faults and unavailable authority.
Canonical directory roles are accepted by the genuine transfer endpoint.

The existing local PostgreSQL probe uses the actual unchanged identity migration
and SqlIdentityStore: directory includes the eligible reviewer, excludes it
immediately after SQL account disabling, restores it on enabling, then actually
transfers, restarts and independently reloads retained owner/version (existing
replay proof remains). Identities are explicitly provisioned **test fixtures**;
SLA remains explicitly seeded, and local canonical relation/provider helpers
are unchanged. This does not prove genuine Operator/SLA creation, cloud rollout
or credential/login acceptance. Mounted Web tests use mocked HTTP responses;
they prove DTO/context/consent behavior, not server authorization or durable UI
success. No full API/web suite, build, browser/E2E execution or final-scope
verification was claimed.

Commands used (logs preserve original execution output):

```sh
.venv/bin/python -m pytest tests/security/test_assisted_assignment_authority.py -k directory -q
.venv/bin/python -m pytest tests/security/test_assisted_assignment_authority.py -k 'not directory' -q
.venv/bin/python -m pytest tests/contract/test_assisted_listing_operations.py \
  tests/contract/test_assisted_listing_v1_runtime.py tests/contract/test_assisted_listing_openapi.py -q
.venv/bin/python -m pytest tests/integration/test_assisted_listing_postgresql_runtime.py \
  -m 'requires_live_env and not requires_postgis' -q
.venv/bin/python delivery_toolchain/openapi/check_drift.py --skip-diff
.venv/bin/python delivery_toolchain/governance/check_code_boundaries.py
# from apps/web, focused Vitest / tsc --noEmit / ESLint via existing node_modules binaries
```

## Next owner work

Complete genuine Operator Assignment/SLA provisioning and shared authoritative
reads, then actual Transfer/Pause UI writes/reload and design-before-after pairs
at 1440/390 with geometry/permissions/VDC evidence. Directory integration does
not repair the independent Operator resource-read-model gap. Continue Spatial
split/permission, remaining negative screen pairs, full-scope density and
independent VDC review. **Task remains in_progress, not ready for
`task_finalize.sh`, handoff, re_review or done.**

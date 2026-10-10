# SLA action authority and retained intervals

Pi · reviewer Codex2 · `ODP-UI-NETWORK-TABS-PARITY-001` · existing PR #1440

**Backend prerequisite increment only. NOT full Package 10 acceptance, Operator
provisioning/shared-resource proof, directory authorization or review submission.**
No layout/product language was changed; this increment has no new screen pairs.
Existing Transfer/Pause screen evidence remains limited as described in
`../transfer-targets/README.md` and must not be reclassified as successful API UI
acceptance by these backend results.

## Source and bounded repair

Resumed clean task head `6808020f7d993259364f1c544c3577acfbcc4241`. Verified live
canonical status/brief, prior review findings, and canonical `origin/dev`
Assignment/SLA state and authorization contracts. During this run dev advanced
from `2fe3ef933794d0bb293449b74dffe9602d53f767` to
`10eb6224fa31010fbf3c6f27612ca7d17ba4401e` (#1443); inspected that delta and
confirmed no changes to these route/runtime-test files, boundary configuration,
or canonical state/authorization documents. No merge/rebase was done.

Product/security anchor: `89945ca06f96`; PostgreSQL probe anchor: `8ef3290a6b18`.
`source.sha256` binds the final tested product/tests and generated inventory.
The final security test also checks stale tokens before any interval mutation.

- SLA pause/resume now check the **linked Intake's** brand, region, store,
  assigned-area and heat-zone scope before mutation **and before replay**.
  Child tenant equality is insufficient. A missing linked Intake denies; a
  linked foreign tenant denies even if the child has the caller's tenant.
- Historical standalone SLA records retain their own scope envelope. Missing
  axes deny restricted principals; this does not invent an Intake association.
- Required pause reason is trimmed and must contain at least three characters.
- Retain one interval per committed pause: required reason/expected resume time,
  actual start time, previous state, actor, version, audit/correlation IDs and
  interval ID matching the API receipt. Replay does not append another interval.
- Resume closes the matching interval with actual end time, reason/actor/version
  and receipt identifiers. Accumulate nonnegative elapsed seconds once. Do not
  fabricate elapsed time for a legacy row without interval history.
- Preserve original Intake state/version, due time and existing resume-to-prior
  state behavior. **Business-calendar policy derivation, scheduled resume,
  escalation and global event/audit publication are not implemented or certified
  here.** Interval records retain the existing action's identifiers; this is not
  a new WORM audit/event-delivery claim.
- `docs/audits/code-boundary-inventory.csv` is regenerated with exactly the one
  new security-test row. No boundary policy/config change.

## Regression proof and persistence limits

`test_assisted_sla_resource_scope.py` uses actual HTTP actions and detail reads
against the local process store, but **explicitly seeds the linked SLA fixture**.
It does not imply an approved SLA creation endpoint exists. It covers all five
scope axes with missing/outside metadata, dangling/foreign links, restricted
standalone metadata, revoked-scope replay rejection, matching-scope acceptance,
required reasons, independent concurrency tokens, elapsed duration/replay and
multiple interval retention. Denials leave Intake, SLA and replay snapshots
unchanged.

The existing PostgreSQL runtime test is expanded with an explicitly seeded SLA,
real pause, app restart, detail read/lost-response replay, real resume and a
second independent DB-backed store reload of the closed interval. It uses the
project's existing local PostgreSQL fixture and canonical relation stubs/provider
validation test helper (PostGIS case excluded). This proves the local durable
store/action boundary, **not cloud deployment, verified identity-directory
membership, Operator read-model integration or genuine SLA provisioning**.

## Original terminal receipts

All complete runs used synchronous tool invocations and original shell exit
receipts in `logs/*.exit`. The first security invocation hit the tool's 120s
limit after partial dot output: `logs/focused.log` has **no exit receipt and no
success claim**. It was rerun with a bounded 600s limit, not an output/passed wait
loop. After adding stale-token assertions the final 32-case security run was
executed again; no test was rerun solely to count results.

| Check | Original result |
| --- | --- |
| Original product + new regressions (`before.xml`) | exit 1; 32 failures / 32 tests, expected |
| Initial complete repaired security run | exit 0; 32 tests |
| Final security run (`final-security.xml`) | exit 0; 32 tests, no skips |
| Existing contract operations (`contract.xml`) | exit 0; 60 tests, no skips |
| Local PostgreSQL runtime, excluding PostGIS | exit 0; 4 tests, no skips |
| Ruff, final touched Python files | exit 0 |
| Boundary check, final generated inventory | exit 0; 1212 files |
| Business E2E list only | exit 0; unchanged 125 tests / 18 files |
| Diff check | exit 0 |

Before regression replay temporarily restored only the original route source
from `6808020f7`, with an EXIT trap restoring the anchored file. The original
failure log/XML and before-source hash are retained; SHA256 verification proved
the restored product matches the tested final product. No business E2E execution,
web unit/typecheck/build/lint, whole API suite, cloud/remote CI, visual parity or
independent VDC approval was run/claimed in this increment.

Reproduction (project environment):

```sh
.venv/bin/python -m pytest tests/security/test_assisted_sla_resource_scope.py -q
.venv/bin/python -m pytest tests/contract/test_assisted_listing_operations.py -q
.venv/bin/python -m pytest tests/integration/test_assisted_listing_postgresql_runtime.py \
  -m 'requires_live_env and not requires_postgis' -q
.venv/bin/python -m ruff check apps/api/app/routes/listings.py \
  tests/security/test_assisted_sla_resource_scope.py \
  tests/integration/test_assisted_listing_postgresql_runtime.py
.venv/bin/python delivery_toolchain/governance/check_code_boundaries.py
npx playwright test --list
```

## Required next work

Genuine Operator Assignment/SLA provisioning and shared authoritative resource
read-model remain absent in the local fixture composition. Add the identity-backed,
tenant/resource-scoped target directory and server target authorization; verify
actual Transfer/Pause UI writes and durable reload before claiming the success
screen pairs. Also finish Spatial split/permission, remaining negative tab/dialog
pairs, density/independent VDC and final-scope verification. Find Areas history
increment remains separately documented; nothing here supersedes its limits.
**Task remains in_progress; no task_finalize/handoff/re_review/done for this
bounded increment.** Commit and push the evidence/changes so the next owner run
can continue from a clean durable task branch.

# Batch006: incoming Store Ops authentication fixture composition

**One additional test path requested; NOT a grant, review submission or admission.**
Manager run `pi-20261015T122146Z-d2046e81`, canonical owner Pi / final reviewer
Human/Ops. Actual live scope has356 entries and DOES NOT include the requested
path. No source/test change to that path has been made.

## History-preserving base advance and measured failure

Explicit dispatch required composing admitted `origin/dev`
`19562ad2a8773099db26366446b3488b446e630c`. Scoped private-index merge anchor
`ffb269ba9a1e8ba5d3a3caa05c3091a018430c58`, tree
`8817663e5509ca1ea4fd87dd136177d479efd98d`, preserves both task1ac806841 and
that base as parents. Git's three-way merge was conflict-free; the seven incoming
non-inventory paths are byte-equal to the base, while the boundary inventory
retains both sets of entries. Human-decision files and complete original task
record are unchanged. No reset/rebase/amendment/force push or discarded history.

At THAT exact clean anchor, a fresh foreground command really terminates exit1:

```
uv run --frozen --python 3.12 pytest tests/integration/test_store_ops_state_materializer.py -q -n 0
```

Native duration22.094073s under420;20 collected members,18 pass and2 fail:

1. `test_tenants_never_see_or_overwrite_each_others_state`: first expected503,
   actual401 at line579.
2. `test_production_never_reads_or_writes_an_unscoped_document`: expected503,
   actual401 at line630.

The complete805-line incoming fixture and owning auth seam were inspected.
`_production_client` sets `ODP_REQUIRE_LIVE_DATA=true`, but `_headers` supplies
ONLY development `x-subject-id`/roles/tenant headers, no verified credential.
`principal_from_headers` uses the authoritative `live_data_required()` resolver
and correctly rejects credentialless live-mode requests with401. Do NOT restore
header trust or reinterpret401 as tenant-isolation/503 success. Later assertions
in the first failed test were not reached; no claim they already pass.

Despite the fixture class name, this module uses the real PostgresDocumentStore
class over a **SQLite engine reporting PostgreSQL dialect**, NOT an actual-PG
runtime test. This run also provisioned an owned PostgreSQL16/PostGIS fixture,
but that does not turn these20 members into actual-PG proof. Existing actual-PG
owning selections remain separately required. No infrastructure/assertion waiver.

## EXACT additional path requested

- `tests/integration/test_store_ops_state_materializer.py`

Authorize and serialize ONLY an explicit module-local supported synthetic signed
service identity fixture through real `config_from_env`/`default_boundary`, with
server-declared separate tenant principals and appropriate existing roles. Keep
live mode and ALL20 original members/assertions, including expected503 before
materialization, own200, foreign404, tenant-partitioned state/audit/idempotency,
CAS/interleavings, no lost operator writes, worker/scheduler registration,
no fixture IDs and unscoped legacy-document read/write refusal. Add forged-header
and anonymous401 negatives and prove no mutation/leak, as justified in this same
module. Reset module-local boundary caches for fixture isolation. No dependency
override, injected accepted Principal, global `_authz.py`/auth helper widening,
production credential, auth-source/configuration/policy/role/permission change,
new SQLite-as-actual-PG claim, skips, deleted assertion or changed test budget.

The request chooses no source authorization fix: the observed401 is the intended
fail-closed boundary. If another owning file is genuinely necessary, stop and
request it separately. Incoming Store Ops production paths are NOT requested for
new authoring; their import from admitted dev does not grant such a lease.
Manager must renew actual executor/context/lease before the requested edit.

## Custody, durable evidence and continuation

Evidence index: [pi-base-advance-diagnostics.json](pi-base-advance-diagnostics.json).
The actual raw output SHA is
`628b177ad613a74260746b40764eeecc173d55d526ee47317f9945ae8d36e766`.
Raw/custody/terminal/fixture/seal records live in the current canonical scratch
run under `base-composition/` and `base-composition-seal.json`. Native root wait0
is NOT claimed: actual root exit1, kernel ECHILD and no owned descendants/writers
were recorded before removal0 of the exact owned container. Temporary genuine
untracked evidence was hash-verified into scratch and removed ONLY after closure.
The command finished before45s, so long-running Manager activity recognition was
NOT exercised/proven. External/inaccessible unrelated fd visibility stays a gap.

This request's documentation successor is a DIFFERENT head: these measurements
are diagnostic evidence forffb only, never transferred to that successor. No
additional full2178/canonical long retry was launched against a known incomplete
composition. All prior1ac13 canonical/21 native/CI successes remain historical.
After justified leased fixture repair freeze a new composition and obtain ALL14
canonical, all17 supplemental, owning/base-composition controls, normal terminal
hosted CI and configured independent non-contributing Codex2 full-body/import/
README/final-terminal assessment with explicit coverage/account-provenance gaps.
Keep1200/420, serial-n0/full inventory, actualPG/frozenNode, native480/540,
Vitest5000ms, Web300kB, Argon64MiB/time3 and all authority/temporal/numerical guards.

Normal incremental task-branch push may update existing PR1478/CI. This is NOT
review-ready; do not run task_finalize/review submission before complete owned
proof. Human/Ops/PENDING, original complete blocked6/failedPi/PR1459@a129,
quarantine/bindingNULL/cleanupUNKNOWN/HOLD/goalfalse persist. No approval,
auto-merge arming, merge, done/finalization, live credentials/accounts/sessions/
recovery/deployment/cloud/IAM/business/data/model/F11 effect.

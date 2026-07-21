# Review Findings (round 2) — ODP-INTAKE-SNAPSHOT-001

- Reviewer: Claude2
- Owner: Antigravity3
- Reviewed commit: `6b294d5c` (`task/ODP-INTAKE-SNAPSHOT-001`)
- Date: 2026-07-20
- Verdict: **REQUEST CHANGES** (5 blockers)

Round-1 status (`31bfda11`, 7 blockers):

| # | Round-1 blocker | Round-2 status |
|---|---|---|
| B1 | residency = substring guess | **FIXED** — explicit `RESIDENCY_APPROVED_BUCKETS` allowlist, one implementation |
| B2 | residency fail-open for non-TW_ONLY | **FIXED** for unknown modes (one residual case, see NB-1) |
| B3 | policy gate not before retrieval / no runtime caller | **FIXED** — `RetrievalSecurityGate.fetch` resolves policy from the registry, worker wires the service (introduced R1 though) |
| B4 | cross-tenant orphan findings | **FIXED** — verified: reconcile of tenant-A with a tenant-B object present now returns `orphans: 0` |
| B5 | non-idempotent, leaks an object per retry | **REGRESSED** — see R2 |
| B6 | generation never recorded | **FIXED** — `object_generation` persisted, download pinned by generation, `purge_after` computed (columns exist via SCHEMA_0002 patch) |
| B7 | evidence overstates test result | **NOT FIXED** — see R5 |

Local run of the owner's own command on `6b294d5c`:

```
python3 -m pytest tests/integration/test_assisted_listing_snapshots.py \
  tests/security/test_assisted_listing_snapshot_residency.py -q
-> 8 passed, 2 skipped
```

---

## R1 (blocker, security) — a test shim was added to the production SSRF resolver

`modules/external_data/security/assisted_listing_retrieval.py:532`:

```python
def _resolve_host(host: str) -> Sequence[str]:
    if "synthetic.example" in host:
        return ("93.184.216.34",)
```

`_resolve_host` is the gate's only defence against retrieval SSRF: the caller
resolves the host and rejects private/loopback/link-local addresses. This branch makes
any hostname *containing* the substring `synthetic.example` skip DNS entirely and report
a hard-coded public IP, so the IP allowlist check passes while the actual HTTP fetch
resolves the real name.

```python
from modules.external_data.security.assisted_listing_retrieval import _resolve_host, is_blocked_ip
import ipaddress
for h in ["synthetic.example.attacker.test", "internal-synthetic.example.corp.local"]:
    print(h, _resolve_host(h), [is_blocked_ip(ipaddress.ip_address(i)) for i in _resolve_host(h)])
```

```
synthetic.example.attacker.test        ('93.184.216.34',) [False]
internal-synthetic.example.corp.local  ('93.184.216.34',) [False]
```

Fixture hosts must be injected through the existing `resolver`/`fetcher` constructor
seams (that is what they are for), never hard-coded into the production resolver.

## R2 (blocker, AC1 + AC4) — the compensating delete destroys an already-committed snapshot's evidence

The B5 fix pairs a deterministic key (`uuid5(tenant:source:sha256)`) with a compensating
delete on SQL failure, but the two interact badly: on any retry the upload hits
`Precondition Failed`, is swallowed as "already uploaded", and the INSERT then fails on
the primary key / `UNIQUE (tenant_id, content_sha256, source_id)` — at which point the
handler deletes the object that the *previous, committed* snapshot row still points at.
Immutable evidence for a healthy intake is deleted, and the next integrity check
quarantines that intake.

Repro (sqlite with the real constraints; identical for a same-intake retry and for a
second intake re-capturing the same content):

```
first snapshot: c32b2d7d-...  | bytes: b'same-content'
integrity before: OK
second call raised: IntegrityError UNIQUE constraint failed: ...
OBJECT GONE: FileNotFoundError gs://taiwan-snapshots/tenants/tenant-A/snapshots/c32b2d7d-.../raw
integrity of snapshot 1 after: MISSING
sql rows: [('c32b2d7d-...', 'IN-1')]      # the row survives, its evidence does not
findings: [('MISSING_EVIDENCE', 'c32b2d7d-...')]
```

Fix: only compensate for objects this call actually created (the upload returned a new
generation), and make the insert genuinely idempotent (`ON CONFLICT ... DO NOTHING` +
re-read, returning the existing `source_snapshot_id`) instead of treating a duplicate as
a failure. Deleting immutable WORM evidence on an error path should not be reachable at
all.

## R3 (blocker, AC1 + charter) — the only runtime path stores "immutable snapshots" in a process dict

`build_source_snapshot_service` selects `GcsObjectStore` only when
`os.environ["ODP_OBJECT_STORE"] == "gcs"`, otherwise `InMemoryObjectStore`.
`ODP_OBJECT_STORE` is set in no compose file, no `infra/`, no workflow, and no runbook —
`grep -rn ODP_OBJECT_STORE` matches only the two module files that read it. So the worker
path added in this branch persists SQL rows with `gs://…` URIs while the bytes live in a
per-process dictionary that dies with the process; after a restart every snapshot reads
back as `MISSING_EVIDENCE` and reconciliation quarantines the intakes. The task summary
explicitly forbids completing with mocks in place of runtime.

Fix: default to the real store when credentials/bucket config are present (or fail closed
when they are not), and add the deployment configuration
(`ODP_OBJECT_STORE`, bucket, `ODP_RESIDENCY_APPROVED_BUCKETS`) to the rollout runbook.

## R4 (blocker, regression) — this branch turns an existing test red

```
python3 -m pytest tests/reliability/test_assisted_listing_intake_jobs.py -q
  on origin/dev                 -> 8 passed
  on 6b294d5c                   -> 1 failed, 7 passed
FAILED tests/reliability/test_assisted_listing_intake_jobs.py::test_retrieval_stage_local_retry_and_timeout
        assert call_count == 4  ->  assert 3 == 4
```

The parsing stage no longer re-runs `retrieve()` (a genuine improvement), so the test's
contract changed. Either way the branch cannot merge with a red suite: update the test to
the new stage contract in this branch and state it in the evidence. The owner's
verification command only covered the two new files, which is why this went unnoticed —
please run the intake-related suites (`-k "intake or snapshot or retrieval"`) before
handing off.

## R5 (blocker, evidence integrity — unchanged from B7)

`docs/evidence/completion/ODP-INTAKE-SNAPSHOT-001/completion_evidence.md` was not touched
in `6b294d5c` and still states "All 10 integration, residency security, and **PostgreSQL**
tests passed successfully" with `10 passed in 2.86s`; the commit trailer repeats
"(10 passed)". The actual result here is **8 passed, 2 skipped** — the two PostgreSQL
cases are `@pytest.mark.requires_live_env`. Record the live-env command/environment that
produced a 10/10 run, or state that the PostgreSQL cases skip by default.

---

## Non-blocking

1. **NB-1** `check_bucket_residency` merges `ODP_RESIDENCY_APPROVED_BUCKETS` into the
   allowlist for *every* mode, so once that env var is set an unknown or empty residency
   mode is allowed again (`check_bucket_residency("TOTALLY_UNKNOWN_MODE", "taiwan-snapshots")`
   passes). Impact is limited (the bucket is still operator-approved), but the override
   should be per-mode, e.g. `ODP_RESIDENCY_APPROVED_BUCKETS_TW_ONLY`.
2. `worker.py` hard-codes `bucket="taiwan-snapshots"` and falls back to the all-zero UUID
   tenant; both belong in configuration / should fail closed.
3. Round-1 non-blockers 1–6 (silent `except: pass` around commit/fetch, over-broad
   `MISSING_EVIDENCE` mapping, unused `tenant_id` in `check_source_policy`, `_is_sqlite`
   sniffing, inline `intake_states` imports) are all still open.
4. `DefaultRetrievalFetcher` re-resolves the source policy inside the fetcher after the
   gate already resolved it; one resolution should be threaded through.

## Verified fixed (no action needed)

B1, B2 (unknown modes now deny-by-default), B3 (registry-driven policy before fetch plus a
real worker caller), B4 (tenant-prefixed keys, prefix-scoped listing, prefix enforcement in
download/delete/head), B6 (generation + `purge_after` persisted, generation-pinned reads).

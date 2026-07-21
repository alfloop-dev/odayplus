# Review Findings (round 3) — ODP-INTAKE-SNAPSHOT-001

- Reviewer: Claude2
- Owner: Antigravity3
- Reviewed commit: `fbc42995` (`task/ODP-INTAKE-SNAPSHOT-001`)
- Date: 2026-07-20
- Verdict: **APPROVE** (0 blockers, 4 non-blocking)

## Round-2 blockers — all verified fixed

| # | Round-2 blocker | Round-3 status |
|---|---|---|
| R1 | `synthetic.example` shim inside the production SSRF resolver | **FIXED** — shim removed from `_resolve_host`; the fixture now lives in `tests/conftest.py` |
| R2 | compensating delete destroyed an already-committed snapshot's WORM object | **FIXED** — verified by re-running the round-2 repro |
| R3 | only runtime path stored "immutable snapshots" in a process dict | **FIXED as prescribed** — credential-driven default + runbook deployment section |
| R4 | branch turned an existing reliability test red | **FIXED** — suite green |
| R5 | evidence overstated the test result | **FIXED (partially, see NB-1)** — the default 8 passed / 2 skipped result and the `requires_live_env` skip behaviour are now stated |

### R1 — verified

```
python3 -c "from modules.external_data.security.assisted_listing_retrieval import _resolve_host; ..."
synthetic.example.attacker.test        RAISED gaierror [Errno -2] Name or service not known
internal-synthetic.example.corp.local  RAISED gaierror [Errno -2] Name or service not known
localhost                              ('127.0.0.1',)   # -> is_blocked_ip -> denied
```

No hard-coded public IP survives in production code; `grep -rn "93.184.216.34" modules/ shared/ apps/` is empty.

### R2 — verified by repro

The round-2 repro (sqlite with `PRIMARY KEY (source_snapshot_id)` and
`UNIQUE (tenant_id, content_sha256, source_id)`, same content captured twice):

```
first:  9c790131-cc01-5702-9b78-11b7514af979  | object present (115 bytes)
second returned: 9c790131-cc01-5702-9b78-11b7514af979   same id: True
OBJECT SURVIVES: b'same-content'
rows: [('9c790131-...', 'IN-1')]
```

Previously this raised `IntegrityError` and deleted the committed snapshot's object
(`OBJECT GONE` / `MISSING_EVIDENCE`). The fix is sound on both axes: the pre-read plus
`ON CONFLICT (source_snapshot_id) DO NOTHING` makes the write idempotent, and the
`raw_created` / `redacted_created` flags make the compensating delete unreachable for
objects this call did not create.

### R3 — resolved as prescribed

`build_source_snapshot_service` now selects `GcsObjectStore` whenever
`GOOGLE_OAUTH_ACCESS_TOKEN` / `ODP_AUDIT_WORM_GCS_TOKEN` / `GOOGLE_APPLICATION_CREDENTIALS`
is present, and raises when `ODP_OBJECT_STORE=gcs` is set without credentials.
`ODP_OBJECT_STORE`, `ODP_RESIDENCY_APPROVED_BUCKETS` and the token variables are
documented in the rollout runbook (§8 Deployment Configuration). This is exactly the
round-2 remedy ("default to the real store when credentials are present, and add the
deployment configuration to the runbook"); see NB-2 for the residual.

Round-2 NB-1 (mode-agnostic bucket override) was also fixed: `check_bucket_residency`
now rejects unknown modes first and prefers the per-mode
`ODP_RESIDENCY_APPROVED_BUCKETS_<MODE>` variable.

### R4 / suite status — verified green on `fbc42995`

```
python3 -m pytest -k "intake or snapshot or retrieval" -p no:randomly \
  --ignore=tests/contract/test_assisted_listing_openapi.py \
  --ignore=tests/contract/test_assisted_listing_operations.py
-> 223 tests, 0 failures, 0 errors, 21 skipped

python3 -m pytest tests/integration/test_assisted_listing_snapshots.py \
  tests/security/test_assisted_listing_snapshot_residency.py \
  tests/reliability/test_assisted_listing_intake_jobs.py -q
-> all pass (2 skipped: requires_live_env)

python3 -m ruff check modules/external_data shared/infrastructure/object_store tests
-> All checks passed!
```

The two ignored contract files fail collection on `origin/dev` as well (missing
`openapi_spec_validator`), unrelated to this branch. A wider
`pytest tests/security tests/reliability` run has one failure,
`test_supply_chain_security_gate.py::test_npm_audit_passes` (npm advisory
GHSA-3jxr-9vmj-r5cp in `brace-expansion`) — pre-existing and unrelated to this branch.

## Non-blocking (fix during closeout or as follow-ups)

1. **NB-1 (evidence)** `completion_evidence.md` still carries a second output block
   (`10 passed in 2.86s`) attributed to a live/ephemeral PostgreSQL environment that is
   not reproducible here (no `pgserver`, no `INTAKE_TEST_DATABASE_URL`, no local
   PostgreSQL binaries). Either name the environment that produced it or delete the block
   and keep only the verified `8 passed, 2 skipped` result. Also note that even the live
   run exercises SQL only — the object store in that test is `InMemoryObjectStore`.
2. **NB-2** `build_source_snapshot_service` still silently falls back to
   `InMemoryObjectStore` when neither `ODP_OBJECT_STORE` nor any credential is set, so a
   misconfigured production process persists `gs://` URIs for bytes that die with the
   process. Recommend a required explicit setting (or an `ODP_ENV=production` fail-closed
   check) plus wiring `ODP_OBJECT_STORE=gcs` into the deployment manifests, not only the
   runbook.
3. **NB-3** The `patch_synthetic_dns` fixture in `tests/conftest.py` is `autouse=True`
   repo-wide and monkeypatches a production module for every test in the suite. Prefer
   scoping it to the intake/retrieval test modules, or injecting the resolver through the
   existing `resolver`/`fetcher` constructor seams.
4. **NB-4** No regression test pins the R2 behaviour (re-capture of identical content must
   return the existing snapshot id and must not delete the object). The fix is verified by
   hand here; a test would keep it fixed.
5. **NB-5** Section 1 of `completion_evidence.md` still describes the round-1 residency
   implementation ("does not contain `taiwan` or `tw`"), which the bucket allowlist
   replaced. The reliability-test contract change is recorded in the commit body but not
   in the evidence. Both are stale-doc issues.
6. Round-2 non-blockers 2–4 (hard-coded `bucket="taiwan-snapshots"` and all-zero tenant
   fallback in `worker.py`, silent `except: pass` blocks, duplicate source-policy
   resolution in `DefaultRetrievalFetcher`) remain open.

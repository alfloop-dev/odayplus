# ODP-DEPLOY-MIGRATION-COMPATIBILITY-PROBE-001 — verification

Owner: Claude2 · Reviewer: Codex6 · Branch:
`task/ODP-DEPLOY-MIGRATION-COMPATIBILITY-PROBE-001` · Base SHA
`7d13f8e162d035ad7318d1f659dfa0f2bd85ca65`

## Focused compatibility tests

```
python3 -m pytest tests/ops/test_cloud_run_live_deployment.py -q \
  -k "compat or probe_retry or probe_json or probe_failure"
```

→ **26 passed, 291 deselected**. Covers:

- `ProbeRetryPolicy` backoff is exponential and capped (`[0, 2, 4, 8, 8, 8]`)
  and the policy rejects a zero/negative attempt count, per-attempt timeout,
  backoff, or deadline, so an unbounded retry contract cannot be configured
- `probe_failure_is_transient` retries only outcomes with no verdict —
  transport errors and `{408, 429, 502, 503, 504}` without a JSON body — and
  never retries a payload the old revision returned, nor invalid JSON at 200
- cold-start recovery: `[timeout, timeout, 200]` passes the gate with exactly
  3 attempts and backoff `[2.0, 4.0]`
- exhausted attempts fail closed with `outcome=attempts_exhausted`, and no
  `version` / `health` payload is written to the report
- non-200 version, invalid JSON, non-object JSON, missing database, unhealthy
  database, `{"status": "unhealthy"}`, a forbidden `sqlite` marker, and a
  non-`{200, 503}` health status all fail closed on **attempt 1** with **no
  sleep**
- `probe_with_bounded_retry` stops on the total deadline before the attempt cap
  (fake clock: 2 attempts, not 10) and clamps the attempt timeout to the
  remaining deadline
- `probe_json_endpoint` never raises and classifies the transport failure
- the deploy script wires all five bounded-retry flags into
  `run_migration_compatibility_gate`, keeps the defaults 4 / 120 s, and still
  runs the gate before `gcloud run deploy "${API_SERVICE}"`
- the CLI records `probe_retry_policy` in the report and fails closed with
  `compatibility:retry_policy` on an unbounded policy

## Full ops suite

```
python3 -m pytest tests/ops/ -p no:cacheprovider
```

→ **371 passed, 1 failed, 20 skipped**.

The single failure is
`test_deploy_preflight_imports_runtime_dependencies_via_locked_python`, which
fails with `Error: required command 'uv' is not installed.` — the `uv` binary
is absent from this worker sandbox. Confirmed pre-existing: the same test fails
identically on the unmodified base commit (`git stash` + re-run). It passes in
CI, where `uv` is installed.

## Lint

```
python3 -m ruff format --check scripts/deployment/validate_cloud_run_live_deployment.py \
                              tests/ops/test_cloud_run_live_deployment.py
python3 -m ruff check .orchestrator scripts        # ci.yml lint job
python3 -m ruff check tests modules apps shared models solver pipelines infra
bash -n scripts/deploy_cloud_run_waji.sh
```

→ all clean.

## Live gate re-runs against Cloud Run

All three ran the shipped CLI with the exact flags
`run_migration_compatibility_gate` now passes.

### 1. Serving revision — passes (`gate-rerun-live-serving-revision.json`)

`--api-url https://oday-api-7sxbjoeozq-de.a.run.app` (revision
`oday-api-00005-gin`, the revision the failed deploy was probing):

```
Cloud Run migration compatibility smoke passed.   exit=0
version: attempts=1 status=200 elapsed=0.062s
health:  attempts=1 status=503 elapsed=1.946s  -> database compatible
```

### 2. Cold revision — retry recovers it (`gate-rerun-cold-revision-retry.json`)

`--api-url https://authcand---oday-api-7sxbjoeozq-de.a.run.app`, an idle
0 %-traffic tagged revision, i.e. the exact condition of run 30402570022:

```
Cloud Run migration compatibility smoke passed.   exit=0
version attempt 1: The read operation timed out   (15.059s, transient=true)
version attempt 2: status=200                     (3.006s)
health  attempt 1: status=503                     (0.679s) -> database compatible
```

Attempt 1 reproduces the original failure string verbatim; the bounded retry
turns it into a pass in 18.1 s of probing plus one 2 s backoff. **This is the
end-to-end proof that the shipped code fixes the reported failure.**

### 3. Fail-closed still holds (`gate-rerun-fail-closed-invalid-json.json`)

Pointing `--api-url` at the web service, whose `/platform/version` answers 200
with HTML:

```
Cloud Run migration compatibility smoke failed (fail-closed):   exit=1
- compatibility:/platform/version:http: ... did not return valid JSON ... (attempts=1 elapsed=3.2s)
- compatibility:/platform/health:database: ... did not return valid JSON ... (attempts=1 elapsed=0.2s)
```

Invalid JSON at status 200 is a verdict, not a blip: one attempt, no retry,
exit 1.

## Scope guard

- The retry helpers (`ProbeAttempt`, `ProbeRetryPolicy`, `ProbeResult`,
  `probe_json_endpoint`, `probe_with_bounded_retry`,
  `probe_failure_is_transient`) are only reachable from
  `compatibility_smoke_checks`. `smoke_checks`, `cloud_run_job_checks`, and
  `preflight_checks` are untouched, so model readiness, external provider
  readiness, secret handling, and the Package 10 runtime gates keep their
  existing single-attempt contracts.
- `_json_request` is unchanged and still serves the other gates.

## One tightening beyond the retry contract

Acceptance requires "unhealthy database ... remain fail closed". It did not:
the old check was `"healthy" in json.dumps(database).lower()`, and the string
`"unhealthy"` contains `"healthy"`, so a database reporting **unhealthy passed
the gate**. `_database_reads_healthy` replaces the substring probe with a
positive allowlist (`healthy` / `ok` / `up` / `pass` / `passed`) over the
declared status token, reading a nested `{"status": ...}` mapping as well as a
bare string, and still rejects forbidden fixture/mock/seed/in-memory/sqlite
markers. This only tightens the gate; the live serving revision reports the
bare string `"healthy"` and still passes (re-run 1 above).

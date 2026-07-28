# ODP-DEPLOY-MIGRATION-COMPATIBILITY-PROBE-001 — runtime evidence

Task: Remediate migration compatibility probe timeout without weakening
fail-closed deployment.

Owner: Claude2 · Reviewer: Codex6 · Collected: 2026-07-28

## 1. What failed

Deploy Dev run
[30402570022](https://github.com/alfloop-dev/odayplus/actions/runs/30402570022)
at exact SHA `7d13f8e162d035ad7318d1f659dfa0f2bd85ca65`:

```
2026-07-28T22:08:34.788Z Execution [oday-migration-r-7d13f8e162d0-zg2jr] has successfully completed.
2026-07-28T22:08:40.467Z Cloud Run migration Job smoke passed.
2026-07-28T22:09:10.699Z Cloud Run migration compatibility smoke failed (fail-closed):
2026-07-28T22:09:10.699Z - compatibility:/platform/version:http: The read operation timed out
2026-07-28T22:09:10.699Z - compatibility:/platform/health:database: The read operation timed out
2026-07-28T22:09:10.712Z Deployment failed; restoring the recorded API/Web traffic split.
```

The 30.2 s between the migration-job smoke passing and the gate failing is
exactly two 15.0 s client timeouts — the `compatibility-smoke` default
`--timeout` applied once to `/platform/version` and once to
`/platform/health`, with no retry.

## 2. Correlation

`cloud-run-compat-correlation-run-30402570022.json` holds the raw Cloud Run
request, platform, and application log entries for `oday-api` between
22:06:00Z and 22:12:00Z, pulled with:

```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="oday-api"
   AND timestamp>="2026-07-28T22:06:00Z" AND timestamp<="2026-07-28T22:12:00Z"' \
  --project alfaloop-data-project --limit 500 --format json
```

Correlated timeline for the old revision `oday-api-00005-gin` (the revision
that still carried 100 % of dev traffic):

| Time (UTC) | Source | Fact |
| --- | --- | --- |
| 22:08:40.718 | request log | `GET /platform/version` arrives — **status 200, latency 28.121974842 s** |
| 22:08:40.739 | platform log | `Starting new instance. Reason: AUTOSCALING - ... no existing capacity for current traffic.` |
| 22:08:55.896 | request log | `GET /platform/health` arrives (the probe had just abandoned `/platform/version` at its 15 s timeout) — status 503, response 6100 B |
| 22:09:10.029 | platform log | `Started server process [1]` / `Application startup complete` / `Default STARTUP TCP probe succeeded` |
| 22:09:10.075 | application log | `HTTP GET /platform/version ok`, `correlation_id=corr-cloud-run-compat-dev-7d13f8e162d035ad7318d1f659dfa0f2bd85ca65`, `result=ok`, 200 |
| 22:09:13.512 | application log | `HTTP GET /platform/health ok`, same correlation id, 503 aggregate |

The exact deploy correlation id appears in the old revision's own application
logs with `result=ok` for both probes. The old revision answered both probes
correctly; the client had already given up.

Revision scaling config (`gcloud run revisions describe oday-api-00005-gin`):
`maxScale=2`, **no `minScale` annotation**, `startup-cpu-boost=true`,
`containerConcurrency=40`, `timeoutSeconds=300`. With `minScale` unset the old
revision is allowed to scale to zero, and dev has no organic traffic, so the
compatibility probe is reliably the first request to a cold container.

## 3. Transient cold start, not database incompatibility

Two independent measurements separate the two hypotheses.

**Warm serving revision** (`warm-serving-revision-probe-2026-07-28.json`,
22:19:42Z, same URL, same headers as the gate):

- `/platform/version` → **200 in 0.062 s**
- `/platform/health` → **503 in 1.312 s** with `dependencies.database == "healthy"`,
  `job_queue == "healthy"`, `external_providers.status == "healthy"` / `mode == "live"`

`compatibility_smoke_checks` accepts `/platform/health` status in `{200, 503}`
and only requires `dependencies.database` to read healthy, so the old revision
is compatible with the migrated schema. The 503 aggregate is the old revision's
pre-existing state, not a database verdict.

**Cold-start reproduction** (`cold-start-retry-reproduction-2026-07-28.json`)
against the idle, 0 %-traffic tagged revision
`https://probe8---oday-api-7sxbjoeozq-de.a.run.app`, per-attempt timeout 15.0 s:

| Attempt | Path | Result |
| --- | --- | --- |
| 1 | `/platform/version` | `TimeoutError: The read operation timed out` after **15.052 s** |
| 2 (after 2 s backoff) | `/platform/version` | **200 in 1.992 s** |
| 3 (after 2 s backoff) | `/platform/health` | 503 in 0.890 s, `database == "healthy"` |

Total 21.9 s. Attempt 1 reproduces the run-30402570022 failure string exactly;
attempt 2 shows a single bounded retry is sufficient once the instance is up.

**Verdict: transient Cloud Run cold start of the old revision. No database
incompatibility, no provider-probe defect, no probe logic defect other than the
missing retry contract.**

## 4. Remediation shipped

`compatibility_smoke_checks` now drives each probe through
`probe_with_bounded_retry`, a bounded, independently testable contract:

- per-attempt timeout `--timeout` (default 15.0 s), unchanged
- `--compat-retry-attempts` (default 4), `--compat-retry-backoff-seconds`
  (default 2.0, doubling, capped by `--compat-retry-max-backoff-seconds` 8.0)
- `--compat-retry-deadline-seconds` (default 120.0) — a finite total deadline
  per probe; a retry is only scheduled when backoff plus a full attempt still
  fit inside it, and the last attempt's timeout is clamped to what remains

Retries are attempted **only** for outcomes that carry no verdict from the old
revision: transport failures (timeout, connection reset/refused) and the
bounded infrastructure status set `{408, 429, 502, 503, 504}` **when the body is
not a JSON object**. Anything the old revision actually answered is a verdict
and is never retried, so all of the following still fail closed on the first
response, before candidate traffic and with the rollback trap armed:

- non-200 `/platform/version`
- `/platform/version` or `/platform/health` returning invalid JSON or a
  non-object payload at status 200
- `/platform/health` with a missing `database` dependency
- `/platform/health` with an unhealthy database or a forbidden
  fixture/mock/seed/in-memory/sqlite marker
- exhausted attempts or exhausted deadline

Worst case per probe is 4 × 15 s of attempts plus 2 + 4 + 8 s of backoff = 74 s,
inside the 120 s deadline; two probes bound the gate at ~148 s.

Model readiness, external provider readiness, secret handling, and the Package
10 runtime gates are untouched — the retry helper is only reachable from
`compatibility_smoke_checks`.

## 5. Verification

See `verification-2026-07-28.md`.

# ODP-DEPLOY-MIGRATION-COMPATIBILITY-PROBE-001: migration compatibility probe closeout evidence

Owner: Claude2 · Reviewer: Codex6 · Phase: Live Runtime Deployment ·
2026-07-28

Remediate the migration compatibility probe timeout without weakening
fail-closed deployment.

## 1. Diagnosis before any behaviour change

Deploy Dev run
[30402570022](https://github.com/alfloop-dev/odayplus/actions/runs/30402570022)
at exact SHA `7d13f8e162d035ad7318d1f659dfa0f2bd85ca65` failed the
`migration-compatibility-smoke` gate with both probes reporting
`The read operation timed out`, 30.2 s apart — two 15.0 s single-attempt
timeouts.

Correlating `corr-cloud-run-compat-dev-7d13f8e162d035ad7318d1f659dfa0f2bd85ca65`
against Cloud Run request, platform, and application logs for `oday-api`
(project `alfaloop-data-project`, region `asia-east1`) shows the old revision
`oday-api-00005-gin` **answered both probes correctly**:

- 22:08:40.718 `GET /platform/version` arrives → **status 200, latency 28.12 s**
- 22:08:40.739 `Starting new instance. Reason: AUTOSCALING - ... no existing
  capacity for current traffic.`
- 22:09:10.03 `Application startup complete` / `Default STARTUP TCP probe
  succeeded` — ~29.3 s of container start
- 22:09:10.075 and 22:09:13.512 the application logs both probes with the exact
  deploy correlation id and `result=ok`

The revision carries `maxScale=2` and **no `minScale`**, and dev has no organic
traffic between deploys, so the compatibility probe is reliably the first
request to a cold container.

Two independent measurements rule out database incompatibility:

- warm re-probe of the same serving revision → `/platform/version` 200 in
  0.062 s, `/platform/health` 503 in 1.312 s with
  `dependencies.database == "healthy"` (the 503 aggregate comes from the old
  revision's other dependencies and is explicitly accepted by the gate)
- cold-start reproduction against an idle 0 %-traffic tagged revision →
  attempt 1 `TimeoutError: The read operation timed out` at 15.052 s,
  attempt 2 after a 2 s backoff → 200 in 1.992 s

**Verdict: transient Cloud Run cold start. No database incompatibility, no
provider-probe defect.** Full detail and raw log extracts:
`docs/evidence/runtime/ODP-DEPLOY-MIGRATION-COMPATIBILITY-PROBE-001/`.

## 2. What shipped

`scripts/deployment/validate_cloud_run_live_deployment.py`

- `ProbeAttempt` / `ProbeRetryPolicy` / `ProbeResult`, `probe_json_endpoint`,
  `probe_with_bounded_retry`, `probe_failure_is_transient` — a bounded,
  independently testable retry contract: at most `attempts` requests of
  `timeout` seconds each with exponential capped backoff, and never past a
  finite `deadline_seconds` measured from the first attempt. A retry is only
  scheduled when its backoff plus a full attempt still fit inside the deadline;
  the attempt timeout is clamped to whatever remains. Both exhaustion modes
  fail the gate.
- Retryable **only** when the old revision returned no verdict: transport
  failures, and `{408, 429, 502, 503, 504}` when the body is not a JSON object.
  Anything the old revision answered is a verdict and is never retried.
- `compatibility_smoke_checks` drives both probes through that contract and
  records `probe_retry_policy`, `version_probe`, and `health_probe` (per-attempt
  status, error, elapsed, transient classification) in the gate report.
- New `compatibility-smoke` flags: `--compat-retry-attempts` (4),
  `--compat-retry-backoff-seconds` (2.0),
  `--compat-retry-max-backoff-seconds` (8.0),
  `--compat-retry-deadline-seconds` (120.0). An invalid policy fails the gate
  closed with `compatibility:retry_policy`.
- `_database_reads_healthy` replaces a substring test that let a database
  reporting **`"unhealthy"` pass the gate** (`"unhealthy"` contains
  `"healthy"`). See § 4.

`scripts/deploy_cloud_run_waji.sh`

- `run_migration_compatibility_gate` passes all five bounds explicitly, sourced
  from overridable `MIGRATION_COMPAT_*` variables with defaults 15 / 4 / 2 / 8 /
  120, so the worst-case gate duration is auditable at the call site. Gate
  ordering is unchanged: it still runs after the migration job and before
  `gcloud run deploy "${API_SERVICE}"`, with the rollback trap armed.

## 3. Fail-closed surface preserved

All of these still fail on the **first** response, before candidate traffic:

| Condition | Behaviour |
| --- | --- |
| non-200 `/platform/version` | 1 attempt, no retry, gate fails |
| invalid JSON / non-object payload at 200 | 1 attempt, no retry, gate fails |
| `/platform/health` outside `{200, 503}` | gate fails |
| missing `database` dependency | 1 attempt, no retry, gate fails |
| unhealthy database | 1 attempt, no retry, gate fails |
| forbidden fixture/mock/seed/in-memory/sqlite marker | gate fails |
| attempts or deadline exhausted | gate fails, no `version`/`health` in report |

Not touched: model readiness, external provider readiness, secret handling, and
the Package 10 runtime gates. The retry helpers are reachable only from
`compatibility_smoke_checks`; `smoke_checks`, `cloud_run_job_checks`,
`preflight_checks`, and `_json_request` are unchanged.

## 4. One tightening beyond the retry contract

Acceptance requires an unhealthy database to remain fail-closed. It did not:
the check was `"healthy" in json.dumps(database).lower()`, and `"unhealthy"`
contains `"healthy"`, so the exact verdict this gate exists to catch passed it.
`_database_reads_healthy` now uses a positive allowlist over the declared
status token (bare string or nested `{"status": ...}`) and still rejects
forbidden data markers. This only tightens the gate — the live serving revision
reports the bare string `"healthy"` and still passes.

## 5. Verification

`docs/evidence/runtime/ODP-DEPLOY-MIGRATION-COMPATIBILITY-PROBE-001/verification-2026-07-28.md`

- focused compatibility tests: 26 passed
- full ops suite: 371 passed, 20 skipped, 1 pre-existing environmental failure
  (`uv` binary absent from the worker sandbox; identical on the unmodified base)
- `ruff format --check`, `ruff check .orchestrator scripts`,
  `ruff check tests modules apps shared models solver pipelines infra`,
  `bash -n scripts/deploy_cloud_run_waji.sh` — clean
- shipped CLI re-run against the real serving revision → **pass**
- shipped CLI re-run against a genuinely cold revision → attempt 1 reproduces
  `The read operation timed out` at 15.059 s, attempt 2 returns 200 in 3.006 s,
  gate **passes**
- shipped CLI re-run against a non-API endpoint → **fails closed, exit 1**, one
  attempt per probe

## 6. Follow-ups (not in this task's scope)

- Setting `minScale=1` on the serving API revision would remove the cold start
  entirely, at a standing cost. The retry contract is the correct fix
  regardless, since a probe of a scale-to-zero revision can always be first.
- After this merges, `ODP-P10-DEV-REDEPLOY-VERIFY-001` is re-dispatched to
  Antigravity3 on the exact merged `dev` SHA.

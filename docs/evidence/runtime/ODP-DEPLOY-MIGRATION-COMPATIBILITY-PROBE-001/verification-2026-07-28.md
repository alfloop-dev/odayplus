# ODP-DEPLOY-MIGRATION-COMPATIBILITY-PROBE-001 — verification

Owner: Claude2 · Reviewer: Codex6 · Branch:
`task/ODP-DEPLOY-MIGRATION-COMPATIBILITY-PROBE-001` · Base SHA
`7d13f8e162d035ad7318d1f659dfa0f2bd85ca65`

## Focused compatibility tests

```
python3 -m pytest tests/ops/test_cloud_run_live_deployment.py -q -k "compat or probe"
```

→ **74 passed**. Covers:

- `ProbeRetryPolicy` backoff is exponential and capped (`[0, 2, 4, 8, 8, 8]`)
  and the policy rejects a zero/negative attempt count, per-attempt timeout,
  backoff, or deadline, so an unbounded retry contract cannot be configured
- `ProbeRetryPolicy` also rejects `nan` / `inf` / `-inf` on every bound, and the
  CLI turns each of those into a fail-closed report rather than a traceback —
  see § Round 3 below
- `probe_failure_is_transient` retries **only** a no-response outcome
  (transport failure / timeout). The status sweep asserts non-retry for
  `{200, 404, 408, 429, 500, 502, 503, 504}` across all three
  received-response provenances (`json_object`, `unparseable_body`,
  `non_object_body`) — see § Round 2 below — and for `invalid_request`, the
  no-response provenance that must not be retried — see § Round 4 below
- a malformed `--api-url` is rejected on attempt 1 with no sleep and no
  traceback, both directly and through the CLI — see § Round 4 below
- `probe_json_endpoint` classifies a real HTTP 503 carrying an HTML body, an
  empty body, `[]`, or `"unavailable"` as a *received response*
  (`response_received=True`, `transient=false`), and `ProbeAttempt` raises on a
  provenance that contradicts its status/payload
- end-to-end through `compatibility_smoke_checks`: a 503 with an unparseable or
  non-object body gives `outcome=rejected`, `attempt_count=1`,
  `transient=false`, **no sleep**, one request per probe, and no
  `version`/`health` payload in the report
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

→ **410 passed, 1 failed, 20 skipped**.

The single failure is
`test_deploy_preflight_imports_runtime_dependencies_via_locked_python`, which
fails with `Error: required command 'uv' is not installed.` — the `uv` binary
is absent from this worker sandbox. Confirmed pre-existing: the same test fails
identically on the unmodified base commit (`git stash` + re-run). It passes in
CI, where `uv` is installed. It executes `scripts/deploy_cloud_run_waji.sh`,
which round 4 did not touch.

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

All four ran the shipped CLI with the exact flags
`run_migration_compatibility_gate` now passes. **Re-run at round-4 head**
(2026-07-29T00:02–00:05Z) after the `invalid_request` change; the reports in
this directory are those runs' raw output, not an earlier round's.

### 1. Serving revision — cold start reproduced and recovered (`gate-rerun-live-serving-revision.json`)

`--api-url https://oday-api-7sxbjoeozq-de.a.run.app` (revision
`oday-api-00005-gin`, the revision the failed deploy was probing):

```
Cloud Run migration compatibility smoke passed.   exit=0
version attempt 1: The read operation timed out   (15.067s, provenance=no_response, transient=true)
version attempt 2: status=200                     (1.948s,  provenance=json_object)
health  attempt 1: status=503                     (3.654s,  provenance=json_object)
         dependencies.database == "healthy"  -> database compatible
```

The serving revision had scaled to zero again and blew straight through the
15 s single-attempt budget that run 30402570022 died on — attempt 1 reproduces
the original failure string verbatim, on the original revision, at this head.
The bounded retry then turns it into a pass in 17.0 s of probing plus one 2 s
backoff. **This is the end-to-end proof that the shipped code fixes the
reported failure**, and it is a live measurement, not a fixture.

### 2. Idle 0 %-traffic revision — still passes (`gate-rerun-cold-revision-retry.json`)

`--api-url https://live-3875485e---oday-api-7sxbjoeozq-de.a.run.app`
(revision `oday-api-00003-lez`), an idle 0 %-traffic tagged revision:

```
Cloud Run migration compatibility smoke passed.   exit=0
version attempt 1: status=200  (11.528s, provenance=json_object)
health  attempt 1: status=503  (0.122s,  provenance=json_object) -> database compatible
```

This run answered **11.5 s** into the 15 s attempt budget, so it did not
reproduce the timeout — which is itself the point: cold-start latency on these
revisions straddles the single-attempt budget, sometimes inside it (11.5 s
here, 13.3 s at round 2) and sometimes outside it (15.1 s at rounds 1–2, and
re-run 1 above at this head). A single attempt is a coin flip; the bounded
retry is what makes the gate deterministic. Rounds 1–3 recorded the timeout →
retry → 200 shape on this URL and on the `authcand` tag; nothing in the retry
contract is revision-specific.

### 3. Fail-closed still holds (`gate-rerun-fail-closed-invalid-json.json`)

Pointing `--api-url` at the web service, whose `/platform/version` answers 200
with HTML:

```
Cloud Run migration compatibility smoke failed (fail-closed):   exit=1
- compatibility:/platform/version:http: ... did not return valid JSON ... (attempts=1 elapsed=3.3s)
- compatibility:/platform/health:database: ... did not return valid JSON ... (attempts=1 elapsed=0.3s)
version_probe.outcome = rejected  attempt_count=1  provenance=unparseable_body  transient=false
health_probe.outcome  = rejected  attempt_count=1  provenance=unparseable_body  transient=false
```

A body we cannot parse is a defect, not a blip: one attempt, no retry, exit 1.

### 4. Unrequestable URL fails closed (`gate-rerun-fail-closed-unrequestable-url.json`)

`--api-url 'https://[::1'` with the default 4-attempt / 2 s-backoff policy:

```
Cloud Run migration compatibility smoke failed (fail-closed):   exit=1
- compatibility:/platform/version:http: https://[::1/platform/version is not a requestable URL: ValueError: Invalid IPv6 URL (attempts=1 elapsed=0.0s)
- compatibility:/platform/health:database: ... same ... (attempts=1 elapsed=0.0s)
version_probe.outcome = rejected  attempt_count=1  provenance=invalid_request  transient=false
health_probe.outcome  = rejected  attempt_count=1  provenance=invalid_request  transient=false
real 0m0.162s
```

Total wall clock 0.162 s under a policy that would have slept 2 + 4 + 8 s had
the rejection been classified transient — see § Round 4.

## Round 2 — Codex6 blocker on exact head `9e7d4c70`

PR #488, review comment 5110430397: `ProbeAttempt.payload=None` conflated a
transport/no-response failure with a *received* response whose body was
malformed or not an object. With the status allowlist that meant an **HTTP 503
carrying invalid JSON was retried** (`transient=true`) rather than rejected on
attempt 1.

Fixed by making provenance explicit and mandatory, and by narrowing the retry
rule to true no-response:

| Attempt outcome | `provenance` | `status` | `payload` | retried? |
| --- | --- | --- | --- | --- |
| transport failure / timeout | `no_response` | `None` | `None` | **yes** |
| response, body not JSON | `unparseable_body` | int | `None` | no |
| response, JSON not an object | `non_object_body` | int | `None` | no |
| response, JSON object | `json_object` | int | dict | no |

- `probe_failure_is_transient` is now `not attempt.response_received`. The
  status allowlist `{408, 429, 502, 503, 504}` is **deleted**: a status code is
  not independent proof that the Cloud Run front end, rather than the old
  revision, produced the response, and retrying on status could retry away a
  genuine 503 verdict from the old revision.
- `ProbeAttempt.__post_init__` enforces the table: a status is present exactly
  when a response was received, a payload exactly when the body parsed as a
  JSON object, and the provenance must be one of the four. A caller cannot
  re-create the conflation by hand.
- Nothing is lost against the reported failure: run 30402570022 failed with
  `The read operation timed out` — `no_response` — which is still retried.
  A front-end 503 with an HTML body now fails the gate closed, which is the
  required posture (a false fail aborts the deploy, rollback preserved).
- `version_probe` / `health_probe` report entries now carry `provenance`
  alongside `transient`, so the gate report shows *why* an attempt was or was
  not retried.

Round 1's regressions built `ProbeAttempt`s by hand, which is how the
conflation survived them. The new ones drive a real HTTP response through
`probe_json_endpoint` and `compatibility_smoke_checks`.

## Round 3 — Codex6 blocker on exact head `c583bd7f`

PR #488, review comment 5110667275: `ProbeRetryPolicy` accepted `NaN` and
infinity. Every guard in `__post_init__` was an ordering comparison, and **all
comparisons against `NaN` are false**, so `timeout_seconds=nan` passed
`<= 0` and `deadline_seconds=inf` passed `<= 0` — the finite-deadline contract
this policy exists to enforce was configurable away.

Reproduced on `c583bd7f` before the fix:

```
$ python3 scripts/deployment/validate_cloud_run_live_deployment.py \
    compatibility-smoke --api-url http://127.0.0.1:1 --web-url http://127.0.0.1:1 \
    --timeout nan --output /tmp/nanprobe.json
...
  File "/usr/lib/python3.12/socket.py", line 834, in create_connection
    sock.settimeout(timeout)
ValueError: Invalid value NaN (not a number)
```

The `ValueError` is raised by the socket layer *inside* `probe_json_endpoint`,
which only catches `(TimeoutError, urllib.error.URLError, OSError)`, so it
escapes past the `except ValueError` in `main` that exists to convert a bad
policy into a fail-closed report. Result: traceback, **no report file**, and a
deploy gate with no compatibility verdict to act on.

Fixed by validating finiteness before any bound is interpreted:

```python
for label, value in (("attempt count", self.attempts), ...):
    if not math.isfinite(value):
        raise ValueError(f"probe retry policy needs a finite {label}, got {value!r}")
```

placed **ahead of** the ordering guards, covering all five fields — attempt
count, per-attempt timeout, backoff, max backoff, and total deadline.

Same command after the fix:

```
Cloud Run migration compatibility smoke failed (fail-closed):   exit=1
- compatibility:retry_policy: probe retry policy needs a finite per-attempt timeout, got nan
report=/tmp/nanprobe.json
```

Regressions added:

- unit: `ProbeRetryPolicy` raises `ValueError` matching `finite` for each of
  `nan` / `inf` / `-inf` on each of the five bounds (15 cases)
- CLI subprocess: `--timeout`, `--compat-retry-backoff-seconds`,
  `--compat-retry-max-backoff-seconds`, and `--compat-retry-deadline-seconds`
  each with `nan` / `inf` / `-inf` (12 cases) must exit 1, emit **no
  `Traceback`**, and write a report whose first check is
  `compatibility:retry_policy` with `finite` in the detail. Passing the value as
  `--flag=-inf` is deliberate: bare `-inf` is parsed by argparse as an option
  string, not a value.

Round 2's regressions covered zero and negative bounds only, which is how the
non-finite hole survived them.

## Round 4 — Codex6 blocker on exact head `92de5735`

PR #488, review comment 5111023008: `probe_json_endpoint` catches
`(TimeoutError, urllib.error.URLError, OSError)`. A malformed HTTPS URL fails
*before* any of those: `urllib.request.Request` raises a bare `ValueError`, and
`http.client` raises `InvalidURL` — an `HTTPException`, not a `ValueError`.
Round 1 had moved the compatibility path off `_json_request`, whose callers
caught `ValueError`, so that boundary was lost.

Reproduced on `92de5735` before the fix:

```
$ python3 scripts/deployment/validate_cloud_run_live_deployment.py \
    compatibility-smoke --api-url 'https://[::1' --web-url 'https://[::1' \
    --output /tmp/badurl-before.json
...
  File ".../validate_cloud_run_live_deployment.py", line 1879, in probe_json_endpoint
    status, _content_type, body = _request(url, headers=headers, timeout=timeout)
  File "/usr/lib/python3.12/urllib/parse.py", line 514, in urlsplit
    raise ValueError("Invalid IPv6 URL")
ValueError: Invalid IPv6 URL
exit=2   # and no report file was written
```

Exit 2 with no report is not a fail-closed verdict: the deploy gate parses the
report, so it learns nothing about compatibility from a traceback.

Fixed with a fifth provenance, `invalid_request`, and a matching arm in
`probe_json_endpoint`:

| Attempt outcome | `provenance` | `status` | `payload` | retried? |
| --- | --- | --- | --- | --- |
| request could not be built | `invalid_request` | `None` | `None` | **no** |
| transport failure / timeout | `no_response` | `None` | `None` | yes |
| response, body not JSON | `unparseable_body` | int | `None` | no |
| response, JSON not an object | `non_object_body` | int | `None` | no |
| response, JSON object | `json_object` | int | dict | no |

- `except (ValueError, http.client.InvalidURL)` is ordered **before** the
  `OSError` arm and covers both families (`InvalidURL` is not a `ValueError`;
  `UnicodeEncodeError` on a non-latin-1 host is).
- `probe_failure_is_transient` is now `provenance == PROBE_NO_RESPONSE` rather
  than `not response_received`, so the new provenance is non-retryable by
  construction: nothing was sent, so there is no cold start to outlast, and
  every retry would rebuild the identical broken request and burn the deadline.
- `ProbeAttempt.__post_init__` keeps enforcing the table: `invalid_request`
  joins `no_response` as a provenance that must carry `status is None`, so a
  received response can never be recorded as an unbuildable request.

Same command after the fix (full output in
`gate-rerun-fail-closed-unrequestable-url.json`):

```
Cloud Run migration compatibility smoke failed (fail-closed):   exit=1
- compatibility:/platform/version:http: https://[::1/platform/version is not a requestable URL: ValueError: Invalid IPv6 URL (attempts=1 elapsed=0.0s)
report=...
```

Regressions added (8 cases, each parametrized over both exception families —
`https://[::1` → `ValueError`, `https://exa mple.invalid` →
`http.client.InvalidURL`; neither touches the network, since both are refused
before a socket is opened):

- direct: `probe_json_endpoint` returns `provenance=invalid_request`,
  `status=None`, `response_received=False`, `has_verdict=False`, and
  `probe_failure_is_transient(...) is False`
- direct: `probe_with_bounded_retry` under a 4-attempt / 2 s-backoff /
  120 s-deadline policy makes **exactly one attempt**, records
  `outcome=rejected` with `exhausted=""`, and **never calls `sleep`**
  (recording sleeper asserts `delays == []`)
- direct: `compatibility_smoke_checks` fails both checks closed, writes
  `attempt_count=1` / `outcome=rejected` / `transient=false` for both probes,
  never sleeps, and records no `version` / `health` payload
- CLI subprocess: exit **1**, **no `Traceback`** in stderr, a report file that
  exists and carries the default 4-attempt / 2 s-backoff policy plus both
  probes rejected on attempt 1, and a total wall clock under 10 s (a transient
  classification would have slept 2 + 4 + 8 s per probe)

Also extended: the `probe_failure_is_transient` sweep and the
`ProbeAttempt` provenance-contradiction test now cover `invalid_request`.

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

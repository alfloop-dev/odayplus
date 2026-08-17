# ODP-PLAN-OBSERVABILITY-LIVE-001 — CodexCoordinator Round 4 Review

- Implementation head reviewed: `dc3db7e2`
- Decision: `CHANGES_REQUESTED`
- Review scope: exact exported-series readback, independent watch signals,
  point-window coverage, and receipt integrity

## Blocking findings

1. Export readback does not prove the metrics that were written. The list
   filter contains only `release_sha`, and the validator accepts any non-empty
   `metric.type`. It never compares returned metric types and label sets to the
   exact series in the POST body, nor requires every exported series to be
   observed. It also parses point timestamps but does not check that they are
   inside the requested interval.

2. Watch status can be proved by one arbitrary positive counter. The query
   merely excludes `deployment_watch_window_status`; it does not require a
   defined set of health, error-rate, and latency metric types or apply their
   SLO thresholds. Any metric name not containing `error` or `failure` is
   treated as healthy when its value is positive.

3. A single point explicitly bypasses window coverage. The span check executes
   only when `len(point_timestamps) > 1`, so one point has
   `window_coverage_seconds=0` and still produces `WATCH_PASSED` for a
   caller-provided 16-minute interval.

4. The canonical receipt hash does not cover the complete proof. It omits the
   provider response, query parameters, point timestamps and values,
   calculated health/error outcomes, execution timestamp, and recorded
   timestamp. The verifier does not revalidate those omitted fields. Provider
   proof can therefore be changed to the circular status metric with value
   zero without invalidating verification.

## Reproduced fail-open behavior

At exact head `dc3db7e2`, independent mutations produced:

```text
EXPORT_ATTACKER_TYPE_OLD_POINT_PASS SUCCESS
  exported claim: api_request_count
  returned proof: custom.googleapis.com/attacker_metric
  point timestamp: 2000-01-01

WATCH_SINGLE_ARBITRARY_POINT_PASS WATCH_PASSED
  metric: custom.googleapis.com/attacker_counter
  verified_points_count: 1
  window_coverage_seconds: 0.0

TAMPERED_PROVIDER_PROOF_VERIFY_PASS WATCH_PASSED
  provider metric changed to deployment_watch_window_status
  provider point value changed to 0
```

## Round 5 exit criteria

- Query and verify each exact exported metric type and complete required label
  set, or perform an equivalent bounded readback that proves every POSTed
  series. Reject missing, extra/unexpected, stale, or out-of-window proof.
- Define an allowlisted independent watch signal contract (health,
  error-rate, latency) with explicit aggregation and thresholds. Do not infer
  health from arbitrary metric names or a generic positive value.
- Require timestamped provider observations that actually cover the full
  window at a defined cadence; one point must never prove 15 minutes.
- Hash the full canonical proof envelope, including query, provider points,
  metric types, values, timestamps, derived outcomes, and all verification
  bindings; revalidate them in `verify_watch_window_receipt()`.
- Add the reproduced mutations as tests.


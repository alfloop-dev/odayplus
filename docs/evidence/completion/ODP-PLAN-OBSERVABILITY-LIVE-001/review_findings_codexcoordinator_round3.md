# ODP-PLAN-OBSERVABILITY-LIVE-001 — CodexCoordinator Round 3 Review

- Implementation head reviewed: `fc1f8037e2a9e6d4e0db0fa1e856a9108b2144de`
- Decision: `CHANGES_REQUESTED`
- Review scope: provider-native readback, release/project/metric binding, point/window provenance, receipt integrity, and fail-closed mutation behavior

## Blocking findings

1. Metrics export readback still accepts an unrelated or incomplete time series.
   `ProductionMetricsExporter.export_metrics()` only requires a matching
   `metric.labels.release_sha`. It does not require the exact exported
   `metric.type`, a non-empty matching `resource.labels.project_id`, any
   points, point value, or point timestamp inside a bounded query interval.
   The GET filter also contains only the release label and no metric type or
   interval. A response with no metric type, no project ID, and `points: []`
   is reported as `SUCCESS`.

2. Watch-window proof is circular and still defaults caller evidence to
   healthy. `record_deployment_watch_window_status()` queries
   `deployment_watch_window_status` itself, accepts `observed_results=None`,
   fills it with zero errors and a passing health check, and permits a series
   with no metric type, no project ID, and no points. It consequently records
   `WATCH_PASSED` with `verified_points_count=0`. A status metric written or
   supplied by the same workflow cannot independently prove 15 minutes of
   healthy service behavior.

3. No provider point establishes the claimed 15-minute observation window.
   The start/end parameters are caller-provided, while returned point
   timestamps are neither required nor checked. The receipt therefore states
   a 15-minute window without evidence that Cloud Monitoring returned health,
   error-rate, or latency observations covering that window.

4. The generated receipt is not integrity-verifiable. Its hash is a truncated
   12-hex digest of only project, release SHA, caller duration, and point
   count. `verify_watch_window_receipt()` does not recompute that hash or
   revalidate metric type, project, points, timestamps, provider response, or
   query parameters.

## Reproduced fail-open behavior

At the exact reviewed head, a fake transport returned:

```json
{
  "timeSeries": [
    {
      "metric": {"labels": {"release_sha": "<expected-sha>"}},
      "resource": {"labels": {}},
      "points": []
    }
  ]
}
```

Both paths accepted it:

```text
EXPORT_FORGED_PASS SUCCESS
WATCH_FORGED_PASS WATCH_PASSED 0
VERIFY_FORGED_PASS WATCH_PASSED
```

## Round 4 exit criteria

- Bind each export readback query to the exact metric type, release SHA,
  project, and bounded interval; verify non-empty points with matching
  timestamps and values for every metric series claimed as exported.
- Derive watch status from independent health, error, latency, and deployment
  signals rather than querying the status metric being asserted. Remove
  default-passing observed results.
- Require Cloud Monitoring points whose timestamps establish the full
  observation window and reject missing project/type/points/timestamps.
- Hash the full canonical receipt with SHA-256 and have the verifier
  recompute it while validating all bound fields.
- Add the reproduced mutations as tests.


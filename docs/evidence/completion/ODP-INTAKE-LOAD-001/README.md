# ODP-INTAKE-LOAD-001 runtime evidence

This packet records measured execution of the assisted-listing intake durable
adapter. It does not replace staging Cloud SQL, Cloud Tasks, GCS, or Pub/Sub
evidence and does not enable any production flag. The quantitative values in
the reliability contract remain proposed because its named owner approvals are
still pending.

## Reproduce

```bash
uv run pytest tests/performance/assisted_listing_intake tests/reliability/assisted_listing_intake -q
python3 -m ruff check tests/performance/assisted_listing_intake tests/reliability/assisted_listing_intake scripts/load/assisted_listing_intake scripts/chaos/assisted_listing_intake
python3 scripts/load/assisted_listing_intake/run.py --volume 1000 --concurrency 100
python3 scripts/chaos/assisted_listing_intake/run.py
```

`load-report.json` is generated from real durable enqueue, lease, fenced status
update, parser-workload, queue-age, and review-KPI measurements. It evaluates
the 1,000-row batch maximum, 100-worker configured envelope, daily-volume
projection, availability, latency percentiles, queue age, parse completion,
review routing/completion, and sample-derived error-budget consumption.

`chaos-report.json` records provider latency, duplicate delivery, expired worker
lease/fence rejection, durable database reopen, object checksum inconsistency,
queue backlog, retry reset, and replay. RPO and RTO are calculated from the
measured drill timeline.

## Target disposition

| Gate | Result | Qualification |
|---|---|---|
| Local durable capacity and SLO | Pass | Exact values are in `load-report.json`. |
| Local recovery and error budget | Pass | Exact events and timings are in `chaos-report.json`. |
| Cloud SQL regional failover | Not executed | Local durable close/reopen is covered; staging regional failover evidence is still required. |
| GCS consistency/reconciliation | Not executed | Checksum fail-closed behavior is covered; real bucket generation/checksum reconciliation is still required. |
| Cloud Tasks/Pub/Sub backlog and DLQ | Not executed | Durable queue backlog/replay is covered; managed-service evidence is still required. |
| Owner approval of proposed SLO/RPO/RTO | Pending | Contract section 12 remains fail-closed. |

These qualifications are deliberately retained as missed production-readiness
targets; this task does not weaken or reinterpret them as passed.

# Worker App

Event consumers and asynchronous workers. Workers should call module
application services and infrastructure adapters, then emit audit/job status.

The worker image's existing Cloud Run Job entrypoint also supports an audit-only
publisher, using the same image and persistence bundle:

```bash
python product_ops/deployment/cloud_run_job_entrypoint.py audit-publisher \
  --run-for-seconds 60 --max-jobs 100
```

Its atomic claim selects only `canonical-audit-delivery`,
`store-ops-audit-delivery` and `durable-audit-delivery`. Other queued or expired work remains untouched. It
keeps polling during an empty queue until the observation window expires or the
job bound is reached, and exports metrics during idle polls. Existing worker
retry, heartbeat, fence and default-registry receipt checks still apply.

This command must run in separately reserved capacity to protect audit delivery
from busy general workers. Starting it in the general worker slot does not
reserve capacity. Window and batch bounds are operational settings, not proof
of an approved throughput/SLO. Provisioning, schedule overlap, credentials,
least-privilege grants, live load/recovery and release admission follow
`docs/runbooks/canonical-audit-delivery.md`.

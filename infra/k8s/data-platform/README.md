# ODay Plus Data Platform GKE Runtime

This package deploys the committed production Mongo-to-PostgreSQL data plane to
the existing `oday-dev` namespace. It never contains credentials, creates a mock
source, or permits a backfill before the database migration receipt is complete.

## Required existing resources

- Kubernetes ServiceAccount `oday-data-platform`, mapped to a Google service
  account with `roles/cloudsql.client`.
- Secret `oday-data-platform-runtime`, keys `mongodb-uri` and
  `postgres-password`.
- Secret `oday-data-platform-status-mapping`, key `status_mapping.json`, containing
  the owner-approved mapping contract.
- Cloud SQL instance connection name and database/user selected by the operator.

The Mongo and PostgreSQL runtime Secret keys are present in `oday-dev`. The governed
`oday-data-platform-status-mapping` Secret was not present when this package was
authored; it remains a deployment prerequisite and is never synthesized from a
default mapping.

## Immutable image

Both the Python base and the pushed data-platform image must be digest-pinned.
The image revision must equal the exact repository commit:

```bash
docker build \
  --file infra/docker/data-platform.Dockerfile \
  --build-arg PYTHON_BASE_IMAGE='python@sha256:<approved-digest>' \
  --build-arg ODP_RELEASE_SHA='<40-char-git-sha>' \
  --tag 'asia-east1-docker.pkg.dev/<project>/<repo>/data-platform:<git-sha>' \
  .
```

After push, resolve the repository digest and render the deployment. Rendering
fails for image tags, short SHAs, invalid Cloud SQL names, or a manual window
greater than one day:

```bash
python infra/k8s/data-platform/render.py \
  --release-sha '<40-char-git-sha>' \
  --data-image 'asia-east1-docker.pkg.dev/<project>/<repo>/data-platform@sha256:<digest>' \
  --cloud-sql-proxy-image 'gcr.io/cloud-sql-connectors/cloud-sql-proxy@sha256:<digest>' \
  --cloud-sql-instance '<project>:<region>:<instance>' \
  --postgres-user postgres \
  --postgres-database postgres \
  --manual-start '2026-07-23T00:00:00Z' \
  --manual-end '2026-07-24T00:00:00Z' \
  --output /tmp/oday-data-platform.yaml
```

## Migration-before-backfill

The first document is an independent migration Job. It:

1. runs Alembic `0001 -> 0002`;
2. applies the checksum-guarded Assisted Listing Intake `001 -> 004` stack,
   including canonical compatibility;
3. applies PostgreSQL runtime migration `000008`;
4. creates the `data_plane` control schema;
5. runs the Assisted Intake schema validator and verifies required relations;
6. writes `odp_runtime.deployment_migration_receipts` with release SHA, image
   digest, schema versions, manifest checksum, verification status, and receipt
   checksum.

The scheduled and manual jobs query that durable receipt using their own release
SHA and image digest before Mongo is read. Missing, failed, stale, or different
image lineage fails closed.

Apply only after a server-side dry run. After creation, wait for the migration
Job and inspect its termination receipt before relying on the schedule:

```bash
kubectl apply --dry-run=server -f /tmp/oday-data-platform.yaml
kubectl apply -f /tmp/oday-data-platform.yaml
kubectl -n oday-dev wait \
  --for=condition=complete \
  'job/oday-data-platform-migrate-<first-12-sha>' \
  --timeout=3600s
kubectl -n oday-dev get pod \
  -l app.kubernetes.io/component=migration \
  -o jsonpath='{.items[0].status.containerStatuses[0].state.terminated.message}'
```

## Workload boundaries and evidence

The daily CronJob loads only bounded merchant, place, device, daily operations,
orders, AI revenue, commercial inputs, and KMeans lineage. The runtime fixes the
window to the previous UTC day, one partition, and source-specific hard limits.

Trade and device logs are separate suspended Jobs. They require an explicitly
rendered positive window of at most one day and are hard-limited to 100,000
records. An operator must review the window and then intentionally unsuspend the
selected Job. They are never scheduled.

Each successful backfill requires every per-source reconciliation result to be
`SUCCEEDED` and `reconciled=true`. Full receipts are written to Pod logs, compact
receipts are exposed as Kubernetes termination messages, and durable run,
checkpoint, quarantine, checksum, and lineage evidence remains in `data_plane`.

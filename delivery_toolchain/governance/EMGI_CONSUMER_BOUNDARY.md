# EMGI consumer boundary

The authoritative EMGI producer design and 45-task cross-repository execution manifest are merged in:

```text
repository: alfloop-dev/oday-data-platform
branch: dev
merge SHA: 97ade4945cd56d938ecf5ab9196fb0cd5d87a634
entry point: docs/design/emgi/v0.4/README.md
task manifest: docs/design/emgi/v0.4/tasks/manifest.json
```

ODay Plus is the product consumer. It owns Assisted Listing and Survey workflows, product API/BFF/UI, target-format context, physical feasibility, site economics, HeatZone, SiteScore, NetPlan, OpsBoard, final decision policy and audit.

ODay Plus does not own new provider connectors, provider credentials, source schedulers, raw external evidence, canonical market schemas, source DQ/coverage/lineage, or product-neutral market data products.

Existing `modules/external_data` code is a strangler-migration surface. Existing producer code may be modified for security, compatibility, instrumentation, migration or deletion, but new producer capabilities must be implemented in `oday-data-platform`.

The checked-in policy and CI workflow enforce the boundary for every PR to `dev`.

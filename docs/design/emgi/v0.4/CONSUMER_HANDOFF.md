---
doc_id: ODP-EMGI-CONSUMER-HANDOFF-004
version: 0.4.0
status: binding-consumer-handoff
platform_repository: alfloop-dev/oday-data-platform
platform_contract_release: oday-data-contracts.v0.4
consumer_repository: alfloop-dev/odayplus
consumer_baseline_sha: 04c3352630ea03a27d16c1bd43ef65e18e11f219
updated_at: 2026-08-14
---

# ODay Plus EMGI Consumer Handoff

## Binding boundary

`odayplus` is not the EMGI data producer.

It consumes versioned data products from `alfloop-dev/oday-data-platform` and owns product workflows and decisions.

### Keep in ODay Plus

- Assisted Listing and XLSX/manual intake.
- Human correction, identity review and Candidate promotion.
- Survey assignment, review and promotion.
- API/BFF/product authorization.
- Market Explorer, Site Dossier and Candidate Compare.
- Target format, physical feasibility and site economics.
- HeatZone, SiteScore, NetPlan and OpsBoard.
- Final decision policy and audit.

### Move to data platform

- provider/dataset/version registry;
- provider connectors and credentials;
- scheduled fetch;
- raw source snapshots/evidence;
- source observations/search coverage;
- canonical geography/POI/competitor/rent/mobility/traffic models;
- PostGIS/H3 persistence;
- market data-product materialization;
- source DQ/quarantine/replay/lineage.

## Immediate freeze

No new provider connector, endpoint, credential, source scheduler or canonical market table may be added to `odayplus`.

`modules/external_data` is a migration surface:

```text
KEEP_PRODUCT_WORKFLOW
MOVE_PLATFORM
ADAPT_FACADE
SPLIT
DEPRECATE_AFTER_CUTOVER
```

The machine-readable disposition is in `LEGACY_EXTERNAL_DATA_DISPOSITION.yaml`.

## Consumer contract

ODay Plus pins `oday-data-contracts` and reads:

```text
emgi.coverage-surface.v1
emgi.market-cell-profile.v1
emgi.catchment-profile.v1
emgi.competitor-network.v1
emgi.rent-benchmark.v1
emgi.property-observation.v1
emgi.site-market-context.v1
emgi.data-acquisition-plan.v1
oday.store-reference.v1
oday.store-daily-performance.v1
oday.machine-capacity.v1
oday.store-coverage.v1
```

It does not read producer internal tables.

## Decision split

```text
platform site-market-context
+ ODay target-format snapshot
+ physical feasibility
+ unit economics
+ decision policy
= ODay product decision context
```

A platform market score is not an investment approval.

## Cutover

1. generated contract client;
2. read facade;
3. dual-run;
4. checksum/count/coverage/lineage reconciliation;
5. consumer restart readback;
6. remove provider credentials and schedulers;
7. delete legacy producer code after rollback window.

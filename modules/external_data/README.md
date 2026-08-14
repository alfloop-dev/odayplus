# External Data — ODay Plus consumer boundary

`modules/external_data` is a legacy migration and product-workflow surface.

Authoritative EMGI source ingestion, provider registry, raw evidence, canonical market models, orchestration, persistence, DQ, coverage and product-neutral data products belong to:

```text
alfloop-dev/oday-data-platform
```

Allowed here:

- Assisted Listing and manual/XLSX intake;
- product authorization and human review;
- generated data-platform contract client;
- product-facing read facade;
- migration/cutover compatibility code.

Forbidden here:

- new provider connectors;
- provider credentials;
- scheduled source ingestion;
- raw source snapshot ownership;
- canonical market tables;
- direct SiteScore/HeatZone/NetPlan/UI provider calls.

See `docs/design/emgi/v0.4/CONSUMER_HANDOFF.md`.

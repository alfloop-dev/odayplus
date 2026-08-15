# External & Internal Source Connector Matrix

## Purpose

This matrix is the product-grade catalogue of the source connectors that land
the data ODay Plus needs for end-to-end validation (`ODP-PV-003`). A *connector*
is the testable unit that turns one upstream dataset into landed, canonicalized
records: it runs the data-quality gate, maps accepted records to the Canonical
Data Model, enriches address-bearing records with geocode / H3, and preserves a
lineage envelope on every record. It composes the existing building blocks
(`ODP-DATA-02` exchange contracts, `ODP-DATA-03` connector spec, `ODP-DATA-05`
source-to-canonical mapping) rather than re-implementing them.

Framework: `modules/integration/connectors/` (base + internal connectors).
External connectors: `modules/external_data/connectors/`. Contracts:
`packages/schemas/source_contracts/`. Golden fixtures:
`tests/fixtures/source_data/`. Acceptance tests:
`tests/integration/test_external_source_connectors.py`.

## Connector matrix

| Source category | Contract id | Kind | Acquisition | Integration mode | Canonical target | Geocode / H3 | Connector |
|---|---|---|---|---|---|---|---|
| Store | `store_master_snapshot` | internal | internal | batch_snapshot | `store` | no | `SourceConnector` (mapper) |
| Machine | `machine_master_snapshot` | internal | internal | batch_snapshot | `machine` | no | `SourceConnector` (mapper) |
| Machine cycle | `machine_cycle_event` | internal | internal | incremental_batch | `machine_cycle` | no | `SourceConnector` (landed) |
| Machine status | `machine_status_event` | internal | internal | event_stream | `machine_status_event` | no | `SourceConnector` (landed) |
| Transaction | `transaction_event` | internal | internal | incremental_batch | `transaction` | no | `SourceConnector` (mapper) |
| Pricing | `price_schedule_snapshot` | internal | internal | batch_snapshot | `price_schedule` | no | `SourceConnector` (landed) |
| Maintenance | `maintenance_work_order_event` | internal | internal | incremental_batch | `work_order` | no | `SourceConnector` (landed) |
| Customer service | `customer_service_case_event` | internal | internal | incremental_batch | `customer_service_case` | no | `SourceConnector` (landed) |
| POI | `poi_snapshot` | external | api | batch_snapshot | `poi` | yes | `PoiConnector` |
| Competitor | `competitor_store_snapshot` | external | manual | batch_snapshot | `competitor_store` | yes | `CompetitorStoreConnector` |
| Listing | `listing_raw_snapshot` | external | feed | batch_snapshot | `listing` | yes | `ListingConnector` |
| Admin boundary | `admin_boundary_snapshot` | external | public_dataset | batch_snapshot | `geo_cell` | centroid → H3 | `AdminBoundaryConnector` |
| Geocode | `geocode_result_snapshot` | external | api | api_lookup | `address_location` | yes | `GeocodeConnector` |

"mapper" connectors produce a typed canonical entity through the shared
source-to-canonical mapper with deterministic identity resolution. "landed"
connectors validate and preserve the record with its lineage envelope; their
typed canonical mapping is owned by the downstream Integration mapper and is out
of scope for this connector pass. External connectors additionally build typed
entities directly and run geocode / H3 enrichment.

## Lineage envelope

Every `ConnectorRecord` carries a `RecordLineage` that preserves the provenance
fields required by `ODP-DATA-03 §9` / `ODP-DATA-05`:

| Field | Meaning |
|---|---|
| `source_system` / `source_id` | originating source system / record-level source id |
| `source_record_id` | natural key from `source_*_id` (e.g. `source_poi_id`) |
| `canonical_target` | canonical entity the record maps to |
| `mapping_id` | source-to-canonical mapping id (e.g. `MAP-EXT-POI-v1`) |
| `schema_version` | contract `schema_version`, falling back to the registry version |
| `event_time` / `observation_time` | business / observation time parsed from the record |
| `ingestion_time` | when the connector landed the record |
| `field_lineage` | per-field `(canonical_field, source_field, source_value)` provenance |
| `quarantine_reasons` | canonical `ODP-DATA-05 §8` reasons for rejected records |

## Data-quality gate

Records are validated against their contract before canonicalization. Rejected
records are quarantined (never canonicalized) and their lineage carries the
canonical quarantine reasons: `missing_required_field`, `schema_mismatch`,
`invalid_time`, `invalid_amount`. Deterministic golden good/bad fixtures for
every contract live under `tests/fixtures/source_data/` and are exercised by the
contract tests and the connector acceptance tests.

## Usage

```python
from modules.external_data.connectors import build_external_connectors
from modules.external_data.geo import GeoPipeline, StaticGeocodeProvider

connectors = build_external_connectors(geo_pipeline=GeoPipeline(StaticGeocodeProvider({})))
run = connectors["poi_snapshot"].ingest(records, ingestion_time=now)
canonical_pois = run.canonical_entities()        # typed Poi entities
quarantined = run.quarantined                     # rejected records + reasons
```

Internal connectors are built the same way via
`modules.integration.connectors.build_internal_connectors()`.

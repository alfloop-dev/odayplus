# Source Data Contracts

Declarative contracts for the raw data the ODay Plus platform ingests, before it
is mapped into the Canonical Data Model. They are the testable inputs that the
Integration Layer (internal / IoT data) and the External Data Platform (external
connectors) validate and canonicalize.

Design references: `ODP-DATA-02` (IoT/internal exchange contract), `ODP-DATA-03`
(external connector spec), `ODP-DATA-05` (source-to-canonical mapping).

## Layout

```
index.json                     registry of every contract + taxonomy reference
envelopes/batch_envelope.json  common Batch/Snapshot exchange envelope (ODP-DATA-02 §3)
envelopes/event_envelope.json  Event-stream exchange envelope (ODP-DATA-02 §6.1)
internal/<dataset>.json        internal / IoT dataset contracts
external/<dataset>.json        external connector dataset contracts
```

## Contract shape

Each contract is a JSON document with metadata:
- Core identity: `contract_id`, `kind` (`internal|external|envelope`), `source_system`, `source_dataset`, `canonical_target`, `mapping_id`
- Delivery & exchange: `integration_mode` (`batch_snapshot|incremental_batch|event_stream|backfill|api_lookup`), `envelope` (`batch|event`), optional `acquisition_method`
- Data catalog & governance metadata:
  - `data_owner`: named business/engineering team responsible for data quality; unconfirmed entries explicitly marked `"unconfirmed"` or `"unknown"`. Empty whitespace fake owners are rejected.
  - `target_latency_sla`: target freshness/latency SLA (e.g. `"PT1H"`, `"24h"`, `"realtime"`, or `"unconfirmed"`).
  - `contact_channel` / `contact_ref`: contact channel or communication ref for alerts/queries (e.g. `"slack:#data-ops"`, `"unconfirmed"`).
  - `runtime_capability`: machine-readable execution capability status (e.g. `"unverified"`, `"batch_watermark_only"`, `"streaming_supported"`, `"manual_attestation"`, `"verified"`), explicitly decoupled from declared `integration_mode` to prevent false readiness.
- Fields & rules: `fields` array (`name`, `type`, `required`, optional `enum`, `minimum`, `invalid_code`, `description`) and optional `invariants` (`time_order`).

## Using them

The contracts are loaded and validated by the dependency-free engine in
`modules/integration/domain/contracts.py`, exposed through the
`modules.integration.application.internal_contracts` and
`modules.external_data.application.external_contracts` facades:

```python
from modules.integration.application.internal_contracts import internal_contract
from modules.integration.domain.contracts import validate_record

contract = internal_contract("transaction_event")
result = validate_record(contract, record)
if not result.ok:
    quarantine(record, reasons=result.quarantine_reasons())  # ODP-DATA-05 §8
```

Golden valid/invalid fixtures live in `tests/fixtures/source_data` and are
exercised by `tests/contract/test_ingestion_contracts.py`.

## Scope

This layer validates field-level conformance (presence, type, enum, basic
time/amount sanity) and produces a quarantine decision. Cross-entity DQ
(referential integrity, row-count reconciliation, identity resolution) is
downstream and out of scope here.

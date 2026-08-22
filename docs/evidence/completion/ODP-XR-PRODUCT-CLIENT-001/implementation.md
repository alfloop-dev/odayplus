# ODP-XR-PRODUCT-CLIENT-001 — Implementation

**Task:** ODP-XR-PRODUCT-CLIENT-001 (Pin and generate the ODay EMGI product-contract client)
**Owner:** Antigravity · **Reviewer:** Claude · **Phase:** IMPLEMENTATION

## Objective

Consume decision-ready EMGI data product contracts released by `alfloop-dev/oday-data-platform` (published in `contracts/releases/emgi/product/` via `XR-CONTRACTS-PRODUCT-001`), pin exact release metadata and cryptographic checksums, expose them via runtime diagnostics, and enforce CI compatibility gates without importing or copying producer internal storage DDL or relation catalogs.

## Architecture & Design Principles

1. **Consume the Released Product Package, Not Producer Tables**:
   - Upstream package `oday-data-product-contracts.v0.4.1` is vendored directly into `packages/oday_data_product_contracts_client/_release/` (`release.json`, `compatibility.json`, `schemas.json`, `dependency-closure.json`).
   - Producer internal implementation tables (`storage-schema.sql`) and relation ownership schemas (`relation-ownership.yaml`) are explicitly forbidden from the client repository and verified absent.
2. **Exact Pinning with Content Digests**:
   - `config/oday_data_product_contracts.toml` pins the exact package ID (`oday-data-product-contracts.v0.4.1`), producer repository (`alfloop-dev/oday-data-platform`), producer commit SHA (`245aa00e417b6f8450baa608e8976584e00be6fc`), content digests, and all 7 product contracts.
3. **Runtime Diagnostics & Verification**:
   - `product_version()`, `product_contracts()`, and `diagnostics()` in `packages.oday_data_product_contracts_client` expose the release metadata, commit SHA, and verified inventory for runtime monitoring.
4. **CI Compatibility Gate**:
   - `verify_release()` validates that every contract schema, semantic version, and breaking change flag satisfies the pin. Any breaking changes or drift raise `IncompatibleContractError`.
5. **Deterministic Pure-Python Code Generation**:
   - `codegen.py` renders pure-Python dataclass models with `from_dict()` and `to_dict()` methods, supporting recursive `$defs` resolution and container models (`SiteMarketContextDocument`, `CatchmentProfileDocument`, `MarketCellProfileDocument`, `CoverageSurface`, `DataAcquisitionPlan`, `FieldSurveyDocument`, `PropertyObservationDocument`).

## Pinned Contracts Inventory

| Contract ID | Category | Version | Schema File | Model / Root Class |
|---|---|---|---|---|
| `emgi.field-survey.v1` | `source_evidence` | `1.0.0` | `schemas/field-survey.schema.json` | `FieldSurveyDocument` |
| `emgi.property-observation.v1` | `domain_observation` | `1.0.0` | `schemas/property-observation.schema.json` | `PropertyObservationDocument` |
| `emgi.coverage-surface.v1` | `decision_product` | `1.0.0` | `schemas/coverage-surface.schema.json` | `CoverageSurface` |
| `emgi.market-cell-profile.v1` | `decision_product` | `1.0.0` | `schemas/market-cell-profile.schema.json` | `MarketCellProfileDocument` |
| `emgi.catchment-profile.v1` | `decision_product` | `1.0.0` | `schemas/catchment-profile.schema.json` | `CatchmentProfileDocument` |
| `emgi.site-market-context.v1` | `decision_product` | `1.0.0` | `schemas/site-market-context.schema.json` | `SiteMarketContextDocument` |
| `emgi.data-acquisition-plan.v1` | `decision_product` | `1.0.0` | `schemas/data-acquisition-plan.schema.json` | `DataAcquisitionPlan` |

## Changes by Component

- **Configuration:**
  - `config/oday_data_product_contracts.toml`: Pins release metadata, SHA256 digests, artifact digests, and contract specifications.
- **Client Package:**
  - `packages/oday_data_product_contracts_client/_release/`: Vendored release artifacts.
  - `packages/oday_data_product_contracts_client/errors.py`: Contract exception hierarchy (`ArtifactDigestError`, `IncompatibleContractError`, `PinError`, `GeneratedClientStaleError`).
  - `packages/oday_data_product_contracts_client/pin.py`: TOML pin parser and model classes.
  - `packages/oday_data_product_contracts_client/release.py`: Verified release bundle loader.
  - `packages/oday_data_product_contracts_client/compatibility.py`: Schema compatibility and drift verifier.
  - `packages/oday_data_product_contracts_client/diagnostics.py`: Runtime diagnostic exposure functions.
  - `packages/oday_data_product_contracts_client/codegen.py`: Python code generator for consumer models.
  - `packages/oday_data_product_contracts_client/models/`: Generated pure-Python consumer dataclasses.
  - `packages/oday_data_product_contracts_client/__init__.py`: Public API exports.
  - `packages/oday_data_product_contracts_client/README.md`: Package documentation and usage instructions.
- **Contract Tests:**
  - `tests/contract/test_oday_data_product_contract_pin.py`: Comprehensive 40-test CI gate testing pin validation, artifact tamper resistance, drift/incompatibility rejection, code generator freshness/determinism, and JSON schema round-trip serialization.

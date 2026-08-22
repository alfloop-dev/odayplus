# ODP-XR-PRODUCT-CLIENT-001 — Verification

**Task:** ODP-XR-PRODUCT-CLIENT-001 (Pin and generate the ODay EMGI product-contract client)
**Owner:** Antigravity · **Reviewer:** Claude · **Phase:** IMPLEMENTATION

## Acceptance Criteria Verification

### 1. Generate consumer models only from the released product package
- **Vendored Release Integrity:** Pinned artifacts in `packages/oday_data_product_contracts_client/_release/` match upstream SHA256 checksums (`test_vendored_artifacts_match_their_pinned_checksums`, `test_edited_release_artifact_is_rejected`).
- **Absence of Producer Tables/DDL:** Producer storage DDL (`storage-schema.sql`) and relation ownership schemas (`relation-ownership.yaml`) are strictly excluded, and package directory contains 0 SQL files or DDL statements (`test_producer_implementation_tables_are_not_vendored`, `test_smuggling_producer_ddl_into_the_bundle_is_rejected`).
- **Generated Consumer Models:** Models are generated solely from the released contract schemas, are deterministic, and round-trip successfully against JSON schemas (`test_generated_client_is_current`, `test_generation_is_deterministic`, `test_generated_*_round_trips`).

### 2. Pin exact product release version/checksum and expose it through runtime diagnostics
- **Exact Release Pin:** `config/oday_data_product_contracts.toml` pins package ID `oday-data-product-contracts.v0.4.1`, producer commit SHA `245aa00e417b6f8450baa608e8976584e00be6fc`, and exact SHA256 digests (`test_pin_names_the_released_product_package`, `test_pin_records_sha256_digests`).
- **Runtime Diagnostics:** `product_version()`, `product_contracts()`, and `diagnostics()` report verified metadata and contract inventory in JSON-serializable format (`test_runtime_reports_the_exact_product_version`, `test_runtime_diagnostics_are_json_serialisable`, `test_runtime_contract_inventory_matches_the_pin`).

### 3. Fail CI on incompatible product schemas without copying producer implementation tables
- **Incompatibility Failure Gates:** Tests verify that schema edits, contract removals, version bumps, moved schema files, declared breaking changes, and unpinned new contracts raise `IncompatibleContractError` (`test_edited_product_schema_fails`, `test_removed_product_contract_fails`, `test_bumped_contract_version_fails`, `test_moved_schema_file_fails`, `test_declared_breaking_change_fails`, `test_new_unpinned_product_contract_fails`).

## Test Results

```bash
$ uv run pytest tests/contract/test_oday_data_product_contract_pin.py -v
============================== 40 passed in 2.37s ==============================
```

```bash
$ uv run python -m packages.oday_data_product_contracts_client.codegen --check
Generated product client matches oday-data-product-contracts.v0.4.1
```

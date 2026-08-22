# ODay Data-Platform Product Contracts Client

Task **ODP-XR-PRODUCT-CLIENT-001** / contract `odayplus.data-platform-product-client.v1`.

This package is the consumer client for the *decision-ready* EMGI product
contracts released by `alfloop-dev/oday-data-platform` (task `XR-CONTRACTS-PRODUCT-001`).

## Design Principles

1. **Consume the release, not producer tables**:
   The client resolves contracts from the vendored release bundle under `_release/`.
   Producer storage DDL (`storage-schema.sql`) and relation catalogs (`relation-ownership.yaml`)
   are strictly excluded.

2. **Pin exact release version & checksums**:
   `config/oday_data_product_contracts.toml` pins the exact release, commit SHA, and
   SHA256 digests. Runtime diagnostics (`product_version()`, `diagnostics()`) expose these
   verified properties.

3. **Fail CI on incompatible schemas**:
   `tests/contract/test_oday_data_product_contract_pin.py` enforces backward compatibility
   and fails if schemas drift, change version, or declare breaking changes.

## Code Generation

Regenerate client models:

```bash
uv run python -m packages.oday_data_product_contracts_client.codegen --write
```

Check client freshness:

```bash
uv run python -m packages.oday_data_product_contracts_client.codegen --check
```

## Running Verification

```bash
uv run pytest tests/contract/test_oday_data_product_contract_pin.py -q
```

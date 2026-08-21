# `oday_data_contracts_client`

Consumer client for the **released** ODay data-platform EMGI foundation package.

- Contract provided: `odayplus.data-platform-foundation-client.v1`
- Contract consumed: `oday-data-foundation-contracts.v0.4.1`
- Task: `ODP-XR-CLIENT-001`

## What this is

`alfloop-dev/oday-data-platform` publishes a versioned foundation release under
`contracts/releases/emgi/foundation/`. odayplus consumes that release — it does
not re-implement it, and it does not copy the producer's tables.

| Path | Role |
| --- | --- |
| `config/oday_data_contracts.toml` | The pin: release identity, producer commit, artifact checksums, per-contract digests. The only place a producer version is named. |
| `_release/` | The release artifacts, vendored verbatim from the pinned commit so CI resolves contracts without network access. |
| `models/` | Generated consumer models, one module per pinned contract. |
| `codegen.py` | The generator that produces `models/`. |
| `compatibility.py` | The fail-closed gate: release drift raises `IncompatibleContractError`. |
| `diagnostics.py` | Runtime exposure of the exact foundation version. |

## What this deliberately is not

`storage-schema.sql` and `relation-ownership.yaml` ship in the upstream release
and are **not** vendored here. They are the producer's internal PostgreSQL DDL
and writer catalog. Copying producer implementation tables into odayplus is the
coupling this client exists to remove, so `release.py` fails if either file
appears under `_release/`.

## Runtime use

```python
from packages.oday_data_contracts_client import foundation_version, diagnostics
from packages.oday_data_contracts_client.models.store_reference import StoreReference

version = foundation_version()
# oday-data-foundation-contracts.v0.4.1 (semver 0.4.1) from alfloop-dev/oday-data-platform@3f0bd995bbd2

store = StoreReference.from_dict(row)
payload = store.to_dict()
```

`foundation_version()` verifies before it answers: artifact checksums first,
then the released catalog against the pin. A process that can report a version
is a process whose contracts were validated.

`diagnostics()` returns the same information as a JSON-serialisable block, for
a health or version endpoint.

## Moving the pin

1. Re-vendor `release.json`, `compatibility.json`, and `schemas.json` from the
   new producer commit into `_release/`.
2. Update `[release]`, `[source]`, `[compatibility]`, `[vendor.artifacts]`, and
   every `[[contracts]]` digest in `config/oday_data_contracts.toml`.
3. Regenerate:

   ```bash
   uv run python -m packages.oday_data_contracts_client.codegen --write
   ```

4. Verify:

   ```bash
   uv run pytest tests/contract/test_oday_data_contract_pin.py -q
   ```

Skipping any step fails the contract test, and therefore CI. That is the point:
an incompatible kernel or internal schema must break the build, not the runtime.

## Generated models

The generator maps JSON Schema onto frozen dataclasses and `str` enums, keeping
wire-level types (timestamps stay ISO-8601 strings). Each model has `from_dict`
and `to_dict`; `to_dict` omits schema-optional fields that are `None`, because
not every optional field is nullable.

Model files are checked in so the client needs no build step, and the contract
test regenerates them in memory and compares — a hand-edited model, or a pin
moved without regeneration, fails.

# ODP-XR-CLIENT-001 — completion evidence

Pin and generate the ODay data-platform foundation client.

- Owner: Claude2
- Reviewer: Antigravity
- Provides contract: `odayplus.data-platform-foundation-client.v1`
- Requires contract: `oday-data-foundation-contracts.v0.4.1`
- Source catalog: `alfloop-dev/oday-data-platform@63e9c2fc5171c0e335f6465f5860704fe4dc4694`
  (`docs/design/emgi/v0.4.1/tasks/definitions/consumer-a.json`)

## Pinned release

The foundation release published by `XR-CONTRACTS-001` is pinned at an exact
producer commit, not a branch:

| Field | Value |
| --- | --- |
| Release | `oday-data-foundation-contracts.v0.4.1` (semver `0.4.1`, status `PUBLISHED`) |
| Producer | `alfloop-dev/oday-data-platform@3f0bd995bbd2248a9cff9176f27ed0e39d25948f` |
| Release path | `contracts/releases/emgi/foundation` |
| Content digest | `22d71f4460db75dc8fb1f5bfe8cf253427af11954c8d68915935dfb47ffd87f6` |
| Pinned contracts | 13 (`platform_foundation` 1, `manifests` 1, `kernel` 6, `internal_analytical` 5) |

`3f0bd995` is the producer's `XR-CONTRACTS-001: harden foundation release and
align compatibility` commit — the current head of that release directory on the
producer's `dev`.

## Deliverables

| Path | Role |
| --- | --- |
| `config/oday_data_contracts.toml` | The pin. Release identity, producer commit, artifact checksums, and the canonical digest of every pinned contract schema. The only place a producer version is named. |
| `packages/oday_data_contracts_client/_release/` | `release.json`, `compatibility.json`, `schemas.json` vendored byte-for-byte from the pinned commit, so CI resolves contracts hermetically. |
| `packages/oday_data_contracts_client/pin.py` | Parses the pin; every structural problem is a `PinError`, never a fallback to "latest". |
| `packages/oday_data_contracts_client/release.py` | Loads the vendored bundle and checksum-verifies it against the pin. |
| `packages/oday_data_contracts_client/compatibility.py` | The fail-closed gate. Reports every drift, then raises `IncompatibleContractError`. |
| `packages/oday_data_contracts_client/codegen.py` | Generates the consumer models from the pinned schemas only. |
| `packages/oday_data_contracts_client/models/` | 13 generated modules: 51 frozen dataclasses with `from_dict` / `to_dict`, 37 `StrEnum` enumerations, 2 type aliases. |
| `packages/oday_data_contracts_client/diagnostics.py` | `foundation_version()` / `diagnostics()` — the runtime version surface. |
| `tests/contract/test_oday_data_contract_pin.py` | 32 contract tests: pin integrity, release consumption, drift regressions, codegen currency, runtime exposure. |

## Acceptance

### "Consume the released foundation package rather than copied producer tables"

The release bundle is the only schema source: `release.py` resolves each
contract by the release-relative `schema_file` key, and the test asserts the
canonical digest of every resolved schema equals the digest the producer's own
release catalog publishes.

The producer's implementation surfaces are provably absent. `storage-schema.sql`
(the PostgreSQL/PostGIS DDL for 31 relations) and `relation-ownership.yaml` (the
writer catalog) are part of the upstream release and are deliberately not
vendored. They are declared under `[vendor.excluded]`, `load_release()` raises
if either appears, and the test additionally scans the whole package for `.sql`
files and `CREATE TABLE` / `CREATE MATERIALIZED VIEW` text.

### "Fail CI on incompatible kernel/internal schemas"

`tests/contract/test_oday_data_contract_pin.py` runs in the CI product-suite job
(`uv run pytest ... tests modules apps shared models`), so each of these fails
the build rather than degrading at runtime:

| Producer change | Detected as |
| --- | --- |
| Kernel schema content edited | `schema content changed under the pin` (canonical digest mismatch) |
| Internal contract removed from the catalog | `contract is no longer published by the foundation release` |
| Contract version bumped | `contract version changed` |
| Schema file moved or renamed | `schema file moved` |
| Breaking change declared | `producer declared a breaking change the consumer has not accepted` |
| Release identity or content digest moved | `release identity changed` / `release content digest changed` |
| New kernel contract added upstream | `released contract is not pinned by the consumer` |
| A vendored artifact edited locally | `ArtifactDigestError` before any contract is read |
| Pin moved without regenerating models | `check_generated()` reports the stale module |

Each row has a test that mutates a copy of the release and asserts the raise;
the last two use a staged copy of `_release/` under `tmp_path`.

### "Expose the exact foundation version at runtime"

`foundation_version()` returns the release id, semver, content digest, producer
repository and commit, release path, publication timestamp, owning producer
task, and pinned-contract count. It verifies before it answers — artifact
checksums first, then the released catalog against the pin — so a process that
can report a version is a process whose contracts were validated.
`diagnostics()` returns the same data plus the per-contract inventory as a
JSON-serialisable block for a health or version endpoint.

## Adjacent changes and why they were required

- `config/code-boundaries.yaml` — `delivery_toolchain/governance/check_code_boundaries.py`
  fails CI on any tracked `.py` file that matches no boundary. `packages/` had
  no Python before this task, so the new client is classified explicitly under
  `product_system`. It is listed per package rather than as `packages/**` so the
  next Python surface under `packages/` still has to be classified on purpose.
- `docs/audits/code-boundary-inventory.csv` — generated by that same checker
  (`--write-inventory`); it is compared byte-for-byte, so it had to be
  refreshed alongside the manifest.

No file under this task's `forbidden_paths` was touched.

## Verification

```console
$ uv run pytest tests/contract/test_oday_data_contract_pin.py -q
................................                                         [100%]
32 passed in 0.80s
# exit 0

$ uv run python -m packages.oday_data_contracts_client.codegen --check
Generated foundation client matches oday-data-foundation-contracts.v0.4.1
# exit 0

$ uv run python delivery_toolchain/governance/check_code_boundaries.py
Code boundary checks passed for 863 files.
- archived: 14
- development_delivery_tooling: 57
- development_platform_system: 60
- evidence_artifact: 21
- product_operations_tooling: 27
- product_system: 414
- verification: 270
# exit 0

$ uv run python scripts/validate_external_data_boundary.py
contract: odayplus.legacy-external-data-disposition.v2
tracked files: 2542
  classified: 2542
  unclassified: 0
  frozen_files: 32
  capability_detections: 68
  provider_reference_hits: 218
external-data boundary: OK
# exit 0

$ uv run ruff check packages/oday_data_contracts_client tests/contract/test_oday_data_contract_pin.py
All checks passed!
# exit 0
```

## What this task deliberately did not do

`ODP-LEGACY-FACADE-001` owns replacing direct external ingestion with a read
facade over this client. No product module imports the client yet; this task
delivers the pinned, generated, verified client and its CI gate, and stops at
that boundary.

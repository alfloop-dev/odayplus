# Sidecar Diagnostic Packet: ODP-XR-PRODUCT-CLIENT-001

## 1. Packet Identity & Governance Metadata

| Field | Value |
|---|---|
| **Sidecar Task ID** | `ODP-XR-PRODUCT-CLIENT-001-SIDECAR-BLOCKED-TASK-DIAGNOSTICS` |
| **Parent Task ID** | `ODP-XR-PRODUCT-CLIENT-001` |
| **Parent Task Title** | `Pin and generate the ODay EMGI product-contract client` |
| **Helper Kind** | `blocked_task_diagnostics` |
| **Sidecar Owner / Reviewer** | `Antigravity6` / `Codex` |
| **Parent Owner / Reviewer** | `Antigravity` / `Claude` |
| **Target / Parent Branch** | `dev` / `task/ODP-XR-PRODUCT-CLIENT-001-SIDECAR-BLOCKED-TASK-DIAGNOSTICS` |
| **Parent Task Status** | `blocked` |
| **Declared Blocked Reason** | `waiting for dependencies: ODP-XR-CLIENT-001, XR-CONTRACTS-PRODUCT-001` |
| **Diagnostic Timestamp** | `2026-08-22` |
| **Scope Boundary** | Support artifact under `support/sidecars/ODP-XR-PRODUCT-CLIENT-001/` only. Strictly zero mutation of L1 canonical platform documents, core contracts, runtime tables, or governance policies. |

---

## 2. Executive Summary & Problem Context

This diagnostic packet provides an evidence-backed blocker analysis, dependency status breakdown, downstream blast radius mapping, unblocking protocol, and bounded verification report for parent task **`ODP-XR-PRODUCT-CLIENT-001`** (*"Pin and generate the ODay EMGI product-contract client"*).

### Problem Statement
`ODP-XR-PRODUCT-CLIENT-001` is the second-stage EMGI client integration in `odayplus`, responsible for pinning and generating the client library for the decision-ready product contracts published by `alfloop-dev/oday-data-platform`.

The parent task is currently in `blocked` status due to two declared dependencies:
1. **`ODP-XR-CLIENT-001`** (Foundation contract client in `odayplus`)
2. **`XR-CONTRACTS-PRODUCT-001`** (Decision-ready EMGI product contract release in `alfloop-dev/oday-data-platform`)

### Diagnostic Finding Summary
- **Internal Precondition Satisfied**: `ODP-XR-CLIENT-001` has completed successfully in `odayplus` (delivering `packages/oday_data_contracts_client/`, `config/oday_data_contracts.toml`, and 32 passing contract pin tests).
- **External True Blocker**: `XR-CONTRACTS-PRODUCT-001` in the upstream repository `alfloop-dev/oday-data-platform` remains **blocked** on 8 upstream contract producer tasks (`XR-CONTRACTS-001`, `DPF-SRC-SURVEY-001`, `DPF-DOM-PROPERTY-001`, `DPF-DP-COVERAGE-001`, `DPF-DP-MARKET-CELL-001`, `DPF-DP-CATCHMENT-001`, `DPF-DP-SITE-CONTEXT-001`, and `DPF-DP-ACQUISITION-001`).
- **Architectural Invariant**: Per `docs/design/emgi/v0.4.1/LEGACY_EXTERNAL_DATA_DISPOSITION.yaml` and L1 runtime policy, `odayplus` is strictly a consumer of released contracts and is forbidden from copying upstream implementation tables or raw SQL DDL (`storage-schema.sql`). Therefore, `ODP-XR-PRODUCT-CLIENT-001` cannot proceed until `XR-CONTRACTS-PRODUCT-001` is published upstream.

---

## 3. Parent Task Objectives & Architecture Boundary

### Parent Task Scope & Deliverables
Parent task `ODP-XR-PRODUCT-CLIENT-001` owns the following implementation paths:
- `packages/oday_data_product_contracts_client/`:
  - `_release/`: Vendored release artifacts (`release.json`, `compatibility.json`, `schemas.json`) from `alfloop-dev/oday-data-platform`.
  - `models/`: Generated frozen dataclasses and enums representing decision-ready product contracts.
  - `pin.py`, `release.py`, `compatibility.py`, `codegen.py`, `diagnostics.py`, `errors.py`: Contract parsing, checksum validation, fail-closed drift detection, and runtime diagnostic exposure.
- `config/oday_data_product_contracts.toml`:
  - Authoritative pin naming the upstream release ID (`oday-data-product-contracts.v0.4.1`), producer commit SHA, artifact checksums, and schema SHA-256 digests.
- `tests/contract/test_oday_data_product_contract_pin.py`:
  - Fail-closed test suite ensuring CI fails on any schema tampering, version drift, missing contracts, or unpinned additions.

### Forbidden Paths & Architectural Rules
Per task specification and boundary rules:
- **Forbidden Paths**:
  - `docs/design/emgi/v0.4.1/tasks/manifest.json`
  - `packages/generated/oday_data_contracts/`
  - `infra/db/migrations/`
  - `modules/external_data/providers/`
  - `modules/external_data/connectors/providers/`
  - `modules/external_data/workers/scheduled_fetch.py`
- **Vendoring Restrictions**:
  - Upstream PostgreSQL/PostGIS table schemas (`storage-schema.sql`) and relation ownership manifests (`relation-ownership.yaml`) must **never** be copied into `odayplus`.

---

## 4. Upstream Dependency Status & Blocker Audit

### Dependency 1: `ODP-XR-CLIENT-001` (Internal Foundation Client)
- **Repository**: `alfloop-dev/odayplus`
- **Status**: **DONE / COMPLETED** (Evidence: `docs/evidence/completion/ODP-XR-CLIENT-001/completion_evidence.md`)
- **Delivered Contracts**: `odayplus.data-platform-foundation-client.v1`
- **Pinned Release**: `oday-data-foundation-contracts.v0.4.1` (producer commit `3f0bd995bbd2248a9cff9176f27ed0e39d25948f`)
- **Verification**: `tests/contract/test_oday_data_contract_pin.py` (32 tests passing)
- **Verdict**: Satisfied. No blocking defect in the foundation client layer.

### Dependency 2: `XR-CONTRACTS-PRODUCT-001` (Cross-Repo Product Release) — Root Cause
- **Repository**: `alfloop-dev/oday-data-platform`
- **Status**: **BLOCKED**
- **Provides Contract**: `oday-data-product-contracts.v0.4.1`
- **Expected Artifacts Upstream**:
  - `oday-data-platform/contracts/releases/emgi/product/`
  - `oday-data-platform/src/oday_data_platform/contracts/product_release.py`
  - `oday-data-platform/scripts/build_emgi_product_contract_bundle.py`
  - `oday-data-platform/tests/contracts/test_emgi_product_contract_bundle.py`
- **Upstream Blocker Chain**:
  `XR-CONTRACTS-PRODUCT-001` is blocked waiting on 8 upstream tasks in `oday-data-platform`:
  1. `XR-CONTRACTS-001` (`oday-data-foundation-contracts.v0.4.1` — complete)
  2. `DPF-SRC-SURVEY-001` (`emgi.field-survey.v1`)
  3. `DPF-DOM-PROPERTY-001` (`emgi.property-observation.v1`)
  4. `DPF-DP-COVERAGE-001` (`emgi.coverage-surface.v1`)
  5. `DPF-DP-MARKET-CELL-001` (`emgi.market-cell-profile.v1`)
  6. `DPF-DP-CATCHMENT-001` (`emgi.catchment-profile.v1`)
  7. `DPF-DP-SITE-CONTEXT-001` (`emgi.site-market-context.v1`)
  8. `DPF-DP-ACQUISITION-001` (`emgi.data-acquisition-plan.v1`)

Until all decision-ready contracts in `oday-data-platform` are assembled, bundled, and published via `XR-CONTRACTS-PRODUCT-001`, the consumer release artifacts cannot be vendored into `odayplus`.

---

## 5. Dependency Graph & Downstream Blast Radius

```
[alfloop-dev/oday-data-platform]
  DPF-SRC-SURVEY-001 (emgi.field-survey.v1) ──────────┐
  DPF-DOM-PROPERTY-001 (emgi.property-observation.v1) ─┤
  DPF-DP-COVERAGE-001 (emgi.coverage-surface.v1) ──────┤
  DPF-DP-MARKET-CELL-001 (emgi.market-cell.v1) ────────┼──► XR-CONTRACTS-PRODUCT-001 (BLOCKED)
  DPF-DP-CATCHMENT-001 (emgi.catchment-profile.v1) ────┤      (Publish EMGI product bundle)
  DPF-DP-SITE-CONTEXT-001 (emgi.site-market.v1) ───────┤            │
  DPF-DP-ACQUISITION-001 (emgi.data-acq-plan.v1) ──────┘            │
                                                                    ▼ (Cross-repo dependency)
[alfloop-dev/odayplus]                                        ┌───────────────────────────────┐
  ODP-XR-CLIENT-001 (Foundation client: DONE) ───────────────►│  ODP-XR-PRODUCT-CLIENT-001   │ (BLOCKED)
                                                              └───────────────┬───────────────┘
                                                                              │
         ┌────────────────────────────┬──────────────────────────────┬────────┴───────────────────────────┐
         ▼                            ▼                              ▼                                    ▼
┌──────────────────┐        ┌──────────────────┐           ┌──────────────────┐                 ┌──────────────────┐
│ODP-LEGACY-FACADE │        │ODP-HEATZONE-V3   │           │ODP-FEASIBILITY   │                 │ODP-ECONOMICS     │
│ -001 (BLOCKED)   │        │ -001 (BLOCKED)   │           │ -001 (BLOCKED)   │                 │ -001 (BLOCKED)   │
└────────┬─────────┘        └──────────────────┘           └─────────┬────────┘                 └────────┬─────────┘
         │                                                           │                                   │
         ▼                                                           └─────────────────┬─────────────────┘
┌──────────────────┐                                                                   ▼
│XR-COMPAT-CI      │                                                        ┌──────────────────┐
│ -001 (BLOCKED)   │                                                        │ODP-SITESCORE-V3  │
└────────┬─────────┘                                                        │ -001 (BLOCKED)   │
         ▼                                                                  └──────────────────┘
┌──────────────────┐
│XR-CUTOVER-001    │
│ (BLOCKED)        │
└──────────────────┘
```

### Direct Downstream Impact in `odayplus`:
1. **`ODP-LEGACY-FACADE-001`** (*Replace direct external ingestion with a data-platform read facade*):
   - Blocked waiting on `ODP-XR-PRODUCT-CLIENT-001` to supply `odayplus.data-platform-product-client.v1`.
2. **`ODP-HEATZONE-V3-001`** (*Consume platform market profiles in HeatZone v3 shadow mode*):
   - Blocked waiting on `ODP-XR-PRODUCT-CLIENT-001` for `emgi.market-cell-profile.v1` and `emgi.catchment-profile.v1`.
3. **`ODP-FEASIBILITY-001`** (*Implement physical site feasibility decision gate*):
   - Blocked waiting on `ODP-XR-PRODUCT-CLIENT-001`.
4. **`ODP-ECONOMICS-001`** (*Implement target-format and monthly site economics simulator*):
   - Blocked waiting on `ODP-XR-PRODUCT-CLIENT-001` for `emgi.site-market-context.v1`.
5. **`ODP-SITESCORE-V3-001`** (*Implement SiteScore v3 separated components*):
   - Blocked waiting on `ODP-XR-PRODUCT-CLIENT-001`, `ODP-FEASIBILITY-001`, and `ODP-ECONOMICS-001`.
6. **`XR-COMPAT-CI-001`** & **`XR-CUTOVER-001`**:
   - Cross-repo compatibility CI and final ingestion cutover blocked downstream.

---

## 6. Unblocking Protocol & Implementation Blueprint

When `XR-CONTRACTS-PRODUCT-001` in `oday-data-platform` publishes the product contract release bundle:

### Step 1: Upstream Release Verification & Artifact Ingestion
1. Verify the release commit on `alfloop-dev/oday-data-platform@<PRODUCER_SHA>`.
2. Ensure `contracts/releases/emgi/product/` contains:
   - `release.json` (Contract ID: `oday-data-product-contracts.v0.4.1`)
   - `compatibility.json`
   - `schemas.json`
   - `checksums.sha256`
3. Copy only the three JSON artifacts to `packages/oday_data_product_contracts_client/_release/`.
4. Confirm `storage-schema.sql` and `relation-ownership.yaml` are **excluded**.

### Step 2: Authoritative Pin Configuration
Create `config/oday_data_product_contracts.toml` patterned after `config/oday_data_contracts.toml`:
```toml
schema_version = 1
client_contract = "odayplus.data-platform-product-client.v1"

[release]
id = "oday-data-product-contracts.v0.4.1"
name = "oday-data-product-contracts"
type = "product"
semantic_version = "0.4.1"
status = "PUBLISHED"
content_digest = "<PRODUCER_CONTENT_DIGEST>"
owner_task_id = "XR-CONTRACTS-PRODUCT-001"

[source]
repository = "alfloop-dev/oday-data-platform"
commit_sha = "<PRODUCER_SHA>"
release_path = "contracts/releases/emgi/product"

[vendor.artifacts]
"release.json" = "<SHA256>"
"compatibility.json" = "<SHA256>"
"schemas.json" = "<SHA256>"

[vendor.excluded]
"storage-schema.sql" = "producer PostgreSQL/PostGIS implementation tables"
"relation-ownership.yaml" = "producer relation ownership and writer catalog"
```

### Step 3: Codegen & Model Generation
1. Implement `packages/oday_data_product_contracts_client/` with:
   - `pin.py`, `release.py`, `compatibility.py`, `codegen.py`, `diagnostics.py`, `errors.py`.
2. Execute code generation:
   ```bash
   uv run python -m packages.oday_data_product_contracts_client.codegen --write
   ```
3. Verify models in `packages/oday_data_product_contracts_client/models/`.

### Step 4: Contract Pin & Regression Test Suite
1. Implement `tests/contract/test_oday_data_product_contract_pin.py` to assert:
   - Pin integrity and upstream SHA binding.
   - Exact digest matches across all product schemas (`emgi.field-survey.v1`, `emgi.property-observation.v1`, `emgi.coverage-surface.v1`, `emgi.market-cell-profile.v1`, `emgi.catchment-profile.v1`, `emgi.site-market-context.v1`, `emgi.data-acquisition-plan.v1`).
   - Rejection of SQL DDL / table copying.
   - Runtime version exposure via `product_version()` and `diagnostics()`.

### Step 5: Code Boundary Classification
1. Register `packages/oday_data_product_contracts_client/*.py` under `product_system` in `config/code-boundaries.yaml`.
2. Refresh inventory:
   ```bash
   python3 delivery_toolchain/governance/check_code_boundaries.py --write-inventory
   ```

---

## 7. Bounded Verification & Evidence Record

To confirm the current repository state, boundary conformance, and readiness of existing contract tooling without modifying canonical product behavior, the following test suites were executed:

### Verification Run 1: Foundation Contract Pin Suite
- **Command**: `/home/lupin/.local/bin/uv run --python 3.12 pytest tests/contract/test_oday_data_contract_pin.py -q`
- **Result**: `32 passed in 1.20s`
- **Finding**: Foundation client (`ODP-XR-CLIENT-001`) is robust, fully passing, and ready to compose with the product client.

### Verification Run 2: Architecture & External Data Boundary Suite
- **Command**: `/home/lupin/.local/bin/uv run --python 3.12 pytest tests/architecture/test_external_data_boundary.py tests/contract/test_oday_data_contract_pin.py -q`
- **Result**: `101 passed in 46.97s` (69 architecture boundary tests + 32 contract pin tests)
- **Finding**: Zero unauthorized provider references, zero unclassified legacy data paths, and complete boundary isolation.

### Verification Run 3: Whole-Repository Code Boundary Audit
- **Command**: `python3 delivery_toolchain/governance/check_code_boundaries.py`
- **Result**: `Code boundary checks passed for 865 files (product_system: 414, verification: 271, development_platform_system: 60, development_delivery_tooling: 58, product_operations_tooling: 27, evidence_artifact: 21, archived: 14)`
- **Finding**: Full code boundary conformance across all 865 tracked Python files.

---

## 8. Conclusion & Recommendation for Parent Task Owner

1. **State Preservation**: Retain `ODP-XR-PRODUCT-CLIENT-001` in `blocked` state until `XR-CONTRACTS-PRODUCT-001` completes upstream in `alfloop-dev/oday-data-platform`.
2. **Readiness**: The client architecture, codegen harness, boundary policy, and testing patterns proven in `ODP-XR-CLIENT-001` are ready for drop-in replication once the upstream product release bundle is published.
3. **No Workaround Authorization**: Do not attempt to mock or bypass product schemas by copying unreleased DDL from `oday-data-platform`, as doing so violates fail-closed boundary invariants.

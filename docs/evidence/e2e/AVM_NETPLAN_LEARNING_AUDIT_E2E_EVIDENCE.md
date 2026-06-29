# AVM, NetPlan, Learning Hub, and Audit Product E2E Evidence

PV-007 adds a product-grade E2E path for the governed AVM, NetPlan, Learning Hub, and Audit loop.

## Covered Path

- AVM case creation, margin normalization, valuation, finance approval, DataRoom creation, and DataRoom export.
- NetPlan scenario creation, OSS-backed constrained solve, approval, execution, outcome recording, and label-registry payload generation.
- Learning Hub dataset registration, model validation, content-addressed artifact storage, model card governance, canary, full release, rollback, and registry evidence generation.
- Audit event lookup by correlation id, decision-card evidence export, checksum generation, subsidy matrix completeness, and retained evidence readback.
- UI smoke coverage for AVM, NetPlan, Learning Hub, and Audit screens inside the OpsBoard shell.

## Canonical Spec

`tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts`

The spec uses correlation id `corr-pv007-avm-netplan-learning-audit` and validates real API writes before checking the UI surfaces.

## Product Runner

`scripts/e2e/run_product_e2e.sh` now includes the PV-007 spec in the Docker-backed product E2E suite.

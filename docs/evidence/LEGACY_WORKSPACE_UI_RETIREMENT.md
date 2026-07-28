# Legacy Workspace UI Retirement Record

- Retirement date: 2026-07-27
- Canonical design: Operator Console R7 — Package 10
  (`docs_archive/00_source_zips/operator_console/r7-20260720-package-10/`,
  `DEMO_STATE_VERSION: oday-plus-r7-20260720`, 40 screen labels)
- Decision basis: the product owner designated the Claude Design interactive
  prototype as the canonical visual-design package (2026-07-18 decision,
  reaffirmed for Package 10 on 2026-07-20 in
  `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE.md`).
- Retirement execution: branch `fix/package10-final-20260725`
  (`refactor(package10): retire legacy visual runtime` and the R3A/R3B
  follow-ups). Verification:
  `docs/evidence/fleet_dispatch/package10_20260726/ODP-P10-LEGACY-VISUAL-RETIREMENT-VERIFICATION.md`
  (result: `retirement_verified_at_head`). Authoritative deleted-path
  inventories:
  `docs/evidence/fleet_dispatch/package10_20260726/acks/ODP-P10-CAN-001-R3A.json`,
  `docs/evidence/fleet_dispatch/package10_20260726/acks/ODP-P10-CAN-001-R3B.json`.

## What was retired

The pre-R7 workspace UI line (June master-brief lineage: OpsBoard shell and
the avm / expansion / netplan / learninghub / operations / priceops / adlift /
intervention / audit workspaces) and its end-to-end specs. The R7 operator
console under `apps/web/features/operator/` is the sole executable UI line;
only the `/operator`, `/intake/[intakeId]`, and `/franchisee` pages remain.

## Why closeout queue entries reference this record

The closeout queues (`PRODUCT_RELEASE_CLOSEOUT_QUEUE.json`,
`PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json`) hold historical delivery
receipts whose evidence files belonged to the retired UI line. Those
deliveries happened and remain valid history; the evidence files are no
longer present in the working tree because the surfaces they exercised were
retired. Each affected `evidence_refs` entry now points to this record
instead of a deleted path. **No acceptance check was weakened**: the
referenced files are preserved verbatim in git history at the commit below.

## Evidence provenance

All retired evidence files are preserved at dev commit
`8e8e7e63` (last dev commit containing the full set). Retrieve any of them
with `git show 8e8e7e63:<path>`.

| Retired evidence path | Referencing closeout entries |
|---|---|
| `tests/e2e/opsboard-shell.spec.ts` | ODP-FE-R0-001 |
| `tests/e2e/e2e-api-bound-ui.spec.ts` | ODP-FE-R0-001 |
| `tests/e2e/e2e-map.spec.ts` | ODP-FE-EXP-001 |
| `tests/e2e/e2e-expansion-product.spec.ts` | ODP-FE-EXP-001 |
| `apps/web/features/avm/AvmWorkspace.tsx` | ODP-FE-ASSET-001 |
| `tests/e2e/e2e-avm-netplan.spec.ts` | ODP-FE-ASSET-001 |
| `tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts` | ODP-FE-ASSET-001, ODP-FE-LEARN-001 |
| `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts` | ODP-FE-OPS-001, ODP-FE-PRICE-001 |
| `tests/e2e/e2e-map-live-boundary.spec.ts` | ODP-MAP-STAGE-001, ODP-MAP-STAGE-002 |

## Boundary

This record documents evidence-file retirement only. It does not claim that
any retired task's functional scope is re-verified against the R7 console;
R7-scope verification lives with the Package 10 / ODP-INTAKE-UX evidence
under `docs/evidence/completion/` and the fleet_dispatch package10 records.

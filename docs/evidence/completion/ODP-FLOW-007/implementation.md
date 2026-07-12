# ODP-FLOW-007 - Complete DealRoom AVM Valuation And Approval Flow

**Owner:** Codex2 · **Reviewer:** Claude2 · **Phase:** Product Flow Implementation

## Goal

Close the DealRoom AVM loop so a valuation case can move through data
readiness, normalized margin, four-lens AVM valuation, finance review/approval,
DataRoom build, DataRoom export, report version history, and durable audit
evidence.

## Changes

### AVM valuation evidence

- `modules/avm/domain/valuation.py` now emits the explicit `blended` lens in
  addition to `income`, `asset`, and `market`.
- Lens evidence now carries the inputs decision makers need to inspect:
  normalized margin inputs, asset/lease values, comparable multiples,
  liquidity discount, and source snapshot ids.
- The market lens marks comparable evidence as `ready` when comparable
  multiples are present, or `missing_default_multiple` when the model must fall
  back to the default multiple.

### Finance approval gate

- `AVMService` now enforces state transitions:
  `DATA_READY -> REVIEW_REQUIRED -> APPROVED -> DATAROOM_READY`.
- Finance approval is blocked before `REVIEW_REQUIRED`, requires a reason,
  rejects self-approval by the case creator, records the request correlation id,
  and updates only the latest valuation report version.
- DataRoom build is blocked until finance approval exists. Export is blocked
  until the DataRoom has been built.

### Versioned reports

- `InMemoryAVMRepository` and `DurableAVMRepository` now expose
  `report_history(case_id)`.
- `GET /avm/cases/{case_id}/reports` returns all report versions plus
  `latest_version`; `GET /avm/cases/{case_id}/report` remains the latest-report
  endpoint.

### DataRoom completeness and export audit

- `DataRoom.to_dict()` now includes `completeness`, `is_complete`, and
  `missing_documents`.
- The DataRoom checklist derives readiness from the valuation report evidence
  and includes the valuation card with finance approval metadata.
- `GET /avm/cases/{case_id}/dataroom` exposes the built DataRoom.
- DataRoom build/export audit metadata includes DataRoom id, completeness,
  missing documents, and export count.

### Worker behavior

- `AVMValuationWorker` no longer builds a DataRoom by default because that would
  bypass finance approval.
- Batch DataRoom generation now requires an explicit `finance_approver` and
  finance reason before the worker builds the DataRoom.

## Acceptance mapping

| Acceptance surface | Evidence |
|---|---|
| Data room completeness | `DataRoom.completeness`, `is_complete`, `missing_documents`, checklist status derivation |
| AVM valuation | `income` / `asset` / `market` / `blended` lenses with P10/P50/P90 bands |
| Comparable evidence | market lens `comparable_multiples`, `liquidity_discount`, `evidence_status` |
| Versioned reports | repository `report_history`, `GET /reports`, durable restart regression |
| Review and finance approval | `REVIEW_REQUIRED` gate, reason requirement, self-approval block, approval correlation id |
| Export audit | `avm.dataroom_exported.v1` audit plus DataRoom `export_audit` |

## Notes

- The product-flow matrix now points FLOW-007 at the actual frontend path
  `apps/web/features/avm` instead of the nonexistent `features/assets`.
- No schema migration was required; durable AVM report history uses the existing
  `durable_documents` grouped-version store.

# Task Completion Evidence: ODP-CAP-XLSX-IMPORT-001

- Task ID: ODP-CAP-XLSX-IMPORT-001
- Title: Implement governed xlsx import
- Owner: Claude3
- Reviewer: Antigravity
- Round: 2 (post-rejection fix cycle; round 1 head `f0309f29` was REJECTED)

## Implementation Summary

Delivered governed XLSX spreadsheet import with safe OpenXML parsing, schema
mapping, preview validation, persisted idempotent commit, sensitive-data masking
error export, and audit logging.

### Key Components

1. **Governed XLSX application service** (`modules/external_data/application/xlsx_import.py`)
   - Zero-dependency OpenXML parser (`SafeXlsxParser`) on stdlib `zipfile` +
     `xml.etree.ElementTree`.
   - Cells are placed by their decoded `r` reference and rows report their real
     spreadsheet row number; the imported sheet is resolved through
     `xl/workbook.xml` + its relationship part, not by filename sort.
   - Fails closed on corrupt containers, malformed relationship parts, DTD/entity
     declarations, and decompression past the streamed byte budget.
   - Schema mapping and domain validation (`map_and_validate_rows`), applied
     identically on the preview and commit paths.
   - Non-writing preview (`preview_xlsx_import`) with a bounded, tenant-bound
     session cache.
   - Idempotent commit (`commit_xlsx_import`) that writes every accepted row
     through an injected `IntakeWriter` and reports the ids that writer returned.
   - Masking + formula-neutralising error exporter (`export_xlsx_import_errors`).
2. **API routes** (`apps/api/app/routes/listings.py`)
   - `POST /intake-batches/xlsx/preview` — schema mapping, formula/link warnings,
     row errors, sample rows. No intake writes.
   - `POST /intake-batches/xlsx/commit` — persists validated rows into intake
     state with `Idempotency-Key` replay; returned ids resolve via
     `GET /intakes/{intake_id}`.
   - `GET /intake-batches/xlsx/errors/{batch_id}/export` — `xlsx` / `csv` / `json`
     report, tenant-scoped, 404 for unknown or foreign batches.
3. **Contract artifacts** — `packages/openapi-client/openapi.json` and
   `packages/openapi-client/src/generated/types.ts` regenerated for the three new
   operations.

## Round 1 Review Findings — Disposition

| # | Finding | Fix |
| :--- | :--- | :--- |
| B1 | `commit_xlsx_import` persisted nothing; receipt ids 404'd | Commit writes each accepted row through `intake_writer`; the route writes `active.intakes[intake_id]`, and the receipt reports the writer's ids |
| B2 | Commit re-validated only `address_raw` + rent, bypassing URL policy and range rules | Commit re-runs `map_and_validate_rows`, so both paths enforce one rule set |
| B3 | Module-global idempotency cache leaked receipts across tenants | Cache key is `tenant \| actor \| key`; the route also rejects a scope that is not the authenticated tenant (403) |
| B4 | Error export had no ownership check on `batch_id` | `get_preview_result(batch_id, tenant_id)` binds previews to their tenant; foreign batches are 404 |
| B5 | Positional cell reading shifted values left past an omitted empty cell | Cells are placed by decoded `r` column reference |
| B6 | OpenAPI artifact stale → `make api-contract` red, PR unmergeable | Artifact + generated client regenerated; gate re-run PASS (3 additive, 0 breaking) |
| N1 | Documented XXE protection was not implemented | Parts declaring a DTD or entity are refused (`UNSAFE_XML`) before parsing; docstring now describes what is actually enforced |
| N2 | Zip-bomb guard trusted attacker-controlled `file_size` | Decompressed bytes are counted as read, with per-part and per-file caps; declared size is only a pre-filter |
| N3 | `row_index` was a sequence position | Row numbers come from the row's `r` attribute |
| N4 | Sheet chosen by filename sort | Resolved via `workbook.xml` sheet order + relationships (natural-sort fallback) |
| N5 | `lstrip("=@+-")` flipped negative text cells | Numeric text is left intact; otherwise exactly one trigger character is removed |
| N6 | Export re-introduced formula injection | `neutralize_export_cell` quotes any exported cell that would parse as a formula |
| N7 | `_PREVIEW_STORE` unbounded | LRU-bounded preview, idempotency, and default-writer stores |
| N8 | Unknown `batch_id` returned an empty 200 | Returns the declared 404 |
| N9 | `.rels` parse errors were swallowed | Fails closed as `MALFORMED_XLSX_FILE` |
| N10 | 9 ruff errors | `ruff check` clean on the delivered surface; dead `if False` branch removed |
| N11 | Two tests did not test their named property | Both now assert against write state / a writer spy; 15 new regression tests added |

## Verification Matrix

| Acceptance Criterion | Status | Evidence |
| :--- | :--- | :--- |
| `malformed formula and external-link inputs fail safely` | PASSED | `unit::test_malformed_formula_and_external_link_inputs_fail_safely`, `::test_entity_declaration_is_refused_before_parsing`, `::test_malformed_relationships_part_fails_closed`, `::test_decompression_is_bounded_while_reading`, `::test_formula_prefix_is_stripped_once_for_non_numeric_text` |
| `preview performs no writes` | PASSED | `unit::test_preview_performs_no_writes` (asserts the commit-target store is unchanged), `contract::test_preview_xlsx_api_endpoint` |
| `commit writes validated rows only` | PASSED | `unit::test_commit_writes_validated_rows_only` (writer spy), `::test_committed_rows_are_retrievable_by_receipt_id`, `::test_commit_applies_full_domain_validation_not_a_subset`, `contract::test_committed_intake_ids_resolve_to_stored_intakes`, `contract::test_commit_enforces_domain_validation_on_client_supplied_rows` |
| `duplicate commit is idempotent` | PASSED | `unit::test_duplicate_commit_is_idempotent`, `::test_idempotency_cache_is_scoped_per_tenant`, `contract::test_commit_xlsx_api_endpoint_idempotent`, `contract::test_idempotency_key_reuse_across_tenants_does_not_leak` |
| `row errors are downloadable with sensitive masking` | PASSED | `unit::test_row_errors_downloadable_with_sensitive_masking`, `::test_error_export_neutralises_formula_injection`, `::test_row_index_reports_the_spreadsheet_row_number`, `contract::test_export_xlsx_errors_api_endpoint`, `contract::test_export_of_unknown_or_foreign_batch_returns_404` |
| Brief constraint: 不得繞過 domain validation | PASSED | `unit::test_commit_applies_full_domain_validation_not_a_subset`, `contract::test_commit_enforces_domain_validation_on_client_supplied_rows` |

Data-corruption regressions (B5/N3/N4) are pinned by
`unit::test_sparse_cells_do_not_shift_values_into_the_wrong_column`,
`::test_row_index_reports_the_spreadsheet_row_number`, and
`::test_first_sheet_is_resolved_through_workbook_order`.

## Commands Run

```text
python3 -m pytest tests/unit/listing/test_xlsx_import.py tests/contract/test_xlsx_import_api.py
  -> 26 passed

python3 -m pytest tests/contract
  -> see task note; run to cover the contract-drift regression that round 1 broke

python3 scripts/openapi/export_openapi.py
python3 scripts/openapi/generate_client.py
python3 scripts/openapi/check_drift.py --base-ref origin/dev
  -> API contract gate: PASS (3 additive, 0 approved breaking, 0 unapproved breaking)

python3 -m ruff check apps/api modules/external_data/application/xlsx_import.py \
  tests/contract/test_xlsx_import_api.py tests/unit/listing/test_xlsx_import.py
  -> All checks passed!
```

## Known Boundaries

- Preview sessions and the commit idempotency cache are per-process and
  LRU-bounded. They are ephemeral by design: a restart or a second replica loses
  them, and the export endpoint then answers 404 rather than an empty report.
  Moving both to a shared store with an explicit TTL is a separate task.
- `intake_method` is recorded as `CSV` for spreadsheet rows; the import
  authorizes under the same `submit_csv` action as the existing batch path, and
  no new enum value was added to the published contract.

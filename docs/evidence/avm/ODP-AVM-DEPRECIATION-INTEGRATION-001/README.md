# AVM artifact rollback and historical provenance

The deployed model artifact schema is separate from the domain input schema. New `ValuationInput` and `NormalizedMargin` records remain `valuation-view-v2`. Existing records without an instance version serialize as `valuation-view-v1`, including nested margins after legacy quality disposition and historical cases processed through `_migrate_legacy_case`. Stored records retain `valuation-view-v1` across read-induced quality migrations and subsequent reads. The report retains its own recorded model feature version.

Production composition, readiness schema selection and `AVMProductionExecutor.from_environment` now select the same artifact schema. `ODP_AVM_ARTIFACT_SCHEMA_VERSION` defaults to `valuation-view-v1`, matching the existing approved artifact contract. A deployment with an approved v2 artifact can explicitly select `valuation-view-v2`. Unknown values fail closed. This setting does not grant model approval or Finance cutover approval.

Operational depreciation rollback still requires the existing valid, unexpired v0 receipt. The `ODP_AVM_DEPRECIATION_VERSION_PIN` and `ODP_AVM_DEPRECIATION_ROLLBACK_RECEIPT_JSON` inputs retain their existing checks; choosing an artifact schema does not bypass them.

Regression coverage loads registered real LightGBM artifacts and runs the real lifelines predictor for both artifact schemas. It then drives production app bootstrap and HTTP valuation against an isolated PostgreSQL bundle with a test-only approved v0 receipt, and exercises the service's environment fallback. The test confirms that artifact/report provenance can be v1 while fresh domain input and normalized margin remain v2. The governed-disabled service override exists only inside the test fixture.

SQLite and PostgreSQL durable legacy fixtures omit both quality status and feature version fields from the serialized historical input, as actual pre-upgrade objects do. They verify that read-induced migration in `DurableAVMRepository` preserves `valuation-view-v1` on both SQLite and PostgreSQL, and verify current report, report history, HTTP GET/history, both quality-disposition paths and persisted raw storage. The original regression failures and subsequent test receipts are retained in the local handoff; final verification receipts and source hashes accompany this document.

This change does not activate a live service or deploy a model. Existing approval, cutover and independent PR review gates remain in force.

Final verification: **76 passed** (2 real artifact variants and 74 remaining AVM/domain/durable/API composition cases). Source commit: `b50bca51e62c58cf183d3a11076f1bb5a04d7817`. Exact commands and original exit codes are in `bounded-continuation-verification.json`.


## Operator rollback composition

Production bootstrap now supplies the same deployment depreciation pin and rollback receipt to the Operator router and its tenant-scoped NetworkRebalanceService. Its AVMService retains receipt validation and canonical tenant persistence. Two real artifact/bootstrap cases verify original-cost writes through both APIs restore v0 even without Finance cutover evidence, refuse expired/mismatched/malformed receipts, preserve existing v1 reports, and isolate tenants. Original failing and passing receipts are retained in operator-rollback-verification.json and sibling logs. Existing AVM/Operator/history/governance regression: 93 passed; real artifact tests: 2 passed; boundary 1157 and ruff passed.

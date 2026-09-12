# AVM artifact rollback and historical provenance

The deployed model artifact schema is separate from the domain input schema. New `ValuationInput` and `NormalizedMargin` records remain `valuation-view-v2`. Existing records without an instance version serialize as `valuation-view-v1`, including nested margins after legacy quality disposition. Reads do not rewrite stored records. The report retains its own recorded model feature version.

Production composition, readiness schema selection and `AVMProductionExecutor.from_environment` now select the same artifact schema. `ODP_AVM_ARTIFACT_SCHEMA_VERSION` defaults to `valuation-view-v1`, matching the existing approved artifact contract. A deployment with an approved v2 artifact can explicitly select `valuation-view-v2`. Unknown values fail closed. This setting does not grant model approval or Finance cutover approval.

Operational depreciation rollback still requires the existing valid, unexpired v0 receipt. The `ODP_AVM_DEPRECIATION_VERSION_PIN` and `ODP_AVM_DEPRECIATION_ROLLBACK_RECEIPT_JSON` inputs retain their existing checks; choosing an artifact schema does not bypass them.

Regression coverage loads registered real LightGBM artifacts and runs the real lifelines predictor for both artifact schemas. It then drives production app bootstrap and HTTP valuation against an isolated PostgreSQL bundle with a test-only approved v0 receipt, and exercises the service's environment fallback. The test confirms that artifact/report provenance can be v1 while fresh domain input and normalized margin remain v2. The governed-disabled service override exists only inside the test fixture.

SQLite and PostgreSQL durable legacy fixtures omit the fields from the serialized instance, as actual pre-upgrade objects do. They verify current report, report history, HTTP GET/history, both quality-disposition paths and untouched raw storage. The original regression failures and subsequent test receipts are retained in the local handoff; final verification receipts and source hashes accompany this document.

This change does not activate a live service or deploy a model. Existing approval, cutover and independent PR review gates remain in force.

Final verification: **76 passed** (2 real artifact variants and 74 remaining AVM/domain/durable/API composition cases). Source commit: `b50bca51e62c58cf183d3a11076f1bb5a04d7817`. Exact commands and original exit codes are in `bounded-continuation-verification.json`.

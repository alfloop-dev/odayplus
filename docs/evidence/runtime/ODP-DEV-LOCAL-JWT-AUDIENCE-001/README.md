# ODP-DEV-LOCAL-JWT-AUDIENCE-001

## Actual deployment failure

Protected dev CI `37577152916` passed for merged dependency fix `5c0033977579382e43449eb22187145e9f5ec459`. Its automatic Deploy Dev run [37579070682](https://github.com/alfloop-dev/odayplus/actions/runs/37579070682) successfully built/signed immutable images, admitted the exact manifest, migrated the database, created API/Web/job revisions and passed release-aware Cloud Run smoke. After promotion, the real browser/password administration gate failed `session:must_change_enforced`: API returned **401 audience_mismatch**, rather than the required **403 PASSWORD_CHANGE_REQUIRED**.

The API serializer takes its local JWT audiences from `ODP_AUTH_LOCAL_AUDIENCES` or the configured legacy `ODP_AUTH_AUDIENCES`. The Web serializer instead overwrote both values with the Cloud Run transport audience `status.url`. The dev environment config is `https://oday-api-767864276141.asia-east1.run.app`; a service's discovered transport hostname can be `https://oday-api-2l6wuyl67q-de.a.run.app`. These are equivalent service routes, **not interchangeable JWT audiences**. The API correctly refused the token.

The failed first release was automatically recovered to verified absent services/jobs and zero traffic; readback `live_release=null`. The downloaded recovery/readback receipts are preserved alongside this document. No application rollback target exists and no live successful deployment is claimed here.

## Repair

- Serialize Web local JWT audiences using the same configured inputs/defaulting as the API; keep `ODP_API_SERVICE_AUDIENCE` unchanged for private Cloud Run invocation.
- Web JWT minting prefers the explicitly separated local audience configuration, selects one declared audience from a CSV list, and refuses missing/empty production audiences. It never substitutes the transport audience.
- Regression coverage executes both real deployment serializers with mismatched application/transport hostnames, both legacy fallback and explicitly separated audiences; Web tests cover precedence, CSV, explicit override and missing/empty fail-closed inputs.
- Give the existing production password-policy fixture an explicit valid local audience, so it continues testing same-credential refusal after token refresh.

No API audience allow-list expansion, auth bypass, changed credentials/secret versions, IAM/network changes, database reset, licence policy change or second deployment workflow.

## Local checks

Base: `5c0033977579382e43449eb22187145e9f5ec459`.

- Python 3.12 frozen dependency sync, deployment serializer/conditional OIDC tests and API trust contract tests: PASS (Terraform CLI-dependent tests skipped when unavailable).
- Clean npm ci including dev/optional packages: PASS.
- Web tests: **612 passed / 63 files**.
- Production Next build/type validation and bundle budget: PASS.
- Official production npm audit gate: zero findings, original high threshold unchanged.
- Shell syntax check and git diff --check: PASS.

## Actual deployment continuation

User explicitly requested completing the work and actual deployment. Independent review and protected push CI must still pass. The existing standing automatic **dev** lane must build a fresh immutable candidate from the merged repair and perform migration, job execution, tagged smoke, live password/first-rotation/admin/E2E acceptance and service/image/release readback. Do not reuse the failed 5c003397 release or manually change Cloud Run environment variables. Staging and production are not authorized by this dev continuation and remain behind their existing human gates.

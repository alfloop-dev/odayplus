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

## Protected PR CI receipts (audit metadata repair)

PR [#1427](https://github.com/alfloop-dev/odayplus/pull/1427), tested source head: `6b7d4fbe064e8810980db73b720fb5e981297e62`.
[CI run 37586326586](https://github.com/alfloop-dev/odayplus/actions/runs/37586326586) is **completed / success**. The final product aggregate completed at `2026-10-07T07:39:13Z`. These are existing terminal receipts, read back after Codex2's metadata-only reopen; no tests were rerun and no product code was changed in this repair.

| Completed successful job | Validation command / result |
| --- | --- |
| [product-node](https://github.com/alfloop-dev/odayplus/actions/runs/37586326586/job/112677224095) | `make node-check`: lint, typecheck, production build, bundle budget and **612 Web tests / 63 files passed**. Completed `2026-10-07T07:18:44Z`. |
| [product-e2e-gate](https://github.com/alfloop-dev/odayplus/actions/runs/37586326586/job/112677224027) | `make product-e2e-bootstrap`; `make product-e2e-gate`: **108 Playwright tests passed**; runner exit receipt `playwright=0 recorder=0 pytest=0 receipt=0`, evidence receipt `status=passed, errors=0`. Completed `2026-10-07T07:23:11Z`. |
| [orchestrator](https://github.com/alfloop-dev/odayplus/actions/runs/37586326586/job/112677174403) | Boundary/measurement/requirement/vocabulary/config checks; `uv run ruff check .orchestrator delivery_toolchain scripts infra`; `uv run pytest -m "not requires_live_env" .orchestrator delivery_toolchain scripts tests/tooling infra`. |
| [product-lint-unit](https://github.com/alfloop-dev/odayplus/actions/runs/37586326586/job/112677224146) | `uv run ruff check tests modules apps shared models solver pipelines infra`; `uv run pytest -m "not requires_live_env and not performance" tests modules apps shared models -n auto`. |
| [product-db](https://github.com/alfloop-dev/odayplus/actions/runs/37586326586/job/112677224012) | `uv run pytest tests/integration/test_official_real_estate_postgresql.py`; `uv run pytest -m "requires_live_env and not requires_postgis" tests/contract tests/ops tests/integration`. |
| [product-api-contract](https://github.com/alfloop-dev/odayplus/actions/runs/37586326586/job/112677224154) | `make api-contract`. |
| [product-security](https://github.com/alfloop-dev/odayplus/actions/runs/37586326586/job/112677224039) | `make security`. |
| [performance-gate](https://github.com/alfloop-dev/odayplus/actions/runs/37586326586/job/112677224038) | Three attempts of `uv run pytest -m performance tests/performance` and `uv run python delivery_toolchain/load/assisted_listing_intake/run.py --volume 120 --concurrency 20 --observe-only --output "performance-reports/assisted_intake_capacity_attempt_${attempt}.json"`. |
| [product](https://github.com/alfloop-dev/odayplus/actions/runs/37586326586/job/112684546548) | `python3 delivery_toolchain/governance/verify_ci_product_jobs.py`: parallel product lanes successful. |
| [change-scope](https://github.com/alfloop-dev/odayplus/actions/runs/37586326586/job/112677174755) | Change-scope classification successful. |

Exact command definitions: [tested CI workflow](https://github.com/alfloop-dev/odayplus/blob/6b7d4fbe064e8810980db73b720fb5e981297e62/.github/workflows/ci.yml) and [tested Makefile](https://github.com/alfloop-dev/odayplus/blob/6b7d4fbe064e8810980db73b720fb5e981297e62/Makefile). `make node-check` executed:

```sh
npm ci
npm run lint --workspaces --if-present
npm run typecheck --workspaces --if-present
npm run build --workspaces --if-present
npm run bundle:budget --workspaces --if-present
npm run test --workspaces --if-present
```

Readback commands (not test execution):

```sh
gh run view 37586326586 --json databaseId,headSha,status,conclusion,url,jobs
gh run view 37586326586 --job 112677224095 --log
gh run view 37586326586 --job 112677224027 --log
```

Success is established by the original run/job terminal statuses; log summaries only establish counts and runner exit details. This run proves the source head above, **not** the subsequent evidence-only commit or its CI. The new exact PR head must receive independent review and its own required checks. The PR run's automatic dev release request was **skipped**; no successful live deployment is claimed. After approval and merge, protected merged-head CI and the existing automatic dev lane remain responsible for actual deployment.

## Actual deployment continuation

User explicitly requested completing the work and actual deployment. Independent review and protected push CI must still pass. The existing standing automatic **dev** lane must build a fresh immutable candidate from the merged repair and perform migration, job execution, tagged smoke, live password/first-rotation/admin/E2E acceptance and service/image/release readback. Do not reuse the failed 5c003397 release or manually change Cloud Run environment variables. Staging and production are not authorized by this dev continuation and remain behind their existing human gates.

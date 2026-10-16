# Manager scope/lease request — source-only CI repairs, not admission

Pi run `pi-20261015T042723Z-64b2f81a` has completed two incremental source
anchors, `8010e6570c806b1a99fb38e67683b04271be7911` (audit FK prerequisite)
and `a83c6d674342e1bbcea5b98288649db498e3cd5b` (future job audit tenant).
All five modified code/test paths were already in canonical task `files`;
no helper workspace, production database or extra source path was modified.
The evidence directory is also already allocated. Neither anchor is a final
verified composition. Reviewer remains **Human/Ops**, disposition PENDING.

## Required Manager action before further edits outside allocated paths

At this run's actual canonical-board readback, none of the following exact
paths is in this task's allocated `files`. The source handoff explicitly
requires inventory/justification/Manager leases before modifying extra paths.
Please authorize/serialize a narrow additional lease batch, or provide another
authorized executor. This request itself neither edits task scope nor grants
leases, and does not permit policy/control-plane changes.

| Proposed path | Source-only justification and required retained controls |
|---|---|
| `apps/api/app/routes/forecastops.py` | Inspect future acknowledge/handoff/feedback audit producers against verified tenant and owning repository records; retain own-tenant positives, foreign/anonymous denials and historical records. |
| `apps/api/oday_api/routes/heatzone.py` | Inspect future scoring/composition audit attribution; no tenant-filter relaxation or client-metadata authority. |
| `modules/opsboard/audit/application/evidence_export.py` | Inspect future export audit attribution from authorized owning decision/export, never rewriting old records or inventing signatures. |
| `tests/performance/test_load_and_soak.py` | Supported authenticated workload fixture for real 401 failures. Preserve 150 requests, waves 10/20/50, P95 3.0s and three-run enforcement. |
| `delivery_toolchain/e2e/seed_product_e2e_data.py` | Supported authorized operation/principal/tenant seed fixture for actual Docker 403. No generic forbidden enqueue, mutation-boundary bypass or E2E inventory omission. |
| `.orchestrator/test_release_lease_integration.py` | Test-only explicit clocks and realistic GO evidence shape; no live supervisor/lease/expiry/approval weakening. |
| `.orchestrator/test_release_lease_issuer.py` | Same test-only GO/clock repair; no real signoff, issuer impersonation or live control-plane edit. |
| `tests/release/test_release_profile.py` | Valid synthetic fixture gates/signoff shape, retaining every admission denial. |
| `tests/identity/test_sql_session_store.py` | Valid persisted scope row in connection-pooling positive fixture; retain unknown-account refusal and connection-return regression. |

This is an initial batch, not an assertion that the complete retained CI failure
inventory has been repaired. Contract/API/production-OSS authentication fixture
paths and additional audit producers may need separately justified leases after
inspecting their complete owning failure/source. Do not silently widen scope or
replace actual production-mode/artifact tests with memory/offline substitutes.

The retained original run38663011109 failed-job logs are available in the canonical
followthrough handoff. This run inspected the failed-test index and relevant
migration/audit producer source, not every complete job log; no universal
root-cause claim is made. Later CI38681182077 at OLD1c19 has actual failures in
orchestrator/product-db/performance-gate/product-e2e-gate and a still-running
product-lint-unit at this run's readback. New source heads need fresh normal CI;
old successes or partial readbacks do not transfer.

## Required work after leases and source repair

Finish owned author work before governed `task_finalize.sh` submission, which can
supersede the worker. At the final immutable head obtain canonical14, all17
separate bounded selections and owning controls, terminal normal hosted CI, and
the configured independent Codex2 supplied-text assessment (unchanged1048576 cap,
no tools/network/fallback/bypass), explicitly listing omissions and late or
uninspected measurements. Existing author logs and old553/dcf1 receipts are NOT
substitutes. Keep1200/420, Vitest5000ms, Web300kB, Argon64MiB/time3, native480/540,
all transport/custody/temporal assertions, actual-PG and frozen-lock Node guards.

No review approval, auto-merge arming, merge, done, deployment/recovery,
credential/account/session, cloud/IAM or business/data/model/F11 effect is granted.
Original complete blocked/six-reopen history and PR1459 remain unchanged; absent
original workspace UNKNOWN, quarantine/bindingNULL/cleanupUNKNOWN, preflightHOLD
and goalfalse persist. Pi's original failed-owner epoch is not cleared and Pi is
not an independent security assessor.

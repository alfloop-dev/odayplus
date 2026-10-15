# Manager source-scope request batch002 — not self-granted authority

Run `pi-20261015T044152Z-8b7b4813` used the renewed Manager allocation and
batch001 leases. Source checkpoint `a7c1b9c005a63ed82c8c0a37aac3926b359c76c9`
is incremental, NOT review-ready. Human/Ops remains final reviewer; no final
admission, approval, merge arming, merge, done or live effect is requested.

Batch001 is resolved, not still blocked. Nine allocated paths were inspected;
eight were changed, while the export service was deliberately NOT changed:
its caller currently supplies no trusted tenant, and request metadata is not
authority. Other code/test changes are in previously allocated paths. The
following THREE exact owning paths are absent from current canonical `files`.
Please justify/serialize their narrow additional leases through Worker Manager,
or arrange an authorized executor. This document does not edit task scope.

| Additional path | Inspected evidence and intended retained boundary |
|---|---|
| `apps/api/app/routes/audit.py` | `export_evidence` has the authenticated principal but calls the already-allocated export service without tenant authority. Thread a separately verified, server-owned tenant to that service, constrain HTTP export selection to that tenant, and attribute future export/denial events. Never derive tenant from body/correlation/client metadata, weaken read filters, rewrite historical events, or invent signatures. Direct offline service callers are not proof of HTTP tenant isolation. |
| `shared/workflow/sitescore.py` | Fresh durable flow at a7c still fails because `sitescore.decision.v1` is absent from tenant-filtered readback. The workflow owns the audit producer and the stored `SiteDecision.tenant_id`; derive future event attribution from the owning authorized decision, not from an added audit read waiver. Preserve decision transitions, tenant repositories, permissions and historical records. |
| `tests/integration/test_assisted_listing_postgresql_runtime.py` | New hosted product-db job116250091609 at b2c has one actual failed test, `test_assisted_intake_http_state_survives_postgresql_restart`: production-mode POST returns AUTHENTICATION_REQUIRED/no_credentials. Its `_headers` only supplies trusted development headers while `_configure_production` sets production. Add supported synthetic authentication/custody to the real production-mode, actual-PG persistence/restart test; preserve every runtime mode, SQL migration, schema, RLS/tenant/ownership and mutation-boundary assertion. No offline replacement or production credential required. |

Actual current-head author composition `owning-pg-domain-composition` exited1:
non-PG owning passes, six PG skips from the existing module marker, and the one
SiteScore event failure above. Skips are NOT PG success. A subsequent bundled-PG
attempt exited1 (wrapper missing multiprocessing main guard plus missing PostGIS);
all logs remain. A NEW owned throwaway Docker PostgreSQL16.9/PostGIS fixture using
the existing CI image then ran the full six-test PG persistence module, exit0,
no skips, and removed its own container. Those results do not fix or waive the
separate hosted production-auth fixture failure.

New b2c logs are retained under this run's canonical scratch root as
`b2c-product-db-download2.raw.log`, `b2c-orchestrator-download2.raw.log`, and inert
`*.safe.log` views. Initial gh ANSI-output refusal is retained, not counted as
successful download. This run inspected the relevant complete failure bodies,
not all twelve thousand log lines or every original32 failure. Further supported
auth/API/other producer fixtures need additional individually justified scope
once fully inspected; this request is not a universal root-cause declaration.

Finish author repairs before governed submission can supersede the author.
Final immutable-head canonical14/all17, owning controls, terminal normal hosted
CI, and independent configured Codex2 supplied-text assessment with explicit
coverage gaps remain owed. Keep all1200/420,5000ms,300kB,Argon64MiB/time3,
native480/540 and transport/custody/lifetime/assertion caps unchanged. Preserve
original complete blocked-six-reopen record/PR1459 and failed epochs, absent
original workspace UNKNOWN, operational HOLD/quarantine/bindingNULL/
cleanupUNKNOWN and goalfalse. No old-head proof transfer, policy/credential/
provider/approval change, deployment/recovery/cloud/business/data/model/F11 effect.

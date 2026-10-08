# Live business journeys (ODP-BUSINESS-LIVE-E2E-COVERAGE-001)

`delivery_toolchain/e2e/live_business_journeys.py` is the executable acceptance
program for six business journeys on a deployed release. Its sealed receipt is
the only input that lets `check_live_e2e_gate.py` claim
`release_profile.full_product_acceptance_claimed=true`. The live run itself is
owned by ODP-BUSINESS-LIVE-E2E-ACCEPTANCE-001.

## Journeys

| Journey | Web selector | Actors (business roles) | Write → readback → audit |
|---|---|---|---|
| operations | `/operator?ws=store` | operations_manager; denied regional_supervisor | Store Ops issue transition → issue status/history → `operator.store_ops.issue_transition` |
| growth | `/operator?ws=growth&gtab=priceops` | pricing_manager + marketing_manager; denied operations_manager | PriceOps plan action and AdLift incrementality job (polled to `succeeded`) → plan status → audit per write |
| expansion | `/operator?ws=network` | executive planner + distinct executive approver; denied expansion_user | NetPlan solve/submit/decide with named approval → scenario status, solve result, modelled/unmodelled disclosure → `netplan.*` |
| governance | `/operator?ws=govern` | operations_manager/executive; denied expansion_user | business approval decision (not user administration) → approval status + decision → audit |
| franchise | `/franchisee` | franchisee; denied operations_manager | own-store field report → own-store reports → audit |
| intake | `/operator?ws=network&tab=intake` | expansion_user; denied pricing_manager | assisted intake decision → stage/version → audit, plus a blocked-source submission that must be refused (nothing persisted) or durably quarantined without retrieval |

Every journey signs in through the Web password form (`POST /login`, session
cookie, BFF proxy). No bearer, role or tenant header is injected. Each account
must be refused `/api/v1/operator/users`, because `platform_admin` is never
promoted into a business role. Every journey also proves two negative probes
against the live system:

- a foreign-tenant or foreign-store record from the scope is refused (403/404/422);
- the denied account's write is refused (401/403) and the record stays unchanged.

## Inputs

| Input | Purpose |
|---|---|
| `ODP_RELEASE_MANIFEST_PATH`, `ODP_RELEASE_MANIFEST_DIGEST`, `ODAY_RELEASE_SHA` | The immutable admitted manifest. Its sealed `release_profile` decides admission. |
| `ODP_LIVE_JOURNEY_SCOPE_PATH` | Scope authorization (`kind: odp.live-business-journey-scope`, `schema_version: 1`), bound to the release SHA and manifest digest. It names `authorized_by` and `authorization_ref`, and per journey: `tenant_id`, `foreign_tenant_id`, `actors`, `records`, `foreign_records`, `writes.<key>.{action, body}`, and `approval_ref` where a named approval is required. The intake journey also needs `writes.policy_probe.{source_ref, body}`. |
| `ODP_LIVE_JOURNEY_<ID>[_<SLOT>]_USERNAME/_PASSWORD` | Business accounts per slot (`primary`, `approver`, `marketer`, `denied`). Each must be exactly the account the scope authorizes. |
| `ODP_LIVE_E2E_WEB_URL`, `ODP_LIVE_E2E_API_URL`, `ODP_API_INVOKER_TOKEN` | Deployed Web origin, plus anonymous API reads of `/platform/version` and `/readiness`. |

## Fail-closed semantics

- A **dev-admin** manifest makes every journey `NOT_ADMITTED`. A caller setting
  `ODP_RELEASE_PROFILE=full` is recorded as a refused override and changes
  nothing.
- Before any mutation the runner checks the profile, the exact served SHA and
  runtime profile, the scope, credentials, sign-in, the business role, and a
  live record with provenance. Until all of these pass, `LedgerHttp` refuses
  every mutation. A refused preflight leaves `business_writes=0` and
  `worker_or_provider_triggers=0` in the receipt.
- A missing admission is `BLOCKED` with the named dependency: `release`,
  `release-profile`, `scope-authorization`, `business-credential`,
  `business-role`, `business-data`, `model`, `source`, `named-approval` or
  `web`. A misbehaving live system is `FAILED`: `tenant-isolation`,
  `authorization`, `business-write`, `durable-readback`, `audit`, `worker`,
  `disclosure`, `policy` or `data-binding`. Nothing is skipped into green.
- The receipt records the source SHA, manifest digest, sealed profile, actor
  subjects and tenant, selector, command and exit code, before/after record
  identity, correlation ids, audit and job references, and a request ledger. It
  never holds a password, cookie or token. It is sealed with `receipt_digest`.

## Gate binding

```bash
python3 delivery_toolchain/e2e/live_business_journeys.py --output receipt.json
python3 delivery_toolchain/e2e/check_live_e2e_gate.py ... \
  --business-journey-receipt receipt.json \
  --expected-manifest-digest "$ODP_RELEASE_MANIFEST_DIGEST" \
  --require-full-acceptance
```

The gate's runtime verdict (`ok`) and its default exit code are unchanged. Full
acceptance (`report.full_acceptance`) is a separate verdict. It is `PASSED` only
when all of these hold:

- the profile is `full`;
- the runtime gate passed;
- the receipt is unaltered and live (`offline_fixture=false`);
- it is bound to this SHA and manifest digest;
- it covers all six journeys with every one `PASSED`;
- it carries no secret fields.

Otherwise full acceptance is `BLOCKED`, or `NOT_ADMITTED` for dev-admin. Each
blocker names its next action. Only `--require-full-acceptance` turns a missing
claim into a non-zero exit.

`tests/e2e/test_live_business_journeys.py` is the offline regression suite. Its
`BusinessWeb` double is clearly marked as non-live and is never acceptance
evidence.

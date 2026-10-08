# Live business journeys (ODP-BUSINESS-LIVE-E2E-COVERAGE-001)

`delivery_toolchain/e2e/live_business_journeys.py` is the executable acceptance
program for six business journeys on a deployed release. Its sealed receipt is
the only input that lets `check_live_e2e_gate.py` claim
`release_profile.full_product_acceptance_claimed=true`. The live run itself is
owned by ODP-BUSINESS-LIVE-E2E-ACCEPTANCE-001.

## Journeys

| Journey | Web selector | Actors (business roles) | Write → readback → audit |
|---|---|---|---|
| operations | `/operator?ws=store` | operations_manager; denied regional_supervisor | Store Ops transition → action-specific status/owner/SLA outcome (transfer changes owner, not status/history) → `operator.store_ops.issue_transition` |
| growth | `/operator?ws=growth&gtab=priceops` | pricing_manager + marketing_manager; denied marketing_manager/operations_manager | PriceOps action → canonical per-action audit; authorized non-empty AdLift campaign job → `succeeded` → fresh durable report with model/feature/snapshot provenance |
| expansion | `/operator?ws=network` | executive planner + distinct executive approver; denied marketing_manager/pricing_manager | Existing operator NetPlan solve → fresh projection and canonical solve timestamp → real browser disclosure → submit/approve with named receipt → solve/decision audit |
| governance | `/operator?ws=govern` | operations_manager/executive; denied expansion_user | capture this business decision ID → same new durable decision/authorized terminal status/reason/account → matching new actor/action/target/reason audit |
| franchise | `/franchisee` | franchisee; denied operations_manager | own-store field report → own-store reports → audit |
| intake | `/operator?ws=network&tab=intake` | expansion_user; denied pricing_manager | assisted intake decision → fresh decision/audit and durable target listing (create/revise/duplicate), or quarantine/reject outcome; blocked-source probe must be refused or durably quarantined without retrieval |

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
| `ODP_LIVE_JOURNEY_SCOPE_PATH` | Scope authorization (`kind: odp.live-business-journey-scope`, `schema_version: 2`), bound to the release SHA and manifest digest. It names `authorized_by` and `authorization_ref`, and per journey: `tenant_id`, `foreign_tenant_id`, `actors` (usernames), `account_ids` (authoritative account UUIDs per slot), `records`, `foreign_records`, `writes.<key>.{action, body}`, and `approval_ref` where a named approval is required. The intake journey also needs `writes.policy_probe.{source_ref, body}`. |
| `ODP_LIVE_JOURNEY_<ID>[_<SLOT>]_USERNAME/_PASSWORD` | Business accounts per slot (`primary`, `approver`, `marketer`, `denied`). Each must be exactly the account the scope authorizes. |
| `ODP_LIVE_E2E_WEB_URL`, `ODP_LIVE_E2E_API_URL`, `ODP_API_INVOKER_TOKEN` | Deployed Web origin and independent API origin. Both must bind the admitted release through the side-effect-free `/api/v1/platform/release-identity`; the authenticated Web BFF additionally reports its own SHA/profile/manifest digest using the same upstream resolver as business writes. No `/readiness` provider probes are performed. |

## Fail-closed semantics

- A **dev-admin** manifest makes every journey `NOT_ADMITTED`. A caller setting
  `ODP_RELEASE_PROFILE=full` is recorded as a refused override and changes
  nothing.
- Before any mutation the runner checks the profile, exact served SHA and
  server-owned manifest digest for both API and actual Web/BFF destination,
  scope, credentials, authoritative account UUID/tenant/roles, served grants,
  and authorized live record with provenance. Until all pass, `LedgerHttp`
  refuses every mutation. A refused preflight leaves `business_writes=0` and
  `worker_or_provider_triggers=0`; identity reads never invoke provider probes.
- Expansion additionally requires an authorized draft scenario uniquely mapped
  to the authorized `rebalance_store_id`, actual model provenance, and scoped
  modelled/unmodelled classes. The local Chromium launch/close check runs before
  writes are armed. After operator solve, the canonical `solve.solved_at`,
  returned projection and browser `data-solve-completed-at` must match this
  fresh solve before submit/approve. Browser assertions only open the store
  card and read disclosure; they never choose a scenario or trigger a solve.
- Intake permits the canonical `create/revise/duplicate/quarantine/reject`
  decisions. READY may remain READY and version need not increment: fresh
  decision/audit plus target-listing durable readback proves the outcome.
  Revise/duplicate require a scoped `target_listing_id`; create preserves the
  intake tenant in persisted listing metadata for tenant-filtered readback.
- NetPlan decision bodies follow `NetPlanDecisionPayload`: `actor_id`, `reason`,
  `decision` and optional receipt/time only (not `comment`). Before any write,
  `actor_id` must equal the distinct authoritative approver UUID in scope.
  Durable approval must be newly created, match this write's captured approval
  ID, actor/principal/receipt/reason and current disclosure, and be authentic.
  POST returns a root `ApprovalRecord`; only GET scenario contains `approvals`.
  Before any mutation/trigger, existing GET scenario must provide a server-owned
  `approval_disclosure_readiness` computed from its current solve, selected
  primary action, effective tenant policy and existing acknowledgement. Missing
  or stale solves, unresolved/blocking policy, missing/mismatched acknowledgement,
  or an older deployment without this read-only field are `named-approval`
  BLOCKED with zero writes/triggers. The field reuses the decision's existing
  checks without a solver, verifier callback, save, transition or audit; it is
  not an approval, waiver, or acknowledgement writer. Actual authorized
  preparation of a valid acknowledged draft remains an external prerequisite.
  Re-solve must preserve this problem/action/policy/ack binding; invalidation
  stops before submit/decide. The final same new approval and sealed receipt
  must bind this prerequisite and a durable `status=approved` scenario.
  The runner does not change the canonical API's authorization policy.
- Franchise captures this POST's report ID and requires that exact new report
  in durable GET, bound to store, actor, content, status and request correlation;
  unrelated concurrent reports cannot substitute for a lost write. Receipts
  bind authorized report content and approval reason with content digests.
- Governance bodies bind `actorName` to the authenticated account UUID (a
  different caller actor blocks before writes). Capture the POST decision ID,
  require that same newly durable decision, the authorized approve/return/reject
  terminal status, canonical final-decision label and reason, and matching new
  audit actor/action/entity/reason. The service's record correlation convention
  `corr-<approvalId>` is distinct from this HTTP write's authorization correlation;
  both are checked. Empty approve reasons use the canonical decision-log default.
- Store Ops verifies the expected outcome of the authorized action, including
  the precise transfer owner. Noop or inadmissible transitions block before
  writes; timestamps, unrelated history or concurrent state changes do not
  count as that action's business outcome.
- Missing server-owned admitted manifest-digest metadata is `BLOCKED`. Setting
  caller variables is not a deployment binding. This engineering task does not
  change deployment workflows or authorize any actual live execution.
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

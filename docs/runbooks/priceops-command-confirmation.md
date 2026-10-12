# PriceOps scenario and decision confirmation

The archived R7/Package 10 `Growth PriceOps` screen contains the recommendation table and draft action, with no default simulation/writeback panel. The Growth tab now preserves that default composition. A bound candidate-price link opens explicit scenario details; this additional detail state follows the pricing simulation/approval requirements and still needs independent visual review. The R7 Growth PriceOps table retains recommendation/segment context. A workbench is linked to a canonical `planId` and `itemId`; unbound fixture rows cannot manufacture simulation results or a successful writeback. `prec` selects the recommendation in the URL. A missing explicit target does not select another row. Changing the selected record unmounts its workbench and discards late callbacks.

Comparison data and command permissions come from the verified PriceOps API. Demand, revenue and gross margin show the actual P10/P50/P90 values. Editing a price invalidates the retained candidate scenario. The simulation button creates a real retained scenario and requires a matching plan, item, candidate price, correlation and audit acknowledgment. Absolute gross margin is never converted into a percentage without a denominator.

Decision reasons start empty and must be authored. Parameters are disabled during a command. A response loss or timeout leaves the decision unconfirmed; retries of the same intent retain the idempotency key and correlation ID. The UI shows success only when the plan, actor, decision, reason, scenario, command key and audit fields match the request. A scenario selection does not approve a plan or apply a price.

Plan reads, plan commands, creation proposals and every batch proposal check the verified tenant and each item's store/brand scope before reading, writing or replaying receipts. Production uses the injected store/brand master repositories: current store, store tenant, brand tenant, brand and region are resolved from actual data. Explicit mismatched proposal brands are rejected; omitted brands are filled from the master. Reads, commands and replay recheck current ownership. Assigned-area/heat-zone restricted principals remain refused because store masters do not carry those bindings; confidential clearance and module scope also apply. Command body actor fields are compatibility inputs: the API binds them to the verified subject. Decision audit metadata links the actual decision/scenario, policy, solver and tenant. The plan endpoint exposes retained decision writebacks for readback.

Approval requires the existing pending-approval lifecycle, feasible optimization, rollback readiness, and candidate prices matching the optimized plan. A real canonical approval record is created. Unsupported decisions, blank reasons, unrelated scenarios and changed direct-service retry intents are refused. This change does not expand the canonical Pricing Manager grants or bypass the existing production optimizer/durable-store requirements.

## Reproduce the supplemental proof

```sh
CI=1 OPSBOARD_PORT=35401 ODP_API_PORT=35402 \
  STORE_OPS_PARITY_EVIDENCE_DIR=/tmp/priceops-command-proof \
  npx playwright test tests/visual/operator-priceops-command-parity.spec.ts \
  --config=tests/visual/operator-priceops-command.config.ts --reporter=list,json
```

The dedicated config starts an isolated loopback synthetic server using the real router/domain service/command receipts. Growth list data is explicitly synthetic. Browser writes are forwarded to this fixture with its synthetic Pricing Manager principal. At 1440/1024/390 px it loses the first successful response, retries the same command, reads the real repository/audit, and checks one decision, one event, scoped axe and viewport containment. It is outside the fixed 130-browser business acceptance inventory and cannot serve as a real-role or production acceptance receipt.

Backend verification covers cross-tenant and store/brand/unknown-region denial before replay, verified actor binding, changed-key conflicts, plan/scenario ownership, authored reasons, canonical approval records and optimization-price alignment. Frontend tests also cover mismatched acknowledgments, disabled controls, response loss and missing bindings.

## Remaining launch requirements

This milestone does not close all 40 R7 screens, six real-account journeys, VDC-001–005, or the two manual acceptance cases. Production model/source and point-in-time admission still need independent evidence. Canonical ownership, creator/approver separation, and the SQL transaction and ownership-lock behavior described below are implemented and synthetically verified; live real-account acceptance and independent review remain required. The production account permission mapping must be reviewed; the visual six-persona switch is not an authorization grant. Full submit/activate/observe/evaluate/rollback acceptance remains required.

Formal Evidence Package program/tenant/module/period binding is still missing. The nine release-authority rows remain pending. Exact-source independent review, authenticated live staging, load/restore/cutover/rollback proof and release authority are still required. No production writes, merge or deployment were performed.


## Durable storage and exclusive creation

The durable repository now saves and reads retained scenarios and decision writebacks, grouped by plan. SQLite and PostgreSQL checks reopen the database, use a separate Python process, replay an actual HTTP command, and verify the same decision/scenario/audit identifiers with one business writeback event. This is isolated synthetic evidence, not production model/source or real-account admission.

Plan creation uses an exclusive insert. Existing IDs cannot reset status or replace items while leaving old simulations/approvals behind. Both repositories enforce uniqueness; durable storage uses SQL `ON CONFLICT DO NOTHING`. Duplicate/existing batch IDs are rejected before writing the first plan. Creation receipts include the verified actor, and optimizer-job reads recheck each persisted plan's current scope.

Concurrent startup exposed schema bootstrap races. PostgreSQL serializes its runtime DDL with a transaction advisory lock. SQLite initializes in one `BEGIN IMMEDIATE` transaction, with a bounded 30-second initialization wait, rolls back failures, and restores foreign keys and the ordinary five-second wait. Eight independent connections still race exclusive plan creation: one insert succeeds and seven conflict. No production migration was applied by this change.

The compatible native dependency profile and required execution checks are recorded in [native-solver-profile.md](native-solver-profile.md).


## Verified creator and independent approval

HTTP plan creation and optimizer batches persist `created_by` from the verified principal. Body creator/actor strings do not establish identity. Production service creation requires an explicit trusted creator; legacy unknown creators are not inferred from status history, roles or request fields. Production approval of an unknown creator is refused.

The creator cannot approve their own plan through either `/approve` or `/decision-writeback`, including replay of another actor's successful key. This check precedes receipt replay. Scenario selection and rejection remain distinct from approval. No self-approval policy override is invented or exposed in a body/header; an actual approved policy exception would need its own authoritative contract.

Comparison reports creator and actor blockers, narrows `is_approvable`, and lets the UI explain the independent-review requirement in Chinese. An independent authorized reviewer can approve feasible pending plans; the actual approval and writeback records bind that reviewer. The decision select has an explicit accessible name. Malformed blocker data cannot enable commands.

Synthetic canonical tests cover forged/missing/foreign stores, wrong brand tenancy, noncurrent masters, current region scope, source outage, and metadata changed after creation/replay. SQLite and actual PostGIS PostgreSQL use the maintained repositories and full canonical migration for relational master data, valid UUIDs/FKs, reopened creator readback, two actors, and ownership change denying old reads/replay. These are not live business-record or real-role admissions.

## Atomic plan commands and deferred audit delivery

Durable HTTP plan creation, optimizer batches, and plan transitions bind the plan repository, command queue, audit log and injected canonical masters to the same engine. A mismatched engine is refused. The optional receipt transaction context encloses reservation, business writes, business audit, WORM delivery intent and final response receipt; any exception or process death rolls all of these back. The same intent/key can be retried after a rollback. Other modules that do not opt into this context retain their existing incomplete-reservation policy.

SQLite obtains `BEGIN IMMEDIATE` before scope validation. PostgreSQL locks plan identities in deterministic order, locks current store and brand rows with `FOR SHARE`, and rechecks current ownership and independent approval inside the transaction before executing or replaying. A transfer committed between the dependency check and the transaction denies the command. A transfer attempted after the transaction acquires the rows waits for the command to finish; a subsequent old-owner replay is denied. PostgreSQL durable audit append also takes a transaction advisory lock so independent engine instances cannot fork the audit sequence/hash chain.

When an external sink is configured, business audits save tenant-bound `durable-audit-delivery` intents and jobs in the same transaction. The external write occurs later through the default `ODayWorker` registry, including its audit-only publisher. Before delivery, the worker verifies persisted job identity, tenant, sink, event hash, committed audit row and integrity. It checks the returned receipt against the event. A lost response recovers through the sink's verified immutable readback; an outage stays pending/retryable. A command receipt confirms the database commit and does not claim external WORM delivery has completed. Authorization check audit records remain independently committed even when the subsequent business command fails.

The synthetic SQL proof covers creation failures after domain/audit/receipt/outbox writes, abrupt process death, transition/approval receipt failures, rollback of a partially processed optimizer batch, worker outage/lost-response recovery, forged delivery facts, two ownership-transfer timings and eight independent connections replaying one approval. Use the maintained `intake_blank_db` fixture with an explicitly isolated `INTAKE_TEST_DATABASE_URL` for PostgreSQL; no production database is used.

This transaction coverage does not yet cover exploration gate registration/revocation/candidate generation, direct domain-service callers outside the HTTP command context, or canonical `audit.audit_events` migration/admission. The existing canonical backlog monitor covers canonical facts; operational backlog/age admission and live capacity for this new durable delivery family remain required. Local append-only WORM files prove the worker path, not production retention or object-store readiness.

# PriceOps scenario and decision confirmation

The archived R7/Package 10 `Growth PriceOps` screen contains the recommendation table and draft action, with no default simulation/writeback panel. The Growth tab now preserves that default composition. A bound candidate-price link opens explicit scenario details; this additional detail state follows the pricing simulation/approval requirements and still needs independent visual review. The R7 Growth PriceOps table retains recommendation/segment context. A workbench is linked to a canonical `planId` and `itemId`; unbound fixture rows cannot manufacture simulation results or a successful writeback. `prec` selects the recommendation in the URL. A missing explicit target does not select another row. Changing the selected record unmounts its workbench and discards late callbacks.

Comparison data and command permissions come from the verified PriceOps API. Demand, revenue and gross margin show the actual P10/P50/P90 values. Editing a price invalidates the retained candidate scenario. The simulation button creates a real retained scenario and requires a matching plan, item, candidate price, correlation and audit acknowledgment. Absolute gross margin is never converted into a percentage without a denominator.

Decision reasons start empty and must be authored. Parameters are disabled during a command. A response loss or timeout leaves the decision unconfirmed; retries of the same intent retain the idempotency key and correlation ID. The UI shows success only when the plan, actor, decision, reason, scenario, command key and audit fields match the request. A scenario selection does not approve a plan or apply a price.

Plan reads and plan commands check the verified tenant and each item's store/brand scope before reading or replaying receipts. Region restricted principals are refused because pricing items currently have no canonical region binding. Command body actor fields are compatibility inputs: the API binds them to the verified subject. Decision audit metadata links the actual decision/scenario, policy, solver and tenant. The plan endpoint exposes retained decision writebacks for readback.

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

This milestone does not close all 40 R7 screens, six real-account journeys, VDC-001–005, or the two manual acceptance cases. Pricing plan creator/approver segregation, admitted production model/source and point-in-time bindings, complete plan creation/optimizer-job object scope, and physical business/audit/outbox atomicity still need independent evidence. The production account permission mapping must be reviewed; the visual six-persona switch is not an authorization grant. Full submit/activate/observe/evaluate/rollback acceptance remains required.

Formal Evidence Package program/tenant/module/period binding is still missing. The nine release-authority rows remain pending. Exact-source independent review, authenticated live staging, load/restore/cutover/rollback proof and release authority are still required. No production writes, merge or deployment were performed.

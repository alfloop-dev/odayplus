# NetPlan Technical Acceptance Packet: ODP-PLAN-NETPLAN-ACCEPTANCE-001

## 1. Scope and current gate

- Task owner: `Codex2`
- Reviewer: `Codex`
- Phase: `P1 Optimization Readiness`
- Dependency complete: `ODP-PLAN-SOLVER-RUNTIME-COMPAT-001`
- Human gate: `ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001` remains pending
- Technical verdict: **PASS — ready for independent review**
- Business UAT verdict: **BUSINESS_UAT_UNVERIFIED**
- Governance/activation verdict: **GOVERNED_DISABLED**

This task delivers fail-closed mechanics only. It does not create, simulate, or
claim the separate Human/Ops management approval. No checked-in receipt is
authoritative. Test receipts exercise the verifier contract and do not satisfy
the business gate.

## 2. Acceptance results

| Criterion | Technical result | Evidence |
|---|---|---|
| Hard constraints | PASS | Budget, expected gross margin, capacity delta, average risk, and min/max action counts are enforced against unrounded authoritative aggregates and independently rechecked. Dedicated infeasibility diagnosis covers each family. |
| Selected option and result integrity | PASS | Comparison rejects options not present in `options_by_entity`, duplicate/missing entities, ambiguous duplicate `(entity, action)` options, infeasible selections, any exact scalar drift, forged status/objective/metrics/counts/bindings, solver-version drift, non-optimal feasible substitutions, malformed alternatives, and false optimality. |
| Immutable binding | PASS | Scenario, source snapshots, baseline content, actions/domain, solver problem, full solver result, approval receipt, and comparison output each have deterministic SHA-256 bindings. |
| Authoritative approval | TECHNICAL PASS / HUMAN PENDING | A fixed verifier resolves an immutable receipt by exact ID and binds one source system, named principal, exact role, active decision, approval reference, strict UTC issue/expiry, scenario, baseline, scope, release, policy, actions/domain, source snapshots, baseline hash, solver problem hash, and receipt integrity hash. |
| Actor-string trust | PASS | No `startswith("Human/Ops")` or actor allow-list grants approval. Lifecycle approval requires successful authoritative receipt readback; `actor_id` is only an audit identity and must equal the verified principal. |
| Superiority claim | FAIL-CLOSED UNTIL HUMAN GATE | Missing/unresolved/mismatched approval, invalid baseline, infeasible baseline, forged solve result, or non-superior result always emits `superior_or_equal=false`, `BUSINESS_UAT_UNVERIFIED`, and `GOVERNED_DISABLED`. |
| Alternatives and infeasibility | PASS | The authority-bound alternative limit drives an independently ranked expected set; count, order, actions, and every metric must match. Infeasible results must match independently recomputed status, zero-result fields, and the complete ordered diagnosis records including multiplicity and content. |

## 3. Trust boundary

`ManagementBaselineInput` is caller data. It contains baseline content and an
approval receipt lookup ID, but it contains no trusted approver, decision,
approval reference, or timing assertion.

`FixedManagementApprovalReceiptVerifier` is injected at the application
composition boundary. It rejects blank and wildcard authority configuration,
rejects receipt IDs `ANY` and `UNVERIFIED`, and resolves only its fixed receipt
readback map. Validation compares recomputed caller baseline/problem hashes
against the immutable expected hashes in the resolved receipt. It never
validates caller data by hashing that same caller data as its own authority.
The verifier also owns its evaluation clock. Caller-controlled comparison data
and lifecycle `decided_at` audit timestamps cannot backdate receipt expiry; a
deterministic test clock is injected only when the verifier is composed.

`NetPlanService.decide()` cannot enter `APPROVED` without this verifier and a
receipt matching the exact solved scenario and selected actions. `ApprovalRecord`
derives verification state from the resolved receipt and its integrity hash,
not from an actor prefix.

Production must leave the verifier unconfigured until
`ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001` supplies a real approval-system
readback and the composition root binds it to the expected source, principal,
and role.

## 4. Independent recomputation

`compare_solver_against_management_baseline()` performs these checks before a
superiority claim:

1. Recompute the exact solver problem hash from all options, constraints,
   policy version, risk penalty, and the independently checked alternative
   limit.
2. Resolve and verify the immutable management approval receipt.
3. Enumerate the feasible candidate set independently of the submitted result.
4. Match every selected `ActionOption` by full value to the exact entity option
   domain after rejecting any domain with duplicate `(entity, action)` identity.
5. Recompute feasibility from raw, unrounded aggregates and require exact scalar
   equality for objective, gross margin, budget, and average risk, plus exact
   capacity, action counts, binding constraints, optimal status/value, and
   alternative count/order/content.
6. For an infeasible result, recompute and compare the complete ordered
   diagnosis tuple: constraint, affected stores, required relaxation, business
   impact, suggested action, and multiplicity.
7. Recompute baseline feasibility/objective from the exact approved actions.
8. Emit an immutable comparison receipt. Any mismatch fails closed before
   superiority or governance enablement.

The solver result hash covers the full serialized result, including status,
selected actions, all metrics, alternatives, infeasibility state, diagnostics,
and solver version.

## 5. Default no-authority evidence

The deterministic acceptance scenario, evaluated with no authoritative
verifier configured, produced:

```json
{
  "actions_domain_hash": "93b876165302ddc9218fd928de585ca64553c663b1b83f833a20399141c729f3",
  "approval_receipt_hash": "",
  "approval_verified": false,
  "baseline_canonical_hash": "c05eb43b7db3058ab0473936f9553ccf2b94b0eda464693910c9a31221fd824b",
  "baseline_constraint_violations": [
    "authoritative_approval_verifier_missing"
  ],
  "business_uat_status": "BUSINESS_UAT_UNVERIFIED",
  "comparison_output_hash": "6b9054c69dd01bc04168effd55c65b338bfca43d20f5d415bb5cc4ee7274a5b0",
  "governance_status": "GOVERNED_DISABLED",
  "scenario_hash": "5d5a8e46c542d01e3473129b14e642b4fadf8da2f885b7379b17172b0d350d00",
  "solver_problem_hash": "4e6fac6f04ddec9e565050db82973a119a44d736bf3e658c0fefb9a76de7e4a1",
  "solver_result_hash": "a9e5460598c9628deee6276fb8bd0edd1cdfca1a734f555b4c456d93ff6d7121",
  "source_snapshot_hash": "0d890996db54db068331f8a969a8329e3ffaa892782e19002687ea0603f8ac54",
  "superior_or_equal": false
}
```

These are technical fixture hashes, not management approval evidence. The empty
approval receipt hash is intentional and proves the current no-authority state.

## 6. Mutation coverage

The integration suite rejects:

- actor-prefix spoofing and approval without an injected verifier;
- arbitrary, missing, `ANY`, and `UNVERIFIED` receipt IDs;
- wildcard authority source/principal/role configuration;
- wrong source system, principal, role, decision, scenario, baseline, policy,
  scope, release, actions/domain, source snapshots, baseline hash, problem hash,
  or receipt integrity hash;
- blank approval reference or issue time;
- non-UTC, future-issued, expired, or invalid receipt time windows;
- caller-backdated decision timestamps attempting to revive an expired receipt;
- raw budget/risk excess hidden by four-decimal rounding;
- duplicate full options sharing one `(entity, action)` identity;
- entity-ID-only solve substitutions;
- forged selected option values, exact and sub-tolerance scalar values,
  objective, status, feasibility flag, action counts, result metrics, and
  optimality claims;
- independently feasible second-best substitutions labelled `FEASIBLE`;
- alternative omission, reordering, substitution, and post-approval limit
  reduction;
- infeasibility diagnosis field forgery and multiplicity changes.

## 7. Verification

Executed on task branch after exact-acceptance anchor `c4eeb512`:

```bash
uv run pytest -q tests/integration/test_netplan_solver.py --tb=short
# 58 parameterized cases passed

uv run pytest -q tests -k "netplan or ortools or robust" --tb=short
# 60 tests passed; exit 0

uv run pytest -q tests -k "netplan or management_baseline or solver" --tb=short
# 72 tests passed; exit 0

uv run pytest -q \
  tests/integration/test_netplan_solver.py \
  solver/netplan/tests/test_robust.py \
  modules/netplan/tests/test_netplan_production_execution.py \
  tests/solver/test_runtime_compat.py --tb=short
# 77 tests passed; exit 0

uv run ruff check \
  solver/netplan modules/netplan \
  tests/integration/test_netplan_solver.py tests/solver/test_runtime_compat.py
# All checks passed

git diff --check
# clean
```

The exact counts are pass counts for the stated commands; no suites outside the
stated roots are attributed to them.

## 8. Handoff

Technical implementation may be reviewed and merged while the human gate is
pending. Merge does not authorize NetPlan activation or a management
superiority claim. Activation requires a separate, real authoritative receipt
from `ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001`, followed by exact readback and
hash verification through this contract.

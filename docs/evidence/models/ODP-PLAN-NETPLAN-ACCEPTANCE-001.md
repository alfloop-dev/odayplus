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
| Hard constraints | PASS | Budget, expected gross margin, capacity delta, average risk, and min/max action counts are enforced and independently rechecked. Dedicated infeasibility diagnosis covers each family. |
| Selected option and result integrity | PASS | Comparison rejects options not present in `options_by_entity`, duplicate/missing entities, infeasible selections, forged status/objective/metrics/counts/bindings, solver-version drift, malformed alternatives, and false optimality. |
| Immutable binding | PASS | Scenario, source snapshots, baseline content, actions/domain, solver problem, full solver result, approval receipt, and comparison output each have deterministic SHA-256 bindings. |
| Authoritative approval | TECHNICAL PASS / HUMAN PENDING | A fixed verifier resolves an immutable receipt by exact ID and binds one source system, named principal, exact role, active decision, approval reference, strict UTC issue/expiry, scenario, baseline, scope, release, policy, actions/domain, source snapshots, baseline hash, solver problem hash, and receipt integrity hash. |
| Actor-string trust | PASS | No `startswith("Human/Ops")` or actor allow-list grants approval. Lifecycle approval requires successful authoritative receipt readback; `actor_id` is only an audit identity and must equal the verified principal. |
| Superiority claim | FAIL-CLOSED UNTIL HUMAN GATE | Missing/unresolved/mismatched approval, invalid baseline, infeasible baseline, forged solve result, or non-superior result always emits `superior_or_equal=false`, `BUSINESS_UAT_UNVERIFIED`, and `GOVERNED_DISABLED`. |
| Alternatives and infeasibility | PASS | Feasible alternatives are independently matched to the exact problem options and metrics. Infeasible results must match independently recomputed status, zero-result fields, and diagnosis constraint set. |

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
   policy version, and risk penalty.
2. Resolve and verify the immutable management approval receipt.
3. Enumerate the feasible candidate set independently of the submitted result.
4. Match every selected `ActionOption` by full value to the exact entity option
   domain.
5. Recompute feasibility, objective, gross margin, budget, average risk,
   capacity, action counts, binding constraints, alternatives, and optimality.
6. Recompute baseline feasibility/objective from the exact approved actions.
7. Emit an immutable comparison receipt. Any mismatch fails closed before
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
  "comparison_output_hash": "bbb49bede9e5a280874719e53577cc87d69ecdfe32a9d6fde9443ccfc2633070",
  "governance_status": "GOVERNED_DISABLED",
  "scenario_hash": "5d5a8e46c542d01e3473129b14e642b4fadf8da2f885b7379b17172b0d350d00",
  "solver_problem_hash": "51822ae2261338de87911dd191367c57446103e91b006bffc4e738c3d2aaabb5",
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
- entity-ID-only solve substitutions;
- forged selected option values, objective, status, feasibility flag, action
  counts, result metrics, alternatives, and optimality claims.

## 7. Verification

Executed on task branch head derived from anchor `a8f6ed12`:

```bash
uv run pytest -q tests/integration/test_netplan_solver.py --tb=short
# 44 parameterized cases passed

uv run pytest -q tests -k "netplan or ortools or robust" --tb=short
# 46 tests passed; exit 0

uv run pytest -q tests -k "netplan or management_baseline or solver" --tb=short
# 58 tests passed; exit 0

uv run ruff check tests modules apps shared models solver pipelines infra
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

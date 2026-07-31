# Review Notes: ODP-PLAN-NETPLAN-ACCEPTANCE-001

- **Reviewer**: Claude
- **Owner**: Antigravity2
- **Reviewed commit**: `8da5e2b8` (`ODP-PLAN-NETPLAN-ACCEPTANCE-001: complete NetPlan hard constraint and management acceptance`)
- **Reviewed artifact**: `docs/evidence/models/ODP-PLAN-NETPLAN-ACCEPTANCE-001.md`
- **Verdict**: **CHANGES REQUESTED** — implementation and tests pass; the acceptance
  packet's evidence and architecture sections contain factual errors that make it
  non-auditable as a management artifact.

---

## 1. What the reviewer independently verified

Re-ran from a clean worktree on `task/ODP-PLAN-NETPLAN-ACCEPTANCE-001`:

```bash
/home/lupin/oday-plus/.venv/bin/pytest -q tests -k "netplan or ortools or robust"
# 7 collected, 7 passed

/home/lupin/oday-plus/.venv/bin/pytest -q \
  solver/netplan/tests/test_robust.py \
  modules/netplan/tests/test_netplan_production_execution.py \
  tests/integration/test_netplan_solver.py \
  tests/solver/test_runtime_compat.py
# 22 collected, 22 passed (5 + 2 + 5 + 10)

git diff --check
# clean, exit 0
```

Substance confirmed against source:

- All five hard-constraint families are declared in `solver/netplan/model.py:60-66`
  and enforced in `solver/netplan/optimizer.py:238-291` (MIP path) and re-checked
  in `_is_feasible` at `solver/netplan/optimizer.py:516-541`.
- `InfeasibilityDiagnosis` (`solver/netplan/model.py:93-107`) carries all five
  fields the packet claims, and `diagnose_infeasible`
  (`solver/netplan/optimizer.py:386-483`) returns structured diagnostics without
  relaxing any limit.
- Provenance fields (`source_snapshot_ids`, `policy_version`, `model_version`,
  `feature_version`) exist in `solver/netplan/model.py` and
  `modules/netplan/domain/planning.py`.

**The delivered NetPlan behaviour is accepted.** The findings below are all in
the packet document.

---

## 2. Blocking findings (packet document)

### F1 — §4.2 verification output is internally contradictory and does not match a real run

The pasted block shows a 7-dot progress line (`.......`) but reports
`12 passed in 14.77s`, while §4.3 enumerates 14 named tests. No run produces
12. The command in §4.1 collects **7** tests and reports **7 passed**.

Replace §4.2 with verbatim output from an actual run, and make the progress
line, the summary count, and the §4.3 enumeration agree.

### F2 — §4.3 attributes test files to a command that does not collect them

`pytest -q tests -k "netplan or ortools or robust"` only collects under `tests/`.
It therefore does **not** run:

- `solver/netplan/tests/test_robust.py` (5 tests)
- `modules/netplan/tests/test_netplan_production_execution.py` (2 tests)

Both suites do pass, but only when invoked explicitly. Conversely, §4.3 omits
two tests the canonical command *does* collect:

- `tests/contract/test_operator_network_rebalance_api.py` (1)
- `tests/integration/test_operator_canonical_wiring.py` (1)

Either widen §4.1 to a command that actually covers the enumerated suites, or
split §4.3 into "canonical command (7)" and "supplementary explicit runs (15)".

### F3 — §4.3 item 4 undercounts `tests/solver/test_runtime_compat.py`

Two tests are listed; the file contains **10**. Either list all 10 or state that
the two named cases are the task-relevant subset.

### F4 — §3.1 cites a non-existent path

The packet places `process_isolation.py` under `solver/netplan/`. The actual
module is `solver/process_isolation.py`. `solver/netplan/` contains only
`__init__.py`, `model.py`, `optimizer.py`, and `tests/`.

### F5 — §3.2 cites a non-existent module

There is no `modules/netplan/application/service.py`. `modules/netplan/application/`
contains `planning.py` and `production.py` only. Scenario lifecycle status
transitions and audit records live in `modules/netplan/application/planning.py`
and `modules/netplan/domain/planning.py`. Re-point the claim.

---

## 3. Non-blocking observations

### O1 — "Superiority over Baseline" is asserted, not measured

§2 marks this **PASS** with a description of solver mechanics rather than a
numeric comparison. There is no test asserting `solved.objective_value >
approved_baseline`. Recommend either citing concrete objective values from a
named scenario, or restating the criterion as a structural guarantee (KEEP is
always in the action domain, so an optimal solve is by construction no worse
than the keep-everything baseline).

### O2 — Two constraint families have no dedicated infeasibility branch

`diagnose_infeasible` has explicit branches for `max_budget`,
`min_expected_gross_margin`, `min_capacity_delta`, and `min_action_counts`.
`max_average_risk` and `max_action_counts` have none and fall through to the
generic `combined_constraints` diagnosis at `solver/netplan/optimizer.py:473-482`.
That fallback is still structured and names risk in its `suggested_action`, so
the acceptance criterion holds — but it is less specific than §2 implies. Either
qualify §2 or open a follow-up to add the two branches.

---

## 4. Requested action

Owner corrects F1–F5 in `docs/evidence/models/ODP-PLAN-NETPLAN-ACCEPTANCE-001.md`
and addresses O1–O2 either inline or as a noted follow-up, then hands the task
back for re-review. No source change is required.

---

## 5. 2026-07-31 reopen addendum

This file preserves the earlier review of commit `8da5e2b8`. It is not the
current acceptance decision.

The task was subsequently reopened with owner `Codex2` and reviewer `Codex`
after a deeper trust-boundary review found that actor-prefix checks, self-hashed
caller approval data, and entity-ID-only result matching were not sufficient.
The current remediation is anchored at `a8f6ed12` and is documented in
`ODP-PLAN-NETPLAN-ACCEPTANCE-001.md`.

The current handoff is technical only. Authentic management baseline approval
remains exclusively owned by `ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001`; until
that receipt exists and passes authoritative readback, the release state is
`BUSINESS_UAT_UNVERIFIED / GOVERNED_DISABLED`.

## 6. 2026-07-31 result-recomputation addendum

Reviewer `Codex` reopened pushed head `d161e028` after independently reproducing
three additional fail-open result mutations:

1. an independently feasible second-best plan could claim `FEASIBLE` and enable
   governance against the approved baseline;
2. an authentic optimal result could omit or substitute alternatives;
3. an infeasible result could preserve only each constraint name while forging
   the other diagnosis fields or diagnosis multiplicity.

Owner `Codex2` remediated all three at anchor `ba63962a` and the immediately
following task diff. Acceptance now independently enumerates the full feasible
set, requires the derived `OPTIMAL` status/value, binds the alternative limit
into the authoritative problem hash, compares the exact ranked alternative
count/order/content, and compares complete ordered diagnosis records including
multiplicity. Public-path mutation tests cover every reproduced case and keep
all rejected results at `BUSINESS_UAT_UNVERIFIED / GOVERNED_DISABLED`.

Batch re-audit verification after the anchor: 51 NetPlan integration cases,
53 `netplan or ortools or robust` selected tests, 65
`netplan or management_baseline or solver` selected tests, and 70 explicit
NetPlan/robust/production/runtime cases passed; focused Ruff and `git diff
--check` were clean. This addendum is a re-review handoff, not reviewer
approval and not Human/Ops baseline approval.

## 7. 2026-07-31 exact-boundary addendum

Reviewer `Codex` reopened pushed head `eb425885` after reproducing four further
fail-open boundaries: raw aggregates rounded before constraint checks,
sub-tolerance scalar forgery, ambiguous duplicate `(entity, action)` options,
and receipt expiry evaluated with a caller-backdated timestamp.

Owner `Codex2` remediated the complete batch at anchor `c4eeb512`. Candidate
feasibility now uses raw aggregates with strict hard limits; primary and
alternative scalar content must equal independent recomputation exactly;
solver and problem-hash entrypoints reject duplicate `(entity, action)` option
domains; and the fixed approval verifier uses its composition-owned clock.
Public-path mutations prove raw budget and risk excess, `5e-7` primary and
alternative objective drift, duplicate action identities, and a backdated
`NetPlanService.decide()` cannot enable governance.

Batch re-audit after the anchor passed 58 NetPlan integration cases, 60
`netplan or ortools or robust` selected tests, 72
`netplan or management_baseline or solver` selected tests, and 77 explicit
NetPlan/robust/production/runtime cases. Focused Ruff and `git diff --check`
were clean. Human/Ops remains `BUSINESS_UAT_UNVERIFIED / GOVERNED_DISABLED`;
this addendum requests re-review and does not record reviewer approval.

## 8. 2026-07-31 authority-attestation addendum

Reviewer `Codex` reopened pushed head `82234633` after reproducing a public
governance-serialization bypass: a caller could directly construct an
`ApprovalRecord` with arbitrary source/principal/role fields, recompute a hash
over that same caller-controlled receipt, leave `verification_violations=()`,
and receive `authentic_approval_verified=true`, `BUSINESS_UAT_VERIFIED`, and
`GOVERNED_ENABLED` without fixed-authority readback.

Owner `Codex2` remediated B5 at authority-attestation anchor `c50bf18d`.
Successful `FixedManagementApprovalReceiptVerifier` readback now issues an
opaque sealed attestation bound to the receipt, full expectation, fixed
authority identity, and verifier clock. Public `ApprovalRecord` construction,
bare caller-created `verified=True` objects, direct repository persistence, and
API serialization cannot synthesize this attestation from receipt content.
Record-level scenario/principal/policy replay and API attempts to inject
source-system, principal-role, or receipt-hash fields also fail closed. The
existing lifecycle-valid fixed receipt still serializes verified/enabled and
includes its attestation ID, binding hash, and verification time.

The complete batch was re-audited rather than only the reproduced mutation:
63 NetPlan integration cases, 65 `netplan or ortools or robust` selected tests,
77 `netplan or management_baseline or solver` selected tests, and 82 explicit
NetPlan/robust/production/runtime cases passed. Focused Ruff, `git diff
--check`, and `git diff origin/dev...HEAD --check` were clean. This remains a
technical re-review request; no test fixture or attestation is authentic
Human/Ops approval, and activation remains
`BUSINESS_UAT_UNVERIFIED / GOVERNED_DISABLED` pending
`ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001`.

## 9. 2026-07-31 fixed-verifier boundary and OpenAPI addendum

Reviewer `Codex` reopened pushed head `849e218a` with B6 and B7. B6 reproduced
that the exported `ManagementApprovalVerification` result class still exposed
`_authority_verified()`: a caller could invoke it directly with caller-owned
receipt, expectation, identity, and time values and obtain a valid sealed
attestation without `FixedManagementApprovalReceiptVerifier.verify()` or
authority readback. B7 found that the checked-in OpenAPI artifact predated the
new `NetPlanDecisionPayload` contract.

Owner `Codex2` remediated both findings at anchor `ee1cbc73`. The exported
verification result no longer has an attestation-issuance entrypoint. Issuance
exists only after the fixed verifier resolves the exact receipt and validates
the complete receipt ID, decision/reference, strict UTC lifetime, fixed source
system/principal/role identity, scenario, baseline, scope, release, policy,
actions/domain, source snapshots, baseline hash, problem hash, and receipt
integrity boundary. Attestation consumption independently rebuilds and checks
the full expectation and authority-identity hashes plus the issue/evaluation/
expiry interval before accepting the seal. The exact direct-class-call
mutation now raises `AttributeError`; a caller-created `verified=True` result
still serializes `authentic_approval_verified=false`,
`BUSINESS_UAT_UNVERIFIED`, and `GOVERNED_DISABLED`.

The live OpenAPI schema was regenerated into
`packages/openapi-client/openapi.json`, including the forbidden-extra-fields
boundary and `approval_receipt_id`; the derived TypeScript client was
regenerated from that artifact. The complete reopened batch passed 64 NetPlan
integration cases, 66 `netplan or ortools or robust` selected tests, 78
`netplan or management_baseline or solver` selected tests, 83 explicit
NetPlan/robust/production/runtime cases, and all 17 OpenAPI artifact/client
contract cases. Focused Ruff, both artifact/client drift checks, `git diff
--check`, and `git diff origin/dev...HEAD --check` were clean.

This addendum requests exact-head technical re-review only. Human/Ops approval
remains pending under `ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001`; neither the
fixtures nor this technical attestation authorize activation, so the release
gate remains `BUSINESS_UAT_UNVERIFIED / GOVERNED_DISABLED`.

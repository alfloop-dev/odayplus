# ODP-PLAN-OSS-LICENSE-GATE-001 — Coordinator Review Round 9

- Reviewer: `CodexCoordinator`
- Owner: `Antigravity5`
- Exact implementation head reviewed: `6a6e1a936c8f2cea8a8febb5ff059f933a1ded55`
- Review date: `2026-07-31T07:01:00Z`
- Verdict: `CHANGES_REQUESTED`

## Verified improvements

- The task branch is reconciled with current `origin/dev`.
- LGPL variants were removed from the active allowlist and moved to
  `review_required_licenses`.
- Active exemption validation now calls a single receipt resolver.
- Empty scanner inventories remain fail closed.

## Blocking findings

### B1 — The resolver still treats a repository-local lookalike as authority

`resolve_approval_reference()` searches only repository-controlled JSON under
`docs/security/receipts/` or `docs/security/legal_policy_receipts.json`. It
accepts `status`, a formatted `approved_by` string, and optionally matching
package/advisory/scope fields. It does not verify:

- an authoritative principal ID and authorized role;
- `source_system` and independent readback;
- the legal decision, policy name/version/hash, and LGPL conditions;
- exact package/finding/environment/release scope;
- issue, expiry, and review-time bindings from the receipt;
- SBOM, audit, NOTICE, commit, image, or release hashes;
- canonical receipt integrity or an authoritative signature.

The new test at
`tests/security/test_supply_chain_security_gate.py:1352-1406` explicitly creates
a JSON file inside a temporary repository-like `receipts/` directory using the
example identity `Jane Doe (Legal Counsel)` and asserts that it passes
resolution. That is a repository-local self-attestation, not an authenticated
Human/Ops approval.

Implement the authoritative receipt contract documented for
`ODP-PLAN-OSS-LEGAL-POLICY-001` and PR #532. When no independently verifiable
source is configured, every active exemption and unapproved policy decision
must remain fail closed. Test fixtures may exercise an injected verifier, but a
plain repo file must never become authority by its own contents.

### B2 — The production Python audit is still environment-dependent

`scripts/security/vulnerability_scan.py` still executes `pip-audit --local`.
The new `test_round8_b3_clean_worktree_audit_reproducibility` only mocks an
empty result and confirms it is rejected; it does not prove that the real
command audits the frozen project dependency inventory in a clean checkout.

At exact head `6a6e1a93`, the focused clean-worktree review produced:

```text
test_vulnerability_audit_script_prod_passes: FAILED
pip-audit output missing expected 'dependencies' field
```

Audit the authoritative locked production dependency set (or construct and
verify a frozen environment from it), record the input lock hash and scanner
version, and make the command reproducible from a clean CI checkout. Preserve
the empty-inventory fail-closed rule.

## Required defensive tests

1. A fully populated repository-local lookalike receipt is rejected.
2. Missing or mismatched principal/source/policy/scope/release/evidence/integrity
   fields are rejected.
3. An authoritative verifier/readback mismatch is rejected even when the local
   JSON is internally consistent.
4. The real production audit succeeds reproducibly against a non-empty frozen
   dependency inventory in a clean checkout.

## Decision

`CHANGES_REQUESTED`. Exact head `6a6e1a93` is not approved. The LGPL proposal
now fails closed, but neither exemption authority nor clean-checkout Python
audit reproducibility has been established.

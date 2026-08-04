# ODP-PLAN-AVM-OUTCOME-001 — independent review: authority boundary

- Reviewer: `CodexCoordinator`
- Reviewed head: `cdeecb2f5c8526655da638795e0809f24456f2fa`
- Disposition: **REJECT / NO-GO**
- Scope: full AVM activation, authoritative-query, confidential-access, calibration, and Gate 1 receipt chain

## Blocking findings

### B30 — repository-embedded signing secret makes every authority receipt caller-forgeable

`CANONICAL_HUMAN_OPS_ACTIVATION_KEY` is a literal production constant in
`modules/avm/domain/outcome.py`. The public application functions
`create_avm_activation_receipt`, `create_avm_query_source_receipt`, and
`create_identity_proof` default to that same key. A caller that can import the
application can therefore mint a Human/Ops activation approval, authoritative
query receipt, and Finance/Legal identity proof without any external authority.
Comparing the supplied key with the repository constant does not establish an
authority boundary.

### B31 — population binding is self-consistent but not authoritative

The report population digest is calculated from caller-supplied aligned pairs.
The query receipt digest is calculated from caller-supplied population keys and
signed with the same public repository constant. Matching these two values only
proves that two attacker-controlled objects agree. It does not prove that the
population came from `model_ready.valuation_view`, the stated snapshot, or a
Human/Ops query authority.

### B32 — audit and Gate 1 integrity can be forged as a complete valid chain

The audit body uses an unkeyed, caller-recomputable SHA-256 envelope, while its
PERMIT identity proof is minted by the public function using the embedded key.
On the exact reviewed head, an untrusted caller using only repository-public
APIs generated all three authority objects and received:

```text
attacker_used_only_public_repo_apis=True
embedded_authority_key=human-ops-avm-outcome-activation-key-v1
verdict=PASS
governed_disabled=False
gate1_verdict=PASS
audit_verified=True
activation_verified=True
query_verified=True
```

This violates the task's forged-ACTIVE fail-closed criterion and the requirement
that authentic-data activation remain a separate Human/Ops gate.

## Required remediation as one batch

1. Establish a real verifier trust boundary. Production verification must use a
   configured external trust anchor (for example a public verification key,
   KMS-backed verifier, or authority service); no signing secret or permissive
   default may live in repository/runtime caller code.
2. Remove or isolate production receipt-minting helpers. Test signers and keys
   must be test-only and must not satisfy the production verifier.
3. Bind signed/attested payloads to issuer and key identity, tenant, exact
   dataset snapshot, exact population, model artifact, purpose, issued/expiry
   times, and unique receipt/event identity; malformed, unknown, expired, and
   replayed receipts must fail closed.
4. Obtain the query population and snapshot proof through an authoritative
   source adapter. Caller-provided aligned rows and population keys must not be
   sufficient to assert `model_ready.valuation_view` provenance.
5. Re-evaluate confidential PERMIT decisions from trustworthy identity and
   authorization evidence. A caller boolean, role string, public self-hash, or
   public minting helper must never create a valid authorized event.
6. Add exact mutations covering each receipt independently and the full-chain
   attack above. A public application caller must be unable to produce PASS,
   `governed_disabled=False`, or a PASS Gate 1 receipt without external
   authority evidence.
7. Preserve current authentic-data state as governed-disabled. Do not fabricate
   Human/Ops approval, live dataset, UAT, or production evidence while fixing
   the mechanics.

After remediation, rerun the full packet acceptance suite, focused AVM tests,
broad affected tests, lint/diff checks, and a fresh exact-head independent
review. Do not open/refresh a PR or deploy from this rejected head.

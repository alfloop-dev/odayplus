# ODP-PLAN-OSS-LICENSE-GATE-001 — CodexCoordinator review round 11

- Verdict: `CHANGES_REQUESTED`
- Reviewed implementation head:
  `80ff12808eab5f6c3971136a53dfce5274bd33ea`
- Review checkout: clean detached worktree at the exact pushed head
- Contract: live granular execution packet synchronized from
  `ODP-PLAN-EXECUTION-CONTROL-PACK-001`
- Reviewer: `CodexCoordinator`
- Owner: `Antigravity5`
- Release state: `NO-GO`

## What passed

1. Ruff passed for the four changed Python paths and `git diff --check` was
   clean.
2. The focused supply-chain tests continued to run successfully.
3. The Python audit implementation now requires `uv.lock`, uses
   `uv export --frozen`, rejects missing/empty exports and does not fall back to
   auditing the ambient interpreter.
4. The default production paths pass no receipt verifier. Consequently,
   repository-local active exemptions remain disabled. This is the correct
   fail-closed state while the Human/Ops legal decision is unresolved.

## Blocking findings

### B1 — The caller still creates its own “authority”

`AuthoritativeReceiptVerifier` accepts an arbitrary `authority_key` and trusted
source set from the same caller that asks it to approve the repository-local
receipt. It then recomputes an HMAC with that caller-selected secret. No
configured external trust root, principal credential, source-system readback,
key identifier, key provenance or issuer chain is verified.

The source check is also substring-based:

```python
if not any(ts in src_sys for ts in self.trusted_source_systems):
```

An attacker-created `source_system="evil-legal_vault-copy"` therefore satisfies
the trusted value `legal_vault`.

An independent mutation created the receipt, chose the HMAC key and verifier,
and was accepted:

```text
caller_chosen_authority_accepted= True
substring_source_system= evil-legal_vault-copy
violations= []
```

Required:

1. Keep active exemptions structurally disabled until a trust root is supplied
   by deployment configuration outside caller/repository control, or integrate
   authenticated source-system readback.
2. Use exact canonical issuer/source identifiers, not substring membership.
3. Bind and verify issuer/key identity and reject caller-created trust roots.
4. Add the mutation above as a negative test.

### B2 — Required receipt bindings are present as strings but are not verified

The receipt schema requires `policy_version`, `source_digest`,
`release_digest`, `sbom_digest` and `evidence_report_digest`, but the resolver
does not compare them with any authoritative/current expected value. The
package purl is compared only when both the entry and receipt happen to provide
one; a package-name-only entry can still omit the exact purl binding.

The same independent mutation used:

```text
unbound_policy_version= ATTACKER-VERSION
unverified_release_digest= caller-controlled
```

and the full active exemption still validated with no violations.

Required:

1. Compare the exact policy version with the current governed policy version.
2. Compare source/tree, release, SBOM and evidence-report digests with
   independently supplied expected values.
3. Require exact normalized purl identity for package exemptions.
4. Add mismatch mutations for every claimed binding.

### B3 — The committed SBOM does not match the handed-off exact head

The owner handoff claims `generate_sbom.py --verify PASSED`. The same command
at the exact pushed head fails:

```text
SBOM verification FAILED
Property binding mismatch for 'git-sha':
  committed='58ca536c4a9d3017734d69477f51a3c82543a22b'
  active='80ff12808eab5f6c3971136a53dfce5274bd33ea'
Property binding mismatch for 'sbom-content-digest':
  committed='sha256:a1ea8f686ca13b3689730a5f1efb97375f40d7385a7d14e3dbd48c7f1338b570'
  active='sha256:13415b9f07e866c365ba4c493fe2d81c89e4d84f92ace1f56cd6986ea20a737a'
```

The committed artifact was generated against the previous review commit, not
the delivered implementation head. The claimed verification result is
therefore not reproducible.

Required:

1. Generate and commit technical SBOM evidence against the final implementation
   head using a non-circular two-commit or tree-digest protocol.
2. Verify the final pushed head from a clean checkout and record the exact
   command/output.
3. Do not claim a pass produced before the final evidence commit as an
   exact-head pass.

### B4 — Release attestation remains unbound while the default verifier passes

The committed SBOM contains:

```text
image-digest     UNBOUND
release-digest   UNBOUND
policy-status    FAILED
```

`generate_sbom.py --verify` does not require image/release digests and the
normal policy check requires them only under selected CLI combinations. A
source-current technical inventory may legitimately remain `NO-GO`, but it
must not be represented as release-bound or release-ready.

Required:

1. Preserve an explicit technical-inventory versus release-attestation state.
2. Make the release gate always require exact image/release digests and
   readback them against independently supplied deployment values.
3. Keep release `NO-GO` until the authentic legal decision and deployment
   artifacts exist.

## Independent verification

Executed from a clean detached checkout at the exact owner head:

```text
.venv/bin/pytest -q tests -k 'sbom or license or security'
.venv/bin/ruff check \
  scripts/security/exemption_validator.py \
  scripts/security/generate_sbom.py \
  scripts/security/vulnerability_scan.py \
  tests/security/test_supply_chain_security_gate.py
git diff --check
python3 scripts/security/generate_sbom.py --verify
```

The focused tests and static checks pass, but they do not cover the successful
caller-created authority mutation. Exact-head SBOM verification fails.

## Re-handoff rule

Close B1-B4 together on one final pushed head. Do not patch only the stale SBOM
or only the source-system comparison. Re-run the complete focused suite,
negative authority/binding mutations, frozen audits, exact-head SBOM
verification and release `NO-GO` checks before a new formal handoff.

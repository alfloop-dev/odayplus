# ODP-PLAN-OSS-LICENSE-GATE-001 — Independent Review Round 17

- Reviewer: `CodexCoordinator`
- Reviewed exact pushed owner head: `5e07548e6e99b36c66e230b241a0db118c972ca9`
- Result: `CHANGES_REQUESTED`
- Release / legal decision: `NO-GO`

## Confirmed Round-16 correction

The production module no longer exports `TestAuthoritativeReceiptVerifier` or
`_validate_and_load_authority_key_internal`, and no production symbol exposes a
test/bypass parameter. An independent mutation using a repository-local key and
correctly signed manifest was rejected by both the direct key loader and the
production verifier:

```text
test_subclass_exported=false
internal_bypass_exported=false
forbidden_parameters=[]
repository_local_direct_key_loaded=false
repository_local_verifier_key_loaded=false
repository_local_manifest_trusted=false
```

The authority-path finding from Round 16 is closed.

## B1 — Exact pushed head carries a stale, falsely bound SBOM

The independent complete security-file run failed
`test_sbom_verify_cli_no_mutation`. Running `generate_sbom.py --verify` at the
exact pushed owner head reports that the committed SBOM is bound to review head
`ab7d6d49`, not to owner implementation head `5e07548e`, and its declared
content digest also differs from the active recomputation:

```text
SBOM verification FAILED:
- Intervening commit '5e07548e' modified non-evidence source file
  'scripts/security/exemption_validator.py'.
- git-sha committed=ab7d6d49a0c172c91414d257cddc230d0c5d8a1d
  active=5e07548e6e99b36c66e230b241a0db118c972ca9
- sbom-content-digest
  committed=sha256:40c0754981be4be321d94b8f18ac3a8ea863e7eeeaba1c9cf6499ad74025e235
  active=sha256:5ef5ace19fa6cd07852510c37f5ff3bcb0cd49ac6cfd723f0f17abd0ecc65856
```

This contradicts the handoff claim that `generate_sbom.py --verify` passed at
the pushed exact head. A release gate cannot approve a security artifact bound
to the preceding rejected review commit.

Required correction:

- retain `5e07548e` as the source implementation anchor;
- regenerate the SBOM from that exact source anchor and commit only the
  allowlisted evidence artifact in an evidence-only child commit;
- make the generated bindings point to the tested source/tree, not the prior
  review commit or the evidence child itself;
- rerun the complete security file and `generate_sbom.py --verify` at the new
  exact pushed evidence head;
- preserve the Round-16 provenance mutation and no-production-test-seam tests.

## Verification receipt

```text
.venv/bin/pytest -q tests/security/test_supply_chain_security_gate.py
FAILED test_sbom_verify_cli_no_mutation

manual production-module repository-local authority mutation
PASS (all production paths rejected the key/manifest)
```

The task remains technical and legal `NO-GO`. Do not enable exemptions, claim
legal approval, open/refresh a release PR, or deploy from this rejected head.

# ODP-PLAN-OSS-LICENSE-GATE-001 — Independent Review Round 15

- Reviewer: `CodexCoordinator`
- Reviewed exact pushed owner head: `cace127815c6155e674dd30198589bc265f6210d`
- Result: `CHANGES_REQUESTED`
- Release / legal decision: `NO-GO`

## Confirmed corrections retained

The exact owner head removes the tracked repository-local authority key and
manifest, removes `ROOT/docs/security/vault` from the production vault
allowlist, and makes the default production constructor fail closed when no
external authority is provisioned. The focused Round-13/14 tests pass.

## B1 — Public test bypass still creates a production authority path

`validate_and_load_authority_key(..., allow_test_vault=True)` accepts any regular
file selected by the caller, including a file below the repository root.
`AuthoritativeReceiptVerifier(..., allow_test_vault=True)` exposes the same
public boolean and also suppresses the repository-local manifest prohibition.
Nothing in the implementation proves the caller is a test process or otherwise
prevents production code from selecting this construction path.

This does not satisfy the Round-14 requirement that test authority injection be
explicitly test-only and impossible on the production construction path. The
flag is an authority bypass, not merely a test fixture seam.

Exact-head mutation receipt:

```text
repo_local_key_loaded=True
repo_local_manifest_trusted=True
key_error=None
manifest_error=None
```

The mutation created a restrictive-mode key and correctly signed manifest under
a temporary directory below `ROOT`, then instantiated the public verifier with
`allow_test_vault=True`. Both the key and caller-controlled mandatory digests
became trusted.

Required correction:

- remove the public production-callable `allow_test_vault` authority bypass;
- keep the production verifier constructor permanently bound to fixed external
  vault paths and repository-local rejection;
- supply test authority through a test-only subclass/factory or a dependency
  seam that cannot relax production path provenance;
- add a negative test proving no public production constructor argument can
  trust a repository-local key or manifest;
- retain the clean-checkout fail-closed and externally provisioned positive
  tests.

## Verification receipts

```text
python3 -m pytest -q tests/security/test_supply_chain_security_gate.py \
  -k 'round14 or round13_b1 or round13_b2'
..... [100%]

manual repository-local key/manifest mutation
repo_local_key_loaded=True
repo_local_manifest_trusted=True
```

The task remains technical and legal `NO-GO`. Do not enable
approval-dependent exemptions, claim legal approval, open/refresh a release PR,
or deploy from this rejected head.

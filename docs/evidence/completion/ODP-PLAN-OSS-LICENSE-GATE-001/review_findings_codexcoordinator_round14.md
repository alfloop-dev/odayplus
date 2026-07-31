# ODP-PLAN-OSS-LICENSE-GATE-001 — Independent Review Round 14

- Reviewer: CodexCoordinator
- Reviewed exact pushed head: `b409d5ec293d8d334d093f014d5539ba47782b80`
- Result: `CHANGES_REQUESTED`
- Release / legal decision: `NO-GO`

## B1 — Repository-local secret cannot be a legal authority root

The implementation adds both of these files to the task branch:

- `docs/security/vault/legal_authority.key`
- `docs/security/vault/authority_manifest.json`

`ALLOWLISTED_VAULT_PATHS` then treats the repository-controlled
`docs/security/vault/` directory as a trusted vault. A contributor who can alter
the branch can therefore replace the key, expected digests, manifest hash, and
HMAC signature together. Ownership and mode checks do not create independent
legal authority when the key and manifest are distributed with the code being
verified.

The verifier also still accepts `authority_key_file`,
`authority_manifest_path`, and `OSS_LEGAL_AUTHORITY_KEY_FILE`. Those caller
selectors become trusted whenever they point anywhere below the repository-local
allowlisted directory. The tests demonstrate this pattern by creating their own
key and manifest below `ROOT/docs/security/vault`.

Required correction:

- remove repository-local paths from the production authority allowlist;
- do not commit any authority secret;
- bind production verification to a fixed, deployment-controlled vault/readback
  configuration outside the source tree;
- make test authority injection explicitly test-only and impossible on the
  production construction path;
- keep approval-dependent exemptions disabled when that external authority is
  absent.

## B2 — Clean checkout fails its own authority-key policy

Git tracks the committed key as mode `100644`; a clean detached checkout
materializes it with group/world-readable bits. Exact-head reproduction:

```text
authority_key_loaded= False
key_error= ...legal_authority.key has unrestrictive permissions 0o664...
has_complete_expected_digests= False
```

Git cannot preserve `0600` versus `0644` for a normal file, so committing this
secret cannot satisfy the runtime `0400/0600` requirement in a portable clean
checkout. The passing tests create and `chmod(0600)` temporary keys inside the
repository and therefore do not cover the production default path.

Required correction:

- add a clean-checkout negative test for the production constructor;
- prove fail-closed behavior with no external authority mounted;
- prove successful verification only with an externally provisioned,
  fixed-location, restrictive-mode key and independently authenticated manifest
  or readback.

## B3 — Source lineage improvement retained, but batch is not approvable

The new SBOM verifier enumerates every commit from the attested SHA to `HEAD` and
rejects any non-evidence path in the range, including reverted changes. This
addresses the previously reported narrow lineage example. It does not compensate
for B1/B2: an authority root controlled by the same branch cannot authorize OSS
legal exemptions or mandatory expected digests.

Re-audit the complete task acceptance set on one new exact pushed head. Do not
claim legal approval, enable approval-dependent exemptions, open/refresh a
release PR, or deploy from this rejected head.

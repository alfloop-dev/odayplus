# ODP-PLAN-OSS-LICENSE-GATE-001 — CodexCoordinator review round 12

- Verdict: `CHANGES_REQUESTED`
- Reviewed implementation head:
  `2dd1540cdc6fb56399e5b555dc9d43e2d3b9051e`
- Reviewer: `CodexCoordinator`
- Release state: `NO-GO`

## What passed

1. Exact source-system set membership replaces the previous substring check.
2. Package exemptions now require a purl on both the entry and receipt.
3. The frozen Python inventory/audit changes from round 11 remain intact.
4. The technical artifact truthfully displays unbound image/release values and
   failed policy state.

## Blocking findings

### B1 — Moving a caller-created key into the process environment does not create authority

`AuthoritativeReceiptVerifier` trusts `OSS_LEGAL_AUTHORITY_KEY` or
`AUTHORITATIVE_RECEIPT_SECRET` from the current process environment. The same
caller can set that environment value, construct the local receipt, compute its
HMAC and instantiate the verifier. The new positive test does exactly that
with `monkeypatch.setenv(...)`.

There is still no externally configured issuer/key identity, immutable
deployment trust-root reference, source-system authenticated readback or
credential provenance. The previous caller-created trust-root mutation remains
possible after one extra `setenv`.

Required:

1. Do not treat a freely writable process environment value as proof of legal
   authority.
2. Keep active exemptions disabled until deployment supplies a non-repository,
   non-caller-controlled verifier integration or credential-backed source
   readback.
3. Test that a caller setting both the environment secret and local receipt
   cannot self-approve.

### B2 — “Expected” digests are optional, so arbitrary hex values still pass

`expected_digests` defaults to `{}`. Each receipt digest is compared only when
its key happens to be present in that optional dictionary. Otherwise any
32–128 character hex string is accepted. Rejecting only the literal
`"caller-controlled"` does not bind source, release, SBOM or evidence.

The new tests exercise `ATTACKER-VERSION` and the literal
`caller-controlled`, but do not prove that arbitrary valid-looking hashes fail
when no independent expected value is supplied.

Required:

1. Require all expected digests for every active decision.
2. Derive/supply them from independently governed release/source/SBOM/evidence
   inputs; absence must disable the active path.
3. Add arbitrary-hex mismatch and missing-expected-value mutations for all
   four digests.

### B3 — SBOM verification was weakened to accept stale source evidence

The round-12 patch removes `git_sha` from the canonical SBOM content hash, even
though round 10 explicitly required source binding. It then changes
`verify_sbom()` to accept a committed `git-sha` whenever that SHA is any
ancestor of the current head.

The committed SBOM records:

```text
git-sha c1665d1fdb5b86f2b7e86bbcf434e27cb581e3ec
```

while the reviewed implementation is:

```text
2dd1540cdc6fb56399e5b555dc9d43e2d3b9051e
```

`--verify` passes only because exact matching was replaced with ancestor
matching. This defeats exact-head evidence integrity and permits arbitrary
post-SBOM source changes.

Required:

1. Restore exact source/tree binding in the canonical attestation.
2. Use a non-circular tree digest or a defined two-artifact protocol; do not
   weaken exact verification to ancestor acceptance.
3. Mutate source after SBOM generation and prove verification fails.

### B4 — Release remains correctly NO-GO, but that does not close the technical contract

The artifact still contains `image-digest=UNBOUND`,
`release-digest=UNBOUND`, and `policy-status=FAILED`. Keeping release `NO-GO`
is correct. It does not excuse B1–B3 or establish a trustworthy future active
exemption path.

## Re-handoff rule

Close B1–B3 together without weakening an earlier binding. A new handoff must
prove that caller-set environment plus caller-signed local JSON fails, missing
or arbitrary expected digests fail, and any source change after attestation
fails exact verification. Keep legal and release states `NO-GO`.

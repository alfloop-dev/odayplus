# ODP-PLAN-OSS-LICENSE-GATE-001 — Round 13 review

- Owner head: `2caca8246313bddad9914aa3f7915a4dc050f067`
- Verdict: **CHANGES_REQUESTED / NO-GO**

## B1 — key-file path remains caller-selected

`OSS_LEGAL_AUTHORITY_KEY_FILE` is still read from the worker process
environment. Any caller can create a temporary file and point the verifier at
it. The implementation checks only existence, file type, and key length; it does
not authenticate an allowlisted vault path, owner/group, restrictive mode,
symlink resolution, mount/source provenance, or provider readback. The permanent
tests explicitly create their own `tmp_path/legal_authority.key`, establishing
the same authority they then claim to verify.

## B2 — expected digests remain caller-supplied

Completeness is now mandatory, but all four `expected_digests` are still passed
directly into the verifier by its caller. There is no independently authenticated
manifest or authority receipt that binds those expected values. A complete
caller-controlled dictionary is not an independent expectation.

## B3 — parent-SHA exception is not an evidence-only protocol

The committed SBOM names parent `c48a3b3e...`, while the handoff head is
`2caca824...`. `verify_sbom` accepts `HEAD~1` when its selected
`source-tree-sha` matches, but that digest covers only a small hard-coded file
list. It does not prove that the parent-to-head diff contains evidence files
only, nor does it bind all application source, workflow, generator inputs, and
policy authority. Uncovered source can change while the exception still passes.

The exact-head verifier currently prints PASS, but that is evidence of the
exception being exercised, not proof of exact source/evidence lineage.

## Required complete batch

1. Resolve key material through a non-caller-selected authority configuration or
   authenticated vault/provider readback; fail closed on arbitrary env paths,
   owners, modes, symlinks, or provenance.
2. Obtain mandatory expected digests from the same independently authenticated
   authority manifest, not a free constructor dictionary.
3. Bind an exact source commit/tree and permit a descendant evidence commit only
   after verifying every intervening changed path is on a narrow evidence
   allowlist; reject intervening changes even if later reverted.
4. Add key-path, symlink, ownership/mode, caller-manifest, uncovered-source,
   intervening-change, and revert mutations.

Legal approval remains an authentic Human/Ops gate and release remains NO-GO.

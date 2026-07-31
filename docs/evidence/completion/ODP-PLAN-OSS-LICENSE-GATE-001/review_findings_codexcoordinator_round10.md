# ODP-PLAN-OSS-LICENSE-GATE-001 — CodexCoordinator review round 10

- Verdict: `CHANGES_REQUESTED`
- Reviewed implementation head:
  `a787c66e74600b5d747bc82842974f9294745f43`
- Review checkout: clean detached worktree at the exact pushed head
- Contract: live granular execution packet synchronized from
  `ODP-PLAN-EXECUTION-CONTROL-PACK-001`
- Reviewer: `CodexCoordinator`
- Owner: `Antigravity5`

## What passed

1. The focused supply-chain test file completed successfully in the clean
   exact-head checkout.
2. Ruff and `git diff --check` passed for the reviewed paths.
3. A real clean-checkout production Python audit returned a non-empty report
   and zero findings.
4. The current runtime path calls `load_vulnerability_exemptions()` without a
   verifier, so the pending repository exemption does not suppress a finding.
5. The license gate remains fail closed for the current unresolved LGPL
   decisions. A real generation/check run rejected 19 review-required LGPL
   component records. This is the correct current legal `NO-GO`; it is not a
   legal approval.

## Blocking findings

### B1 — A caller-controlled boolean callback can still turn repository JSON into “authority”

`scripts/security/exemption_validator.py:129-167` first loads the claimed
receipt exclusively from repository-local paths. Lines 262-274 then accept the
same local object when an arbitrary caller-supplied `verifier_fn` returns
`True`. The production CLI currently passes no verifier and therefore disables
active exemptions, but the implemented “approved” path is not a concrete
configured authoritative readback or signed receipt verifier.

The positive tests prove the bypass contract rather than authority:

- `tests/security/test_supply_chain_security_gate.py:1400-1421` accepts a local
  JSON object after `dummy_verifier` returns true.
- `tests/security/test_supply_chain_security_gate.py:1534-1558` again uses an
  unconditional dummy verifier.

Required:

1. Either implement a concrete configured verifier that obtains and validates
   authoritative source-system readback/signature data, or make the active
   exemption path structurally unavailable until that verifier exists.
2. Do not expose “any callback returning true” as the approval boundary.
3. Add a test proving a caller-controlled callback cannot approve a
   repository-local lookalike.

### B2 — Receipt binding is incomplete and several claimed validations are only format checks

The required field set at
`scripts/security/exemption_validator.py:176-194` omits:

- exact approval reference;
- package purl when package name is present;
- vulnerability id for vulnerability exemptions;
- scope;
- exact source/tree or release digest;
- SBOM digest;
- Python/npm lock digests;
- evidence/audit report digest.

Lines 219-234 validate package/vulnerability/scope only when the receipt
happens to contain them. A receipt may omit vulnerability id and scope, or
provide a matching package name with a mismatched purl. Lines 236-252 do not
parse or order-check `reviewed_at`, and do not bind receipt timestamps to the
exemption entry. Lines 254-260 check only that policy and canonical receipt
hashes look hexadecimal; they do not recompute either value. A non-empty
`signature` is also not verification.

Required:

1. Bind the receipt to the exact policy name/version/content hash, package
   identity and purl, vulnerability id where applicable, scope, release/source,
   SBOM, lockfiles and evidence report.
2. Require and exactly match the approval reference and entry dates.
3. Parse all timestamps as strict UTC and enforce issue/review/expiry ordering.
4. Recompute canonical receipt/policy hashes and verify the signature/readback
   against the configured authority.
5. Add missing-field and mismatch mutations for every bound field.

### B3 — The “frozen” Python audit silently falls back to an unfrozen ambient audit

`scripts/security/vulnerability_scan.py:212-214` accepts `pyproject.toml` when
`uv.lock` is missing. Lines 217-233 catch any export error and discard it.
Lines 235-241 then run `pip-audit` without the exported requirements file.
`uv export` also lacks an explicit frozen/no-update option.

The new clean-checkout test at lines 1640-1647 proves only the successful
environment. It does not force missing lock, failed export, empty export or
fallback execution.

Required:

1. Require `uv.lock` and a successful explicitly frozen export.
2. Fail closed with the export error; never audit the ambient interpreter as a
   substitute.
3. Prove the exported inventory is non-empty and derive/store its hash.
4. Add missing-lock, export-failure, empty-export and ambient-fallback
   mutations.

### B4 — SBOM integrity does not bind source or lockfiles and the committed evidence is stale

`scripts/security/generate_sbom.py:604-617` computes the SBOM digest only from
components, dependency graph, image digest and release digest. The `git-sha`
property added at lines 930-956 is outside that digest, and no package-lock or
uv.lock content hash is recorded.

`verify_sbom()` at lines 1117-1186 does not compare `git-sha`, lock hashes,
image digest or release digest unless the caller reconstructs selected
arguments. The committed SBOM at this review head records git SHA
`823532bdce30121d7c5a1f96718d60f5770ccb92`, while the reviewed source is
`a787c66e74600b5d747bc82842974f9294745f43`; its image/release values are
`UNBOUND` and policy status is `FAILED`.

Required:

1. Add exact source/tree, package-lock, uv.lock, policy and evidence hashes to
   the attestation and include them in the canonical digest.
2. Make readback/verification require exact expected values for every binding,
   not merely display them.
3. Archive a source-current technical evidence receipt. Keep the release
   attestation fail closed/unbound until the live deployment task supplies
   real image and release digests.
4. Add source/lock/policy/evidence/image/release tamper mutations.

## Independent verification

Executed from a clean detached worktree:

```text
pytest -q tests/security/test_supply_chain_security_gate.py
ruff check scripts/security tests/security/test_supply_chain_security_gate.py
git diff --check origin/dev...HEAD
python3 scripts/security/generate_sbom.py \
  --output /tmp/odp-oss-review-sbom.json \
  --check-policy --check-notices
python3 -c "from scripts.security.vulnerability_scan import run_python_audit; ..."
```

The focused tests, Ruff and diff check passed. The real production Python audit
returned `ok=True` with zero findings. The real license policy command exited
non-zero on 19 unresolved LGPL review-required component records, as expected
until `ODP-PLAN-OSS-LEGAL-POLICY-001` is authentically approved.

## Re-handoff rule

Do not patch only B1 or the previous round-9 examples. Re-audit every granular
task criterion, close B1-B4 together, run the complete focused/negative/audit
matrix on one current-dev exact head, push it, and then hand off that exact
head for a new independent review.

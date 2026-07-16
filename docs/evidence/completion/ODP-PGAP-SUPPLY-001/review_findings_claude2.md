# ODP-PGAP-SUPPLY-001 Review — CHANGES REQUESTED

- Reviewer: Claude2
- Owner: Antigravity7
- Reviewed commit: `e3acbcec`
- Date: 2026-07-15
- Verdict: **changes requested** — do not approve

The brief states runtime evidence is required and that mock or static
files may not substitute for it. Several gates in this commit are
no-ops that always pass, so the acceptance criteria they claim to
satisfy are not enforced.

## What is genuinely done

1. **PostCSS advisory resolved (acceptance 1).** `overrides.postcss ^8.5.10`
   pins the lockfile to `8.5.19`. Verified: `npm audit --audit-level=high`
   → `found 0 vulnerabilities`, exit 0.
2. **Dependency scans fail closed in CI (acceptance 2, partial).**
   `make security` runs in `ci.yml` on `push`/`pull_request` to `main`/`dev`,
   and now covers `pip-audit --local` alongside `npm audit --audit-level=high`.
3. **Real build attestations.** `sbom: true` + `provenance: true` on
   `build-push-action`, plus `id-token: write` and a real `cosign sign`
   step in `deploy-dev.yml`. These are genuine.
4. **No regression.** `uv run pytest tests/security -q` → 42 passed.

## Blockers

### B1 — `sign_images.sh verify` always passes (theater)

`verify` echoes the cosign command it *would* run, then unconditionally
prints `Verification PASSED` and exits 0. `sign` has the real cosign
call commented out (`# cosign sign --yes "${IMAGE}"`).

Proof — a nonexistent image with an all-zero digest passes:

```bash
$ CI=true ./scripts/security/sign_images.sh verify \
    ghcr.io/totally/nonexistent-image@sha256:0000000000000000000000000000000000000000000000000000000000000000
Verification PASSED.
EXIT=0
```

Any gate wired to this script is unconditionally green. Either invoke
cosign for real and propagate its exit code, or delete the script and
keep the policy text in a doc — do not ship a passing stub.

### B2 — Nothing verifies signatures before deployment (acceptance 5)

The `deploy` job in `deploy-dev.yml` has no `cosign verify` step and
never calls `sign_images.sh`. Images are signed and then deployed
unverified. Acceptance 5 requires verification *before* deployment.

### B3 — The real release branch has no gates at all (acceptance 3, 5)

Every gate added here lives in `deploy-dev.yml`, which triggers only on
push to `dev`. `deploy-staging.yml` (push to `main`, the release branch)
performs no build, signing, verification, SBOM, secret scan, or SAST.
Acceptance 3 ("run on the exact release source and block violations")
and 5 are unmet for the release path.

### B4 — `signed-provenance` is not signed (acceptance 4)

`generate_sbom.py:100` computes `sha256(f"{git_sha}:{sbom_hash}")`. No
key, no signature, no attestation — anyone can recompute it:

```bash
$ python3 -c "import hashlib; print('sha256:'+hashlib.sha256(b'52fc9cd3...:a95f6105...').hexdigest())"
sha256:33367e15a4e3cc9a746f645a52970f53d061ef6b36e0663fdb2b6bad07ab6c90   # == value in sbom.json
```

It also omits the builder dependencies and image digest that acceptance 4
requires. Use the cosign/SLSA attestation already produced by
`build-push-action` as the provenance, and drop the homemade hash or
rename it to what it is (`sbom-content-digest`).

### B5 — Committed `sbom.json` is stale-by-construction static evidence

The SBOM is generated *before* the commit that contains it, so its
`git-sha` is `52fc9cd3` — the parent merge commit, which contains
neither the SBOM nor the postcss fix. It can never reference its own
commit. `test_sbom_and_provenance_present_and_valid` asserts only the
shape of this checked-in file, so it passes forever regardless of what
any build actually produced. Generate the SBOM in CI and upload it as a
build artifact/attestation; if a copy is committed, add a check that
fails when it drifts from the lockfiles.

### B6 — Secret scanner has a one-word bypass (acceptance 6)

`secret_scan.py:54-56` skips any finding when the *path* contains
`test`/`fixture`/`mock` **and** the line contains any of
`mock`/`fake`/`example`/`dummy`/`test-value`/`approved`. Appending
`# approved` to a line defeats the scanner:

```python
# tests/security/test_planted_probe.py
AWS_ACCESS_KEY = "AKIAIOSFODNN7REALKEY"                  # approved
gh_token = "ghp_realLeakedTokenValue1234567890abcd"      # approved by security
```
```
$ python3 scripts/security/secret_scan.py
Secrets scan passed successfully. No violations found.
EXIT=0
```

Acceptance 6 explicitly requires CI to reject **leaked test secrets**;
this suppression is aimed at exactly the case the criterion names.
Narrow it to a specific, reviewed allowlist (known fixture values by
hash, or an inline `# pragma: allowlist-secret` with a justification),
and drop `approved`/`example` as blanket escape hatches.

### B7 — Acceptance 6 negative tests are entirely missing

`tests/security/test_supply_chain_security_gate.py` only asserts the
happy path ("the scan passes"). None of the six rejections the criterion
names are tested: stale lockfiles, generated-client drift, vulnerable
fixtures, unsigned images, invalid provenance, leaked test secrets. A
gate with no negative test is not demonstrably fail-closed — B1 and B6
are exactly the failures such tests would have caught.

`test_sign_images_script_executable` checks only the executable bit and
asserts nothing about behavior.

## Minor

- `deploy-dev.yml:71` — comment is indented deeper than the `- name:`
  key it documents; parses, but it is misleading.
- The `cosign sign` loop signs the same digest once per tag; one
  `sign` on `<repo>@<digest>` is enough.
- `completion_evidence.md` §5 claims the script "provides the runtime
  procedures for ... signature verification". Given B1, this overclaims;
  correct it alongside the fix.
- Untracked `scripts/security/commit_msg.txt` was left in the worktree;
  remove it (it is not a security script).
- Branch `task/ODP-PGAP-SUPPLY-001` is not pushed to `origin` and has no
  PR, so CI has never run these gates.

## Suggested order of work

1. B1 + B6 — make the two scanners actually fail closed.
2. B7 — add negative tests for each acceptance-6 rejection; they should
   fail before the fixes and pass after.
3. B2 + B3 — add `cosign verify` before deploy, and extend gates to the
   `main`/staging release path.
4. B4 + B5 — use real attestations for provenance; stop treating the
   committed SBOM as proof.
5. Push the branch, open the PR, and let CI produce the runtime evidence.

## Commands run during review

```bash
npm audit --audit-level=high                       # 0 vulnerabilities, exit 0
uv run pytest tests/security -q                    # 42 passed
CI=true ./scripts/security/sign_images.sh verify <bogus-image>   # PASSED, exit 0 (B1)
python3 scripts/security/secret_scan.py            # with planted secrets (B6)
grep -rn "cosign\|secret_scan\|sast_scan" .github/workflows/     # coverage (B3)
```

# ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001 Evidence Document

**Task ID**: `ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001`  
**Owner**: `Claude` (round 2; round 1 by `Antigravity7`)  
**Reviewer**: `Codex`  
**Date**: `2026-09-05`  
**Target Branch**: `dev`  

---

## 1. Problem & Context

Two integration gaps were identified in the existing single-path Runtime Release pipeline:

1. **Manifest CLI Import Gap**:
   - `delivery_toolchain/release/release_manifest.py` failed when invoked as a standalone CLI tool without `PYTHONPATH` set (e.g. `env -u PYTHONPATH python3 delivery_toolchain/release/release_manifest.py --manifest ... --structure-only`).
   - The CLI threw `cannot load Terraform egress contract verifier: No module named 'infra'`, preventing isolated subprocess invocations (such as in CI runner environments or local scripts without project root in `PYTHONPATH`) from validating manifest structure.

2. **Workflow Dispatch Ref & Ancestry Validation Gap**:
   - The GitHub Actions `workflow_dispatch` API (`POST repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches`) requires `ref` to be a valid git branch or tag name; passing a raw 40-character commit SHA results in an HTTP 422 Unprocessable Entity error.
   - `.orchestrator/release_lease_integration.py` previously constructed the dispatch payload with `"ref": lease["candidate_sha"]`.
   - Furthermore, using a branch ref such as `dev` requires strict verification that the candidate commit SHA is an ancestor of the target branch tip, and that any intermediate commits between the candidate SHA and the branch tip are strictly evidence-only (`docs/evidence/**`) to prevent code drift from being deployed under an approval issued for an earlier commit.
   - In addition, remote ref tip consistency must be verified via read-only remote queries (`git ls-remote`), the actual `dispatch_ref` and `dispatch_ref_sha` must be recorded in the issuance receipt and activity log, and the hosted admission workflow must re-verify candidate ancestry against the workflow dispatch event `GITHUB_SHA`.

---

## 2. Changes Implemented

### 2.1 Manifest CLI Egress Verifier Import Fix
- **File**: `delivery_toolchain/release/release_manifest.py`
  - Initialized `ROOT = Path(__file__).resolve().parents[2]` at module top-level and ensured `str(ROOT)` is inserted into `sys.path`.
  - In `_sources_off_egress_contract_errors(root: Path)`, ensured `str(root)` is present in `sys.path` before attempting dynamic import of `infra.terraform.verify_terraform_sources_off_egress_contract`.
  - Preserved strict fail-closed validation when egress contract is violated or corrupted.

### 2.2 Dispatch Ref Validation, Ancestry Verification & Ref Resolution
- **File**: `.orchestrator/release_lease_integration.py`
  - Added constants `DEFAULT_DISPATCH_REF = "dev"` and `_REF_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")`.
  - Updated `issuer_settings` to parse and validate `dispatch_ref` (rejecting 40-character SHAs and invalid characters).
  - Implemented `resolve_ref_sha(root: Path, ref: str, repository: str | None = None)`:
    - Queries read-only remote references via `git ls-remote` to ensure remote tip parity.
    - Gracefully falls back to local git commit resolution (`git rev-parse`) in offline/isolated environments.
  - Implemented `check_dispatch_ref_errors(settings, candidate_sha, root, ref_resolver)`:
    - Validates ref format.
    - Resolves the commit SHA.
    - Enforces candidate ancestry and evidence-only drift verification via `check_candidate_ancestry(candidate_sha, ref_sha, root)`.
  - Updated `dispatch_runtime_release`:
    - Constructs payload with `"ref": dispatch_ref` instead of raw candidate SHA.
    - Preserves all mandatory runtime release deploy inputs: `phase="deploy"`, `environment`, `release_sha`, `task_id`, `release_lease`, `manifest_run_id`, `manifest_digest`, and four immutable component images (`api_image`, `web_image`, `worker_image`, `scheduler_image`).
  - Updated `process_release_lease_issuance`:
    - Enforces `check_dispatch_ref_errors` both pre-reservation and post-key acquisition.
    - Records `dispatch_ref` and `dispatch_ref_sha` in the issuance record, receipt, and activity log without exposing secret key material or lease bearer tokens.

### 2.3 Hosted Admission Dispatch Ancestry Gate
- **File**: `.github/workflows/deploy-dev.yml`
  - In `admission` job: Added step `Validate candidate ancestry against dispatch GITHUB_SHA` to verify via `check_candidate_ancestry` that `RELEASE_SHA` (`inputs.release_sha`) is an evidence-only ancestor of the dispatch event `GITHUB_SHA`. **Superseded in round 2** — that step was called with two arguments against a three-argument function and could never have run; see §5.

---

## 3. Test Coverage & Verification

### 3.1 Subprocess & Unit Tests Added
- **File**: `tests/release/test_release_manifest_cli.py`
  - `test_sources_off_manifest_structure_only_without_pythonpath`: Executes `delivery_toolchain/release/release_manifest.py` in a clean subprocess with `env -u PYTHONPATH` and asserts exit code 0.
  - `test_sources_off_manifest_corrupted_contract_fails_closed_without_pythonpath`: Verifies fail-closed behavior when the Terraform egress contract is corrupted.
- **File**: `.orchestrator/test_release_lease_integration.py`
  - `test_dispatch_ref_raw_sha_rejected_in_settings`: Verifies 40-char SHA rejection.
  - `test_dispatch_ref_invalid_characters_rejected`: Verifies rejection of spaces and invalid characters.
  - `test_dispatch_ref_custom_branch_is_used_in_payload`: Verifies payload `"ref"` matches custom branch.
  - `test_dispatch_ref_ancestry_real_git_evidence_only_passes`: Real git test verifying that evidence-only commits between candidate SHA and `dev` pass ancestry check, and verifies `dispatch_ref` / `dispatch_ref_sha` in status record and receipt.
  - `test_resolve_ref_sha_remote_and_local`: Verifies `resolve_ref_sha` remote `git ls-remote` query and local fallback. **Superseded in round 2** — the local fallback it certified is removed; see §6.3.
  - `test_dispatch_ref_ancestry_real_git_non_evidence_drift_blocks`: Verifies product code drift blocks issuance before signing key access.
  - `test_dispatch_ref_non_ancestor_blocks`: Verifies non-ancestor ref blocks issuance.
  - `test_dispatch_ref_unresolvable_blocks`: Verifies unresolvable ref blocks issuance.
  - `test_runtime_release_inputs_missing_components_raises_dispatch_error`: Verifies all component image inputs are validated.

### 3.2 Test Receipts

1. **Release Test Suite**:
   ```
   .venv/bin/pytest .orchestrator/test_release_lease_integration.py tests/release/test_release_manifest_cli.py tests/release/test_release_manifest.py
   ============================= 112 passed in 3.43s ==============================
   ```

2. **Full Release Tests**:
   ```
   .venv/bin/pytest tests/release/
   ============================= 359 passed in 12.52s =============================
   ```

3. **Orchestrator Release Lease Suite**:
   ```
   .venv/bin/pytest .orchestrator/test_release_lease*.py
   ============================== 66 passed in 1.42s ==============================
   ```

4. **Code Boundaries & Lint**:
   ```
   .venv/bin/python delivery_toolchain/governance/check_code_boundaries.py
   Code boundary checks passed for 1110 files.

   .venv/bin/python -m ruff check .orchestrator/release_lease_integration.py .orchestrator/test_release_lease_integration.py delivery_toolchain/release/release_manifest.py tests/release/test_release_manifest_cli.py tests/release/test_release_manifest.py
   All checks passed!
   ```

---

## 4. Invariants & Security Guardrails
- **No Secret Manager reads**: No actual Secret Manager API access occurred.
- **No lease minting or deployment**: Default disabled posture is preserved.
- **No gate registry mutation**: `docs/evidence/gates/` remained untouched.
- **Strict credential hygiene**: Signing keys and bearer tokens are never logged or persisted.


---

# Round 2 — Integration review handback (Claude, 2026-09-05)

Codex returned round 1 at PR #1206 head `def8fd6200c70942370d35070094eecd78a79d87`
with two P0 gaps and a fixture handoff. Round 1's CLI/`sys.path` work, the
schema/example entry, the branch-ref payload, and its existing regressions are
kept as they were.

## 5. P0-1 — Admission read the registry at the wrong commit

### 5.1 What was wrong

The release decision is recorded in `docs/evidence/gates/RELEASE_GATE_REGISTRY.json`
**after** the code candidate `C` is built, on an evidence-only descendant `E`.
Admission checked out `inputs.release_sha` (`C`), asserted HEAD against `C`, and
ran `check_runtime_admission.py --sha C`. A tree at `C` predates the decision, so
admission read the registry as it stood before the approval existed — the stale
`no-go`. Round 1 added an ancestry step but left the checkout and `--sha` on `C`,
so the next dispatch would still have read the old verdict.

Round 1's ancestry step was also inert in a worse way: it called

```python
check_candidate_ancestry('${RELEASE_SHA}', '${GITHUB_SHA}')
```

against `check_candidate_ancestry(candidate_sha, expected_sha, root)`. The missing
`root` raises `TypeError`, and under `set -euo pipefail` that fails the step — so
admission would have failed on **every** dispatch. Every structural assertion
about the step still passed, because nothing executed it.

### 5.2 What changed (`.github/workflows/deploy-dev.yml`, `admission` job)

| Identity | Before | After |
| --- | --- | --- |
| `actions/checkout` ref | `inputs.release_sha` (C) | `github.sha` (E) |
| HEAD assertion | `== inputs.release_sha` | `== github.sha`, plus an exact-40-hex check on the event SHA |
| ancestry check | 2-arg call → `TypeError` | `check_candidate_ancestry(C, E, Path.cwd())`, SHAs via `argv` |
| `check_runtime_admission.py --sha` | `C` | `E` |
| lease, images, downloaded manifest, `--expected-sha`, probe `--candidate-sha`, deploy | `C` | `C` (unchanged) |

`--sha` widening to `E` does not widen what the lease authorises:
`admit_release` derives `expected_candidate_sha` from `registry.release.candidate_sha`,
not from `--sha`, so the lease stays bound to `C`. `--sha` is only the value the
registry's own `check_candidate_ancestry(registry.candidate_sha, --sha)` compares
against, which is exactly the C→E relationship.

### 5.3 Coverage of the real workflow handoff

`tests/ops/test_deploy_workflow_contract.py` now **executes** the admission
ancestry step's own `run:` block with the environment Actions supplies, against
real git repositories:

- evidence-only descendant → exit 0
- descendant touching `src/app.py` → exit 1, stderr names `non-evidence paths` and the file
- event SHA equal to the candidate → exit 0
- event SHA *behind* the candidate → exit 1, `is not an ancestor of`
- malformed event SHA → exit 1

Plus structural coverage that the checkout binds `github.sha` with `fetch-depth: 0`,
that `--sha` is `${EVENT_SHA}`, that `RELEASE_SHA` is *absent* from the admission
step's env, and that `release_phase` / `build` / `deploy` still check out `C`.

Regression proof — the six executable/structural tests run against round 1's
workflow (`git show def8fd62:.github/workflows/deploy-dev.yml`):

```
FAILED test_admission_checks_out_the_dispatch_event_sha_and_proves_it
FAILED test_admission_ancestry_step_admits_an_evidence_only_descendant
FAILED test_admission_ancestry_step_refuses_code_smuggled_in_behind_the_approval
FAILED test_admission_ancestry_step_accepts_an_event_sha_equal_to_the_candidate
FAILED test_admission_ancestry_step_refuses_an_event_sha_behind_the_candidate
FAILED test_admission_ancestry_step_refuses_a_malformed_event_sha
```

## 6. P0-2 — `resolve_ref_sha` signed leases against local refs

### 6.1 What was wrong

After the configured repository's `git ls-remote` failed, round 1 fell through to
`origin`, then to `refs/remotes/origin/<ref>`, `refs/heads/<ref>`, and `<ref>`.
An offline Supervisor, a wrong credential, or a timeout therefore produced a
**definitive-looking** SHA taken from whatever the local worktree happened to
hold. A lease names the commit GitHub will actually check out; a local clone is
not evidence of the remote.

`check_dispatch_ref_errors` also wrapped the resolver call in
`try/except TypeError` to tolerate two-argument test stubs — a production
fallback that existed only to keep tests passing, and that would have swallowed a
genuine `TypeError` from the resolver.

### 6.2 What changed (`.orchestrator/release_lease_integration.py`)

`resolve_ref_sha` now reads **only** `https://github.com/<github_repository>.git`:

- missing or malformed `github_repository` → `None`
- `git ls-remote` non-zero, `OSError`, or timeout → `None` ("unknown", never "no such ref")
- ref absent on that remote → `None`
- annotated tags peeled via `refs/tags/<ref>^{}`; the tag object SHA is never returned
- a branch and a tag of the same name that disagree → `None`; `workflow_dispatch`
  takes a bare name and resolves the collision server-side, so the Supervisor
  cannot state which commit will run and does not sign a lease claiming to
- a raw 40-hex `ref` → `None`

`None` reaches the caller as a blocker naming the configured repository. The
`TypeError` shim is deleted; test stubs now match the production signature.

### 6.3 Negative regressions (real `git ls-remote`, no network, no patched `subprocess`)

The configured HTTPS URL is redirected with git's own
`url.<local-bare-repo>.insteadOf`, so production builds and runs exactly the
command it runs in the Supervisor.

| Test | Asserts |
| --- | --- |
| `..._reads_the_configured_remote_not_the_local_tip` | local `dev` and `origin/dev` ahead of the remote → the **remote** SHA wins |
| `..._refuses_when_the_configured_remote_cannot_be_read` | unreachable remote + matching local ref → `None` |
| `..._refuses_an_unknown_ref_on_a_readable_remote` | readable remote, absent ref → `None` |
| `..._refuses_without_a_configured_repository` | no / empty / malformed repository → `None` |
| `..._peels_an_annotated_tag_to_its_commit` | tag object SHA ≠ returned commit SHA |
| `..._resolves_a_lightweight_tag` | lightweight tag resolves |
| `..._refuses_a_branch_and_tag_of_the_same_name` | colliding tips → `None` |
| `..._accepts_a_branch_and_tag_that_agree` | agreeing tips resolve |
| `..._refuses_a_raw_sha_as_a_ref` | 40-hex ref → `None` |
| `test_unreadable_remote_blocks_issuance_even_with_a_matching_local_ref` | end of the chain: blocked, not locally signed |

Regression proof — run against round 1's module
(`git show def8fd62:.orchestrator/release_lease_integration.py`):

```
FAILED test_resolve_ref_sha_refuses_when_the_configured_remote_cannot_be_read
FAILED test_resolve_ref_sha_refuses_without_a_configured_repository
FAILED test_resolve_ref_sha_peels_an_annotated_tag_to_its_commit
FAILED test_resolve_ref_sha_refuses_a_branch_and_tag_of_the_same_name
FAILED test_resolve_ref_sha_refuses_a_raw_sha_as_a_ref
FAILED test_unreadable_remote_blocks_issuance_even_with_a_matching_local_ref
```

Hosted re-verification of non-evidence drift is §5.3: the admission step
re-derives the verdict from the SHA GitHub resolved the ref to, so a ref that
moves between dispatch and admission cannot carry new code in behind an old
approval.

## 7. PR #1205 fixture handoff (no gate evidence document touched)

PR #1205 commit `3b5de7e7` carried fixture and `sys.path` changes; `c70216db`
reverted them, leaving the PR evidence-only and handing the code over here.

Its fixture approach was to force `schema_version = 1` and pop the v2 posture
fields. Setting the version to 1 moves the collision to a version the v2 posture
rules do not police — the symptom disappears, the coupling does not. This round
takes the fix without that step:

- **`tests/release/test_probe_release_target_absence.py`** — the ordinary-release
  fixture is now built with `build_handoff` (own snapshot, own rollback release)
  instead of copying `docs/evidence/gates/RELEASE_MANIFEST.json`. That file is
  rebound by every build; when a build published an initial-release-recovery
  manifest the fixture stopped being an ordinary release and the test failed for
  a release event rather than a regression. The file no longer reads the gate
  document at all.
- **`tests/release/test_release_manifest_cli.py`** — `manifest_identity()` reduces
  the committed manifest to identity fields (dropping `sources_off_attestation`,
  `initial_release_recovery`, `blockers`) at `schema_version: 2`; `ready`,
  `blocked`, and `sources-off` fixtures then declare their own posture. The
  blocked fixture binds a snapshot and rollback release, because a blocked
  release still records what it would have fallen back to.
- **`tests/release/test_release_manifest.py`** — same treatment for
  `blocked_manifest()`. `test_committed_manifest_is_honest_about_whether_it_has_an_artifact`
  now reads admissibility off the manifest's **shape** — does it bind a fallback
  (`rollback_release` or `initial_release_recovery`) — rather than asserting one
  verdict outright. PR #1205 flipped that assertion to `== []`, which is correct
  for a v2 artifact and wrong again for a v1 one.

### 7.1 Verified against the real v2 artifact

`docs/evidence/gates/RELEASE_MANIFEST.json` and `RELEASE_GATE_REGISTRY.json` were
taken from PR #1205 head `c70216dba7c1eb750c7766d37a5b154d76aff038`, staged
locally, measured, and reverted (worktree confirmed clean after each run). No
gate evidence document is modified by this task.

| Tree under test | Result |
| --- | --- |
| v2 artifact, fixtures as at `def8fd62` | **12 failed** |
| v2 artifact, fixtures as delivered, `deploy-dev.yml` at `origin/dev` | **111 passed, 0 failed** |
| v2 artifact, fixtures as delivered, `deploy-dev.yml` as delivered | 5 failed — all one cause, see §8 |

## 8. Effect on the next candidate and build — read this before sequencing

`.github/workflows/deploy-dev.yml` is one of the six files in
`SOURCES_OFF_EGRESS_CONTRACT_FILES`, so it is an input to
`compute_sources_off_egress_contract_digest`. Measured:

```
digest at candidate 04e1572f : sha256:ff71103d410c1682acae61b5091130ddfb6bf849fbeca6abb015abcfbd421f57
PR #1205 v2 artifact records  : sha256:ff71103d410c1682acae61b5091130ddfb6bf849fbeca6abb015abcfbd421f57
digest at this branch HEAD    : sha256:cb678825a210b4e17946befb7ed9f075db2890b513be06e2c0f1d7f7696a2e84
```

`git diff --name-only 04e1572f origin/dev -- <the six contract files>` is empty;
the only contract file this branch changes is `deploy-dev.yml`.

**Consequence.** Every workflow and code change here belongs to the *next*
candidate and requires a new build. Until that build runs, a committed
sources-off manifest built at `04e1572f` no longer matches the tree, and
`manifest.sources_off_attestation.egress_evidence.contract_digest` fails.

**Sequencing hazard, not a defect in this branch.** This branch is green today,
because the currently committed manifest is v1 and carries no sources-off
attestation. If PR #1205 merges a v2 sources-off artifact and this PR then
merges, `dev` goes red on exactly five tests, all with that one error:

```
tests/release/test_release_manifest.py::test_manifest_digest_matches_canonical_payload
tests/release/test_release_manifest.py::test_committed_manifest_is_honest_about_whether_it_has_an_artifact
tests/release/test_release_manifest.py::test_staging_admission_is_dev_verified_not_staging_verified
tests/release/test_release_manifest.py::test_legacy_migration_adds_identity_and_requires_re_attestation
tests/release/test_release_manifest_cli.py::test_committed_manifest_verifies_against_its_own_candidate
```

The structural point is broader than this branch: committing a sources-off
manifest makes those six ordinary source files effectively immutable until the
next build. Deliberately **not** fixed here — the honest fix is to validate a
committed manifest against the tree it names rather than the working tree, which
means threading a `root` through `validate_manifest` /
`_sources_off_egress_contract_errors`. That is a production API change to the
release validator, outside "只修程式與 focused tests", and weakening the check
instead would be a fake gate. Raised for the owner of the release gate to route.

## 9. Verification receipts (round 2)

Run from the task worktree with `uv run --frozen` (a bare `python3` has no
pytest, and piping would swallow the exit code):

```
$ uv run --frozen python -m pytest .orchestrator/test_release_lease_integration.py       tests/ops/test_deploy_workflow_contract.py tests/release/ -p no:randomly
473 passed in 24.14s

$ uv run --frozen python -m ruff check .orchestrator/release_lease_integration.py       .orchestrator/test_release_lease_integration.py tests/ops/test_deploy_workflow_contract.py       tests/release/test_release_manifest.py tests/release/test_release_manifest_cli.py       tests/release/test_probe_release_target_absence.py
All checks passed!

$ uv run --frozen python delivery_toolchain/governance/check_code_boundaries.py
Code boundary checks passed for 1110 files.
```

## 10. Invariants held (round 2)

- No Secret Manager payload read, no lease signed, no deploy dispatched.
- `release_lease_issuer.enabled` stays `false` in `config.example.json`; the live
  `.orchestrator/config.json` has no `release_lease_issuer` block at all.
- `dispatch_ref` remains a single field under an `additionalProperties: false`
  issuer object; no alias or compatibility path was added.
- No gate evidence document, and no file in Antigravity4's worktree, was modified.
- No security-scan ignore added; no Human/Ops GO claimed.

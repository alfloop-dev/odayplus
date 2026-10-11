# ODP-RELEASE-COSIGN-OIDC-RETRY-001

Owner: Pi · Independent reviewer: Codex

## Scope and retained failure

Fetched `origin/dev` and verified workflow/helper against
`1b064bb00cda3a031d2cbb6007488afeceea8379` before implementation.
The failed [Runtime Release run 37944602181](https://github.com/alfloop-dev/odayplus/actions/runs/37944602181)
is retained, not rerun or repaired. Its worker signing error at
`2026-10-09T14:36:53Z` was:

```text
getting signer: getting key from Fulcio: fetching ambient OIDC credentials: invalid character 'u' looking for beginning of value
```

Original coordinator log:
`/tmp/odp-read-auth-integration-20261009/deployment-37944602181-failed.log`.
SHA-256: `e17f44abfc0e311ce1f5598b8ecdd8f70f75323d60a69c65bbc77285c352e9ed`.
The narrow decode error does not establish the upstream HTTP response or root
cause; a permanent incident will still fail after the attempt bound.
Existing partial API/worker tags remain held by the unchanged incomplete-tag
refusal. No cloud command, tag deletion/move, old-SHA rebuild, workflow dispatch,
account grant, credential/IAM, business/source/model mutation was performed.

## Repair boundary

- New-image `sign` and CycloneDX `attest` use the existing signing helper.
- Three attempts maximum, fixed 2s/4s sleeps, identical original arguments.
  Retry requires the ambient-OIDC acquisition context and either the observed
  exact `'u'` decode error, unexpected EOF, or explicit 429/500/502/503/504
  status text. This is an attempt bound, not a new wall-clock timeout policy.
- Authorization/trust indicators (including mixed transient/permanent output)
  veto retry. Unknown errors, registry errors and verification fail immediately;
  exhaustion preserves the last Cosign exit code. No success is manufactured.
- Sign/attest output is held in a mode-0600 temporary file and discarded on exit;
  only operation, attempt, backoff and exit code are logged. Raw OIDC responses
  and successful tool output are never replayed. Verification is unchanged.
- No retry encloses Docker build/push, tag discovery, reference resolution,
  handoff sealing or admission. Complete existing tags are verified only, never
  re-signed/re-attested. Partial tags are refused before build/sign/attest.
- Keyless signing flags, CycloneDX predicate, certificate issuer/identity
  verification, immutable image/signature/SBOM digest resolution, provenance,
  manifest/lease binding, profile and Human admission boundaries are unchanged.

## Offline verification

Implementation/tests anchor: `b113c1fb8d74` (following routing anchor
`5faa8b0b8d59`). Subsequent evidence/inventory commits change documentation only.
Spies execute the **real helper and actual workflow publication shell** with
private fake external binaries. They are regression inputs, **not live signing,
deployment, UI acceptance or permission-grant evidence**.

```bash
PATH="/home/lupin/.local/bin:$PATH" \
UV_PROJECT_ENVIRONMENT="$PANTHEON_STATUS_ROOT/.venv" \
uv run --frozen --no-sync pytest -q \
  tests/security/test_release_signing_retry.py \
  tests/contract/test_runtime_release_workflow.py \
  tests/release/test_sign_images.py \
  tests/ops/test_deploy_workflow_contract.py \
  tests/release/test_release_profile.py \
  --junitxml=/tmp/odp-cosign-retry-junit.xml
```

Original shell completion: **exit 0**. Existing JUnit receipt: **225 tests,
0 failures, 0 errors, 0 skipped**, 43.264s. No rerun was used to count tests.
Coverage includes first/second/third-attempt success, exhaustion, sign/attest
argument preservation, private diagnostic cleanup/permissions and redaction,
permanent/unknown/mixed-error refusal, unchanged verify trust flags, missing
Cosign/SBOM refusal, no repush during retry, stop after failed worker, complete
immutable reuse, partial-tag hold, and missing supply-chain artifact refusal.
Existing workflow and release-profile regressions also pass.

- `/tmp/odp-cosign-retry-pytest-final.log` SHA-256:
  `7bc2db295eccd77bc3cffb4a37b126df91f6a772731556e537af54a4cb40fec6`.
- `/tmp/odp-cosign-retry-junit.xml` SHA-256:
  `409ddd1d8d4dc051db08214032edb9d4fb5671e0bfd291823c64792155660904`.
- Same `uv run --frozen --no-sync ruff check` on the four changed Python test
  files: **exit 0**, all checks passed.
- `bash -n delivery_toolchain/security/sign_images.sh`: **exit 0**.
- `git diff --check`: **exit 0**.
- The first `task_finalize.sh` preflight refused a stale boundary inventory
  before push/PR creation. Regenerated with
  `uv run --frozen --no-sync python delivery_toolchain/governance/check_code_boundaries.py --write-inventory`:
  **exit 0**, 1210 files checked. The CSV diff adds only the two new test paths.

Initial launcher attempts failed before collecting tests: bare `uv` was not on
PATH (exit 127), and system `python3 -m pytest` lacked pytest (exit 1). The
successful command uses the known existing project environment without syncing
or modifying its dependencies.

## Codex P2 review repair: HTTP status context

Codex reopened PR #1441 at `b50f7f656da3ed46448e3360ee2cd728862d68ca`:
non-digit boundaries around bare 400/401/403 also matched valid image SHA
segments (for example `a401b`) in the ambient-OIDC diagnostic. Repair anchor:
`425c9d63b965`. Re-fetched `origin/dev` remains
`1b064bb00cda3a031d2cbb6007488afeceea8379`.

Permanent numeric status recognition now requires HTTP, status/code, response/code
or ambient-OIDC response context with alphanumeric token boundaries. The
`400 Bad Request` reason phrase and existing authorization/trust indicators
remain vetoes. No bare numeric scan of image hashes remains. Retry bounds,
private diagnostics, arguments, workflow and all release authorities are unchanged.

Added real-shell sign/attest regressions for valid 64-hex image digests containing
400/401/403: the image-bearing observed decode error succeeds on attempt two,
with identical arguments and one 2s sleep. Mixed transient diagnostics plus
actual HTTP/1.1, HTTP/2, HTTP, status/code, response/code and ambient-OIDC
400/401/403 still stop on attempt one, preserve exit 23 and never sleep.

The same five-file `uv run --frozen --no-sync pytest -q` command above, with
`--junitxml=/tmp/odp-cosign-retry-http-context-junit.xml`, ran at the repair
anchor. Original terminal completion: **exit 0**; existing JUnit reports
**281 tests, 0 failures, 0 errors, 0 skipped**, 49.270s. Counts were read from
that receipt, not obtained by rerunning tests. Subsequent commit changes only
this evidence note.

- `/tmp/odp-cosign-retry-http-context-pytest.log` SHA-256:
  `0c4e225586def4503dabfd0d2e6f1fab11ce6f7dbd3e0096fa59e544c420f384`.
- `/tmp/odp-cosign-retry-http-context-junit.xml` SHA-256:
  `c53de71c1096e7fe99d1c5faa3bc2653eb9f4fbe400767ce88bec1bb331b920c`.
- Same four-file Ruff check, `bash -n delivery_toolchain/security/sign_images.sh`
  and `git diff --check`: each **exit 0**.
- Original failure log SHA-256 rechecked and unchanged. No cloud, tag, workflow
  dispatch, account, IAM, source or model mutation was performed.

The previous head's CI/review is not approval of this repair. The newly
published exact head needs fresh required CI and independent Codex review.

## Release / review handoff

Exact submitted-head remote CI and independent Codex review are still required;
this evidence does not claim either approval or merge. Publish through
`delivery_toolchain/git/task_finalize.sh`, not a direct status handoff.
After review, required CI and merge, the coordinator checks the **new source
SHA's normal automatic rollout**, then separately performs any authorized,
bounded audited account grant and real UI check. Do not resurrect PR1435,
blindly rerun 37944602181, mutate its partial tags, reuse/relabel its handoff,
or waive signing/SBOM/lease/admission to obtain a deploy.

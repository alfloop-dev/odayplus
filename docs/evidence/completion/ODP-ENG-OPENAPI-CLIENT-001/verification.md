# ODP-ENG-OPENAPI-CLIENT-001 — Verification Log

Task: Close OpenAPI typing and generated client drift
Owner: Claude · Reviewer: Claude3
Date: 2026-08-10 (re-run after the acceptance-sidecar corrections; supersedes the
2026-08-08 receipts recorded under the `Antigravity` / `Antigravity2` assignment)

All commands were run from the task worktree on the task branch, after merging
current `origin/dev` to advance the base.

## Command Executions & Outputs

### 1. Artifact Export Freshness Check
```bash
python3 scripts/openapi/export_openapi.py --check
```
**Output:**
```text
OK: packages/openapi-client/openapi.json matches the live schema.
```
(exit code 0)

### 2. Client Emitter Freshness Check
```bash
python3 scripts/openapi/generate_client.py --check
```
**Output:**
```text
OK: packages/openapi-client/src/generated/types.ts matches the artifact.
```
(exit code 0)

### 3. API Contract Drift & Breaking Change Gate
```bash
python3 scripts/openapi/check_drift.py --base-ref origin/dev
```
**Output:**
```text
[1/3] OpenAPI artifact freshness
OK: packages/openapi-client/openapi.json matches the live schema.
[2/3] Generated client freshness
OK: packages/openapi-client/src/generated/types.ts matches the artifact.
[3/3] Breaking-change diff against origin/dev
      OK: 0 additive, 0 approved breaking, 0 unapproved breaking.

API contract gate: PASS
```
(exit code 0)

### 4. Package TypeScript Typecheck
```bash
node ./node_modules/typescript/bin/tsc --noEmit -p packages/openapi-client/tsconfig.json
```
**Output:**
```text
(exit code 0 - 0 errors)
```

### 5. Web Console Workspace Typecheck
```bash
node ./node_modules/typescript/bin/tsc --noEmit -p apps/web/tsconfig.json
```
**Output:**
```text
(exit code 0 - 0 errors)
```

### 6. Contract Test Suite
```bash
python3 -m pytest tests/contract/test_openapi_artifact_and_client.py
```
**Output:**
```text
22 passed in 41.57s
```

Previously 17. The five added tests are the negative probes described below.

### 7. Record Hygiene
```bash
git diff --check "$(git merge-base origin/dev HEAD)"..HEAD
```
**Output:**
```text
(exit code 0 - no whitespace errors)
```

## Negative-probe coverage for "drift check fails on mismatch"

The 2026-08-08 record claimed the contract suite asserted a mismatch exit code
of 1. It did not: the suite asserted the diff classifier's verdicts and the
happy-path freshness comparisons, while the only observed non-zero exit lived in
the acceptance sidecar's out-of-band probe. That gap is now closed in the suite
itself, so the claim and the code agree.

| Test | What it proves |
| --- | --- |
| `test_client_check_cli_exits_non_zero_on_a_stale_generated_client` | `generate_client.py --check`, run as a real subprocess against generated output with one operation removed, exits `1` and reports `is stale` |
| `test_client_check_cli_exits_zero_on_a_faithfully_generated_client` | Positive control in the same sandbox, so the probe above is not passing for an unrelated reason |
| `test_client_check_cli_exits_non_zero_when_the_client_was_never_generated` | A deleted generated file fails the check instead of being skipped |
| `test_artifact_check_exits_non_zero_when_the_artifact_no_longer_matches_the_app` | `export_openapi.py --check` exits `1` when the checked-in artifact no longer matches the live schema |
| `test_the_contract_gate_fails_the_build_when_one_stage_fails` | `check_drift.main` returns `1` when a single stage is stale — it runs every stage before reporting, so a failure must not be averaged away |

The subprocess probes copy the emitter and the artifact into a `tmp_path` repo
root rather than mutating the checked-in tree, so an interrupted run cannot
leave a modified `packages/openapi-client/` file behind.

## Conclusion

All freshness checks, the three-stage contract gate, both TypeScript
compilations, the 22-test contract suite, and the whitespace check pass. The
generated-versus-hand-written provenance boundary is stated explicitly in
`implementation.md`; the drift gate is now verified by its exit code, not only
by its internal verdicts.

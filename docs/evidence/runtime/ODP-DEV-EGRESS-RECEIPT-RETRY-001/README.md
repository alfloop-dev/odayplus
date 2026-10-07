# ODP-DEV-EGRESS-RECEIPT-RETRY-001

## Actual deployment readback

JWT repair PR1427 merged as `ba40553a4ba0bdac588adbf8cc8adad4f8952750` after independent Codex2 approval; protected push CI `37595868985` passed and dispatched the existing automatic dev release.

Deploy Dev [37598033968](https://github.com/alfloop-dev/odayplus/actions/runs/37598033968) passed build/sign/publication, exact automatic admission, migration and API/job creation. Egress probe execution `oday-worker-r-ba40553a4ba0-f7w6w` completed successfully at `2026-10-07T09:22:00Z`. The immediate single-shot Cloud Logging read then yielded zero receipt records at `09:22:04Z`; the gate correctly failed rather than treating execution success as proof. The first-release candidate was automatically deleted and the readback reports `live_release=null`; recovery receipts are retained here.

The receipt producer emits a structured, release/manifest/job/egress-bound receipt before normal successful exit. The deploy reader has no wait for independent Cloud Logging visibility after execution completion. This patch fixes that observed missing-receipt read boundary; it does not claim that an unobserved receipt is valid or that deployment has succeeded. A local later cloud read could not refresh the approved account (reauthentication required); no alternate account or IAM change was attempted. The existing workflow's WIF identity continues to own authorized cloud readback.

## Bounded fail-closed repair

- Capture the execution once and re-read logs with the same exact project/job/execution filter.
- At most **six reads**, with **five 10-second sleeps** only when a valid log list contains zero probe receipts. Do not re-execute the probe job or rerun the deployment inside this wait.
- Exactly one receipt must pass the unchanged candidate SHA, manifest digest, actual egress, job and semantic receipt digest checks before deployment can proceed.
- Cloud Logging errors, malformed/non-list payloads, duplicates and invalid receipts fail immediately without retry.
- Exhausted missing receipts still fail the deployment and invoke existing recovery.

No network/default-deny relaxation, synthetic receipt, blind rollout retry, application/auth change, credentials/IAM change, database reset, alternative workflow or staging/production operation.

## Local verification

Frozen Python 3.12 environment; base `ba40553a4ba0bdac588adbf8cc8adad4f8952750`.

```sh
bash -n product_ops/deployment/deploy_cloud_run_waji.sh
NODE_ENV=test uv run --python 3.12 pytest \
  tests/ops/test_egress_receipt_visibility.py \
  tests/ops/test_egress_readback_recovery.py \
  tests/ops/test_deploy_workflow_contract.py -q
uv run --python 3.12 ruff check tests/ops/test_egress_receipt_visibility.py
git diff --check
```

All commands completed with exit 0. Initial hosted CI `37600821089` also passed the product lanes but rejected the stale code-boundary inventory after adding the regression test file. The follow-up runs `check_code_boundaries.py --write-inventory` and its check successfully (1203 files), adding only the new verification file's inventory row. This is a generated inventory synchronization, not a classification-policy change.

The shell integration tests execute the actual capture function with simulated reads: missing/missing/valid accepts only the valid receipt; permanent absence performs exactly six reads/five sleeps then refuses; duplicate/wrong-candidate/wrong-job/non-list/read-error inputs refuse on their first read. Existing manifest/egress/recovery/workflow contract tests remain green.

## Independent review repair (2026-10-07)

Codex2 reopened PR #1428 at `5489619616026add6997f91e284fec5b722d5440`: malformed probe JSON in `textPayload` was silently discarded as absent, allowing retries and eventual acceptance alongside a valid receipt. Repair anchor `6d952273a734aa4ba256c0f3af2ae2a17ca2bd4e` rejects malformed text containing the explicit `"receipt_kind": "public_egress_probe"` field (including spacing variations). Extraction must finish before absence classification, validation or report writing, so malformed receipts also refuse when a valid receipt appears first in the same read. Ordinary unrelated text remains ignorable; valid JSON text receipts remain supported. The semantic validator, exact execution filter and default-deny are unchanged.

New shell regressions cover malformed-only, malformed-then-valid, malformed alongside valid in either order, and malformed spacing variants: all return nonzero after **one read, zero sleeps, no accepted report**. Unrelated text followed by a valid receipt retries once and succeeds; valid text JSON succeeds immediately. The existing six-read/five-sleep permanent-absence regression still passes.

The worker initially could not start `uv` because its PATH omitted `/home/lupin/.local/bin` (exit 127; not a test result). After adding that existing tool directory to PATH, the same evidence-declared verification commands above completed with terminal exit **0**, including the three focused pytest suites, shell syntax, ruff and diff checks. Original pytest output is retained as [`review-repair-pytest.log`](review-repair-pytest.log); no tests were rerun for counting. Verification used the repair anchor above; the subsequent evidence commit changes documentation only. No cloud mutation or live deployment was performed. Prior-head green CI is not proof for the repaired head: fresh protected CI and independent Codex2 approval are still required.

## Delivery boundary

Independent review, protected merge and merged-head CI remain mandatory. The same standing auto-dev workflow builds a fresh candidate and obtains real cloud receipts; old successful build artifacts are not silently rebound to changed source. Actual deployment is incomplete until its egress proof, jobs, tagged smoke, password/first-rotation/admin E2E and exact release readback pass. User's request to complete actual deployment does not waive those gates or authorize staging/production.

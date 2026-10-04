# AGY model compatibility — source-only proposal

Owner: Pi. Independent reviewer: Codex (not Codex2).

## Source change and authority

The supplied `agy-supported-model-compatibility.json` is copied unchanged from
`$PANTHEON_STATUS_ROOT/support/handoffs/dev-automatic-deployment-20261004/`.
It records successful installed CLI `agy models` readback, not inference.
The removed IDs are `claude-opus-4-6-thinking` and `claude-sonnet-4-6`.

| Dispatch | Source default |
| --- | --- |
| P0/P1, sensitive scope, review reopen | `claude-opus-5-5-high` |
| Ordinary/docs/lint, bounded sidecar/finalize | `gemini-3.7-flash-high` (unchanged, still supported) |
| Standard work with Gemini quota exhausted | `claude-sonnet-5-5-high` |

High-risk Claude policy remains Opus even when Gemini quota is exhausted.
When its Claude pool is cooling, high-risk delivery fails clearly rather than
launching on standard Gemini or labelling a downgrade as high risk. Standard
work retains its existing rotation semantics. No reset/cooldown policy changed.
The existing separate `--effort` rule is retained: high Claude IDs encode their
reasoning level and do not receive an additional effort override.

Explicit operator static/model-policy/fallback pins are not rewritten by source
defaults. Selection tests retain historical Sonnet4.6 pins; adapter dispatch
rejects the two known removed IDs before spawn with the exact offending ID and
migration-required error. This is a targeted compatibility guard based on the
supplied receipt, not a claim that every arbitrary configured ID has been
live-validated. Other IDs retain the existing CLI validation contract.
Dispatch metadata continues to bind the exact model and actual quota pool;
concurrent failures still cool the immutable dispatched pool, not current state.

## Proposed operator migration — NOT applied

`proposed-operator-migration.json` is an RFC6902 JSON Patch with both `test`
preconditions first. Its only two `replace` operations are:

- `/providers/antigravity/antigravity/model_rotation/fallback_model`
- `/providers/antigravity2/antigravity/model_rotation/fallback_model`

Both change `claude-sonnet-4-6` to `claude-sonnet-5-5-high`. Root must inspect the
exact patch after independent source approval/merge, apply through the existing
reviewed runtime/config rollout, and verify loaded source SHA/config digest.
A changed/missing precondition requires operator adjudication, not blind
replacement. No canonical config read/write, rollout, credentials read, or live
inference was performed by this source task. Example config is not live config.
No capability/auth/role/permission/sandbox flag is weakened.

PR1409 and its six findings/history/count, original owner/reviewer, and
continuation gates remain untouched. This source fix neither admits that task
nor authorizes an alternate worker/runtime/release lane. Any later runtime probe
requires the original task's eligibility/authority and a normal admitted worker.

## Focused verification

The declared unittest command now executes nine real unittest cases rather
than discovering zero pytest functions. Canonical metadata `assign` registered
`uv run pytest -q .orchestrator/test_model_rotation.py` to also cover the existing
pytest regressions and adapter mocks. All process/auth interactions are mocked;
quota files are isolated in temporary directories. No model inference is tested.

Verification receipts bind measured HEAD, exact command, exit code, duration,
selection, and tested file blobs. An evidence-only successor must preserve
those blobs; final submitted-head checks are separately recorded in the
canonical task note. No approval/done claim is made by these receipts.

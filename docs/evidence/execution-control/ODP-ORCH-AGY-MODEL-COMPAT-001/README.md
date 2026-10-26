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
When its Claude pool is cooling, high-risk delivery produces a structured
pre-launch environmental wait, with the exact selected model, quota pool and
existing reset deadline. The launch boundary does not emit a failed-worker
receipt. The queue uses its existing retry-backoff state without spending the
failure/retry budget, reassigning the owner, or recording new quota exhaustion.
After expiry it revalidates normal dispatch gates and resumes the same owner
on the high model, never standard Gemini or a labelled downgrade. Standard
work retains its existing rotation semantics. No reset/cooldown policy changed.
Explicit `gpt-*` fallback models bill the existing Claude/GPT quota pool; their
immutable dispatch metadata ensures quota failure cools Claude/GPT, not Gemini.
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

## Review repair and base composition

PR #1413's independent review identified the generic cooldown-failure path and
GPT fallback pool attribution. Both are repaired in this same task branch.
Current base `a2dbb63eb19d2427baf007b7b6455d25ec007e2f` was merged normally,
without conflicts or rewriting/discarding task history. Canonical metadata
registered `.orchestrator/supervisor.py` (only launch-result handling) and
`.orchestrator/worker_lifecycle.py` (only queue wait/resume handling) before
editing. A follow-through audit also registered
`.orchestrator/worker_failure_policy.py` before repairing its narrow
`retry_due_workers` caller: already-retrying workers retain their parent and
retry budget during the same environmental wait, then launch a high-model
replacement after expiry. No failure classifier changes are needed: this
explicit structured pre-launch wait bypasses failure processing; genuine
config/terminal errors retain the existing classifier/streak/reassignment path.

New `CooldownLifecycleTests` exercise real adapter -> launch -> queue handling,
P0/P1/reopened work, both-pools cooling, no-launch/no-failure before reset,
same-owner/exact-model resumption at reset, retry-parent/budget preservation,
genuine terminal config failure, and explicit GPT fallback metadata plus actual
quota handling. Process/auth,
workspace/status persistence and activity logging are mocked; time and cooldown
files are isolated. The new focused selection was registered before execution:
`uv run --python 3.12 pytest -q .orchestrator/test_model_rotation.py -k CooldownLifecycleTests`.

## Review repair verification

All five registered commands passed on repair anchor
`cff3436110e737e1b62e27ffcd8895938dc89d07`, after the normal base merge:

| Command | Result | Exit | Seconds |
| --- | --- | --- | --- |
| `git diff --check` | clean | 0 | 0.015 |
| `python3 -m unittest discover -s .orchestrator -p test_model_rotation.py` | 14 tests | 0 | 1.586 |
| `uv run --python 3.12 pytest -q .orchestrator/test_model_rotation.py` | 63 test dots, 100% | 0 | 3.508 |
| `python3 delivery_toolchain/governance/check_code_boundaries.py` | 1199 files | 0 | 9.086 |
| `uv run --python 3.12 pytest -q .orchestrator/test_model_rotation.py -k CooldownLifecycleTests` | 5 test dots, 100% | 0 | 2.722 |

`review-repair-receipts/` contains the tool's unmodified, SHA-bound receipts
from both repair anchors (`cd9a3cec` and `cff34361`); exit codes, durations,
selections and original output are retained. Pytest's quiet output has no
count summary; dots are counted from completed receipts, not by rerunning.
The evidence-only successor preserves the tested code/config blobs and runs
the registered checks again at its exact submission SHA through
`task_verification.py`; those final receipts remain in the normal receipt store
and are referenced in the canonical task note before publication.

## Second independent review: complete supervisor loops

Codex reopened exact head `5d8ac767ed190d6859c688ba6de2ec7f75263c22` with
integration findings in both normal callers. This repair preserves that review
history/count and the same PR #1413; no original-task gate or live adoption is
changed.

- `poll_workers` now recognizes a validated structured cooldown wait **after**
  normal orphan/assignment/helper authority reconciliation. A dead parent keeps
  its original failure log and runner exit receipt, but does not replay that
  already-handled failure into retry/streak/reassignment processing. At reset,
  `retry_due_workers` additionally checks current responsibility before launch
  (it runs before the poller's reconciliation). Changed ownership supersedes
  the parent both before and at reset, without launching an obsolete request.
- `prune_event_queue` no longer rewrites a valid workerless cooldown wait to
  `queued`. Orphan reconciliation likewise retains that deliberate wait,
  including its due tick; ordinary stale-dispatch checks still retire it when
  responsibility changes. The shared predicate requires retry-backoff status,
  structured kind/model/pool, and a valid reset timestamp matching
  `next_retry_at`; malformed/ordinary backoff retains existing queue repair and
  orphan behavior. Expiry is consumed by the ordinary dispatch/retry path.

The already-registered `CooldownLifecycleTests` selection now runs complete
`poll_workers` ticks with a real original failure log and failed runner marker
(exit1), plus `process_queue -> prune_event_queue -> process_queue` cycles.
Repeated pre-reset ticks preserve owner, exact high model/pool, deadline,
retry/attempt budget and cooldown file; no failure evidence, streak, retry
rescheduling or reassignments occur. P0 and reopened parents resume on the
same supported high model at expiry. Queue coverage includes P0/P1/reopened,
both-pools cooling, events aged beyond orphan grace, unchanged workspace/
adapter/activity call counts while waiting, moved ownership and malformed
waits. No live processes, inference, credentials, canonical config or runtime
were used; process and external persistence boundaries are mocked.

All five registered commands passed on loop-repair anchor
`b53b28b683990701e8a57806262eab852a7ed6a0`:

| Command | Result | Exit | Seconds |
| --- | --- | --- | --- |
| `git diff --check` | clean | 0 | 0.015 |
| `python3 -m unittest discover -s .orchestrator -p test_model_rotation.py` | 17 tests | 0 | 1.628 |
| `uv run --python 3.12 pytest -q .orchestrator/test_model_rotation.py` | 66 dots, 100% | 0 | 3.668 |
| `python3 delivery_toolchain/governance/check_code_boundaries.py` | 1199 files | 0 | 9.135 |
| `uv run --python 3.12 pytest -q .orchestrator/test_model_rotation.py -k CooldownLifecycleTests` | 8 dots, 100% | 0 | 2.835 |

`review-loop-receipts/` retains all20 unmodified receipts from four distinct
anchors, including the three initial failed fixture iterations (static nested
mock limit, originally orphaned rather than admitted event, and unmocked queue
file replacement). No failed run is represented as a pass. Each fixed fixture
was committed before remeasurement at a new SHA. No wider suite or live test
was substituted. This evidence-only successor keeps the tested production and
test blobs unchanged; all five commands are then measured again at its exact
submission SHA in the standard receipt store, referenced in the canonical
status note before `task_finalize.sh` publication. Independent Codex approval
and merge remain required before operator adoption or task closeout.

## Original pre-review verification (historical)

The initial declared unittest command executed nine real unittest cases rather
than discovering zero pytest functions. Canonical metadata `assign` registered
`uv run --python 3.12 pytest -q .orchestrator/test_model_rotation.py` to also cover
the existing pytest regressions and adapter mocks. The initial unpinned `uv run`
chose Python3.14 and failed dependency installation (pgserver cp312 only), exit2
before collection; the explicit3.12 run passed all58 selected tests. Anchor
`ec7ff5bd683f36878e2edb30a472593a17f1bc11` also passed unittest9, diff check and
code boundaries1199. `verification-anchor/` retains every command receipt,
including that initial failure, not just successful output.
All process/auth interactions are mocked;
quota files are isolated in temporary directories. No model inference is tested.

Verification receipts bind measured HEAD, exact command, exit code, duration,
selection, and tested file blobs. An evidence-only successor must preserve
those blobs; final submitted-head checks are separately recorded in the
canonical task note. No approval/done claim is made by these receipts.

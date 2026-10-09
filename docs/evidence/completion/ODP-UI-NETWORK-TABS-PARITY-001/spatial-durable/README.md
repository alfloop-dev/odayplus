# Spatial genuine API / SQLite readback — bounded increment

Task `ODP-UI-NETWORK-TABS-PARITY-001` · owner Pi · reviewer Codex2.
**Not full acceptance or review resubmission. Generated local integration data,
not live maturity, deployment, PostgreSQL, authenticated IdP or production WORM proof.**

## Sources and design boundary

- Before source: `b0b88e97d900` (UI inherited from `3f34a3ba6`).
- Contrast repair: `cfe2df48f43d`; final verified source: `153d3702e52825545671dfa0e6d52819676a678b`.
- Fresh `origin/dev`: `2fe3ef933794d0bb293449b74dffe9602d53f767`, still an ancestor.
- Package 10 has **no Spatial reference or decision dialog**. Same-width Network
  reference images and absence assertions remain in
  [`../spatial-availability/`](../spatial-availability/). These are later-spec
  integrations, not fabricated prototype parity.
- `source.sha256` binds the changed UI, harness/config and boundary inventory.

## What this proves

No browser route interception or synthetic decision response is used. The test
factory loads the existing integration helper's generated 224-day / 32-cell
absorption history into isolated SQLite relations. The existing test-only matured
inventory loader seam enables evaluation; its fixture name includes a future
inventory date. **This does not certify release-bound data readiness.** No canonical
receipt, release gate, production setting, IAM or business backend file is changed.

Four cases exercise approve/reject at 1440×900 and 390×900:

1. Actual mounted `/evaluate` computes a merge from the SQL fixture history,
   including measured metrics, model/policy versions and declined candidates.
2. The rendered preview invokes the actual API; proposal ID, cells and gain are
   compared with generated output.
3. The modal's real POST succeeds; a separate API GET and UI list readback confirm
   terminal state, authenticated **local header-stub** subject and recorded reason.
   The browser client intentionally does not consume the POST response body;
   `decision.json` records POST status/body sent and independent GET, not an
   invented POST response payload.
4. Full page reload retains the terminal proposal and removes both decision CTAs.
   A repeated API decision returns 422; a separate Python process opens a fresh
   SQLite bundle and confirms the same proposal and exactly one decision audit
   event, including actor/reason and intact full audit hash chain.
5. Approval persists two composition records with exact cells, model/policy and
   decider; lineage reports active topology. The harness then explicitly rolls
   back those fixture records so the next case can evaluate the same cells.
   Rollback receipts are retained. Rejection creates no active topology.

Final server and inspector bind a scratch-local exclusive-create audit sink;
`fresh-process.json` includes the checked `file:///tmp/.../audit-worm` identity.
This is local append-only behavior, **not retained cloud WORM**. Fresh connections
are tested while the API runs; a complete server restart/deployed DB is not claimed.
Baseline/earlier runs used the worktree-local default audit sink, never the live
canonical status files; final receipts use the explicitly isolated sink.

## Paired visual / accessibility evidence

`before/` and `after/` each contain 12 screenshots plus geometry, scoped axe,
generation/preview/decision/reload, refusal and fresh-process JSON receipts.
Each approve/reject width captures preview, modal and reloaded terminal detail.
Geometry enforces document containment, visible control widths and contained modal
height. Different random proposal IDs/timestamps are expected; fixture metrics and
layout are otherwise stable.

Genuine persisted rejection text exposed an existing 4.4:1 contrast violation on
`#f1f5f9` that the prior controlled response (without a rejection reason) missed.
The repair changes only its foreground from `#dc2626` to `#b91c1c`. Both baseline
rejection terminal scans retain the violation; all **12 final scoped AA scans are
clear**, without rule exclusion. Baseline mode records AA findings rather than
asserting their absence; final mode asserts every scoped finding list is empty.
Pi inspected the before/after mobile terminal pair and final isolated capture:
reason, decider and audit timestamp remain visible and wrapped. This is owner
inspection, not independent VDC-005 sign-off or whole-console accessibility.

## Original terminal receipts

All exits were captured synchronously in the same shell immediately after the
original command; counts come from existing terminal logs, not count-only reruns.

| Check | Result / logs |
|---|---|
| Before generated API + SQL captures | 4 passed, exit 0 · `before.*` |
| Final isolated generated API + SQL captures | 4 passed, exit 0 · `after-isolated-final.*` |
| Prior post-repair capture | 4 passed, exit 0 · `after.*` |
| Existing controlled Spatial states/modals regression | 9 passed, exit 0 · `render-regression.*` |
| Focused panel/client/workspace Vitest | 50 passed / 3 files, exit 0 · `focused.*` |
| Full web Vitest | 785 passed / 72 files, exit 0 · `unit.*` |
| Typecheck, web lint, Python harness ruff | exit 0 · `typecheck.*`, `lint.*`, `ruff-final.*` |
| Boundaries, business inventory, diff | exit 0 · `boundaries-final.*`, `inventory.*`, `diff-final.*`; business inventory unchanged 125 / 18 |

Failures are also retained: startup tried to seed a read-only SQL evidence reader;
second run waited on an unconsumed browser POST body (other cases could not propose
with its uncleared active merge); third found broad Playwright context headers had
overridden persona identity; fourth found the genuine rejection contrast defect.
`after-isolated.*` retains the unsupported factory argument failure, repaired using
the public environment seam. `boundaries.*` failed before adding the harness row;
final inventory includes it. `api.*` and `ruff.*` show `uv` missing (exit 127): no
Python API suite result is claimed. The project's existing `.venv/bin/python`
ruff command completed; nothing was installed or located by a host-wide search.
Vitest stderr includes expected fixture/network/SSL warnings; its terminal exit is 0.

## Reproduction

Use a **new** scratch directory for every run; the server refuses an existing DB.
Do not overlap browser servers or builds on these ports.

```sh
mkdir -p /tmp/spatial-new-run /tmp/spatial-new-evidence
OPSBOARD_PORT=3310 ODP_API_PORT=8310 ODP_API_BASE_URL=http://127.0.0.1:8310 \
NETWORK_SPATIAL_DURABLE_DIR=/tmp/spatial-new-run \
NETWORK_PARITY_CAPTURE_PHASE=after NETWORK_PARITY_EVIDENCE_DIR=/tmp/spatial-new-evidence \
npx playwright test --config tests/visual/operator-spatial-durable-parity.config.ts
```

The before run used phase `before` at the pinned before source. Remaining owner
work includes real Transfer/Pause resource IDs/versions, applicable Spatial split /
permission / negative-state pairs (durable barrier evidence remains separately
gated), Find Areas reload-before-back/detail density, remaining all-tab negative
states, final complete verification/CI and independent VDC outcomes. Preserve the
later-spec explanation and release-bound maturity hold. **Do not finalize or move
to review/done for this bounded checkpoint.**

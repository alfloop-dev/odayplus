# Review Packet: ODP-OBS-INSTRUMENTATION-AS-CODE-001

- Sidecar task: `ODP-OBS-INSTRUMENTATION-AS-CODE-001-SIDECAR-REVIEW`
- Parent task: `ODP-OBS-INSTRUMENTATION-AS-CODE-001`
- Sidecar owner: `Codex`
- Assigned sidecar reviewer: `Codex3`
- Parent owner at evidence capture: `Antigravity4`
- Parent reviewer: `Antigravity`
- Evidence captured: `2026-08-08T13:33:20Z`
- Parent branch: `origin/task/ODP-OBS-INSTRUMENTATION-AS-CODE-001`
- Exact reviewed parent HEAD: `5dba12f89a927bcc8ad8c562d6c89bc5cf6419ee`
- Parent PR: `#704` (`OPEN`, `BLOCKED` at capture time)
- Scope: support-only review packet; no parent implementation, canonical truth, runtime registry, monitoring configuration, or governance implementation changed.

## Executive disposition

The parent branch adds a reproducible observability evidence generator and its generated JSON/Markdown artifacts. Relative to its merge base with current `origin/dev`, its task-owned surface is exactly three files:

- `scripts/e2e/generate_obs_instrumentation_evidence.py`
- `docs/evidence/completion/ODP-OBS-INSTRUMENTATION-AS-CODE-001/evidence.json`
- `docs/evidence/completion/ODP-OBS-INSTRUMENTATION-AS-CODE-001/evidence.md`

The underlying observability runtime, metric catalog, dashboards, alert routing, SLOs, and runbooks already exist outside this parent diff. The parent therefore packages evidence for existing behavior; it does not itself implement those runtime/configuration layers.

Independent execution confirms that the evidence generator runs and the focused observability suite passes all 71 tests. The packet is nevertheless **not ready for parent closeout**:

1. PR `#704` has a failing required `orchestrator` CI job caused by four unused imports in the new generator.
2. The generated evidence overstates three acceptance claims: named signal ownership is not present in `MetricDefinition`, bounded label cardinality is neither enforced nor tested, and the generator does not verify alert-to-release identity binding.
3. The Markdown hard-codes an old full-suite statement. The focused file contains 71 tests, while the current full `tests/reliability/` collection contains 137 tests.
4. The generator records a synthetic test SHA (`ffff...ffff`) without classifying it as test-only in the artifact.

Recommended disposition: approve this sidecar packet as an accurate review aid, but return the parent to implementation/re-review before merge.

## Parent provenance and change surface

| Item | Captured value | Review observation |
| --- | --- | --- |
| Approved parent head | `5dba12f89a927bcc8ad8c562d6c89bc5cf6419ee` | Matches `approved_head`, `last_approved_head`, origin task ref, and PR `headRefOid`. |
| PR | `#704` | Open and blocked; task review gate, product, product E2E, and performance checks are green, but `orchestrator` is red. |
| CI run/job | Run `31254950401`, job `93096861127` | Fails at `Lint orchestrator code`; later orchestrator steps are skipped. |
| PR base at run | `dbb046994d9e6f0720ab46fe5450da97183b8179` | Current `origin/dev` had advanced to `50dda113403328a7aa11830e40d037a8ba1c5cb8` at capture time. A base update will move the parent head and requires re-review. |
| Task-owned diff | 3 added files | Evidence generator plus generated JSON and Markdown only. |

## Acceptance evidence matrix

| Parent acceptance criterion | Independent result | Evidence and gap |
| --- | --- | --- |
| Required signals have stable names and owners | **PARTIAL / NOT PROVEN** | Stable names are defined by the 34-entry `PLATFORM_METRICS` catalog and exercised by tests. `MetricDefinition` has `name`, `type`, `category`, `description`, `labels`, `unit`, and bounds, but no owner field. Dashboard audiences and alert receivers do not establish an owner for every required signal. The generator does not test ownership. |
| Sensitive values are excluded | **PASS** | The generator asserts redaction for password, access token, and API key. Focused tests also cover recursive redaction and non-sensitive field preservation. |
| Cardinality is bounded | **NOT PROVEN** | Metric definitions declare allowed label names, but `MetricsRegistry._resolve()` accepts arbitrary label keys/values and does not validate them against `definition.labels`, cap series count, or constrain value domains. `bounded_cardinality_verified` is set to `true` unconditionally. The cited round-8 test checks numeric metric value bounds, not label cardinality. |
| Alerts link to runbooks and release identity | **PARTIAL** | All 11 alert entries name a runbook file and the named Markdown file exists. The generator checks only file existence, does not validate anchors, and does not assert that every `runbook_verified` result is true. `alerts.json` contains no release SHA field; release binding exists separately in dashboard/exporter/notification paths, but the generator does not prove that every alert carries that identity. |
| Configuration and emission tests are reproducible | **CONDITIONAL PASS** | Generator execution and the 71-test focused observability suite pass. Full reliability collection is now 137 tests and exits successfully in this review environment, so the hard-coded `71/71 tests/reliability` narrative is stale. CI remains red on lint, so repository-level reproducibility is not yet green. |

## Concrete parent findings

### B1 — Required CI failure: unused imports

`uv run ruff check .orchestrator scripts` reports four `F401` errors in `scripts/e2e/generate_obs_instrumentation_evidence.py`:

- `shared.observability.logging.redact`
- `shared.observability.metrics.MetricCategory`
- `shared.observability.metrics.MetricsRegistry`
- `shared.observability.metrics.ProductionMetricsExporter`

All four are mechanically removable if the generator will not exercise those contracts. If the unused exporter types were intended to prove release binding, the stronger remediation is to add explicit verification rather than merely deleting the imports.

### B2 — Signal ownership claim is unsupported

The acceptance language requires stable names **and owners**. The metric catalog provides stable definitions but no owner field or external per-signal ownership mapping. The generated Markdown marks this criterion passed without checking owner coverage.

Required remediation: add a code/config-owned ownership mapping with non-empty owner validation for all required signals, or narrow the parent claim and acceptance evidence through the proper canonical owner.

### B3 — Cardinality claim is unsupported

The registry converts any supplied labels to strings and uses the complete label tuple as a series key. It neither rejects undeclared labels nor bounds the number or domain of label values. Numeric min/max tests are valuable but are not cardinality controls.

Required remediation: enforce declared label keys and bounded value/series policies, add rejection tests for undeclared/high-cardinality labels, and derive `bounded_cardinality_verified` from those checks instead of a literal `true`.

### B4 — Alert release identity evidence is incomplete

The generator's alert loop records alert metadata and checks only whether the runbook file exists. It does not:

- assert every runbook exists;
- validate the Markdown fragment after `#`;
- bind or verify a release SHA for each emitted/routed alert; or
- invoke the notification/exporter path that enforces release identity.

Required remediation: validate the full runbook target and exercise the release-bound alert delivery/receipt path, or explicitly scope the evidence to runbook file linkage only.

### N1 — Generated test evidence is presented as release evidence

The JSON uses `release_sha = ffffffffffffffffffffffffffffffffffffffff`, while the reviewed parent head is `5dba12f8...`. The value is generated by a local simulation and is not a deployment identity. Mark it `TEST_ONLY` with the actual source commit captured separately, or bind the artifact to the reviewed source SHA without implying live provider delivery.

### N2 — Test narrative is stale and non-derived

The generator embeds a static result block claiming `71 passed in 19.04s` for `tests/reliability/`. At review time:

- `tests/reliability/test_runtime_observability.py`: 71 tests, independently passed;
- `tests/reliability/`: 137 tests collected, suite execution exited `0`.

The generated artifact should receive structured test results or omit timing/count claims it did not produce itself.

### N3 — Local `file://` links are not portable

The Markdown artifact embeds absolute paths from the parent's isolated worktree. Those links will not resolve for GitHub reviewers or after worktree cleanup. Repository-relative links should be used.

## Independent verification log

Commands were executed against exact parent HEAD `5dba12f89a927bcc8ad8c562d6c89bc5cf6419ee` or its task-owned worktree.

```bash
/home/lupin/oday-plus/.venv/bin/python scripts/e2e/generate_obs_instrumentation_evidence.py
# exit 0; evidence files materialized in a disposable detached worktree

/home/lupin/oday-plus/.venv/bin/python -m pytest \
  -o addopts='' -q tests/reliability/test_runtime_observability.py
# 71 passed, 5 warnings in 12.72s

/home/lupin/oday-plus/.venv/bin/python -m pytest tests/reliability/
# exit 0

/home/lupin/oday-plus/.venv/bin/python -m pytest \
  -o addopts='' --collect-only -q tests/reliability/
# 137 tests collected

/home/lupin/oday-plus/.venv/bin/ruff check .orchestrator scripts
# exit 1; four F401 unused-import findings in the new generator

git diff --check
# clean
```

The detached verification worktree was removed after execution. Both the parent task worktree and this sidecar worktree were clean before this support artifact was created.

## Reviewer checklist

- [ ] Confirm this packet stays support-only and makes no canonical/runtime change.
- [ ] Confirm parent owner addresses B1 before attempting merge.
- [ ] Decide whether B2-B4 require implementation or a formally narrowed acceptance claim.
- [ ] Require a new parent head and `re_review` after parent fixes or base advance; do not reuse approval frozen at `5dba12f8`.
- [ ] Confirm regenerated evidence no longer hard-codes stale test results, synthetic release provenance, or absolute worktree links.

## Sidecar boundary and handoff

This file is the sole deliverable of `ODP-OBS-INSTRUMENTATION-AS-CODE-001-SIDECAR-REVIEW`. It is a non-canonical support artifact and does not alter parent acceptance state by itself.

Handoff target: `Codex3` (current assigned sidecar reviewer). `Antigravity4` was the parent owner when the evidence was captured; the current parent owner decides which findings to absorb into `ODP-OBS-INSTRUMENTATION-AS-CODE-001` before returning that parent task to reviewer `Antigravity`.

## Closeout refresh — 2026-08-08

Codex3 approved the support-only packet at pushed HEAD
`3d2325d739f229eeb88c8a1abf451caafa6f176a`. Before closeout, the task
branch was history-preservingly composed with current `origin/dev`
`9282082b4c9679d688ffb55db289159450b8a7ed` by merge commit
`c78f65c4c0ee05d3d1d74fb75b21cffa2cba81da`. The compose completed without
conflicts, and the pre-refresh artifact remained byte-identical to the approved
packet (SHA-256
`6dac6264cedde6314350c18acca9a6b17ee098a35b4b8905bab07dec59a4adee`).

Closeout verification confirms that `origin/dev` is an ancestor of the task
HEAD, `origin/dev...HEAD` remains limited to this declared support artifact,
and `git diff --check` passes. This ancestry refresh does not change any parent
finding or approve the parent implementation. Because it produces a new exact
task HEAD, the refreshed packet must be pushed normally and re-approved by
Codex3 before the task can merge and move to `done`.

## Final base advance — 2026-08-08

After `origin/dev` advanced again, Codex fetched the current remote refs and
history-preservingly composed `origin/dev`
`7430ba8539ca58f6c19a654ec81b8919a2f28583` into this task branch with merge
commit `2db1a24fd30c5554763bd699802ebffe15bf5629`. The compose completed without
conflicts. Verification confirmed that `origin/dev` is now an ancestor of the
task HEAD, `origin/dev...HEAD` still contains only this support packet as its
task-owned file, and `git diff --check` passes.

This finalization refresh does not alter the review findings, broaden the
sidecar boundary, or approve any parent implementation. It advances the exact
task HEAD, so the branch must be pushed normally and handed back to Codex3 via
`re_review` before merge and `done`.

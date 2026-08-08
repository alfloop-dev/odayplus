# LearningHub Operations Review Packet

- Sidecar task: `ODP-CAP-LEARNINGHUB-OPERATIONS-001-SIDECAR-REVIEW`
- Parent task: `ODP-CAP-LEARNINGHUB-OPERATIONS-001`
- Prepared by: Codex
- Assigned sidecar reviewer: Antigravity3
- Evidence snapshot: 2026-08-08T13:29:08Z
- Parent review head: `c9ea148fc52c8f3b5b51960983c4c4b41749abc0`
- Parent PR: [#707](https://github.com/alfloop-dev/odayplus/pull/707)

## Scope Boundary

This packet summarizes review evidence for the parent implementation. It does
not modify or redefine canonical architecture, runtime contracts, registry
governance, or the parent implementation. The parent owner decides whether and
how to compose these findings into the main task.

## Review Disposition

The parent acceptance test is reproducible at the approved head, and the code
contains implementation anchors for all five stated acceptance criteria.
However, PR #707 is not ready to merge: its `product` CI job fails in the lint
step, and the same failure is reproducible locally. The parent owner must fix
the lint errors and request re-review because that fix will advance the frozen
approved head.

Two additional reviewer questions are recorded below: the API triage path
appears to emit the same audit event once in the service and once in the route,
and the new permission test checks dependency wiring rather than a complete
authorized-role matrix. These are review observations, not sidecar changes.

## Acceptance Evidence Matrix

| Acceptance criterion | Implementation anchor | Test/evidence anchor | Assessment |
| --- | --- | --- | --- |
| DQ actions persist actor, time, and rationale | `DqTriageRecord` in `modules/learninghub/domain/dataset_snapshot.py`; `record_dq_triage` in `modules/learninghub/application/release.py`; in-memory and durable repository methods | `test_dq_actions_persist_actor_time_and_rationale` in `tests/integration/test_learninghub_operations_acceptance.py` | Focused test passes and covers persistence plus service audit event. |
| Model operations are role gated | `require_permission("model", Action.*)` dependencies on LearningHub dataset, model, release, evidence, and triage routes in `apps/api/app/routes/learninghub.py` | `test_learninghub_api_role_gating_and_triage_endpoints` | Route dependency wiring and unauthenticated denial are covered; an authorized-role action matrix is not exercised by this new test. |
| Empty registry never fabricates a model | Existing repository list/get behavior remains empty/`None`; `/models` serializes repository contents without fallback creation | `test_empty_registry_never_fabricates_a_model` | Repository invariant passes. The new test does not perform an authorized HTTP request to the empty `/models` endpoint. |
| Unsupported promotion fails closed | Existing `LearningHubService.request_release` precondition, self-review, and rollback-target guards | `test_unsupported_promotion_fails_closed` | Focused test passes for missing required arguments, self-review, and missing FULL rollback target. |
| Lifecycle and permission tests are delivered | New `tests/integration/test_learninghub_operations_acceptance.py` | Parent `verification.md` records focused `4 passed` and full LearningHub `41 passed, 3 skipped`; sidecar independently reran the focused suite | Test artifact exists and focused result is independently reproducible. Full-suite result is owner-recorded, not independently rerun by this sidecar. |

## Change Surface at Approved Head

Relative to the parent branch base, the approved head changes nine files with
401 insertions:

- API routes: `apps/api/app/routes/learninghub.py`
- Application service: `modules/learninghub/application/release.py`
- Domain/export surface: `modules/learninghub/domain/dataset_snapshot.py` and
  `modules/learninghub/domain/__init__.py`
- In-memory/durable persistence:
  `modules/learninghub/infrastructure/repositories.py` and
  `shared/infrastructure/persistence/repositories.py`
- Acceptance test: `tests/integration/test_learninghub_operations_acceptance.py`
- Parent evidence: `docs/evidence/completion/ODP-CAP-LEARNINGHUB-OPERATIONS-001/implementation.md`
  and `verification.md`

No production model is required or fabricated by this scope.

## Independent Verification

Run from the parent task worktree at
`c9ea148fc52c8f3b5b51960983c4c4b41749abc0`:

```text
python3 -m pytest tests/integration/test_learninghub_operations_acceptance.py -q
```

Result: **PASS — 4 passed**. Two non-failing third-party warnings were emitted:
a Starlette `httpx` deprecation warning and an MLflow model-stage deprecation
warning.

```text
uv run ruff check tests modules apps shared models solver pipelines infra
```

Result: **FAIL — 9 errors**, all in
`tests/integration/test_learninghub_operations_acceptance.py`:

- `I001`: import block is not sorted/formatted.
- Seven `F401` errors: unused imports (`MetricThreshold`, `ModelAlias`,
  `ModelCardApproval`, `ModelStage`, `ModelVersion`, `_model_card`, and
  `_model_version`).
- `F841`: local variable `service` is assigned but unused in the empty-registry
  test.

This matches PR #707: the `product` job failed at **Lint product code**, so its
product tests and later product steps were skipped. At the evidence snapshot,
the other reported checks were:

| Check | Result |
| --- | --- |
| `orchestrator` | Pass |
| `performance-gate` | Pass |
| `product` | Fail at lint |
| `product-e2e-gate` | Pass |
| `task-review-gate` | Pass for approved head `c9ea148f...` |

## Reviewer Attention

1. **Merge blocker — lint:** remove the unused imports/assignment and organize
   imports, rerun the exact product lint command, then rerun the focused test.
   Because this changes the parent head beyond `c9ea148f...`, use the formal
   re-review flow before finalization.
2. **Possible duplicate audit record:** the POST triage route calls
   `LearningHubService.record_dq_triage`, which records
   `learninghub.dq_triage_recorded.v1`, and then the route calls `_record_audit`
   with the same event type against the same `active_audit_log`. Confirm whether
   two audit entries per API action are intentional. The focused test calls the
   service directly and therefore does not detect this route-level behavior.
3. **Permission coverage boundary:** the new API test proves unauthenticated
   denial and inspects route dependencies, but it does not exercise which roles
   can and cannot perform each create/view/approve/publish/update operation.
   Reviewer should decide whether structural coverage satisfies this task or an
   authorized-role matrix is required before parent closeout.
4. **Empty-registry HTTP boundary:** the invariant is covered at repository
   level. If the acceptance intent includes the operator HTTP response, add an
   authenticated empty-registry API assertion rather than relying only on route
   inspection.

## Suggested Parent Handoff

- Fix the narrow lint defects on the parent branch.
- Resolve or explicitly accept the duplicate-audit and coverage observations.
- Rerun:
  `uv run ruff check tests modules apps shared models solver pipelines infra`
  and
  `python3 -m pytest tests/integration/test_learninghub_operations_acceptance.py -q`.
- Record the new exact head and use `re_review` because the currently approved
  head will no longer match.
- Merge/finalize only after required CI is green and the new head is approved.


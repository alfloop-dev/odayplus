# Sidecar Review Packet: ODP-CAP-INTERVENTION-WORKSPACE-001

## Packet identity

- Sidecar task: `ODP-CAP-INTERVENTION-WORKSPACE-001-SIDECAR-REVIEW`
- Parent task: `ODP-CAP-INTERVENTION-WORKSPACE-001`
- Helper kind: `review_packet`
- Sidecar owner / reviewer: `Codex3` / `Antigravity2`
- Parent owner / reviewer recorded in completion evidence: `Antigravity4` / `Claude`
- Parent implementation head: `4c0f6069edcc6aad8ebde161052abb3fece3851e`
- Parent merge commit: `a78776214c638e6d0ac6573cdd0090143886637a` (PR #702)
- Review baseline: `0c36566c4bb14b908fa4f896defd5552c1af68e6`
- Captured: 2026-08-08 UTC
- Scope: support evidence and reviewer handoff only; this packet changes no
  canonical truth, runtime, registry, OpenAPI artifact, test, or governance file.

## Outcome

The merged parent implementation is present on the sidecar baseline, and the
focused contract and integration suites complete successfully. The repository
evidence supports the five backend acceptance claims recorded by the parent:
deep-linkable inbox/detail API access, server-authoritative transitions, RBAC,
optimistic-concurrency conflicts, and per-decision audit evidence.

This packet recommends `READY_FOR_REVIEW` for the sidecar. It does not issue a
second approval of the parent and does not change parent task truth. The parent
owner decides whether to absorb the portability corrections and review notes
below into the canonical completion record.

## Parent delivery provenance

PR #702 merged parent head `4c0f6069` into `dev` as `a7877621`. Both commits
are ancestors of this packet's baseline. Relative to the merge commit's first
parent, the parent delivery changes seven files:

```text
 apps/api/app/routes/interventions.py               |  97 +++++-
 docs/evidence/completion/.../COMPLETION_EVIDENCE.md|  68 ++++
 modules/intervention/application/workflow.py       | 112 ++++++
 modules/intervention/domain/lifecycle.py           |  12 +
 packages/openapi-client/openapi.json               | 375 +++++++++++++++++++++
 packages/openapi-client/src/generated/types.ts     |  16 +
 tests/integration/test_intervention_workflow.py    | 139 +++++++-
 7 files changed, 811 insertions(+), 8 deletions(-)
```

The parent diff includes backend domain, workflow, API, generated contract,
integration coverage, and completion evidence. It includes no frontend/UI
file. Accordingly, this packet treats “Inbox and Detail workflow” as the
backend/API scope evidenced by the parent record; it does not claim that a
rendered operator workspace was implemented or visually tested.

## Acceptance and evidence matrix

| Ref | Recorded parent acceptance | Repository evidence on the review baseline | Sidecar assessment |
| --- | --- | --- | --- |
| A1 | Inbox and detail are deep-linkable | `list_interventions` accepts `store_id`, `assigned_to`, `status`, and `kind` and delegates to `list_cases` in `apps/api/app/routes/interventions.py:266`; detail is exposed at `GET /{intervention_id}` at line 287. Integration coverage is in `test_api_assignment_rbac_and_inbox_deep_link_filtering`. | PASS for API deep links. No UI claim. |
| A2 | State transitions are server-authoritative | `InterventionWorkflow` owns assignment, eligibility, action, conflict, approval, execution, outcome, evaluation, and close operations in `modules/intervention/application/workflow.py`. API mutation routes delegate to that workflow. | PASS. |
| A3 | Unauthorized actions are rejected | Intervention routes attach `require_permission("intervention", ...)`; the focused integration suite includes negative assign/unassign RBAC assertions. | PASS. |
| A4 | Stale/conflicting updates are visible | `Intervention.version` is serialized in `modules/intervention/domain/lifecycle.py:532`; workflow `_check_version` is at `modules/intervention/application/workflow.py:967`; API maps stale updates to `STALE_UPDATE_CONFLICT` at `apps/api/app/routes/interventions.py:621`. | PASS. |
| A5 | Every decision writes audit evidence | Workflow `_audit` is at `modules/intervention/application/workflow.py:990`, with call sites across creation and lifecycle mutations. Assignment, lifecycle, close, conflict, and label behavior are exercised by the focused integration suite. | PASS for the recorded lifecycle surface. |
| A6 | Generated contract stays aligned | Checked-in OpenAPI paths include assign and unassign at `packages/openapi-client/openapi.json:15235` and `:16379`; the artifact/client drift suite completes successfully. | PASS. |

## Independent verification

Executed from the sidecar branch at baseline `0c36566c`:

```bash
python3 -m pytest -q \
  tests/contract/test_openapi_artifact_and_client.py \
  tests/integration/test_intervention_workflow.py
```

Observed result: exit code 0 and `[100%]`. Repository `addopts = "-q"` makes
the explicit `-q` an effective `-qq`, so pytest suppresses its numeric summary.
Collection independently reports 17 contract cases and 21 integration cases
(38 total).

Warnings were non-blocking and pre-existing in the exercised surface:

- one `fastapi.testclient` / Starlette `httpx` deprecation warning;
- four uses of deprecated `HTTP_422_UNPROCESSABLE_ENTITY` constants in
  intervention route execution paths.

Additional checks:

```bash
git merge-base --is-ancestor 4c0f6069 HEAD
git merge-base --is-ancestor a7877621 HEAD
git status --short
```

Observed result: both ancestry checks succeeded and the worktree was clean
before this packet was created.

## Evidence-quality notes

### E1 — completion evidence links are not portable

`docs/evidence/completion/ODP-CAP-INTERVENTION-WORKSPACE-001/COMPLETION_EVIDENCE.md`
uses absolute `file:///tmp/pantheon-worker-worktrees/...` links tied to the
parent worker's temporary path. Those links will not resolve for GitHub
reviewers or other worktrees. This packet therefore uses repository-relative
paths and line anchors. If the parent owner refreshes the canonical completion
record, those links should be converted to normal relative Markdown links.

Disposition: non-blocking for the merged implementation; recommended evidence
hygiene correction.

### E2 — recorded test counts are historical, not a current suite inventory

The parent completion record quotes separate results of 17 contract and 16
integration tests. The current review baseline collects 17 and 21 respectively,
and the combined focused invocation is green. The older numbers remain useful
as historical output but should not be presented as the current collected
suite size.

Disposition: non-blocking; this packet supplies a fresh baseline result.

### E3 — “workspace” must not be inferred to mean rendered UI

The parent merge contains no frontend asset or browser/e2e test. The evidence
establishes the backend workflow and API contract. If canonical parent
acceptance separately required a rendered inbox/detail page, this packet does
not prove that requirement and the parent owner should route it to the owning
UI task rather than widening this support sidecar.

Disposition: reviewer scope check; no canonical acceptance text was modified.

### E4 — deprecations are follow-up quality work

The warning-only constants do not affect the tested 422 behavior today. A
narrow follow-up may replace them with `HTTP_422_UNPROCESSABLE_CONTENT` and
address the TestClient dependency warning, but these changes are outside this
support-only task.

Disposition: non-blocking follow-up candidate.

## Reviewer handoff

Antigravity2 should verify that:

1. the only sidecar diff is this support artifact;
2. the parent provenance hashes and merge ancestry are acceptable evidence;
3. the packet accurately limits its PASS statements to backend/API behavior;
4. E1–E4 are treated as evidence/scope notes, not unauthorized canonical edits;
5. any correction to the parent completion record or any UI follow-up remains
   owned by the parent owner.

Sidecar disposition: `READY_FOR_REVIEW`. Parent disposition: merged backend
delivery is supported by fresh focused verification; absorption of this packet
and any follow-up routing remains the parent owner's decision.

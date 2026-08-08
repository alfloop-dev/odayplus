# Review Packet: ODP-ENG-OPENAPI-CLIENT-001

- Sidecar task: `ODP-ENG-OPENAPI-CLIENT-001-SIDECAR-REVIEW`
- Parent task: `ODP-ENG-OPENAPI-CLIENT-001`
- Sidecar owner: `Codex`
- Assigned sidecar reviewer / parent owner: `Antigravity`
- Parent reviewer: `Antigravity2`
- Evidence captured: `2026-08-08` UTC
- Parent branch: `task/ODP-ENG-OPENAPI-CLIENT-001`
- Exact reviewed parent HEAD: `a42cf990507787174fda4534e831137bdcef3aa8`
- Parent merge-base at capture: `072b943bb54260fb13aca3cbe51e4dee0192604a`
- Scope: review packet and evidence summary only; no canonical truth, runtime,
  registry, OpenAPI artifact, or generated client was changed by this sidecar

## Executive disposition

The OpenAPI freshness gate, generated TypeScript emitter, breaking-change
classifier, package typecheck, web typecheck, and focused contract suite all
pass at the exact parent HEAD. The checked-in artifact is byte-reproducible
from the composed FastAPI application, the generated component/path surface is
fresh, and a deliberately mismatched generated-client target exits non-zero.

The packet is **ready for parent-owner absorption with evidence corrections**.
Before the parent is approved, its completion record should distinguish the
generated surface from the hand-written success-response DTO surface. The
current generator explicitly does not generate success response DTOs, and the
package source says they remain hand-written. The parent branch also had no
published PR at capture and was behind the then-current `origin/dev`; its owner
must compose it onto current `dev`, publish the task PR, and rerun the focused
checks before final closeout.

## Reviewed parent delivery

The parent task-scoped delta contains one commit:

`a42cf990 ODP-ENG-OPENAPI-CLIENT-001: deliver completion evidence`

Relative to its merge-base, that commit adds only:

| File | Role | Review observation |
| --- | --- | --- |
| `docs/evidence/completion/ODP-ENG-OPENAPI-CLIENT-001/implementation.md` | Implementation and acceptance summary | Correctly identifies the exporter, generated emitter, drift gate, error envelope, and web package boundary, but should state that most success-response DTOs are still hand-written rather than generated. |
| `docs/evidence/completion/ODP-ENG-OPENAPI-CLIENT-001/verification.md` | Command receipts | Reported outputs were reproduced at exact parent HEAD. |

No parent runtime or contract implementation is introduced by this task
commit. This review therefore verifies the already-present implementation at
the parent snapshot and the accuracy of the new completion evidence; it does
not attribute historical implementation commits to this task.

## Acceptance and evidence matrix

| Parent acceptance criterion | Disposition | Evidence and boundary |
| --- | --- | --- |
| Generated artifacts are reproducible | PASS | `export_openapi.py --check` matched the live composed schema; `generate_client.py --check` matched `openapi.json`; deterministic serialization is also covered by the focused suite. |
| Affected success and error paths are typed | PASS WITH PROVENANCE CAVEAT | `ErrorEnvelope` is generated and `OdpApiError` performs typed envelope extraction. Success response DTOs are TypeScript-typed, but the generator documents that the OpenAPI success responses are generally `additionalProperties: true`, so their useful DTOs remain hand-written in `src/index.ts`. |
| Drift check fails on mismatch | PASS | The full gate passed on the clean snapshot. The focused suite covers removal/type/required-field/enum/response breaking cases. A sidecar negative probe redirected the generated output check to a known non-generated repository file and observed `EXPECTED_MISMATCH_EXIT=1`. |
| Affected call sites use the generated contract | PASS WITH WORDING CAVEAT | 38 web TypeScript/TSX files import `@oday-plus/openapi-client`, and client construction is centralized through `createOdpApiClient` in the web API, operator network, and operator console surfaces. Imports may resolve either generated exports or hand-written response DTOs, so “package contract” is more accurate than saying every imported DTO is generated. |
| Focused type build and contract evidence is delivered | PASS | Both TypeScript projects exited 0 and the focused Python suite reported `17 passed`. The parent evidence documents the same commands. |

## Independent verification at exact parent HEAD

The parent worktree was clean before and after these read-only checks.

```bash
python3 scripts/openapi/check_drift.py --base-ref origin/dev
# API contract gate: PASS
# 0 additive, 0 approved breaking, 0 unapproved breaking

node /home/lupin/oday-plus/node_modules/typescript/bin/tsc \
  --noEmit -p packages/openapi-client/tsconfig.json
# exit 0

node /home/lupin/oday-plus/node_modules/typescript/bin/tsc \
  --noEmit -p apps/web/tsconfig.json
# exit 0

python3 -m pytest tests/contract/test_openapi_artifact_and_client.py
# 17 passed in 31.71s

git diff --check "$(git merge-base origin/dev HEAD)"..HEAD
# clean
```

Negative generated-client mismatch probe (no repository file was modified):

```bash
python3 -c '<redirect OUTPUT_PATH to the existing README.md; run main(["--check"])>'
# ERROR: README.md is stale ...
# EXPECTED_MISMATCH_EXIT=1
```

The same freshness gate and 17-test suite were also run successfully on the
sidecar base. TypeScript checks there could not start because that isolated
worktree has no `node_modules`; the exact-parent runs above used the shared
installed TypeScript binary and are the authoritative receipts.

## Sidecar base-advance refresh

Before the renewed reviewer handoff on `2026-08-08`, the sidecar fetched the
remote and composed its existing packet commit onto current `origin/dev` at
`50dda113403328a7aa11830e40d037a8ba1c5cb8`. The compose completed without a
conflict and preserved the original packet commit and reviewed parent SHA.

Post-compose verification:

```bash
git merge-base --is-ancestor origin/dev HEAD
# exit 0

git diff --name-status origin/dev...HEAD
# A support/sidecars/ODP-ENG-OPENAPI-CLIENT-001/ODP-ENG-OPENAPI-CLIENT-001-SIDECAR-REVIEW.md

git diff --check origin/dev...HEAD
# clean

test "$(git rev-parse task/ODP-ENG-OPENAPI-CLIENT-001)" = \
  a42cf990507787174fda4534e831137bdcef3aa8
# exit 0
```

This refresh verifies the publication base and scope of the sidecar packet. It
does not replace the exact-parent runtime and contract receipts above, and it
does not change the parent task or any canonical surface.

## Generated-versus-hand-written boundary

The reviewer should preserve this distinction when absorbing the packet:

| Surface | Provenance | Observed state |
| --- | --- | --- |
| `packages/openapi-client/openapi.json` | Exported from `create_app()` | Fresh and deterministic at parent HEAD. |
| `src/generated/types.ts` component schemas | Generated from `openapi.json` | Fresh; includes `ErrorEnvelope`, `ApiError`, request DTOs, and other declared component schemas. |
| `src/generated/types.ts` `API_PATHS` | Generated from versioned OpenAPI paths | Fresh; deprecated unversioned aliases are excluded. |
| `OdpApiError` | Hand-written in `src/index.ts`, typed against generated `ErrorEnvelope` | Extracts stable code, next action, correlation ID, and operator-facing detail. |
| Most success-response DTOs | Hand-written in `src/index.ts` | Typechecked and consumed through the package, but not generated because the server schemas generally expose open dictionaries for success responses. |

The package comment currently says hand-written response DTOs are quarantined
in `./handwritten`, but no such source file exists at the reviewed snapshot;
the DTOs are present directly in `src/index.ts`. This is a documentation
consistency issue, not a failing runtime or typecheck result.

## Reviewer attention points

1. **Evidence wording:** Update the parent implementation record so “affected
   paths are typed” does not imply all success response DTOs are OpenAPI-
   generated. Generated error/request/path coverage and hand-written success
   response typing are both real, but they have different provenance.
2. **Response-contract follow-up:** The generator itself records that route
   `response_model` declarations are needed before useful success response DTOs
   can be generated. Do not mark that broader migration complete through this
   evidence-only task.
3. **Branch publication:** At capture, the parent branch was local-only, had no
   GitHub PR, and its task commit was behind current `origin/dev`. Rebase or
   compose without dropping the evidence commit, rerun the exact checks, then
   hand the parent task to `Antigravity2` for its formal review.
4. **Negative gate coverage:** The focused suite directly tests the diff
   classifier's breaking cases and freshness equality. The sidecar probe adds
   an observed non-zero mismatch receipt; a future test may encode the CLI
   mismatch path to prevent control-flow regressions.

## Recommended handoff

- Sidecar disposition: **approve this support packet for absorption**.
- Handoff target: `Antigravity` (assigned sidecar reviewer and parent owner).
- Parent disposition: correct the evidence provenance wording, compose the
  evidence commit onto current `dev`, rerun the focused checks, publish the
  parent PR, and request formal review from `Antigravity2`.
- This sidecar does not authorize or perform any change to canonical truth or
  the parent implementation.

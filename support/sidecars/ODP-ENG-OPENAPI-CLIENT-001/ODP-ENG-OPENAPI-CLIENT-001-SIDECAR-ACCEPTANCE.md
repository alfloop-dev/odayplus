# Acceptance Packet and Dependency Map: ODP-ENG-OPENAPI-CLIENT-001

- Sidecar task: `ODP-ENG-OPENAPI-CLIENT-001-SIDECAR-ACCEPTANCE`
- Parent task: `ODP-ENG-OPENAPI-CLIENT-001`
- Helper kind: `acceptance_packet`
- Sidecar owner: `Codex9`
- Assigned sidecar reviewer / parent owner: `Antigravity4`
- Parent reviewer: `Codex`
- Evidence captured: `2026-08-08` UTC
- Parent branch: `task/ODP-ENG-OPENAPI-CLIENT-001`
- Parent review-gate SHA: `f4ab00f5db50bf15c0aa644c98e7bdc12270250d`
- Parent merge-base at capture: `07167d47819ff9ad7dc1731b625dfea64c946c99`
- Boundary: support packet only; no canonical truth, runtime, registry,
  OpenAPI artifact, generated client, or parent evidence was changed

## Disposition

The parent delivery has evidence for all five acceptance criteria and is ready
for focused review after three record-hygiene corrections listed below. The
artifact and client generation chain is reproducible, the package and web
typechecks are reported clean, and the focused contract suite reports 17
passing tests. The provenance boundary must remain explicit: request, error,
component, and path surfaces are generated, while most useful success-response
DTOs remain hand-written in `packages/openapi-client/src/index.ts`.

This packet recommends **absorption by the parent owner with evidence
corrections**. It does not approve the parent task or authorize a change to its
implementation.

## Parent snapshot and delivered scope

The parent review-gate SHA is two commits ahead of its captured merge-base:

```text
f4ab00f5 ODP-ENG-OPENAPI-CLIENT-001: clarify DTO provenance and fix comments
a42cf990 ODP-ENG-OPENAPI-CLIENT-001: deliver completion evidence
```

The parent delta contains only these files:

| File | Role at the review gate |
| --- | --- |
| `docs/evidence/completion/ODP-ENG-OPENAPI-CLIENT-001/implementation.md` | Parent scope, provenance, and acceptance summary |
| `docs/evidence/completion/ODP-ENG-OPENAPI-CLIENT-001/verification.md` | Parent command receipts |
| `packages/openapi-client/src/index.ts` | Comment correction identifying the actual hand-written DTO location |
| `scripts/openapi/generate_client.py` | Matching generator documentation correction |

The parent commits document and verify implementation already present at the
base; they do not introduce a new exporter, drift classifier, generated
artifact, generated emitter, or client call-site migration.

## Acceptance checklist

| Parent acceptance criterion | Packet disposition | Evidence and boundary |
| --- | --- | --- |
| Generated artifacts are reproducible | **PASS** | The parent verification record reports successful `export_openapi.py --check`, `generate_client.py --check`, and the combined drift gate. The focused suite compares both checked-in outputs byte-for-byte with freshly rendered content and checks deterministic schema serialization. |
| Affected success and error paths are typed | **PASS WITH PROVENANCE BOUNDARY** | Generated surfaces include component/request/error types and versioned `API_PATHS`; `OdpApiError` consumes the generated `ErrorEnvelope`. Most success-response DTOs are TypeScript-typed but hand-written in `src/index.ts` because the OpenAPI success responses generally expose open dictionaries. |
| Drift check fails on mismatch | **PASS WITH EVIDENCE CORRECTION** | The 17-test suite covers freshness equality and breaking-classifier cases including removed operations/responses, new required fields, type changes, and enum narrowing. The prior review sidecar recorded a separate generated-output mismatch probe exiting 1. The parent implementation record should not say the 17 tests themselves include that CLI exit-code probe. |
| Affected call sites use the generated contract | **PASS WITH WORDING BOUNDARY** | At capture, 38 TypeScript/TSX files under `apps/web` imported `@oday-plus/openapi-client`. The package re-exports generated surfaces and also owns hand-written success DTOs, so the demonstrated claim is package-contract adoption, not that every imported DTO is generated. |
| Focused type build and contract evidence is delivered | **PASS, SUBJECT TO RECORD CLEANUP** | The parent record reports package and web `tsc --noEmit` exits of 0, a passing full drift gate, and `17 passed`. The current parent delta is documentation/comment-only, but its evidence metadata and whitespace finding should be corrected before approval. |

## Dependency map

```text
FastAPI composed app: apps.api.oday_api.main.create_app()
                         |
                         v
scripts/openapi/export_openapi.py
                         |
                         v
packages/openapi-client/openapi.json
                         |
                         v
scripts/openapi/generate_client.py
                         |
                         v
packages/openapi-client/src/generated/types.ts
                         |
                         +--------------------------+
                         |                          |
                         v                          v
packages/openapi-client/src/index.ts       scripts/openapi/check_drift.py
  generated re-exports +                    freshness + base diff
  hand-written success DTOs                 + approved-break registry
                         |                          |
                         v                          v
38 apps/web TS/TSX consumers             CI/reviewer acceptance gate
```

| Surface | Relationship | Acceptance impact |
| --- | --- | --- |
| `apps.api.oday_api.main.create_app()` | Upstream schema authority | Export freshness is meaningful only against the composed application, not a hand-authored schema fragment. |
| `packages/openapi-client/openapi.json` | Versioned generated artifact | Input to the TypeScript emitter and baseline for API diff classification. |
| `scripts/openapi/approved_breaking_changes.json` | Reviewed escape hatch | An intentional breaking signature needs a reason, task ID, and reviewer-visible diff. |
| `packages/openapi-client/src/generated/types.ts` | Generated TypeScript surface | Must remain byte-reproducible from the OpenAPI artifact and marked `DO NOT EDIT`. |
| `packages/openapi-client/src/index.ts` | Public package boundary | Re-exports generated types and contains the still-hand-written success-response DTOs and client behavior. |
| `apps/web` | Downstream package consumer | Typecheck demonstrates compatibility across the current web call sites. |
| Route `response_model` follow-up | Deferred upstream improvement | Required before useful success-response DTOs can be generated safely; outside this parent and sidecar scope. |

## Generated versus hand-written boundary

| Surface | Provenance | Current acceptance meaning |
| --- | --- | --- |
| `openapi.json` | Exported from the composed FastAPI app | Reproducible schema artifact |
| `src/generated/types.ts` component/request/error types | Generated from `openapi.json` | Reproducible compile-time contract |
| `src/generated/types.ts` `API_PATHS` | Generated from versioned OpenAPI paths | Versioned operation map; deprecated aliases excluded |
| `OdpApiError` | Hand-written, typed with generated `ErrorEnvelope` | Typed error extraction at the package boundary |
| Most success-response DTOs | Hand-written directly in `src/index.ts` | Typed for consumers, but not proof of generated response schemas |

## Parent-owner actions before approval

1. Remove the extra blank line at the end of the parent implementation record;
   `git diff --check origin/dev...f4ab00f5` currently reports
   `implementation.md:41: new blank line at EOF`.
2. Update the verification record header from the superseded
   `Antigravity` / `Antigravity2` assignment to the live parent assignment,
   `Antigravity4` / `Codex`.
3. Reword the drift-mismatch evidence in the implementation record. The 17
   tests exercise deterministic freshness and classifier failure cases, but do
   not invoke a deliberately stale generated-output target and assert the CLI
   exit code. Cite the separate sidecar negative probe or add a focused test.
4. Preserve the generated-versus-hand-written distinction when summarizing
   call-site adoption and typed success paths.

## Focused reviewer verification

Run these commands on the exact parent review SHA after applying any evidence
fix commit:

```bash
python3 scripts/openapi/check_drift.py --base-ref origin/dev
node ./node_modules/typescript/bin/tsc \
  --noEmit -p packages/openapi-client/tsconfig.json
node ./node_modules/typescript/bin/tsc \
  --noEmit -p apps/web/tsconfig.json
python3 -m pytest tests/contract/test_openapi_artifact_and_client.py
git diff --check "$(git merge-base origin/dev HEAD)"..HEAD
```

Expected acceptance receipts are a passing three-stage API contract gate, two
zero-exit typechecks, `17 passed`, and a clean whitespace check. If the parent
continues to claim direct CLI mismatch-exit coverage, also run or encode a
negative probe that deliberately points the generated-client check at stale
content and observes a non-zero exit.

## Sidecar scope audit and handoff

- Sidecar artifact:
  `support/sidecars/ODP-ENG-OPENAPI-CLIENT-001/ODP-ENG-OPENAPI-CLIENT-001-SIDECAR-ACCEPTANCE.md`
- Sidecar-owned layer: acceptance checklist, dependency map, provenance
  boundary, and reviewer handoff actions.
- Not changed: parent files, canonical documents, runtime, package behavior,
  generated files, contract registry, or governance policy.
- Handoff target: `Antigravity4`, the assigned sidecar reviewer and parent
  owner.
- Recommended parent flow: absorb the packet, correct the three evidence
  findings, rerun the focused commands at the new review SHA, and return the
  parent task to `Codex` for formal review.

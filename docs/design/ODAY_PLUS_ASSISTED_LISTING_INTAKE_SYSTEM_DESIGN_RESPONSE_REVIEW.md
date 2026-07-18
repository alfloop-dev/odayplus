---
doc_id: ODP-SD-INTAKE-REVIEW-004
title: ODay Plus Assisted Listing Intake System Design Response Review
review_version: 0.2.2
response_version: 0.2.1
status: changes-requested
decision: CHANGES_REQUESTED
owner: Independent Architecture Review
reviewer: Codex
reviewers: Product / Security / Privacy / Data / Platform-SRE / Expansion Engineering / QA
reviews: ODP-SD-INTAKE-001
responds_to: ODP-SD-INTAKE-ALIGN-001
reviewed_commit: 3afe385bd4e5fe2fba2001b1bc5da1b932301c1b
base_branch: dev
base_commit: e2ef2156375c733747d968346fd85ca54cc751c1
artifact_manifest: docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_REVIEW_MANIFEST.yaml
supersedes_review_target: d75fe8ab13d69f039c2cabe237d2401face8418b
updated_at: 2026-07-18
---

# ODay Plus Assisted Listing Intake System Design Response Review

## 1. Review Decision

`CHANGES_REQUESTED`

This is a fresh review of exact response commit:

```text
3afe385bd4e5fe2fba2001b1bc5da1b932301c1b
```

The prior normative-stack contradiction is corrected in the response,
correction pack, and review manifest. The effective OpenAPI and PostgreSQL
schema stack also pass structural validation. Approval is nevertheless blocked
because the exact-head cross-contract validator returns `FAIL`, while the
GitHub Actions workflow masks that non-zero exit and reports the Design
Contract Gate as successful.

## 2. Target and Lineage Verification

| Check | Result | Evidence |
|---|---|---|
| PR #319 response head | PASS | `3afe385bd4e5fe2fba2001b1bc5da1b932301c1b` |
| PR #319 base | PASS | `dev` at `e2ef2156375c733747d968346fd85ca54cc751c1` |
| PR #319 merge state | PASS | Draft, open, not merged |
| PR #323 review base | PASS | `agent/assisted-listing-intake-system-design` at the reviewed commit |
| Review ancestry | PASS | `91d7d8e9...` is one direct descendant commit of the reviewed commit |
| Review merge safety | PASS | PR #323 cannot merge independently to `dev` |

The IDE copy of `ODP-SD-INTAKE-REVIEW-001` remains historical only and was not
used as review evidence.

## 3. Corrected Contract Assessment

| Review area | Result | Evidence |
|---|---|---|
| Normative artifact membership | PASS | Response, correction pack, and manifest parse to the same list |
| Precedence | PASS | All three representations match the validator canonical value |
| Schema apply order | PASS | Base, `0002`, `0003`, `0004` |
| OpenAPI bundle order | PASS | Base plus all five overlays |
| Event apply order | PASS | Base catalog, v1.1 addendum, payload registry |
| Main response wording | PASS | Six-artifact OpenAPI bundle; no stale three-file wording |
| Correction-pack wording | PASS | Complete four-file schema stack; `0002` is not final |
| Manifest-driven OpenAPI builder | PASS | Default builder execution reads the complete manifest order |
| Effective OpenAPI validation | PASS | `openapi-spec-validator`; Redocly 0 errors / 0 warnings |
| PostgreSQL 16 schema stack | PASS | All four artifacts apply successfully |
| Tenant RLS and lineage catalog validation | PASS | FORCE RLS, tenant policies, composite lineage constraints |
| Commit-bound cross-contract validator | FAIL | `ci_uses_registered_stacks` reports the base OpenAPI as missing |
| Design Contract Gate integrity | FAIL | Workflow run is green although its validator artifact says `status: FAIL` |

## 4. Blocking Finding

### SDR-009 - Cross-contract validation is false-green

Affected contract and release decisions: `SDI-004`, `SDI-014`, `SDI-019`, and
`SDI-024`.

The formal review command required by the response and review manifest was run
at the exact reviewed commit. It exited `1` with:

```json
{
  "status": "FAIL",
  "findings": [
    {
      "check": "ci_uses_registered_stacks",
      "ok": false,
      "detail": "builder_ok=True; missing=['docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1.yaml']"
    }
  ]
}
```

The same failure is present in GitHub Actions run `29619947760`. The run still
concludes `success` for two independent reasons:

1. `.github/workflows/assisted-intake-design-validation.yml`, lines 73-78,
   pipes the validator into `tee` without `set -o pipefail` or an explicit
   `PIPESTATUS` check. Bash therefore returns the successful `tee` status and
   discards the validator exit code.
2. The final structural-gate step checks OpenAPI, Redocly, schema apply, and
   schema validation outcomes, but does not check the cross-contract step.

There is also a contract mismatch inside the check itself. The OpenAPI builder
correctly obtains the base document and overlay order from the manifest, but
`validate_assisted_listing_intake_design.py` only accepts each path when its
literal text appears in the workflow or builder source. The base path lives in
the manifest, so a valid manifest-driven implementation is incorrectly marked
missing.

Consequences:

- the claimed exact-head cross-contract gate did not pass;
- future register/order regressions can produce a green workflow despite a
  validator failure;
- run `29619947760` cannot be used as approval evidence.

## 5. Required Corrections

1. Make `ci_uses_registered_stacks` validate the resolved manifest-driven
   builder input rather than searching for every path literal in source text.
2. Run the OpenAPI builder from its manifest default in CI, or explicitly
   compare supplied arguments with the manifest order.
3. Preserve the validator exit code with `set -o pipefail`, `PIPESTATUS[0]`, or
   by writing output before a separate `cat` step.
4. Give the cross-contract step an `id` and include its outcome in the final
   `Enforce all structural gates` step.
5. Rerun the formal review command and GitHub workflow at the new exact head;
   both the process exit code and uploaded JSON must report `PASS`.
6. Supersede PR #323 with a new commit-bound review after the response head
   changes, or rebase the review artifact so ancestry remains valid.

## 6. Final Decision

```text
Decision: CHANGES_REQUESTED
Reviewer: Codex
Reviewed commit: 3afe385bd4e5fe2fba2001b1bc5da1b932301c1b
Decision date: 2026-07-18
Required change: fix the false-green cross-contract gate and resubmit an exact-head review
```

PR #319 must remain Draft and must not merge on the current evidence.

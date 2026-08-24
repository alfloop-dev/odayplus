# Release Gate 0-6 Registry

`docs/evidence/gates/RELEASE_GATE_REGISTRY.json` is the authoritative,
machine-readable state of the seven ODay Plus release gates. It is validated by
`delivery_toolchain/e2e/check_release_gate_registry.py` and is the file the final gate
audit (`ODP-PLAN-FINAL-GATE-AUDIT-001`) reads instead of re-deriving gate state
from prose. The registry is schema `2.0.0`; the v1 registry is not accepted by
the validator.

`docs/evidence/gates/RELEASE_MANIFEST.json` is the immutable release identity.
It binds the exact candidate SHA, every component image to an `@sha256:` digest,
the migration/data-contract/source-policy digests, SBOM/signature references,
and its own canonical `manifest_digest`. Deployments carry this manifest by
digest; they do not rebuild it per environment.

`docs/release/RELEASE_GATE_CHECKLIST.md` stays the human-facing narrative and
per-check worksheet. Where the two disagree, the registry is the state of
record and the checklist is the explanation.

## Current state

**NO-GO.** All seven gates are `blocked`, none carries a receipt, and
`release.decision` is `no-go` against candidate SHA
`e496be62c47c45d758681b8a4d3abfae16f1c96d`. Deterministic product-E2E readiness
(`docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md`) is not release readiness. The
current state is `candidate-built` in `dev`, with admission target `dev`.

## Gate 0-6

The seven gates collapse the eleven checklist rows in
`docs/release/RELEASE_GATE_CHECKLIST.md` and the nine mandatory engineering
gates in `docs/architecture/ODAY_PLUS_EXECUTION_BASELINE.md` into the
release-ordered sequence the plan refers to as Gate 0-6.

| Gate | Name | Collapses checklist rows |
|---|---|---|
| `gate-0` | Code Gate | Code Gate |
| `gate-1` | Contract Gate | Contract Gate |
| `gate-2` | Data Gate | Data Gate |
| `gate-3` | Model and Solver Gate | Model Gate, Solver Gate |
| `gate-4` | Security and Privacy Gate | Security Gate |
| `gate-5` | E2E, Performance and UAT Gate | E2E Gate, Performance Gate, UAT Gate |
| `gate-6` | Ops, Release and Audit Gate | Ops Gate, Audit Gate |

Gate owners and reviewers are seeded from the owners of the corresponding P0
tasks in program `ODP-PLAN-GAP-CLOSEOUT-2026-07-30`. Human/Ops confirms or
reassigns them at the final gate audit.

## Running the validator

```bash
# Integrity check. Exits 0 for a well-formed registry, including a NO-GO one.
python3 delivery_toolchain/e2e/check_release_gate_registry.py

# Release check. Exits non-zero unless every gate is cleared and the recorded
# decision is 'go'. This is the form a release promotion must call.
python3 delivery_toolchain/e2e/check_release_gate_registry.py --require-go

# Bind the check to the commit actually being released.
python3 delivery_toolchain/e2e/check_release_gate_registry.py \
  --expected-sha "$(gh pr view <pr> --json headRefOid --jq .headRefOid)"

# Machine-readable report for downstream audits.
python3 delivery_toolchain/e2e/check_release_gate_registry.py --json
```

`make product-e2e-gate` runs the integrity check, so registry drift fails CI
before it reaches a release decision.

## Fail-closed rules

The validator exits non-zero when any of these is true:

1. The registry file is missing, unparseable, or not a JSON object.
2. The registry is not schema `2.0.0`, or its explicit v1 migration record is
   missing/incomplete.
3. The manifest is missing, not digest-pinned, bound to a different candidate,
   or its canonical `manifest_digest` does not match the file contents.
4. A release or gate has a stage/environment/admission target combination that
   is not in the staged admission contract.
5. A required top-level, release, gate, evidence, receipt, deviation, or
   sign-off field is absent or empty.
6. `release.candidate_sha`, `gates[].release_sha`, or a receipt's
   `release_sha` is not an exact 40-character lowercase git SHA.
7. A gate's `release_sha` differs from `release.candidate_sha` — a new
   candidate re-opens every gate rather than inheriting old attestations.
8. A receipt names a SHA other than the candidate. Stale receipts are not
   evidence.
6. Evidence of kind `doc`, `script`, or `test`, or any receipt `artifact`,
   points at a repository path that does not exist.
7. The gate list is not exactly `gate-0` … `gate-6`, in order, without
   duplicates.
8. A gate whose status is `not-started`, `in-progress`, `blocked`, or `failed`
   names no blocker.
9. A gate whose status is `passed` or `passed-with-deviation` has no evidence,
   has no passing receipt bound to the candidate SHA, carries a failing
   receipt, or still carries blockers.
10. A `passed-with-deviation` gate has no `deviation` object with description,
    approver, and `review_by` date; or a `not-applicable` gate has no
    justification, or still carries blockers.
11. `release.decision` is `go` while any gate is not cleared, or without a
    `release.human_signoff` approver and date.
12. `--expected-sha` was passed and does not match `release.candidate_sha`.
13. `--require-go` was passed and the release is not in a cleared GO state.

Note the asymmetry: the validator never objects to a registry that is *more*
conservative than its evidence. It only objects to one that claims more than it
proves.

## Schema

```jsonc
{
  "schema_version": "2.0.0",
  "registry_id": "ODP-RELEASE-GATE-REGISTRY",
  "task_id": "<task that last changed the registry>",
  "generated_at": "YYYY-MM-DD",
  "description": "...",
  "migration": {
    "from_schema_version": "1.0.0",
    "source_registry_id": "<legacy registry id>",
    "migrated_at": "RFC3339 timestamp",
    "strategy": "legacy-gates-to-staged-registry",
    "re_attestation_required": true
  },
  "release": {
    "candidate_sha": "<40-char lowercase git SHA>",
    "candidate_ref": "<branch, tag, or PR head the SHA came from>",
    "manifest_ref": "docs/evidence/gates/RELEASE_MANIFEST.json",
    "manifest_digest": "sha256:<64 lowercase hex>",
    "stage": "candidate-built | dev-verified | staging-verified | prod-admitted | prod-switched | release-complete",
    "environment": "dev | staging | production",
    "admission_target": "dev | staging | production | production-green | production-switch | release-complete",
    "decision": "go | no-go",
    "decision_owner": "<who owns the decision>",
    "decision_date": "YYYY-MM-DD",
    "decision_note": "<why the decision reads the way it does>",
    "human_signoff": {            // required only when decision is "go"
      "approver": "Human/Ops",
      "date": "YYYY-MM-DD"
    }
  },
  "gates": [
    {
      "id": "gate-0",             // gate-0 … gate-6, in order
      "index": 0,                 // must equal the id suffix
      "name": "Code Gate",
      "scope": "<what this gate covers>",
      "owner": "<accountable agent or role>",
      "reviewer": "<must differ from owner>",
      "status": "not-started | in-progress | blocked | failed | passed | passed-with-deviation | not-applicable",
      "status_date": "YYYY-MM-DD",
      "release_sha": "<must equal release.candidate_sha>",
      "stage": "candidate-built | dev-verified | staging-verified | prod-admitted | prod-switched | release-complete",
      "environment": "dev | staging | production",
      "admission_target": "<stage contract target>",
      "required_checks": ["<what must be true for this gate to pass>"],
      "evidence": [
        {
          "kind": "doc | script | test | ci-check | command",
          "ref": "<repo path for doc/script/test; identifier otherwise>",
          "description": "<what this evidence shows>"
        }
      ],
      "receipts": [
        {
          "receipt_id": "<stable id>",
          "release_sha": "<must equal release.candidate_sha>",
          "result": "pass | fail",
          "recorded_at": "<ISO 8601 timestamp>",
          "recorded_by": "<who ran it>",
          "artifact": "<repo path to the receipt artifact>"
        }
      ],
      "blockers": ["<required while the gate is open, forbidden once cleared>"],
      "deviation": {              // required only for passed-with-deviation
        "description": "...",
        "approver": "...",
        "review_by": "YYYY-MM-DD"
      },
      "justification": "..."      // required only for not-applicable
    }
  ]
}
```

## Updating the registry

1. Land the work that produces the evidence, and write the receipt artifact
   into the repository.
2. Add the receipt to the gate with the exact release SHA it was produced
   against, then move the gate's status and clear its blockers.
3. Run `python3 delivery_toolchain/e2e/check_release_gate_registry.py` and
   `python3 -m pytest -q tests/e2e/test_release_gate_registry.py`.
4. Only Human/Ops moves `release.decision` to `go`, and only with a recorded
   `human_signoff`.

When the release candidate moves to a new commit, update
`release.candidate_sha` and every gate's `release_sha`. This deliberately
invalidates all existing receipts: gates are re-attested against the commit
that actually ships, not inherited from a commit that did not. The manifest
must also be regenerated, with a new `manifest_digest`, and all component
digests must be re-recorded. A registry mutation that changes the manifest
file without changing its digest fails closed.

## Staged admission contract

Each gate records the boundary at which its evidence applies. The validator
enforces these pairs:

| Stage | Environment | Admission target |
|---|---|---|
| `candidate-built` | `dev` | `dev` |
| `dev-verified` | `dev` | `staging` |
| `staging-verified` | `staging` | `production` |
| `prod-admitted` | `production` | `production-green` |
| `prod-switched` | `production` | `production-switch` |
| `release-complete` | `production` | `release-complete` |

The `staging` admission target is the `dev-verified` boundary. It does not
require staging receipts, because those receipts can only be produced after
the ephemeral environment exists. Staging verification is a later
`staging-verified` boundary used to request production approval.

## Legacy migration

Use `delivery_toolchain/release/migrate_gate_registry.py` to migrate a v1
registry. It requires a valid manifest for the same candidate SHA, preserves
all old statuses and receipts, adds the stage metadata, and marks
`re_attestation_required: true`. It never fabricates a receipt or promotes a
gate. A missing or mismatched manifest aborts the migration.

```bash
python3 delivery_toolchain/release/migrate_gate_registry.py \
  --registry legacy/RELEASE_GATE_REGISTRY.json \
  --manifest docs/evidence/gates/RELEASE_MANIFEST.json \
  --output docs/evidence/gates/RELEASE_GATE_REGISTRY.json
```

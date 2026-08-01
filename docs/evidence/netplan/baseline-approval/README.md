# NetPlan management baseline approval intake

Task: `ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001`

Status: **BLOCKED — authoritative Human/Ops evidence is not present**

NetPlan remains `BUSINESS_UAT_UNVERIFIED` and `GOVERNED_DISABLED`. This
directory is an intake boundary, not an approval record. Nothing authored by
an AI worker, committed to this repository, or supplied by a caller may be
used as management authentication.

## Why this is blocked

The task branch does not contain an authentic management baseline, the source
snapshots behind that baseline, or an authoritative management approval-system
readback. Therefore the baseline content hash, solver problem hash, source
hashes, approving principal, approval reference, and receipt integrity value
cannot be truthfully filled in by the worker.

Technical fixture values in
`docs/evidence/models/ODP-PLAN-NETPLAN-ACCEPTANCE-001.md` are deliberately not
management approval evidence. A locally constructed JSON receipt or a
self-consistent receipt hash is also insufficient.

## Required Human/Ops submission

The accountable management owner must provide all of the following as one
immutable decision set:

1. An exact baseline snapshot containing the scenario and entity domain,
   action selected for every entity, policy version, all constraints,
   objective definition, risk penalty, alternative limit, scope, release, and
   source snapshot identifiers plus source artifact hashes.
2. The canonical baseline content hash recomputed with
   `ManagementBaselineInput.compute_canonical_hash()` and the exact constraints
   and risk penalty from the snapshot.
3. The solver problem hash recomputed with
   `compute_solver_problem_hash()` over the exact option domain, constraints,
   risk penalty, and alternative limit.
4. A management approval-system readback resolved by a fixed receipt ID. The
   readback must bind an authenticated principal ID and role, active decision,
   approval reference, issue and expiry timestamps in strict UTC, scenario,
   baseline, scope, release, policy, actions/domain, source snapshot IDs and
   hashes, baseline content hash, solver problem hash, and source-issued
   receipt integrity proof.
5. The authority query/audit reference needed by the reviewer to resolve the
   same receipt independently from the configured management approval system.

The machine-readable intake checklist is in
`APPROVAL_INTAKE_REQUIREMENTS.json`.

## Reviewer replay

Human/Ops must resolve the receipt from the authority system rather than trust
the submitted actor string or local file. The reviewer then recomputes every
baseline/problem/source hash from the submitted immutable source artifacts and
replays at least these mutations:

- AI/caller principal, actor-string role spoof, wildcard or arbitrary receipt
  ID, and unresolved receipt;
- missing, malformed, non-UTC, future-issued, expired, or inverted issue/expiry
  window;
- scenario, baseline, scope, release, policy, actions/domain, constraints,
  objective, risk penalty, option domain, source snapshot, or hash mismatch;
- inactive decision, blank approval reference, and tampered receipt integrity
  proof.

Any missing evidence or mutation acceptance keeps the gate unverified and
must not configure the production `FixedManagementApprovalReceiptVerifier`.

## Prohibited substitutions

- Do not generate an `APPROVED` receipt in a repository script.
- Do not infer a named approver or source-system identity from free-form text.
- Do not use the technical acceptance fixture as the management baseline.
- Do not hash mutable caller content and treat that same hash as authority.
- Do not open the release gate from a PR, local JSON file, self-hash, test
  attestation, or AI-authored audit report.

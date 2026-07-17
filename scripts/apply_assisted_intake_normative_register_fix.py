#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ALIGNMENT = "docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SYSTEM_DESIGN_ALIGNMENT_REQUEST.md"
RESPONSE = "docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SYSTEM_DESIGN_RESPONSE.md"
CORRECTION = "docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V021_CROSS_CONTRACT_CORRECTIONS.md"
STATE = "docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_STATE_CONTRACTS.md"
AUTH = "docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_AUTHORIZATION_MATRIX.md"
MANIFEST = "docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_REVIEW_MANIFEST.yaml"
SCHEMA_BASE = "docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA.sql"
SCHEMA_0002 = "docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0002_CONSISTENCY_PATCH.sql"
SCHEMA_0003 = "docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0003_PROMOTION_STATE_PATCH.sql"
SCHEMA_0004 = "docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0004_TENANT_RLS_LINEAGE_PATCH.sql"
API_BASE = "docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1.yaml"
API_1001 = "docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_0_1_PRELUDE_OVERLAY.yaml"
API_110 = "docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_OVERLAY.yaml"
API_111 = "docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_1_CONSISTENCY_OVERLAY.yaml"
API_112 = "docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_2_LINT_OVERLAY.yaml"
API_113 = "docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_3_REDOCLY_OVERLAY.yaml"
EVENT_BASE = "docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1.yaml"
EVENT_ADDENDUM = "docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1_1_ADDENDUM.yaml"
EVENT_PAYLOADS = "docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENT_PAYLOAD_SCHEMAS_V1.yaml"
RELIABILITY = "docs/operations/ODAY_PLUS_ASSISTED_LISTING_INTAKE_RELIABILITY_PRIVACY_CONTRACT.md"
MIGRATION = "docs/operations/ODAY_PLUS_ASSISTED_LISTING_INTAKE_MIGRATION_ROLLOUT_RUNBOOK.md"
DESIGN_VALIDATOR = "scripts/validate_assisted_listing_intake_design.py"
OPENAPI_BUILDER = "scripts/build_validate_assisted_listing_intake_openapi.py"
SCHEMA_VALIDATOR = "scripts/validate_assisted_listing_intake_schema.sql"
WORKFLOW = ".github/workflows/assisted-intake-design-validation.yml"

NORMATIVE_ARTIFACTS = [
    ALIGNMENT, RESPONSE, CORRECTION, STATE, AUTH, MANIFEST,
    SCHEMA_BASE, SCHEMA_0002, SCHEMA_0003, SCHEMA_0004,
    API_BASE, API_1001, API_110, API_111, API_112, API_113,
    EVENT_BASE, EVENT_ADDENDUM, EVENT_PAYLOADS,
    RELIABILITY, MIGRATION,
    DESIGN_VALIDATOR, OPENAPI_BUILDER, SCHEMA_VALIDATOR, WORKFLOW,
]
SCHEMA_ORDER = [SCHEMA_BASE, SCHEMA_0002, SCHEMA_0003, SCHEMA_0004]
OPENAPI_ORDER = [API_BASE, API_1001, API_110, API_111, API_112, API_113]
EVENT_ORDER = [EVENT_BASE, EVENT_ADDENDUM, EVENT_PAYLOADS]
PRECEDENCE = [
    "alignment_request",
    "consolidated_response",
    "review_manifest_for_artifact_register_and_apply_order",
    "correction_pack_for_explicit_textual_corrections",
    "machine_readable_stacks_in_manifest_order_later_artifact_overrides_earlier",
    "unchanged_base_artifact_clauses",
    "runtime_implementation",
]
REGISTER = {
    "manifest_path": MANIFEST,
    "normative_artifacts": NORMATIVE_ARTIFACTS,
    "precedence": PRECEDENCE,
    "schema_apply_order": SCHEMA_ORDER,
    "openapi_bundle_order": OPENAPI_ORDER,
    "event_apply_order": EVENT_ORDER,
}
REGISTER_JSON = json.dumps(REGISTER, ensure_ascii=False, indent=2)
REGISTER_BLOCK = (
    "<!-- normative-register:start -->\n"
    "```json\n" + REGISTER_JSON + "\n```\n"
    "<!-- normative-register:end -->"
)


def replace_section(text: str, start_heading: str, end_heading: str, replacement: str) -> str:
    start = text.index(start_heading)
    end = text.index(end_heading, start)
    return text[:start] + replacement.rstrip() + "\n\n" + text[end:]


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.write_text(content.rstrip() + "\n", encoding="utf-8")


main = (ROOT / RESPONSE).read_text(encoding="utf-8")
main_section = f"""## 2. Normative Artifact Register, Precedence, and Apply Order

The review manifest is the sole authority for package membership and machine-readable apply order. This response and the correction pack intentionally duplicate the same register so humans cannot follow a stale subset. The validator requires all three representations to be byte-for-byte equivalent after JSON/YAML parsing.

{REGISTER_BLOCK}

Precedence semantics:

1. The alignment request defines the required product and governance boundary.
2. This consolidated response defines the selected architecture and SDI-001 through SDI-024 decisions.
3. The review manifest is authoritative for the complete artifact register and schema/OpenAPI/event apply order.
4. The correction pack overrides only clauses it explicitly identifies as corrected.
5. Within each machine-readable stack, artifacts are applied in manifest order and each later patch/overlay overrides earlier conflicting content.
6. Unchanged base-artifact clauses remain effective.
7. Runtime implementation is evidence only and has the lowest precedence.

Any difference among this register, the correction-pack register, or the review manifest is a P0 validation failure. No review decision or engineering handoff is valid while they differ. All artifacts remain `proposed` until approvals in section 12 are recorded.
"""
main = replace_section(main, "## 2. Normative Artifact Register and Precedence", "## 3. Canonical Domain, Identity, and Ownership", main_section)
main = main.replace(
    "The effective API is the three-file bundle listed in section 2. Client generation and contract tests must apply overlays in order.",
    "The effective API is the six-artifact OpenAPI bundle listed in section 2 and the review manifest. Client generation, examples, lint, and contract tests must apply all five overlays to the base in the exact registered order.",
)
write(RESPONSE, main)

correction = (ROOT / CORRECTION).read_text(encoding="utf-8")
correction_section_1 = f"""## 1. Purpose, normative register, and precedence

This document records explicit textual corrections to `ODP-SD-INTAKE-001`. It does not maintain an independent or partial artifact list. The review manifest is the sole authority for package membership and apply order, and the identical machine-readable register below is validated against both the manifest and the consolidated response.

{REGISTER_BLOCK}

Precedence semantics are identical to the consolidated response:

1. alignment request;
2. consolidated response;
3. review manifest for artifact membership and apply order;
4. this correction pack for clauses explicitly corrected here;
5. machine-readable stacks in manifest order, with later artifacts overriding earlier conflicts;
6. unchanged base-artifact clauses;
7. runtime implementation.

No runtime task may implement a contradictory earlier clause. A mismatch among the response, this correction pack, and the manifest fails the pre-review gate.
"""
correction = replace_section(correction, "## 1. Purpose and precedence", "## 2. Confirmed blockers in v0.2.0", correction_section_1)
correction = correction.replace(
    "| `CCR-007` | SQL foreign keys did not consistently enforce tenant equality; RLS was enabled on only a subset of tenant tables. | Apply schema consistency patch `0002`; see §8. |",
    "| `CCR-007` | SQL foreign keys did not consistently enforce tenant equality; RLS was enabled on only a subset of tenant tables. | Apply the complete four-file schema stack through patch `0004`; see §8. |",
)
correction = correction.replace(
    "The machine-readable overlay is `docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_OVERLAY.yaml` and must be bundled with the base OpenAPI before client generation or contract testing.",
    "The machine-readable API contract is the complete six-artifact OpenAPI bundle in the normative register. The v1.1 command overlay is only one member of that stack; all registered overlays must be applied before client generation, examples, lint, or contract testing.",
)
correction_section_8 = f"""## 8. Persistence corrections and canonical schema stack

The canonical relational contract is the complete four-file schema stack, in this exact order:

1. `{SCHEMA_BASE}`
2. `{SCHEMA_0002}`
3. `{SCHEMA_0003}`
4. `{SCHEMA_0004}`

Patch responsibilities:

- `0002` corrects URL/snapshot uniqueness, adds assignment/SLA/pause history, promotion migration lineage, reconciliation findings, and the first tenant-qualified composite constraints.
- `0003` makes promotion `PENDING_REVIEW` schema-valid.
- `0004` completes tenant-qualified current-pointer and lineage foreign keys and enforces `ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL SECURITY`, and the fail-closed `tenant_isolation` policy on every tenant-bearing contract table.

`0002` alone is not the canonical relational patch and must never be applied as the final schema. Production migration must apply all four artifacts, reconcile existing rows, validate every `NOT VALID` constraint, and pass PostgreSQL catalog checks before authoritative writes are enabled. Cross-tenant or orphaned rows become blocking reconciliation findings and are never silently rewritten.
"""
correction = replace_section(correction, "## 8. Persistence corrections", "## 9. Migration correction", correction_section_8)
correction = correction.replace(
    "2. Base OpenAPI plus overlay bundles and validates.",
    "2. The complete six-artifact OpenAPI stack in the normative register composes and validates with zero Redocly errors or warnings.",
)
correction = correction.replace(
    "3. Schema baseline plus patch parses and all tenant-isolation tests pass.",
    "3. The complete four-file schema stack in the normative register applies to PostgreSQL 16 and all FORCE RLS, tenant-policy, and tenant-lineage catalog tests pass.",
)
write(CORRECTION, correction)


def yaml_list(name: str, values: list[str]) -> str:
    return f"{name}:\n" + "\n".join(f"  - {value}" for value in values)

manifest = f"""manifest_id: ODP-SD-INTAKE-REVIEW-MANIFEST-001
response_id: ODP-SD-INTAKE-001
effective_response_version: 0.2.1
status: proposed
base_branch: dev
target_branch: agent/assisted-listing-intake-system-design
updated_at: 2026-07-17

review_target_policy:
  rule: reviewer must record the exact current PR head SHA at review start
  required_front_matter:
    - reviewed_commit
    - response_version
    - base_branch
    - base_commit
    - artifact_manifest
  ancestry_rule: formal review commit must be a descendant of reviewed_commit
  merge_safety_rule: a review artifact must not be mergeable to dev without the reviewed artifacts
  stale_condition: reviewed_commit != current_pr_head
  stale_result: STALE_REVIEW_TARGET
  prohibited_results_for_stale_target: [APPROVED, APPROVED_WITH_CONDITIONS, CHANGES_REQUESTED]

historical_reviews:
  - review_id: ODP-SD-INTAKE-REVIEW-001
    reviewed_commit: ffe14c77f7d4f1ae97d301db3a8177cd3effeed6
    response_version: 0.1.0
    status_for_current_head: HISTORICAL_ONLY
  - review_id: ODP-SD-INTAKE-REVIEW-002
    reviewed_commit: a5a9a2be88e20ffff8719eaaba3c7eba263abc31
    response_version: 0.2.1
    status_for_current_head: INVALID_REVIEW_LINEAGE
    invalid_reason: review commit was not a descendant of the reviewed commit; PR 320 closed
  - review_id: ODP-SD-INTAKE-REVIEW-003
    reviewed_commit: d75fe8ab13d69f039c2cabe237d2401face8418b
    response_version: 0.2.1
    status_for_current_head: CHANGES_REQUESTED
    invalid_reason: normative response/correction/manifest register and apply order diverged; PR 322 closed

{yaml_list('normative_precedence', PRECEDENCE)}

normative_artifacts:
""" + "\n".join(f"  - path: {path}\n    role: implementation-binding package member" for path in NORMATIVE_ARTIFACTS) + f"""

required_pre_review_checks:
  - all normative_artifacts paths exist
  - consolidated response correction pack and manifest have identical normative register precedence and apply order
  - all SDI-001 through SDI-024 appear in the decision matrix
  - SLA and decision transition tables exist
  - effective OpenAPI is built by applying every overlay in order
  - effective OpenAPI passes openapi-spec-validator and Redocly with zero errors or warnings
  - every effective Response Object has description
  - ApiError requires and defines occurred_at and next_action
  - all four schema artifacts apply successfully to PostgreSQL 16
  - every tenant-bearing table has ENABLE and FORCE ROW LEVEL SECURITY
  - every tenant-bearing table has a fail-closed tenant_isolation policy
  - every tenant-scoped foreign-key relationship has a tenant-qualified composite counterpart
  - all required lineage constraints exist in PostgreSQL catalog
  - reviewed_commit equals current PR head
  - formal review commit is a descendant of reviewed_commit

{yaml_list('openapi_bundle_order', OPENAPI_ORDER)}

{yaml_list('schema_apply_order', SCHEMA_ORDER)}

{yaml_list('event_apply_order', EVENT_ORDER)}

formal_review_command: >-
  python scripts/validate_assisted_listing_intake_design.py
  --reviewed-commit <current-pr-head>
  --current-pr-head <current-pr-head>
  --base-commit <current-dev-head>
  --strict-review-target

fail_closed_gates:
  normative_register_or_order_mismatch: no review decision or engineering handoff is valid
  openapi_bundle_build_or_lint_failed: assisted_intake_v1_read=false, assisted_intake_v1_write=false
  event_payload_schema_incomplete: assisted_intake_v1_events=false
  tenant_fk_rls_or_policy_validation_failed: assisted_intake_v1_write=false
  promotion_contract_tests_failed: assisted_intake_v1_promotion=false
  stale_review_target: no approval or changes-requested decision may be attributed to current head
  invalid_review_lineage: review artifact cannot merge independently of reviewed artifacts
"""
write(MANIFEST, manifest)

builder_path = ROOT / OPENAPI_BUILDER
builder = builder_path.read_text(encoding="utf-8")
old_builder_block = '''ROOT = Path(__file__).resolve().parents[1]\nDEFAULT_BASE = ROOT / "docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1.yaml"\nDEFAULT_OVERLAYS = [\n    ROOT / "docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_OVERLAY.yaml",\n    ROOT / "docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_1_CONSISTENCY_OVERLAY.yaml",\n    ROOT / "docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_2_LINT_OVERLAY.yaml",\n]\nHTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}\n'''
new_builder_block = '''ROOT = Path(__file__).resolve().parents[1]\nMANIFEST_PATH = ROOT / "docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_REVIEW_MANIFEST.yaml"\n\ndef _manifest_openapi_order() -> list[Path]:\n    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))\n    order = manifest.get("openapi_bundle_order")\n    if not isinstance(order, list) or len(order) < 2 or not all(isinstance(item, str) for item in order):\n        raise ValueError("review manifest must define openapi_bundle_order with base plus overlays")\n    return [ROOT / item for item in order]\n\n_OPENAPI_ORDER = _manifest_openapi_order()\nDEFAULT_BASE = _OPENAPI_ORDER[0]\nDEFAULT_OVERLAYS = _OPENAPI_ORDER[1:]\nHTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}\n'''
if old_builder_block not in builder:
    raise RuntimeError("OpenAPI builder default stack block did not match expected source")
builder = builder.replace(old_builder_block, new_builder_block)
write(OPENAPI_BUILDER, builder)

validator = r'''#!/usr/bin/env python3
"""Commit-bound cross-contract gate for ODP-SD-INTAKE-001."""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESPONSE = "docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SYSTEM_DESIGN_RESPONSE.md"
CORRECTION = "docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V021_CROSS_CONTRACT_CORRECTIONS.md"
MANIFEST = "docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_REVIEW_MANIFEST.yaml"

NORMATIVE_ARTIFACTS = __NORMATIVE_ARTIFACTS__
SCHEMA_ORDER = __SCHEMA_ORDER__
OPENAPI_ORDER = __OPENAPI_ORDER__
EVENT_ORDER = __EVENT_ORDER__
PRECEDENCE = __PRECEDENCE__
EXPECTED_REGISTER = {
    "manifest_path": MANIFEST,
    "normative_artifacts": NORMATIVE_ARTIFACTS,
    "precedence": PRECEDENCE,
    "schema_apply_order": SCHEMA_ORDER,
    "openapi_bundle_order": OPENAPI_ORDER,
    "event_apply_order": EVENT_ORDER,
}

REQUIRED_COMMAND_PATHS = (
    "/v1/intakes/{intake_id}/promotion-requests",
    "/v1/promotion-decisions/{promotion_decision_id}/actions/review",
    "/v1/intakes/{intake_id}/actions/cancel",
    "/v1/intakes/{intake_id}/actions/quarantine",
    "/v1/intakes/{intake_id}/actions/reopen",
    "/v1/assignments/{assignment_id}/actions/claim",
    "/v1/assignments/{assignment_id}/actions/transfer",
    "/v1/assignments/{assignment_id}/actions/complete",
    "/v1/sla-instances/{sla_instance_id}/actions/pause",
    "/v1/sla-instances/{sla_instance_id}/actions/resume",
    "/v1/identity-decisions/{decision_id}/actions/review",
    "/v1/identity-decisions/{decision_id}/actions/reverse",
)
CANONICAL_ERROR_CODES = {
    "AUTHENTICATION_REQUIRED", "ROLE_DENIED", "TENANT_SCOPE_DENIED", "SCOPE_DENIED",
    "OWNERSHIP_REQUIRED", "ASSIGNMENT_SCOPE_DENIED", "SOURCE_SCOPE_DENIED", "FIELD_MASKED",
    "DATA_CLASSIFICATION_DENIED", "PURPOSE_REQUIRED", "PRECONDITION_REQUIRED", "VERSION_CONFLICT",
    "WORKFLOW_STATE_DENIED", "OWNER_CONFLICT", "SECOND_ACTOR_REQUIRED", "SELF_REVIEW_DENIED",
    "RISK_ACKNOWLEDGEMENT_REQUIRED", "SOURCE_POLICY_DENIED", "SOURCE_POLICY_UNKNOWN",
    "SOURCE_AUTH_REQUIRED", "LEGAL_HOLD_CONFLICT", "RETENTION_NOT_REACHED", "RESIDENCY_DENIED",
    "EXPORT_APPROVAL_REQUIRED", "PURGE_APPROVAL_REQUIRED", "QUARANTINE_RELEASE_DENIED",
    "PROMOTION_APPROVAL_REQUIRED", "RESTRICTED_EXPORT_DENIED", "BREAK_GLASS_DENIED",
    "DEPENDENCY_CONFLICT", "DUPLICATE_CANDIDATE", "IDEMPOTENCY_KEY_REUSED",
    "RETRY_BUDGET_EXHAUSTED", "CHECKPOINT_UNAVAILABLE", "JOB_FENCE_REJECTED", "SLA_PAUSE_DENIED",
    "DECISION_INCOMPLETE", "BACKPRESSURE_ACTIVE", "RATE_LIMITED", "RESOURCE_NOT_FOUND",
    "VALIDATION_FAILED", "FIELD_REQUIRED", "CURSOR_INVALID", "CURSOR_EXPIRED", "INTERNAL_ERROR",
}
REQUIRED_EVENT_TYPES = {
    "intake.submitted", "intake.state_changed", "snapshot.created", "parser.run_completed",
    "match.review_required", "match.decided", "identity.resolution_changed", "listing.created",
    "listing.revised", "listing.status_changed", "assignment.assigned", "assignment.transferred",
    "assignment.claimed", "assignment.completed", "sla.state_changed", "sla.breached",
    "candidate.promotion_requested", "candidate.promotion_reviewed", "candidate.created",
    "candidate.promotion_completed", "candidate.promotion_failed", "sitescore.requested",
    "sitescore.failed", "job.replay_requested", "job.dead_lettered", "legal_hold.placed",
    "legal_hold.released", "evidence.exported", "audit.event_recorded",
}

@dataclass
class Finding:
    check: str
    ok: bool
    detail: str

def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")

def add(findings: list[Finding], check: str, ok: bool, detail: str) -> None:
    findings.append(Finding(check, ok, detail))

def extract_register(text: str) -> dict:
    start = "<!-- normative-register:start -->"
    end = "<!-- normative-register:end -->"
    if start not in text or end not in text:
        raise ValueError("normative register markers missing")
    segment = text.split(start, 1)[1].split(end, 1)[0]
    match = re.search(r"```json\s*(\{.*\})\s*```", segment, flags=re.S)
    if not match:
        raise ValueError("normative register JSON fence missing")
    return json.loads(match.group(1))

def yaml_list(text: str, key: str) -> list[str]:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line == f"{key}:":
            values: list[str] = []
            for row in lines[i + 1:]:
                if row.startswith("  - "):
                    values.append(row[4:].strip())
                elif row and not row.startswith(" "):
                    break
            return values
    return []

def manifest_artifacts(text: str) -> list[str]:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line == "normative_artifacts:":
            values: list[str] = []
            for row in lines[i + 1:]:
                match = re.match(r"^  - path:\s*(\S+)\s*$", row)
                if match:
                    values.append(match.group(1))
                elif row and not row.startswith(" "):
                    break
            return values
    return []

def extract_event_types(text: str) -> set[str]:
    return set(re.findall(r"^\s*-?\s*event_type:\s*([a-z0-9_.-]+)\s*$", text, flags=re.M))

def extract_schema_refs(text: str) -> set[str]:
    return set(re.findall(r"schema_ref:\s*['\"]?#/payloads/([A-Za-z0-9_]+)", text))

def extract_payload_names(text: str) -> set[str]:
    if "payloads:" not in text:
        return set()
    names: set[str] = set()
    for line in text.split("payloads:", 1)[1].splitlines():
        match = re.match(r"^\s{2}([A-Za-z][A-Za-z0-9_]+):\s*$", line)
        if match:
            names.add(match.group(1))
    return names

def report(findings: list[Finding], as_json: bool) -> int:
    failed = [f for f in findings if not f.ok]
    if as_json:
        print(json.dumps({"status": "PASS" if not failed else "FAIL", "findings": [f.__dict__ for f in findings]}, indent=2))
    else:
        for finding in findings:
            print(f"[{'PASS' if finding.ok else 'FAIL'}] {finding.check}: {finding.detail}")
    return 0 if not failed else 1

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reviewed-commit")
    parser.add_argument("--current-pr-head")
    parser.add_argument("--base-commit")
    parser.add_argument("--strict-review-target", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()
    findings: list[Finding] = []

    missing = [path for path in NORMATIVE_ARTIFACTS if not (ROOT / path).is_file()]
    empty = [path for path in NORMATIVE_ARTIFACTS if (ROOT / path).is_file() and (ROOT / path).stat().st_size == 0]
    add(findings, "required_artifacts", not missing and not empty, f"missing={missing}; empty={empty}" if missing or empty else "all present")
    if missing:
        return report(findings, args.json_output)

    response = read(RESPONSE)
    correction = read(CORRECTION)
    manifest = read(MANIFEST)
    try:
        response_register = extract_register(response)
        correction_register = extract_register(correction)
        manifest_register = {
            "manifest_path": MANIFEST,
            "normative_artifacts": manifest_artifacts(manifest),
            "precedence": yaml_list(manifest, "normative_precedence"),
            "schema_apply_order": yaml_list(manifest, "schema_apply_order"),
            "openapi_bundle_order": yaml_list(manifest, "openapi_bundle_order"),
            "event_apply_order": yaml_list(manifest, "event_apply_order"),
        }
        registers_ok = response_register == correction_register == manifest_register == EXPECTED_REGISTER
        detail = "response, correction pack and manifest are identical" if registers_ok else json.dumps({"expected": EXPECTED_REGISTER, "response": response_register, "correction": correction_register, "manifest": manifest_register}, ensure_ascii=False)
        add(findings, "normative_register_precedence_apply_order", registers_ok, detail)
    except Exception as exc:
        add(findings, "normative_register_precedence_apply_order", False, str(exc))

    add(findings, "effective_version", "version: 0.2.1" in response and "version: 0.2.1" in correction and "effective_response_version: 0.2.1" in manifest, "all normative narrative/control files identify 0.2.1")
    add(findings, "effective_api_wording", "three-file bundle" not in response and "six-artifact OpenAPI bundle" in response, "main response names the complete bundle")
    add(findings, "persistence_wording", "0002` alone is not the canonical relational patch" in correction and SCHEMA_ORDER[-1] in correction, "correction pack names the complete four-file schema stack")

    missing_sdi = [f"SDI-{i:03d}" for i in range(1, 25) if f"SDI-{i:03d}" not in response]
    add(findings, "decision_coverage", not missing_sdi, "complete" if not missing_sdi else f"missing={missing_sdi}")
    add(findings, "binding_transition_tables", "## 3. Binding SLA state machine" in correction and "## 4. Binding decision review, execution, and reversal transitions" in correction, "present")

    overlays = "\n".join(read(path) for path in OPENAPI_ORDER[1:])
    missing_commands = [path for path in REQUIRED_COMMAND_PATHS if path not in overlays]
    add(findings, "command_api_coverage", not missing_commands, "complete" if not missing_commands else f"missing={missing_commands}")
    missing_errors = sorted(code for code in CANONICAL_ERROR_CODES if code not in overlays)
    add(findings, "canonical_error_registry", not missing_errors, "complete" if not missing_errors else f"missing={missing_errors}")

    base_events = read(EVENT_ORDER[0])
    addendum = read(EVENT_ORDER[1])
    payloads = read(EVENT_ORDER[2])
    event_types = extract_event_types(base_events) | extract_event_types(addendum)
    missing_events = sorted(REQUIRED_EVENT_TYPES - event_types)
    add(findings, "event_catalog_coverage", not missing_events, "complete" if not missing_events else f"missing={missing_events}")
    refs = extract_schema_refs(base_events) | extract_schema_refs(addendum)
    names = extract_payload_names(payloads) | extract_payload_names(addendum)
    missing_payloads = sorted(refs - names)
    add(findings, "event_payload_schema_coverage", not missing_payloads, "complete" if not missing_payloads else f"missing={missing_payloads}")

    schema_stack = "\n".join(read(path) for path in SCHEMA_ORDER)
    patch_0004 = read(SCHEMA_ORDER[-1])
    rls_tokens = ("FORCE ROW LEVEL SECURITY", "CREATE POLICY tenant_isolation", "fk_intake_resolved_listing_tenant", "fk_edge_supersedes_tenant", "fk_promotion_candidate_tenant")
    missing_rls = [token for token in rls_tokens if token not in patch_0004]
    add(findings, "tenant_rls_lineage_contract", not missing_rls, "complete" if not missing_rls else f"missing={missing_rls}")
    history_tokens = ("workflow.assignment_transitions", "workflow.sla_transitions", "workflow.sla_pause_intervals", "LEGACY_RECONCILED", "migration_ref", "workflow.reconciliation_findings", "PENDING_REVIEW")
    missing_history = [token for token in history_tokens if token not in schema_stack]
    add(findings, "history_and_migration_schema", not missing_history, "complete" if not missing_history else f"missing={missing_history}")

    builder = read("scripts/build_validate_assisted_listing_intake_openapi.py")
    workflow = read(".github/workflows/assisted-intake-design-validation.yml")
    builder_ok = "openapi_bundle_order" in builder and "MANIFEST_PATH" in builder
    workflow_missing = [path for path in SCHEMA_ORDER + OPENAPI_ORDER if path not in workflow and path not in builder]
    add(findings, "ci_uses_registered_stacks", builder_ok and not workflow_missing, "manifest-driven OpenAPI builder and complete CI schema/API stack" if builder_ok and not workflow_missing else f"builder_ok={builder_ok}; missing={workflow_missing}")

    if args.strict_review_target and (not args.reviewed_commit or not args.current_pr_head):
        add(findings, "review_target", False, "strict mode requires reviewed and current SHA")
    elif args.reviewed_commit or args.current_pr_head:
        ok = bool(args.reviewed_commit and args.current_pr_head and args.reviewed_commit == args.current_pr_head)
        add(findings, "review_target", ok, "commit-bound" if ok else "STALE_REVIEW_TARGET")
    else:
        add(findings, "review_target", True, "not evaluated outside formal review")

    return report(findings, args.json_output)

if __name__ == "__main__":
    sys.exit(main())
'''
validator = validator.replace("__NORMATIVE_ARTIFACTS__", repr(NORMATIVE_ARTIFACTS))
validator = validator.replace("__SCHEMA_ORDER__", repr(SCHEMA_ORDER))
validator = validator.replace("__OPENAPI_ORDER__", repr(OPENAPI_ORDER))
validator = validator.replace("__EVENT_ORDER__", repr(EVENT_ORDER))
validator = validator.replace("__PRECEDENCE__", repr(PRECEDENCE))
write(DESIGN_VALIDATOR, validator)

print("Normative register, precedence, apply order, builder defaults and validator synchronized.")

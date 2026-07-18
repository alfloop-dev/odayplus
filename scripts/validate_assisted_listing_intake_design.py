#!/usr/bin/env python3
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

NORMATIVE_ARTIFACTS = ['docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SYSTEM_DESIGN_ALIGNMENT_REQUEST.md', 'docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SYSTEM_DESIGN_RESPONSE.md', 'docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V021_CROSS_CONTRACT_CORRECTIONS.md', 'docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_STATE_CONTRACTS.md', 'docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_AUTHORIZATION_MATRIX.md', 'docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_REVIEW_MANIFEST.yaml', 'docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA.sql', 'docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0002_CONSISTENCY_PATCH.sql', 'docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0003_PROMOTION_STATE_PATCH.sql', 'docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0004_TENANT_RLS_LINEAGE_PATCH.sql', 'docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1.yaml', 'docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_0_1_PRELUDE_OVERLAY.yaml', 'docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_OVERLAY.yaml', 'docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_1_CONSISTENCY_OVERLAY.yaml', 'docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_2_LINT_OVERLAY.yaml', 'docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_3_REDOCLY_OVERLAY.yaml', 'docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1.yaml', 'docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1_1_ADDENDUM.yaml', 'docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENT_PAYLOAD_SCHEMAS_V1.yaml', 'docs/operations/ODAY_PLUS_ASSISTED_LISTING_INTAKE_RELIABILITY_PRIVACY_CONTRACT.md', 'docs/operations/ODAY_PLUS_ASSISTED_LISTING_INTAKE_MIGRATION_ROLLOUT_RUNBOOK.md', 'scripts/validate_assisted_listing_intake_design.py', 'scripts/build_validate_assisted_listing_intake_openapi.py', 'scripts/validate_assisted_listing_intake_schema.sql', '.github/workflows/assisted-intake-design-validation.yml']
SCHEMA_ORDER = ['docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA.sql', 'docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0002_CONSISTENCY_PATCH.sql', 'docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0003_PROMOTION_STATE_PATCH.sql', 'docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0004_TENANT_RLS_LINEAGE_PATCH.sql']
OPENAPI_ORDER = ['docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1.yaml', 'docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_0_1_PRELUDE_OVERLAY.yaml', 'docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_OVERLAY.yaml', 'docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_1_CONSISTENCY_OVERLAY.yaml', 'docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_2_LINT_OVERLAY.yaml', 'docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_3_REDOCLY_OVERLAY.yaml']
EVENT_ORDER = ['docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1.yaml', 'docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1_1_ADDENDUM.yaml', 'docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENT_PAYLOAD_SCHEMAS_V1.yaml']
PRECEDENCE = ['alignment_request', 'consolidated_response', 'review_manifest_for_artifact_register_and_apply_order', 'correction_pack_for_explicit_textual_corrections', 'machine_readable_stacks_in_manifest_order_later_artifact_overrides_earlier', 'unchanged_base_artifact_clauses', 'runtime_implementation']
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
    builder_ok = (
        "MANIFEST_PATH" in builder
        and "_manifest_openapi_order" in builder
        and 'manifest.get("openapi_bundle_order")' in builder
    )
    openapi_step = workflow.split(
        "- name: Build and structurally validate effective OpenAPI", 1
    )[-1].split("- name: Redocly lint effective OpenAPI", 1)[0]
    workflow_uses_manifest_openapi = (
        "scripts/build_validate_assisted_listing_intake_openapi.py" in openapi_step
        and "--base" not in openapi_step
        and "--overlay" not in openapi_step
    )
    schema_positions = [workflow.find(path) for path in SCHEMA_ORDER]
    workflow_uses_schema_order = (
        all(position >= 0 for position in schema_positions)
        and schema_positions == sorted(schema_positions)
    )
    registered_stacks_ok = (
        builder_ok and workflow_uses_manifest_openapi and workflow_uses_schema_order
    )
    stack_detail = (
        "manifest-driven OpenAPI builder and complete ordered CI schema stack"
        if registered_stacks_ok
        else json.dumps(
            {
                "builder_ok": builder_ok,
                "workflow_uses_manifest_openapi": workflow_uses_manifest_openapi,
                "workflow_schema_positions": schema_positions,
            }
        )
    )
    add(findings, "ci_uses_registered_stacks", registered_stacks_ok, stack_detail)

    cross_contract_step = workflow.split(
        "- name: Run commit-bound cross-contract validation", 1
    )[-1].split("- name: Verify cross-contract gate fails closed", 1)[0]
    enforcement_step = workflow.split("- name: Enforce all structural gates", 1)[-1]
    fail_closed_ci_ok = (
        "id: cross_contract" in cross_contract_step
        and "continue-on-error: true" in cross_contract_step
        and "| tee" not in cross_contract_step
        and 'exit "$STATUS"' in cross_contract_step
        and "steps.cross_contract.outcome" in enforcement_step
        and "cross-contract-negative-validation.json" in workflow
    )
    add(
        findings,
        "ci_enforces_cross_contract_exit",
        fail_closed_ci_ok,
        "validator exit is preserved, enforced, and covered by a negative mismatch test"
        if fail_closed_ci_ok
        else "cross-contract validator can still produce a false-green workflow",
    )

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

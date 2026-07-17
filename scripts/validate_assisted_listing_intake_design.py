#!/usr/bin/env python3
"""Pre-review consistency gate for ODP-SD-INTAKE-001.

This validator intentionally uses only the Python standard library so it can run
in a fresh checkout before the product dependency graph is installed. It checks
cross-artifact invariants that previously allowed a design package to look
complete while carrying contradictory state, API, schema, authorization, event,
and review-target contracts.

Usage:
    python scripts/validate_assisted_listing_intake_design.py
    python scripts/validate_assisted_listing_intake_design.py \
        --reviewed-commit "$REVIEWED_COMMIT" \
        --current-pr-head "$CURRENT_PR_HEAD" \
        --base-commit "$BASE_COMMIT" \
        --strict-review-target
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_ARTIFACTS = (
    "docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SYSTEM_DESIGN_ALIGNMENT_REQUEST.md",
    "docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SYSTEM_DESIGN_RESPONSE.md",
    "docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V021_CROSS_CONTRACT_CORRECTIONS.md",
    "docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_STATE_CONTRACTS.md",
    "docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_AUTHORIZATION_MATRIX.md",
    "docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_REVIEW_MANIFEST.yaml",
    "docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA.sql",
    "docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0002_CONSISTENCY_PATCH.sql",
    "docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1.yaml",
    "docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_OVERLAY.yaml",
    "docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1.yaml",
    "docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1_1_ADDENDUM.yaml",
    "docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENT_PAYLOAD_SCHEMAS_V1.yaml",
    "docs/operations/ODAY_PLUS_ASSISTED_LISTING_INTAKE_RELIABILITY_PRIVACY_CONTRACT.md",
    "docs/operations/ODAY_PLUS_ASSISTED_LISTING_INTAKE_MIGRATION_ROLLOUT_RUNBOOK.md",
)

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
    "AUTHENTICATION_REQUIRED",
    "ROLE_DENIED",
    "TENANT_SCOPE_DENIED",
    "SCOPE_DENIED",
    "OWNERSHIP_REQUIRED",
    "ASSIGNMENT_SCOPE_DENIED",
    "SOURCE_SCOPE_DENIED",
    "FIELD_MASKED",
    "DATA_CLASSIFICATION_DENIED",
    "PURPOSE_REQUIRED",
    "PRECONDITION_REQUIRED",
    "VERSION_CONFLICT",
    "WORKFLOW_STATE_DENIED",
    "OWNER_CONFLICT",
    "SECOND_ACTOR_REQUIRED",
    "SELF_REVIEW_DENIED",
    "RISK_ACKNOWLEDGEMENT_REQUIRED",
    "SOURCE_POLICY_DENIED",
    "SOURCE_POLICY_UNKNOWN",
    "SOURCE_AUTH_REQUIRED",
    "LEGAL_HOLD_CONFLICT",
    "RETENTION_NOT_REACHED",
    "RESIDENCY_DENIED",
    "EXPORT_APPROVAL_REQUIRED",
    "PURGE_APPROVAL_REQUIRED",
    "BREAK_GLASS_DENIED",
    "DEPENDENCY_CONFLICT",
    "DUPLICATE_CANDIDATE",
    "IDEMPOTENCY_KEY_REUSED",
    "RETRY_BUDGET_EXHAUSTED",
    "CHECKPOINT_UNAVAILABLE",
    "JOB_FENCE_REJECTED",
    "SLA_PAUSE_DENIED",
    "DECISION_INCOMPLETE",
    "BACKPRESSURE_ACTIVE",
    "RATE_LIMITED",
    "RESOURCE_NOT_FOUND",
    "VALIDATION_FAILED",
    "FIELD_REQUIRED",
    "CURSOR_INVALID",
    "CURSOR_EXPIRED",
    "INTERNAL_ERROR",
}

REQUIRED_EVENT_TYPES = {
    "intake.submitted",
    "intake.state_changed",
    "snapshot.created",
    "parser.run_completed",
    "match.review_required",
    "match.decided",
    "identity.resolution_changed",
    "listing.created",
    "listing.revised",
    "listing.status_changed",
    "assignment.assigned",
    "assignment.transferred",
    "assignment.claimed",
    "assignment.completed",
    "sla.state_changed",
    "sla.breached",
    "candidate.promotion_requested",
    "candidate.promotion_reviewed",
    "candidate.created",
    "candidate.promotion_completed",
    "candidate.promotion_failed",
    "sitescore.requested",
    "sitescore.failed",
    "job.replay_requested",
    "job.dead_lettered",
    "legal_hold.placed",
    "legal_hold.released",
    "evidence.exported",
    "audit.event_recorded",
}


@dataclass
class Finding:
    check: str
    ok: bool
    detail: str


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def require_contains(findings: list[Finding], check: str, text: str, values: tuple[str, ...]) -> None:
    missing = [value for value in values if value not in text]
    findings.append(
        Finding(check, not missing, "present" if not missing else f"missing: {', '.join(missing)}")
    )


def extract_event_types(text: str) -> set[str]:
    return set(re.findall(r"^\s*-?\s*event_type:\s*([a-z0-9_.-]+)\s*$", text, flags=re.MULTILINE))


def extract_schema_refs(text: str) -> set[str]:
    return set(re.findall(r"schema_ref:\s*['\"]?#/payloads/([A-Za-z0-9_]+)", text))


def extract_payload_names(text: str) -> set[str]:
    payload_section = text.split("payloads:", 1)
    if len(payload_section) != 2:
        return set()
    names: set[str] = set()
    for line in payload_section[1].splitlines():
        match = re.match(r"^\s{2}([A-Za-z][A-Za-z0-9_]+):\s*$", line)
        if match:
            names.add(match.group(1))
    return names


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reviewed-commit")
    parser.add_argument("--current-pr-head")
    parser.add_argument("--base-commit")
    parser.add_argument("--strict-review-target", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    findings: list[Finding] = []

    missing_files = [path for path in REQUIRED_ARTIFACTS if not (ROOT / path).is_file()]
    empty_files = [
        path
        for path in REQUIRED_ARTIFACTS
        if (ROOT / path).is_file() and (ROOT / path).stat().st_size == 0
    ]
    findings.append(
        Finding(
            "required_artifacts",
            not missing_files and not empty_files,
            f"missing={missing_files}; empty={empty_files}" if missing_files or empty_files else "all present",
        )
    )
    if missing_files:
        return report(findings, args.json_output)

    response = read("docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SYSTEM_DESIGN_RESPONSE.md")
    correction = read("docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V021_CROSS_CONTRACT_CORRECTIONS.md")
    state_contracts = read("docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_STATE_CONTRACTS.md")
    auth = read("docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_AUTHORIZATION_MATRIX.md")
    base_api = read("docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1.yaml")
    overlay = read("docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_OVERLAY.yaml")
    base_events = read("docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1.yaml")
    event_addendum = read("docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1_1_ADDENDUM.yaml")
    payload_registry = read("docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENT_PAYLOAD_SCHEMAS_V1.yaml")
    base_schema = read("docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA.sql")
    schema_patch = read("docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0002_CONSISTENCY_PATCH.sql")
    migration = read("docs/operations/ODAY_PLUS_ASSISTED_LISTING_INTAKE_MIGRATION_ROLLOUT_RUNBOOK.md")

    findings.append(
        Finding(
            "effective_version",
            "version: 0.2.1" in response and "version: 0.2.1" in correction,
            "main response and correction pack must both identify 0.2.1",
        )
    )

    missing_sdi = [f"SDI-{index:03d}" for index in range(1, 25) if f"SDI-{index:03d}" not in response]
    findings.append(Finding("decision_coverage", not missing_sdi, "complete" if not missing_sdi else f"missing={missing_sdi}"))

    require_contains(
        findings,
        "binding_transition_tables",
        correction,
        ("## 3. Binding SLA state machine", "## 4. Binding decision review, execution, and reversal transitions"),
    )
    require_contains(
        findings,
        "base_state_models",
        state_contracts,
        ("## 2. Intake processing", "## 3. Listing lifecycle", "## 4. Identity graph", "## 5. Assignment and SLA", "## 7. Candidate promotion"),
    )

    overlay_missing = [path for path in REQUIRED_COMMAND_PATHS if path not in overlay]
    findings.append(
        Finding(
            "command_api_coverage",
            not overlay_missing,
            "complete" if not overlay_missing else f"missing={overlay_missing}",
        )
    )
    old_promotion_removed = (
        "$.paths['/v1/intakes/{intake_id}/promotion']" in overlay
        and "remove: true" in overlay
        and "/v1/intakes/{intake_id}/promotion-requests" in overlay
    )
    findings.append(Finding("promotion_api_correction", old_promotion_removed, "old final receipt route removed; request/review flow present"))

    missing_error_codes = sorted(code for code in CANONICAL_ERROR_CODES if code not in overlay)
    auth_codes = set(re.findall(r"`([A-Z][A-Z0-9_]+)`", auth))
    missing_auth_codes = sorted(code for code in auth_codes if code.endswith(("DENIED", "REQUIRED", "CONFLICT")) and code not in CANONICAL_ERROR_CODES)
    findings.append(
        Finding(
            "canonical_error_registry",
            not missing_error_codes and not missing_auth_codes,
            f"missing_from_overlay={missing_error_codes}; auth_not_registered={missing_auth_codes}",
        )
    )

    event_types = extract_event_types(base_events) | extract_event_types(event_addendum)
    missing_events = sorted(REQUIRED_EVENT_TYPES - event_types)
    findings.append(Finding("event_catalog_coverage", not missing_events, "complete" if not missing_events else f"missing={missing_events}"))

    schema_refs = extract_schema_refs(base_events) | extract_schema_refs(event_addendum)
    payload_names = extract_payload_names(payload_registry) | extract_payload_names(event_addendum)
    missing_payloads = sorted(schema_refs - payload_names)
    findings.append(
        Finding(
            "event_payload_schema_coverage",
            not missing_payloads,
            "complete typed payload registry" if not missing_payloads else f"missing={missing_payloads}",
        )
    )

    require_contains(
        findings,
        "tenant_isolation_patch",
        schema_patch,
        (
            "fk_transition_intake_tenant",
            "fk_listing_property_tenant",
            "fk_candidate_promotion_tenant",
            "ENABLE ROW LEVEL SECURITY",
            "CROSS_TENANT_REFERENCE",
        ),
    )
    require_contains(
        findings,
        "history_and_migration_schema",
        schema_patch,
        (
            "workflow.assignment_transitions",
            "workflow.sla_transitions",
            "workflow.sla_pause_intervals",
            "LEGACY_RECONCILED",
            "migration_ref",
            "workflow.reconciliation_findings",
        ),
    )
    findings.append(
        Finding(
            "legacy_reconciled_contract",
            "LEGACY_RECONCILED" in schema_patch and "LEGACY_RECONCILED" in migration,
            "schema and migration agree",
        )
    )

    # Detect the two uniqueness contracts corrected by patch 0002.
    findings.append(
        Finding(
            "lineage_safe_uniqueness",
            "DROP INDEX IF EXISTS intake.ux_intakes_exact_url_active" in schema_patch
            and "uq_snapshot_per_intake_content" in schema_patch,
            "URL history and per-intake snapshot evidence preserved",
        )
    )

    if args.strict_review_target and (not args.reviewed_commit or not args.current_pr_head):
        findings.append(Finding("review_target", False, "strict mode requires --reviewed-commit and --current-pr-head"))
    elif args.reviewed_commit or args.current_pr_head:
        findings.append(
            Finding(
                "review_target",
                bool(args.reviewed_commit and args.current_pr_head and args.reviewed_commit == args.current_pr_head),
                "commit-bound" if args.reviewed_commit == args.current_pr_head else "STALE_REVIEW_TARGET",
            )
        )
    else:
        findings.append(Finding("review_target", True, "not evaluated; use --strict-review-target for formal review"))

    # The base files may contain superseded clauses only when the correction pack
    # and overlays are present. This check prevents silently deleting the base
    # lineage while still forbidding direct implementation from it.
    findings.append(
        Finding(
            "supersession_lineage",
            "ODP-SD-INTAKE-001-CORR-021" in correction
            and "extends: ./ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1.yaml" in overlay
            and "Apply after ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA.sql" in schema_patch,
            "base artifacts retained with explicit correction precedence",
        )
    )

    # Make sure the baseline still exists; this is useful in review output.
    findings.append(Finding("base_contract_nonempty", len(base_api) > 1000 and len(base_schema) > 1000, "base OpenAPI and DDL present"))

    return report(findings, args.json_output)


def report(findings: list[Finding], json_output: bool) -> int:
    failed = [finding for finding in findings if not finding.ok]
    if json_output:
        print(
            json.dumps(
                {
                    "status": "PASS" if not failed else "FAIL",
                    "root": str(ROOT),
                    "findings": [finding.__dict__ for finding in findings],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for finding in findings:
            marker = "PASS" if finding.ok else "FAIL"
            print(f"[{marker}] {finding.check}: {finding.detail}")
        print(f"\nResult: {'PASS' if not failed else 'FAIL'} ({len(failed)} failed)")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

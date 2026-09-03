"""Contract and compatibility tests for ODP-FR-FCT-004 root_cause disposition.

This test module verifies the acceptance criteria of ODP-FCT-ROOT-CAUSE-CONTRACT-001:
1. Proves that WorkOrder.root_cause is marked as RESERVED (unproduced).
2. Proves that no fake automated producer exists in ForecastOps domain/application.
3. Verifies forward/backward compatibility across Python domain models, source ingestion
   contracts, TypeScript definitions, database migrations, and governance registries.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from modules.forecastops.domain.forecasting import Alert, ForecastOutput
from modules.integration.domain.contracts import load_contract, validate_record
from shared.domain import WorkOrder

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_work_order_domain_model_reserved_status_and_compatibility() -> None:
    """WorkOrder defaults root_cause to None and preserves compatibility."""
    order_default = WorkOrder(
        store_id=str(uuid4()),
        issue_type="failure",
        issue_subtype="compressor_leak",
        cost_amount=1500.0,
    )
    assert order_default.root_cause is None

    order_with_cause = WorkOrder(
        store_id=str(uuid4()),
        issue_type="failure",
        root_cause="worn_seal",
    )
    assert order_with_cause.root_cause == "worn_seal"

    # Verify docstring and comment state RESERVED status
    doc = WorkOrder.__doc__ or ""
    assert "RESERVED" in doc
    assert "ODP-FR-FCT-004" in doc
    assert "ForecastOps / Platform Ops" in doc
    assert "Wave 5+" in doc


def test_forecastops_does_not_manufacture_fake_root_cause_producer() -> None:
    """ForecastOps outputs must not manufacture a fake root cause to pass requirements."""
    # ForecastOutput carries trajectory and turning point, not fake root causes
    field_names = {f.name for f in ForecastOutput.__dataclass_fields__.values()}
    assert "trajectory_class" in field_names
    assert "turning_point_probability" in field_names
    assert "root_cause" not in field_names
    assert "cause_candidate" not in field_names

    # Alert carries evidence_json for anomaly evidence, not fake deduction engine
    alert_fields = {f.name for f in Alert.__dataclass_fields__.values()}
    assert "evidence_json" in alert_fields
    assert "root_cause" not in alert_fields


def test_source_contract_ingestion_compatibility() -> None:
    """maintenance_work_order_event source contract accepts both present and omitted root_cause."""
    contract = load_contract("maintenance_work_order_event")
    root_cause_field = next(
        field for field in contract.fields if field.name == "root_cause"
    )
    assert "manual/field maintenance annotation" in root_cause_field.description
    assert "no automated root-cause producer" in root_cause_field.description

    # Case 1: Record with explicit root_cause (manual field maintenance note)
    record_with_cause = {
        "source_work_order_id": "WO-101",
        "source_store_id": "S001",
        "issue_type": "failure",
        "opened_at": "2026-09-03T08:00:00Z",
        "status": "resolved",
        "root_cause": "worn_bearing",
    }
    result_with = validate_record(contract, record_with_cause)
    assert result_with.ok is True

    # Case 2: Record omitting root_cause
    record_without_cause = {
        "source_work_order_id": "WO-102",
        "source_store_id": "S001",
        "issue_type": "cleaning",
        "opened_at": "2026-09-03T09:00:00Z",
        "status": "open",
    }
    result_without = validate_record(contract, record_without_cause)
    assert result_without.ok is True


def test_canonical_ts_schema_marks_root_cause_as_reserved() -> None:
    """packages/schemas/canonical/index.ts must document root_cause as reserved."""
    ts_schema_path = REPO_ROOT / "packages/schemas/canonical/index.ts"
    content = ts_schema_path.read_text(encoding="utf-8")

    assert "interface WorkOrder" in content
    assert "@reserved" in content
    assert "ODP-FR-FCT-004" in content
    assert "root_cause: string | null;" in content


def test_frontend_contracts_and_design_mark_root_cause_evidence_as_reserved() -> None:
    """packages/domain-types and component contracts design doc must mark RootCauseEvidenceCard / causeCandidate as reserved."""
    frontend_contracts_path = REPO_ROOT / "packages/domain-types/src/frontend-contracts.ts"
    design_doc_path = REPO_ROOT / "docs/design/ODAY_PLUS_COMPONENT_CONTRACTS.md"

    ts_content = frontend_contracts_path.read_text(encoding="utf-8")
    assert "export type RootCauseEvidenceCardContract" in ts_content
    assert "@reserved" in ts_content
    assert "ODP-FR-FCT-004" in ts_content
    assert "causeCandidate" in ts_content
    assert "ForecastOps / Platform Ops" in ts_content
    assert "Wave 5+" in ts_content

    doc_content = design_doc_path.read_text(encoding="utf-8")
    assert "### 5.6 RootCauseEvidenceCard" in doc_content
    assert "ODP-FR-FCT-004" in doc_content
    assert "unproduced / reserved" in doc_content
    assert "ForecastOps / Platform Ops" in doc_content


def test_migration_0013_structure_and_rollback() -> None:
    """Alembic revision 0013 and SQL 000018 are present and executable."""
    sql_path = REPO_ROOT / "infra/db/migrations/000018_work_orders_root_cause_disposition.sql"
    py_path = REPO_ROOT / "infra/db/migrations/versions/0013_work_orders_root_cause_disposition.py"

    assert sql_path.exists()
    assert py_path.exists()

    sql_content = sql_path.read_text(encoding="utf-8")
    assert "COMMENT ON COLUMN core.work_orders.root_cause" in sql_content
    assert "RESERVED" in sql_content
    assert "ODP-FR-FCT-004" in sql_content

    py_content = py_path.read_text(encoding="utf-8")
    assert 'revision: str = "0013"' in py_content
    assert 'down_revision: str | None = "0012"' in py_content
    assert "000018_work_orders_root_cause_disposition.sql" in py_content
    assert "def downgrade" in py_content


def test_governance_registry_records_fct004_disposition() -> None:
    """delivery_toolchain/governance/set_valued_requirements.json includes ODP-FR-FCT-004."""
    manifest_path = REPO_ROOT / "delivery_toolchain/governance/set_valued_requirements.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    fct004 = next((r for r in manifest["requirements"] if r["id"] == "ODP-FR-FCT-004"), None)
    assert fct004 is not None, "ODP-FR-FCT-004 missing from set_valued_requirements.json"
    assert fct004["member_count"] == 4

    members_by_name = {m["name"]: m for m in fct004["members"]}
    assert members_by_name["TRAJECTORY_CLASS"]["status"] == "satisfied"
    assert members_by_name["TURNING_POINT_PROBABILITY"]["status"] == "satisfied"
    assert members_by_name["ANOMALY_EVIDENCE"]["status"] == "satisfied"
    assert members_by_name["ROOT_CAUSE_CANDIDATE"]["status"] == "absent"
    root_cause_member = members_by_name["ROOT_CAUSE_CANDIDATE"]
    assert "RESERVED" in root_cause_member["note"]
    assert "Wave 5+" in root_cause_member["note"]

    disposition = root_cause_member["disposition"]
    assert disposition["state"] == "IMPLEMENTATION_READY"
    assert disposition["assigned_to"] == "ForecastOps / Platform Ops"
    assert disposition["target_phase"] == "Wave 5+"
    assert "DECIDED" not in root_cause_member["note"]
    assert "formal_decision_ref" not in disposition
    assert "decider" not in disposition

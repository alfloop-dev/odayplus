from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPONENT_CONTRACTS = REPO_ROOT / "docs/design/ODAY_PLUS_COMPONENT_CONTRACTS.md"
UI_INDEX = REPO_ROOT / "packages/ui/src/index.ts"
UI_COMPONENTS = REPO_ROOT / "packages/ui/src/components"

CORE_COMPONENTS = [
    "Toolbar",
    "FilterBar",
    "Drawer",
    "Button",
    "Card",
    "Table",
    "Form",
    "Modal",
    "Tabs",
    "Timeline",
    "Toast",
    "Tooltip",
    "CommandPalette",
    "EmptyState",
    "DataStatusBadge",
    "ModelVersionBadge",
    "ApprovalPanel",
    "AuditMetadata",
    "AlertChip",
    "EvidencePanel",
]


def test_documented_core_components_are_exported_from_ui_package() -> None:
    docs = COMPONENT_CONTRACTS.read_text(encoding="utf-8")
    index_source = UI_INDEX.read_text(encoding="utf-8")
    contracts_source = (UI_COMPONENTS / "contracts.ts").read_text(encoding="utf-8")

    assert "CORE_UI_COMPONENT_KEYS" in index_source

    for component in CORE_COMPONENTS:
        assert component in docs, f"{component} missing from component contracts"
        assert component in index_source, f"{component} missing from @oday-plus/ui exports"
        assert component in contracts_source, f"{component} missing from core UI coverage list"


def test_core_ui_contracts_keep_audit_and_permission_hooks() -> None:
    contracts_source = (UI_COMPONENTS / "contracts.ts").read_text(encoding="utf-8")

    for hook in [
        "PermissionAware",
        "disabledReason",
        "requiresAudit",
        "DataQualityAware",
        "ApprovalSubmitPayload",
        "AuditMeta",
    ]:
        assert hook in contracts_source

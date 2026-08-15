"""Regression checks for ODP-PV-012 release blocker remediation."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_ci_baseline_includes_security_gate() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "audit:security" in (ROOT / "package.json").read_text(encoding="utf-8")
    assert "npm run audit:security" in makefile
    assert "security: bootstrap dependency-audit" in makefile
    assert "npm run build --workspaces --if-present" in makefile
    assert "ci: bootstrap lint security test smoke node-check" in makefile
    assert "make ci" in workflow


def test_production_readiness_metadata_is_filled() -> None:
    readiness = (ROOT / "docs/evidence/PRODUCTION_READINESS_PACKAGE.md").read_text(encoding="utf-8")

    metadata_block = readiness.split("## Required Evidence Manifest", maxsplit=1)[0]
    assert "[ASSIGNMENT_REQUIRED" not in metadata_block
    assert "ODP-PV-012" in metadata_block
    assert "0.1.0-pv.12" in metadata_block
    assert "task/ODP-PV-012@" in metadata_block


def test_release_blocker_evidence_records_dependency_remediation() -> None:
    evidence = (ROOT / "docs/evidence/RELEASE_BLOCKER_REMEDIATION.md").read_text(encoding="utf-8")

    assert "next` and `eslint-config-next` to `15.5.19" in evidence
    assert "`@playwright/test` to `1.61.1" in evidence
    assert "npm audit --audit-level=high" in evidence
    assert "0 high or critical findings" in evidence
    assert "production build" in evidence

"""Regression checks for the NetPlan Human/Ops baseline approval addendum."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs/evidence/DEVELOPMENT_PLAN_IMPLEMENTATION_GAP_MATRIX_2026-07-30.md"
LEDGER = ROOT / "docs/evidence/DEVELOPMENT_PLAN_GAP_EXECUTION_TASKS_2026-07-30.md"


def test_addendum_preserves_rtm_coverage_and_expands_governance_ledger() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")
    ledger = LEDGER.read_text(encoding="utf-8")

    rtm_ids = re.findall(r"^\| (PLAN-S[0-7]-(?:\d{3}|GATE)) \|", matrix, flags=re.MULTILINE)
    governance_rows = re.findall(r"^\| [A-E] \| `([^`]+)` \|", ledger, flags=re.MULTILINE)

    assert len(rtm_ids) == 84
    assert len(set(rtm_ids)) == 84
    assert len(governance_rows) == 26
    assert len(set(governance_rows)) == 26


def test_netplan_technical_and_human_approval_gates_are_separate() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")
    ledger = LEDGER.read_text(encoding="utf-8")

    assert ledger.count("### ODP-PLAN-LEDGER-NETPLAN-HUMAN-GATE-001") == 1
    assert ledger.count("### ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001") == 1
    assert "`BUSINESS_UAT_UNVERIFIED`／`GOVERNED_DISABLED`" in ledger
    assert "AI 代簽時必須 fail closed" in ledger
    assert "ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001" in matrix
    assert "AI fixture／角色字串不得代替核准" in matrix


def test_uat_and_final_gate_depend_on_authentic_netplan_approval() -> None:
    ledger = LEDGER.read_text(encoding="utf-8")

    uat_row = next(
        line
        for line in ledger.splitlines()
        if line.startswith("| D |") and "`ODP-PLAN-UAT-SIGNOFF-001`" in line
    )
    final_row = next(
        line
        for line in ledger.splitlines()
        if line.startswith("| E |") and "`ODP-PLAN-FINAL-GATE-AUDIT-001`" in line
    )

    assert "ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001" in uat_row
    assert "both NetPlan addendum tasks" in final_row

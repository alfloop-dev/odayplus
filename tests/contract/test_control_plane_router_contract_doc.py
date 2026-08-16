"""The router contract doc must stay in sync with the executable contract.

`services/control-plane/router/README.md` is the documented routing contract
for P4-001. These tests fail when `contract.py` grows a route, failure code, or
metric that the document never mentions, so the doc cannot silently rot.
"""

from __future__ import annotations

import re
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
ROUTER_DIR = ROOT / "services" / "control-plane" / "router"
CONTRACT_PATH = ROUTER_DIR / "contract.py"
DOC_PATH = ROUTER_DIR / "README.md"


@pytest.fixture(scope="module")
def contract() -> dict[str, object]:
    return runpy.run_path(str(CONTRACT_PATH))


@pytest.fixture(scope="module")
def doc() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def test_doc_pins_contract_version_and_canonical_schema(
    contract: dict[str, object], doc: str
) -> None:
    assert f"`{contract['ROUTER_CONTRACT_VERSION']}`" in doc
    assert "services/research/schema.json" in doc


def test_doc_documents_every_route_with_its_owner(
    contract: dict[str, object], doc: str
) -> None:
    for (domain, intent), target in contract["DEFAULT_ROUTES"].items():
        row = next(
            (
                line
                for line in doc.splitlines()
                if line.startswith("|") and f"`{domain}`" in line and f"`{intent}`" in line
            ),
            None,
        )
        assert row is not None, f"routing table row missing for {domain}/{intent}"
        assert f"`{target.name}`" in row, f"destination missing for {domain}/{intent}"
        assert f"`{target.owner}`" in row, f"destination owner missing for {domain}/{intent}"


def test_doc_documents_every_failure_code_with_disposition_and_retryability(
    contract: dict[str, object], doc: str
) -> None:
    dispositions = {member.value for member in contract["FailureDisposition"]}

    for code in contract["RouteErrorCode"]:
        row = next(
            (
                line
                for line in doc.splitlines()
                if line.startswith("|") and f"`{code.value}`" in line
            ),
            None,
        )
        assert row is not None, f"failure contract row missing for {code.value}"
        assert any(f"`{value}`" in row for value in dispositions), (
            f"no disposition documented for {code.value}"
        )
        assert re.search(r"\|\s*(yes|no)\s*\|", row), (
            f"no retryability documented for {code.value}"
        )


def test_doc_documents_every_metric_with_its_exact_labels(
    contract: dict[str, object], doc: str
) -> None:
    for metric, labels in contract["METRIC_CONTRACT"].items():
        row = next(
            (line for line in doc.splitlines() if line.startswith("|") and f"`{metric}`" in line),
            None,
        )
        assert row is not None, f"metric table row missing for {metric}"
        for label in labels:
            assert f"`{label}`" in row, f"label {label} missing for {metric}"


def test_doc_states_the_monitoring_handoff_owners(doc: str) -> None:
    assert "## Monitoring handoff" in doc
    for owner in ("Control-plane team", "Messaging/platform team", "Security team"):
        assert owner in doc, f"monitoring handoff does not name {owner}"
    assert "RouteTarget.owner" in doc


def test_doc_forbids_high_cardinality_metric_labels(
    contract: dict[str, object], doc: str
) -> None:
    forbidden = {"tenant_id", "signal_id", "correlation_id", "idempotency_key"}
    for labels in contract["METRIC_CONTRACT"].values():
        assert not forbidden.intersection(labels)
    assert "Never label with tenant, signal, correlation, or idempotency identifiers" in doc

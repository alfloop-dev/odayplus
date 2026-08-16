"""The router contract doc must stay in sync with the executable contract.

`services/control-plane/router/README.md` is the documented routing contract
for P4-001. These tests parse the document's tables and compare them cell for
cell against `contract.py`, so the doc fails the build when it gains a stale
row, loses a route, mislabels a disposition, or advertises a metric label the
contract does not emit.
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

FORBIDDEN_LABELS = frozenset({"tenant_id", "signal_id", "correlation_id", "idempotency_key"})


@pytest.fixture(scope="module")
def contract() -> dict[str, object]:
    return runpy.run_path(str(CONTRACT_PATH))


@pytest.fixture(scope="module")
def doc() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def _code_spans(cell: str) -> list[str]:
    """Every backticked token in a table cell, in document order."""

    return re.findall(r"`([^`]+)`", cell)


def _markdown_tables(doc: str) -> list[list[dict[str, str]]]:
    """Parse every pipe table into a list of header-keyed rows."""

    tables: list[list[dict[str, str]]] = []
    headers: list[str] | None = None
    rows: list[dict[str, str]] = []

    for line in [*doc.splitlines(), ""]:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if headers is None:
                headers = cells
            elif not all(set(cell) <= set("-: ") for cell in cells):
                rows.append(dict(zip(headers, cells, strict=True)))
            continue
        if rows:
            tables.append(rows)
        headers = None
        rows = []

    return tables


def _table(doc: str, *columns: str) -> list[dict[str, str]]:
    matches = [rows for rows in _markdown_tables(doc) if set(columns) <= set(rows[0])]
    assert len(matches) == 1, f"expected exactly one table with columns {columns}, got {len(matches)}"
    return matches[0]


def test_doc_pins_contract_version_and_canonical_schema(
    contract: dict[str, object], doc: str
) -> None:
    assert f"`{contract['ROUTER_CONTRACT_VERSION']}`" in doc
    assert "services/research/schema.json" in doc


def test_doc_routing_table_matches_the_default_routes_exactly(
    contract: dict[str, object], doc: str
) -> None:
    documented = {
        (
            _code_spans(row["Domain"])[0],
            _code_spans(row["Intent"])[0],
        ): (
            _code_spans(row["Destination"])[0],
            _code_spans(row["Destination owner"])[0],
        )
        for row in _table(doc, "Domain", "Intent", "Destination", "Destination owner")
    }
    expected = {
        key: (target.name, target.owner)
        for key, target in contract["DEFAULT_ROUTES"].items()
    }

    assert documented == expected


def test_doc_failure_rows_match_each_code_disposition_and_retryability(
    contract: dict[str, object], doc: str
) -> None:
    documented = {
        _code_spans(row["Code"])[0]: (
            _code_spans(row["Disposition"]),
            row["Retryable"],
        )
        for row in _table(doc, "Code", "Disposition", "Retryable")
    }
    expected = {
        code.value: ([semantics.disposition.value], "yes" if semantics.retryable else "no")
        for code, semantics in contract["FAILURE_CONTRACT"].items()
    }

    assert documented == expected


def test_doc_metric_rows_declare_exactly_the_contract_labels(
    contract: dict[str, object], doc: str
) -> None:
    documented = {
        _code_spans(row["Metric"])[0]: _code_spans(row["Labels"])
        for row in _table(doc, "Metric", "Labels")
    }
    expected = {metric: list(labels) for metric, labels in contract["METRIC_CONTRACT"].items()}

    assert documented == expected


def test_doc_states_the_monitoring_handoff_owners(doc: str) -> None:
    assert "## Monitoring handoff" in doc
    for owner in ("Control-plane team", "Messaging/platform team", "Security team"):
        assert owner in doc, f"monitoring handoff does not name {owner}"
    assert "RouteTarget.owner" in doc


def test_doc_and_contract_both_forbid_high_cardinality_metric_labels(
    contract: dict[str, object], doc: str
) -> None:
    for labels in contract["METRIC_CONTRACT"].values():
        assert not FORBIDDEN_LABELS.intersection(labels)
    for row in _table(doc, "Metric", "Labels"):
        assert not FORBIDDEN_LABELS.intersection(_code_spans(row["Labels"])), (
            f"metric row {row['Metric']} documents an unbounded label"
        )
    assert "Never label with tenant, signal, correlation, or idempotency identifiers" in doc

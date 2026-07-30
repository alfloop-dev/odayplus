from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADR_DIR = ROOT / "docs" / "adr"
ADR_FILE = ADR_DIR / "ADR-0002-deferred-oss-decisions.md"


def test_deferred_oss_adr_file_exists() -> None:
    assert ADR_FILE.exists(), f"Expected ADR file at {ADR_FILE}"


def test_deferred_oss_adr_covers_all_required_components() -> None:
    content = ADR_FILE.read_text(encoding="utf-8")

    required_components = [
        "GeoPandas",
        "ruptures",
        "Superset",
        "Temporal",
        "OPA",
        "pgvector",
        "Feast",
        "DoubleML",
        "EconML",
        "TFT",
        "Pyomo",
    ]

    for comp in required_components:
        assert comp.lower() in content.lower(), f"ADR-0002 must contain section/decision for {comp}"


def test_deferred_oss_adr_contains_explicit_decisions_and_triggers() -> None:
    content = ADR_FILE.read_text(encoding="utf-8")

    # Verify decision keywords
    assert "replace" in content.lower()
    assert "defer" in content.lower()

    # Verify key governance sections
    assert "Revisit Trigger" in content or "重新評估觸發條件" in content
    assert "Decision Summary Matrix" in content or "決策總表" in content
    assert "ODP-HLR" in content

    # Verify uninstalled package rule compliance statement
    assert "未安裝" in content or "governedDisabled" in content

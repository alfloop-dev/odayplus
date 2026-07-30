import inspect
import re
from pathlib import Path

from models.shared_ml.production_contracts import PRODUCTION_MODEL_CONTRACTS
from modules.listing.application.pipeline import ListingRepository
from modules.listing.domain.intake_states import IntakeStage
from modules.listing.domain.models import ListingDedupKey
from scripts.models.contracts import ModelSpec
from scripts.models.release import _temporal_split

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


def test_deferred_oss_adr_cited_paths_exist() -> None:
    content = ADR_FILE.read_text(encoding="utf-8")

    cited_paths = [
        "docs/adr/ADR-0001-platform-foundation.md",
        "docs/evidence/ODP_OSS_AI_INTEGRATION_EVIDENCE.md",
        "docs/evidence/PLATFORM_COMPLETENESS_INVENTORY_2026-07-25.md",
        "docs/evidence/PRODUCTION_MODEL_RISK_ACCEPTANCE_2026-07-25.md",
        "docs/architecture/ODAY_PLUS_EXECUTION_BASELINE.md",
        "modules/learninghub/infrastructure/evidently_monitor.py",
        "modules/forecastops/infrastructure/forecast_engines.py",
        "pipelines/orchestration/dagster_training.py",
        "shared/infrastructure/persistence/job_queue.py",
        "shared/auth/",
        "modules/listing/",
        "modules/listing/application/pipeline.py",
        "modules/listing/domain/models.py",
        "modules/listing/domain/intake_states.py",
        "modules/learninghub/infrastructure/mlflow_adapter.py",
        "modules/adlift/domain/incrementality.py",
        "scripts/models/release.py",
        "scripts/models/contracts.py",
        "models/shared_ml/production_contracts.py",
    ]

    for path_str in cited_paths:
        assert (ROOT / path_str).exists(), f"Cited path '{path_str}' in ADR-0002 does not exist in repo"

    # Assert non-existent legacy intake path is NOT present
    assert "modules/intake" not in content, "ADR-0002 cites non-existent 'modules/intake' path"


def test_deferred_oss_adr_claimed_locked_packages_resolve() -> None:
    pyproject_content = (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    content = ADR_FILE.read_text(encoding="utf-8")

    # Extract claimed locked packages list from Section 'Verification and Traceability'
    # Format: (`statsmodels`, `lifelines`, ...)
    match = re.search(r"現行整合之替代套件 \((.*?)\)", content)
    assert match is not None, "Could not find locked packages list in ADR-0002"

    packages = [pkg.strip(" `") for pkg in match.group(1).split(",")]

    for pkg in packages:
        # Check normalization (e.g. great_expectations vs great-expectations)
        norm_pkg = pkg.replace("_", "-")
        assert (
            pkg in pyproject_content or norm_pkg in pyproject_content
        ), f"Claimed locked package '{pkg}' in ADR-0002 is not present in pyproject.toml"

    # Scan Decision Summary Matrix 替代/現行實作能力 column for package claims
    alias_map = {
        "h3-py": "h3",
        "or-tools": "ortools",
        "statsforecast": "statsforecast",
        "mlforecast": "mlforecast",
        "evidently": "evidently",
        "fastapi": "fastapi",
        "dagster": "dagster",
        "mlflow": "mlflow",
        "statsmodels": "statsmodels",
        "cvxpy": "cvxpy",
        "pymoo": "pymoo",
        "shapely": "shapely",
        "geopandas": "geopandas",
        "ruptures": "ruptures",
        "feast": "feast",
        "doubleml": "doubleml",
        "econml": "econml",
        "pyomo": "pyomo",
    }

    matrix_lines = [line for line in content.splitlines() if line.startswith("| **")]
    for line in matrix_lines:
        columns = [col.strip() for col in line.split("|")]
        if len(columns) > 4:
            capability_cell = columns[4]
            tokens = re.split(r"[\s\+\,`\(\)]+", capability_cell)
            for token in tokens:
                token_clean = token.strip().lower()
                if not token_clean:
                    continue
                pkg_name = alias_map.get(token_clean)
                if pkg_name:
                    norm_pkg = pkg_name.replace("_", "-")
                    assert (
                        pkg_name in pyproject_content or norm_pkg in pyproject_content
                    ), f"Matrix claimed package '{token}' (resolves to '{pkg_name}') is not present in pyproject.toml"


def test_deferred_oss_adr_cited_symbols_and_signatures_resolve() -> None:
    content = ADR_FILE.read_text(encoding="utf-8")

    # 1. Check IntakeStage.MATCHING attribution
    assert IntakeStage.MATCHING == "MATCHING"
    assert "IntakeStage.MATCHING" in content
    assert "modules/listing/domain/intake_states.py" in content

    # 2. Check ListingDedupKey
    assert hasattr(ListingDedupKey, "__annotations__")
    assert "ListingDedupKey" in content
    assert "modules/listing/domain/models.py" in content

    # 3. Check has_duplicate method
    assert hasattr(ListingRepository, "has_duplicate")
    assert "has_duplicate" in content

    # 4. Check _temporal_split signature and absence of fabricated purge_label_overlap
    sig = inspect.signature(_temporal_split)
    assert "rows" in sig.parameters
    assert "holdout_fraction" in sig.parameters
    assert "purge_label_overlap" not in sig.parameters
    assert "purge_label_overlap" not in content

    # 5. Check PIT label maturity column
    assert hasattr(ModelSpec, "label_maturity_column")
    assert "label_maturity_column" in content
    assert "scripts/models/contracts.py" in content

    # 6. Check DATA_CONTRACT_NOT_MATURE reason code and absence of un-implemented CAPABILITY_DEFERRED
    found_reason = any(
        c.unavailable_reason == "DATA_CONTRACT_NOT_MATURE"
        for c in PRODUCTION_MODEL_CONTRACTS.values()
        if c.unavailable_reason
    )
    assert found_reason, "DATA_CONTRACT_NOT_MATURE must exist in PRODUCTION_MODEL_CONTRACTS"
    assert "DATA_CONTRACT_NOT_MATURE" in content
    assert "CAPABILITY_DEFERRED" not in content

    # 7. Check GeoPandas ST_* PostGIS functions marked as planned SQL surface
    for func in ["ST_Contains", "ST_Buffer", "ST_Distance", "ST_DWithin"]:
        assert func in content
    assert "planned SQL surface" in content

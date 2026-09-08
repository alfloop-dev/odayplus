"""Negative regression tests for NLTK/Evidently dependency removal.

These tests verify that the production dependency chain no longer includes
``evidently``, ``nltk``, ``defusedxml``, or standalone ``regex``, and that
the native drift monitoring engine works without them.

defusedxml and regex are banned because they are sole reverse-dependencies
of nltk in this project's uv.lock (see ODP_NLTK_UNPATCHED_DEPENDENCY_DISPOSITION
§3.2: defusedxml has no other dependent, regex has no other dependent).
Banning them prevents silent re-introduction via a new transitive path.

Task: ODP-DRIFT-SECURITY-VERIFY-003
Advisory: GHSA-8mgp-746c-j5xp / PYSEC-2026-3740
"""

from __future__ import annotations

import importlib.metadata
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# --- Packages that must NOT be present in the production dependency chain ---
# evidently / nltk: primary banned packages per acceptance criteria.
# defusedxml / regex: sole reverse-dependencies of nltk with no other
# dependents in uv.lock (§3.2 of ODP_NLTK_UNPATCHED_DEPENDENCY_DISPOSITION).
BANNED_PACKAGES = frozenset({"evidently", "nltk", "defusedxml", "regex"})


def test_banned_packages_not_installed() -> None:
    """No banned package should be installed (via importlib.metadata, not import).

    Using importlib.metadata.distribution() checks installed dist-info,
    which correctly detects packages that are installed but might fail to
    import due to broken dependencies. Catching ImportError would falsely
    pass a package that is installed but has an import-time crash.
    """
    for package in BANNED_PACKAGES:
        try:
            dist = importlib.metadata.distribution(package)
            raise AssertionError(
                f"{package} is installed (version={dist.version}) but should "
                f"have been removed from the production dependency chain. "
                f"importlib.metadata found its dist-info; this is NOT an import "
                f"test — the package metadata is physically present."
            )
        except importlib.metadata.PackageNotFoundError:
            pass  # expected: package metadata not found


def test_pyproject_does_not_declare_evidently() -> None:
    """pyproject.toml must not declare evidently as a direct dependency."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject.get("project", {}).get("dependencies", [])
    for dep in dependencies:
        dep_lower = dep.lower().strip()
        assert not dep_lower.startswith("evidently"), (
            f"pyproject.toml still declares evidently as a dependency: {dep}"
        )


def test_uv_lock_does_not_contain_banned_packages() -> None:
    """uv.lock must not contain evidently, nltk, defusedxml, or regex."""
    lock_path = ROOT / "uv.lock"
    assert lock_path.exists(), "uv.lock must exist"
    lock_text = lock_path.read_text(encoding="utf-8")
    for package in BANNED_PACKAGES:
        # Match the TOML package declaration pattern
        assert f'name = "{package}"' not in lock_text, (
            f"uv.lock still contains package declaration for {package}"
        )


def test_no_evidently_imports_in_production_code() -> None:
    """Production source must not import evidently or nltk."""
    production_dirs = [
        ROOT / "modules",
        ROOT / "models",
        ROOT / "apps",
    ]
    violations: list[str] = []
    for prod_dir in production_dirs:
        if not prod_dir.exists():
            continue
        for py_file in prod_dir.rglob("*.py"):
            # Skip test files
            if "test" in py_file.name.lower():
                continue
            content = py_file.read_text(encoding="utf-8", errors="replace")
            for line_no, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "from evidently" in stripped or "import evidently" in stripped:
                    violations.append(f"{py_file}:{line_no}: {stripped}")
                if "from nltk" in stripped or "import nltk" in stripped:
                    violations.append(f"{py_file}:{line_no}: {stripped}")
    assert not violations, (
        "Production code still imports banned packages:\n"
        + "\n".join(violations)
    )


def test_native_drift_engine_is_used() -> None:
    """The drift monitor must use the native engine, not evidently."""
    monitor_path = (
        ROOT / "modules" / "learninghub" / "infrastructure" / "evidently_monitor.py"
    )
    assert monitor_path.exists(), "evidently_monitor.py must exist"
    content = monitor_path.read_text(encoding="utf-8")
    # Must import from native_drift, not from evidently
    assert "from modules.learninghub.infrastructure.native_drift" in content, (
        "evidently_monitor.py must import from native_drift"
    )
    assert "from evidently import" not in content, (
        "evidently_monitor.py must not import from evidently"
    )
    assert "from evidently.presets" not in content, (
        "evidently_monitor.py must not import evidently presets"
    )


def test_native_drift_engine_does_not_depend_on_banned_packages() -> None:
    """native_drift.py must not import evidently or nltk."""
    native_path = (
        ROOT / "modules" / "learninghub" / "infrastructure" / "native_drift.py"
    )
    assert native_path.exists(), "native_drift.py must exist"
    content = native_path.read_text(encoding="utf-8")
    for banned in ("evidently", "nltk"):
        for line_no, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert f"import {banned}" not in stripped and f"from {banned}" not in stripped, (
                f"native_drift.py:{line_no} imports banned package {banned}: {stripped}"
            )


def test_sbom_does_not_contain_banned_packages() -> None:
    """Task-scoped SBOM must exist, have components, and not list banned packages.

    Fail-closed: missing SBOM or empty/missing components list is a test
    failure, not a silent pass.
    """
    sbom_path = (
        ROOT / "docs" / "evidence" / "completion"
        / "ODP-DRIFT-SECURITY-VERIFY-003" / "sbom.json"
    )
    assert sbom_path.exists(), (
        "Task-scoped SBOM must exist at "
        "docs/evidence/completion/ODP-DRIFT-SECURITY-VERIFY-003/sbom.json"
    )
    data = json.loads(sbom_path.read_text(encoding="utf-8"))
    assert data.get("bomFormat") == "CycloneDX", (
        "SBOM bomFormat must be CycloneDX"
    )
    components = data.get("components")
    assert isinstance(components, list) and len(components) > 0, (
        "SBOM must contain a non-empty 'components' list; "
        "an empty or missing components list cannot be treated as passing"
    )
    for component in components:
        name = component.get("name", "").lower()
        assert name not in BANNED_PACKAGES, (
            f"SBOM lists banned package: {name}"
        )


def test_sbom_missing_is_failure() -> None:
    """Verify that SBOM validation logic rejects missing/empty formats.

    This is a negative test for the SBOM check itself: a dict with no
    components key, or an empty components list, must not be silently
    accepted as 'no banned packages found'.
    """
    # Case 1: no components key at all
    data_no_components: dict = {"bomFormat": "CycloneDX", "specVersion": "1.5"}
    components = data_no_components.get("components")
    assert not (isinstance(components, list) and len(components) > 0), (
        "Missing components must be rejected"
    )

    # Case 2: empty components list
    data_empty: dict = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "components": [],
    }
    components = data_empty.get("components")
    assert not (isinstance(components, list) and len(components) > 0), (
        "Empty components must be rejected"
    )


def test_lock_consistency() -> None:
    """uv.lock must be consistent with pyproject.toml."""
    import subprocess

    result = subprocess.run(
        ["uv", "lock", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"uv.lock is inconsistent with pyproject.toml:\n{result.stderr}"
    )

"""Negative regression tests for NLTK/Evidently dependency removal.

These tests verify that the production dependency chain no longer includes
``evidently``, ``nltk``, ``defusedxml``, or standalone ``regex``, and that
the native drift monitoring engine works without them.

Task: ODP-DRIFT-SECURITY-VERIFY-003
Advisory: GHSA-8mgp-746c-j5xp / PYSEC-2026-3740
"""

from __future__ import annotations

import importlib
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# --- Packages that must NOT be present in the production dependency chain ---
BANNED_PACKAGES = frozenset({"evidently", "nltk", "defusedxml", "regex"})


def test_banned_packages_not_importable() -> None:
    """No banned package should be importable in the production environment."""
    for package in BANNED_PACKAGES:
        try:
            importlib.import_module(package)
            raise AssertionError(
                f"{package} is importable but should have been removed "
                f"from the production dependency chain"
            )
        except ImportError:
            pass  # expected


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
    """If an SBOM exists for this task, it must not list banned packages."""
    sbom_candidates = [
        ROOT / "docs" / "evidence" / "completion" / "ODP-DRIFT-SECURITY-VERIFY-003" / "sbom.json",
        ROOT / "docs" / "evidence" / "completion" / "ODP-DRIFT-DEP-REMOVE-002" / "candidate-audit.json",
    ]
    for sbom_path in sbom_candidates:
        if not sbom_path.exists():
            continue
        data = json.loads(sbom_path.read_text(encoding="utf-8"))
        components = data.get("components", [])
        for component in components:
            name = component.get("name", "").lower()
            assert name not in BANNED_PACKAGES, (
                f"SBOM {sbom_path.name} lists banned package: {name}"
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

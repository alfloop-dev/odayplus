from __future__ import annotations

from pathlib import Path

import pytest

from delivery_toolchain.governance import check_code_boundaries as guard


def manifest_fixture() -> dict:
    return {
        "retention_classes": {
            "product_required": {"removable": False, "removal_condition": "never"},
            "delivery_only": {"removable": True, "removal_condition": "retired"},
        },
        "boundaries": {
            "product": {
                "include": ["product/*.py"],
                "retention_class": "product_required",
                "is_product_runtime": True,
                "allowed_import_scopes": ["product"],
            },
            "tooling": {
                "include": ["tooling/*.py"],
                "retention_class": "delivery_only",
                "is_product_runtime": False,
                "allowed_import_scopes": ["product", "tooling"],
            },
        },
        "artifact_profiles": {
            "production": {"include_scopes": ["product"]},
            "engineering": {"include_scopes": ["product", "tooling"]},
        },
    }


def test_every_file_must_match_exactly_one_boundary() -> None:
    manifest = manifest_fixture()
    manifest["boundaries"]["duplicate"] = {
        **manifest["boundaries"]["product"],
        "include": ["product/a.py"],
    }

    classified, errors = guard.classify_files(
        ["product/a.py", "unknown.py"],
        manifest,
    )

    assert classified == []
    assert any("multiple boundaries" in error for error in errors)
    assert any("unclassified" in error for error in errors)


def test_production_profile_rejects_tooling_scope() -> None:
    manifest = manifest_fixture()
    manifest["artifact_profiles"]["production"]["include_scopes"].append("tooling")

    errors = guard.validate_artifact_profiles(manifest)

    assert errors == ["production artifact contains non-product scopes: ['tooling']"]


def test_product_cannot_import_development_tooling(tmp_path: Path) -> None:
    manifest = manifest_fixture()
    (tmp_path / "product").mkdir()
    (tmp_path / "tooling").mkdir()
    (tmp_path / "product/a.py").write_text("from tooling.helper import build\n", encoding="utf-8")
    (tmp_path / "tooling/helper.py").write_text("def build(): pass\n", encoding="utf-8")
    classified, errors = guard.classify_files(
        ["product/a.py", "tooling/helper.py"],
        manifest,
    )

    assert errors == []
    assert guard.validate_import_boundaries(tmp_path, classified, manifest) == [
        "forbidden import: product/a.py (product) imports tooling.helper from tooling"
    ]


def test_tooling_may_import_product_public_code(tmp_path: Path) -> None:
    manifest = manifest_fixture()
    (tmp_path / "product").mkdir()
    (tmp_path / "tooling").mkdir()
    (tmp_path / "product/a.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "tooling/helper.py").write_text("from product.a import VALUE\n", encoding="utf-8")
    classified, errors = guard.classify_files(
        ["product/a.py", "tooling/helper.py"],
        manifest,
    )

    assert errors == []
    assert guard.validate_import_boundaries(tmp_path, classified, manifest) == []


def test_checked_in_repository_has_complete_boundary_coverage() -> None:
    manifest = guard.load_manifest()
    classified, errors = guard.validate_repository(
        guard.ROOT,
        manifest,
        check_inventory=False,
    )

    assert errors == []
    assert classified
    assert sum(entry.is_product_runtime for entry in classified) > 0
    assert all(entry.verified_scope for entry in classified if entry.boundary == "verification")


def test_verification_ownership_uses_specific_rule_before_default() -> None:
    manifest = manifest_fixture()
    manifest["boundaries"]["verification"] = {
        "include": ["tests/**/*.py"],
        "retention_class": "delivery_only",
        "is_product_runtime": False,
        "allowed_import_scopes": ["product", "tooling", "verification"],
    }
    manifest["verification_ownership"] = {
        "default_scope": "product",
        "rules": [{"scope": "tooling", "include": ["tests/tooling/**/*.py"]}],
    }

    classified, errors = guard.classify_files(
        ["tests/product/test_api.py", "tests/tooling/test_gate.py"],
        manifest,
    )

    assert errors == []
    assert {entry.path: entry.verified_scope for entry in classified} == {
        "tests/product/test_api.py": "product",
        "tests/tooling/test_gate.py": "tooling",
    }


def test_invalid_python_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "broken.py"
    path.write_text("def broken(:\n", encoding="utf-8")

    with pytest.raises(ValueError, match="cannot parse"):
        guard.imported_modules(path)

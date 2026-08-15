from __future__ import annotations

from delivery_toolchain.governance import classify_change_review_scope as scope


def manifest_fixture() -> dict:
    return {
        "development_tooling": {
            "include_prefixes": ["tools/"],
            "include_path_prefixes": ["scripts/tooling/"],
            "include_paths": ["config/tooling.json"],
        }
    }


def test_declared_tooling_paths_auto_qualify() -> None:
    result = scope.classify_paths(
        ["tools/check.py", "scripts/tooling/rollout.py", "config/tooling.json"],
        manifest_fixture(),
    )

    assert result["scope"] == "development_tooling"
    assert result["non_tooling_paths"] == []


def test_product_or_unknown_path_requires_product_review() -> None:
    result = scope.classify_paths(
        ["tools/check.py", "apps/api/main.py", "docs/product-contract.md"],
        manifest_fixture(),
    )

    assert result == {
        "scope": "product_or_mixed",
        "paths": ["apps/api/main.py", "docs/product-contract.md", "tools/check.py"],
        "non_tooling_paths": ["apps/api/main.py", "docs/product-contract.md"],
    }


def test_empty_change_is_not_automatically_approved() -> None:
    assert scope.classify_paths([], manifest_fixture())["scope"] == "product_or_mixed"


def test_supervisor_runtime_docs_and_ai_status_tests_are_tooling() -> None:
    manifest = scope.load_manifest()

    result = scope.classify_paths(
        [
            "docs/runbooks/supervisor-runtime-rollout.md",
            "scripts/test_ai_status.py",
        ],
        manifest,
    )

    assert result["scope"] == "development_tooling"
    assert result["non_tooling_paths"] == []

"""Keep SBOM edges bound to npm's installed dependency versions."""

import json

from delivery_toolchain.security import generate_sbom as sbom


def test_file_dependency_graph_resolves_nested_versions(monkeypatch, tmp_path):
    packages = {
        "": {"dependencies": {"lint-config": "1.0.0"}},
        "node_modules/lint-config": {
            "version": "1.0.0",
            "license": "MIT",
            "dependencies": {"lint-plugin": "1.0.0"},
        },
        "node_modules/lint-plugin": {"link": True, "resolved": "packages/third-party/plugin"},
        "packages/third-party/plugin": {
            "name": "lint-plugin",
            "version": "1.0.0-local.1",
            "license": "MIT",
            "dependencies": {"matcher": "2.0.0"},
        },
        "packages/third-party/plugin/node_modules/matcher": {
            "version": "2.0.0",
            "license": "MIT",
            "dependencies": {"balanced": "1.0.0"},
        },
        "packages/third-party/plugin/node_modules/balanced": {"version": "1.0.0", "license": "MIT"},
        "node_modules/matcher": {"version": "4.0.0", "license": "MIT"},
        "node_modules/balanced": {"version": "4.0.0", "license": "MIT"},
    }
    lock = tmp_path / "package-lock.json"
    lock.write_text(json.dumps({"packages": packages}))
    monkeypatch.setattr(sbom, "PACKAGE_LOCK", lock)
    uv_lock = tmp_path / "uv.lock"
    uv_lock.write_text('[[package]]\nname = "python-control"\nversion = "1.0.0"\n')
    monkeypatch.setattr(sbom, "UV_LOCK", uv_lock)
    monkeypatch.setattr(sbom, "get_installed_npm_metadata", lambda: {})
    result = sbom.generate_sbom()
    edges = {item["ref"]: item["dependsOn"] for item in result["dependencies"]}
    assert edges["pkg:npm/lint-config@1.0.0"] == ["pkg:npm/lint-plugin@1.0.0-local.1"]
    assert edges["pkg:npm/lint-plugin@1.0.0-local.1"] == ["pkg:npm/matcher@2.0.0"]
    assert edges["pkg:npm/matcher@2.0.0"] == ["pkg:npm/balanced@1.0.0"]
    root = result["metadata"]["component"]["purl"]
    assert edges[root] == ["pkg:npm/lint-config@1.0.0"]


def test_dependency_resolution_does_not_borrow_an_unrelated_nested_package():
    packages = {"node_modules/unrelated/node_modules/matcher": {"version": "2.0.0"}}
    assert sbom._resolve_npm_package_path("matcher", "node_modules/requester", packages) is None


def test_dependency_resolution_rejects_a_lockfile_link_cycle():
    packages = {
        "node_modules/plugin": {"link": True, "resolved": "packages/plugin"},
        "packages/plugin": {"link": True, "resolved": "node_modules/plugin"},
    }
    assert sbom._resolve_npm_package_path("plugin", "", packages) is None


def test_installed_metadata_tracks_file_dependency_real_paths(monkeypatch, tmp_path):
    plugin = tmp_path / "packages/third-party/plugin"
    matcher = plugin / "node_modules/matcher"
    matcher.mkdir(parents=True)
    (plugin / "package.json").write_text(json.dumps({"name": "plugin", "license": "MIT"}))
    (matcher / "package.json").write_text(json.dumps({"name": "matcher", "license": "ISC"}))
    modules = tmp_path / "node_modules"
    modules.mkdir()
    (modules / "plugin").symlink_to(plugin, target_is_directory=True)
    monkeypatch.setattr(sbom, "ROOT", tmp_path)
    monkeypatch.setattr(sbom, "NODE_MODULES", modules)
    metadata = sbom.get_installed_npm_metadata()
    assert metadata["packages/third-party/plugin"]["license"] == "MIT"
    assert metadata["packages/third-party/plugin/node_modules/matcher"]["license"] == "ISC"

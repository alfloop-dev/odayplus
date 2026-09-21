"""The shipped OSS notice must keep naming what we actually ship.

The legal policy and LGPL disposition remain PROPOSED under
ODP-PLAN-OSS-LEGAL-POLICY-001 until an authoritative external receipt is resolved.
The notice ensures all third-party components and obligations are tracked and
reconciled against installed trees without unreviewed drift.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NOTICE = ROOT / "NOTICE-THIRD-PARTY.md"
GENERATOR = ROOT / "delivery_toolchain/security/generate_oss_notice.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_oss_notice", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_oss_notice"] = module
    spec.loader.exec_module(module)
    return module


def test_notice_is_checked_in() -> None:
    assert NOTICE.exists(), (
        "NOTICE-THIRD-PARTY.md must be committed -- it is what we distribute to "
        "satisfy the attribution obligations, not a build artifact."
    )


def test_notice_matches_the_installed_trees() -> None:
    if not (ROOT / "node_modules").is_dir():
        pytest.skip("node_modules absent; licences can only be read from an install")
    generator = _load_generator()
    assert NOTICE.read_text(encoding="utf-8") == generator.build(), (
        "NOTICE-THIRD-PARTY.md is stale. Run delivery_toolchain/security/generate_oss_notice.py."
    )


def test_lgpl_components_are_named_with_their_obligation() -> None:
    """The three LGPL components are the reason this notice exists."""
    text = NOTICE.read_text(encoding="utf-8")
    for package in (
        "@img/sharp-libvips-linux-x64",
        "@img/sharp-libvips-linuxmusl-x64",
        "psycopg2-binary",
    ):
        assert package in text, f"{package} is LGPL and must be named in the notice"
    assert "Weak copyleft" in text


def test_attribution_only_licences_are_named_too() -> None:
    """Apache-2.0 NOTICE retention and caniuse-lite's CC-BY were both unmet before."""
    text = NOTICE.read_text(encoding="utf-8")
    assert "caniuse-lite" in text
    assert "Attribution required" in text
    assert "### Apache-2.0" in text


def test_first_party_packages_are_not_attributed_to_a_third_party() -> None:
    """@oday-plus/* are ours; listing them here would misrepresent our own code
    as an unidentified third-party dependency. They are declared UNLICENSED in
    their own manifests, which is a first-party marker and not a third-party
    licence grant, so they still do not belong in a third-party notice."""
    text = NOTICE.read_text(encoding="utf-8")
    assert "@oday-plus/" not in text


# ---------------------------------------------------------------------------
# D05 (ODP-OSS-DECISION-PACK-001, user choice B): the eight first-party
# workspace packages carry an explicit UNLICENSED marker so a scanner can tell
# our own code apart from a third party whose licence is genuinely unknown.
# ---------------------------------------------------------------------------

FIRST_PARTY_WORKSPACES = {
    "packages/ui": "@oday-plus/ui",
    "packages/design-tokens": "@oday-plus/design-tokens",
    "packages/testkit": "@oday-plus/testkit",
    "packages/ui-domain": "@oday-plus/ui-domain",
    "packages/domain-types": "@oday-plus/domain-types",
    "packages/schemas": "@oday-plus/schemas",
    "apps/web": "@oday-plus/web",
    "packages/openapi-client": "@oday-plus/openapi-client",
}


def test_the_eight_first_party_manifests_declare_unlicensed() -> None:
    """Each package the decision pack names must still exist under that name and
    carry the marker. A renamed or removed workspace is a decision-scope
    question, not something to silently re-point at a different package."""
    for workspace, expected_name in FIRST_PARTY_WORKSPACES.items():
        manifest = ROOT / workspace / "package.json"
        assert manifest.exists(), f"{workspace} named by D05 no longer exists"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        assert data.get("name") == expected_name, (
            f"{workspace} is now {data.get('name')!r}; D05 was decided for "
            f"{expected_name!r} and does not carry over to a renamed package"
        )
        assert data.get("license") == "UNLICENSED", (
            f"{expected_name} must declare license UNLICENSED (D05 choice B)"
        )


def test_the_lockfile_carries_the_same_marker_as_the_manifests() -> None:
    """The lockfile is what the SBOM reads, so a marker only in package.json
    would leave the published inventory saying something different."""
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    packages = lock["packages"]
    for workspace, expected_name in FIRST_PARTY_WORKSPACES.items():
        entry = packages.get(workspace)
        assert entry is not None, f"{workspace} is not a lockfile workspace entry"
        assert entry.get("name") == expected_name
        assert entry.get("license") == "UNLICENSED", (
            f"package-lock.json entry for {workspace} is out of sync with its "
            "package.json; run npm install --package-lock-only"
        )


def test_a_third_party_may_not_inherit_the_first_party_exclusion() -> None:
    """Synthetic fixture, not an installed dependency: a registry package whose
    name merely begins with our root name must stay in the notice. A bare
    string-prefix test would drop it, and dropping it hides a third party of
    unknown licence from both the notice and the policy gate that reads it."""
    generator = _load_generator()

    assert generator.is_first_party("@oday-plus/ui")
    assert generator.is_first_party("oday-plus")
    assert not generator.is_first_party("oday-plus-lookalike")
    assert not generator.is_first_party("@oday-plus-lookalike/ui")


def test_lookalike_third_party_is_collected_and_fails_the_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end on the same synthetic fixture: collected as UNKNOWN, and the
    policy evaluation fails closed on it. Our own workspace package in the same
    tree is still excluded."""
    generator = _load_generator()

    node_modules = tmp_path / "node_modules"
    lookalike = node_modules / "oday-plus-lookalike"
    lookalike.mkdir(parents=True)
    (lookalike / "package.json").write_text(
        json.dumps({"name": "oday-plus-lookalike", "version": "9.9.9"}),
        encoding="utf-8",
    )
    ours = node_modules / "@oday-plus" / "ui"
    ours.mkdir(parents=True)
    (ours / "package.json").write_text(
        json.dumps({"name": "@oday-plus/ui", "version": "0.1.0", "license": "UNLICENSED"}),
        encoding="utf-8",
    )

    lock = tmp_path / "package-lock.json"
    lock.write_text(
        json.dumps(
            {
                "packages": {
                    "": {"name": "oday-plus"},
                    "node_modules/oday-plus-lookalike": {"version": "9.9.9"},
                    "node_modules/@oday-plus/ui": {"resolved": "packages/ui", "link": True},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(generator, "PACKAGE_LOCK", lock)

    collected = generator.collect_npm(node_modules)
    assert [(c.name, c.license) for c in collected] == [("oday-plus-lookalike", "UNKNOWN")]

    result = generator.evaluate_policy(components=collected)
    assert result["status"] == "FAIL"
    assert any(
        v["component"].name == "oday-plus-lookalike" and "Unknown" in v["reason"]
        for v in result["violations"]
    )


def test_generator_excludes_operating_system_python_packages() -> None:
    """Enumerating site-packages sweeps in Ubuntu's own GPL tooling, which this
    project does not depend on. uv.lock is what bounds the python side."""
    text = NOTICE.read_text(encoding="utf-8")
    for os_package in ("cloud-init", "ubuntu-pro-client", "apparmor", "ufw"):
        assert os_package not in text, (
            f"{os_package} is an operating system package, not a dependency"
        )

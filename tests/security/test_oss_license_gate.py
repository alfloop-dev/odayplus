"""OSS License and Release Gate Acceptance and Negative Tests (ODP-OSS-LICENSE-GATE-002)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from delivery_toolchain.security.attestation import (
    generate_attestation,
    verify_attestation,
)
from delivery_toolchain.security.generate_oss_notice import (
    AuthoritativeReceiptVerification,
    AuthoritativeReceiptVerifier,
    Component,
    FixedAuthoritativeReceiptVerifier,
    collect_npm,
    collect_python,
    evaluate_compound_expression,
    evaluate_policy,
    exemption_content_sha256,
    validate_exemption,
)
from delivery_toolchain.security.generate_sbom import generate_sbom, get_repo_release_digests

POLICY_PATH = ROOT / "docs/security/license_policy.json"
EXEMPTIONS_PATH = ROOT / "docs/security/license_exemptions.json"
RELEASE_BINDINGS_PATH = ROOT / "docs/security/release_bindings.json"
NOTICE_PATH = ROOT / "NOTICE-THIRD-PARTY.md"
SBOM_PATH = ROOT / "docs/evidence/sbom.json"

# The directory basenames of the eight first-party workspace members named by
# D05. Each is also a real package name on the public npm registry, which is
# why deriving a component name from the lockfile path collided with one.
FIRST_PARTY_WORKSPACE_DIRS = (
    "ui",
    "design-tokens",
    "testkit",
    "ui-domain",
    "domain-types",
    "schemas",
    "web",
    "openapi-client",
)


# -----------------------------------------------------------------------------
# Acceptance 1: CycloneDX SBOM with licenses, purls, suppliers, hashes, graph, scopes, digests
# -----------------------------------------------------------------------------


def test_cyclonedx_sbom_spec_and_components_present() -> None:
    sbom = generate_sbom()
    assert sbom.get("bomFormat") == "CycloneDX"
    assert sbom.get("specVersion") == "1.5"
    assert sbom.get("version") == 1
    components = sbom.get("components", [])
    assert len(components) > 0, "SBOM must contain cataloged components"


def test_sbom_licenses_purls_suppliers_hashes_populated() -> None:
    sbom = generate_sbom()
    components = sbom.get("components", [])
    for comp in components:
        assert "name" in comp and comp["name"], "Component missing name"
        assert "version" in comp and comp["version"], "Component missing version"
        assert "purl" in comp and comp["purl"].startswith("pkg:"), (
            f"Invalid purl: {comp.get('purl')}"
        )
        assert "licenses" in comp and len(comp["licenses"]) > 0, (
            f"Component missing licenses: {comp['name']}"
        )
        assert comp.get("scope") in ("required", "optional"), f"Invalid scope: {comp.get('scope')}"
        # Most components have hashes from package-lock.json or uv.lock
        if "hashes" in comp:
            for h in comp["hashes"]:
                assert "alg" in h and "content" in h and h["content"]


def test_sbom_dependency_graph_and_scopes_valid() -> None:
    sbom = generate_sbom()
    deps = sbom.get("dependencies", [])
    assert len(deps) > 0, "SBOM must have a dependency graph"
    root_node = next((d for d in deps if "odayplus" in d.get("ref", "")), None)
    assert root_node is not None, "Root application dependency node missing from SBOM"
    root_depends_on = root_node.get("dependsOn", [])
    assert len(root_depends_on) > 0, "Root node must declare direct dependencies"

    # Must contain direct Python dependencies from pyproject.toml
    assert any(p.startswith("pkg:pypi/fastapi@") for p in root_depends_on), (
        "Python root dependencies must be present in root dependsOn"
    )

    # Must contain direct third-party npm dependencies from workspaces (e.g. apps/web)
    for expected_web_dep in ("next", "react", "argon2", "maplibre-gl", "pg"):
        assert any(p.startswith(f"pkg:npm/{expected_web_dep}@") for p in root_depends_on), (
            f"Workspace third-party dependency {expected_web_dep} must be connected to root dependsOn"
        )

    # First-party workspace packages must NOT appear in root dependsOn or in components
    assert not any("@oday-plus/" in p for p in root_depends_on), (
        f"First-party packages must not be in root dependsOn: {root_depends_on}"
    )


def test_sbom_workspace_third_party_dependencies_retained_in_root_synthetic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Synthetic fixture: when first-party workspace components are excluded from
    the SBOM catalogue, their third-party direct dependencies must still be resolved
    and connected to the application root node, and transitive edges must be preserved."""
    import delivery_toolchain.security.generate_sbom as sbom_mod

    synth_lock = tmp_path / "package-lock.json"
    synth_lock.write_text(
        json.dumps(
            {
                "name": "oday-plus",
                "version": "0.1.0",
                "lockfileVersion": 3,
                "packages": {
                    "": {
                        "name": "oday-plus",
                        "workspaces": ["apps/web", "packages/ui"],
                    },
                    "apps/web": {
                        "name": "@oday-plus/web",
                        "version": "0.1.0",
                        "license": "UNLICENSED",
                        "dependencies": {
                            "next": "15.5.21",
                            "@oday-plus/ui": "0.1.0",
                        },
                    },
                    "packages/ui": {
                        "name": "@oday-plus/ui",
                        "version": "0.1.0",
                        "license": "UNLICENSED",
                        "dependencies": {
                            "clsx": "2.1.1",
                        },
                    },
                    "node_modules/next": {
                        "version": "15.5.21",
                        "license": "MIT",
                        "dependencies": {
                            "postcss": "8.4.49",
                        },
                    },
                    "node_modules/postcss": {
                        "version": "8.4.49",
                        "license": "MIT",
                    },
                    "node_modules/clsx": {
                        "version": "2.1.1",
                        "license": "MIT",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    synth_pyproject = tmp_path / "pyproject.toml"
    synth_pyproject.write_text(
        '[project]\nname = "odayplus"\nversion = "0.1.0"\ndependencies = []\n',
        encoding="utf-8",
    )
    synth_uv_lock = tmp_path / "uv.lock"
    synth_uv_lock.write_text("", encoding="utf-8")

    monkeypatch.setattr(sbom_mod, "PACKAGE_LOCK", synth_lock)
    monkeypatch.setattr(sbom_mod, "PYPROJECT", synth_pyproject)
    monkeypatch.setattr(sbom_mod, "UV_LOCK", synth_uv_lock)
    monkeypatch.setattr(sbom_mod, "NODE_MODULES", tmp_path / "node_modules_absent")

    result = sbom_mod.generate_sbom()

    # 1. First-party packages are excluded from components catalogue
    comp_names = {c["name"] for c in result["components"]}
    assert "@oday-plus/web" not in comp_names
    assert "@oday-plus/ui" not in comp_names
    assert "web" not in comp_names
    assert "ui" not in comp_names
    assert {"next", "postcss", "clsx"}.issubset(comp_names)

    # 2. Root dependency node retains workspace third-party dependencies
    deps = result["dependencies"]
    root_node = next(d for d in deps if "odayplus" in d.get("ref", ""))
    root_deps = root_node.get("dependsOn", [])
    assert "pkg:npm/next@15.5.21" in root_deps
    assert "pkg:npm/clsx@2.1.1" in root_deps
    assert not any("@oday-plus" in d for d in root_deps)

    # 3. Transitive component edge is preserved
    next_node = next((d for d in deps if d.get("ref") == "pkg:npm/next@15.5.21"), None)
    assert next_node is not None
    assert "pkg:npm/postcss@8.4.49" in next_node.get("dependsOn", [])


def test_sbom_container_and_repository_release_digests() -> None:
    sbom = generate_sbom()
    props = {p["name"]: p["value"] for p in sbom.get("metadata", {}).get("properties", [])}
    assert "git-sha" in props
    assert "sbom-content-digest" in props and props["sbom-content-digest"].startswith("sha256:")
    assert "container-base-images" in props
    base_images = json.loads(props["container-base-images"])
    assert "python:3.12-slim" in base_images
    assert "node:22-slim" in base_images

    assert "repository-release-digests" in props
    repo_digests = json.loads(props["repository-release-digests"])
    assert "alfloop-dev/odayplus" in repo_digests
    assert repo_digests == get_repo_release_digests()
    assert "alfloop-dev/oday-data-platform" in repo_digests


def test_release_bindings_are_explicit_and_resolvable() -> None:
    bindings = json.loads(RELEASE_BINDINGS_PATH.read_text(encoding="utf-8"))
    assert set(bindings["repositories"]) == {
        "alfloop-dev/odayplus",
        "alfloop-dev/oday-data-platform",
    }
    for record in bindings["repositories"].values():
        assert record["ref"].startswith("refs/")
        assert record["source"].startswith("https://github.com/")
        assert len(record["digest"]) == 40


def test_sbom_check_cli_passes() -> None:
    res = subprocess.run(
        [sys.executable, "delivery_toolchain/security/generate_sbom.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"generate_sbom.py --check failed:\n{res.stdout}\n{res.stderr}"


# -----------------------------------------------------------------------------
# Acceptance 2: Reconciliation against NOTICE, SBOM, and License Policy
# -----------------------------------------------------------------------------


def test_notice_reconciles_with_sbom_and_installed_trees() -> None:
    npm_comps = collect_npm(ROOT / "node_modules")
    py_comps = collect_python()
    all_comps = npm_comps + py_comps

    assert len(all_comps) > 0
    notice_text = NOTICE_PATH.read_text(encoding="utf-8")

    # Key packages must be in notice
    for key_pkg in ("fastapi", "next", "psycopg2-binary", "caniuse-lite"):
        assert key_pkg in notice_text, f"{key_pkg} should be listed in NOTICE"


def test_no_unidentified_or_unknown_third_party_licenses() -> None:
    npm_comps = collect_npm(ROOT / "node_modules")
    py_comps = collect_python()
    all_comps = npm_comps + py_comps

    unknowns = [c for c in all_comps if c.license.strip().upper() == "UNKNOWN"]
    assert len(unknowns) == 0, f"Third party packages with UNKNOWN license: {unknowns}"


def test_sbom_catalogues_no_first_party_workspace_package() -> None:
    """The SBOM keys npm components off lockfile paths. A workspace member is
    keyed by its directory, so deriving the name from the path renamed
    `@oday-plus/ui` to `ui` and minted `pkg:npm/ui@0.1.0` -- a purl that
    belongs to an unrelated public package. That both hid our own packages
    from the first-party filter and published eight of them as third parties.
    """
    components = generate_sbom()["components"]

    first_party = [c for c in components if c["name"].startswith("@oday-plus/")]
    assert not first_party, (
        f"first-party packages must not be catalogued as third party: {first_party}"
    )

    collided = [
        c
        for c in components
        if c["purl"] in {f"pkg:npm/{name}@0.1.0" for name in FIRST_PARTY_WORKSPACE_DIRS}
    ]
    assert not collided, (
        "these purls name unrelated public packages, not our workspace members: "
        f"{[c['purl'] for c in collided]}"
    )


def test_third_party_unlicensed_is_not_admitted_by_the_first_party_marker() -> None:
    """D05 marks our own packages UNLICENSED. That marker is a first-party
    identity claim, so it must not become a licence any third party can declare
    to walk through the gate."""
    result = evaluate_policy(
        components=[Component("npm", "some-third-party-pkg", "1.0.0", "UNLICENSED")]
    )
    assert result["status"] == "FAIL"
    assert result["violations"], "an unrecognised licence string must fail closed"
    assert not result["allowed"] and not result["allowed_with_obligations"]


def test_license_policy_evaluation_fails_on_unadjudicated_cases() -> None:
    """With an empty register every LGPL case stays in review_required. The
    python side of that set is pinned by uv.lock and therefore deterministic;
    the npm side depends on which optional sharp binaries the install pulled."""
    eval_result = evaluate_policy(policy_path=POLICY_PATH)
    assert eval_result["status"] == "FAIL", "Gate should fail while LGPL cases are un-adjudicated"
    unadjudicated = {item["component"].name for item in eval_result["review_required"]}
    assert {"psycopg", "psycopg-binary", "psycopg-pool", "psycopg2-binary", "moocore"} <= unadjudicated
    assert all(item["exemption_rejections"] == [] for item in eval_result["review_required"]), (
        "an empty register has no candidate to refuse"
    )


def test_compound_expression_respects_parentheses_and_obligations() -> None:
    """Nested SPDX expressions must not let an inner OR hide an outer AND."""
    denied = evaluate_policy(
        components=[
            Component(
                "pypi",
                "test-gpl-compound",
                "1.0.0",
                "GPL-3.0-only AND (Apache-2.0 OR MIT)",
            )
        ]
    )
    assert denied["status"] == "FAIL"
    assert denied["violations"]
    assert "Denied license" in denied["violations"][0]["reason"]

    orjson = evaluate_policy(
        components=[
            Component(
                "pypi",
                "orjson",
                "3.11.9",
                "MPL-2.0 AND (Apache-2.0 OR MIT)",
            )
        ]
    )
    assert orjson["status"] == "PASS"
    assert [c.name for c in orjson["allowed_with_obligations"]] == ["orjson"]
    assert not orjson["allowed"]

    assert (
        evaluate_compound_expression(
            "GPL-3.0-only AND (Apache-2.0 OR MIT)",
            {"MIT", "Apache-2.0"},
            {"MPL-2.0"},
            set(),
            {"GPL-3.0-only"},
        )
        == "deny"
    )


def test_notice_check_cli_passes() -> None:
    res = subprocess.run(
        [sys.executable, "delivery_toolchain/security/generate_oss_notice.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, (
        f"generate_oss_notice.py --check failed:\n{res.stdout}\n{res.stderr}"
    )


# -----------------------------------------------------------------------------
# Acceptance 3: Policy remains proposed until authoritative external receipt
# -----------------------------------------------------------------------------


def test_policy_and_exemptions_remain_proposed() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    exemptions = json.loads(EXEMPTIONS_PATH.read_text(encoding="utf-8"))

    assert policy.get("status") == "proposed", (
        "policy status must remain 'proposed'; approval requires external authoritative receipt"
    )
    assert exemptions.get("status") == "proposed", (
        "exemptions status must remain 'proposed'; approval requires external authoritative receipt"
    )
    assert exemptions.get("exemptions") == [], "exemptions register must start empty in proposal"


def test_no_false_claim_of_prior_human_ops_approval() -> None:
    policy_text = POLICY_PATH.read_text(encoding="utf-8")
    notice_script_text = (ROOT / "delivery_toolchain/security/generate_oss_notice.py").read_text(
        encoding="utf-8"
    )
    test_notice_text = (ROOT / "tests/security/test_oss_notice.py").read_text(encoding="utf-8")

    for text, name in [
        (policy_text, "license_policy.json"),
        (notice_script_text, "generate_oss_notice.py"),
        (test_notice_text, "test_oss_notice.py"),
    ]:
        assert "Human/Ops decided" not in text, f"Found false claim of Human/Ops decision in {name}"
        assert "Human/Ops already approved" not in text, f"Found false claim in {name}"


# -----------------------------------------------------------------------------
# Acceptance 4: Signed/readback attestation contract
# -----------------------------------------------------------------------------


def test_attestation_contract_valid_and_integrity_readback() -> None:
    attestation = generate_attestation(ROOT)
    valid, errors = verify_attestation(attestation, ROOT)
    assert not valid, (
        "Attestation readback should fail because of unadjudicated review_required components"
    )
    assert attestation["task_id"] == "ODP-OSS-LICENSE-GATE-002"
    assert attestation["status"] == "proposed"
    assert attestation["gate_summary"]["gate_decision"] == "FAIL"


def test_attestation_check_cli_fails() -> None:
    res = subprocess.run(
        [sys.executable, "delivery_toolchain/security/attestation.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1, (
        "attestation.py --check should fail due to unadjudicated review_required cases"
    )


# -----------------------------------------------------------------------------
# Acceptance 5: Fail-Closed Negative Tests
# -----------------------------------------------------------------------------


def test_negative_stale_notice_rejected(tmp_path: Path) -> None:
    """Tampered / stale NOTICE must fail verification."""
    script = ROOT / "delivery_toolchain/security/generate_oss_notice.py"
    original_content = NOTICE_PATH.read_text(encoding="utf-8")
    try:
        # Write modified notice
        NOTICE_PATH.write_text(
            original_content + "\n- `tampered-extra-package` 1.0.0 (npm)\n", encoding="utf-8"
        )
        res = subprocess.run(
            [sys.executable, str(script), "--check"], cwd=ROOT, capture_output=True, text=True
        )
        assert res.returncode != 0, (
            "generate_oss_notice.py --check should fail on stale/tampered NOTICE"
        )
    finally:
        NOTICE_PATH.write_text(original_content, encoding="utf-8")


def test_negative_partial_install_rejected(tmp_path: Path) -> None:
    """Partial install missing dependencies must be detected."""
    import pytest

    from delivery_toolchain.security.generate_oss_notice import collect_npm

    empty_node_modules = tmp_path / "node_modules"
    empty_node_modules.mkdir()

    with pytest.raises(RuntimeError, match="Partial install detected. Missing npm packages"):
        collect_npm(empty_node_modules)


def test_negative_corrupt_package_lock_rejected(tmp_path: Path, monkeypatch) -> None:
    """A corrupt lockfile must not skip the npm completeness check."""
    import pytest

    import delivery_toolchain.security.generate_oss_notice as notice_module

    corrupt_lock = tmp_path / "package-lock.json"
    corrupt_lock.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(notice_module, "PACKAGE_LOCK", corrupt_lock)

    with pytest.raises(RuntimeError, match="package-lock.json"):
        notice_module.collect_npm(tmp_path / "node_modules")


def test_ambiguous_python_bsd_is_resolved_to_spdx() -> None:
    """Known generic BSD metadata must resolve without a policy escape hatch."""
    python_components = {
        component.name: component
        for component in collect_python()
        if component.name in {"antlr4-python3-runtime", "pyasn1_modules"}
    }
    assert python_components["antlr4-python3-runtime"].license == "BSD-3-Clause"
    assert python_components["pyasn1_modules"].license == "BSD-2-Clause"

    sbom_components = {
        component["name"]: component
        for component in generate_sbom()["components"]
        if component["name"] in {"antlr4-python3-runtime", "pyasn1-modules"}
    }
    assert sbom_components["antlr4-python3-runtime"]["licenses"] == [
        {"license": {"id": "BSD-3-Clause"}}
    ]
    assert sbom_components["pyasn1-modules"]["licenses"] == [
        {"license": {"id": "BSD-2-Clause"}}
    ]


def test_negative_hash_drift_rejected() -> None:
    """Tampered file hash in attestation evidence must fail readback."""
    attestation = generate_attestation(ROOT)
    # Tamper with uv_lock_sha256
    attestation["evidence_hashes"]["uv_lock_sha256"] = (
        "0000000000000000000000000000000000000000000000000000000000000000"
    )
    # Recompute content hash to isolate the file hash check
    payload_copy = {k: v for k, v in attestation.items() if k != "integrity"}
    attestation["integrity"]["content_sha256"] = hashlib.sha256(
        json.dumps(payload_copy, sort_keys=True).encode()
    ).hexdigest()
    # verify_attestation must detect the hash drift
    valid, errors = verify_attestation(attestation, ROOT)
    assert not valid, "Attestation must fail when an evidence hash drifts"
    assert any("Hash drift" in err or "Integrity check failed" in err for err in errors)


def test_negative_wrong_scope_rejected() -> None:
    """Transitive dev-only components must not be marked as required scope."""
    from delivery_toolchain.security.generate_sbom import generate_sbom

    sbom = generate_sbom()

    scopes = {component["name"]: component["scope"] for component in sbom["components"]}
    assert scopes["eslint"] == "optional", "dev-only eslint must not be required"
    assert scopes["next"] == "required", "production next must remain required"


def test_negative_denied_license_rejected() -> None:
    """Components carrying GPL, AGPL, SSPL, or BUSL must be rejected."""
    denied_licenses = [
        "GPL-2.0-only",
        "GPL-2.0-or-later",
        "GPL-3.0-only",
        "GPL-3.0-or-later",
        "AGPL-3.0-only",
        "AGPL-3.0-or-later",
        "SSPL-1.0",
        "BUSL-1.1",
    ]
    for lic in denied_licenses:
        comp = Component(ecosystem="pypi", name=f"test-denied-{lic}", version="1.0.0", license=lic)
        eval_result = evaluate_policy(components=[comp])
        assert eval_result["status"] == "FAIL", f"Denied license {lic} should have caused FAIL"
        assert len(eval_result["violations"]) > 0


def test_negative_unknown_license_rejected() -> None:
    """Components with UNKNOWN or empty license must fail closed."""
    unknown_comp = Component(
        ecosystem="npm", name="test-unknown-pkg", version="1.0.0", license="UNKNOWN"
    )
    eval_result = evaluate_policy(components=[unknown_comp])
    assert eval_result["status"] == "FAIL", "UNKNOWN license must cause FAIL"
    assert any("Unknown" in v["reason"] for v in eval_result["violations"])


def test_negative_expired_exemption_rejected(tmp_path: Path) -> None:
    """Exemptions with expired timestamp must be rejected."""
    expired_exemption = {
        "exemption_id": "EX-001",
        "task_id": "ODP-PLAN-OSS-LEGAL-POLICY-001",
        "package": "psycopg",
        "purl": "pkg:pypi/psycopg@3.3.4",
        "license_or_finding": "LGPL-3.0-only",
        "scope": "prod",
        "applicable_releases": ["a4d81f0524fe72e73c4c46773da86f11edfe8ad2"],
        "rationale": "test only",
        "issued_at": "2026-01-01T00:00:00Z",
        "expires_at": "2026-06-01T00:00:00Z",  # in the past
        "approved_by": {
            "principal_id": "legal-user-123",
            "display_name": "Alice Legal",
            "role": "Legal Counsel",
        },
    }
    exemption_path = tmp_path / "expired-exemptions.json"
    exemption_path.write_text(json.dumps({"exemptions": [expired_exemption]}), encoding="utf-8")
    result = evaluate_policy(
        components=[Component("pypi", "psycopg", "3.3.4", "LGPL-3.0-only")],
        exemptions_path=exemption_path,
    )
    assert result["status"] == "FAIL"
    assert result["review_required"]


def test_negative_local_or_ai_approval_rejected(tmp_path: Path) -> None:
    """Exemptions approving with AI agent names or role-only strings must be rejected."""
    invalid_approvers = [
        {"principal_id": "ai-agent", "display_name": "Antigravity3", "role": "AI Agent"},
        {"principal_id": "ai-agent", "display_name": "Claude", "role": "AI Assistant"},
        {"principal_id": "ai-agent", "display_name": "Codex", "role": "AI Assistant"},
        {"principal_id": "", "display_name": "Human/Ops", "role": "Operations"},
        {"principal_id": "", "display_name": "Legal", "role": "Legal"},
        {"principal_id": "sample", "display_name": "Jane Doe", "role": "Tester"},
    ]

    for index, approver in enumerate(invalid_approvers):
        exemption = {
            "exemption_id": f"EX-AI-{index}",
            "task_id": "ODP-PLAN-OSS-LEGAL-POLICY-001",
            "package": "psycopg",
            "purl": "pkg:pypi/psycopg@3.3.4",
            "license_or_finding": "LGPL-3.0-only",
            "scope": "prod",
            "applicable_releases": ["a4d81f0524fe72e73c4c46773da86f11edfe8ad2"],
            "rationale": "test only",
            "issued_at": "2026-01-01T00:00:00Z",
            "expires_at": "2030-01-01T00:00:00Z",
            "approved_by": approver,
        }
        exemption_path = tmp_path / f"invalid-{index}.json"
        exemption_path.write_text(json.dumps({"exemptions": [exemption]}), encoding="utf-8")
        result = evaluate_policy(
            components=[Component("pypi", "psycopg", "3.3.4", "LGPL-3.0-only")],
            exemptions_path=exemption_path,
        )
        assert result["status"] == "FAIL"
        assert result["review_required"], f"Approver {approver} must be rejected"


def test_negative_tampered_integrity_rejected() -> None:
    """Mismatched content_sha256 must fail attestation integrity check."""
    attestation = generate_attestation(ROOT)
    # Corrupt the integrity content_sha256
    attestation["integrity"]["content_sha256"] = (
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    )
    valid, errors = verify_attestation(attestation, ROOT)
    assert not valid, "Tampered content_sha256 must fail integrity check"
    assert any("Integrity check failed" in err for err in errors)


# -----------------------------------------------------------------------------
# Acceptance 5: receipt-bound exemptions (ODP-OSS-LICENSE-EXEMPTION-REGISTER-001)
#
# An exemption discharges a review_required component only when it binds the
# exact installed purl, names the policy case that adjudicated this package,
# covers the pinned release, carries a complete, sealed receipt, and resolves
# against a verified external authoritative source system. Each negative test
# below deviates from the honoured fixture in exactly one requirement and must
# be refused for that reason.
# -----------------------------------------------------------------------------

PSYCOPG3_COMPONENT = Component("pypi", "psycopg", "3.3.4", "LGPL-3.0-only")
SHARP_COMPOUND_LICENSE = "Apache-2.0 AND LGPL-3.0-or-later AND MIT"


def _sealed(entry: dict) -> dict:
    """Seal an entry the way a receipt is sealed: content hash over all but integrity."""
    sealed = {key: value for key, value in entry.items() if key != "integrity"}
    sealed["integrity"] = {
        "algorithm": "sha256",
        "content_sha256": exemption_content_sha256(sealed),
    }
    return sealed


def _receipt_bound_exemption(**overrides: object) -> dict:
    """A fully bound, sealed exemption for psycopg 3.3.4 under case LGPL-PSYCOPG3.

    Test-only receipt: approver, reference and source system are synthetic and
    prove nothing about the real cases. They exist so the positive path can be
    shown to pass and each negative path can be shown to fail on one deviation."""
    entry: dict = {
        "exemption_id": "EX-TEST-PSYCOPG3",
        "task_id": "ODP-PLAN-OSS-LEGAL-POLICY-001",
        "package": "psycopg",
        "purl": "pkg:pypi/psycopg@3.3.4",
        "license_or_finding": "LGPL-3.0-only",
        "scope": "prod",
        "applicable_releases": [get_repo_release_digests(ROOT)["alfloop-dev/odayplus"]],
        "rationale": "test only",
        "policy_case_id": "LGPL-PSYCOPG3",
        "approved_by": {
            "principal_id": "legal-user-123",
            "display_name": "Alice Legal",
            "role": "Legal Counsel",
        },
        "approval_reference": "LEGAL-DECISION-0042",
        "source_system": "corp-legal-tracker",
        "issued_at": "2026-01-01T00:00:00Z",
        "expires_at": "2030-01-01T00:00:00Z",
        "review_at": "2026-02-01T00:00:00Z",
        "conditions": ["dynamic linking only"],
        "evidence_hashes": [hashlib.sha256(b"LEGAL-DECISION-0042").hexdigest()],
    }
    entry.update(overrides)
    return _sealed(entry)


def _default_verifier_for(exemption: dict) -> FixedAuthoritativeReceiptVerifier:
    principal = (
        exemption.get("approved_by", {}).get("principal_id", "legal-user-123")
        if isinstance(exemption.get("approved_by"), dict)
        else "legal-user-123"
    )
    hashes = (
        list(exemption.get("evidence_hashes", []))
        if isinstance(exemption.get("evidence_hashes"), list)
        else []
    )
    src = str(exemption.get("source_system") or "corp-legal-tracker")
    ref = str(exemption.get("approval_reference") or "LEGAL-DECISION-0042")
    return FixedAuthoritativeReceiptVerifier(
        {
            (src, ref): {
                "principal_id": principal,
                "evidence_hashes": hashes,
                "status": "APPROVED",
            }
        }
    )


_SENTINEL = object()


def _evaluate_with(
    exemption: dict,
    tmp_path: Path,
    component: Component = PSYCOPG3_COMPONENT,
    *,
    authoritative_verifier: Any = _SENTINEL,
) -> dict:
    ex_id = str(exemption.get("exemption_id") or "test-exemption")
    register = tmp_path / f"{ex_id}.json"
    register.write_text(json.dumps({"exemptions": [exemption]}), encoding="utf-8")
    verifier = (
        _default_verifier_for(exemption)
        if authoritative_verifier is _SENTINEL
        else authoritative_verifier
    )
    return evaluate_policy(
        components=[component],
        exemptions_path=register,
        authoritative_verifier=verifier,
    )


def _refusals(result: dict) -> list[str]:
    return [
        rejection["reason"]
        for item in result["review_required"]
        for rejection in item["exemption_rejections"]
    ]


def test_receipt_bound_exemption_is_honoured_as_obligated_not_allowed(tmp_path: Path) -> None:
    result = _evaluate_with(_receipt_bound_exemption(), tmp_path)
    assert result["status"] == "PASS", result["review_required"]
    assert result["review_required"] == [] and result["violations"] == []
    assert [component.name for component in result["allowed_with_obligations"]] == ["psycopg"]
    assert result["allowed"] == [], "an exemption never promotes a component to plain allow"


def test_exemption_refusal_names_the_candidate_and_the_reason(tmp_path: Path) -> None:
    result = _evaluate_with(_receipt_bound_exemption(scope="dev"), tmp_path)
    assert result["status"] == "FAIL"
    (item,) = result["review_required"]
    assert item["component"].name == "psycopg"
    assert item["exemption_rejections"] == [
        {
            "exemption_id": "EX-TEST-PSYCOPG3",
            "reason": "scope mismatch: case LGPL-PSYCOPG3 is scoped 'prod', exemption claims 'dev'",
        }
    ]


# -----------------------------------------------------------------------------
# Authoritative readback boundary tests (P1 R1)
# -----------------------------------------------------------------------------


def test_negative_authoritative_verification_missing_fails_closed(tmp_path: Path) -> None:
    """Offline evaluation without an authoritative readback verifier must fail closed."""
    result = _evaluate_with(_receipt_bound_exemption(), tmp_path, authoritative_verifier=None)
    assert result["status"] == "FAIL"
    assert result["allowed_with_obligations"] == []
    reasons = _refusals(result)
    assert len(reasons) == 1 and "authoritative approval verification missing" in reasons[0], reasons


def test_negative_authoritative_source_unreachable_rejected(tmp_path: Path) -> None:
    """An unreachable source system must fail closed."""
    verifier = FixedAuthoritativeReceiptVerifier(
        {
            ("corp-legal-tracker", "LEGAL-DECISION-0042"): {
                "principal_id": "legal-user-123",
                "evidence_hashes": [hashlib.sha256(b"LEGAL-DECISION-0042").hexdigest()],
                "status": "APPROVED",
            }
        },
        unreachable_sources={"corp-legal-tracker"},
    )
    result = _evaluate_with(_receipt_bound_exemption(), tmp_path, authoritative_verifier=verifier)
    assert result["status"] == "FAIL"
    reasons = _refusals(result)
    assert len(reasons) == 1 and "authoritative source system unreachable" in reasons[0], reasons


def test_negative_authoritative_reference_unresolvable_rejected(tmp_path: Path) -> None:
    """An unresolvable approval reference must fail closed."""
    verifier = FixedAuthoritativeReceiptVerifier({})  # Empty record registry
    result = _evaluate_with(_receipt_bound_exemption(), tmp_path, authoritative_verifier=verifier)
    assert result["status"] == "FAIL"
    reasons = _refusals(result)
    assert len(reasons) == 1 and "authoritative approval reference unresolvable" in reasons[0], reasons


def test_negative_authoritative_evidence_hash_mismatch_rejected(tmp_path: Path) -> None:
    """Mismatched evidence hashes between exemption and authoritative system fail closed."""
    verifier = FixedAuthoritativeReceiptVerifier(
        {
            ("corp-legal-tracker", "LEGAL-DECISION-0042"): {
                "principal_id": "legal-user-123",
                "evidence_hashes": ["0" * 64],  # Mismatched hash
                "status": "APPROVED",
            }
        }
    )
    result = _evaluate_with(_receipt_bound_exemption(), tmp_path, authoritative_verifier=verifier)
    assert result["status"] == "FAIL"
    reasons = _refusals(result)
    assert len(reasons) == 1 and "authoritative evidence hash mismatch" in reasons[0], reasons


def test_negative_authoritative_approver_mismatch_rejected(tmp_path: Path) -> None:
    """Mismatched approver principal between exemption and authoritative system fails closed."""
    verifier = FixedAuthoritativeReceiptVerifier(
        {
            ("corp-legal-tracker", "LEGAL-DECISION-0042"): {
                "principal_id": "different-legal-principal",
                "evidence_hashes": [hashlib.sha256(b"LEGAL-DECISION-0042").hexdigest()],
                "status": "APPROVED",
            }
        }
    )
    result = _evaluate_with(_receipt_bound_exemption(), tmp_path, authoritative_verifier=verifier)
    assert result["status"] == "FAIL"
    reasons = _refusals(result)
    assert len(reasons) == 1 and "authoritative approver mismatch" in reasons[0], reasons


def test_negative_authoritative_revoked_or_rejected_status_fails_closed(tmp_path: Path) -> None:
    """Revoked or non-approved status in authoritative system fails closed."""
    for bad_status in ("REVOKED", "REJECTED", "PENDING"):
        verifier = FixedAuthoritativeReceiptVerifier(
            {
                ("corp-legal-tracker", "LEGAL-DECISION-0042"): {
                    "principal_id": "legal-user-123",
                    "evidence_hashes": [hashlib.sha256(b"LEGAL-DECISION-0042").hexdigest()],
                    "status": bad_status,
                }
            }
        )
        result = _evaluate_with(_receipt_bound_exemption(), tmp_path, authoritative_verifier=verifier)
        assert result["status"] == "FAIL"
        reasons = _refusals(result)
        assert len(reasons) == 1 and f"authoritative approval status is {bad_status}" in reasons[0], reasons


# -----------------------------------------------------------------------------
# Binding and Adjudication negative tests
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "component", "expected_reason"),
    [
        pytest.param(
            {},
            Component("pypi", "psycopg", "3.3.5", "LGPL-3.0-only"),
            "purl mismatch",
            id="installed-version-differs",
        ),
        pytest.param(
            {"purl": "pkg:pypi/psycopg@3.3.5"},
            PSYCOPG3_COMPONENT,
            "purl mismatch",
            id="purl-pins-other-version",
        ),
        pytest.param(
            {"purl": "pkg:npm/psycopg@3.3.4"},
            PSYCOPG3_COMPONENT,
            "purl mismatch",
            id="purl-wrong-ecosystem",
        ),
        pytest.param({"scope": "dev"}, PSYCOPG3_COMPONENT, "scope mismatch", id="scope-dev-vs-prod"),
        pytest.param(
            {"scope": "prod,dev"}, PSYCOPG3_COMPONENT, "is not one of", id="scope-not-a-value"
        ),
        pytest.param(
            {"applicable_releases": ["0" * 40]},
            PSYCOPG3_COMPONENT,
            "release mismatch",
            id="release-not-pinned-one",
        ),
        pytest.param(
            {"applicable_releases": []},
            PSYCOPG3_COMPONENT,
            "applicable_releases must be a non-empty list",
            id="release-list-empty",
        ),
        pytest.param(
            {"policy_case_id": "LGPL-NONEXISTENT"},
            PSYCOPG3_COMPONENT,
            "does not name a review_required case",
            id="no-policy-case",
        ),
        pytest.param(
            {"policy_case_id": "LGPL-MOOCORE"},
            PSYCOPG3_COMPONENT,
            "adjudicates 'LGPL-2.1-or-later', not 'LGPL-3.0-only'",
            id="case-adjudicates-other-license",
        ),
    ],
)
def test_negative_binding_mismatch_rejected(
    overrides: dict, component: Component, expected_reason: str, tmp_path: Path
) -> None:
    result = _evaluate_with(_receipt_bound_exemption(**overrides), tmp_path, component)
    assert result["status"] == "FAIL"
    assert result["allowed_with_obligations"] == []
    reasons = _refusals(result)
    assert len(reasons) == 1 and expected_reason in reasons[0], reasons


@pytest.mark.parametrize(
    ("component", "expected_reason"),
    [
        pytest.param(
            Component("npm", "@img/sharp-wasm32", "0.35.4", SHARP_COMPOUND_LICENSE),
            "case LGPL-SHARP-LIBVIPS adjudicates 'LGPL-3.0-or-later', not",
            id="wasm32-compound-license-not-the-adjudicated-one",
        ),
        pytest.param(
            Component("npm", "@img/sharp-linux-x64", "0.35.4", "LGPL-3.0-or-later"),
            "is not among the packages adjudicated under case LGPL-SHARP-LIBVIPS",
            id="package-not-listed-in-case",
        ),
    ],
)
def test_negative_unadjudicated_package_cannot_borrow_a_case(
    component: Component, expected_reason: str, tmp_path: Path
) -> None:
    """A package the case never named is not exempted by analogy, whatever the
    rationale says. This is what keeps @img/sharp-wasm32 out until it has a
    ruling of its own."""
    exemption = _receipt_bound_exemption(
        exemption_id="EX-TEST-SHARP-BORROWED",
        package=component.name,
        purl=f"pkg:npm/{component.name}@{component.version}",
        license_or_finding=component.license,
        policy_case_id="LGPL-SHARP-LIBVIPS",
    )
    result = _evaluate_with(exemption, tmp_path, component)
    assert result["status"] == "FAIL"
    reasons = _refusals(result)
    assert len(reasons) == 1 and expected_reason in reasons[0], reasons


# -----------------------------------------------------------------------------
# Receipt Fields Completeness & Negative Tests (P2 R2)
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "bad_value", "expected_reason"),
    [
        pytest.param("exemption_id", "", "exemption_id is missing or empty", id="exemption_id-empty"),
        pytest.param("exemption_id", "   ", "exemption_id is missing or empty", id="exemption_id-whitespace"),
        pytest.param("exemption_id", None, "missing required field(s)", id="exemption_id-missing"),
        pytest.param("task_id", "", "task_id is missing or empty", id="task_id-empty"),
        pytest.param("task_id", "   ", "task_id is missing or empty", id="task_id-whitespace"),
        pytest.param("task_id", None, "missing required field(s)", id="task_id-missing"),
        pytest.param("package", "", "package is missing or empty", id="package-empty"),
        pytest.param("package", None, "missing required field(s)", id="package-missing"),
        pytest.param("purl", "", "purl is missing or empty", id="purl-empty"),
        pytest.param("purl", None, "missing required field(s)", id="purl-missing"),
        pytest.param("license_or_finding", "", "license_or_finding is missing or empty", id="license-empty"),
        pytest.param("license_or_finding", None, "missing required field(s)", id="license-missing"),
        pytest.param("rationale", "", "rationale is missing or empty", id="rationale-empty"),
        pytest.param("rationale", "   ", "rationale is missing or empty", id="rationale-whitespace"),
        pytest.param("rationale", None, "missing required field(s)", id="rationale-missing"),
        pytest.param("policy_case_id", "", "policy_case_id is missing or empty", id="policy_case_id-empty"),
        pytest.param("policy_case_id", None, "missing required field(s)", id="policy_case_id-missing"),
        pytest.param("review_at", "", "review_at must be a UTC timestamp", id="review_at-empty"),
        pytest.param("review_at", "not-a-timestamp", "review_at must be a UTC timestamp", id="review_at-invalid"),
        pytest.param("review_at", "2026-01-01T00:00:00", "review_at must be a UTC timestamp", id="review_at-naive"),
        pytest.param("review_at", None, "missing required field(s)", id="review_at-missing"),
        pytest.param("approved_by", None, "missing required field(s)", id="approved_by-missing"),
        pytest.param("approved_by", {}, "missing required approver field(s)", id="approved_by-empty-dict"),
        pytest.param("approved_by.principal_id", "", "approved_by.principal_id is missing or empty", id="approver-principal-empty"),
        pytest.param("approved_by.principal_id", "   ", "approved_by.principal_id is missing or empty", id="approver-principal-whitespace"),
        pytest.param("approved_by.principal_id", None, "missing required approver field(s)", id="approver-principal-missing"),
        pytest.param("approved_by.display_name", "", "approved_by.display_name is missing or empty", id="approver-display_name-empty"),
        pytest.param("approved_by.display_name", "   ", "approved_by.display_name is missing or empty", id="approver-display_name-whitespace"),
        pytest.param("approved_by.display_name", None, "missing required approver field(s)", id="approver-display_name-missing"),
        pytest.param("approved_by.role", "", "approved_by.role is missing or empty", id="approver-role-empty"),
        pytest.param("approved_by.role", "   ", "approved_by.role is missing or empty", id="approver-role-whitespace"),
        pytest.param("approved_by.role", None, "missing required approver field(s)", id="approver-role-missing"),
        pytest.param("approval_reference", "", "approval_reference is empty", id="approval_reference-empty"),
        pytest.param("approval_reference", "   ", "approval_reference is empty", id="approval_reference-whitespace"),
        pytest.param("approval_reference", None, "missing required field(s)", id="approval_reference-missing"),
        pytest.param("source_system", "", "offers no external authoritative readback", id="source_system-empty"),
        pytest.param("source_system", "   ", "offers no external authoritative readback", id="source_system-whitespace"),
        pytest.param("source_system", None, "missing required field(s)", id="source_system-missing"),
        pytest.param("evidence_hashes", [], "evidence_hashes is empty", id="evidence_hashes-empty-list"),
        pytest.param("evidence_hashes", None, "missing required field(s)", id="evidence_hashes-missing"),
        pytest.param("integrity", None, "missing required field(s)", id="integrity-missing"),
    ],
)
def test_negative_required_receipt_fields_missing_or_blank_rejected(
    field: str, bad_value: object, expected_reason: str, tmp_path: Path
) -> None:
    entry: dict = {
        "exemption_id": "EX-TEST-PSYCOPG3",
        "task_id": "ODP-PLAN-OSS-LEGAL-POLICY-001",
        "package": "psycopg",
        "purl": "pkg:pypi/psycopg@3.3.4",
        "license_or_finding": "LGPL-3.0-only",
        "scope": "prod",
        "applicable_releases": [get_repo_release_digests(ROOT)["alfloop-dev/odayplus"]],
        "rationale": "test only",
        "policy_case_id": "LGPL-PSYCOPG3",
        "approved_by": {
            "principal_id": "legal-user-123",
            "display_name": "Alice Legal",
            "role": "Legal Counsel",
        },
        "approval_reference": "LEGAL-DECISION-0042",
        "source_system": "corp-legal-tracker",
        "issued_at": "2026-01-01T00:00:00Z",
        "expires_at": "2030-01-01T00:00:00Z",
        "review_at": "2026-02-01T00:00:00Z",
        "conditions": ["dynamic linking only"],
        "evidence_hashes": [hashlib.sha256(b"LEGAL-DECISION-0042").hexdigest()],
    }
    if "." in field:
        parent, child = field.split(".", 1)
        if bad_value is None:
            entry[parent].pop(child, None)
        else:
            entry[parent][child] = bad_value
    else:
        if bad_value is None:
            entry.pop(field, None)
        else:
            entry[field] = bad_value

    sealed_entry = _sealed(entry)
    if field == "integrity" and bad_value is None:
        sealed_entry.pop("integrity", None)
    if field == "package":
        review_cases = {
            "LGPL-PSYCOPG3": {
                "id": "LGPL-PSYCOPG3",
                "license": "LGPL-3.0-only",
                "scope": "prod",
                "packages": [{"package": "psycopg"}],
            }
        }
        reason = validate_exemption(
            sealed_entry,
            PSYCOPG3_COMPONENT,
            PSYCOPG3_COMPONENT.license,
            review_cases=review_cases,
            release_digest=get_repo_release_digests(ROOT)["alfloop-dev/odayplus"],
            authoritative_verifier=_default_verifier_for(sealed_entry),
        )
        assert reason is not None and expected_reason in reason, reason
        return
    result = _evaluate_with(sealed_entry, tmp_path)
    assert result["status"] == "FAIL"
    reasons = _refusals(result)
    assert len(reasons) == 1 and expected_reason in reasons[0], reasons


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        pytest.param(
            {"source_system": "repository-local-handoff-document"},
            "offers no external authoritative readback",
            id="source-repository-local",
        ),
        pytest.param(
            {"evidence_hashes": ["not-a-digest"]},
            "must all be lowercase sha256 hex digests",
            id="evidence-not-a-digest",
        ),
        pytest.param(
            {"issued_at": "2026-01-01T00:00:00"}, "issued_at must be a UTC timestamp", id="issued-naive"
        ),
        pytest.param(
            {"issued_at": "2026-01-01T08:00:00+08:00"},
            "issued_at must be a UTC timestamp",
            id="issued-non-utc-offset",
        ),
        pytest.param(
            {"issued_at": (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")},
            "issued_at is in the future",
            id="issued-future",
        ),
        pytest.param(
            {"expires_at": "2025-12-31T00:00:00Z"},
            "expires_at must be later than issued_at",
            id="expires-before-issued",
        ),
    ],
)
def test_negative_incomplete_receipt_rejected(
    overrides: dict, expected_reason: str, tmp_path: Path
) -> None:
    result = _evaluate_with(_receipt_bound_exemption(**overrides), tmp_path)
    assert result["status"] == "FAIL"
    reasons = _refusals(result)
    assert len(reasons) == 1 and expected_reason in reasons[0], reasons


def test_negative_unsealed_or_tampered_receipt_rejected(tmp_path: Path) -> None:
    """integrity.content_sha256 must be present, sha256, and match the entry it
    seals; editing any field after sealing invalidates it."""
    empty = _receipt_bound_exemption()
    empty["integrity"]["content_sha256"] = ""
    assert _refusals(_evaluate_with(empty, tmp_path)) == ["integrity.content_sha256 is empty"]

    wrong_algorithm = _receipt_bound_exemption()
    wrong_algorithm["integrity"]["algorithm"] = "md5"
    assert _refusals(_evaluate_with(wrong_algorithm, tmp_path)) == [
        "integrity.algorithm must be sha256"
    ]

    tampered = _receipt_bound_exemption()
    tampered["rationale"] = "edited after sealing"
    (reason,) = _refusals(_evaluate_with(tampered, tmp_path))
    assert reason.startswith("receipt integrity check failed: recorded ")


def test_negative_round_one_register_entry_shape_rejected(tmp_path: Path) -> None:
    """The shape PR #1357 first submitted: a named approver but a repository-local
    handoff document as source, no evidence hashes and an empty seal. The gate
    must refuse it on the receipt, not wave it through on the approver."""
    entry = _receipt_bound_exemption(
        approval_reference="support/handoffs/remaining-inputs-20260913/OSS-LICENSE-4-CASES-FOR-SIGNOFF.md",
        source_system="repository-local-handoff-document",
        evidence_hashes=[],
    )
    entry["integrity"] = {"algorithm": "sha256", "content_sha256": ""}
    result = _evaluate_with(entry, tmp_path)
    assert result["status"] == "FAIL"
    (reason,) = _refusals(result)
    assert "offers no external authoritative readback" in reason


def test_reconcile_cli_names_components_awaiting_adjudication() -> None:
    """--reconcile must say which components keep the gate closed, not only
    print a violation count of zero."""
    res = subprocess.run(
        [sys.executable, "delivery_toolchain/security/generate_oss_notice.py", "--reconcile"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1
    assert "components awaiting adjudication" in res.stderr
    assert "psycopg (3.3.4): Review required license: LGPL-3.0-only" in res.stderr


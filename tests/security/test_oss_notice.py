"""The shipped OSS notice must keep naming what we actually ship.

Human/Ops decided the LGPL question on ODP-PLAN-OSS-LEGAL-POLICY-001 as
allow-with-obligations. The obligation is this notice, so a notice that has
drifted from the dependency tree is the obligation quietly lapsing rather than
a cosmetic staleness.
"""

from __future__ import annotations

import importlib.util
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
    """@oday-plus/* are ours and carry no licence; listing them as UNKNOWN would
    misrepresent our own code as an unidentified third-party dependency."""
    text = NOTICE.read_text(encoding="utf-8")
    assert "@oday-plus/" not in text


def test_generator_excludes_operating_system_python_packages() -> None:
    """Enumerating site-packages sweeps in Ubuntu's own GPL tooling, which this
    project does not depend on. uv.lock is what bounds the python side."""
    text = NOTICE.read_text(encoding="utf-8")
    for os_package in ("cloud-init", "ubuntu-pro-client", "apparmor", "ufw"):
        assert os_package not in text, (
            f"{os_package} is an operating system package, not a dependency"
        )

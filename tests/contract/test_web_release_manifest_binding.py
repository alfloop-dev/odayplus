"""Execute normal deploy serializers and the unchanged strict identity guard offline.

Inputs below are synthetic metadata, not admission or live deployment evidence.
No deploy script, cloud command, auth transport, or provisioning is executed.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from delivery_toolchain.e2e.live_business_journeys import (
    ReleaseBinding,
    release_identity_failures,
)

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "product_ops/deployment/deploy_cloud_run_waji.sh"
SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
EXPORT_PREFIX = "export ODP_RELEASE_MANIFEST_DIGEST="
API_MARKER = 'python3 - "${API_ENV_FILE}" <<\'PY\''
WEB_MARKER = 'python3 - "${WEB_ENV_FILE}" "${API_URL}" "${API_SERVICE_AUDIENCE}" <<\'PY\''


def _heredoc(text: str, marker: str) -> str:
    assert text.count(marker) == 1
    start = text.index("\n", text.index(marker)) + 1
    return text[start:text.index("\nPY\n", start)]


def _serialize(tmp_path: Path, profile: str, **overrides: str | None) -> tuple[dict, dict]:
    """Run the actual shell export followed by both actual inline serializers.

    A minimal env prevents credentials or caller metadata leaking into the test.
    Extracting just these stdlib blocks avoids executing cloud side effects.
    """
    text = DEPLOY.read_text(encoding="utf-8")
    exports = [line for line in text.splitlines() if line.startswith(EXPORT_PREFIX)]
    assert len(exports) == 1
    assert text.index(exports[0]) < text.index(API_MARKER) < text.index(WEB_MARKER)
    env = {
        "PATH": "/usr/bin:/bin",
        "MANIFEST_DIGEST": DIGEST,
        # The normal shell export must override this unrelated inherited value.
        "ODP_RELEASE_MANIFEST_DIGEST": "sha256:" + "c" * 64,
        "ODAY_RELEASE_SHA": SHA,
        "ODP_RELEASE_PROFILE": profile,
        "ODP_DEPLOY_ENV": "dev",
        "ODP_REQUIRE_LIVE_DATA": "true",
        "ODP_DATA_BINDING_MODE": "live",
        "ODP_PRODUCT_MODE": "production",
        "ODP_WEB_BASE_URL": "https://web.example.test",
        "ODP_AUTH_MODE": "local",
        "ODP_AUTH_OIDC_ENABLED": "false",
        "ODP_AUTH_AUDIENCES": "urn:test:api",
        "ODP_TENANT_ID": "00000000-0000-0000-0000-000000000001",
    }
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    outputs = []
    for name, marker in (("api", API_MARKER), ("web", WEB_MARKER)):
        out = tmp_path / f"{name}.json"
        argv = [sys.executable, "-", str(out)]
        if name == "web":
            argv += ["https://candidate-api.example.test", "https://api.example.test"]
        script = (
            "set -euo pipefail\n" + exports[0] + "\n"
            + shlex.join(argv) + " <<'PY'\n" + _heredoc(text, marker) + "\nPY\n"
        )
        subprocess.run(
            ["bash", "-c", script], env=env, cwd=ROOT,
            check=True, capture_output=True, text=True,
        )
        outputs.append(json.loads(out.read_text(encoding="utf-8")))
    return outputs[0], outputs[1]


def _readback(api: dict, web: dict) -> dict:
    """Project each serializer's OWN metadata, as the API/BFF readback does.

    This offline mapping is not a claimed HTTP/authenticated BFF test; the
    production BFF route is unchanged and never substitutes API identity.
    """
    return {
        "release_sha": api["ODAY_RELEASE_SHA"],
        "release_profile": api["ODP_RELEASE_PROFILE"],
        "release_profile_valid": True,
        "manifest_digest": api["ODP_RELEASE_MANIFEST_DIGEST"],
        "web_release_sha": web["ODAY_RELEASE_SHA"],
        "web_release_profile": web["ODP_RELEASE_PROFILE"],
        "web_manifest_digest": web["ODP_RELEASE_MANIFEST_DIGEST"],
    }


def _binding(profile: str) -> ReleaseBinding:
    return ReleaseBinding(SHA, DIGEST, profile, "")


@pytest.mark.parametrize("profile", ["full", "dev-admin"])
def test_normal_serializers_bind_exact_admitted_identity(tmp_path: Path, profile: str) -> None:
    api, web = _serialize(tmp_path, profile)
    for payload in (api, web):
        assert payload["ODP_RELEASE_MANIFEST_DIGEST"] == DIGEST
        assert payload["ODAY_RELEASE_SHA"] == SHA
        assert payload["ODP_RELEASE_PROFILE"] == profile
    assert web["ODP_API_BASE_URL"] == "https://candidate-api.example.test"
    assert web["ODP_API_SERVICE_AUDIENCE"] == "https://api.example.test"
    assert "NEXT_PUBLIC_ODP_RELEASE_MANIFEST_DIGEST" not in web
    assert release_identity_failures(_readback(api, web), _binding(profile), include_web=True) == []


@pytest.mark.parametrize("profile", ["full", "dev-admin"])
@pytest.mark.parametrize("digest", [None, "", "sha256:" + "d" * 64, "not-a-digest"])
def test_missing_or_wrong_export_is_not_replaced_or_accepted(
    tmp_path: Path, profile: str, digest: str | None,
) -> None:
    api, web = _serialize(tmp_path, profile, MANIFEST_DIGEST=digest)
    # Missing export does not reuse the inherited digest or manufacture one.
    assert api["ODP_RELEASE_MANIFEST_DIGEST"] == (digest or "")
    assert web["ODP_RELEASE_MANIFEST_DIGEST"] == (digest or "")
    errors = release_identity_failures(_readback(api, web), _binding(profile), include_web=True)
    assert {error[0] for error in errors} == {
        "runtime:manifest_digest", "runtime:web_manifest_digest",
    }


@pytest.mark.parametrize("profile", ["full", "dev-admin"])
@pytest.mark.parametrize("side", ["api", "web"])
@pytest.mark.parametrize("metadata", ["manifest_digest", "release_sha", "release_profile"])
@pytest.mark.parametrize("missing", [False, True])
def test_strict_guard_refuses_independent_missing_or_mismatched_runtime_metadata(
    tmp_path: Path, profile: str, side: str, metadata: str, missing: bool,
) -> None:
    api, web = _serialize(tmp_path, profile)
    payload = _readback(api, web)
    field = ("web_" if side == "web" else "") + metadata
    if missing:
        payload.pop(field)
    else:
        payload[field] = {
            "manifest_digest": "sha256:" + "e" * 64,
            "release_sha": "f" * 40,
            "release_profile": "dev-admin" if profile == "full" else "full",
        }[metadata]
    errors = release_identity_failures(payload, _binding(profile), include_web=True)
    assert [error[0] for error in errors] == [f"runtime:{field}"]

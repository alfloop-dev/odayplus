"""Service-level minimum instances in the Cloud Run release entrypoint.

ODP-DEV-CLOUD-RUN-MIN-INSTANCES-001: the dev API cold start was measured at
~24-29s, longer than the Web BFF's 10s upstream timeout, so dev keeps one warm
API and Web instance. The minimum must be service level: a revision-level
``--min-instances`` would keep every tagged candidate revision warm too.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "product_ops/deployment/deploy_cloud_run_waji.sh"
TEXT = SCRIPT.read_text(encoding="utf-8")

DEFAULTING_START = 'CLOUD_RUN_MIN_INSTANCES="${ODP_CLOUD_RUN_MIN_INSTANCES:-}"'


def _resolve(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    start = TEXT.index(DEFAULTING_START)
    snippet = TEXT[start : TEXT.index("\nfi\n", TEXT.index("must be a non-negative integer", start)) + 4]
    return subprocess.run(
        ["bash", "-euo", "pipefail", "-c", snippet + 'printf "%s" "${CLOUD_RUN_MIN_INSTANCES}"'],
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", **env},
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"ODP_DEPLOY_ENV": "dev"}, "1"),
        ({"ODP_DEPLOY_ENV": "staging"}, ""),
        ({"ODP_DEPLOY_ENV": "production"}, ""),
        ({"ODP_DEPLOY_ENV": "staging", "ODP_CLOUD_RUN_MIN_INSTANCES": "2"}, "2"),
        ({"ODP_DEPLOY_ENV": "dev", "ODP_CLOUD_RUN_MIN_INSTANCES": "0"}, "0"),
    ],
)
def test_minimum_defaults_to_one_only_on_dev(env: dict[str, str], expected: str) -> None:
    result = _resolve(env)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


@pytest.mark.parametrize("value", ["-1", "1.5", "one", "1 2"])
def test_invalid_minimum_fails_before_any_mutation(value: str) -> None:
    result = _resolve({"ODP_DEPLOY_ENV": "dev", "ODP_CLOUD_RUN_MIN_INSTANCES": value})
    assert result.returncode == 1
    assert "non-negative integer" in result.stderr
    # Validation sits with the other required inputs, ahead of the first gcloud call.
    first_gcloud_call = re.search(r"^\s*gcloud ", TEXT, re.MULTILINE)
    assert first_gcloud_call is not None
    assert TEXT.index(DEFAULTING_START) < first_gcloud_call.start()


def test_minimum_is_service_level_and_applied_after_promotion() -> None:
    assert "--min-instances" not in TEXT
    update = TEXT.index('gcloud run services update "${min_service}"')
    block = TEXT[update : update + 300]
    assert '--min="${CLOUD_RUN_MIN_INSTANCES}"' in block
    assert TEXT.index('promote_service_traffic "${WEB_SERVICE}" "${WEB_REVISION}"') < update
    assert update < TEXT.index("Running fail-closed live E2E acceptance gate")
    loop = TEXT[TEXT.rindex("for min_service in", 0, update) : update]
    assert '"${API_SERVICE}" "${WEB_SERVICE}"' in loop


def test_candidate_smoke_timeout_outlasts_the_cold_start() -> None:
    """Tagged candidates get no warm instance, so the smoke probe must wait out
    the measured ~24-29s API cold start instead of the 15s CLI default."""
    call = TEXT.index("validate_cloud_run_live_deployment.py smoke \\")
    block = TEXT[call : TEXT.index("--output", call)]
    match = re.search(r'--timeout "\$\{CANDIDATE_SMOKE_TIMEOUT_SECONDS:-(\d+)\}"', block)
    assert match is not None
    assert int(match.group(1)) >= 45

"""Exercise the real shell helper; spies are offline inputs, not signing evidence."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "delivery_toolchain/security/sign_images.sh"
IMAGE = "registry.example.invalid/api@sha256:" + "a" * 64
OIDC_ERROR = (
    "Error: signing image: getting signer: getting key from Fulcio: "
    "fetching ambient OIDC credentials: invalid character 'u' looking for beginning of value"
)


@pytest.fixture
def spy(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    tools = tmp_path / "bin"
    tools.mkdir()
    log = tmp_path / "calls.jsonl"
    script = tools / "cosign"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, stat, sys\n"
        "if sys.argv[1] != 'verify':\n"
        "    assert stat.S_IMODE(os.fstat(1).st_mode) == 0o600\n"
        "p = pathlib.Path(os.environ['SPY_LOG'])\n"
        "calls = p.read_text().splitlines() if p.exists() else []\n"
        "with p.open('a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "print('successful response contains secret-do-not-print')\n"
        "if len(calls) < int(os.environ.get('SPY_FAILURES', '0')):\n"
        "    print(os.environ['SPY_ERROR'], file=sys.stderr)\n"
        "    print('secret-do-not-print', file=sys.stderr)\n"
        "    sys.exit(23)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    sleep = tools / "sleep"
    sleep.write_text(
        '#!/bin/bash\nprintf "%s\\n" "$1" >> "$SPY_SLEEPS"\n', encoding="utf-8"
    )
    sleep.chmod(0o755)
    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    env = dict(
        os.environ,
        PATH=f"{tools}:{os.environ['PATH']}",
        SPY_LOG=str(log),
        SPY_SLEEPS=str(tmp_path / "sleeps"),
        SPY_ERROR=OIDC_ERROR,
        TMPDIR=str(diagnostics),
    )
    sbom = tmp_path / "SBOM with spaces.json"
    sbom.write_text('{}\n', encoding="utf-8")
    return env, log, sbom


def invoke(
    spy: tuple[dict[str, str], Path, Path],
    operation: str,
    failures: int,
    error: str,
    image: str = IMAGE,
):
    env, log, sbom = spy
    env.update(SPY_FAILURES=str(failures), SPY_ERROR=error)
    args = ["/bin/bash", str(SCRIPT), operation, image]
    if operation == "attest":
        args.append(str(sbom))
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=10, check=False)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    delays = Path(env["SPY_SLEEPS"])
    sleeps = delays.read_text().splitlines() if delays.exists() else []
    assert not list(Path(env["TMPDIR"]).iterdir()), "private diagnostics must be removed"
    if operation != "verify":
        assert "secret-do-not-print" not in result.stdout + result.stderr
        assert error not in result.stdout + result.stderr
    return result, calls, sleeps


@pytest.mark.parametrize("operation", ["sign", "attest"])
@pytest.mark.parametrize("failures", [0, 1, 2, 3, 20])
def test_fixed_attempt_bound_and_identical_arguments(spy, operation: str, failures: int) -> None:
    result, calls, sleeps = invoke(spy, operation, failures, OIDC_ERROR)
    expected = [operation, "--yes"]
    if operation == "attest":
        expected += ["--type", "cyclonedx", "--predicate", str(spy[2])]
    expected += [IMAGE]
    assert calls == [expected] * min(failures + 1, 3)
    assert sleeps == ["2", "4"][: min(failures, 2)]
    assert result.returncode == (0 if failures < 3 else 23)
    assert ("successfully" in result.stdout) == (failures < 3)
    assert ("exhausted 3 attempts" in result.stderr) == (failures >= 3)


@pytest.mark.parametrize("operation", ["sign", "attest"])
@pytest.mark.parametrize(
    "error",
    [
        "fetching ambient OIDC credentials: unexpected EOF",
        "fetching ambient OIDC credentials: 429 Too Many Requests",
        "fetching ambient OIDC credentials: 500 Internal Server Error",
        "fetching ambient OIDC credentials: 502 Bad Gateway",
        "fetching ambient OIDC credentials: 503 Service Unavailable",
        "fetching ambient OIDC credentials: 504 Gateway Timeout",
    ],
)
def test_only_oidc_transient_transport_errors_are_retried(spy, operation: str, error: str) -> None:
    result, calls, sleeps = invoke(spy, operation, 1, error)
    assert result.returncode == 0
    assert len(calls) == 2
    assert sleeps == ["2"]


@pytest.mark.parametrize("operation", ["sign", "attest"])
@pytest.mark.parametrize(
    "error",
    [
        "fetching ambient OIDC credentials: 401 Unauthorized",
        "fetching ambient OIDC credentials: 403 Forbidden",
        "permission denied",
        "invalid audience",
        "invalid issuer",
        "invalid token",
        "x509: certificate signed by unknown authority",
        "signature verification failed",
        "no matching signatures",
        "registry push: 503 Service Unavailable",
        "invalid character 'u' looking for beginning of value",
        "fetching ambient OIDC credentials: invalid character 'x' looking for beginning of value",
        "unknown failure",
        OIDC_ERROR + "\n403 Forbidden",
        OIDC_ERROR + "\ninvalid issuer",
        OIDC_ERROR + "\ninvalid identity token",
        OIDC_ERROR + "\nissuer mismatch",
        OIDC_ERROR + "\nUnauthenticated",
        OIDC_ERROR + "\ntrust policy failure",
    ],
)
def test_permanent_and_unknown_errors_fail_closed_without_retry(spy, operation, error) -> None:
    result, calls, sleeps = invoke(spy, operation, 20, error)
    assert result.returncode == 23
    assert len(calls) == 1
    assert sleeps == []
    assert "successfully" not in result.stdout


@pytest.mark.parametrize("operation", ["sign", "attest"])
@pytest.mark.parametrize("digits", ["400", "401", "403"])
def test_image_digest_digits_are_not_http_status(spy, operation, digits) -> None:
    image = "registry.example.invalid/api@sha256:" + "a" * 30 + digits + "b" * 31
    error = f"Error: signing {image}: {OIDC_ERROR}"
    result, calls, sleeps = invoke(spy, operation, 1, error, image)
    assert result.returncode == 0
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert calls[0][-1] == image
    assert sleeps == ["2"]
    assert "successfully" in result.stdout


@pytest.mark.parametrize("operation", ["sign", "attest"])
@pytest.mark.parametrize("status", ["400", "401", "403"])
@pytest.mark.parametrize(
    "context",
    [
        "HTTP {status}",
        "HTTP/1.1 {status}",
        "HTTP/2 {status}",
        "HTTP status code: {status}",
        "status={status}",
        "statusCode: {status}",
        "response code: {status}",
        "fetching ambient OIDC credentials: {status}",
    ],
)
def test_permanent_http_status_vetoes_transient_error(spy, operation, status, context) -> None:
    error = OIDC_ERROR + "\n" + context.format(status=status)
    result, calls, sleeps = invoke(spy, operation, 1, error)
    assert result.returncode == 23
    assert len(calls) == 1
    assert sleeps == []
    assert "successfully" not in result.stdout


@pytest.mark.parametrize("operation", ["sign", "attest"])
def test_bad_request_reason_vetoes_transient_error(spy, operation) -> None:
    result, calls, sleeps = invoke(spy, operation, 1, OIDC_ERROR + "\n400 Bad Request")
    assert result.returncode == 23
    assert len(calls) == 1
    assert sleeps == []


def test_verification_is_not_retried_and_keeps_trust_flags(spy) -> None:
    result, calls, sleeps = invoke(spy, "verify", 20, OIDC_ERROR)
    assert result.returncode == 23
    assert calls == [[
        "verify", "--certificate-identity-regexp", "https://github.com/alfloop-dev/.*",
        "--certificate-oidc-issuer", "https://token.actions.githubusercontent.com", IMAGE,
    ]]
    assert sleeps == []
    assert "PASSED" not in result.stdout


def test_attest_without_sbom_fails_before_cosign(spy) -> None:
    env, log, _ = spy
    result = subprocess.run(
        ["/bin/bash", str(SCRIPT), "attest", IMAGE, "/nonexistent/sbom.json"],
        env=env, capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode != 0
    assert not log.exists()
    assert "successfully" not in result.stdout

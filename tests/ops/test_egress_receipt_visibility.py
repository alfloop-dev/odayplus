"""Exercise the real shell readback gate, including bounded ingestion retries."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tests.ops.test_egress_readback_recovery import (
    CANDIDATE_JOB,
    CANDIDATE_SHA,
    DEPLOY_SCRIPT,
    MANIFEST_DIGEST,
    ROOT,
    runtime_receipt,
)

EXECUTION = CANDIDATE_JOB + "-execution"


def run_gate(tmp_path: Path, responses: list[object], *, read_failure: bool = False):
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    function = script[script.index("capture_public_egress_probe_receipt() {") :]
    function = function[: function.index("\n}\n") + 3]
    response_file = tmp_path / "responses.json"
    response_file.write_text(json.dumps(responses))
    calls = tmp_path / "calls.jsonl"
    sleeps = tmp_path / "sleeps"
    report = tmp_path / "probe.json"
    execution = tmp_path / "execution.json"
    execution.write_text(json.dumps({"metadata": {"name": EXECUTION}}))
    fake = tmp_path / "logging.py"
    fake.write_text('''import json, os, sys
from pathlib import Path
path = Path(os.environ["READ_CALLS"])
previous = path.read_text().splitlines() if path.exists() else []
with path.open("a") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
if os.environ.get("READ_FAILURE") == "true":
    raise SystemExit(42)
responses = json.loads(Path(os.environ["READ_RESPONSES"]).read_text())
print(json.dumps(responses[min(len(previous), len(responses) - 1)]))
''')
    python = shlex.quote(sys.executable)
    shell = f'''set -euo pipefail
run_locked_python() {{ {python} "$@"; }}
capture_latest_execution() {{ cp "$EXECUTION_FIXTURE" "$2"; }}
gcloud() {{ {python} {shlex.quote(str(fake))} "$@"; }}
sleep() {{ printf '%s\\n' "$1" >> "$READ_SLEEPS"; }}
{function}
capture_public_egress_probe_receipt ALL_TRAFFIC
'''
    result = subprocess.run(
        ["bash", "-c", shell], cwd=ROOT, text=True, capture_output=True,
        env={
            **os.environ, "PYTHONPATH": str(ROOT),
            "PUBLIC_EGRESS_PROBE_REPORT": str(report),
            "WORKER_CANDIDATE_JOB": CANDIDATE_JOB,
            "ODAY_RELEASE_SHA": CANDIDATE_SHA,
            "MANIFEST_DIGEST": MANIFEST_DIGEST,
            "GCP_PROJECT": "test-project",
            "EXECUTION_FIXTURE": str(execution),
            "READ_CALLS": str(calls), "READ_SLEEPS": str(sleeps),
            "READ_RESPONSES": str(response_file),
            "READ_FAILURE": "true" if read_failure else "false",
        },
    )
    read_calls = [json.loads(line) for line in calls.read_text().splitlines()]
    for call in read_calls:
        assert call[:2] == ["logging", "read"]
        assert f'resource.labels.job_name="{CANDIDATE_JOB}"' in call[2]
        assert f'labels."run.googleapis.com/execution_name"="{EXECUTION}"' in call[2]
    delays = sleeps.read_text().splitlines() if sleeps.exists() else []
    return result, report, read_calls, delays


def test_missing_then_valid_receipt_retries_without_rerunning_job(tmp_path: Path):
    receipt = runtime_receipt()
    result, report, calls, delays = run_gate(tmp_path, [[], [], [{"jsonPayload": receipt}]])
    assert result.returncode == 0, result.stderr
    assert len(calls) == 3
    assert delays == ["10", "10"]
    assert json.loads(report.read_text()) == receipt


def test_missing_receipt_exhausts_bounded_wait_and_fails_closed(tmp_path: Path):
    result, report, calls, delays = run_gate(tmp_path, [[]])
    assert result.returncode != 0
    assert len(calls) == 6
    assert delays == ["10"] * 5
    assert "still absent after six reads" in result.stderr
    assert not report.exists()


@pytest.mark.parametrize("bad", [
    [{"jsonPayload": runtime_receipt(candidate_sha="0" * 40)}],
    [{"jsonPayload": runtime_receipt(job="wrong-job")}],
    [{"jsonPayload": runtime_receipt()}, {"jsonPayload": runtime_receipt()}],
    {"unexpected": "not a log list"},
])
def test_invalid_or_duplicate_receipt_is_not_retried(tmp_path: Path, bad: object):
    result, report, calls, delays = run_gate(tmp_path, [bad])
    assert result.returncode != 0
    assert len(calls) == 1
    assert delays == []
    assert not report.exists()


MALFORMED_RECEIPT = {"textPayload": '{"receipt_kind":"public_egress_probe","result":'}
VALID_RECEIPT = {"jsonPayload": runtime_receipt()}


@pytest.mark.parametrize("responses", [
    pytest.param([[MALFORMED_RECEIPT]], id="malformed-only"),
    pytest.param(
        [[MALFORMED_RECEIPT], [MALFORMED_RECEIPT, VALID_RECEIPT]],
        id="malformed-then-valid",
    ),
    pytest.param([[MALFORMED_RECEIPT, VALID_RECEIPT]], id="malformed-alongside-valid"),
    pytest.param([[VALID_RECEIPT, MALFORMED_RECEIPT]], id="valid-before-malformed"),
    pytest.param(
        [[{"textPayload": '{ "receipt_kind" : "public_egress_probe", "result":'}]],
        id="malformed-whitespace",
    ),
])
def test_identifiable_malformed_receipt_fails_on_first_read(
    tmp_path: Path, responses: list[object],
):
    result, report, calls, delays = run_gate(tmp_path, responses)
    assert result.returncode != 0
    assert len(calls) == 1
    assert delays == []
    assert "malformed public egress probe receipt JSON" in result.stderr
    assert not report.exists()


def test_unrelated_text_logs_are_not_malformed_probe_receipts(tmp_path: Path):
    unrelated = [
        {"textPayload": "Starting public_egress_probe job"},
        {"textPayload": '{"receipt_kind":"another_probe","result":'},
    ]
    result, report, calls, delays = run_gate(
        tmp_path, [unrelated, [*unrelated, VALID_RECEIPT]],
    )
    assert result.returncode == 0, result.stderr
    assert len(calls) == 2
    assert delays == ["10"]
    assert json.loads(report.read_text()) == runtime_receipt()


def test_valid_text_receipt_is_still_accepted(tmp_path: Path):
    receipt = runtime_receipt()
    result, report, calls, delays = run_gate(tmp_path, [[{"textPayload": json.dumps(receipt)}]])
    assert result.returncode == 0, result.stderr
    assert len(calls) == 1
    assert delays == []
    assert json.loads(report.read_text()) == receipt


def test_logging_read_failure_is_not_retried_or_treated_as_missing(tmp_path: Path):
    result, report, calls, delays = run_gate(tmp_path, [[]], read_failure=True)
    assert result.returncode != 0
    assert len(calls) == 1
    assert delays == []
    assert "unable to read" in result.stderr
    assert not report.exists()

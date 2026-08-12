from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/release/check_runtime_admission.py"
SHA = "e" * 40


def module():
    spec = importlib.util.spec_from_file_location("runtime_admission", SCRIPT)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def registry() -> dict:
    payload = {
        "release": {"decision": "go", "candidate_sha": SHA},
        "gates": [],
    }
    for index in range(7):
        payload["gates"].append(
            {
                "id": f"gate-{index}",
                "status": "passed",
                "release_sha": SHA,
                "receipts": [{"receipt_id": f"receipt-{index}"}],
            }
        )
    return payload


def kwargs() -> dict[str, str]:
    return {
        "release_sha": SHA,
        "environment": "dev",
        "task_id": "ODP-RUNTIME-RELEASE-001",
        "lease": "release-lease-001",
    }


def test_valid_go_registry_is_admitted() -> None:
    assert module().admission_errors(registry(), **kwargs()) == []


def test_no_go_is_blocked_even_when_all_receipts_exist() -> None:
    payload = registry()
    payload["release"]["decision"] = "no-go"
    errors = module().admission_errors(payload, **kwargs())
    assert any("expected 'go'" in error for error in errors)


def test_sha_mismatch_is_blocked() -> None:
    payload = registry()
    args = kwargs()
    args["release_sha"] = "f" * 40
    errors = module().admission_errors(payload, **args)
    assert any("candidate_sha" in error for error in errors)


def test_missing_receipt_is_blocked() -> None:
    payload = registry()
    payload["gates"][0]["receipts"] = []
    errors = module().admission_errors(payload, **kwargs())
    assert "gate-0 has no release receipt" in errors

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "delivery_toolchain/release/check_runtime_admission.py"
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
        "task_id": "SINGLE-RUNTIME-RELEASE-0D1603CF",
        "lease": "release-lease-001",
    }


def test_valid_go_registry_is_admitted() -> None:
    assert module().admission_errors(registry(), **kwargs()) == []


def test_staging_environment_is_admitted() -> None:
    args = kwargs()
    args["environment"] = "staging"
    assert module().admission_errors(registry(), **args) == []


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


def test_invalid_environment_is_blocked() -> None:
    payload = registry()
    args = kwargs()
    args["environment"] = "production"
    errors = module().admission_errors(payload, **args)
    assert "environment must be dev or staging" in errors


def test_gate_count_must_equal_seven() -> None:
    payload = registry()
    payload["gates"].pop()
    errors = module().admission_errors(payload, **kwargs())
    assert "registry must contain exactly seven gates" in errors


def test_gate_status_failure_is_blocked() -> None:
    payload = registry()
    payload["gates"][1]["status"] = "failed"
    errors = module().admission_errors(payload, **kwargs())
    assert "gate-1 status is 'failed'" in errors


def test_gate_release_sha_mismatch_is_blocked() -> None:
    payload = registry()
    payload["gates"][0]["release_sha"] = "f" * 40
    errors = module().admission_errors(payload, **kwargs())
    assert "gate-0 release_sha does not match candidate_sha" in errors


def test_candidate_ancestry_real_git_evidence_only_is_admitted(tmp_path: Path) -> None:
    mod = module()
    repo = tmp_path / "repo"
    repo.mkdir()

    def run_git(*args: str) -> str:
        res = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, check=True
        )
        return res.stdout.strip()

    run_git("init")
    run_git("config", "user.email", "test@example.com")
    run_git("config", "user.name", "Test")

    (repo / "docs" / "evidence").mkdir(parents=True)
    (repo / "docs" / "evidence" / "gate.md").write_text("initial evidence\n")
    run_git("add", ".")
    run_git("commit", "-m", "candidate commit")
    candidate_sha = run_git("rev-parse", "HEAD")

    (repo / "docs" / "evidence" / "gate2.md").write_text("extra evidence\n")
    run_git("add", ".")
    run_git("commit", "-m", "evidence commit")
    release_sha = run_git("rev-parse", "HEAD")

    payload = registry()
    payload["release"]["candidate_sha"] = candidate_sha
    for gate in payload["gates"]:
        gate["release_sha"] = candidate_sha

    args = kwargs()
    args["release_sha"] = release_sha
    assert mod.admission_errors(payload, root=repo, **args) == []


def test_candidate_ancestry_real_git_non_evidence_change_blocked(tmp_path: Path) -> None:
    mod = module()
    repo = tmp_path / "repo"
    repo.mkdir()

    def run_git(*args: str) -> str:
        res = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, check=True
        )
        return res.stdout.strip()

    run_git("init")
    run_git("config", "user.email", "test@example.com")
    run_git("config", "user.name", "Test")

    (repo / "main.py").write_text("print('v1')\n")
    run_git("add", ".")
    run_git("commit", "-m", "candidate commit")
    candidate_sha = run_git("rev-parse", "HEAD")

    (repo / "main.py").write_text("print('v2')\n")
    run_git("add", ".")
    run_git("commit", "-m", "product commit")
    release_sha = run_git("rev-parse", "HEAD")

    payload = registry()
    payload["release"]["candidate_sha"] = candidate_sha
    for gate in payload["gates"]:
        gate["release_sha"] = candidate_sha

    args = kwargs()
    args["release_sha"] = release_sha
    errors = mod.admission_errors(payload, root=repo, **args)
    assert any("intervening commits touch non-evidence paths" in error for error in errors)
    assert any("main.py" in error for error in errors)


def test_candidate_ancestry_real_git_not_an_ancestor_blocked(tmp_path: Path) -> None:
    mod = module()
    repo = tmp_path / "repo"
    repo.mkdir()

    def run_git(*args: str) -> str:
        res = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, check=True
        )
        return res.stdout.strip()

    run_git("init")
    run_git("config", "user.email", "test@example.com")
    run_git("config", "user.name", "Test")

    (repo / "a.txt").write_text("a\n")
    run_git("add", ".")
    run_git("commit", "-m", "commit a")
    sha_a = run_git("rev-parse", "HEAD")

    run_git("checkout", "--orphan", "branch-b")
    run_git("rm", "-rf", ".")
    (repo / "b.txt").write_text("b\n")
    run_git("add", ".")
    run_git("commit", "-m", "commit b")
    sha_b = run_git("rev-parse", "HEAD")

    payload = registry()
    payload["release"]["candidate_sha"] = sha_a
    for gate in payload["gates"]:
        gate["release_sha"] = sha_a

    args = kwargs()
    args["release_sha"] = sha_b
    errors = mod.admission_errors(payload, root=repo, **args)
    assert any("not an ancestor of expected SHA" in error for error in errors)

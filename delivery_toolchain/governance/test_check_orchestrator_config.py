from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from delivery_toolchain.governance import check_orchestrator_config as guard


@pytest.fixture
def valid_config_dict() -> dict:
    example_path = guard.ORCHESTRATOR_DIR / "config.example.json"
    if example_path.exists():
        return json.loads(example_path.read_text(encoding="utf-8"))
    return {
        "paths": {"status_file": "ai-status.json"},
        "approvals": {"stale_pending_seconds": 300},
    }


def test_default_repository_configs_pass(capsys: pytest.CaptureFixture[str]) -> None:
    rc = guard.main([])
    captured = capsys.readouterr()

    assert rc == 0
    assert "Validated" in captured.out
    assert "config documents and their merged runtime views." in captured.out
    assert captured.err == ""


def test_explicit_valid_config_passes(
    tmp_path: Path, valid_config_dict: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    config_file = tmp_path / "custom_config.json"
    config_file.write_text(json.dumps(valid_config_dict), encoding="utf-8")

    rc = guard.main(["--config", str(config_file)])
    captured = capsys.readouterr()

    assert rc == 0
    assert "Validated" in captured.out
    assert captured.err == ""


def test_non_existent_config_fails(capsys: pytest.CaptureFixture[str]) -> None:
    missing_path = "/tmp/non_existent_orchestrator_config_proof.json"
    rc = guard.main(["--config", missing_path])
    captured = capsys.readouterr()

    assert rc == 1
    assert f"Orchestrator config does not exist: {missing_path}" in captured.err


def test_empty_config_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    empty_file = tmp_path / "empty_config.json"
    empty_file.write_text("   \n", encoding="utf-8")

    rc = guard.main(["--config", str(empty_file)])
    captured = capsys.readouterr()

    assert rc == 1
    assert f"Orchestrator config is empty: {empty_file}" in captured.err


def test_malformed_json_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    broken_file = tmp_path / "broken_config.json"
    broken_file.write_text('{"paths": {"status_file": ', encoding="utf-8")

    rc = guard.main(["--config", str(broken_file)])
    captured = capsys.readouterr()

    assert rc == 1
    assert f"Unable to parse orchestrator config {broken_file}" in captured.err


def test_non_object_json_root_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    array_file = tmp_path / "array_config.json"
    array_file.write_text('["not", "a", "dict"]', encoding="utf-8")

    rc = guard.main(["--config", str(array_file)])
    captured = capsys.readouterr()

    assert rc == 1
    assert f"Orchestrator config {array_file} must contain a JSON object" in captured.err


def test_unknown_top_level_property_fails(
    tmp_path: Path, valid_config_dict: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    invalid_dict = dict(valid_config_dict)
    invalid_dict["forbidden_rogue_top_level_section"] = {"foo": "bar"}
    invalid_file = tmp_path / "unknown_top_level.json"
    invalid_file.write_text(json.dumps(invalid_dict), encoding="utf-8")

    rc = guard.main(["--config", str(invalid_file)])
    captured = capsys.readouterr()

    assert rc == 1
    assert f"Invalid orchestrator config {invalid_file}" in captured.err
    assert "forbidden_rogue_top_level_section" in captured.err


def test_unknown_nested_property_in_strict_section_fails(
    tmp_path: Path, valid_config_dict: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    invalid_dict = dict(valid_config_dict)
    invalid_dict["approvals"] = {"stale_pending_seconds": 300, "unknown_strict_field": 123}
    invalid_file = tmp_path / "unknown_nested.json"
    invalid_file.write_text(json.dumps(invalid_dict), encoding="utf-8")

    rc = guard.main(["--config", str(invalid_file)])
    captured = capsys.readouterr()

    assert rc == 1
    assert f"Invalid orchestrator config {invalid_file}" in captured.err
    assert "unknown_strict_field" in captured.err


def test_type_mismatch_fails(
    tmp_path: Path, valid_config_dict: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    invalid_dict = dict(valid_config_dict)
    invalid_dict["approvals"] = {"stale_pending_seconds": "not_an_integer"}
    invalid_file = tmp_path / "type_mismatch.json"
    invalid_file.write_text(json.dumps(invalid_dict), encoding="utf-8")

    rc = guard.main(["--config", str(invalid_file)])
    captured = capsys.readouterr()

    assert rc == 1
    assert f"Invalid orchestrator config {invalid_file}" in captured.err
    assert "approvals.stale_pending_seconds" in captured.err
    assert "not of type 'integer'" in captured.err


def test_retired_keys_are_tolerated_before_validation(
    tmp_path: Path, valid_config_dict: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    retired_dict = dict(valid_config_dict)
    retired_dict["worker_tree_guard"] = {"enabled": True}
    retired_file = tmp_path / "retired_key.json"
    retired_file.write_text(json.dumps(retired_dict), encoding="utf-8")

    rc = guard.main(["--config", str(retired_file)])
    captured = capsys.readouterr()

    assert rc == 0
    assert "Validated" in captured.out
    assert captured.err == ""


def test_multiple_configs_with_one_invalid_fails(
    tmp_path: Path, valid_config_dict: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    valid_file = tmp_path / "valid.json"
    valid_file.write_text(json.dumps(valid_config_dict), encoding="utf-8")

    invalid_file = tmp_path / "invalid.json"
    invalid_file.write_text('{"bad_key": true}', encoding="utf-8")

    rc = guard.main(["--config", str(valid_file), "--config", str(invalid_file)])
    captured = capsys.readouterr()

    assert rc == 1
    assert f"Invalid orchestrator config {invalid_file}" in captured.err


def test_config_paths_resolution(tmp_path: Path) -> None:
    file_a = tmp_path / "a.json"
    file_b = tmp_path / "b.json"
    file_a.write_text("{}", encoding="utf-8")
    file_b.write_text("{}", encoding="utf-8")

    non_existent = tmp_path / "does_not_exist.json"

    paths = guard.config_paths([str(file_a), str(file_b), str(file_a), str(non_existent)])

    assert file_a in paths
    assert file_b in paths
    assert non_existent not in paths
    assert paths.count(file_a) == 1


def test_cli_subprocess_execution(tmp_path: Path, valid_config_dict: dict) -> None:
    script_path = guard.ROOT / "delivery_toolchain" / "governance" / "check_orchestrator_config.py"

    # Pass case
    proc_ok = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=guard.ROOT,
        capture_output=True,
        text=True,
    )
    assert proc_ok.returncode == 0
    assert "Validated" in proc_ok.stdout

    # Fail case
    invalid_file = tmp_path / "cli_invalid.json"
    invalid_file.write_text('{"bad_key": 1}', encoding="utf-8")

    proc_err = subprocess.run(
        [sys.executable, str(script_path), "--config", str(invalid_file)],
        cwd=guard.ROOT,
        capture_output=True,
        text=True,
    )
    assert proc_err.returncode == 1
    assert "Invalid orchestrator config" in proc_err.stderr

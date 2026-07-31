from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = ROOT / "scripts/ops/validate_plan_execution_pack.py"
SYNC_PATH = ROOT / "scripts/ops/sync_plan_execution_pack.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_plan_execution_pack", VALIDATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_sync():
    spec = importlib.util.spec_from_file_location("sync_plan_execution_pack", SYNC_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _task_for_packet(packet: dict, *, task_id: str | None = None) -> dict:
    owner = "OwnerAgent"
    reviewer = "ReviewerAgent"
    if packet["task_id"] in {
        "ODP-PLAN-AVM-OUTCOME-BACKFILL-001",
        "ODP-PLAN-HEATZONE-LABEL-BACKFILL-001",
        "ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001",
        "ODP-PLAN-OSS-LEGAL-POLICY-001",
        "ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001",
    }:
        owner = "Human/Ops"
    if packet["task_id"] in {
        "ODP-PLAN-FINAL-GATE-AUDIT-001",
        "ODP-PLAN-UAT-SIGNOFF-001",
    }:
        reviewer = "Human/Ops"
    return {
        "id": task_id or packet["task_id"],
        "status": "in_progress",
        "owner": owner,
        "reviewer": reviewer,
        "task_class": packet["class"],
        "acceptance": [
            "Deliverable: complete the scoped result.",
            "Fail-closed: reject incomplete or forged evidence.",
            "Evidence set: authoritative receipts and hashes.",
            "Handoff gate: all criteria pass before review.",
            "Batch rule: re-audit the complete packet after changes.",
        ],
        "source_docs": ["docs/evidence/DEVELOPMENT_PLAN_OPEN_TASK_EXECUTION_PACK_2026-07-31.json"],
        "artifacts": ["docs/evidence/"],
        "verification": ["pytest -q"],
        "execution_packet_id": "ODP-PLAN-EXECUTION-CONTROL-PACK-001",
        "gap_ids": list(packet["gap_ids"]),
    }


def _write_live_fixture(tmp_path: Path, validator) -> tuple[dict, Path, Path]:
    packet_data = json.loads(validator.DEFAULT_PACKET.read_text(encoding="utf-8"))
    status_path = tmp_path / "ai-status.json"
    archive_root = tmp_path / "ai-task-archive"
    (archive_root / "tasks").mkdir(parents=True)
    status_path.write_text(
        json.dumps({"tasks": [_task_for_packet(packet) for packet in packet_data["task_packets"]]}),
        encoding="utf-8",
    )
    return packet_data, status_path, archive_root


def _archive_task(archive_root: Path, task: dict, *, outcome: str = "completed") -> None:
    task = dict(task)
    task["status"] = "done"
    if outcome == "superseded":
        task["terminal_outcome"] = "superseded"
    snapshot = {
        "version": 1,
        "task_id": task["id"],
        "terminal_status": "done",
        "terminal_outcome": outcome,
        "task": task,
    }
    (archive_root / "tasks" / f"{task['id']}.json").write_text(
        json.dumps(snapshot), encoding="utf-8"
    )


def test_execution_control_pack_is_complete() -> None:
    validator = _load_validator()
    assert validator.validate_packet() == []


def test_execution_control_pack_rejects_missing_task(tmp_path: Path) -> None:
    import json

    validator = _load_validator()
    packet = json.loads(validator.DEFAULT_PACKET.read_text(encoding="utf-8"))
    packet["task_packets"] = packet["task_packets"][:-1]
    bad_packet = tmp_path / "packet.json"
    bad_packet.write_text(json.dumps(packet), encoding="utf-8")

    errors = validator.validate_packet(
        packet_path=bad_packet,
        markdown_path=validator.DEFAULT_MARKDOWN,
    )

    assert any("must contain 19 entries" in error for error in errors)
    assert any("task packet id mismatch" in error for error in errors)


def test_execution_control_pack_rejects_incomplete_contract(tmp_path: Path) -> None:
    import json

    validator = _load_validator()
    packet = json.loads(validator.DEFAULT_PACKET.read_text(encoding="utf-8"))
    packet["task_packets"][0]["must_reject"] = []
    bad_packet = tmp_path / "packet.json"
    bad_packet.write_text(json.dumps(packet), encoding="utf-8")

    errors = validator.validate_packet(
        packet_path=bad_packet,
        markdown_path=validator.DEFAULT_MARKDOWN,
    )

    assert any("must_reject must be a non-empty string list" in error for error in errors)


def test_execution_control_pack_rejects_packet_gap_scope_drift(tmp_path: Path) -> None:
    validator = _load_validator()
    packet = json.loads(validator.DEFAULT_PACKET.read_text(encoding="utf-8"))
    target = packet["task_packets"][0]
    target["gap_ids"] = ["WRONG-SCOPE"]
    bad_packet = tmp_path / "packet.json"
    bad_packet.write_text(json.dumps(packet), encoding="utf-8")

    errors = validator.validate_packet(
        packet_path=bad_packet,
        markdown_path=validator.DEFAULT_MARKDOWN,
    )

    assert any("gap_ids must equal authoritative scope" in error for error in errors)


def test_execution_control_pack_recomputes_source_matrix_and_ledger(tmp_path: Path) -> None:
    validator = _load_validator()
    matrix = validator.DEFAULT_MATRIX.read_text(encoding="utf-8")
    ledger = validator.DEFAULT_LEDGER.read_text(encoding="utf-8")
    bad_matrix = tmp_path / "matrix.md"
    bad_ledger = tmp_path / "ledger.md"
    bad_matrix.write_text(
        matrix.replace("| PLAN-S0-001 |", "| REMOVED-S0-001 |", 1),
        encoding="utf-8",
    )
    bad_ledger.write_text(
        ledger.replace("| A | `ODP-PLAN-GAP-ARCHIVE-001` |", "| A | `WRONG-TASK` |", 1),
        encoding="utf-8",
    )

    errors = validator.validate_packet(
        matrix_path=bad_matrix,
        ledger_path=bad_ledger,
    )

    assert any("source RTM matrix must contain 84 unique rows" in error for error in errors)
    assert any("source RTM matrix stage distribution drifted" in error for error in errors)
    assert any("source execution ledger must contain the exact 26" in error for error in errors)


def test_sync_metadata_expands_granular_acceptance_and_preserves_task_fields() -> None:
    import json

    validator = _load_validator()
    synchronizer = _load_sync()
    packet_data = json.loads(validator.DEFAULT_PACKET.read_text(encoding="utf-8"))
    packet = next(
        item
        for item in packet_data["task_packets"]
        if item["task_id"] == "ODP-PLAN-OSS-LICENSE-GATE-001"
    )
    task = {
        "id": packet["task_id"],
        "acceptance": ["old broad acceptance"],
        "source_docs": ["matrix.md"],
        "artifacts": ["scripts/security/"],
        "verification": ["old focused test"],
        "gap_ids": ["GAP-P1-007"],
    }

    metadata = synchronizer.build_task_metadata(task, packet)

    assert len(metadata["acceptance"]) >= 8
    assert all("old broad acceptance" != item for item in metadata["acceptance"])
    assert any(item.startswith("Deliverable:") for item in metadata["acceptance"])
    assert any(item.startswith("Fail-closed:") for item in metadata["acceptance"])
    assert any(item.startswith("Evidence set:") for item in metadata["acceptance"])
    assert any(item.startswith("Handoff gate:") for item in metadata["acceptance"])
    assert synchronizer.PACKET_JSON in metadata["source_docs"]
    assert metadata["artifacts"] == ["scripts/security/"]
    assert "old focused test" in metadata["verification"]
    assert metadata["execution_mode"] == "complete-batch-before-handoff-pr-or-deploy"


def test_live_validation_accepts_completed_packet_from_official_archive(tmp_path: Path) -> None:
    validator = _load_validator()
    packet_data, status_path, archive_root = _write_live_fixture(tmp_path, validator)
    target = packet_data["task_packets"][0]
    status = json.loads(status_path.read_text(encoding="utf-8"))
    archived_task = next(task for task in status["tasks"] if task["id"] == target["task_id"])
    status["tasks"] = [task for task in status["tasks"] if task["id"] != target["task_id"]]
    status_path.write_text(json.dumps(status), encoding="utf-8")
    _archive_task(archive_root, archived_task)

    assert (
        validator.validate_packet(
            live_status_path=status_path,
            live_archive_root=archive_root,
        )
        == []
    )


def test_live_validation_rejects_packet_missing_from_active_and_archive(tmp_path: Path) -> None:
    validator = _load_validator()
    packet_data, status_path, archive_root = _write_live_fixture(tmp_path, validator)
    target_id = packet_data["task_packets"][0]["task_id"]
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["tasks"] = [task for task in status["tasks"] if task["id"] != target_id]
    status_path.write_text(json.dumps(status), encoding="utf-8")

    errors = validator.validate_packet(
        live_status_path=status_path,
        live_archive_root=archive_root,
    )

    assert f"{target_id}: absent from active and archive state" in errors


def test_live_validation_rejects_active_archive_duplicate(tmp_path: Path) -> None:
    validator = _load_validator()
    packet_data, status_path, archive_root = _write_live_fixture(tmp_path, validator)
    target = packet_data["task_packets"][0]
    status = json.loads(status_path.read_text(encoding="utf-8"))
    active_task = next(task for task in status["tasks"] if task["id"] == target["task_id"])
    _archive_task(archive_root, active_task)

    errors = validator.validate_packet(
        live_status_path=status_path,
        live_archive_root=archive_root,
    )

    assert f"{target['task_id']}: exists in both active and archive state" in errors


def test_live_validation_rejects_malformed_archive_identity(tmp_path: Path) -> None:
    validator = _load_validator()
    packet_data, status_path, archive_root = _write_live_fixture(tmp_path, validator)
    target = packet_data["task_packets"][0]
    target_id = target["task_id"]
    status = json.loads(status_path.read_text(encoding="utf-8"))
    archived_task = next(task for task in status["tasks"] if task["id"] == target_id)
    status["tasks"] = [task for task in status["tasks"] if task["id"] != target_id]
    status_path.write_text(json.dumps(status), encoding="utf-8")
    archived_task["status"] = "done"
    (archive_root / "tasks" / f"{target_id}.json").write_text(
        json.dumps(
            {
                "task_id": "WRONG-ID",
                "terminal_status": "review",
                "terminal_outcome": "invented",
                "task": archived_task,
            }
        ),
        encoding="utf-8",
    )

    errors = validator.validate_packet(
        live_status_path=status_path,
        live_archive_root=archive_root,
    )

    assert any("archived snapshot task_id mismatch" in error for error in errors)
    assert any("terminal_status must be 'done'" in error for error in errors)
    assert any("terminal_outcome must be completed or superseded" in error for error in errors)


def test_live_validation_rejects_blank_archived_contract_fields(tmp_path: Path) -> None:
    validator = _load_validator()
    mutations = (
        ("owner", None, "owner and reviewer must be non-empty strings"),
        ("reviewer", None, "owner and reviewer must be non-empty strings"),
        ("acceptance", ["", "", "", "", ""], "acceptance must contain at least five non-empty"),
        ("artifacts", [""], "artifacts must be a non-empty string list"),
        ("verification", [""], "verification must be a non-empty string list"),
    )

    for field, value, expected_error in mutations:
        case_root = tmp_path / field
        packet_data, status_path, archive_root = _write_live_fixture(case_root, validator)
        target = packet_data["task_packets"][0]
        target_id = target["task_id"]
        status = json.loads(status_path.read_text(encoding="utf-8"))
        archived_task = next(task for task in status["tasks"] if task["id"] == target_id)
        archived_task[field] = value
        status["tasks"] = [task for task in status["tasks"] if task["id"] != target_id]
        status_path.write_text(json.dumps(status), encoding="utf-8")
        _archive_task(archive_root, archived_task)

        errors = validator.validate_packet(
            live_status_path=status_path,
            live_archive_root=archive_root,
        )

        assert any(expected_error in error for error in errors), (field, errors)


def test_live_validation_rejects_ai_only_human_gate_approval(tmp_path: Path) -> None:
    validator = _load_validator()
    packet_data, status_path, archive_root = _write_live_fixture(tmp_path, validator)
    target = next(
        packet for packet in packet_data["task_packets"] if packet["class"] == "human_gate"
    )
    status = json.loads(status_path.read_text(encoding="utf-8"))
    task = next(item for item in status["tasks"] if item["id"] == target["task_id"])
    task.update(
        {
            "status": "review_approved",
            "owner": "AIAgent",
            "reviewer": "AIReviewer",
        }
    )
    status_path.write_text(json.dumps(status), encoding="utf-8")

    errors = validator.validate_packet(
        live_status_path=status_path,
        live_archive_root=archive_root,
    )

    assert any("must be Human/Ops" in error for error in errors)


def test_live_validation_rejects_repeated_generic_acceptance(tmp_path: Path) -> None:
    validator = _load_validator()
    packet_data, status_path, archive_root = _write_live_fixture(tmp_path, validator)
    target_id = packet_data["task_packets"][0]["task_id"]
    status = json.loads(status_path.read_text(encoding="utf-8"))
    task = next(item for item in status["tasks"] if item["id"] == target_id)
    task["acceptance"] = ["generic criterion"] * 5
    status_path.write_text(json.dumps(status), encoding="utf-8")

    errors = validator.validate_packet(
        live_status_path=status_path,
        live_archive_root=archive_root,
    )

    assert any("must be unique" in error for error in errors)
    assert any("missing criterion classes" in error for error in errors)


def test_sync_dry_run_skips_valid_archive_and_continues_active_packets(
    tmp_path: Path, capsys
) -> None:
    validator = _load_validator()
    synchronizer = _load_sync()
    packet_data, status_path, archive_root = _write_live_fixture(tmp_path, validator)
    target_id = packet_data["task_packets"][0]["task_id"]
    status = json.loads(status_path.read_text(encoding="utf-8"))
    archived_task = next(item for item in status["tasks"] if item["id"] == target_id)
    status["tasks"] = [item for item in status["tasks"] if item["id"] != target_id]
    status_path.write_text(json.dumps(status), encoding="utf-8")
    _archive_task(archive_root, archived_task)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "ai-status.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    synchronizer.sync(validator.DEFAULT_PACKET, tmp_path, "CodexCoordinator", True)

    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    archived_record = next(record for record in records if record["task_id"] == target_id)
    assert archived_record == {
        "task_id": target_id,
        "state": "official_archive_validated",
        "action": "skip",
    }
    assert len(records) == 19
    assert sum(record.get("action") != "skip" for record in records) == 18


def test_sync_dry_run_keeps_truly_missing_packet_fatal(tmp_path: Path) -> None:
    validator = _load_validator()
    synchronizer = _load_sync()
    packet_data, status_path, _archive_root = _write_live_fixture(tmp_path, validator)
    target_id = packet_data["task_packets"][0]["task_id"]
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["tasks"] = [item for item in status["tasks"] if item["id"] != target_id]
    status_path.write_text(json.dumps(status), encoding="utf-8")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "ai-status.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    with pytest.raises(ValueError, match="absent from active and archive state"):
        synchronizer.sync(validator.DEFAULT_PACKET, tmp_path, "CodexCoordinator", True)


def test_sync_dry_run_rejects_malformed_official_archive(tmp_path: Path) -> None:
    validator = _load_validator()
    synchronizer = _load_sync()
    packet_data, status_path, archive_root = _write_live_fixture(tmp_path, validator)
    target_id = packet_data["task_packets"][0]["task_id"]
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["tasks"] = [item for item in status["tasks"] if item["id"] != target_id]
    status_path.write_text(json.dumps(status), encoding="utf-8")
    (archive_root / "tasks" / f"{target_id}.json").write_text(
        json.dumps(
            {
                "task_id": target_id,
                "terminal_status": "review",
                "terminal_outcome": "completed",
                "task": {"id": target_id, "status": "done"},
            }
        ),
        encoding="utf-8",
    )
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "ai-status.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    with pytest.raises(ValueError, match="terminal_status must be 'done'"):
        synchronizer.sync(validator.DEFAULT_PACKET, tmp_path, "CodexCoordinator", True)


def test_live_validation_rejects_superseded_packet_without_replacement(tmp_path: Path) -> None:
    validator = _load_validator()
    packet_data, status_path, archive_root = _write_live_fixture(tmp_path, validator)
    target = packet_data["task_packets"][0]
    target_id = target["task_id"]
    status = json.loads(status_path.read_text(encoding="utf-8"))
    archived_task = next(task for task in status["tasks"] if task["id"] == target_id)
    status["tasks"] = [task for task in status["tasks"] if task["id"] != target_id]
    status_path.write_text(json.dumps(status), encoding="utf-8")
    _archive_task(archive_root, archived_task, outcome="superseded")

    errors = validator.validate_packet(
        live_status_path=status_path,
        live_archive_root=archive_root,
    )

    assert f"{target_id}: superseded packet has no replacement task" in errors


def test_live_validation_accepts_scope_preserving_superseded_replacement(tmp_path: Path) -> None:
    validator = _load_validator()
    packet_data, status_path, archive_root = _write_live_fixture(tmp_path, validator)
    target = packet_data["task_packets"][0]
    target_id = target["task_id"]
    replacement_id = f"{target_id}-FOLLOWUP"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    archived_task = next(task for task in status["tasks"] if task["id"] == target_id)
    archived_task["superseded_by"] = replacement_id
    status["tasks"] = [task for task in status["tasks"] if task["id"] != target_id]
    status["tasks"].append(_task_for_packet(target, task_id=replacement_id))
    status_path.write_text(json.dumps(status), encoding="utf-8")
    _archive_task(archive_root, archived_task, outcome="superseded")

    assert (
        validator.validate_packet(
            live_status_path=status_path,
            live_archive_root=archive_root,
        )
        == []
    )


def test_live_validation_rejects_superseded_replacement_scope_drift(tmp_path: Path) -> None:
    validator = _load_validator()
    packet_data, status_path, archive_root = _write_live_fixture(tmp_path, validator)
    target = packet_data["task_packets"][0]
    target_id = target["task_id"]
    replacement_id = f"{target_id}-SCOPE-DRIFT"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    archived_task = next(task for task in status["tasks"] if task["id"] == target_id)
    archived_task["superseded_by"] = replacement_id
    status["tasks"] = [task for task in status["tasks"] if task["id"] != target_id]
    replacement = _task_for_packet(target, task_id=replacement_id)
    replacement["gap_ids"] = []
    status["tasks"].append(replacement)
    status_path.write_text(json.dumps(status), encoding="utf-8")
    _archive_task(archive_root, archived_task, outcome="superseded")

    errors = validator.validate_packet(
        live_status_path=status_path,
        live_archive_root=archive_root,
    )

    assert any("gap_ids must preserve the packet scope" in error for error in errors)


def test_live_validation_rejects_superseded_replacement_cycle(tmp_path: Path) -> None:
    validator = _load_validator()
    packet_data, status_path, archive_root = _write_live_fixture(tmp_path, validator)
    target = packet_data["task_packets"][0]
    target_id = target["task_id"]
    replacement_id = f"{target_id}-FOLLOWUP"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    archived_task = next(task for task in status["tasks"] if task["id"] == target_id)
    archived_task["superseded_by"] = replacement_id
    replacement = _task_for_packet(target, task_id=replacement_id)
    replacement["superseded_by"] = target_id
    status["tasks"] = [task for task in status["tasks"] if task["id"] != target_id]
    status_path.write_text(json.dumps(status), encoding="utf-8")
    _archive_task(archive_root, archived_task, outcome="superseded")
    _archive_task(archive_root, replacement, outcome="superseded")

    errors = validator.validate_packet(
        live_status_path=status_path,
        live_archive_root=archive_root,
    )

    assert any("replacement chain contains a cycle" in error for error in errors)

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


def _task_for_packet(packet: dict, validator, *, task_id: str | None = None) -> dict:
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
        "acceptance": validator.build_expected_acceptance(packet),
        "source_docs": ["docs/evidence/DEVELOPMENT_PLAN_OPEN_TASK_EXECUTION_PACK_2026-07-31.json"],
        "artifacts": [validator.PACKET_MD, validator.PACKET_JSON],
        "verification": list(packet["verification"]),
        "execution_packet_id": "ODP-PLAN-EXECUTION-CONTROL-PACK-001",
        "execution_packet_deliverables": list(packet["batch_deliverables"]),
        "execution_packet_must_reject": list(packet["must_reject"]),
        "execution_packet_evidence": list(packet["evidence"]),
        "execution_packet_handoff_gate": packet["handoff_gate"],
        "deployment_contract": packet["deployment_contract"],
        "gap_ids": list(packet["gap_ids"]),
    }


def _write_live_fixture(tmp_path: Path, validator) -> tuple[dict, Path, Path]:
    packet_data = json.loads(validator.DEFAULT_PACKET.read_text(encoding="utf-8"))
    status_path = tmp_path / "ai-status.json"
    archive_root = tmp_path / "ai-task-archive"
    (archive_root / "tasks").mkdir(parents=True)
    status_path.write_text(
        json.dumps(
            {
                "tasks": [
                    _task_for_packet(packet, validator) for packet in packet_data["task_packets"]
                ]
            }
        ),
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

    assert any("source RTM matrix must contain the exact 84 row ids" in error for error in errors)
    assert any("source RTM matrix stage distribution drifted" in error for error in errors)
    assert any("source execution ledger must contain the exact 26" in error for error in errors)


def test_execution_control_pack_rejects_same_stage_rtm_and_ledger_scope_substitution(
    tmp_path: Path,
) -> None:
    validator = _load_validator()
    bad_matrix = tmp_path / "matrix.md"
    bad_ledger = tmp_path / "ledger.md"
    bad_matrix.write_text(
        validator.DEFAULT_MATRIX.read_text(encoding="utf-8").replace(
            "| PLAN-S0-001 |", "| PLAN-S0-999 |", 1
        ),
        encoding="utf-8",
    )
    bad_ledger.write_text(
        validator.DEFAULT_LEDGER.read_text(encoding="utf-8").replace(
            "| A | `ODP-PLAN-OSS-LICENSE-GATE-001` | P1-007 |",
            "| A | `ODP-PLAN-OSS-LICENSE-GATE-001` | WRONG-SCOPE |",
            1,
        ),
        encoding="utf-8",
    )

    errors = validator.validate_packet(matrix_path=bad_matrix, ledger_path=bad_ledger)

    assert any("source RTM matrix must contain the exact 84 row ids" in error for error in errors)
    assert any(
        "source execution ledger scope for ODP-PLAN-OSS-LICENSE-GATE-001" in error
        for error in errors
    )


@pytest.mark.parametrize("mutation", ["missing", "escalated"])
def test_execution_control_pack_rejects_deployment_contract_mutation(
    tmp_path: Path, mutation: str
) -> None:
    validator = _load_validator()
    packet = json.loads(validator.DEFAULT_PACKET.read_text(encoding="utf-8"))
    target = next(
        item
        for item in packet["task_packets"]
        if item["task_id"] == "ODP-PLAN-OSS-LICENSE-GATE-001"
    )
    if mutation == "missing":
        target.pop("deployment_contract")
    else:
        target["deployment_contract"] = "staging_live_allowed"
    bad_packet = tmp_path / f"packet-{mutation}.json"
    bad_packet.write_text(json.dumps(packet), encoding="utf-8")

    errors = validator.validate_packet(packet_path=bad_packet)

    assert any("deployment_contract must equal 'forbidden'" in error for error in errors)
    if mutation == "escalated":
        assert any("exactly ODP-PLAN-LIVE-STAGING-PROOF-001 may allow" in error for error in errors)


def test_sync_metadata_expands_and_binds_granular_contract() -> None:
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
    assert any(item.startswith("Deployment:") for item in metadata["acceptance"])
    assert synchronizer.PACKET_JSON in metadata["source_docs"]
    assert metadata["artifacts"] == [
        "scripts/security/",
        synchronizer.PACKET_MD,
        synchronizer.PACKET_JSON,
    ]
    assert "old focused test" in metadata["verification"]
    assert metadata["execution_packet_deliverables"] == packet["batch_deliverables"]
    assert metadata["execution_packet_must_reject"] == packet["must_reject"]
    assert metadata["execution_packet_evidence"] == packet["evidence"]
    assert metadata["execution_packet_handoff_gate"] == packet["handoff_gate"]
    assert metadata["deployment_contract"] == "forbidden"
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


def test_live_validation_accepts_only_frozen_legacy_archive_contract(tmp_path: Path) -> None:
    validator = _load_validator()
    packet_data, status_path, archive_root = _write_live_fixture(tmp_path, validator)
    target = packet_data["task_packets"][0]
    target_id = target["task_id"]
    status = json.loads(status_path.read_text(encoding="utf-8"))
    archived_task = next(task for task in status["tasks"] if task["id"] == target_id)
    archived_task["acceptance"] = validator.build_legacy_acceptance(target)
    archived_task["artifacts"] = [validator.LEGACY_ARCHIVE_ARTIFACT_ANCHORS[target_id]]
    for field in (
        "execution_packet_deliverables",
        "execution_packet_must_reject",
        "execution_packet_evidence",
        "execution_packet_handoff_gate",
        "deployment_contract",
    ):
        archived_task.pop(field)
    status["tasks"] = [task for task in status["tasks"] if task["id"] != target_id]
    status_path.write_text(json.dumps(status), encoding="utf-8")
    _archive_task(archive_root, archived_task)

    assert (
        validator.validate_packet(
            live_status_path=status_path,
            live_archive_root=archive_root,
        )
        == []
    )

    archive_path = archive_root / "tasks" / f"{target_id}.json"
    snapshot = json.loads(archive_path.read_text(encoding="utf-8"))
    snapshot["task"]["artifacts"] = ["arbitrary artifact"]
    archive_path.write_text(json.dumps(snapshot), encoding="utf-8")

    errors = validator.validate_packet(
        live_status_path=status_path,
        live_archive_root=archive_root,
    )

    assert any("legacy archive artifacts do not match" in error for error in errors)


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


@pytest.mark.parametrize("mutation", ["generic_prefixed", "omitted", "extra_scope"])
def test_live_validation_rejects_task_contract_substitution(tmp_path: Path, mutation: str) -> None:
    validator = _load_validator()
    packet_data, status_path, archive_root = _write_live_fixture(tmp_path, validator)
    target = packet_data["task_packets"][0]
    status = json.loads(status_path.read_text(encoding="utf-8"))
    task = next(item for item in status["tasks"] if item["id"] == target["task_id"])
    if mutation == "generic_prefixed":
        task["acceptance"] = [
            "Deliverable: unrelated generic result.",
            "Fail-closed: unrelated generic rejection.",
            "Evidence set: unrelated generic evidence.",
            "Handoff gate: unrelated generic handoff.",
            "Batch rule: unrelated generic batch.",
            "Deployment: forbidden; unrelated generic boundary.",
        ]
    elif mutation == "omitted":
        task["acceptance"] = task["acceptance"][:-1]
    else:
        task["gap_ids"].append("WRONG-EXTRA-SCOPE")
    status_path.write_text(json.dumps(status), encoding="utf-8")

    errors = validator.validate_packet(
        live_status_path=status_path,
        live_archive_root=archive_root,
    )

    expected = (
        "acceptance must exactly match"
        if mutation != "extra_scope"
        else "gap_ids must exactly equal"
    )
    assert any(expected in error for error in errors)


@pytest.mark.parametrize("lifecycle", ["archive", "superseded_replacement"])
def test_live_validation_rejects_packet_contract_drift_across_lifecycle(
    tmp_path: Path, lifecycle: str
) -> None:
    validator = _load_validator()
    packet_data, status_path, archive_root = _write_live_fixture(tmp_path, validator)
    target = packet_data["task_packets"][0]
    target_id = target["task_id"]
    status = json.loads(status_path.read_text(encoding="utf-8"))
    original = next(task for task in status["tasks"] if task["id"] == target_id)
    status["tasks"] = [task for task in status["tasks"] if task["id"] != target_id]
    if lifecycle == "archive":
        original["execution_packet_evidence"] = ["arbitrary evidence"]
        _archive_task(archive_root, original)
    else:
        replacement_id = f"{target_id}-CONTRACT-DRIFT"
        original["superseded_by"] = replacement_id
        replacement = _task_for_packet(target, validator, task_id=replacement_id)
        replacement["verification"] = ["arbitrary verification"]
        status["tasks"].append(replacement)
        _archive_task(archive_root, original, outcome="superseded")
    status_path.write_text(json.dumps(status), encoding="utf-8")

    errors = validator.validate_packet(
        live_status_path=status_path,
        live_archive_root=archive_root,
    )

    expected = (
        "execution_packet_evidence must exactly match"
        if lifecycle == "archive"
        else "verification must contain the complete packet verification"
    )
    assert any(expected in error for error in errors)


def test_live_validation_rejects_deployment_privilege_escalation(tmp_path: Path) -> None:
    validator = _load_validator()
    packet_data, status_path, archive_root = _write_live_fixture(tmp_path, validator)
    target = next(
        item
        for item in packet_data["task_packets"]
        if item["task_id"] == "ODP-PLAN-OSS-LICENSE-GATE-001"
    )
    status = json.loads(status_path.read_text(encoding="utf-8"))
    task = next(item for item in status["tasks"] if item["id"] == target["task_id"])
    task["deployment_contract"] = "staging_live_allowed"
    status_path.write_text(json.dumps(status), encoding="utf-8")

    errors = validator.validate_packet(
        live_status_path=status_path,
        live_archive_root=archive_root,
    )

    assert any("deployment_contract must exactly match" in error for error in errors)


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
    status["tasks"].append(_task_for_packet(target, validator, task_id=replacement_id))
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
    replacement = _task_for_packet(target, validator, task_id=replacement_id)
    replacement["gap_ids"] = []
    status["tasks"].append(replacement)
    status_path.write_text(json.dumps(status), encoding="utf-8")
    _archive_task(archive_root, archived_task, outcome="superseded")

    errors = validator.validate_packet(
        live_status_path=status_path,
        live_archive_root=archive_root,
    )

    assert any("gap_ids must exactly equal the packet scope" in error for error in errors)


def test_live_validation_rejects_superseded_replacement_cycle(tmp_path: Path) -> None:
    validator = _load_validator()
    packet_data, status_path, archive_root = _write_live_fixture(tmp_path, validator)
    target = packet_data["task_packets"][0]
    target_id = target["task_id"]
    replacement_id = f"{target_id}-FOLLOWUP"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    archived_task = next(task for task in status["tasks"] if task["id"] == target_id)
    archived_task["superseded_by"] = replacement_id
    replacement = _task_for_packet(target, validator, task_id=replacement_id)
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

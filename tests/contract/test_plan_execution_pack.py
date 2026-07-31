from __future__ import annotations

import importlib.util
from pathlib import Path

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

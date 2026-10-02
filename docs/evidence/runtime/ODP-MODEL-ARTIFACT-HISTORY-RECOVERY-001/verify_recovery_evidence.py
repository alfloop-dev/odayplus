"""Verify the ODP-MODEL-ARTIFACT-HISTORY-RECOVERY-001 recovery evidence.

Keeps recovered / artifact_missing / approval_missing / history_unknown facts
apart, and refuses any document that turns an investigation into model
readiness, a deploy GO, or a cloud write.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

EVIDENCE_DIR = Path(__file__).resolve().parent
EVIDENCE_PATH = EVIDENCE_DIR / "recovery_evidence.json"

READINESS_MODELS = {
    "forecast_revenue_interval",
    "dealroom_avm",
    "sitescore_propensity",
    "heatzone_priority",
}
REGISTRY_STATES = {"absent", "registered", "not_read_current"}
ARTIFACT_STATES = {"recovered", "artifact_missing", "not_read_current"}
APPROVAL_STATES = {"approved", "approval_missing"}
HISTORY_STATES = {"history_unknown", "history_current"}
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _sha(value: object) -> bool:
    return isinstance(value, str) and bool(SHA256.match(value))


def _model_errors(model: Mapping[str, Any], readbacks: set[str]) -> list[str]:
    name = model.get("model")
    errors: list[str] = []
    registry = model.get("registry") or {}
    artifact = model.get("artifact") or {}
    approval = model.get("approval") or {}
    history = model.get("history") or {}

    if registry.get("state") not in REGISTRY_STATES:
        errors.append(f"{name}: unknown registry state {registry.get('state')!r}")
    if artifact.get("state") not in ARTIFACT_STATES:
        errors.append(f"{name}: unknown artifact state {artifact.get('state')!r}")
    if approval.get("state") not in APPROVAL_STATES:
        errors.append(f"{name}: unknown approval state {approval.get('state')!r}")
    if history.get("state") not in HISTORY_STATES:
        errors.append(f"{name}: unknown history state {history.get('state')!r}")

    # A registry conclusion must come from a current readback of this exact model;
    # one model's 404 is not evidence about another.
    if registry.get("state") in {"absent", "registered"}:
        if registry.get("evidence") != "current_readback":
            errors.append(f"{name}: registry state needs a current readback")
        if registry.get("readback_model_name") != name:
            errors.append(f"{name}: registry readback names a different model")
        if registry.get("readback_ref") not in readbacks:
            errors.append(f"{name}: registry readback is not a saved receipt")
    elif registry.get("evidence") != "historical_handback" or not _sha(
        registry.get("handback_sha256")
    ):
        errors.append(f"{name}: not_read_current needs a hashed historical handback")

    if history.get("state") == "history_unknown":
        if history.get("current_row_count") != "unknown":
            errors.append(f"{name}: history_unknown must not carry a current row count")
    elif not history.get("current_readback_ref"):
        errors.append(f"{name}: history_current needs a current readback ref")

    recovered = model.get("recovered") is True
    if artifact.get("state") == "recovered" or recovered:
        if not (artifact.get("state") == "recovered" and recovered):
            errors.append(f"{name}: recovered flag and artifact state disagree")
        if not _sha(artifact.get("sha256")):
            errors.append(f"{name}: recovered artifact needs a sha256")
        for field in ("uri", "version", "training_ref"):
            if not artifact.get(field):
                errors.append(f"{name}: recovered artifact needs {field}")
        if approval.get("state") != "approved" or not approval.get("approval_ref"):
            errors.append(f"{name}: recovered artifact needs an approval ref")
        if not approval.get("rollback_target"):
            errors.append(f"{name}: recovered artifact needs a rollback target")
    if approval.get("state") == "approved" and not approval.get("approval_ref"):
        errors.append(f"{name}: approved without approval_ref")
    return errors


def validate(doc: Mapping[str, Any], *, root: Path | None = None) -> list[str]:
    errors: list[str] = []
    if doc.get("mutation") is not False or doc.get("cloud_writes"):
        errors.append("investigation must not record a cloud write")

    receipts = doc.get("saved_receipts") or []
    readbacks: set[str] = set()
    for receipt in receipts:
        path = receipt.get("path", "")
        if not _sha(receipt.get("sha256")):
            errors.append(f"saved receipt {path} lacks sha256")
            continue
        readbacks.add(Path(path).name)
        if root is not None:
            target = root / path
            if not target.is_file():
                errors.append(f"saved receipt {path} is missing")
            elif hashlib.sha256(target.read_bytes()).hexdigest() != receipt["sha256"]:
                errors.append(f"saved receipt {path} sha256 mismatch")

    models = doc.get("models") or []
    names = {m.get("model") for m in models}
    if names != READINESS_MODELS or len(models) != len(READINESS_MODELS):
        errors.append(
            f"models must be exactly the four readiness models, got {sorted(map(str, names))}"
        )
    for model in models:
        errors.extend(_model_errors(model, readbacks))

    claims = doc.get("claims") or {}
    all_recovered = bool(models) and all(m.get("recovered") is True for m in models)
    if claims.get("model_ready") is not False and not all_recovered:
        errors.append("model_ready claimed while a model is not recovered")
    for claim in ("live_done", "deploy_go"):
        if claims.get(claim) is not False:
            errors.append(f"{claim} cannot be claimed by an investigation")

    if (doc.get("risk_acceptance") or {}).get("used_as_current_go") is not False:
        errors.append("July risk acceptance cannot be used as a current GO")

    for task in doc.get("original_tasks") or []:
        if not re.fullmatch(r"[0-9a-f]{40}", str(task.get("branch_head", ""))):
            errors.append(f"{task.get('task_id')}: branch_head must be a full SHA")
        for item in task.get("terminal_evidence") or []:
            if not _sha(item.get("sha256")):
                errors.append(f"{task.get('task_id')}: terminal evidence lacks sha256")

    if not all_recovered:
        missing = doc.get("missing_inputs") or []
        if not missing:
            errors.append("unrecovered models need missing_inputs")
        for item in missing:
            if not item.get("responsible") or not item.get("handback_task"):
                errors.append(f"missing input {item.get('id')} needs responsible and handback_task")
        if doc.get("operation_plan") is not None and not any(
            m.get("recovered") is True for m in models
        ):
            errors.append("operation plan proposed with nothing recovered")
    return errors


def main() -> int:
    doc = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    errors = validate(doc, root=EVIDENCE_DIR.parents[3])
    for error in errors:
        print(f"FAIL: {error}")
    if not errors:
        print("recovery evidence OK")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
